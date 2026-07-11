# Supabase + Telethon Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate v0 codebase (SQLite + Bot API + local disk) to target architecture (Supabase + Telethon + zero local storage) as documented in `AGENTS.md`.

**Architecture:** Three-layer rewrite: (1) Telethon userbot replaces Bot API, (2) Supabase replaces SQLite, (3) local disk fallback and backup APIs removed. Frontend updated to match new API contracts.

**Tech Stack:** Flask, Telethon, supabase-py, python-dotenv, cryptography

## Global Constraints
- ZERO LOCAL STORAGE — all file ops use `io.BytesIO`, never disk
- Telethon user session only, no Bot API tokens for file storage
- Supabase is the only metadata store — no SQLite
- `supabase_schema.sql` is single source of truth for schema
- All mutating actions write to `audit_logs`
- Permission checks folder-scoped via `permissions` table
- Role-expanded: `master_admin` > `org_admin` > `read_write` > `read_only`

---

## File Structure

```
Modified:
  app.py                    # Full rewrite: Supabase, Telethon, permissions, audit
  telegram_bot.py           # Rewrite: Telethon userbot, chunked upload
  supabase_schema.sql       # Update: replace file_chunks with message_ids jsonb
  .env.example              # Replace Bot API vars with Telethon API vars
  requirements.txt          # Add supabase-py, remove cryptg (optional)
  app.js                    # Update: new role names, permission display
  index.html                # Update: role dropdown, permission UI
  
Deleted (v0 artifacts):
  uploads/                  # Remove directory + all disk paths
  backups/                  # Remove directory + all backup APIs
  teamvault.db              # SQLite file (rebuild from zero)
  session_name.session      # Will be recreated by Telethon
  .secret_key               # Will be managed differently
```

---

### Task 1: Update Config & Schema

**Files:**
- Modify: `.env.example`
- Modify: `requirements.txt`
- Modify: `supabase_schema.sql`

**Interfaces:**
- Produces: env var names for Telethon (`TELETHON_API_ID`, `TELETHON_API_HASH`), updated Python deps, corrected schema

- [ ] **Update `.env.example`**

Replace Bot API vars with Telethon vars:
```
# TeamVault Telethon Configuration
# Get api_id and api_hash from https://my.telegram.org/apps
TELETHON_API_ID=your_api_id
TELETHON_API_HASH=your_api_hash

# Default Telegram channel chat ID for file storage
TELETHON_CHAT_ID=-1001234567890

# Flask secret key (optional — auto-generated if not set)
SECRET_KEY=your_secret_key_here
```

- [ ] **Update `requirements.txt`**

```txt
flask
supabase-py
cryptography
python-dotenv
telethon
requests               # kept only for backward compat during migration
```

- [ ] **Update `supabase_schema.sql`**

Replace the `file_chunks` table with `message_ids` jsonb on `file_versions`, add `permissions` table, rename `users.salt`, add `storage_key` fallback:

```sql
-- Updated schema — single source of truth for Supabase tables

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    industry TEXT,
    size TEXT,
    settings JSONB DEFAULT '{}'::jsonb,
    telegram_chat_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'read_only'
        CHECK (role IN ('master_admin', 'org_admin', 'read_write', 'read_only')),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE folders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES folders(id),
    name TEXT NOT NULL,
    is_essential BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    folder_id UUID REFERENCES folders(id),
    name TEXT NOT NULL,
    mime_type TEXT,
    is_deleted BOOLEAN NOT NULL DEFAULT false,
    deleted_at TIMESTAMPTZ,
    deleted_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE file_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL DEFAULT 1,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    sha256 TEXT,
    message_ids JSONB DEFAULT '[]'::jsonb,
    uploaded_by UUID REFERENCES users(id),
    uploaded_at TIMESTAMPTZ DEFAULT now(),
    is_current BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (file_id, version_number)
);

CREATE TABLE permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    folder_id UUID REFERENCES folders(id) ON DELETE CASCADE,
    permission_level TEXT NOT NULL DEFAULT 'read_only'
        CHECK (permission_level IN ('read_only', 'read_write', 'org_admin')),
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (user_id, folder_id)
);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    actor_id UUID REFERENCES users(id),
    actor_role TEXT,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Version limit trigger: keep max 5 versions per file, FIFO
CREATE OR REPLACE FUNCTION trg_enforce_version_limit()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM file_versions fv
    WHERE fv.file_id = NEW.file_id
      AND fv.id NOT IN (
          SELECT id FROM file_versions
          WHERE file_id = NEW.file_id
          ORDER BY version_number DESC
          LIMIT 5
      );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_enforce_version_limit
    AFTER INSERT ON file_versions
    FOR EACH ROW EXECUTE FUNCTION trg_enforce_version_limit();

-- RLS
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE folders ENABLE ROW LEVEL SECURITY;
ALTER TABLE files ENABLE ROW LEVEL SECURITY;
ALTER TABLE file_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE permissions ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Org isolation: users see only their org's data
CREATE POLICY org_isolation ON organizations
    FOR ALL USING (
        id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY org_isolation ON users
    FOR SELECT USING (true);

CREATE POLICY org_isolation ON users
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
        OR current_setting('app.user_role')::text = 'master_admin'
    );

CREATE POLICY org_isolation ON folders
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

CREATE POLICY org_isolation ON files
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

CREATE POLICY org_isolation ON file_versions
    FOR ALL USING (
        file_id IN (
            SELECT f.id FROM files f
            JOIN users u ON f.org_id = u.org_id
            WHERE u.id = current_setting('app.user_id')::UUID
        )
    );

CREATE POLICY org_isolation ON permissions
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

CREATE POLICY org_isolation ON audit_logs
    FOR ALL USING (
        org_id IN (SELECT org_id FROM users WHERE id = current_setting('app.user_id')::UUID)
    );

-- Helper to set user context for RLS
CREATE OR REPLACE FUNCTION set_app_context(uid UUID, urole TEXT)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('app.user_id', uid::text, true);
    PERFORM set_config('app.user_role', urole, true);
END;
$$;
```

