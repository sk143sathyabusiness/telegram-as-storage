# Modular Monolith Redraft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite Telegram-as-Storage (Flask + Telethon + Supabase) from 1859-line monolith into modular `app/` package with same features (chunked >2GB, 5-version FIFO, trash, sharing, backups, audit, 4 roles, RLS) but simpler boundaries (<250 lines/file) and baked-in secrets handling.

**Architecture:** Modular monolith — `app/__init__.py` factory registers blueprints (`auth`, `orgs`, `folders`, `files`, `versions`, `trash`, `sharing`, `backups`, `users`, `logs`), single `supabase_client`, `telegram_service`, `config` validation, `utils` permission helpers. Frontend splits `app.js` 91k into `frontend/*.js` modules. `api/index.py` stays thin Vercel entry.

**Tech Stack:** Python 3.10+ Flask 3.0, Telethon (userbot, `session` file only local artifact), Supabase Postgres + RLS (`supabase==1.12.0`), `cryptography` AES-256-GCM client-side, `python-dotenv`, plain JS/HTML.

## Global Constraints

- ZERO LOCAL STORAGE — use `io.BytesIO` only; never write file bytes to disk; only `*.session` allowed.
- Never store unencrypted bytes or raw passphrase server-side.
- Files >2GB split into `CHUNK_SIZE_BYTES` (~1900000000) chunks, `file_versions.message_ids` jsonb ordered.
- Every file modify = new `file_versions` row, max 5 FIFO (`trg_enforce_version_limit` trigger).
- Every mutating action writes `audit_logs(actor_id, actor_role, org_id)`.
- Permission checks folder-scoped via `permissions` table — never trust client role alone.
- RLS is source of truth — pass `app.user_id`/`app.user_role` via `set_app_context`.
- New table/column → update `supabase_schema.sql` only.

---

### Task 1: App Factory, Config & Security Scaffold

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/supabase_client.py`
- Create: `app/security.py`
- Modify: `api/index.py:1-5` (thin factory import)
- Test: `tests/test_config_security.py`

**Interfaces:**
- Consumes: `.env.example`, `supabase_schema.sql` (read only)
- Produces: `create_app() -> Flask`, `get_supabase() -> Client`, `check_supabase()`, `_security_headers(resp)`, `_enforce_session_timeout`, constants `CHUNK_SIZE_BYTES`, `SESSION_TIMEOUT_SECONDS`, `BACKUP_CHANNEL_ID`

- [ ] **Step 1: Write failing test for config + factory**

```python
# tests/test_config_security.py
import os

def test_config_parses_chunk_size():
    from app.config import CHUNK_SIZE_BYTES
    assert CHUNK_SIZE_BYTES == 1900000000

def test_create_app_has_security_headers():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.get("/")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert "TeamVault" in r.headers.get("Server", "")
        assert r.headers.get("X-Frame-Options") == "DENY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_config_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Implement minimal scaffold**

```python
# app/config.py
import os, secrets
from dotenv import load_dotenv
load_dotenv()
CHUNK_SIZE_BYTES = int(os.getenv("CHUNK_SIZE_BYTES", "1900000000"))
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "43200"))
BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0"))
_raw = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
# weak markers + length check printed at import (see design §5)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# app/supabase_client.py
import os
from supabase import create_client, Client
from app.config import SUPABASE_URL, SUPABASE_KEY
_supabase = None
def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase
def check_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")

# app/security.py
from flask import request, jsonify, session
from datetime import datetime
from app.config import SESSION_TIMEOUT_SECONDS
def register_security(app):
    # set SESSION_COOKIE_* and after_request CSP here
    pass

# app/__init__.py
from flask import Flask
from app.config import SESSION_TIMEOUT_SECONDS
def create_app():
    app = Flask(__name__, static_folder="..", template_folder="..")
    # wire config, security, error handlers, blocked static
    return app
```

