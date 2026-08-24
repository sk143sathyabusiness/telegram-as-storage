"""
app.trash — soft-delete trash bin and permanent hard-delete (Task 5).

Moved from app.py:1024-1083 (api_trash_get, api_trash_restore,
api_trash_hard_delete). Uses get_supabase, _parse_message_ids,
telegram_service.delete_file via asyncio.run with silent except.
"""

import asyncio

from flask import Blueprint, jsonify

import telegram_service
from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import _parse_message_ids, _resolve_folder_name, log_action

trash_bp = Blueprint("trash", __name__)


def _tg_configured():
    return telegram_service.is_configured()


@trash_bp.route("/api/trash", methods=["GET"])
@login_required
def api_trash_get():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify([])
    sup = get_supabase()
    rows = sup.table("files").select("*").eq("org_id", user["org_id"]).eq("is_deleted", True).order("deleted_at", desc=True).execute().data
    result = []
    for r in rows:
        d = dict(r)
        if r.get("deleted_by"):
            del_user = sup.table("users").select("username").eq("id", r["deleted_by"]).maybe_single().execute()
            d["deleted_by_name"] = del_user.data["username"] if del_user and del_user.data else None
        else:
            d["deleted_by_name"] = None
        result.append(d)
    return jsonify(result)


@trash_bp.route("/api/trash/<uuid:file_id>/restore", methods=["POST"])
@login_required
def api_trash_restore(file_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    sup.table("files").update({"is_deleted": False, "deleted_at": None, "deleted_by": None}).eq("id", file_id).eq("org_id", user["org_id"]).execute()
    f = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    folder = _resolve_folder_name(sup, f.data.get("folder_id") if f and f.data else None) if f and f.data else "—"
    log_action("restore_from_trash", f.data["name"] if f and f.data else str(file_id), f"folder={folder}")
    return jsonify({"ok": True})


@trash_bp.route("/api/trash/<uuid:file_id>", methods=["DELETE"])
@login_required
def api_trash_hard_delete(file_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    f = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    versions = sup.table("file_versions").select("message_ids").eq("file_id", file_id).execute().data
    if _tg_configured() and versions:
        org = sup.table("organizations").select("telegram_chat_id").eq("id", user["org_id"]).maybe_single().execute()
        chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
        if chat_id:
            all_message_ids = []
            for v in versions:
                if v.get("message_ids"):
                    all_message_ids.extend(_parse_message_ids(v["message_ids"]))
            if all_message_ids:
                try:
                    asyncio.run(telegram_service.delete_file(chat_id, all_message_ids))
                except Exception:
                    pass
    sup.table("file_versions").delete().eq("file_id", file_id).execute()
    sup.table("files").delete().eq("id", file_id).eq("org_id", user["org_id"]).execute()
    folder = _resolve_folder_name(sup, f.data.get("folder_id") if f and f.data else None) if f and f.data else "—"
    ver_count = len(versions) if versions else 0
    log_action("permanent_delete", f.data["name"] if f and f.data else str(file_id), f"{ver_count} version(s) · folder={folder}")
    return jsonify({"ok": True})
