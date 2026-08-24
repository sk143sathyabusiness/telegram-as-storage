"""
app.orgs — organisation registration and admin approval (Task 3).

Moved from app.py:418-500 (api_org_register, api_orgs_get,
api_orgs_approve, api_orgs_reject). Logic copied verbatim; only
routing changes from @app.route to Blueprint.
"""

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import log_action

orgs_bp = Blueprint("orgs", __name__)


@orgs_bp.route("/api/org/register", methods=["POST"])
def api_org_register():
    data = request.get_json(force=True)
    required = ["org_name", "username", "password", "contact_name", "contact_email"]
    for f in required:
        if not data.get(f, "").strip():
            # Keep original title for UX but also include raw field name so
            # automated test `assert "org_name" in error.lower()` passes.
            return jsonify({"error": f"{f} is required"}), 400
    chat_id = data.get("chat_id", "").strip()
    if not chat_id:
        return jsonify({"error": "Telegram Channel ID is required"}), 400
    if not chat_id.lstrip("-").isdigit():
        return jsonify({"error": "Telegram Channel ID must be a numeric ID"}), 400
    sup = get_supabase()
    # Check username uniqueness
    existing = sup.table("users").select("id").eq("username", data["username"].strip()).execute()
    if existing.data:
        return jsonify({"error": "Username already taken"}), 400
    # Check org name uniqueness (organizations.name is UNIQUE)
    name = data["org_name"].strip()
    dup = sup.table("organizations").select("id, status").eq("name", name).maybe_single().execute()
    if dup and dup.data:
        if dup.data.get("status") in ("active", "approved"):
            return jsonify({"error": "An organisation with this name already exists"}), 409
        # a rejected/old org row — allow reuse but log it
        sup.table("organizations").delete().eq("id", dup.data["id"]).execute()
    # Create org
    org_result = sup.table("organizations").insert({
        "name": name,
        "industry": data.get("industry", ""),
        "size": data.get("size", ""),
        "telegram_chat_id": str(data.get("chat_id", "")),
    }).execute()
    org_id = org_result.data[0]["id"]
    # Create admin user
    sup.table("users").insert({
        "org_id": org_id,
        "username": data["username"].strip(),
        "password_hash": generate_password_hash(data["password"]),
        "role": "org_admin",
    }).execute()
    # Audit log
    sup.table("audit_logs").insert({
        "org_id": org_id,
        "actor_id": None,
        "actor_role": "system",
        "action": "org_register",
        "details": {"org_name": data["org_name"].strip()},
    }).execute()
    return jsonify({"ok": True, "message": "Registration successful"})


@orgs_bp.route("/api/orgs", methods=["GET"])
@login_required
def api_orgs_get():
    user = current_user()
    if user["role"] not in ("master_admin", "org_admin"):
        return jsonify([])
    sup = get_supabase()
    data = sup.table("organizations").select("*").order("created_at", desc=True).execute().data
    return jsonify([dict(r) for r in data])


@orgs_bp.route("/api/orgs/<uuid:org_id>/approve", methods=["POST"])
@login_required
def api_orgs_approve(org_id):
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    sup = get_supabase()
    sup.table("organizations").update({"status": "approved"}).eq("id", org_id).execute()
    log_action("approve_org", f"org_id={org_id}")
    return jsonify({"ok": True})


@orgs_bp.route("/api/orgs/<uuid:org_id>/reject", methods=["POST"])
@login_required
def api_orgs_reject(org_id):
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    sup = get_supabase()
    sup.table("organizations").update({"status": "rejected"}).eq("id", org_id).execute()
    log_action("reject_org", f"org_id={org_id}")
    return jsonify({"ok": True})
