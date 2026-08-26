"""
app.stats — organisation usage statistics (Phase-1 M3/O5).

Master admin (global) gets an aggregate list across all orgs + totals.
Org admin (or acting master) gets stats for their effective org.

Storage = sum(file_versions.size_bytes) for the org's non-deleted files.
"""

from flask import Blueprint, jsonify

from app.supabase_client import get_supabase
from app.security import login_required, current_user

stats_bp = Blueprint("stats", __name__)


def _safe(data, default=None):
    """Return PostgREST data, or default on any error (e.g. schema drift on the
    live DB where a column/table from supabase_schema.sql does not yet exist)."""
    try:
        return data
    except Exception as e:
        print(f"[STATS] query degraded: {e}")
        return default


def _stats_for_org(sup, org_id):
    # files (non-deleted) for org
    files = _safe(sup.table("files").select("id, org_id, is_deleted").eq("org_id", org_id).execute().data, [])
    file_ids = [f["id"] for f in files if not f.get("is_deleted")]
    file_count = len(file_ids)

    storage_bytes = 0
    if file_ids:
        # fetch versions for these files (batch by 100)
        vers = []
        for i in range(0, len(file_ids), 100):
            batch = file_ids[i:i + 100]
            rows = _safe(sup.table("file_versions").select("file_id, size_bytes").in_("file_id", batch).execute().data, [])
            vers.extend(rows)
        for v in vers:
            try:
                storage_bytes += int(v.get("size_bytes") or 0)
            except (TypeError, ValueError):
                pass

    users = _safe(sup.table("users").select("id").eq("org_id", org_id).execute().data, [])
    user_count = len(users)

    folders = _safe(sup.table("folders").select("id").eq("org_id", org_id).execute().data, [])
    folder_count = len(folders)

    name = "—"
    status = "—"
    backup_channel_id = None
    storage_quota_bytes = None
    org = _safe(sup.table("organizations").select("name, status, backup_channel_id, storage_quota_bytes").eq("id", org_id).maybe_single().execute(), None)
    if org and org.data:
        od = org.data
        name = od.get("name", "—")
        status = od.get("status", "—")
        backup_channel_id = od.get("backup_channel_id")
        storage_quota_bytes = od.get("storage_quota_bytes")

    last_activity = None
    al = _safe(sup.table("audit_logs").select("created_at").eq("org_id", org_id).order("created_at", desc=True).limit(1).execute().data, [])
    if al:
        last_activity = al[0]["created_at"]

    last_backup = None
    bk = _safe(sup.table("backups").select("created_at").eq("org_id", org_id).order("created_at", desc=True).limit(1).execute().data, [])
    if bk:
        last_backup = bk[0]["created_at"]

    return {
        "org_id": str(org_id),
        "name": name,
        "status": status,
        "storage_bytes": storage_bytes,
        "file_count": file_count,
        "user_count": user_count,
        "folder_count": folder_count,
        "last_activity": last_activity,
        "last_backup": last_backup,
        "backup_channel_id": backup_channel_id,
        "storage_quota_bytes": storage_quota_bytes,
    }


@stats_bp.route("/api/stats", methods=["GET"])
@login_required
def api_stats():
    user = current_user()
    sup = get_supabase()
    is_master_global = user["role"] == "master_admin" and not user["org_id"]
    if is_master_global:
        orgs = sup.table("organizations").select("id").order("created_at", desc=True).execute().data
        org_stats = [_stats_for_org(sup, o["id"]) for o in orgs]
        totals = {
            "orgs": len(org_stats),
            "storage_bytes": sum(o["storage_bytes"] for o in org_stats),
            "file_count": sum(o["file_count"] for o in org_stats),
            "user_count": sum(o["user_count"] for o in org_stats),
            "folder_count": sum(o["folder_count"] for o in org_stats),
        }
        return jsonify({"orgs": org_stats, "totals": totals})
    org_id = user["org_id"]
    if not org_id:
        return jsonify({"error": "No organisation context"}), 400
    stat = _stats_for_org(sup, org_id)
    return jsonify({"org": stat, "totals": stat})
