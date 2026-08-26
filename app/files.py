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


def _resolve_chat_id(sup, org_id):
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    return int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None


def _finalize_upload(sup, user, filename, folder_id, sha256, size_bytes, message_ids):
    """Insert file + new version, write audit log, and run blocking auto-backup.
    Shared by the legacy single-shot upload and the new chunked upload/commit flow."""
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
        print(f"[UPLOAD] New file created: file_id={file_id}, new version=v{new_ver}")
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
    # Auto-backup on upload: forward bytes to backup channel + full snapshot.
    # Runs in the background by default so the upload response returns fast.
    # Set AUTO_BACKUP_BLOCKING=1 to wait for the backup before responding
    # (stricter, but on serverless the response may kill the bg thread).
    _auto_backup(sup, user, message_ids)
    return file_id, new_ver


def _auto_backup(sup, user, message_ids):
    import os as _os
    if _os.environ.get("AUTO_BACKUP_BLOCKING") == "1":
        _run_backup(sup, user, message_ids)
        return
    import threading
    threading.Thread(target=_run_backup, args=(None, user, message_ids), daemon=True).start()


def _run_backup(sup, user, message_ids):
    try:
        from app.backups import auto_backup_on_upload
        client = sup
        if client is None:
            try:
                from app.supabase_client import get_supabase
                client = get_supabase()
            except Exception:
                client = None
        if client is None:
            print("[BACKUP] no supabase client available for backup")
            return
        auto_backup_on_upload(client, user["org_id"], user, message_ids)
    except Exception as e:
        print(f"[BACKUP] auto-backup failed: {e}")


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
    except RuntimeError as e:
        # Configuration/session errors — surface hint even in production (no secrets leaked)
        import traceback; traceback.print_exc()
        msg = str(e)
        # Telethon session missing is the #1 cause on Vercel (ephemeral FS)
        return jsonify({"error": msg, "type": type(e).__name__}), 503
    except Exception as e:
        import traceback; traceback.print_exc()
        # Surface detail even in production for Vercel debugging (sanitized, no keys)
        detail = str(e)[:400]
        detail = detail.split("\n")[0][:400]
        # Detect Vercel SQLite session issue even when raised as OperationalError
        if "unable to open database file" in detail.lower():
            return jsonify({
                "error": "Telegram session file cannot be opened on Vercel (read-only filesystem). Set TG_SESSION_STRING env var from your local session.session and redeploy.",
                "type": type(e).__name__,
                "hint": "Generate StringSession: py -c \"from telethon.sessions import StringSession; print(StringSession.save(StringSession.load(open('session.session','rb').read())))\" then vercel env add TG_SESSION_STRING"
            }), 503
        return jsonify({"error": f"Storage failed. {type(e).__name__}: {detail}"}), 500
    print(f"[UPLOAD] Stored to Telegram. message_ids={message_ids}, size={size_bytes}")
    try:
        file_id, new_ver = _finalize_upload(sup, user, filename, folder_id, sha256, size_bytes, message_ids)
    except Exception as e:
        return jsonify({"error": f"Upload failed: {e}"}), 500
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})


@files_bp.route("/api/files/chunk", methods=["POST"])
@login_required
def api_files_chunk():
    """Receive one encrypted client chunk and forward it to Telegram as a single
    message. Stateless — the client tracks ordered message_ids and commits at the end.
    Keeps each HTTP request tiny so large uploads work on Vercel serverless."""
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    chunk = request.files.get("chunk")
    filename = request.form.get("filename", "unnamed")
    folder_id = request.form.get("folder_id") or None
    if not chunk:
        return jsonify({"error": "No chunk provided"}), 400
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured — check TG_API_ID and TG_API_HASH"}), 503
    chat_id = _resolve_chat_id(sup, user["org_id"])
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured for this organisation"}), 503
    try:
        message_ids = telegram_service.upload_chunks_streaming(chunk.stream, filename or "file", chat_id)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Chunk storage failed: {type(e).__name__}: {str(e)[:300]}"}), 500
    if not message_ids:
        return jsonify({"error": "Telegram returned no message id for chunk"}), 500
    print(f"[UPLOAD] chunk stored -> message_id={message_ids[0]}")
    return jsonify({"ok": True, "message_id": message_ids[0]})


