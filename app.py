"""
TeamVault — small-team private file storage backed by Telegram.
"""

import os, io, sqlite3, time, secrets, hashlib
from functools import wraps
from flask import Flask, request, jsonify, session, g, send_from_directory, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

import telegram_bot

app = Flask(__name__)
SECRET_KEY_FILE = os.path.join(os.path.dirname(__file__), ".secret_key")
if os.getenv("SECRET_KEY"):
    app.secret_key = os.getenv("SECRET_KEY")
else:
    try:
        app.secret_key = open(SECRET_KEY_FILE, "rb").read()
    except (OSError, IOError):
        key = secrets.token_hex(32)
        try:
            with open(SECRET_KEY_FILE, "w") as f:
                f.write(key)
            os.chmod(SECRET_KEY_FILE, 0o600)
        except OSError:
            pass
        app.secret_key = key

DB_PATH = os.path.join(os.path.dirname(__file__), "teamvault.db")

# ── DATABASE ───────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'read_write',
            created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS orgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT,
            size TEXT,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            plan TEXT DEFAULT 'standard',
            message TEXT,
            status TEXT DEFAULT 'pending',
            created_by INTEGER REFERENCES users(id),
            chat_id TEXT,
            channel_name TEXT,
            created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
            created_by INTEGER REFERENCES users(id),
            created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
            created_by INTEGER REFERENCES users(id),
            is_deleted INTEGER DEFAULT 0,
            deleted_at REAL,
            deleted_by INTEGER REFERENCES users(id),
            created_at REAL NOT NULL DEFAULT (strftime('%s','now'))
        );
        CREATE TABLE IF NOT EXISTS file_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
            version_no INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT,
            storage_key TEXT NOT NULL,
            uploaded_by INTEGER REFERENCES users(id),
            uploaded_at REAL NOT NULL DEFAULT (strftime('%s','now')),
            is_current INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL DEFAULT (strftime('%s','now')),
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT
        );
    """)
    # Seed default admin if no users exist
    cur = db.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin"), "admin"),
        )
    db.commit()
    db.close()
    # Ensure db file is group/world-writable to avoid permission issues
    try:
        os.chmod(DB_PATH, 0o664)
    except OSError:
        pass

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
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def log_action(action, target=None, detail=None):
    user = current_user()
    db = get_db()
    db.execute(
        "INSERT INTO logs (user_id, username, action, target, detail) VALUES (?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else None, action, target, detail),
    )
    db.commit()

def ensure_dirs():
    os.makedirs(os.path.join(app.root_path, "uploads"), exist_ok=True)

# ── STATIC / PAGES ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.root_path, "index.html")

@app.route("/register")
def register_page():
    return send_from_directory(app.root_path, "register.html")

@app.route("/<path:filename>")
def static_files(filename):
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
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid credentials"}), 401
    session["user_id"] = user["id"]
    log_action("login")
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
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username=?", (data["username"].strip(),)).fetchone()
    if existing:
        return jsonify({"error": "Username already taken"}), 400
    # Create the admin user immediately
    db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (data["username"].strip(), generate_password_hash(data["password"]), "admin"),
    )
    user_id = db.execute("SELECT id FROM users WHERE username=?", (data["username"].strip(),)).fetchone()["id"]
    # Store org request
    db.execute(
        """INSERT INTO orgs (name, industry, size, contact_name, contact_email,
           contact_phone, plan, message, status, created_by, chat_id, channel_name)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["org_name"].strip(),
            data.get("industry", ""),
            data.get("size", ""),
            data["contact_name"].strip(),
            data["contact_email"].strip(),
            data.get("contact_phone", ""),
            data.get("plan", "standard"),
            data.get("message", ""),
            "approved",
            user_id,
            data.get("chat_id", ""),
            data.get("channel_name", ""),
        ),
    )
    db.commit()
    return jsonify({"ok": True, "message": "Registration successful — you can now log in"})

# ── ORGS API ────────────────────────────────────────────────────────────────

@app.route("/api/orgs", methods=["GET"])
@login_required
def api_orgs_get():
    user = current_user()
    if user["role"] != "admin":
        return jsonify([])
    db = get_db()
    rows = db.execute("SELECT * FROM orgs ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/orgs/<int:org_id>/approve", methods=["POST"])
