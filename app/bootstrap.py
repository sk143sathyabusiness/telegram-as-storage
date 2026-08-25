"""
app.bootstrap — one-time master_admin creation (Task A).

POST /api/bootstrap  — creates the first master_admin if none exists.
Body: {username, password}  (fallback to MASTER_ADMIN_* env)
Once a master_admin exists, returns 403 "already initialized".
"""
from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from app.supabase_client import get_supabase
from app.config import MASTER_ADMIN_USERNAME, MASTER_ADMIN_BOOTSTRAP_PASSWORD

bootstrap_bp = Blueprint("bootstrap", __name__)

@bootstrap_bp.route("/api/bootstrap", methods=["POST"])
def api_bootstrap():
    sup = get_supabase()
    # Check if master_admin already exists
    existing = sup.table("users").select("id").eq("role", "master_admin").execute()
    if existing.data:
        return jsonify({"error": "already initialized"}), 403

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or MASTER_ADMIN_USERNAME or "").strip()
    password = (data.get("password") or MASTER_ADMIN_BOOTSTRAP_PASSWORD or "").strip()

    if not username or not password:
        return jsonify({"error": "username and password required (or set MASTER_ADMIN_* env)"}), 400
    if len(password) < 6:
        return jsonify({"error": "password must be at least 6 characters"}), 400
    if len(username) < 3:
        return jsonify({"error": "username must be at least 3 characters"}), 400

    # Check username taken
    taken = sup.table("users").select("id").eq("username", username).execute()
    if taken.data:
        return jsonify({"error": "username already taken"}), 409

    user = sup.table("users").insert({
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": "master_admin",
        "org_id": None,
    }).execute()

    # Audit log (org_id null for global)
    try:
        sup.table("audit_logs").insert({
            "org_id": None,
            "actor_id": user.data[0]["id"] if user.data else None,
            "actor_role": "master_admin",
            "action": "bootstrap_master",
            "details": {"target": username},
        }).execute()
    except Exception:
        pass

    print(f"[BOOTSTRAP] Created master_admin '{username}'")
    return jsonify({"ok": True, "username": username, "role": "master_admin"}), 201


@bootstrap_bp.route("/api/bootstrap/status", methods=["GET"])
def api_bootstrap_status():
    sup = get_supabase()
    existing = sup.table("users").select("id").eq("role", "master_admin").execute()
    return jsonify({"initialized": bool(existing.data)}), 200
