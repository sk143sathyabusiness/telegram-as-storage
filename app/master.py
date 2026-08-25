"""
app.master — master_admin acting as org_admin (impersonation).

POST /api/master/switch-org  {org_id}  — master only, sets session act_as_org_id
POST /api/master/clear       — master only, clears act_as
GET  /api/master/context     — returns current effective org
"""
from flask import Blueprint, jsonify, request, session

from app.security import login_required, current_user
from app.supabase_client import get_supabase

master_bp = Blueprint("master", __name__)

@master_bp.route("/api/master/switch-org", methods=["POST"])
@login_required
def api_master_switch():
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    data = request.get_json(silent=True) or {}
    org_id = (data.get("org_id") or "").strip()
    if not org_id:
        return jsonify({"error": "org_id required"}), 400
    sup = get_supabase()
    org = sup.table("organizations").select("id, name, status").eq("id", org_id).maybe_single().execute()
    if not org or not org.data:
        return jsonify({"error": "Organisation not found"}), 404
    session["act_as_org_id"] = org_id
    session["_last_activity"] = __import__("datetime").datetime.utcnow().timestamp()
    print(f"[MASTER] {user['username']} acting as org {org.data['name']} ({org_id})")
    return jsonify({"ok": True, "act_as_org_id": org_id, "org_name": org.data["name"]})

@master_bp.route("/api/master/clear", methods=["POST"])
@login_required
def api_master_clear():
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    session.pop("act_as_org_id", None)
    return jsonify({"ok": True})

@master_bp.route("/api/master/context", methods=["GET"])
@login_required
def api_master_context():
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    return jsonify({
        "real_org_id": user.get("real_org_id"),
        "act_as_org_id": user.get("act_as_org_id"),
        "effective_org_id": user["org_id"],
        "role": user["role"],
    })
