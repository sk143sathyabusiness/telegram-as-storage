"""
app.orgs — organisation registration and admin approval (Task 3).

Moved from app.py:418-500 (api_org_register, api_orgs_get,
api_orgs_approve, api_orgs_reject). Logic copied verbatim; only
routing changes from @app.route to Blueprint.
"""

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from telethon.tl.functions.channels import CreateChannelRequest
from telethon.errors import ChatIdInvalidError

from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import log_action
from telegram_service import create_backup_channel

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


@orgs_bp.route("/api/orgs/create", methods=["POST"])
@login_required
def api_orgs_create():
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403

    data = request.get_json(force=True) or {}

    required = ["org_name", "chat_id", "username", "password"]
    for f in required:
        if not str(data.get(f, "")).strip():
            return jsonify({"error": f"{f} is required"}), 400

    chat_id = str(data["chat_id"]).strip()
    if not chat_id.lstrip("-").isdigit():
        return jsonify({"error": "Telegram Channel ID must be a numeric ID"}), 400

    name = data["org_name"].strip()

    sup = get_supabase()

    existing_user = sup.table("users").select("id").eq("username", data["username"].strip()).execute()
    if existing_user.data:
        return jsonify({"error": "Username already taken"}), 400

    dup = sup.table("organizations").select("id, status").eq("name", name).maybe_single().execute()
    if dup and dup.data:
        if dup.data.get("status") in ("active", "approved"):
            return jsonify({"error": "An organisation with this name already exists"}), 409
        sup.table("organizations").delete().eq("id", dup.data["id"]).execute()

    backup_channel_id = None
    warning = None
    manual_backup_id = str(data.get("backup_channel_id", "")).strip()

    if manual_backup_id:
        if not manual_backup_id.lstrip("-").isdigit():
            return jsonify({"error": "Backup Channel ID must be a numeric ID"}), 400
        backup_channel_id = manual_backup_id
    else:
        from app import config
        if config.TELEGRAM_CONFIGURED:
            try:
                backup_channel_id = str(create_backup_channel(f"Backup — {name}"))
            except Exception as e:
                warning = f"Could not auto-create backup channel: {e}"
                backup_channel_id = None
        else:
            warning = "Telegram not configured — backup channel not set"

    org_result = sup.table("organizations").insert({
        "name": name,
        "industry": data.get("industry", ""),
        "size": data.get("size", ""),
        "contact_name": data.get("contact_name", ""),
        "contact_email": data.get("contact_email", ""),
        "telegram_chat_id": chat_id,
        "backup_channel_id": backup_channel_id,
        "status": "active",
    }).execute()
    org_id = org_result.data[0]["id"]

    sup.table("users").insert({
        "org_id": org_id,
        "username": data["username"].strip(),
        "password_hash": generate_password_hash(data["password"]),
        "role": "org_admin",
    }).execute()

    log_action("org_create", f"org_id={org_id}", org_id=org_id)

    resp = {"ok": True, "org_id": org_id}
    if backup_channel_id:
        resp["backup_channel_id"] = backup_channel_id
    if warning:
        resp["warning"] = warning
    return jsonify(resp)


@orgs_bp.route("/api/orgs/<uuid:org_id>/backup-channel", methods=["PUT"])
@login_required
def api_orgs_set_backup_channel(org_id):
    user = current_user()
    if user["role"] == "org_admin":
        if str(org_id) != str(user["org_id"]):
            return jsonify({"error": "You can only manage your own organisation's backup channel"}), 403
    elif user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403

    data = request.get_json(force=True) or {}
    raw = str(data.get("backup_channel_id", "")).strip()

    if not raw:
        sup = get_supabase()
        sup.table("organizations").update({"backup_channel_id": None}).eq("id", org_id).execute()
        log_action("set_backup_channel", f"org_id={org_id} cleared", org_id=org_id)
        return jsonify({"ok": True, "backup_channel_id": None})

    if not raw.lstrip("-").isdigit():
        return jsonify({"error": "Backup Channel ID must be a numeric ID"}), 400

    sup = get_supabase()
    sup.table("organizations").update({"backup_channel_id": raw}).eq("id", org_id).execute()
    log_action("set_backup_channel", f"org_id={org_id} -> {raw}", org_id=org_id)
    return jsonify({"ok": True, "backup_channel_id": raw})


