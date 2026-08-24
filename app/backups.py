"""
app.backups — backup to Telegram channel (Task 6).

Moved from app.py:1655-1815 (api_backup_list, api_backup_create,
api_backup_restore, api_backup_download, api_backup_delete).
Metadata JSON is uploaded via telegram_service.upload_chunks to
BACKUP_CHANNEL_ID (app.config). Zero local storage.
"""

import asyncio
import json
from datetime import datetime

from flask import Blueprint, jsonify, Response

import telegram_service
from app.config import BACKUP_CHANNEL_ID
from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import log_action

backups_bp = Blueprint("backups", __name__)


def _backup_channel_ok():
    if not BACKUP_CHANNEL_ID:
        return False, jsonify({"error": "BACKUP_CHANNEL_ID not configured in .env"}), 500
    return True, None, None


@backups_bp.route("/api/backup/list", methods=["GET"])
@login_required
def api_backup_list():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    try:
        rows = sup.table("backups").select("id,name,size_bytes,created_at,created_by").eq("org_id", user["org_id"]).order("created_at", desc=True).execute().data
    except Exception as e:
        if "PGRST205" in str(e):
            return jsonify({"error": "Backups table not set up. Run the migration SQL first.", "sql_hint": True}), 503
        raise
    return jsonify(rows)


@backups_bp.route("/api/backup/create", methods=["POST"])
@login_required
def api_backup_create():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    org_id = user["org_id"]
    print(f"[BACKUP] Creating backup for org {org_id}")
    tables_data = {}
    for table_name in ["organizations", "users", "folders", "files", "file_versions", "permissions", "audit_logs"]:
        try:
            rows = sup.table(table_name).select("*").eq("org_id", org_id).execute().data
            tables_data[table_name] = [dict(r) for r in rows]
        except Exception:
            try:
                rows = sup.table(table_name).select("*").execute().data
                tables_data[table_name] = [dict(r) for r in rows if dict(r).get("org_id") == org_id]
            except Exception as e:
                print(f"[BACKUP] Warning: could not read table '{table_name}': {e}")
                tables_data[table_name] = []
    backup_payload = {
        "version": 1,
        "org_id": org_id,
        "created_at": datetime.utcnow().isoformat(),
        "created_by": user["username"],
        "tables": tables_data,
    }
    backup_bytes = json.dumps(backup_payload, indent=2, default=str).encode("utf-8")
    size = len(backup_bytes)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    remote_name = f"backup_{org_id}_{timestamp}.json"
    print(f"[BACKUP] Uploading {size} bytes to Telegram channel {BACKUP_CHANNEL_ID}...")
    message_ids = telegram_service.upload_chunks(backup_bytes, remote_name, BACKUP_CHANNEL_ID)
    msg_id = message_ids[0]
    print(f"[BACKUP] Uploaded — message_id={msg_id}")
    try:
        sup.table("backups").insert({
            "org_id": org_id,
            "name": remote_name,
            "size_bytes": size,
            "message_id": msg_id,
            "created_by": user["id"],
        }).execute()
    except Exception as e:
        if "PGRST205" in str(e):
            return jsonify({"error": "Backups table not set up. Run the migration SQL first.", "sql_hint": True}), 503
        raise
    log_action("create_backup", remote_name, f"size={size}")
    return jsonify({"ok": True, "name": remote_name, "size_bytes": size})


@backups_bp.route("/api/backup/restore/<path:name>", methods=["POST"])
@login_required
def api_backup_restore(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    print(f"[BACKUP] Downloading backup from Telegram (message_id={msg_id})...")
    backup_bytes = telegram_service.download_chunks(BACKUP_CHANNEL_ID, [msg_id])
    backup = json.loads(backup_bytes.decode("utf-8"))
    if backup.get("org_id") != user["org_id"]:
        return jsonify({"error": "Backup belongs to another organisation"}), 403
    org_id = user["org_id"]
    tables = backup.get("tables", {})
    restore_order = ["audit_logs", "permissions", "file_versions", "files", "folders", "users", "organizations"]
    restored = 0
    for table_name in restore_order:
        rows = tables.get(table_name, [])
        if not rows:
            continue
        try:
            sup.table(table_name).delete().eq("org_id", org_id).execute()
        except Exception:
            pass
        for row in rows:
            row.pop("id", None)
            row.pop("created_at", None)
            try:
                sup.table(table_name).insert(row).execute()
                restored += 1
            except Exception as e:
                print(f"[BACKUP] Warning: failed to restore row in '{table_name}': {e}")
    log_action("restore_backup", name, f"rows={restored}")
    print(f"[BACKUP] Restored from '{name}', {restored} rows")
    return jsonify({"ok": True, "restored_rows": restored})


@backups_bp.route("/api/backup/download/<path:name>", methods=["GET"])
@login_required
def api_backup_download(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    backup_bytes = telegram_service.download_chunks(BACKUP_CHANNEL_ID, [msg_id])
    log_action("download_backup", name)
    resp = Response(backup_bytes, mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@backups_bp.route("/api/backup/delete/<path:name>", methods=["DELETE"])
@login_required
def api_backup_delete(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    print(f"[BACKUP] Deleting backup from Telegram (message_id={msg_id})...")
    asyncio.run(telegram_service.delete_file(BACKUP_CHANNEL_ID, [msg_id]))
    sup.table("backups").delete().eq("id", record.data["id"]).execute()
    log_action("delete_backup", name)
    print(f"[BACKUP] Deleted backup '{name}'")
    return jsonify({"ok": True})
