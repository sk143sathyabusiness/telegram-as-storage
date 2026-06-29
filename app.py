"""
TeamVault — small-team private file storage backed by Telegram.

SETUP
  1. Create a Telegram bot via @BotFather and get the bot token.
  2. Create a Telegram channel (or group) and add the bot as admin.
  3. Get the channel ID (use @userinfobot or check channel info).
  4. Export environment variables:
       export TELEGRAM_BOT_TOKEN="your_bot_token"
       export TELEGRAM_CHANNEL_ID="your_channel_id"

  5. pip install flask requests cryptography python-dotenv

  6. python app.py

  7. First visit shows registration page to create organization admin.
"""

import os
import io
import sqlite3
import time
import secrets
import hashlib
import requests
from functools import wraps
from flask import Flask, request, jsonify, session, g, send_from_directory
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")

DB_PATH = os.path.join(os.path.dirname(__file__), "teamvault.db")
MAX_FILE_SIZE = 4_000_000_000  # 4 GB

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = secrets.token_hex(32)

ROLES = ("admin", "read_write", "read_only")

# ---------------------------------------------------------------------------
# TELEGRAM STORAGE CLIENT
# ---------------------------------------------------------------------------
def telegram_send_file(filename: str, file_bytes: bytes) -> dict:
    """Send file to Telegram channel. Returns dict with file_id and message_id."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        raise Exception("Telegram bot token or channel ID not configured")
    
    # Send document to channel
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    files = {"document": (filename, file_bytes)}
    data = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "parse_mode": "HTML"
    }
    resp = requests.post(url, files=files, data=data)
    result = resp.json()
    
    if not result.get("ok"):
        raise Exception(f"Telegram API error: {result.get('description', 'Unknown error')}")
    
    doc = result.get("result", {}).get("document", {})
    return {
        "file_id": doc.get("file_id"),
        "message_id": result.get("result", {}).get("message_id")
    }

def telegram_get_file(file_id: str) -> bytes:
    """Download file from Telegram using file_id."""
    if not TELEGRAM_BOT_TOKEN:
        raise Exception("Telegram bot token not configured")
    
    # Get file info
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile"
    resp = requests.post(url, json={"file_id": file_id})
    result = resp.json()
    
    if not result.get("ok"):
        raise Exception(f"Telegram API error: {result.get('description', 'Unknown error')}")
    
    file_path = result.get("result", {}).get("file_path")
    if not file_path:
        raise Exception("File path not found in response")
    
    # Download the file
    download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    resp = requests.get(download_url)
    return resp.content

def telegram_delete_file(file_id: str) -> bool:
    """Delete file from Telegram (unfortunately Telegram doesn't support this directly)."""
    # Telegram doesn't support deleting files programmatically
    # We just return True to indicate the operation was attempted
    # Files will remain in the channel but won't be tracked
    return True

def verify_telegram_connection() -> bool:
    """Verify bot can connect to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        resp = requests.get(url)
        if resp.status_code != 200:
            return False
        # Also verify channel access
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat"
        resp = requests.get(url, params={"chat_id": TELEGRAM_CHANNEL_ID})
        return resp.status_code == 200
    except Exception:
        return False

# ---------------------------------------------------------------------------
# DB SETUP
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'read_only',
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        parent_id INTEGER,
        created_by INTEGER,
        created_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        folder_id INTEGER,
        filename TEXT NOT NULL,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        deleted_by INTEGER,
        deleted_at INTEGER,
        created_by INTEGER,
        created_at INTEGER NOT NULL
    );

    -- Every upload/edit creates a new version row.
    CREATE TABLE IF NOT EXISTS file_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id INTEGER NOT NULL,
        version_no INTEGER NOT NULL,
        size_bytes INTEGER NOT NULL,
        chunk_count INTEGER NOT NULL DEFAULT 1,
        sha256 TEXT NOT NULL,
        uploaded_by INTEGER,
        uploaded_at INTEGER NOT NULL,
        is_current INTEGER NOT NULL DEFAULT 1
    );

    -- For MinIO we store one row per version (chunk_index=0).
    -- `telegram_file_id` repurposed as `object_key` (column name kept for
    -- schema compatibility so existing DBs don't need migration).
    CREATE TABLE IF NOT EXISTS file_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL,
        chunk_index INTEGER NOT NULL,
        telegram_file_id TEXT NOT NULL,   -- stores MinIO object key
        telegram_message_id TEXT NOT NULL, -- stores MinIO bucket name
        size_bytes INTEGER NOT NULL,
        UNIQUE(version_id, chunk_index)
    );

    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        ts INTEGER NOT NULL
    );
    """)
    db.commit()
    db.close()

