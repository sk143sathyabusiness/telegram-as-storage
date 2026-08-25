"""
app.users — user management and per-user permissions (Task 6).

Moved from app.py:1085-1289 (api_users_get, api_users_stats, api_users_update,
api_user_activity, api_user_permissions, api_user_permissions_set,
api_user_permission_delete, api_users_post, api_users_delete).
"""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from werkzeug.security import generate_password_hash

from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import _resolve_folder_name, log_action

users_bp = Blueprint("users", __name__)


@users_bp.route("/api/users", methods=["GET"])
@login_required
def api_users_get():
    user = current_user()
    sup = get_supabase()
    is_master_global = user["role"] == "master_admin" and not user["org_id"]
    if is_master_global:
        data = sup.table("users").select("id, username, role, created_at, org_id").order("username").execute().data
        # Enrich with org names
        org_ids = list(set(r["org_id"] for r in data if r.get("org_id")))
        org_map = {}
        if org_ids:
            orgs = sup.table("organizations").select("id, name").in_("id", org_ids).execute().data
            org_map = {o["id"]: o["name"] for o in orgs}
        result = []
        for r in data:
            d = dict(r)
            d["org_name"] = org_map.get(r.get("org_id"), "—" if not r.get("org_id") else str(r.get("org_id"))[:8])
            result.append(d)
        return jsonify(result)
    data = sup.table("users").select("id, username, role, created_at").eq("org_id", user["org_id"]).order("username").execute().data
    return jsonify([dict(r) for r in data])


@users_bp.route("/api/users/stats", methods=["GET"])
@login_required
def api_users_stats():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    is_master_global = user["role"] == "master_admin" and not user["org_id"]
    if is_master_global:
        all_users = sup.table("users").select("id, role, created_at, org_id").execute().data
        # For master global, active is across all orgs but we return global
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        month_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        week_ago_count = sum(1 for u in all_users if u.get("created_at", "") >= week_ago)
        month_ago_count = sum(1 for u in all_users if u.get("created_at", "") >= month_ago)
        # active across all
        logs = sup.table("audit_logs").select("actor_id").gte("created_at", week_ago).execute().data
    else:
        all_users = sup.table("users").select("id, role, created_at").eq("org_id", user["org_id"]).execute().data
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        month_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
        week_ago_count = sum(1 for u in all_users if u.get("created_at", "") >= week_ago)
        month_ago_count = sum(1 for u in all_users if u.get("created_at", "") >= month_ago)
        logs = sup.table("audit_logs").select("actor_id").eq("org_id", user["org_id"]).gte("created_at", week_ago).execute().data
    total = len(all_users)
    by_role = {}
    for u in all_users:
        r = u["role"]
        by_role[r] = by_role.get(r, 0) + 1
    active_actor_ids = set(l["actor_id"] for l in logs if l.get("actor_id"))
    return jsonify({
        "total": total,
        "by_role": by_role,
        "joined_this_week": week_ago_count,
        "joined_this_month": month_ago_count,
        "active_this_week": len(active_actor_ids),
    })


@users_bp.route("/api/users/me/password", methods=["POST"])
@login_required
def api_users_change_own_password():
    """Self-service password change for any logged-in user (Phase-1 O2)."""
    from werkzeug.security import check_password_hash
    user = current_user()
    data = request.get_json(force=True) or {}
    current = str(data.get("current_password", ""))
    new_pw = str(data.get("new_password", ""))
    if len(new_pw) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400
    sup = get_supabase()
    me = sup.table("users").select("id, password_hash").eq("id", user["id"]).maybe_single().execute()
    if not me or not me.data:
        return jsonify({"error": "User not found"}), 404
    stored = me.data.get("password_hash") or ""
    ok = False
    try:
        if check_password_hash(stored, current):
            ok = True
    except Exception:
        pass
    if not ok and stored.startswith("$2"):
        try:
            import bcrypt
            ok = bcrypt.checkpw(current.encode(), stored.encode())
        except Exception:
            pass
    if not ok:
        return jsonify({"error": "Current password is incorrect"}), 400
    sup.table("users").update({"password_hash": generate_password_hash(new_pw)}).eq("id", user["id"]).execute()
    log_action("change_own_password", user["username"])
    return jsonify({"ok": True})