---

### Task 2: Rewrite `telegram_bot.py` with Telethon

**Files:**
- Modify: `telegram_bot.py`

**Interfaces:**
- Produces: `upload_chunks(file_path, file_name, chat_id) -> list[int]`, `download_chunks(chat_id, message_ids) -> bytes`, `get_chat_id(org_name) -> int`

- [ ] **Write Telethon-based telegram_bot.py**

```python
"""
Telethon userbot for TeamVault.
Uploads/downloads encrypted file chunks to/from Telegram channels.
Each chunk = one Telegram message. Message IDs stored in file_versions.message_ids.
"""

import os
import io
import asyncio
from telethon import TelegramClient, utils

API_ID = int(os.getenv("TELETHON_API_ID", "0"))
API_HASH = os.getenv("TELETHON_API_HASH", "")
SESSION_FILE = os.path.join(os.path.dirname(__file__), "session_name.session")
CHUNK_SIZE = 1_900_000_000  # ~1.9 GB per chunk

_client = None

async def _get_client():
    global _client
    if _client is None:
        _client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await _client.start()
    return _client

def is_configured():
    return bool(API_ID and API_HASH)

async def upload_chunks(file_bytes: bytes, filename: str, chat_id: int) -> list[int]:
    """Upload encrypted bytes as one or more messages. Returns list of message IDs."""
    client = await _get_client()
    entity = await client.get_entity(utils.resolve_id(chat_id))
    message_ids = []
    offset = 0
    while offset < len(file_bytes):
        chunk = file_bytes[offset:offset + CHUNK_SIZE]
        # Upload as document with filename
        buf = io.BytesIO(chunk)
        buf.name = filename if offset == 0 else f"{filename}.part{offset // CHUNK_SIZE}"
        msg = await client.send_file(entity, buf, force_document=True)
        message_ids.append(msg.id)
        offset += CHUNK_SIZE
    return message_ids

async def download_chunks(chat_id: int, message_ids: list[int]) -> bytes:
    """Download and concatenate chunks from Telegram messages."""
    client = await _get_client()
    entity = await client.get_entity(utils.resolve_id(chat_id))
    chunks = []
    for mid in message_ids:
        msg = await client.get_messages(entity, ids=mid)
        if msg and msg.document:
            buf = io.BytesIO()
            await client.download_file(msg.document, buf)
            chunks.append(buf.getvalue())
    return b"".join(chunks)
```

- [ ] **Verify telethon_bot can be imported without errors**

Run: `python3 -c "import telegram_bot; print('OK:', telegram_bot.is_configured())"`
Expected: `OK: False` (no creds in test env)

---

### Task 3: Rewrite `app.py` — Supabase client, auth, org registration

**Files:**
- Modify: `app.py`

**Interfaces:**
- Consumes: `telegram_bot.upload_chunks()`, `telegram_bot.download_chunks()`
- Produces: same Flask API routes but backed by Supabase

- [ ] **Add Supabase client module** at top of `app.py`

```python
import os, io, json, time, secrets, hashlib, uuid
from functools import wraps
from flask import Flask, request, jsonify, session, g, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

import telegram_bot

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

_supabase: Client | None = None
def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase
```

- [ ] **Replace `get_db()`, `init_db()`, `close_db()` with Supabase init**

No SQLite boilerplate. Remove `DB_PATH`, `get_db()`, `init_db()`, `close_db()`, `ensure_dirs()`, `ensure_backup_dir()`.

Add startup check:
```python
def check_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        raise SystemExit(1)
```

- [ ] **Rewrite auth routes** (`/api/login`, `/api/logout`, `/api/me`)

```python
def set_rls_context(user_id, role):
    """Set user context for Supabase RLS policies."""
    sup = get_supabase()
    sup.rpc("set_app_context", {"uid": user_id, "urole": role}).execute()

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
    # Write audit log
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["id"],
        "actor_role": user["role"],
        "action": "login",
    }).execute()
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"]})
```

- [ ] **Rewrite org registration** (`/api/org/register`)

Create org + user in a single logical step:
```python
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
```

---

### Task 4: Rewrite `app.py` — File operations with Supabase + Telethon

**Files:**
- Modify: `app.py` (add file API routes)