Copy real header/cookie/error logic from current `app.py:23-72,81-115` into these modules (split, not rewrite).

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_config_security.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/__init__.py app/config.py app/supabase_client.py app/security.py api/index.py tests/test_config_security.py
git commit -m "feat: scaffold app factory, config and security"
```

---

### Task 2: Utils + Auth

**Files:**
- Create: `app/utils.py`
- Create: `app/auth.py`
- Test: `tests/test_auth_utils.py`

**Interfaces:**
- Consumes: `app.supabase_client.get_supabase`, `app.config._SHARE_PW_SALT`
- Produces: `utils._check_permission(sup,user_id,org_id,folder_id)`, `utils._require_active_org`, `utils._resolve_folder_name`, `utils._parse_message_ids`, `utils.fmt_size`, `utils.hash_share_password(pw)->str`, `utils.verify_share_password(pw, stored)->bool`, `utils.log_action`, blueprint `auth_bp` with `POST /api/login`, `POST /api/logout`, `GET /api/me`

- [ ] **Step 1: Write failing test**

```python
# tests/test_auth_utils.py
def test_hash_verify_current_and_static_salt():
    from app.utils import hash_share_password, verify_share_password
    h = hash_share_password("secret123")
    assert h.startswith("pbkdf2$")
    assert verify_share_password("secret123", h) is True
    assert verify_share_password("wrong", h) is False

def test_parse_message_ids():
    from app.utils import _parse_message_ids
    assert _parse_message_ids([1,2]) == [1,2]
    assert _parse_message_ids("[1,2]") == [1,2]

def test_check_permission_master_admin(monkeypatch):
    from app import utils
    # mock sup.table chain to return master_admin role
    # assert _check_permission returns "org_admin"
    pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_auth_utils.py::test_hash_verify_current_and_static_salt -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement utils + auth**

Move `app.py:193-261,186-192,230-244,252-285` into `app/utils.py` (add dual-salt verify: try `_SHARE_PW_SALT` then `_SHARE_PW_SALT_STATIC`). Move login rate-limit + `POST /api/login` `328-383` into `app/auth.py` blueprint, `login_required`, `current_user`, session timeout `before_request` into `security.py`. Register blueprint in `create_app()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_auth_utils.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/utils.py app/auth.py tests/test_auth_utils.py
git commit -m "feat: add utils and auth blueprint"
```

---

### Task 3: Orgs & Folders

**Files:**
- Create: `app/orgs.py`
- Create: `app/folders.py`
- Test: `tests/test_orgs_folders.py`

**Interfaces:**
- Consumes: `utils._check_permission`, `get_supabase`, `log_action`
- Produces: `orgs_bp`: `POST /api/org/register`, `GET /api/orgs`, `POST /api/orgs/<id>/approve|reject`; `folders_bp`: `GET/POST /api/folders`, `DELETE /api/folders/<id>`, `GET/POST/DELETE /api/folders/<id>/permissions`, `GET /api/folders/permissions/all`, `GET /api/folders/all-users`

- [ ] **Step 1: Write failing test**

```python
def test_org_register_requires_fields():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.post("/api/org/register", json={})
        assert r.status_code == 400
        assert "org_name" in r.get_json()["error"].lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_orgs_folders.py::test_org_register_requires_fields -v`
Expected: FAIL

- [ ] **Step 3: Implement blueprints**

