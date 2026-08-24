"""
app.versions — file version history and restore (Task 5).

Moved from app.py:977-1021,1331-1354 (api_versions, api_restore_version,
api_versions_all). Uses get_supabase, _check_permission, _parse_message_ids,
and log_action. No Telegram interaction needed for version listing/restore
(only trash hard-delete touches Telegram).
"""

from flask import Blueprint, jsonify

from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import _check_permission, _resolve_folder_name, _parse_message_ids, log_action

versions_bp = Blueprint("versions", __name__)


@versions_bp.route("/api/files/<uuid:file_id>/versions", methods=["GET"])
@login_required
def api_versions(file_id):
    sup = get_supabase()
    user = current_user()
    file_check = sup.table("files").select("org_id, folder_id").eq("id", file_id).maybe_single().execute()
    if not file_check or not file_check.data or file_check.data["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    perm = _check_permission(sup, user["id"], user["org_id"], file_check.data.get("folder_id"))
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    rows = sup.table("file_versions").select("*").eq("file_id", file_id).order("version_number", desc=True).execute().data
    result = []
    for r in rows:
        d = dict(r)
        d["message_ids"] = _parse_message_ids(d.get("message_ids"))
        uploader = sup.table("users").select("username").eq("id", r["uploaded_by"]).maybe_single().execute()
        d["uploaded_by_name"] = uploader.data["username"] if uploader and uploader.data else None
        d["is_current"] = bool(r["is_current"])
        result.append(d)
    return jsonify(result)


@versions_bp.route("/api/files/<uuid:file_id>/restore/<int:version_no>", methods=["POST"])
@login_required
def api_restore_version(file_id, version_no):
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    sup = get_supabase()
    f = sup.table("files").select("name, folder_id, org_id").eq("id", file_id).maybe_single().execute()
    if not f or not f.data or f.data["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    perm = _check_permission(sup, user["id"], user["org_id"], f.data.get("folder_id"))
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    ver = sup.table("file_versions").select("id").eq("file_id", file_id).eq("version_number", version_no).maybe_single().execute()
    if not ver or not ver.data:
        return jsonify({"error": "Version not found"}), 404
    sup.table("file_versions").update({"is_current": False}).eq("file_id", file_id).execute()
    sup.table("file_versions").update({"is_current": True}).eq("id", ver.data["id"]).execute()
    f2 = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    folder = _resolve_folder_name(sup, f2.data.get("folder_id") if f2 and f2.data else None) if f2 and f2.data else "—"
    log_action("restore_version", f2.data["name"] if f2 and f2.data else str(file_id), f"v{version_no} · folder={folder}", target_type="file", target_id=file_id)
    return jsonify({"ok": True})


@versions_bp.route("/api/versions/all", methods=["GET"])
@login_required
def api_versions_all():
    user = current_user()
    sup = get_supabase()
    files_data = sup.table("files").select("id, name").eq("org_id", user["org_id"]).eq("is_deleted", False).execute().data
    file_ids = [f["id"] for f in files_data]
    file_map = {f["id"]: f["name"] for f in files_data}
    if not file_ids:
        return jsonify([])
    versions = sup.table("file_versions").select("*").in_("file_id", file_ids).order("uploaded_at", desc=True).limit(500).execute().data
    uploader_ids = list(set(v["uploaded_by"] for v in versions if v.get("uploaded_by")))
    users_data = {}
    if uploader_ids:
        users_result = sup.table("users").select("id, username").in_("id", uploader_ids).execute().data
        users_data = {u["id"]: u["username"] for u in users_result}
    result = []
    for v in versions:
        d = dict(v)
        d["filename"] = file_map.get(v["file_id"])
        d["uploaded_by_name"] = users_data.get(v["uploaded_by"])
        d["is_current"] = bool(v["is_current"])
        result.append(d)
    return jsonify(result)
