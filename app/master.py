"""
app.master — cross-organisation master-admin operations (Phase-1 M5).

- GET /api/master/search?q=  → search files + users across all orgs.
"""

from flask import Blueprint, jsonify, request

from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import log_action

master_bp = Blueprint("master", __name__)


@master_bp.route("/api/master/search", methods=["GET"])
@login_required
def api_master_search():
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"files": [], "users": []})
    sup = get_supabase()

    # Files across all orgs (non-deleted)
    files = sup.table("files").select(
        "id, name, org_id, folder_id, is_deleted"
    ).eq("is_deleted", False).ilike("name", f"%{q}%").limit(50).execute().data
    file_org_ids = list(set(f["org_id"] for f in files if f.get("org_id")))
    org_map = {}
    if file_org_ids:
        orgs = sup.table("organizations").select("id, name").in_("id", file_org_ids).execute().data
        org_map = {o["id"]: o["name"] for o in orgs}
    file_results = []
    for f in files:
        file_results.append({
            "id": f["id"],
            "name": f["name"],
            "org_id": f["org_id"],
            "org_name": org_map.get(f.get("org_id"), "—"),
            "folder_id": f.get("folder_id"),
        })

    # Users across all orgs
    users = sup.table("users").select(
        "id, username, role, org_id"
    ).ilike("username", f"%{q}%").limit(50).execute().data
    user_org_ids = list(set(u["org_id"] for u in users if u.get("org_id")))
    if user_org_ids and not org_map:
        orgs = sup.table("organizations").select("id, name").in_("id", user_org_ids).execute().data
        org_map = {o["id"]: o["name"] for o in orgs}
    user_results = []
    for u in users:
        user_results.append({
            "id": u["id"],
            "username": u["username"],
            "role": u["role"],
            "org_id": u["org_id"],
            "org_name": org_map.get(u.get("org_id"), "—"),
        })

    log_action("master_search", f"q={q} files={len(file_results)} users={len(user_results)}")
    return jsonify({"files": file_results, "users": user_results})