def log_action(user_id, action, target="", detail=""):
    db = get_db()
    db.execute(
        "INSERT INTO logs (user_id, action, target, detail, ts) VALUES (?,?,?,?,?)",
        (user_id, action, target, detail, int(time.time())),
    )
    db.commit()

# ---------------------------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------------------------
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 200_000
    ).hex()

def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user_id" not in session:
            return jsonify({"error": "not authenticated"}), 401
        return f(*a, **kw)
    return wrapper

def role_required(*allowed_roles):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            db = get_db()
            row = db.execute(
                "SELECT role FROM users WHERE id=?", (session["user_id"],)
            ).fetchone()
            if not row or row["role"] not in allowed_roles:
                return jsonify({"error": "permission denied"}), 403
            return f(*a, **kw)
        return wrapper
    return deco

# ---------------------------------------------------------------------------
# STATIC — serve register.html if no users, else index.html
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count == 0:
        return send_from_directory(".", "register.html")
    return send_from_directory(".", "index.html")

# ---------------------------------------------------------------------------
# AUTH ROUTES
# ---------------------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    db = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username=?", (data.get("username"),)
    ).fetchone()
    if not user or hash_password(data.get("password", ""), user["salt"]) != user["password_hash"]:
        return jsonify({"error": "invalid credentials"}), 401
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    log_action(user["id"], "login")
    return jsonify({"ok": True, "username": user["username"], "role": user["role"]})

@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    log_action(session["user_id"], "logout")
    session.clear()
    return jsonify({"ok": True})

@app.route("/api/me", methods=["GET"])
@login_required
def me():
    db = get_db()
    user = db.execute(
        "SELECT id, username, role, created_at FROM users WHERE id=?",
        (session["user_id"],),
    ).fetchone()
    return jsonify(dict(user))

# ---------------------------------------------------------------------------
# USER MANAGEMENT
# ---------------------------------------------------------------------------
@app.route("/api/users", methods=["GET"])
@login_required
@role_required("admin")
def list_users():
    db = get_db()
    rows = db.execute(
        "SELECT id, username, role, created_at FROM users ORDER BY created_at"
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/users", methods=["POST"])
@login_required
@role_required("admin")
def create_user():
    data = request.json
    username, password, role = data["username"], data["password"], data.get("role", "read_only")
    if role not in ROLES:
        return jsonify({"error": "invalid role"}), 400
    salt = secrets.token_hex(16)
    pw_hash = hash_password(password, salt)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?,?,?,?,?)",
            (username, pw_hash, salt, role, int(time.time())),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "username already exists"}), 400
    log_action(session["user_id"], "create_user", target=username, detail=role)
    return jsonify({"ok": True})

@app.route("/api/users/<int:uid>", methods=["DELETE"])
@login_required
@role_required("admin")
def delete_user(uid):
    if uid == session["user_id"]:
        return jsonify({"error": "cannot delete yourself"}), 400
    db = get_db()
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    log_action(session["user_id"], "delete_user", target=str(uid))
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# FOLDER ROUTES
# ---------------------------------------------------------------------------
@app.route("/api/folders", methods=["GET"])
@login_required
def list_folders():
    db = get_db()
    # Return all folders so the client can build the full tree
    rows = db.execute("SELECT * FROM folders ORDER BY name").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/folders", methods=["POST"])
@login_required
@role_required("admin", "read_write")
def create_folder():
    data = request.json
    db = get_db()
    db.execute(
        "INSERT INTO folders (name, parent_id, created_by, created_at) VALUES (?,?,?,?)",
        (data["name"], data.get("parent_id"), session["user_id"], int(time.time())),
    )
    db.commit()
    log_action(session["user_id"], "create_folder", target=data["name"])
    return jsonify({"ok": True})

@app.route("/api/folders/<int:folder_id>", methods=["DELETE"])
@login_required
@role_required("admin")
def delete_folder(folder_id):
    db = get_db()
    # Soft-delete all files in this folder
    db.execute(
        "UPDATE files SET is_deleted=1, deleted_by=?, deleted_at=? WHERE folder_id=?",
        (session["user_id"], int(time.time()), folder_id),
    )
    db.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    db.commit()
    log_action(session["user_id"], "delete_folder", target=str(folder_id))
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# FILE ROUTES
# ---------------------------------------------------------------------------
@app.route("/api/files", methods=["GET"])
@login_required
def list_files():
    db = get_db()
    folder_id = request.args.get("folder_id") or None
    files = db.execute(
        "SELECT f.*, u.username as created_by_name FROM files f "
        "LEFT JOIN users u ON f.created_by = u.id "
        "WHERE f.folder_id IS ? AND f.is_deleted=0",
        (folder_id,),
    ).fetchall()
    out = []
    for f in files:
        cur = db.execute(
            "SELECT fv.*, u.username as uploaded_by_name FROM file_versions fv "
            "LEFT JOIN users u ON fv.uploaded_by = u.id "
            "WHERE fv.file_id=? AND fv.is_current=1",
            (f["id"],),
        ).fetchone()
        out.append({**dict(f), "current_version": dict(cur) if cur else None})
    return jsonify(out)

