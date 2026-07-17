"""
TeamVault — small-team private file storage backed by Telegram.
"""

import os, json, secrets, hashlib, uuid as uuid_lib, smtplib, asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory, Response, stream_with_context
from werkzeug.routing import BaseConverter
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

try:
    import telegram_bot
except Exception:
    telegram_bot = None

app = Flask(__name__)

# Support large file uploads (default 10GB)
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_UPLOAD_SIZE', 10 * 1024 * 1024 * 1024))

class UUIDConverter(BaseConverter):
    def to_python(self, value):
        return uuid_lib.UUID(value)
    def to_url(self, value):
        return str(value)

app.url_map.converters['uuid'] = UUIDConverter

SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), ".secret_key")
if os.getenv("SECRET_KEY"):
    app.secret_key = os.getenv("SECRET_KEY")
else:
    try:
        app.secret_key = open(SECRET_KEY_FILE, "r").read()
    except (OSError, IOError):
        key = secrets.token_hex(32)
        try:
            with open(SECRET_KEY_FILE, "w") as f:
                f.write(key)
            os.chmod(SECRET_KEY_FILE, 0o600)
        except OSError:
            pass
        app.secret_key = key

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

BACKUP_CHANNEL_ID = int(os.environ.get("BACKUP_CHANNEL_ID", 0))

_supabase: Client | None = None

def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

def check_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        if __name__ == "__main__":
            print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
            raise SystemExit(1)

def set_rls_context(user_id, role):
    sup = get_supabase()
    sup.rpc("set_app_context", {"uid": user_id, "urole": role}).execute()

def _tg_configured():
    return telegram_bot is not None and _tg_configured()

# ── HELPERS ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped

def current_user():
    if "user_id" not in session:
        return None
    return {"id": session["user_id"], "org_id": session["org_id"], "role": session["role"], "username": session.get("username")}

def log_action(action, target=None, detail=None, target_type=None, target_id=None):
    sup = get_supabase()
    user = current_user()
    details = {}
    if target:
        details["target"] = target
    if detail:
        details["detail"] = detail
    sup.table("audit_logs").insert({
        "org_id": user["org_id"] if user else None,
        "actor_id": user["id"] if user else None,
        "actor_role": user["role"] if user else "system",
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id) if target_id else None,
        "details": details if details else None,
    }).execute()

def fmt_size(n):
    if n is None: return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

# ── PERMISSION HELPER ─────────────────────────────────────────────────────

def _check_permission(sup, user_id, org_id, folder_id=None):
    """Check user's effective permission for a folder. Returns level or None."""
    user_result = sup.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not user_result or not user_result.data:
        return None
    role = user_result.data["role"]
    if role == "master_admin":
        return "org_admin"  # full access
    if role == "org_admin":
        return "org_admin"
    if not folder_id:
        return role  # org-wide default
    perm = sup.table("permissions").select("permission_level").eq("org_id", org_id).eq("user_id", user_id).eq("folder_id", folder_id).maybe_single().execute()
    if perm and perm.data:
        return perm.data["permission_level"]
    return role  # fall back to user's org role

def _require_active_org(sup, org_id):
    """Return error response if org is not active, or None if OK."""
    org = sup.table("organizations").select("status").eq("id", org_id).maybe_single().execute()
    if not org or not org.data or org.data["status"] != "active":
        return jsonify({"error": "Organisation is not active"}), 403
    return None

def _resolve_folder_name(sup, folder_id):
    """Resolve a folder UUID to its name for audit log readability."""
    if not folder_id:
        return "Root"
    f = sup.table("folders").select("name").eq("id", folder_id).maybe_single().execute()
    return f.data["name"] if f and f.data else str(folder_id)

