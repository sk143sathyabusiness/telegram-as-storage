"""
TeamVault — small-team private file storage backed by Telegram.
"""

import os, io, json, time, secrets, hashlib, uuid as uuid_lib
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session, g, send_from_directory, send_file, Response, stream_with_context
from werkzeug.routing import BaseConverter
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

import telegram_bot

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
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_supabase: Client | None = None

def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

def check_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        raise SystemExit(1)

def set_rls_context(user_id, role):
    sup = get_supabase()
    sup.rpc("set_app_context", {"uid": user_id, "urole": role}).execute()

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

def log_action(action, target=None, detail=None):
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
        "details": json.dumps(details) if details else None,
    }).execute()

# ── PERMISSION HELPER ─────────────────────────────────────────────────────

def _check_permission(sup, user_id, org_id, folder_id=None):
    """Check user's effective permission for a folder. Returns level or None."""
    user_result = sup.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not user_result.data:
        return None
    role = user_result.data["role"]
    if role == "master_admin":
        return "org_admin"  # full access
    if role == "org_admin":
        return "org_admin"
    if not folder_id:
        return role  # org-wide default
    perm = sup.table("permissions").select("permission_level").eq("org_id", org_id).eq("user_id", user_id).eq("folder_id", folder_id).maybe_single().execute()
    if perm.data:
        return perm.data["permission_level"]
    return role  # fall back to user's org role

def _require_active_org(sup, org_id):
    """Return error response if org is not active, or None if OK."""
    org = sup.table("organizations").select("status").eq("id", org_id).maybe_single().execute()
    if not org.data or org.data["status"] != "active":
        return jsonify({"error": "Organisation is not active"}), 403
    return None

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
    "requirements.txt", "register.js", "register.css",
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
        "details": json.dumps({"org_name": data["org_name"].strip()}),
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
    return jsonify([dict(r) for r in data])

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
        if ver_result.data:
            ver = ver_result.data
            uploader_res = sup.table("users").select("username").eq("id", ver["uploaded_by"]).maybe_single().execute()
            d["current_version"] = {
                "version_number": ver["version_number"],
                "size_bytes": ver["size_bytes"],
                "sha256": ver["sha256"],
                "uploaded_at": ver["uploaded_at"],
                "uploaded_by_name": uploader_res.data["username"] if uploader_res.data else None,
            }
        result.append(d)
    return jsonify(result)

def _store_file_blob(f, org_id):
    """Upload encrypted bytes to org's Telegram channel. Returns (message_ids, size_bytes).

    Reads the uploaded file in a streaming fashion to keep peak memory
    close to CHUNK_SIZE_BYTES (~1.9 GB) rather than the full file size.
    """
    if not telegram_bot.is_configured():
        raise RuntimeError("Telegram not configured")
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured for this organisation")
    message_ids = telegram_bot.upload_chunks_streaming(f.stream, f.filename or "file", chat_id)
    size_bytes = f.content_length or 0
    return message_ids, size_bytes


def _load_file_blob(org_id, message_ids):
    """Download encrypted bytes from Telegram chunks."""
    if not telegram_bot.is_configured():
        raise RuntimeError("Telegram not configured")
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured")
    return telegram_bot.download_chunks(chat_id, message_ids)


@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    f = request.files.get("file")
    filename = request.form.get("filename", "unnamed")
    folder_id = request.form.get("folder_id") or None
    sha256 = request.form.get("sha256", "")
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
        return jsonify({"error": f"Storage failed: {e}"}), 500
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
    else:
        file_result = sup.table("files").insert({
            "org_id": user["org_id"],
            "folder_id": folder_id,
            "name": filename,
        }).execute()
        file_id = file_result.data[0]["id"]
        new_ver = 1
    sup.table("file_versions").insert({
        "file_id": file_id,
        "version_number": new_ver,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "message_ids": json.dumps(message_ids),
        "uploaded_by": user["id"],
        "is_current": True,
    }).execute()
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["id"],
        "actor_role": user["role"],
        "action": "upload",
        "target_type": "file",
        "target_id": str(file_id),
        "details": json.dumps({"filename": filename, "version": new_ver}),
    }).execute()
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})

