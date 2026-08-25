"""
app.backups — backup to Telegram channel (Task 6).

Moved from app.py:1655-1815 (api_backup_list, api_backup_create,
api_backup_restore, api_backup_download, api_backup_delete).
Metadata JSON is uploaded via telegram_service.upload_chunks to
the org's backup channel (from organizations.backup_channel_id,
falling back to global BACKUP_CHANNEL_ID). Zero local storage.
"""

import asyncio
import json
from datetime import datetime

from flask import Blueprint, jsonify, Response

import telegram_service
from app.config import BACKUP_CHANNEL_ID, TELEGRAM_CONFIGURED
from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import log_action

backups_bp = Blueprint("backups", __name__)


def _get_org_backup_channel_id(sup, org_id) -> int | None:
    """Resolve the backup channel ID for an org.
    Priority: org-specific backup_channel_id > global BACKUP_CHANNEL_ID > None.
    """
    org = sup.table("organizations").select("backup_channel_id").eq("id", org_id).maybe_single().execute()
    if org and org.data and org.data.get("backup_channel_id"):
        try:
            return int(org.data["backup_channel_id"])
        except (ValueError, TypeError):
            pass
    if BACKUP_CHANNEL_ID:
        return BACKUP_CHANNEL_ID
    return None


@backups_bp.route("/api/backup/list", methods=["GET"])
@login_required
def api_backup_list():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    is_master_global = user["role"] == "master_admin" and not user["org_id"]
    try:
        if is_master_global:
            rows = sup.table("backups").select("id,name,size_bytes,created_at,created_by,org_id").order("created_at", desc=True).execute().data
            # Enrich with org names
            org_ids = list(set(r["org_id"] for r in rows if r.get("org_id")))
            org_map = {}
            if org_ids:
                orgs = sup.table("organizations").select("id, name").in_("id", org_ids).execute().data
                org_map = {o["id"]: o["name"] for o in orgs}
            for r in rows:
                r["org_name"] = org_map.get(r.get("org_id"), str(r.get("org_id"))[:8] if r.get("org_id") else "—")
        else:
            rows = sup.table("backups").select("id,name,size_bytes,created_at,created_by").eq("org_id", user["org_id"]).order("created_at", desc=True).execute().data
    except Exception as e:
        if "PGRST205" in str(e):
            return jsonify({"error": "Backups table not set up. Run the migration SQL first.", "sql_hint": True}), 503
        raise
    return jsonify(rows)


def _make_backup(sup, org_id, user, essential_only=False):
    """Build + upload a backup for org_id. Returns the backups row dict or raises."""
    backup_channel_id = _get_org_backup_channel_id(sup, org_id)
    if not backup_channel_id:
        raise RuntimeError("No backup channel configured for this organisation")

    essential_folder_ids = []
    if essential_only:
        ef = sup.table("folders").select("id").eq("org_id", org_id).eq("is_essential", True).execute().data
        essential_folder_ids = [f["id"] for f in ef]

    tables_data = {}
    # Base tables (no file-specific filtering)
    for table_name in ["organizations", "users", "permissions", "audit_logs"]:
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

    # Folders: if essential_only, only essential folders
    if essential_only:
        tables_data["folders"] = [dict(r) for r in tables_data.get("folders", []) if r["id"] in essential_folder_ids]
    else:
        tables_data.setdefault("folders", [dict(r) for r in sup.table("folders").select("*").eq("org_id", org_id).execute().data])

    # Files + file_versions: if essential_only, only files inside essential folders
    if essential_only:
        files_rows = sup.table("files").select("*").eq("org_id", org_id).in_("folder_id", essential_folder_ids).execute().data if essential_folder_ids else []
        file_ids = [f["id"] for f in files_rows]
        versions_rows = []
        if file_ids:
            for i in range(0, len(file_ids), 100):
                batch = file_ids[i:i + 100]
                versions_rows.extend(sup.table("file_versions").select("*").in_("file_id", batch).execute().data)
        tables_data["files"] = [dict(r) for r in files_rows]
        tables_data["file_versions"] = [dict(r) for r in versions_rows]
    else:
        tables_data["files"] = [dict(r) for r in sup.table("files").select("*").eq("org_id", org_id).execute().data]
        tables_data["file_versions"] = [dict(r) for r in sup.table("file_versions").select("*").eq("org_id", org_id).execute().data]

    backup_payload = {
        "version": 1,
        "org_id": org_id,
        "essential_only": essential_only,
        "created_at": datetime.utcnow().isoformat(),
        "created_by": user["username"],
        "tables": tables_data,
    }
    backup_bytes = json.dumps(backup_payload, indent=2, default=str).encode("utf-8")
    size = len(backup_bytes)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    prefix = "daily_essential_" if essential_only else "backup_"
    remote_name = f"{prefix}{org_id}_{timestamp}.json"
    print(f"[BACKUP] Uploading {size} bytes to Telegram channel {backup_channel_id}...")
    message_ids = telegram_service.upload_chunks(backup_bytes, remote_name, backup_channel_id)
    msg_id = message_ids[0]
    print(f"[BACKUP] Uploaded — message_id={msg_id}")
    sup.table("backups").insert({
        "org_id": org_id,
        "name": remote_name,
        "size_bytes": size,
        "message_id": msg_id,
        "created_by": user["id"],
    }).execute()
    log_action("create_backup", remote_name, f"size={size} essential_only={essential_only}")
    return {"name": remote_name, "size_bytes": size}