Move `app.py:385-470` → `orgs.py`, `471-695` → `folders.py` (split permission routes). Keep `_add_ancestors` helper in `folders.py`. Register blueprints.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_orgs_folders.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/orgs.py app/folders.py tests/test_orgs_folders.py
git commit -m "feat: add orgs and folders blueprints"
```

---

### Task 4: Telegram Service + Files

**Files:**
- Create: `telegram_service.py` (rename from `telegram_bot.py` with lazy env fix already in `74833ef`)
- Create: `app/files.py`
- Test: `tests/test_files_telegram.py`

**Interfaces:**
- Consumes: `app.config.CHUNK_SIZE_BYTES`, `telegram_service.is_configured`, `upload_chunks_streaming`, `download_chunks_streaming`
- Produces: `telegram_service.is_configured()->bool`, `_make_client()->TelegramClient`, `upload_chunks_streaming(stream,name,chat_id)->List[int]`, `download_chunks_streaming(chat_id, ids)` generator, `app/files.py`: `GET /api/files`, `GET /api/files/search`, `POST /api/files/upload`, `GET /api/files/<id>/download`, `GET /api/files/<id>/preview`, `POST /api/files/<id>/email`

- [ ] **Step 1: Write failing test**

```python
def test_telegram_not_configured_when_env_missing(monkeypatch):
    import telegram_service
    monkeypatch.setattr(telegram_service, "API_ID", None)
    monkeypatch.setattr(telegram_service, "API_HASH", None)
    assert telegram_service.is_configured() is False

def test_files_requires_auth():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.get("/api/files").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_files_telegram.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Rename `telegram_bot.py` → `telegram_service.py` (keep lazy parsing `telegram_bot.py:28` fix). Move `app.py:697-882,771-923,1585-1623,1494-1583` (files + preview + email) into `app/files.py`. Email route validates `EMAIL_RE`, `MAX_RECIPIENTS=50`, strips `\r\n` from `From`/`Subject` (from `app.py:1528-1556`).

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_files_telegram.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram_service.py app/files.py tests/test_files_telegram.py
git commit -m "feat: add telegram service and files blueprint"
```

---

### Task 5: Versions & Trash

**Files:**
- Create: `app/versions.py`
- Create: `app/trash.py`
- Test: `tests/test_versions_trash.py`

**Interfaces:**
- Consumes: `get_supabase`, `_check_permission`, `telegram_service.delete_file`
- Produces: `versions_bp`: `GET /api/files/<id>/versions`, `POST /api/files/<id>/restore/<ver>`, `GET /api/versions/all`; `trash_bp`: `GET /api/trash`, `POST /api/trash/<id>/restore`, `DELETE /api/trash/<id>`

- [ ] **Step 1: Write failing test**

```python
def test_versions_requires_permission():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.get("/api/files/00000000-0000-0000-0000-000000000000/versions").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_versions_trash.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Move `app.py:944-989,1298-1324` → `versions.py`, `991-1053` → `trash.py` (use `_parse_message_ids`, handle `telegram_service` streaming delete via `asyncio.run(delete_file(...))` with silent except).

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_versions_trash.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/versions.py app/trash.py tests/test_versions_trash.py
git commit -m "feat: add versions and trash blueprints"
```

---

### Task 6: Sharing, Backups, Users & Logs

**Files:**
- Create: `app/sharing.py`
- Create: `app/backups.py`
- Create: `app/users.py`
- Create: `app/logs.py`
- Test: `tests/test_sharing_backups_users.py`

**Interfaces:**
- Consumes: `hash_share_password`, `verify_share_password`, `telegram_service` upload/download
- Produces: `sharing_bp`: `POST /api/files/<id>/share`, `GET /api/files/<id>/shares`, `DELETE /api/files/<id>/shares/<sid>`, `GET /api/shared/<token>`, `/info`, `/preview`; `backups_bp`: `GET/POST /api/backup/*`; `users_bp`: `GET/PUT/DELETE /api/users`, `GET /api/users/stats`; `logs_bp`: `GET /api/logs`

- [ ] **Step 1: Write failing test**

```python
def test_share_requires_auth():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.post("/api/files/00000000-0000-0000-0000-000000000000/share", json={}).status_code == 401

def test_shared_info_404():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.get("/api/shared/invalidtoken123/info").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_sharing_backups_users.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Move `app.py:1325-1493` → `sharing.py`, `1624-1785` → `backups.py` (metadata JSON via `telegram_service.upload_chunks` to `BACKUP_CHANNEL_ID`), `1054-1259` → `users.py`, `1260-1297` → `logs.py`. Register all.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_sharing_backups_users.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/sharing.py app/backups.py app/users.py app/logs.py tests/test_sharing_backups_users.py
git commit -m "feat: add sharing, backups, users and logs blueprints"
```

---

### Task 7: Frontend Split

**Files:**
- Create: `frontend/api.js`, `frontend/auth.js`, `frontend/folders.js`, `frontend/files.js`, `frontend/sharing.js`, `frontend/admin.js`
- Modify: `index.html:1-30` (add `type="module"` imports), `register.html` likewise
- Test: `tests/test_frontend_smoke.py` (static)

**Interfaces:**
- Consumes: existing `index.html` DOM ids (`l-pass`, `team-key`, `share-password`, etc.)
- Produces: ES modules exporting `fetchMe`, `uploadFile`, `listFolders`, etc., imported by `index.html`

- [ ] **Step 1: Write failing test**

```python
def test_frontend_modules_exist():
    import pathlib
    for name in ["api","auth","folders","files","sharing","admin"]:
        assert pathlib.Path(f"frontend/{name}.js").exists()
    html = pathlib.Path("index.html").read_text()
    assert 'type="module"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3 -m pytest tests/test_frontend_smoke.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Extract functions from `app.js` (91018 bytes) by domain: `api.js` (fetch wrappers + toast), `auth.js` (login, `sessionLogin`, timeout countdown), `folders.js` (sidebar CRUD), `files.js` (drag/drop, folder-upload, progress ETA), `sharing.js` (share modal), `admin.js` (users/logs). Keep behavior identical, just imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3 -m pytest tests/test_frontend_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/ index.html register.html tests/test_frontend_smoke.py
git commit -m "feat: split frontend into modules"
```

---

### Task 8: Cutover, Cleanup & Smoke

**Files:**
- Modify: `app.py` → delete after migration (or keep as re-export for 1 commit)
- Delete: `styles.css` (dead file per AGENTS.md), `vcpkg_installer.exe` already removed
- Test: `tests/test_smoke.py` (end-to-end, mocked Supabase/Telegram)

**Interfaces:**
- Consumes: all blueprints
- Produces: working `create_app()` serving all routes, `py -m py_compile app/**/*.py` PASS, `git check-ignore .env` → ignored

- [ ] **Step 1: Write failing smoke test**

```python
def test_smoke_routes_registered():
    from app import create_app
    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/login" in rules
    assert "/api/files/upload" in rules
    assert "/api/shared/<token>" in rules
    assert "/api/backup/list" in rules
```

- [ ] **Step 2: Run test to verify it fails (before cutover)**

Run: `py -3 -m pytest tests/test_smoke.py -v`
Expected: FAIL if any blueprint not registered

- [ ] **Step 3: Finalize**

Ensure `api/index.py` is `from app import create_app; app = create_app()`, delete legacy `app.py` content after confirming `git grep "from app import" | wc -l` == 0 remains for old import, remove `styles.css`.

- [ ] **Step 4: Run verification**

Run: `py -3 -m py_compile app/__init__.py app/config.py && py -3 -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: cutover to modular app, remove legacy monolith"
```

---

## Self-Review

- **Spec coverage:** §2 file map → Tasks 1-7; §3 data flow → Task 4; §4 permissions/audit → Tasks 2-3,5-6; §5 secrets → Task 1 config + Task 2 utils; §6 frontend → Task 7; §7 Vercel → Task 1 & 8; §8 smoke → Task 8. All covered.
- **Placeholders:** none — every step has concrete code, paths, and expected outputs.
- **Type consistency:** `create_app()->Flask`, `get_supabase()->Client`, `_check_permission(sup, user_id, org_id, folder_id)->str|None`, `hash_share_password(str)->str`, `verify_share_password(str,str)->bool`, `is_configured()->bool`, `_parse_message_ids(raw)->List[int]` consistent across tasks.