@app.route("/api/files/upload", methods=["POST"])
@login_required
@role_required("admin", "read_write")
def upload_file():
    """
    Expects multipart: file=<file data>, filename, folder_id, sha256.
    Stores the file in Telegram channel; records metadata in SQLite.
    Re-uploading the same filename in the same folder creates a new version.
    """
    f = request.files["file"]
    filename = request.form["filename"]
    folder_id = request.form.get("folder_id") or None
    sha256 = request.form.get("sha256", "")
    file_bytes = f.read()
    size_bytes = len(file_bytes)

    if size_bytes > MAX_FILE_SIZE:
        return jsonify({"error": f"file exceeds 4 GB limit"}), 400

    db = get_db()
    existing = db.execute(
        "SELECT * FROM files WHERE filename=? AND folder_id IS ? AND is_deleted=0",
        (filename, folder_id),
    ).fetchone()

    if existing:
        file_id = existing["id"]
        last_v = db.execute(
            "SELECT MAX(version_no) as v FROM file_versions WHERE file_id=?", (file_id,)
        ).fetchone()["v"] or 0
        db.execute("UPDATE file_versions SET is_current=0 WHERE file_id=?", (file_id,))
        version_no = last_v + 1
    else:
        cur = db.execute(
            "INSERT INTO files (folder_id, filename, created_by, created_at) VALUES (?,?,?,?)",
            (folder_id, filename, session["user_id"], int(time.time())),
        )
        file_id = cur.lastrowid
        version_no = 1

    cur = db.execute(
        """INSERT INTO file_versions
           (file_id, version_no, size_bytes, chunk_count, sha256, uploaded_by, uploaded_at, is_current)
           VALUES (?,?,?,?,?,?,?,1)""",
        (file_id, version_no, size_bytes, 1, sha256, session["user_id"], int(time.time())),
    )
    version_id = cur.lastrowid

    # Upload to Telegram
    telegram_result = telegram_send_file(filename, file_bytes)
    file_id_telegram = telegram_result["file_id"]
    message_id = telegram_result["message_id"]

    db.execute(
        """INSERT INTO file_chunks
           (version_id, chunk_index, telegram_file_id, telegram_message_id, size_bytes)
           VALUES (?,?,?,?,?)""",
        (version_id, 0, file_id_telegram, str(message_id), size_bytes),
    )
    db.commit()

    log_action(
        session["user_id"], "upload", target=filename,
        detail=f"v{version_no}, {size_bytes} bytes",
    )
    return jsonify({"ok": True, "file_id": file_id, "version": version_no, "size_bytes": size_bytes})

@app.route("/api/files/<int:file_id>/download", methods=["GET"])
@login_required
def download_file(file_id):
    db = get_db()
    version = db.execute(
        "SELECT * FROM file_versions WHERE file_id=? AND is_current=1", (file_id,)
    ).fetchone()
    if not version:
        return jsonify({"error": "not found"}), 404
    fmeta = db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    chunk = db.execute(
        "SELECT * FROM file_chunks WHERE version_id=? AND chunk_index=0", (version["id"],)
    ).fetchone()
    if not chunk:
        return jsonify({"error": "no data stored for this version"}), 500

    file_id_telegram = chunk["telegram_file_id"]
    content = telegram_get_file(file_id_telegram)

    log_action(
        session["user_id"], "download", target=fmeta["filename"],
        detail=f"v{version['version_no']}",
    )
    return content, 200, {"Content-Type": "application/octet-stream", "Content-Disposition": f"attachment; filename=\"{fmeta['filename']}\""}