@backups_bp.route("/api/backup/create", methods=["POST"])
@login_required
def api_backup_create():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    essential_only = bool((request.get_json(silent=True) or {}).get("essential_only"))
    try:
        result = _make_backup(sup, user["org_id"], user, essential_only=essential_only)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **result})


@backups_bp.route("/api/backup/daily", methods=["POST"])
@login_required
def api_backup_daily():
    """Scheduled daily backup of essential folders (AGENTS.md rule #6).

    Org admin / acting master: backs up their org's essential folders.
    Master global: iterates all orgs and creates an essential backup for each.
    Designed to be triggered by a cron (Vercel Cron / system cron).
    """
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    is_master_global = user["role"] == "master_admin" and not user["org_id"]
    if is_master_global:
        orgs = sup.table("organizations").select("id").eq("status", "active").execute().data
        done, failed = [], []
        for o in orgs:
            try:
                res = _make_backup(sup, o["id"], user, essential_only=True)
                done.append(res["name"])
            except Exception as e:
                failed.append({"org_id": str(o["id"]), "error": str(e)[:160]})
        return jsonify({"ok": True, "backed_up": done, "failed": failed})
    try:
        res = _make_backup(sup, user["org_id"], user, essential_only=True)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **res})


@backups_bp.route("/api/backup/restore/<path:name>", methods=["POST"])
@login_required
def api_backup_restore(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    org_id = user["org_id"]
    backup_channel_id = _get_org_backup_channel_id(sup, org_id)
    if not backup_channel_id:
        return jsonify({"error": "No backup channel configured for this organisation"}), 400
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    print(f"[BACKUP] Downloading backup from Telegram (message_id={msg_id})...")
    backup_bytes = telegram_service.download_chunks(backup_channel_id, [msg_id])
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
    sup = get_supabase()
    org_id = user["org_id"]
    backup_channel_id = _get_org_backup_channel_id(sup, org_id)
    if not backup_channel_id:
        return jsonify({"error": "No backup channel configured for this organisation"}), 400
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    backup_bytes = telegram_service.download_chunks(backup_channel_id, [msg_id])
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
    sup = get_supabase()
    org_id = user["org_id"]
    backup_channel_id = _get_org_backup_channel_id(sup, org_id)
    if not backup_channel_id:
        return jsonify({"error": "No backup channel configured for this organisation"}), 400
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    print(f"[BACKUP] Deleting backup from Telegram (message_id={msg_id})...")
    asyncio.run(telegram_service.delete_file(backup_channel_id, [msg_id]))
    sup.table("backups").delete().eq("id", record.data["id"]).execute()
    log_action("delete_backup", name)
    print(f"[BACKUP] Deleted backup '{name}'")
    return jsonify({"ok": True})