@users_bp.route("/api/users/<uuid:user_id>", methods=["PUT"])
@login_required
def api_users_update(user_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    target = sup.table("users").select("*").eq("id", user_id).eq("org_id", user["org_id"]).maybe_single().execute()
    if not target or not target.data:
        return jsonify({"error": "User not found"}), 404
    data = request.get_json(force=True)
    updates = {}
    if "role" in data:
        if data["role"] not in ("org_admin", "read_write", "read_only"):
            return jsonify({"error": "Invalid role"}), 400
        if str(user_id) == user["id"] and data["role"] != user["role"]:
            return jsonify({"error": "Cannot change your own role"}), 400
        updates["role"] = data["role"]
    if "username" in data:
        new_name = data["username"].strip()
        if new_name and new_name != target.data["username"]:
            existing = sup.table("users").select("id").eq("username", new_name).maybe_single().execute()
            if existing and existing.data:
                return jsonify({"error": "Username already taken"}), 400
            updates["username"] = new_name
    if "password" in data and data["password"]:
        updates["password_hash"] = generate_password_hash(data["password"])
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    sup.table("users").update(updates).eq("id", user_id).execute()
    changes = list(updates.keys())
    if "password_hash" in changes:
        changes[changes.index("password_hash")] = "password"
    log_action("update_user", target.data["username"], ",".join(changes))
    print(f"[USER] Updated user '{target.data['username']}': {changes}")
    return jsonify({"ok": True})


@users_bp.route("/api/users/<uuid:user_id>/reset-password", methods=["POST"])
@login_required
def api_users_reset_password(user_id):
    """Master admin resets ANY user's password (Phase-1 M6)."""
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    data = request.get_json(force=True) or {}
    new_password = str(data.get("password", "")).strip()
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    sup = get_supabase()
    target = sup.table("users").select("id, username").eq("id", user_id).maybe_single().execute()
    if not target or not target.data:
        return jsonify({"error": "User not found"}), 404
    sup.table("users").update({"password_hash": generate_password_hash(new_password)}).eq("id", user_id).execute()
    log_action("master_reset_user_password", target.data["username"], org_id=target.data.get("org_id"))
    return jsonify({"ok": True, "username": target.data["username"]})


@users_bp.route("/api/users/<uuid:user_id>/activity", methods=["GET"])
@login_required
def api_user_activity(user_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    target = sup.table("users").select("username").eq("id", user_id).maybe_single().execute()
    if not target or not target.data:
        return jsonify({"error": "User not found"}), 404
    limit = request.args.get("limit", 100, type=int)
    logs = sup.table("audit_logs").select("*").eq("org_id", user["org_id"]).eq("actor_id", user_id).order("created_at", desc=True).limit(limit).execute().data
    result = []
    for r in logs:
        d = dict(r)
        d["ts"] = d.pop("created_at")
        d["username"] = target.data["username"]
        result.append(d)
    return jsonify(result)


@users_bp.route("/api/users/<uuid:user_id>/permissions", methods=["GET"])
@login_required
def api_user_permissions(user_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    perms = sup.table("permissions").select("*, folders(name)").eq("org_id", user["org_id"]).eq("user_id", user_id).execute().data
    result = []
    for p in perms:
        d = dict(p)
        folder = d.pop("folders", None)
        d["folder_name"] = folder["name"] if folder else "Root (all folders)"
        result.append(d)
    return jsonify(result)


@users_bp.route("/api/users/<uuid:user_id>/permissions", methods=["POST"])
@login_required
def api_user_permissions_set(user_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    target = sup.table("users").select("username").eq("id", str(user_id)).maybe_single().execute()
    if not target or not target.data:
        return jsonify({"error": "User not found"}), 404
    data = request.get_json(force=True)
    folder_id = data.get("folder_id")
    permission_level = data.get("permission_level", "read_only")
    if permission_level not in ("read_only", "read_write", "org_admin"):
        return jsonify({"error": "Invalid permission level"}), 400
    user_id_str = str(user_id)
    existing_query = sup.table("permissions").select("id").eq("org_id", user["org_id"]).eq("user_id", user_id_str)
    if folder_id:
        existing_query = existing_query.eq("folder_id", folder_id)
    else:
        existing_query = existing_query.is_("folder_id", "null")
    existing = existing_query.maybe_single().execute()
    if existing and existing.data:
        sup.table("permissions").update({"permission_level": permission_level}).eq("id", existing.data["id"]).execute()
    else:
        sup.table("permissions").insert({
            "org_id": user["org_id"],
            "user_id": user_id_str,
            "folder_id": folder_id,
            "permission_level": permission_level,
        }).execute()
    log_action("set_permission", target.data["username"], f"folder={_resolve_folder_name(sup, folder_id)} level={permission_level}")
    return jsonify({"ok": True})


@users_bp.route("/api/users/<uuid:user_id>/permissions/<uuid:perm_id>", methods=["DELETE"])
@login_required
def api_user_permission_delete(user_id, perm_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    perm = sup.table("permissions").select("folder_id, user_id").eq("id", perm_id).maybe_single().execute()
    target = sup.table("users").select("username").eq("id", user_id).maybe_single().execute()
    folder_name = _resolve_folder_name(sup, perm.data["folder_id"] if perm and perm.data else None) if perm and perm.data else "—"
    username = target.data["username"] if target and target.data else str(user_id)
    sup.table("permissions").delete().eq("id", perm_id).eq("org_id", user["org_id"]).execute()
    log_action("delete_permission", username, f"folder={folder_name}")
    return jsonify({"ok": True})


@users_bp.route("/api/users", methods=["POST"])
@login_required
def api_users_post():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "read_write")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if role not in ("org_admin", "read_write", "read_only"):
        return jsonify({"error": "Invalid role"}), 400
    sup = get_supabase()
    existing = sup.table("users").select("id").eq("username", username).maybe_single().execute()
    if existing and existing.data:
        return jsonify({"error": "Username already exists"}), 400
    sup.table("users").insert({
        "org_id": user["org_id"],
        "username": username,
        "password_hash": generate_password_hash(password),
        "role": role,
    }).execute()
    log_action("create_user", username, f"role={role}")
    return jsonify({"ok": True})


@users_bp.route("/api/users/<uuid:user_id>", methods=["DELETE"])
@login_required
def api_users_delete(user_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    if str(user_id) == user["id"]:
        return jsonify({"error": "Cannot remove yourself"}), 400
    sup = get_supabase()
    target = sup.table("users").select("username").eq("id", user_id).maybe_single().execute()
    if not target or not target.data:
        return jsonify({"error": "User not found"}), 404
    sup.table("permissions").delete().eq("user_id", user_id).eq("org_id", user["org_id"]).execute()
    sup.table("users").delete().eq("id", user_id).eq("org_id", user["org_id"]).execute()
    log_action("delete_user", target.data["username"])
    return jsonify({"ok": True})