def _parse_message_ids(raw):
    """Safely parse message_ids from Supabase — handles both native list and legacy JSON string."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return []

# ── STATIC / PAGES ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.root_path, "index.html")

@app.route("/register")
def register_page():
    return send_from_directory(app.root_path, "register.html")

_BLOCKED_STATIC = {
    ".env", ".env.example", ".secret_key", ".git", ".gitignore",
    "app.py", "telegram_bot.py", "supabase_schema.sql",
    "requirements.txt",
}

@app.route("/<path:filename>")
def static_files(filename):
    parts = filename.replace("\\", "/").split("/")
    for part in parts:
        if part in _BLOCKED_STATIC or part.endswith(".session"):
            return jsonify({"error": "not found"}), 404
    return send_from_directory(app.root_path, filename)

# ── AUTH API ───────────────────────────────────────────────────────────────

@app.route("/api/me", methods=["GET"])
@login_required
def api_me():
    user = current_user()
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"]})

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    sup = get_supabase()
    result = sup.table("users").select("*").eq("username", username).execute()
    if not result.data:
        return jsonify({"error": "Invalid credentials"}), 401
    user = result.data[0]
    if not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401
    session["user_id"] = user["id"]
    session["org_id"] = user["org_id"]
    session["role"] = user["role"]
    session["username"] = user["username"]
    print(f"[AUTH] Login: user='{user['username']}' role={user['role']} org_id={user['org_id']}")
    # Write audit log
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["id"],
        "actor_role": user["role"],
        "action": "login",
    }).execute()
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"]})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})

# ── ORG REGISTRATION ──────────────────────────────────────────────────────

@app.route("/api/org/register", methods=["POST"])
def api_org_register():
    data = request.get_json(force=True)
    required = ["org_name", "username", "password", "contact_name", "contact_email"]
    for f in required:
        if not data.get(f, "").strip():
            return jsonify({"error": f"{f.replace('_',' ').title()} is required"}), 400
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
    # Create org
    org_result = sup.table("organizations").insert({
        "name": data["org_name"].strip(),
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

# ── ORGS API ────────────────────────────────────────────────────────────────

@app.route("/api/orgs", methods=["GET"])
@login_required
def api_orgs_get():
    user = current_user()
    if user["role"] not in ("master_admin", "org_admin"):
        return jsonify([])
    sup = get_supabase()
    data = sup.table("organizations").select("*").order("created_at", desc=True).execute().data
    return jsonify([dict(r) for r in data])

@app.route("/api/orgs/<uuid:org_id>/approve", methods=["POST"])
@login_required
def api_orgs_approve(org_id):
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    sup = get_supabase()
    sup.table("organizations").update({"status": "approved"}).eq("id", org_id).execute()
    log_action("approve_org", f"org_id={org_id}")
    return jsonify({"ok": True})

@app.route("/api/orgs/<uuid:org_id>/reject", methods=["POST"])
@login_required
def api_orgs_reject(org_id):
    user = current_user()
    if user["role"] != "master_admin":
        return jsonify({"error": "Master admin only"}), 403
    sup = get_supabase()
    sup.table("organizations").update({"status": "rejected"}).eq("id", org_id).execute()
    log_action("reject_org", f"org_id={org_id}")
    return jsonify({"ok": True})

# ── FOLDERS API ────────────────────────────────────────────────────────────

@app.route("/api/folders", methods=["GET"])
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
    for f in data:
        fid = f["id"]
        if not f.get("parent_id"):
            perm = _check_permission(sup, user["id"], user["org_id"], fid)
            if perm:
                visible_ids.add(fid)
                _add_ancestors(f, data, visible_ids)
    filtered = [dict(f) for f in data if f["id"] in visible_ids]
    return jsonify(filtered)

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

@app.route("/api/folders", methods=["POST"])
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

@app.route("/api/folders/<uuid:folder_id>", methods=["DELETE"])
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

# ── FOLDER PERMISSIONS API ───────────────────────────────────────────────

@app.route("/api/folders/permissions/all", methods=["GET"])
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

@app.route("/api/folders/<uuid:folder_id>/permissions", methods=["GET"])
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

@app.route("/api/folders/<uuid:folder_id>/permissions", methods=["POST"])
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

@app.route("/api/folders/<uuid:folder_id>/permissions/<uuid:perm_id>", methods=["DELETE"])
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

@app.route("/api/folders/all-users", methods=["GET"])
@login_required
def api_folder_all_users():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    users = sup.table("users").select("id, username, role").eq("org_id", user["org_id"]).order("username").execute().data
    return jsonify([dict(u) for u in users])

# ── FILES API ──────────────────────────────────────────────────────────────

@app.route("/api/files", methods=["GET"])
@login_required
def api_files_get():
    user = current_user()
    folder_id = request.args.get("folder_id")
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    query = sup.table("files").select("id, name, folder_id, created_at").eq("org_id", user["org_id"]).eq("is_deleted", False)
    if folder_id:
        query = query.eq("folder_id", folder_id)
    else:
        query = query.is_("folder_id", "null")
    rows = query.order("name").execute().data
    result = []
    for r in rows:
        d = dict(r)
        ver_result = sup.table("file_versions").select("version_number, size_bytes, sha256, uploaded_at, uploaded_by").eq("file_id", r["id"]).eq("is_current", True).maybe_single().execute()
        if ver_result and ver_result.data:
            ver = ver_result.data
            uploader_res = sup.table("users").select("username").eq("id", ver["uploaded_by"]).maybe_single().execute()
            d["current_version"] = {
                "version_number": ver["version_number"],
                "size_bytes": ver["size_bytes"],
                "sha256": ver["sha256"],
                "uploaded_at": ver["uploaded_at"],
                "uploaded_by_name": uploader_res.data["username"] if uploader_res and uploader_res.data else None,
            }
        result.append(d)
    return jsonify(result)

def _store_file_blob(f, org_id):
    """Upload encrypted bytes to org's Telegram channel. Returns (message_ids, size_bytes).

    Reads the uploaded file in a streaming fashion to keep peak memory
    close to CHUNK_SIZE_BYTES (~1.9 GB) rather than the full file size.
    """
    print(f"[UPLOAD] _store_file_blob called: org_id={org_id}, filename={f.filename}")
    if not _tg_configured():
        raise RuntimeError("Telegram not configured — check TG_API_ID and TG_API_HASH in .env")
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured for this organisation")
    size_bytes = 0
    try:
        f.stream.seek(0, 2)
        size_bytes = f.stream.tell()
        f.stream.seek(0)
    except Exception:
        size_bytes = f.content_length or 0
    print(f"[UPLOAD] chat_id={chat_id}, size_bytes={size_bytes}, starting Telegram upload...")
    message_ids = telegram_bot.upload_chunks_streaming(f.stream, f.filename or "file", chat_id)
    print(f"[UPLOAD] Telegram upload done, message_ids={message_ids}")
    return message_ids, size_bytes


def _load_file_blob(org_id, message_ids):
    """Download encrypted bytes from Telegram chunks."""
    if not _tg_configured():
        raise RuntimeError("Telegram not configured")
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured")
    return telegram_bot.download_chunks(chat_id, message_ids)


@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    user = current_user()
    print(f"[UPLOAD] User '{user['username']}' ({user['role']}) requesting upload")
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    f = request.files.get("file")
    filename = request.form.get("filename", "unnamed")
    folder_id = request.form.get("folder_id") or None
    sha256 = request.form.get("sha256", "")
    print(f"[UPLOAD] filename={filename}, folder_id={folder_id}, sha256={sha256[:16]}...")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    sup = get_supabase()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    perm = _check_permission(sup, user["id"], user["org_id"], folder_id)
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    try:
        message_ids, size_bytes = _store_file_blob(f, user["org_id"])
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Storage failed: {str(e)[:200]}"}), 500
    print(f"[UPLOAD] Stored to Telegram. message_ids={message_ids}, size={size_bytes}")
    # Find existing file by name + folder
    existing_query = sup.table("files").select("id").eq("org_id", user["org_id"]).eq("name", filename).eq("is_deleted", False)
    if folder_id:
        existing_query = existing_query.eq("folder_id", folder_id)
    else:
        existing_query = existing_query.is_("folder_id", "null")
    existing = existing_query.execute()
    if existing.data:
        file_id = existing.data[0]["id"]
        last = sup.table("file_versions").select("version_number").eq("file_id", file_id).order("version_number", desc=True).limit(1).execute()
        new_ver = (last.data[0]["version_number"] if last.data else 0) + 1
        sup.table("file_versions").update({"is_current": False}).eq("file_id", file_id).execute()
        print(f"[UPLOAD] Existing file updated: file_id={file_id}, new version=v{new_ver}")
    else:
        file_result = sup.table("files").insert({
            "org_id": user["org_id"],
            "folder_id": folder_id,
            "name": filename,
        }).execute()
        file_id = file_result.data[0]["id"]
        new_ver = 1
        print(f"[UPLOAD] New file created: file_id={file_id}, version=v{new_ver}")
    sup.table("file_versions").insert({
        "file_id": file_id,
        "version_number": new_ver,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "message_ids": message_ids,
        "uploaded_by": user["id"],
        "is_current": True,
    }).execute()
    folder_name = _resolve_folder_name(sup, folder_id)
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["id"],
        "actor_role": user["role"],
        "action": "upload",
        "target_type": "file",
        "target_id": str(file_id),
        "details": {
            "target": filename,
            "detail": f"v{new_ver} · {fmt_size(size_bytes)} · folder={folder_name}",
        },
    }).execute()
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})

@app.route("/api/files/<uuid:file_id>/download", methods=["GET"])
@login_required
def api_files_download(file_id):
    sup = get_supabase()
    user = current_user()
    print(f"[DOWNLOAD] User '{user['username']}' requesting download of file_id={file_id}")
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    ver_result = sup.table("file_versions").select("*").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    print(f"[DOWNLOAD] file={fdata['name']}, version=v{ver['version_number']}, size={size_bytes}, chunks={len(message_ids)}")
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    sup2 = get_supabase()
    org = sup2.table("organizations").select("telegram_chat_id").eq("id", fdata["org_id"]).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured"}), 500
    def generate():
        for chunk in telegram_bot.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    log_action("download", fdata["name"], f"v{ver['version_number']} · {fmt_size(size_bytes)} · folder={_resolve_folder_name(sup, fdata.get('folder_id'))}", target_type="file", target_id=file_id)
    print(f"[DOWNLOAD] Starting streaming response...")
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{fdata["name"]}"'
    resp.headers["Content-Length"] = str(size_bytes)
    return resp

@app.route("/api/files/<uuid:file_id>", methods=["DELETE"])
@login_required
def api_files_delete(file_id):
    user = current_user()
    print(f"[DELETE] User '{user['username']}' ({user['role']}) deleting file_id={file_id}")
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only — you need org_admin or master_admin role"}), 403
    sup = get_supabase()
    file_check = sup.table("files").select("id, org_id").eq("id", file_id).maybe_single().execute()
    if not file_check or not file_check.data:
        return jsonify({"error": "File not found"}), 404
    if file_check.data["org_id"] != user["org_id"]:
        return jsonify({"error": "Permission denied — file belongs to another organisation"}), 403
    sup.table("files").update({"is_deleted": True, "deleted_at": datetime.utcnow().isoformat(), "deleted_by": user["id"]}).eq("id", file_id).eq("org_id", user["org_id"]).execute()
    f = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    folder = _resolve_folder_name(sup, f.data.get("folder_id") if f and f.data else None) if f and f.data else "—"
    log_action("trash", f.data["name"] if f and f.data else str(file_id), f"folder={folder}", target_type="file", target_id=file_id)
    return jsonify({"ok": True})

# ── VERSIONS API ───────────────────────────────────────────────────────────

@app.route("/api/files/<uuid:file_id>/versions", methods=["GET"])
@login_required
def api_versions(file_id):
    sup = get_supabase()
    rows = sup.table("file_versions").select("*").eq("file_id", file_id).order("version_number", desc=True).execute().data
    result = []
    for r in rows:
        d = dict(r)
        uploader = sup.table("users").select("username").eq("id", r["uploaded_by"]).maybe_single().execute()
        d["uploaded_by_name"] = uploader.data["username"] if uploader and uploader.data else None
        d["is_current"] = bool(r["is_current"])
        result.append(d)
    return jsonify(result)

@app.route("/api/files/<uuid:file_id>/restore/<int:version_no>", methods=["POST"])
@login_required
def api_restore_version(file_id, version_no):
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    sup = get_supabase()
    ver = sup.table("file_versions").select("id").eq("file_id", file_id).eq("version_number", version_no).maybe_single().execute()
    if not ver or not ver.data:
        return jsonify({"error": "Version not found"}), 404
    sup.table("file_versions").update({"is_current": False}).eq("file_id", file_id).execute()
    sup.table("file_versions").update({"is_current": True}).eq("id", ver.data["id"]).execute()
    f = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    folder = _resolve_folder_name(sup, f.data.get("folder_id") if f and f.data else None) if f and f.data else "—"
    log_action("restore_version", f.data["name"] if f and f.data else str(file_id), f"v{version_no} · folder={folder}", target_type="file", target_id=file_id)
    return jsonify({"ok": True})

# ── TRASH API ──────────────────────────────────────────────────────────────

@app.route("/api/trash", methods=["GET"])
@login_required
def api_trash_get():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify([])
    sup = get_supabase()
    rows = sup.table("files").select("*").eq("org_id", user["org_id"]).eq("is_deleted", True).order("deleted_at", desc=True).execute().data
    result = []
    for r in rows:
        d = dict(r)
        if r.get("deleted_by"):
            del_user = sup.table("users").select("username").eq("id", r["deleted_by"]).maybe_single().execute()
            d["deleted_by_name"] = del_user.data["username"] if del_user and del_user.data else None
        else:
            d["deleted_by_name"] = None
        result.append(d)
    return jsonify(result)

@app.route("/api/trash/<uuid:file_id>/restore", methods=["POST"])
@login_required
def api_trash_restore(file_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    sup.table("files").update({"is_deleted": False, "deleted_at": None, "deleted_by": None}).eq("id", file_id).eq("org_id", user["org_id"]).execute()
    f = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    folder = _resolve_folder_name(sup, f.data.get("folder_id") if f and f.data else None) if f and f.data else "—"
    log_action("restore_from_trash", f.data["name"] if f and f.data else str(file_id), f"folder={folder}")
    return jsonify({"ok": True})

@app.route("/api/trash/<uuid:file_id>", methods=["DELETE"])
@login_required
def api_trash_hard_delete(file_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    f = sup.table("files").select("name, folder_id").eq("id", file_id).maybe_single().execute()
    versions = sup.table("file_versions").select("message_ids").eq("file_id", file_id).execute().data
    if _tg_configured() and versions:
        org = sup.table("organizations").select("telegram_chat_id").eq("id", user["org_id"]).maybe_single().execute()
        chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
        if chat_id:
            all_message_ids = []
            for v in versions:
                if v.get("message_ids"):
                    all_message_ids.extend(_parse_message_ids(v["message_ids"]))
            if all_message_ids:
                try:
                    asyncio.run(telegram_bot.delete_file(chat_id, all_message_ids))
                except Exception:
                    pass
    sup.table("file_versions").delete().eq("file_id", file_id).execute()
    sup.table("files").delete().eq("id", file_id).eq("org_id", user["org_id"]).execute()
    folder = _resolve_folder_name(sup, f.data.get("folder_id") if f and f.data else None) if f and f.data else "—"
    ver_count = len(versions) if versions else 0
    log_action("permanent_delete", f.data["name"] if f and f.data else str(file_id), f"{ver_count} version(s) · folder={folder}")
    return jsonify({"ok": True})

# ── USERS API ──────────────────────────────────────────────────────────────

@app.route("/api/users", methods=["GET"])
@login_required
def api_users_get():
    user = current_user()
    sup = get_supabase()
    data = sup.table("users").select("id, username, role, created_at").eq("org_id", user["org_id"]).order("username").execute().data
    return jsonify([dict(r) for r in data])

@app.route("/api/users/stats", methods=["GET"])
@login_required
def api_users_stats():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    all_users = sup.table("users").select("id, role, created_at").eq("org_id", user["org_id"]).execute().data
    total = len(all_users)
    by_role = {}
    for u in all_users:
        r = u["role"]
        by_role[r] = by_role.get(r, 0) + 1
    from datetime import timedelta
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    month_ago = (datetime.utcnow() - timedelta(days=30)).isoformat()
    week_ago_count = sum(1 for u in all_users if u.get("created_at", "") >= week_ago)
    month_ago_count = sum(1 for u in all_users if u.get("created_at", "") >= month_ago)
    logs = sup.table("audit_logs").select("actor_id").eq("org_id", user["org_id"]).gte("created_at", week_ago).execute().data
    active_actor_ids = set(l["actor_id"] for l in logs if l.get("actor_id"))
    return jsonify({
        "total": total,
        "by_role": by_role,
        "joined_this_week": week_ago_count,
        "joined_this_month": month_ago_count,
        "active_this_week": len(active_actor_ids),
    })

@app.route("/api/users/<uuid:user_id>", methods=["PUT"])
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

@app.route("/api/users/<uuid:user_id>/activity", methods=["GET"])
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

@app.route("/api/users/<uuid:user_id>/permissions", methods=["GET"])
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

@app.route("/api/users/<uuid:user_id>/permissions", methods=["POST"])
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

@app.route("/api/users/<uuid:user_id>/permissions/<uuid:perm_id>", methods=["DELETE"])
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

@app.route("/api/users", methods=["POST"])
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

@app.route("/api/users/<uuid:user_id>", methods=["DELETE"])
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

# ── LOGS API ───────────────────────────────────────────────────────────────

@app.route("/api/logs", methods=["GET"])
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

# ── VERSIONS ALL API ────────────────────────────────────────────────────────

@app.route("/api/versions/all", methods=["GET"])
@login_required
def api_versions_all():
    user = current_user()
    sup = get_supabase()
    files_data = sup.table("files").select("id, name").eq("org_id", user["org_id"]).eq("is_deleted", False).execute().data
    file_ids = [f["id"] for f in files_data]
    file_map = {f["id"]: f["name"] for f in files_data}
    if not file_ids:
        return jsonify([])
    versions = sup.table("file_versions").select("*").in_("file_id", file_ids).order("uploaded_at", desc=True).limit(500).execute().data
    uploader_ids = list(set(v["uploaded_by"] for v in versions if v.get("uploaded_by")))
    users_data = {}
    if uploader_ids:
        users_result = sup.table("users").select("id, username").in_("id", uploader_ids).execute().data
        users_data = {u["id"]: u["username"] for u in users_result}
    result = []
    for v in versions:
        d = dict(v)
        d["filename"] = file_map.get(v["file_id"])
        d["uploaded_by_name"] = users_data.get(v["uploaded_by"])
        d["is_current"] = bool(v["is_current"])
        result.append(d)
    return jsonify(result)

# ── SHARING API ──────────────────────────────────────────────────────────

@app.route("/api/files/<uuid:file_id>/share", methods=["POST"])
@login_required
def api_files_share(file_id):
    sup = get_supabase()
    user = current_user()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    data = request.get_json(force=True) if request.data else {}
    expires_days = data.get("expires_days", 7)
    password = data.get("password", "")
    token = secrets.token_urlsafe(24)
    expires_at = None
    if expires_days:
        from datetime import timedelta
        expires_at = (datetime.utcnow() + timedelta(days=int(expires_days))).isoformat()
    insert_data = {
        "file_id": str(file_id),
        "token": token,
        "created_by": user["id"],
        "expires_at": expires_at,
    }
    if password:
        insert_data["password_hash"] = hashlib.sha256(password.encode()).hexdigest()
    result = sup.table("shared_links").insert(insert_data).execute()
    log_action("share_file", fdata["name"], f"folder={_resolve_folder_name(sup, fdata.get('folder_id'))} token={token[:8]}...")
    print(f"[SHARE] Created link for file '{fdata['name']}', token={token[:8]}...")
    return jsonify({"ok": True, "token": token, "expires_at": expires_at})

@app.route("/api/files/<uuid:file_id>/shares", methods=["GET"])
@login_required
def api_files_shares(file_id):
    sup = get_supabase()
    user = current_user()
    shares = sup.table("shared_links").select("*").eq("file_id", file_id).order("created_at", desc=True).execute().data
    result = []
    for s in shares:
        d = dict(s)
        d["has_password"] = bool(d.pop("password_hash", None))
        result.append(d)
    return jsonify(result)

@app.route("/api/files/<uuid:file_id>/shares/<uuid:share_id>", methods=["DELETE"])
@login_required
def api_files_unshare(file_id, share_id):
    sup = get_supabase()
    user = current_user()
    sup.table("shared_links").delete().eq("id", share_id).eq("file_id", file_id).execute()
    f = sup.table("files").select("name").eq("id", file_id).maybe_single().execute()
    log_action("remove_share", f.data["name"] if f and f.data else str(file_id))
    return jsonify({"ok": True})

@app.route("/api/shared/<token>", methods=["GET"])
def api_shared_download(token):
    sup = get_supabase()
    link = sup.table("shared_links").select("*, files(name, org_id, folder_id)").eq("token", token).maybe_single().execute()
    if not link or not link.data:
        return jsonify({"error": "Link not found or expired"}), 404
    link_data = link.data
    if link_data.get("expires_at"):
        from datetime import datetime as dt
        exp = dt.fromisoformat(link_data["expires_at"].replace("Z", "+00:00")) if "T" in link_data["expires_at"] else dt.strptime(link_data["expires_at"][:19], "%Y-%m-%dT%H:%M:%S")
        if dt.utcnow() > exp:
            return jsonify({"error": "Link has expired"}), 410
    fdata = link_data.get("files", {})
    file_id = link_data["file_id"]
    org_id = fdata.get("org_id")
    filename = fdata.get("name", "file")
    sup.table("shared_links").update({"download_count": (link_data.get("download_count") or 0) + 1}).eq("id", link_data["id"]).execute()
    ver_result = sup.table("file_versions").select("*").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "Telegram chat not configured"}), 500
    def generate():
        for chunk in telegram_bot.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    print(f"[SHARED] Download: '{filename}', token={token[:8]}...")
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Content-Length"] = str(size_bytes)
    return resp

@app.route("/api/shared/<token>/info", methods=["GET"])
def api_shared_info(token):
    sup = get_supabase()
    link = sup.table("shared_links").select("*, files(name, org_id)").eq("token", token).maybe_single().execute()
    if not link or not link.data:
        return jsonify({"error": "Link not found"}), 404
    link_data = link.data
    fdata = link_data.get("files", {})
    file_id = link_data["file_id"]
    ver_result = sup.table("file_versions").select("version_number, size_bytes, sha256, uploaded_at").eq("file_id", file_id).eq("is_current", True).maybe_single().execute()
    ver = ver_result.data if ver_result and ver_result.data else {}
    return jsonify({
        "filename": fdata.get("name"),
        "size_bytes": ver.get("size_bytes"),
        "version": ver.get("version_number"),
        "has_password": bool(link_data.get("password_hash")),
        "expires_at": link_data.get("expires_at"),
        "download_count": link_data.get("download_count", 0),
    })

@app.route("/api/shared/<token>/preview", methods=["GET"])
def api_shared_preview(token):
    sup = get_supabase()
    link = sup.table("shared_links").select("*, files(name, org_id)").eq("token", token).maybe_single().execute()
    if not link or not link.data:
        return jsonify({"error": "Link not found"}), 404
    link_data = link.data
    fdata = link_data.get("files", {})
    file_id = link_data["file_id"]
    org_id = fdata.get("org_id")
    filename = fdata.get("name", "file")
    ver_result = sup.table("file_versions").select("message_ids, size_bytes").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "Telegram chat not configured"}), 500
    def generate():
        for chunk in telegram_bot.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp

# ── EMAIL API ────────────────────────────────────────────────────────────

@app.route("/api/files/<uuid:file_id>/email", methods=["POST"])
@login_required
def api_files_email(file_id):
    sup = get_supabase()
    user = current_user()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm or perm == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    data = request.get_json(force=True)
    recipients = data.get("recipients", "")
    message = data.get("message", "")
    if not recipients:
        return jsonify({"error": "Recipients required"}), 400
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "TeamVault")
    if not smtp_host or not smtp_user:
        return jsonify({"error": "Email not configured — ask admin to set SMTP_* variables in .env"}), 500
    # Create share link for email
    token = secrets.token_urlsafe(24)
    from datetime import timedelta
    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
    sup.table("shared_links").insert({
        "file_id": str(file_id),
        "token": token,
        "created_by": user["id"],
        "expires_at": expires_at,
    }).execute()
    share_url = f"{request.host_url}shared/{token}"
    email_list = [r.strip() for r in recipients.split(",") if r.strip()]
    for addr in email_list:
        msg = MIMEMultipart()
        msg["From"] = f"{smtp_from_name} <{smtp_from}>"
        msg["To"] = addr
        msg["Subject"] = f"{user['username']} shared a file with you — {fdata['name']}"
        body = f"""Hi,

