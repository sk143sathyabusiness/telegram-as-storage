"""
app.folders — folder CRUD and folder-scoped permissions (Task 3).

Moved from app.py:502-727 (api_folders_get/post/delete and all
permission routes 591-727). Keeps _add_ancestors helper verbatim.
"""

from flask import Blueprint, jsonify, request

from app.supabase_client import get_supabase
from app.security import login_required, current_user
from app.utils import _check_permission, _require_active_org, log_action

folders_bp = Blueprint("folders", __name__)


def _add_ancestors(folder, all_folders, visible_ids):
    parent_id = folder.get("parent_id")
    while parent_id:
        if parent_id in visible_ids:
            break
        visible_ids.add(parent_id)
        parent = next((f for f in all_folders if f["id"] == parent_id), None)
        if parent:
            parent_id = parent.get("parent_id")
        else:
            break


@folders_bp.route("/api/folders", methods=["GET"])
@login_required
def api_folders_get():
    user = current_user()
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    data = sup.table("folders").select("id, name, parent_id").eq("org_id", user["org_id"]).order("name").execute().data
    if user["role"] in ("org_admin", "master_admin"):
        return jsonify([dict(r) for r in data])
    visible_ids = set()
    folders_map = {f["id"]: f for f in data}
    for f in data:
        fid = f["id"]
        if not f.get("parent_id"):
            perm = _check_permission(sup, user["id"], user["org_id"], fid)
            if perm:
                visible_ids.add(fid)
                _add_ancestors(f, data, visible_ids)
    changed = True
    while changed:
        changed = False
        for f in data:
            fid = f["id"]
            if fid in visible_ids:
                continue
            parent_id = f.get("parent_id")
            if parent_id and parent_id in visible_ids:
                perm = _check_permission(sup, user["id"], user["org_id"], fid)
                if perm:
                    visible_ids.add(fid)
                    changed = True
    filtered = [dict(f) for f in data if f["id"] in visible_ids]
    return jsonify(filtered)


@folders_bp.route("/api/folders", methods=["POST"])
@login_required
def api_folders_post():
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Folder name required"}), 400
    parent_id = data.get("parent_id")
    sup = get_supabase()
    sup.table("folders").insert({"org_id": user["org_id"], "name": name, "parent_id": parent_id}).execute()
    log_action("create_folder", name)
    return jsonify({"ok": True})


@folders_bp.route("/api/folders/<uuid:folder_id>", methods=["DELETE"])
@login_required
def api_folders_delete(folder_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    folder = sup.table("folders").select("name, org_id").eq("id", folder_id).maybe_single().execute()
    if not folder or not folder.data:
        return jsonify({"error": "Folder not found"}), 404
    if folder.data["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied"}), 403
    files_in_folder = sup.table("files").select("id").eq("folder_id", folder_id).eq("is_deleted", False).execute().data
    if files_in_folder:
        return jsonify({"error": f"Cannot delete folder — it contains {len(files_in_folder)} file(s). Move or delete them first."}), 400
    children = sup.table("folders").select("id").eq("parent_id", folder_id).execute().data
    if children:
        return jsonify({"error": f"Cannot delete folder — it has {len(children)} subfolder(s). Delete them first."}), 400
    sup.table("permissions").delete().eq("folder_id", folder_id).eq("org_id", user["org_id"]).execute()
    sup.table("folders").delete().eq("id", folder_id).eq("org_id", user["org_id"]).execute()
    log_action("delete_folder", folder.data["name"])
    print(f"[FOLDER] Deleted folder '{folder.data['name']}' (id={folder_id})")
    return jsonify({"ok": True})


# ── Folder permissions ────────────────────────────────────────────────────

@folders_bp.route("/api/folders/permissions/all", methods=["GET"])
@login_required
def api_folders_permissions_all():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    folders = sup.table("folders").select("id, name, parent_id").eq("org_id", user["org_id"]).order("name").execute().data
    perms = sup.table("permissions").select("id, user_id, folder_id, permission_level, created_at").eq("org_id", user["org_id"]).execute().data
    user_ids = list(set(p["user_id"] for p in perms))
    user_map = {}
    if user_ids:
        users_data = sup.table("users").select("id, username").in_("id", user_ids).execute().data
        user_map = {u["id"]: u["username"] for u in users_data}
    perm_by_folder = {}
    for p in perms:
        fid = p["folder_id"]
        if fid not in perm_by_folder:
            perm_by_folder[fid] = []
        perm_by_folder[fid].append({
            "id": p["id"],
            "user_id": p["user_id"],
            "username": user_map.get(p["user_id"], "Unknown"),
            "permission_level": p["permission_level"],
            "created_at": p["created_at"],
        })
    result = []
    for f in folders:
        folder_perms = perm_by_folder.get(f["id"], [])
        result.append({
            "id": f["id"],
            "name": f["name"],
            "parent_id": f["parent_id"],
            "permissions": folder_perms,
            "user_count": len(folder_perms),
        })
    return jsonify(result)


@folders_bp.route("/api/folders/<uuid:folder_id>/permissions", methods=["GET"])
@login_required
def api_folder_permissions(folder_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    perms = sup.table("permissions").select("id, user_id, permission_level, created_at").eq("org_id", user["org_id"]).eq("folder_id", folder_id).execute().data
    user_ids = [p["user_id"] for p in perms]
    user_map = {}
    if user_ids:
        users_data = sup.table("users").select("id, username").in_("id", user_ids).execute().data
        user_map = {u["id"]: u["username"] for u in users_data}
    result = []
    for p in perms:
        result.append({
            "id": p["id"],
            "user_id": p["user_id"],
            "username": user_map.get(p["user_id"], "Unknown"),
            "permission_level": p["permission_level"],
            "created_at": p["created_at"],
        })
    return jsonify(result)


@folders_bp.route("/api/folders/<uuid:folder_id>/permissions", methods=["POST"])
@login_required
def api_folder_permissions_add(folder_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    folder = sup.table("folders").select("name").eq("id", folder_id).maybe_single().execute()
    if not folder or not folder.data:
        return jsonify({"error": "Folder not found"}), 404
    data = request.get_json(force=True)
    target_user_id = data.get("user_id")
    permission_level = data.get("permission_level", "read_only")
    if not target_user_id:
        return jsonify({"error": "user_id required"}), 400
    if permission_level not in ("read_only", "read_write", "org_admin"):
        return jsonify({"error": "Invalid permission level"}), 400
    target = sup.table("users").select("username").eq("id", target_user_id).maybe_single().execute()
    if not target or not target.data:
        return jsonify({"error": "User not found"}), 404
    folder_id_str = str(folder_id)
    existing_query = sup.table("permissions").select("id").eq("org_id", user["org_id"]).eq("user_id", target_user_id)
    if folder_id:
        existing_query = existing_query.eq("folder_id", folder_id_str)
    else:
        existing_query = existing_query.is_("folder_id", "null")
    existing = existing_query.maybe_single().execute()
    if existing and existing.data:
        sup.table("permissions").update({"permission_level": permission_level}).eq("id", existing.data["id"]).execute()
    else:
        sup.table("permissions").insert({
            "org_id": user["org_id"],
            "user_id": target_user_id,
            "folder_id": folder_id_str,
            "permission_level": permission_level,
        }).execute()
    log_action("grant_folder_access", folder.data["name"], f"user={target.data['username']} level={permission_level}")
    print(f"[PERM] Granted '{target.data['username']}' {permission_level} on folder '{folder.data['name']}'")
    return jsonify({"ok": True})


@folders_bp.route("/api/folders/<uuid:folder_id>/permissions/<uuid:perm_id>", methods=["DELETE"])
@login_required
def api_folder_permissions_remove(folder_id, perm_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    perm = sup.table("permissions").select("user_id").eq("id", perm_id).maybe_single().execute()
    sup.table("permissions").delete().eq("id", perm_id).eq("org_id", user["org_id"]).execute()
    folder_name = "—"
    if folder_id:
        f = sup.table("folders").select("name").eq("id", folder_id).maybe_single().execute()
        if f and f.data:
            folder_name = f.data["name"]
    user_name = "—"
    if perm and perm.data and perm.data.get("user_id"):
        target = sup.table("users").select("username").eq("id", perm.data["user_id"]).maybe_single().execute()
        if target and target.data:
            user_name = target.data["username"]
    log_action("revoke_folder_access", folder_name, f"user={user_name}")
    return jsonify({"ok": True})


@folders_bp.route("/api/folders/all-users", methods=["GET"])
@login_required
def api_folder_all_users():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    users = sup.table("users").select("id, username, role").eq("org_id", user["org_id"]).order("username").execute().data
    return jsonify([dict(u) for u in users])