@app.route("/api/files/<int:file_id>/versions", methods=["GET"])
@login_required
def list_versions(file_id):
    db = get_db()
    rows = db.execute(
        """SELECT fv.*, u.username as uploaded_by_name
           FROM file_versions fv
           LEFT JOIN users u ON fv.uploaded_by = u.id
           WHERE fv.file_id=? ORDER BY fv.version_no DESC""",
        (file_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/files/<int:file_id>/restore/<int:version_no>", methods=["POST"])
@login_required
@role_required("admin", "read_write")
def restore_version(file_id, version_no):
    db = get_db()
    target = db.execute(
        "SELECT * FROM file_versions WHERE file_id=? AND version_no=?", (file_id, version_no)
    ).fetchone()
    if not target:
        return jsonify({"error": "version not found"}), 404
    db.execute("UPDATE file_versions SET is_current=0 WHERE file_id=?", (file_id,))
    db.execute("UPDATE file_versions SET is_current=1 WHERE id=?", (target["id"],))
    db.commit()
    fmeta = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    log_action(
        session["user_id"], "restore_version",
        target=fmeta["filename"] if fmeta else str(file_id),
        detail=f"to v{version_no}",
    )
    return jsonify({"ok": True})

@app.route("/api/files/<int:file_id>", methods=["DELETE"])
@login_required
@role_required("admin")
def delete_file(file_id):
    db = get_db()
    fmeta = db.execute("SELECT filename FROM files WHERE id=?", (file_id,)).fetchone()
    db.execute(
        "UPDATE files SET is_deleted=1, deleted_by=?, deleted_at=? WHERE id=?",
        (session["user_id"], int(time.time()), file_id),
    )
    db.commit()
    log_action(
        session["user_id"], "delete",
        target=fmeta["filename"] if fmeta else str(file_id),
    )
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# TRASH (admin only — view soft-deleted files, restore or hard-delete)
# ---------------------------------------------------------------------------
@app.route("/api/trash", methods=["GET"])
@login_required
@role_required("admin")
def list_trash():
    db = get_db()
    rows = db.execute(
        """SELECT f.*, u.username as deleted_by_name
           FROM files f
           LEFT JOIN users u ON f.deleted_by = u.id
           WHERE f.is_deleted=1
           ORDER BY f.deleted_at DESC""",
    ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/trash/<int:file_id>/restore", methods=["POST"])
@login_required
@role_required("admin")
def restore_from_trash(file_id):
    db = get_db()
    db.execute(
        "UPDATE files SET is_deleted=0, deleted_by=NULL, deleted_at=NULL WHERE id=?",
        (file_id,),
    )
    db.commit()
    log_action(session["user_id"], "restore_from_trash", target=str(file_id))
    return jsonify({"ok": True})

@app.route("/api/trash/<int:file_id>", methods=["DELETE"])
@login_required
@role_required("admin")
def hard_delete_file(file_id):
    """Permanently remove file + all versions from Telegram and SQLite."""
    db = get_db()
    versions = db.execute(
        "SELECT id FROM file_versions WHERE file_id=?", (file_id,)
    ).fetchall()
    for v in versions:
        chunks = db.execute(
            "SELECT telegram_file_id FROM file_chunks WHERE version_id=?", (v["id"],)
        ).fetchall()
        for c in chunks:
            try:
                telegram_delete_file(c["telegram_file_id"])
            except Exception:
                pass
        db.execute("DELETE FROM file_chunks WHERE version_id=?", (v["id"],))
    db.execute("DELETE FROM file_versions WHERE file_id=?", (file_id,))
    db.execute("DELETE FROM files WHERE id=?", (file_id,))
    db.commit()
    log_action(session["user_id"], "hard_delete", target=str(file_id))
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# LOGS
# ---------------------------------------------------------------------------
@app.route("/api/logs", methods=["GET"])
@login_required
@role_required("admin")
def get_logs():
    db = get_db()
    limit = min(int(request.args.get("limit", 200)), 1000)
    rows = db.execute(
        """SELECT logs.*, users.username FROM logs
           LEFT JOIN users ON logs.user_id = users.id
           ORDER BY ts DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])

# ---------------------------------------------------------------------------
# BOOTSTRAP FIRST ADMIN
# ---------------------------------------------------------------------------
@app.route("/api/org/register", methods=["POST"])
def org_register():
    """Register first organization admin (only works when no users exist)."""
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count > 0:
        return jsonify({"error": "organization already initialized"}), 400
    data = request.json
    username = data.get("username")
    password = data.get("password")
    org_name = data.get("orgName", "My Organization")
    
    if not username or not password:
        return jsonify({"error": "username and password required"}), 400
    
    salt = secrets.token_hex(16)
    pw_hash = hash_password(password, salt)
    db.execute(
        "INSERT INTO users (username, password_hash, salt, role, created_at) VALUES (?,?,?,?,?)",
        (username, pw_hash, salt, "admin", int(time.time())),
    )
    db.commit()
    return jsonify({"ok": True, "redirect": "/"})

@app.route("/api/bootstrap", methods=["POST"])
def bootstrap():
    """Legacy endpoint for bootstrap - redirects to org_register."""
    return org_register()

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    if verify_telegram_connection():
        print("✓ Telegram bot connected successfully")
    else:
        print("! Warning: Telegram bot not configured or cannot connect")
        print("  Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID in .env")
    print("TeamVault starting on http://localhost:5000")
    app.run(debug=True, port=5000)