@files_bp.route("/api/files/commit", methods=["POST"])
@login_required
def api_files_commit():
    """Finalize a chunked upload: create the file version from the ordered
    message_ids collected client-side, write audit log, run auto-backup."""
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    data = request.get_json(force=True)
    filename = data.get("filename", "unnamed")
    folder_id = data.get("folder_id") or None
    sha256 = data.get("sha256", "")
    total_size = int(data.get("total_size") or 0)
    message_ids = data.get("message_ids") or []
    if not isinstance(message_ids, list) or not message_ids:
        return jsonify({"error": "No message_ids provided"}), 400
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    try:
        file_id, new_ver = _finalize_upload(sup, user, filename, folder_id, sha256, total_size, message_ids)
    except Exception as e:
        return jsonify({"error": f"Commit failed: {e}"}), 500
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})


# ── BULK OPERATIONS (Phase-1 O3) ──────────────────────────────────────────────
def _valid_org_file_ids(sup, org_id, ids):
    existing = sup.table("files").select("id").eq("org_id", org_id).in_("id", [str(x) for x in ids]).execute().data
    return [r["id"] for r in existing]


@files_bp.route("/api/files/bulk-delete", methods=["POST"])
@login_required
def api_files_bulk_delete():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    if not ids:
        return jsonify({"error": "No file ids provided"}), 400
    sup = get_supabase()
    valid = _valid_org_file_ids(sup, user["org_id"], ids)
    if not valid:
        return jsonify({"ok": True, "deleted": 0})
    sup.table("files").update({
        "is_deleted": True,
        "deleted_at": datetime.utcnow().isoformat(),
        "deleted_by": user["id"],
    }).in_("id", valid).eq("org_id", user["org_id"]).execute()
    log_action("bulk_delete", f"{len(valid)} file(s)")
    return jsonify({"ok": True, "deleted": len(valid)})


@files_bp.route("/api/files/bulk-restore", methods=["POST"])
@login_required
def api_files_bulk_restore():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    if not ids:
        return jsonify({"error": "No file ids provided"}), 400
    sup = get_supabase()
    # restore targets files currently in trash for this org
    existing = sup.table("files").select("id").eq("org_id", user["org_id"]).eq("is_deleted", True).in_("id", [str(x) for x in ids]).execute().data
    valid = [r["id"] for r in existing]
    if not valid:
        return jsonify({"ok": True, "restored": 0})
    sup.table("files").update({"is_deleted": False, "deleted_at": None, "deleted_by": None}).in_("id", valid).eq("org_id", user["org_id"]).execute()
    log_action("bulk_restore", f"{len(valid)} file(s)")
    return jsonify({"ok": True, "restored": len(valid)})


@files_bp.route("/api/files/bulk-move", methods=["POST"])
@login_required
def api_files_bulk_move():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    folder_id = data.get("folder_id") or None
    if not ids:
        return jsonify({"error": "No file ids provided"}), 400
    sup = get_supabase()
    if folder_id:
        f = sup.table("folders").select("id").eq("id", folder_id).eq("org_id", user["org_id"]).maybe_single().execute()
        if not f or not f.data:
            return jsonify({"error": "Target folder not found"}), 404
    valid = _valid_org_file_ids(sup, user["org_id"], ids)
    if not valid:
        return jsonify({"ok": True, "moved": 0})
    sup.table("files").update({"folder_id": folder_id}).in_("id", valid).eq("org_id", user["org_id"]).execute()
    log_action("bulk_move", f"{len(valid)} file(s) -> folder {folder_id}")
    return jsonify({"ok": True, "moved": len(valid)})


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


@files_bp.route("/api/files/<uuid:file_id>/chunk/<int:index>", methods=["GET"])
@login_required
def api_files_chunk_download(file_id, index):
    """Stream ONE stored chunk (Telegram message) by index. Lets the client fetch
    chunks with many small requests instead of one 60s-bounded stream — so large
    files download reliably on serverless (same chunked model as upload)."""
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
    if index < 0 or index >= len(message_ids):
        return jsonify({"error": "No such chunk"}), 404
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", fdata["org_id"]).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured"}), 500

    def generate():
        for chunk in telegram_service.download_chunks_streaming(chat_id, [message_ids[index]]):
            yield chunk

    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'inline; filename="{fdata["name"]}.part{index}"'
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
    if size_bytes:
        resp.headers["Content-Length"] = str(size_bytes)
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
