"""
app.sharing — file sharing via tokenised links (Task 6).

Moved from app.py:1356-1523 (api_files_share, api_files_shares,
api_files_unshare, api_shared_download, api_shared_info, api_shared_preview).
Preserves hash_share_password / verify_share_password usage (design §5) and
Telegram streaming via telegram_service.
"""

import secrets
from datetime import datetime

from flask import Blueprint, jsonify, request, Response, stream_with_context

import telegram_service
from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import (
    _check_permission,
    _require_active_org,
    _resolve_folder_name,
    _parse_message_ids,
    hash_share_password,
    verify_share_password,
    log_action,
)

sharing_bp = Blueprint("sharing", __name__)


def _tg_configured():
    return telegram_service.is_configured()


@sharing_bp.route("/api/files/<uuid:file_id>/share", methods=["POST"])
@login_required
def api_files_share(file_id):
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
    data = request.get_json(force=True) if request.data else {}
    expires_days = data.get("expires_days", 7)
    password = data.get("password", "")
    token = secrets.token_urlsafe(24)
    expires_at = None
    if expires_days:
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(days=int(expires_days))).isoformat()
    insert_data = {
        "file_id": str(file_id),
        "token": token,
        "created_by": user["id"],
        "expires_at": expires_at,
    }
    if password:
        insert_data["password_hash"] = hash_share_password(password)
    result = sup.table("shared_links").insert(insert_data).execute()
    log_action("share_file", fdata["name"], f"folder={_resolve_folder_name(sup, fdata.get('folder_id'))} token={token[:8]}...")
    print(f"[SHARE] Created link for file '{fdata['name']}', token={token[:8]}...")
    return jsonify({"ok": True, "token": token, "expires_at": expires_at})


@sharing_bp.route("/api/files/<uuid:file_id>/shares", methods=["GET"])
@login_required
def api_files_shares(file_id):
    sup = get_supabase()
    user = current_user()
    file_check = sup.table("files").select("org_id").eq("id", file_id).maybe_single().execute()
    if not file_check or not file_check.data or file_check.data["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    shares = sup.table("shared_links").select("*").eq("file_id", file_id).order("created_at", desc=True).execute().data
    result = []
    for s in shares:
        d = dict(s)
        d["has_password"] = bool(d.pop("password_hash", None))
        result.append(d)
    return jsonify(result)


@sharing_bp.route("/api/files/<uuid:file_id>/shares/<uuid:share_id>", methods=["DELETE"])
@login_required
def api_files_unshare(file_id, share_id):
    sup = get_supabase()
    user = current_user()
    file_check = sup.table("files").select("org_id").eq("id", file_id).maybe_single().execute()
    if not file_check or not file_check.data or file_check.data["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    sup.table("shared_links").delete().eq("id", share_id).eq("file_id", file_id).execute()
    f = sup.table("files").select("name").eq("id", file_id).maybe_single().execute()
    log_action("remove_share", f.data["name"] if f and f.data else str(file_id))
    return jsonify({"ok": True})


@sharing_bp.route("/api/shared/<token>", methods=["GET"])
def api_shared_download(token):
    sup = get_supabase()
    link = sup.table("shared_links").select("*, files(name, org_id, folder_id)").eq("token", token).maybe_single().execute()
    if not link or not link.data:
        return jsonify({"error": "Link not found or expired"}), 404
    link_data = link.data
    if link_data.get("expires_at"):
        from datetime import datetime as dt
        exp = dt.fromisoformat(link_data["expires_at"].replace("Z", "+00:00")) if "T" in link_data["expires_at"] else dt.strptime(link_data["expires_at"][:19], "%Y-%m-%dT%H:%M:%S")
        if dt.utcnow() > exp:
            return jsonify({"error": "Link has expired"}), 410
    pw_hash = link_data.get("password_hash")
    if pw_hash:
        provided = request.args.get("password", "")
        if not verify_share_password(provided, pw_hash):
            return jsonify({"error": "Password required", "password_required": True}), 401
    fdata = link_data.get("files", {})
    file_id = link_data["file_id"]
    org_id = fdata.get("org_id")
    filename = fdata.get("name", "file")
    sup.table("shared_links").update({"download_count": (link_data.get("download_count") or 0) + 1}).eq("id", link_data["id"]).execute()
    ver_result = sup.table("file_versions").select("*").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "Telegram chat not configured"}), 500

    def generate():
        for chunk in telegram_service.download_chunks_streaming(chat_id, message_ids):
            yield chunk

    print(f"[SHARED] Download: '{filename}', token={token[:8]}...")
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Content-Length"] = str(size_bytes)
    return resp


@sharing_bp.route("/api/shared/<token>/info", methods=["GET"])
def api_shared_info(token):
    sup = get_supabase()
    link = sup.table("shared_links").select("*, files(name, org_id)").eq("token", token).maybe_single().execute()
    if not link or not link.data:
        return jsonify({"error": "Link not found"}), 404
    link_data = link.data
    fdata = link_data.get("files", {})
    file_id = link_data["file_id"]
    ver_result = sup.table("file_versions").select("version_number, size_bytes, sha256, uploaded_at").eq("file_id", file_id).eq("is_current", True).maybe_single().execute()
    ver = ver_result.data if ver_result and ver_result.data else {}
    return jsonify({
        "filename": fdata.get("name"),
        "size_bytes": ver.get("size_bytes"),
        "version": ver.get("version_number"),
        "has_password": bool(link_data.get("password_hash")),
        "expires_at": link_data.get("expires_at"),
        "download_count": link_data.get("download_count", 0),
    })


@sharing_bp.route("/api/shared/<token>/preview", methods=["GET"])
def api_shared_preview(token):
    sup = get_supabase()
    link = sup.table("shared_links").select("*, files(name, org_id)").eq("token", token).maybe_single().execute()
    if not link or not link.data:
        return jsonify({"error": "Link not found"}), 404
    link_data = link.data
    pw_hash = link_data.get("password_hash")
    if pw_hash:
        provided = request.args.get("password", "")
        if not verify_share_password(provided, pw_hash):
            return jsonify({"error": "Password required", "password_required": True}), 401
    fdata = link_data.get("files", {})
    file_id = link_data["file_id"]
    org_id = fdata.get("org_id")
    filename = fdata.get("name", "file")
    ver_result = sup.table("file_versions").select("message_ids, size_bytes").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "Telegram chat not configured"}), 500

    def generate():
        for chunk in telegram_service.download_chunks_streaming(chat_id, message_ids):
            yield chunk

    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp
