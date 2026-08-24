"""
app.files — file listing, search, upload, download, preview, email (Task 4).

Moved from app.py:730-954, 1527-1653 (files + preview + email).
Keeps email header-injection guard verbatim: EMAIL_RE, MAX_RECIPIENTS=50,
strips \\r\\n from From/Subject (from app.py:1585-1586).
"""

import os
import re
import secrets
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Blueprint, jsonify, request, Response, stream_with_context

import telegram_service
from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import (
    _check_permission,
    _require_active_org,
    _resolve_folder_name,
    _parse_message_ids,
    log_action,
    fmt_size,
)

files_bp = Blueprint("files", __name__)

# Email validation — preserved from app.py:1558-1561
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MAX_RECIPIENTS = 50


def _tg_configured():
    return telegram_service.is_configured()


def _store_file_blob(f, org_id):
    """Upload encrypted bytes to org's Telegram channel. Returns (message_ids, size_bytes).

    Reads the uploaded file in a streaming fashion to keep peak memory
    close to CHUNK_SIZE_BYTES (~1.9 GB) rather than the full file size.
    """
    print(f"[UPLOAD] _store_file_blob called: org_id={org_id}, filename={f.filename}")
    if not _tg_configured():
        raise RuntimeError("Telegram not configured — check TG_API_ID and TG_API_HASH in .env")
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured for this organisation")
    size_bytes = 0
    try:
        f.stream.seek(0, 2)
        size_bytes = f.stream.tell()
        f.stream.seek(0)
    except Exception:
        size_bytes = f.content_length or 0
    print(f"[UPLOAD] chat_id={chat_id}, size_bytes={size_bytes}, starting Telegram upload...")
    message_ids = telegram_service.upload_chunks_streaming(f.stream, f.filename or "file", chat_id)
    print(f"[UPLOAD] Telegram upload done, message_ids={message_ids}")
    return message_ids, size_bytes


def _load_file_blob(org_id, message_ids):
    """Download encrypted bytes from Telegram chunks."""
    if not _tg_configured():
        raise RuntimeError("Telegram not configured")
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured")
    return telegram_service.download_chunks(chat_id, message_ids)


@files_bp.route("/api/files", methods=["GET"])
@login_required
def api_files_get():
    user = current_user()
    folder_id = request.args.get("folder_id")
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    query = sup.table("files").select("id, name, folder_id, created_at").eq("org_id", user["org_id"]).eq("is_deleted", False)
    if folder_id:
        query = query.eq("folder_id", folder_id)
    else:
        query = query.is_("folder_id", "null")
    rows = query.order("name").execute().data
    result = []
    for r in rows:
        d = dict(r)
        ver_result = sup.table("file_versions").select("version_number, size_bytes, sha256, uploaded_at, uploaded_by").eq("file_id", r["id"]).eq("is_current", True).maybe_single().execute()
        if ver_result and ver_result.data:
            ver = ver_result.data
            uploader_res = sup.table("users").select("username").eq("id", ver["uploaded_by"]).maybe_single().execute()
            d["current_version"] = {
                "version_number": ver["version_number"],
                "size_bytes": ver["size_bytes"],
                "sha256": ver["sha256"],
                "uploaded_at": ver["uploaded_at"],
                "uploaded_by_name": uploader_res.data["username"] if uploader_res and uploader_res.data else None,
            }
        result.append(d)
    return jsonify(result)


@files_bp.route("/api/files/search", methods=["GET"])
@login_required
def api_files_search():
    user = current_user()
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    user_role = user["role"]
    can_read_write = user_role in ("org_admin", "master_admin", "read_write")
    rows = sup.table("files").select("id, name, folder_id, created_at").eq("org_id", user["org_id"]).eq("is_deleted", False).ilike("name", f"%{q}%").order("name").limit(100).execute().data
    result = []
    for r in rows:
        folder_id = r.get("folder_id")
        perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
        if not perm:
            continue
        d = dict(r)
        d["can_download"] = bool(perm) and perm != "read_only"
        d["can_write"] = can_read_write and perm != "read_only"
        ver_result = sup.table("file_versions").select("version_number, size_bytes, uploaded_at, uploaded_by").eq("file_id", r["id"]).eq("is_current", True).maybe_single().execute()
        if ver_result and ver_result.data:
            ver = ver_result.data
            uploader_res = sup.table("users").select("username").eq("id", ver["uploaded_by"]).maybe_single().execute()
            d["current_version"] = {
                "version_number": ver["version_number"],
                "size_bytes": ver["size_bytes"],
                "uploaded_at": ver["uploaded_at"],
                "uploaded_by_name": uploader_res.data["username"] if uploader_res and uploader_res.data else None,
            }
        d["folder_name"] = _resolve_folder_name(sup, folder_id)
        result.append(d)
    return jsonify(result)