@login_required
def api_orgs_approve(org_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    db = get_db()
    db.execute("UPDATE orgs SET status='approved' WHERE id=?", (org_id,))
    db.commit()
    log_action("approve_org", f"org_id={org_id}")
    return jsonify({"ok": True})

@app.route("/api/orgs/<int:org_id>/reject", methods=["POST"])
@login_required
def api_orgs_reject(org_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    db = get_db()
    db.execute("UPDATE orgs SET status='rejected' WHERE id=?", (org_id,))
    db.commit()
    log_action("reject_org", f"org_id={org_id}")
    return jsonify({"ok": True})

# ── FOLDERS API ────────────────────────────────────────────────────────────

@app.route("/api/folders", methods=["GET"])
@login_required
def api_folders_get():
    db = get_db()
    rows = db.execute("SELECT id, name, parent_id FROM folders ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

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
    db = get_db()
    db.execute("INSERT INTO folders (name, parent_id, created_by) VALUES (?,?,?)",
               (name, parent_id, user["id"]))
    db.commit()
    log_action("create_folder", name)
    return jsonify({"ok": True})

# ── FILES API ──────────────────────────────────────────────────────────────

@app.route("/api/files", methods=["GET"])
@login_required
def api_files_get():
    folder_id = request.args.get("folder_id")
    db = get_db()
    if folder_id:
        rows = db.execute(
            "SELECT * FROM files WHERE folder_id=? AND is_deleted=0 ORDER BY filename",
            (int(folder_id),),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM files WHERE (folder_id IS NULL OR folder_id=0) AND is_deleted=0 ORDER BY filename"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        ver = db.execute(
            "SELECT * FROM file_versions WHERE file_id=? AND is_current=1", (r["id"],)
        ).fetchone()
        if ver:
            uploader = db.execute("SELECT username FROM users WHERE id=?", (ver["uploaded_by"],)).fetchone()
            d["current_version"] = {
                "version_no": ver["version_no"],
                "size_bytes": ver["size_bytes"],
                "sha256": ver["sha256"],
                "uploaded_at": ver["uploaded_at"],
                "uploaded_by_name": uploader["username"] if uploader else None,
            }
        result.append(d)
    return jsonify(result)

def _store_file_blob(f, user_id):
    """Store uploaded file bytes. Returns (storage_key, size_bytes).
       Uses Telegram if configured, otherwise falls back to local disk."""
    file_bytes = f.read()
    size_bytes = len(file_bytes)

    if telegram_bot.is_configured():
        # Get the org's chat_id from the DB
        db = get_db()
        chat_id = None
        org = db.execute("SELECT chat_id FROM orgs WHERE created_by=?", (user_id,)).fetchone()
        if org and org["chat_id"]:
            chat_id = org["chat_id"]
        try:
            file_id, msg_id = telegram_bot.send_document(chat_id, file_bytes, f.filename or "file")
            storage_key = f"tg:{file_id}:{msg_id}"
            return storage_key, size_bytes
        except Exception as e:
            log_action("telegram_upload_error", str(e))
            # Fall through to local storage
    # Fallback: save to local disk
    ensure_dirs()
    file_key = secrets.token_hex(16)
    with open(os.path.join(app.root_path, "uploads", file_key), "wb") as out:
        out.write(file_bytes)
    return file_key, size_bytes


def _load_file_blob(storage_key):
    """Load file bytes by storage_key. Handles both Telegram and local storage."""
    if storage_key.startswith("tg:"):
        parts = storage_key.split(":", 2)
        if len(parts) >= 2:
            file_id = parts[1]
            return telegram_bot.get_file_bytes(file_id)
        raise RuntimeError(f"Invalid Telegram storage_key: {storage_key}")
    # Local file
    path = os.path.join(app.root_path, "uploads", storage_key)
    with open(path, "rb") as f:
        return f.read()


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
    if folder_id:
        folder_id = int(folder_id)
    if not f:
        return jsonify({"error": "No file provided"}), 400
    try:
        storage_key, size_bytes = _store_file_blob(f, user["id"])
    except Exception as e:
        return jsonify({"error": f"Storage failed: {e}"}), 500
    db = get_db()
    # Find existing file with same name in same folder
    existing = db.execute(
        "SELECT id FROM files WHERE filename=? AND folder_id IS ? AND is_deleted=0",
        (filename, folder_id),
    ).fetchone()
    if existing:
        file_id = existing["id"]
        # Bump version
        last = db.execute(
            "SELECT MAX(version_no) as m FROM file_versions WHERE file_id=?", (file_id,)
        ).fetchone()
        new_ver = (last["m"] or 0) + 1
        # Mark old versions as not current
        db.execute("UPDATE file_versions SET is_current=0 WHERE file_id=?", (file_id,))
    else:
        cur = db.execute(
            "INSERT INTO files (filename, folder_id, created_by) VALUES (?,?,?)",
            (filename, folder_id, user["id"]),
        )
        file_id = cur.lastrowid
        new_ver = 1
    db.execute(
        "INSERT INTO file_versions (file_id, version_no, size_bytes, sha256, storage_key, uploaded_by, is_current) VALUES (?,?,?,?,?,?,1)",
        (file_id, new_ver, size_bytes, sha256, storage_key, user["id"]),
    )
    db.commit()
    log_action("upload", filename, f"v{new_ver}")
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})

@app.route("/api/files/<int:file_id>/download", methods=["GET"])
@login_required
def api_files_download(file_id):
    db = get_db()
    ver = db.execute(
        "SELECT * FROM file_versions WHERE file_id=? AND is_current=1", (file_id,)
    ).fetchone()
    if not ver:
        return jsonify({"error": "No current version"}), 404
    try:
        file_bytes = _load_file_blob(ver["storage_key"])
    except Exception as e:
        return jsonify({"error": f"File not found: {e}"}), 404
    fname = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()["filename"]
    log_action("download", fname)
    return send_file(io.BytesIO(file_bytes), download_name=fname, as_attachment=True)

@app.route("/api/files/<int:file_id>", methods=["DELETE"])
@login_required
def api_files_delete(file_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    db = get_db()
    db.execute("UPDATE files SET is_deleted=1, deleted_at=?, deleted_by=? WHERE id=?",
               (time.time(), user["id"], file_id))
    db.commit()
    f = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    log_action("trash", f["filename"] if f else None)
    return jsonify({"ok": True})

# ── VERSIONS API ───────────────────────────────────────────────────────────

@app.route("/api/files/<int:file_id>/versions", methods=["GET"])
@login_required
def api_versions(file_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM file_versions WHERE file_id=? ORDER BY version_no DESC", (file_id,)
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        uploader = db.execute("SELECT username FROM users WHERE id=?", (r["uploaded_by"],)).fetchone()
        d["uploaded_by_name"] = uploader["username"] if uploader else None
        d["is_current"] = bool(r["is_current"])
        result.append(d)
    return jsonify(result)

@app.route("/api/files/<int:file_id>/restore/<int:version_no>", methods=["POST"])
@login_required
def api_restore_version(file_id, version_no):
    user = current_user()
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    db = get_db()
    ver = db.execute(
        "SELECT * FROM file_versions WHERE file_id=? AND version_no=?", (file_id, version_no)
    ).fetchone()
    if not ver:
        return jsonify({"error": "Version not found"}), 404
    db.execute("UPDATE file_versions SET is_current=0 WHERE file_id=?", (file_id,))
    db.execute("UPDATE file_versions SET is_current=1 WHERE id=?", (ver["id"],))
    db.commit()
    f = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    log_action("restore_version", f["filename"] if f else None, f"v{version_no}")
    return jsonify({"ok": True})

# ── TRASH API ──────────────────────────────────────────────────────────────

@app.route("/api/trash", methods=["GET"])
@login_required
def api_trash_get():
    user = current_user()
    if user["role"] != "admin":
        return jsonify([])
    db = get_db()
    rows = db.execute(
        "SELECT * FROM files WHERE is_deleted=1 ORDER BY deleted_at DESC"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        del_user = db.execute("SELECT username FROM users WHERE id=?", (r["deleted_by"],)).fetchone()
        d["deleted_by_name"] = del_user["username"] if del_user else None
        result.append(d)
    return jsonify(result)

@app.route("/api/trash/<int:file_id>/restore", methods=["POST"])
@login_required
def api_trash_restore(file_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    db = get_db()
    db.execute("UPDATE files SET is_deleted=0, deleted_at=NULL, deleted_by=NULL WHERE id=?", (file_id,))
    db.commit()
    f = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    log_action("restore_from_trash", f["filename"] if f else None)
    return jsonify({"ok": True})

@app.route("/api/trash/<int:file_id>", methods=["DELETE"])
@login_required
def api_trash_hard_delete(file_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    db = get_db()
    # Delete file versions from disk
    versions = db.execute("SELECT storage_key FROM file_versions WHERE file_id=?", (file_id,)).fetchall()
    for v in versions:
        path = os.path.join(app.root_path, "uploads", v["storage_key"])
        if os.path.exists(path):
            os.remove(path)
    f = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    db.execute("DELETE FROM file_versions WHERE file_id=?", (file_id,))
    db.execute("DELETE FROM files WHERE id=?", (file_id,))
    db.commit()
    log_action("permanent_delete", f["filename"] if f else None)
    return jsonify({"ok": True})

# ── USERS API ──────────────────────────────────────────────────────────────

@app.route("/api/users", methods=["GET"])
@login_required
def api_users_get():
    db = get_db()
    rows = db.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/users", methods=["POST"])
@login_required
def api_users_post():
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "read_write")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400
    if role not in ("admin", "read_write", "read_only"):
        return jsonify({"error": "Invalid role"}), 400
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        return jsonify({"error": "Username already exists"}), 400
    db.execute("INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
               (username, generate_password_hash(password), role))
    db.commit()
    log_action("create_user", username, f"role={role}")
    return jsonify({"ok": True})

@app.route("/api/users/<int:user_id>", methods=["DELETE"])
@login_required
def api_users_delete(user_id):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    if user["id"] == user_id:
        return jsonify({"error": "Cannot remove yourself"}), 400
    db = get_db()
    target = db.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        return jsonify({"error": "User not found"}), 404
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    log_action("delete_user", target["username"])
    return jsonify({"ok": True})

# ── LOGS API ───────────────────────────────────────────────────────────────

@app.route("/api/logs", methods=["GET"])
@login_required
def api_logs_get():
    user = current_user()
    if user["role"] != "admin":
        return jsonify([])
    limit = request.args.get("limit", 300, type=int)
    db = get_db()
    rows = db.execute("SELECT * FROM logs ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    return jsonify([dict(r) for r in rows])

# ── VERSIONS ALL API ────────────────────────────────────────────────────────

@app.route("/api/versions/all", methods=["GET"])
@login_required
def api_versions_all():
    db = get_db()
    rows = db.execute("""
        SELECT fv.id, fv.file_id, fv.version_no, fv.size_bytes, fv.sha256,
               fv.uploaded_by, fv.uploaded_at, fv.is_current,
               f.filename, u.username AS uploaded_by_name
        FROM file_versions fv
        JOIN files f ON f.id = fv.file_id
        LEFT JOIN users u ON u.id = fv.uploaded_by
        WHERE f.is_deleted = 0
        ORDER BY fv.uploaded_at DESC
        LIMIT 500
    """).fetchall()
    return jsonify([dict(r) for r in rows])

# ── BACKUP API ──────────────────────────────────────────────────────────────

BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")

def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    try:
        os.chmod(BACKUP_DIR, 0o775)
    except OSError:
        pass

@app.route("/api/backup/create", methods=["POST"])
@login_required
def api_backup_create():
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    ensure_backup_dir()
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    backup_name = f"teamvault_{ts}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        return jsonify({"error": f"Backup failed: {e}"}), 500
    log_action("backup_create", backup_name)
    return jsonify({"ok": True, "name": backup_name})

@app.route("/api/backup/list", methods=["GET"])
@login_required
def api_backup_list():
    user = current_user()
    if user["role"] != "admin":
        return jsonify([])
    ensure_backup_dir()
    backups = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        path = os.path.join(BACKUP_DIR, f)
        if os.path.isfile(path) and f.endswith(".db") and f.startswith("teamvault_"):
            backups.append({
                "name": f,
                "size_bytes": os.path.getsize(path),
                "created_at": os.path.getmtime(path),
            })
    return jsonify(backups)

@app.route("/api/backup/restore/<name>", methods=["POST"])
@login_required
def api_backup_restore(name):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    ensure_backup_dir()
    backup_path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(backup_path):
        return jsonify({"error": "Backup not found"}), 404
    try:
        src = sqlite3.connect(backup_path)
        dst = sqlite3.connect(DB_PATH)
        src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        return jsonify({"error": f"Restore failed: {e}"}), 500
    log_action("backup_restore", name)
    return jsonify({"ok": True, "message": f"Restored from {name}"})

@app.route("/api/backup/download/<name>", methods=["GET"])
@login_required
def api_backup_download(name):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    ensure_backup_dir()
    return send_from_directory(BACKUP_DIR, name, as_attachment=True)

@app.route("/api/backup/delete/<name>", methods=["DELETE"])
@login_required
def api_backup_delete(name):
    user = current_user()
    if user["role"] != "admin":
        return jsonify({"error": "Admin only"}), 403
    ensure_backup_dir()
    backup_path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(backup_path):
        return jsonify({"error": "Backup not found"}), 404
    os.remove(backup_path)
    log_action("backup_delete", name)
    return jsonify({"ok": True})

# ── INIT & RUN ─────────────────────────────────────────────────────────────

init_db()
ensure_dirs()
ensure_backup_dir()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