- [ ] **Rewrite `_store_file_blob()` and `_load_file_blob()`**

```python
def _store_file_blob(f, org_id):
    """Upload encrypted bytes to org's Telegram channel. Returns list of message IDs."""
    file_bytes = f.read()
    if not telegram_bot.is_configured():
        raise RuntimeError("Telegram not configured")
    # Get org's chat_id
    sup = get_supabase()
    org = sup.table("organizations").select("telegram_chat_id").eq("id", org_id).single().execute()
    chat_id = int(org.data["telegram_chat_id"]) if org.data.get("telegram_chat_id") else None
    if not chat_id:
        raise RuntimeError("No Telegram chat_id configured for this organisation")
    message_ids = telegram_bot.upload_chunks(file_bytes, f.filename or "file", chat_id)
    return message_ids, len(file_bytes)

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
```

- [ ] **Rewrite file upload route** (`/api/files/upload`)

```python
@app.route("/api/files/upload", methods=["POST"])
@login_required
def api_files_upload():
    user = session  # user_id, org_id, role set in session
    if user["role"] == "read_only":
        return jsonify({"error": "Permission denied"}), 403
    f = request.files.get("file")
    filename = request.form.get("filename", "unnamed")
    folder_id = request.form.get("folder_id") or None
    sha256 = request.form.get("sha256", "")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    sup = get_supabase()
    # Check folder permission if folder specified
    if folder_id:
        perm = _check_permission(sup, user["user_id"], user["org_id"], folder_id)
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
        "uploaded_by": user["user_id"],
        "is_current": True,
    }).execute()
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["user_id"],
        "actor_role": user["role"],
        "action": "upload",
        "target_type": "file",
        "target_id": str(file_id),
        "details": json.dumps({"filename": filename, "version": new_ver}),
    }).execute()
    return jsonify({"ok": True, "file_id": file_id, "version": new_ver})
```

---

### Task 5: Add permission checking, folders, users, audit, orgs APIs

**Files:**
- Modify: `app.py`

- [ ] **Add `_check_permission()` helper**

```python
def _check_permission(sup, user_id, org_id, folder_id=None):
    """Check user's effective permission for a folder. Returns level or None."""
    user_result = sup.table("users").select("role").eq("id", user_id).single().execute()
    role = user_result.data["role"]
    if role == "master_admin":
        return "org_admin"  # full access
    if role == "org_admin":
        return "org_admin"
    if not folder_id:
        return role  # org-wide default
    # Check folder-specific permission
    perm = sup.table("permissions").select("permission_level").eq("user_id", user_id).eq("folder_id", folder_id).maybe_single().execute()
    if perm.data:
        return perm.data["permission_level"]
    return role  # fall back to user's org role
```

- [ ] **Rewrite folders, files list, versions, trash, users, logs, orgs routes**

All follow same pattern: `sup.table(...).select(...).eq("org_id", org_id).execute()` instead of `db.execute("SELECT ...")`.

Key changes from current SQLite queries:
- All queries filtered by `org_id`
- `files.is_deleted` for soft delete
- `file_versions.message_ids` (jsonb) instead of `storage_key`
- `audit_logs` table instead of `logs`
- User role validation expanded to 4 roles

---

### Task 6: Zero local storage — remove backup APIs and local fallback

**Files:**
- Modify: `app.py`
- Delete: `uploads/`, `backups/` (directories)

- [ ] **Remove backup API routes** (`/api/backup/create`, `/api/backup/list`, `/api/backup/restore`, `/api/backup/download`, `/api/backup/delete`)

- [ ] **Remove all `os.path.join(app.root_path, "uploads", ...)` and `ensure_dirs()` calls**

- [ ] **Delete local storage directories**

```bash
rm -rf uploads/ backups/
```

---

### Task 7: Update frontend to match new API

**Files:**
- Modify: `app.js`
- Modify: `index.html`

- [ ] **Update role display and permission UI in `index.html`**

Change role dropdown options in new-user-form:
```html
<select id="nu-role">
  <option value="read_only">Read only</option>
  <option value="read_write">Read/Write</option>
  <option value="org_admin">Org Admin</option>
</select>
```
Add `master_admin` option (only shown to master_admin users).

- [ ] **Update `app.js` role checks**

Replace `currentUser.role !== "admin"` with `currentUser.role !== "org_admin" && currentUser.role !== "master_admin"` where needed.

Add org_id to upload payload:
```javascript
fd.append("org_id", currentUser.org_id);
```

---

### Task 8: Cleanup — remove v0 artifacts

**Files:**
- Delete: `bot.py` (if it's old Bot API code)
- Delete: `teamvault.db`
- Modify: `.gitignore`

- [ ] **Remove stale files**

```bash
rm -f bot.py teamvault.db session_name.session
```

- [ ] **Update `.gitignore`**

```
.env
.secret_key
session_name.session
__pycache__/
*.pyc
venv/
```

---

## Verification

After each task:
```bash
python3 -c "from flask import Flask; app = Flask(__name__); print('Flask OK')"
python3 -c "import telegram_bot; print('Telethon OK:', telegram_bot.is_configured())"
```