{user['username']} shared a file with you via TeamVault.

File: {fdata['name']}
Download link (expires in 7 days): {share_url}

{message if message else ''}

— TeamVault"""
        msg.attach(MIMEText(body, "plain"))
        try:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_from, addr, msg.as_string())
            server.quit()
        except Exception as e:
            print(f"[EMAIL] Failed to send to {addr}: {e}")
            return jsonify({"error": f"Failed to send email: {str(e)[:200]}"}), 500
    log_action("email_file", fdata["name"], f"folder={_resolve_folder_name(sup, fdata.get('folder_id'))} to={recipients}")
    print(f"[EMAIL] Sent '{fdata['name']}' to {recipients}")
    return jsonify({"ok": True})

# ── PREVIEW API ──────────────────────────────────────────────────────────

@app.route("/api/files/<uuid:file_id>/preview", methods=["GET"])
@login_required
def api_files_preview(file_id):
    sup = get_supabase()
    user = current_user()
    err = _require_active_org(sup, user["org_id"])
    if err:
        return err
    file_result = sup.table("files").select("id, name, org_id, folder_id").eq("id", file_id).execute()
    if not file_result.data:
        return jsonify({"error": "File not found"}), 404
    fdata = file_result.data[0]
    perm = _check_permission(sup, user["id"], user["org_id"], fdata.get("folder_id"))
    if not perm:
        return jsonify({"error": "Permission denied"}), 403
    ver_result = sup.table("file_versions").select("*").eq("file_id", file_id).eq("is_current", True).execute()
    if not ver_result.data:
        return jsonify({"error": "No current version"}), 404
    ver = ver_result.data[0]
    message_ids = _parse_message_ids(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not _tg_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    org = sup.table("organizations").select("telegram_chat_id").eq("id", fdata["org_id"]).maybe_single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org and org.data and org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured"}), 500
    def generate():
        for chunk in telegram_bot.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    print(f"[PREVIEW] Serving preview for '{fdata['name']}'")
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'inline; filename="{fdata["name"]}"'
    return resp

# ── BACKUP API ──────────────────────────────────────────────────────────
# Backups are metadata-only snapshots (Supabase rows dumped to JSON),
# uploaded to BACKUP_CHANNEL_ID on Telegram. Zero local storage.

def _backup_channel_ok():
    if not BACKUP_CHANNEL_ID:
        return False, jsonify({"error": "BACKUP_CHANNEL_ID not configured in .env"}), 500
    return True, None, None

@app.route("/api/backup/list", methods=["GET"])
@login_required
def api_backup_list():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    try:
        rows = sup.table("backups").select("id,name,size_bytes,created_at,created_by").eq("org_id", user["org_id"]).order("created_at", desc=True).execute().data
    except Exception as e:
        if "PGRST205" in str(e):
            return jsonify({"error": "Backups table not set up. Run the migration SQL first.", "sql_hint": True}), 503
        raise
    return jsonify(rows)

@app.route("/api/backup/create", methods=["POST"])
@login_required
def api_backup_create():
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    org_id = user["org_id"]
    print(f"[BACKUP] Creating backup for org {org_id}")
    tables_data = {}
    for table_name in ["organizations", "users", "folders", "files", "file_versions", "permissions", "audit_logs"]:
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
    backup_payload = {
        "version": 1,
        "org_id": org_id,
        "created_at": datetime.utcnow().isoformat(),
        "created_by": user["username"],
        "tables": tables_data,
    }
    backup_bytes = json.dumps(backup_payload, indent=2, default=str).encode("utf-8")
    size = len(backup_bytes)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    remote_name = f"backup_{org_id}_{timestamp}.json"
    print(f"[BACKUP] Uploading {size} bytes to Telegram channel {BACKUP_CHANNEL_ID}...")
    message_ids = telegram_bot.upload_chunks(backup_bytes, remote_name, BACKUP_CHANNEL_ID)
    msg_id = message_ids[0]
    print(f"[BACKUP] Uploaded — message_id={msg_id}")
    try:
        sup.table("backups").insert({
            "org_id": org_id,
            "name": remote_name,
            "size_bytes": size,
            "message_id": msg_id,
            "created_by": user["id"],
        }).execute()
    except Exception as e:
        if "PGRST205" in str(e):
            return jsonify({"error": "Backups table not set up. Run the migration SQL first.", "sql_hint": True}), 503
        raise
    log_action("create_backup", remote_name, f"size={size}")
    return jsonify({"ok": True, "name": remote_name, "size_bytes": size})

@app.route("/api/backup/restore/<path:name>", methods=["POST"])
@login_required
def api_backup_restore(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    print(f"[BACKUP] Downloading backup from Telegram (message_id={msg_id})...")
    backup_bytes = telegram_bot.download_chunks(BACKUP_CHANNEL_ID, [msg_id])
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

@app.route("/api/backup/download/<path:name>", methods=["GET"])
@login_required
def api_backup_download(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    backup_bytes = telegram_bot.download_chunks(BACKUP_CHANNEL_ID, [msg_id])
    log_action("download_backup", name)
    resp = Response(backup_bytes, mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp

@app.route("/api/backup/delete/<path:name>", methods=["DELETE"])
@login_required
def api_backup_delete(name):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    ok, err, code = _backup_channel_ok()
    if not ok:
        return err, code
    sup = get_supabase()
    record = sup.table("backups").select("*").eq("org_id", user["org_id"]).eq("name", name).maybe_single().execute()
    if not record or not record.data:
        return jsonify({"error": "Backup not found"}), 404
    msg_id = record.data["message_id"]
    print(f"[BACKUP] Deleting backup from Telegram (message_id={msg_id})...")
    asyncio.run(telegram_bot.delete_file(BACKUP_CHANNEL_ID, [msg_id]))
    sup.table("backups").delete().eq("id", record.data["id"]).execute()
    log_action("delete_backup", name)
    print(f"[BACKUP] Deleted backup '{name}'")
    return jsonify({"ok": True})

# ── INIT & RUN ─────────────────────────────────────────────────────────────

def _ensure_backups_table():
    """Create the backups table if it doesn't exist + fix grants."""
    sup = get_supabase()
    try:
        sup.table("users").select("id").limit(1).execute()
    except Exception as e:
        if "42501" in str(e) or "permission denied" in str(e).lower():
            print("[MIGRATE] service_role lacks GRANT privileges on tables")
            _print_grants_sql()
        return
    try:
        sup.table("backups").select("id").limit(1).execute()
    except Exception as e:
        if "PGRST205" in str(e):
            print("[MIGRATE] backups table missing - creating now...")
        else:
            return
    sql = """CREATE TABLE IF NOT EXISTS backups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    size_bytes INTEGER,
    message_id INTEGER NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE backups ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'backups_org_isolation') THEN
        CREATE POLICY backups_org_isolation ON backups FOR ALL USING (
            org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
            OR current_setting('app.user_role')::text = 'master_admin'
        );
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_backups_org_id ON backups(org_id);"""
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        print("[MIGRATE] Cannot auto-create backups table - no SUPABASE_DB_URL in .env")
        print("[MIGRATE] Paste this SQL into the Supabase Dashboard -> SQL Editor -> Run:")
        print(sql)
        return
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.close()
        print("[MIGRATE] backups table created successfully")
    except Exception as e2:
        print(f"[MIGRATE] Auto-create failed: {e2}")
        print("[MIGRATE] Paste this SQL into the Supabase Dashboard -> SQL Editor -> Run:")
        print(sql)

def _print_grants_sql():
    sql = """-- Fix: Grant service_role full access to all tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO service_role;"""
    print("[MIGRATE] Paste this SQL into Supabase Dashboard -> SQL Editor -> Run:")
    print(sql)

check_supabase()

if __name__ == "__main__":
    _ensure_backups_table()
    app.run(debug=True, port=5000)
