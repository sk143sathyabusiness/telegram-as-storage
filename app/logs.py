"""
app.logs — audit log listing (Task 6).

Moved from app.py:1291-1327 (api_logs_get).
"""

import json

from flask import Blueprint, jsonify, request

from app.supabase_client import get_supabase
from app.security import login_required, current_user

logs_bp = Blueprint("logs", __name__)


@logs_bp.route("/api/logs", methods=["GET"])
@login_required
def api_logs_get():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify([])
    limit = request.args.get("limit", 300, type=int)
    sup = get_supabase()
    data = sup.table("audit_logs").select("*").eq("org_id", user["org_id"]).order("created_at", desc=True).limit(limit).execute().data
    actor_ids = list(set(r["actor_id"] for r in data if r.get("actor_id")))
    user_map = {}
    if actor_ids:
        users = sup.table("users").select("id, username").in_("id", actor_ids).execute().data
        user_map = {u["id"]: u["username"] for u in users}
    result = []
    for r in data:
        d = dict(r)
        d["ts"] = d.pop("created_at")
        d["user_id"] = d.pop("actor_id")
        d["role"] = d.pop("actor_role")
        d["username"] = user_map.get(d["user_id"])
        details = d.pop("details", None)
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}
        if isinstance(details, dict):
            d["target"] = details.get("target")
            d["detail"] = details.get("detail")
        else:
            d["target"] = None
            d["detail"] = None
        result.append(d)
    return jsonify(result)