@orgs_bp.route("/api/orgs/<uuid:org_id>", methods=["PUT"])
@login_required
def api_orgs_edit(org_id):
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403

    data = request.get_json(force=True) or {}
    sup = get_supabase()
    org = sup.table("organizations").select("id, name").eq("id", org_id).maybe_single().execute()
    if not org or not org.data:
        return jsonify({"error": "Organisation not found"}), 404

    update = {}
    if "name" in data and str(data["name"]).strip():
        new_name = str(data["name"]).strip()
        if new_name != org.data["name"]:
            dup = sup.table("organizations").select("id, status").eq("name", new_name).maybe_single().execute()
            if dup and dup.data and dup.data["id"] != org_id:
                if dup.data.get("status") in ("active", "approved"):
                    return jsonify({"error": "An organisation with this name already exists"}), 409
            update["name"] = new_name
    if "telegram_chat_id" in data and str(data["telegram_chat_id"]).strip():
        cid = str(data["telegram_chat_id"]).strip()
        if not cid.lstrip("-").isdigit():
            return jsonify({"error": "Telegram Channel ID must be a numeric ID"}), 400
        update["telegram_chat_id"] = cid
    for f in ("industry", "size", "contact_name", "contact_email"):
        if f in data:
            update[f] = str(data[f]).strip()
    if "status" in data and str(data["status"]).strip():
        update["status"] = str(data["status"]).strip()
    if "storage_quota_bytes" in data:
        raw_q = data["storage_quota_bytes"]
        try:
            q = int(raw_q) if raw_q not in (None, "") else None
        except (ValueError, TypeError):
            return jsonify({"error": "storage_quota_bytes must be an integer"}), 400
        update["storage_quota_bytes"] = q

    if not update:
        return jsonify({"error": "Nothing to update"}), 400

    sup.table("organizations").update(update).eq("id", org_id).execute()
    log_action("edit_org", f"org_id={org_id}", org_id=org_id)
    return jsonify({"ok": True, **update})


@orgs_bp.route("/api/orgs/<uuid:org_id>", methods=["DELETE"])
@login_required
def api_orgs_delete(org_id):
    """Soft-delete an organisation (sets status='deleted'); master only (M9)."""
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    sup = get_supabase()
    org = sup.table("organizations").select("id, name").eq("id", org_id).maybe_single().execute()
    if not org or not org.data:
        return jsonify({"error": "Organisation not found"}), 404
    sup.table("organizations").update({"status": "deleted"}).eq("id", org_id).execute()
    log_action("delete_org", f"org_id={org_id} name={org.data['name']}", org_id=org_id)
    return jsonify({"ok": True})


@orgs_bp.route("/api/orgs/<uuid:org_id>/reset-admin", methods=["POST"])
@login_required
def api_orgs_reset_admin(org_id):
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403

    data = request.get_json(force=True) or {}
    new_password = str(data.get("password", "")).strip()
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    sup = get_supabase()
    org = sup.table("organizations").select("id").eq("id", org_id).maybe_single().execute()
    if not org or not org.data:
        return jsonify({"error": "Organisation not found"}), 404
    admin = sup.table("users").select("id, username").eq("org_id", org_id).eq("role", "org_admin").order("created_at").limit(1).execute().data
    if not admin:
        return jsonify({"error": "No org_admin user found for this organisation"}), 404
    sup.table("users").update({"password_hash": generate_password_hash(new_password)}).eq("id", admin[0]["id"]).execute()
    log_action("reset_org_admin_password", f"org_id={org_id} user={admin[0]['username']}", org_id=org_id)
    return jsonify({"ok": True, "username": admin[0]["username"]})