@app.route("/api/files/<uuid:file_id>/download", methods=["GET"])
@login_required
def api_files_download(file_id):
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
    message_ids = json.loads(ver["message_ids"])
    size_bytes = ver["size_bytes"]
    if not telegram_bot.is_configured():
        return jsonify({"error": "Telegram not configured"}), 500
    sup2 = get_supabase()
    org = sup2.table("organizations").select("telegram_chat_id").eq("id", fdata["org_id"]).single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org.data.get("telegram_chat_id") else None
    if not chat_id:
        return jsonify({"error": "No Telegram chat_id configured"}), 500
    def generate():
        for chunk in telegram_bot.download_chunks_streaming(chat_id, message_ids):
            yield chunk
    log_action("download", fdata["name"])
    resp = Response(stream_with_context(generate()), mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = f'attachment; filename="{fdata["name"]}"'
    resp.headers["Content-Length"] = str(size_bytes)
    return resp

@app.route("/api/files/<uuid:file_id>", methods=["DELETE"])
@login_required
def api_files_delete(file_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    sup.table("files").update({"is_deleted": True, "deleted_at": datetime.utcnow().isoformat(), "deleted_by": user["id"]}).eq("id", file_id).eq("org_id", user["org_id"]).execute()
    f = sup.table("files").select("name").eq("id", file_id).maybe_single().execute()
    log_action("trash", f.data["name"] if f.data else None)
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
        d["uploaded_by_name"] = uploader.data["username"] if uploader.data else None
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
    if not ver.data:
        return jsonify({"error": "Version not found"}), 404
    sup.table("file_versions").update({"is_current": False}).eq("file_id", file_id).execute()
    sup.table("file_versions").update({"is_current": True}).eq("id", ver.data["id"]).execute()
    f = sup.table("files").select("name").eq("id", file_id).maybe_single().execute()
    log_action("restore_version", f.data["name"] if f.data else None, f"v{version_no}")
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
            d["deleted_by_name"] = del_user.data["username"] if del_user.data else None
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
    f = sup.table("files").select("name").eq("id", file_id).maybe_single().execute()
    log_action("restore_from_trash", f.data["name"] if f.data else None)
    return jsonify({"ok": True})

@app.route("/api/trash/<uuid:file_id>", methods=["DELETE"])
@login_required
def api_trash_hard_delete(file_id):
    user = current_user()
    if user["role"] not in ("org_admin", "master_admin"):
        return jsonify({"error": "Admin only"}), 403
    sup = get_supabase()
    f = sup.table("files").select("name").eq("id", file_id).maybe_single().execute()
    versions = sup.table("file_versions").select("message_ids").eq("file_id", file_id).execute().data
    if telegram_bot.is_configured() and versions:
        org = sup.table("organizations").select("telegram_chat_id").eq("id", user["org_id"]).single().execute()
        chat_id = int(org.data["telegram_chat_id"]) if org.data and org.data.get("telegram_chat_id") else None
        if chat_id:
            all_message_ids = []
            for v in versions:
                if v.get("message_ids"):
                    all_message_ids.extend(json.loads(v["message_ids"]))
            if all_message_ids:
                try:
                    telegram_bot.delete_file(chat_id, all_message_ids)
                except Exception:
                    pass
    sup.table("file_versions").delete().eq("file_id", file_id).execute()
    sup.table("files").delete().eq("id", file_id).eq("org_id", user["org_id"]).execute()
    log_action("permanent_delete", f.data["name"] if f.data else None)
    return jsonify({"ok": True})

# ── USERS API ──────────────────────────────────────────────────────────────

@app.route("/api/users", methods=["GET"])
@login_required
def api_users_get():
    user = current_user()
    sup = get_supabase()
    data = sup.table("users").select("id, username, role, created_at").eq("org_id", user["org_id"]).order("username").execute().data
    return jsonify([dict(r) for r in data])

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
    if role not in ("org_admin", "read_write", "read_only"):
        return jsonify({"error": "Invalid role"}), 400
    sup = get_supabase()
    existing = sup.table("users").select("id").eq("username", username).maybe_single().execute()
    if existing.data:
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
    if user["id"] == user_id:
        return jsonify({"error": "Cannot remove yourself"}), 400
    sup = get_supabase()
    target = sup.table("users").select("username").eq("id", user_id).maybe_single().execute()
    if not target.data:
        return jsonify({"error": "User not found"}), 404
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

# ── INIT & RUN ─────────────────────────────────────────────────────────────

check_supabase()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