@files_bp.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    user = current_user()
    print(f"[UPLOAD] User '{user['username']}' ({user['role']}) requesting upload")
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    f = request.files.get("file")
    filename = request.form.get("filename", "unnamed")
    folder_id = request.form.get("folder_id") or None
    sha256 = request.form.get("sha256", "")
    print(f"[UPLOAD] filename={filename}, folder_id={folder_id}, sha256={sha256[:16]}...")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    try:
        message_ids, size_bytes = _store_file_blob(f, user["org_id"])
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Storage failed. Please try again or contact an administrator."}), 500
    print(f"[UPLOAD] Stored to Telegram. message_ids={message_ids}, size={size_bytes}")
    # Find existing file by name + folder
    existing_query = sup.table("files").select("id").eq("org_id", user["org_id"]).eq("name", filename).eq("is_deleted", False)
    if folder_id:
        existing_query = existing_query.eq("folder_id", folder_id)
    else:
        existing_query = existing_query.is_("folder_id", "null")
    existing = existing_query.execute()
    if existing.data:
        file_id = existing.data[0]["id"]
        last = sup.table("file_versions").select("version_number").eq("file_id", file_id).order("version_number", desc=True).limit(1).execute()
        new_ver = (last.data[0]["version_number"] if last.data else 0) + 1
        sup.table("file_versions").update({"is_current": False}).eq("file_id", file_id).execute()
        print(f"[UPLOAD] Existing file updated: file_id={file_id}, new version=v{new_ver}")
    else:
        file_result = sup.table("files").insert({
            "org_id": user["org_id"],
            "folder_id": folder_id,
            "name": filename,
        }).execute()
        file_id = file_result.data[0]["id"]
        new_ver = 1
        print(f"[UPLOAD] New file created: file_id={file_id}, version=v{new_ver}")
    sup.table("file_versions").insert({
        "file_id": file_id,
        "version_number": new_ver,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "message_ids": message_ids,
        "uploaded_by": user["id"],
        "is_current": True,
    }).execute()
    folder_name = _resolve_folder_name(sup, folder_id)
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["id"],
        "actor_role": user["role"],
        "action": "upload",
        "target_type": "file",
        "target_id": str(file_id),
        "details": {
            "target": filename,
            "detail": f"v{new_ver} · {fmt_size(size_bytes)} · folder={folder_name}",
        },
    }).execute()
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})


@files_bp.route("/api/files/<uuid:file_id>/download", methods=["GET"])
@login_required
def api_files_download(file_id):
    sup = get_supabase()
    user = current_user()
    print(f"[DOWNLOAD] User '{user['username']}' requesting download of file_id={file_id}")
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    if fdata["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    ver_result = sup.table("file_versions").select("*").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    print(f"[DOWNLOAD] file={fdata['name']}, version=v{ver['version_number']}, size={size_bytes}, chunks={len(message_ids)}")
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    sup2 = get_supabase()
    org = sup2.table("organizations").select("telegram_chat_id").eq("id", fdata["org_id"]).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured"}), 500
    def generate():
        for chunk in telegram_service.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    log_action("download", fdata["name"], f"v{ver['version_number']} · {fmt_size(size_bytes)} · folder={_resolve_folder_name(sup, fdata.get('folder_id'))}", target_type="file", target_id=file_id)
    print(f"[DOWNLOAD] Starting streaming response...")
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{fdata["name"]}"'
    resp.headers["Content-Length"] = str(size_bytes)
    return resp


@files_bp.route("/api/files/<uuid:file_id>/preview", methods=["GET"])
@login_required
def api_files_preview(file_id):
    sup = get_supabase()
    user = current_user()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    if fdata["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    ver_result = sup.table("file_versions").select("*").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", fdata["org_id"]).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured"}), 500
    def generate():
        for chunk in telegram_service.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    print(f"[PREVIEW] Serving preview for '{fdata['name']}'")
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'inline; filename="{fdata["name"]}"'
    return resp


@files_bp.route("/api/files/<uuid:file_id>/email", methods=["POST"])
@login_required
def api_files_email(file_id):
    sup = get_supabase()
    user = current_user()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    if fdata["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    data = request.get_json(force=True)
    recipients = data.get("recipients", "")
    message = data.get("message", "")
    if not recipients:
        return jsonify({"error": "Recipients required"}), 400
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "TeamVault")
    if not smtp_host or not smtp_user:
        return jsonify({"error": "Email not configured — ask admin to set SMTP_* variables in .env"}), 500

    # Validate recipient addresses to prevent SMTP/email header injection.
    raw = [r.strip() for r in recipients.replace(";", ",").split(",") if r.strip()]
    email_list = []
    for addr in raw:
        if not EMAIL_RE.match(addr) or "\n" in addr or "\r" in addr:
            return jsonify({"error": f"Invalid recipient address: {addr}"}), 400
        email_list.append(addr)
    if len(email_list) > MAX_RECIPIENTS:
        return jsonify({"error": f"Too many recipients (max {MAX_RECIPIENTS})"}), 400
    if not EMAIL_RE.match(smtp_from or "") and "@" not in (smtp_from or ""):
        return jsonify({"error": "SMTP_FROM is misconfigured"}), 500

    # Create share link for email
    token = secrets.token_urlsafe(24)
    from datetime import timedelta
    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
    sup.table("shared_links").insert({
        "file_id": str(file_id),
        "token": token,
        "created_by": user["id"],
        "expires_at": expires_at,
    }).execute()
    share_url = f"{request.host_url}shared/{token}"

    safe_from_name = re.sub(r"[\r\n]", "", smtp_from_name)
    subject = re.sub(r"[\r\n]", "", f"{user['username']} shared a file with you — {fdata['name']}")
    try:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        for addr in email_list:
            msg = MIMEMultipart()
            msg["From"] = f"{safe_from_name} <{smtp_from}>"
            msg["To"] = addr
            msg["Subject"] = subject
            body = f"""Hi,

{user['username']} shared a file with you via TeamVault.

File: {fdata['name']}
Download link (expires in 7 days): {share_url}

{message if message else ''}

— TeamVault"""
            msg.attach(MIMEText(body, "plain"))
            server.sendmail(smtp_from, addr, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"[EMAIL] Failed to send: {e}")
        return jsonify({"error": "Failed to send email. Please try again later."}), 500
    log_action("email_file", fdata["name"], f"folder={_resolve_folder_name(sup, fdata.get('folder_id'))} to={len(email_list)} recipient(s)")
    print(f"[EMAIL] Sent '{fdata['name']}' to {len(email_list)} recipient(s)")
    return jsonify({"ok": True})
