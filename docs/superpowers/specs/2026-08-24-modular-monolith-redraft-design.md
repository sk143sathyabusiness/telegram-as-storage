# Design: Modular Monolith Redraft — Telegram-as-Storage

**Date:** 2026-08-24  
**Approach chosen:** Approach 1 — Modular Monolith (keep Flask + Telethon + Supabase, same features, cleaner boundaries)  
**Context:** Rewrite from scratch to simplify without feature loss. Current codebase is mid-migration v0→target, single 1859-line `app.py`, 91k `app.js`, sensitive-data fixes already pushed (`74833ef`). Core rules (AGENTS.md) remain: Zero Local Storage, AES-256-GCM client-side, Telegram channels = orgs, Supabase metadata, RLS isolation.

## 1. Goals / Non-Goals

**Goals:**
- Same product behavior 1:1 (no feature loss): multi-tenant orgs, 4 roles (`master_admin` > `org_admin` > `read_write` > `read_only`), folder-scoped permissions, chunked >2GB (1.9GB chunks, `message_ids[]` jsonb), 5-version FIFO (`trg_enforce_version_limit`), trash + permanent delete, share links + password (PBKDF2), email, backups (essential folders), audit logs, RLS.
- Simplify mental model: each file does one job, <250 lines, well-named boundaries.
- Bake secrets handling into architecture (not patch): single config validation, no hardcoded fallbacks.

**Non-Goals:**
- No stack change (stay Flask, not FastAPI), no infra split (API + worker), no UI redesign.
- No history rewrite in this spec (documented in `SECURITY.md`, executed separately).

## 2. Architecture & File Map

```
telegram-as-storage/
├── app/
│   ├── __init__.py          # create_app(), register blueprints, security headers, error handlers
│   ├── config.py            # all env validation + weak-value warnings
│   ├── supabase_client.py   # single get_supabase(), check_supabase()
│   ├── security.py          # cookies, CSP, rate-limit, session-timeout
│   ├── auth.py              # POST /api/login, /api/logout, GET /api/me
│   ├── orgs.py              # POST /api/org/register, GET /api/orgs, approve/reject
│   ├── folders.py           # GET/POST /api/folders, DELETE /api/folders/<id>, permissions/*
│   ├── files.py             # GET /api/files, search, POST upload, GET download/preview, _store/_load helpers
│   ├── versions.py          # GET /api/files/<id>/versions, POST restore, GET /api/versions/all
│   ├── trash.py             # GET /api/trash, POST restore, DELETE hard-delete
│   ├── sharing.py           # POST /api/files/<id>/share, shares, unshare, GET /api/shared/<token>/*
│   ├── backups.py           # GET/POST /api/backup/*
│   ├── users.py             # GET/POST /api/users, PUT/DELETE /api/users/<id>, stats, permissions
│   ├── logs.py              # GET /api/logs
│   └── utils.py             # _check_permission, _require_active_org, _resolve_folder_name, _parse_message_ids, fmt_size, hash/verify_share_password, log_action
├── telegram_service.py      # (renamed from telegram_bot.py) upload_chunks[_streaming], download_chunks[_streaming], delete_file, verify_bytes, backup_essential_folder
├── supabase_schema.sql      # single source of truth (unchanged)
├── api/index.py             # thin Vercel entry: from app import create_app; app = create_app()
├── frontend/                # split from monolithic app.js
│   ├── api.js               # fetch wrappers
│   ├── auth.js              # login, session-timeout countdown
│   ├── folders.js
│   ├── files.js             # upload progress/ETA, folder-upload
│   ├── sharing.js
│   └── admin.js             # users, logs
├── index.html / register.html / shared.html (structure preserved)
├── style.css / register.css
├── .env.example (placeholders only) / .env (gitignored)
└── docs/superpowers/specs/  # this file
```

**Dependency rule:** `app/*.py` → `config`, `supabase_client`, `utils`, `telegram_service` only. No circular imports. No file >300 lines.

## 3. Data Flow (Preserved)

**Upload:** Browser derives key PBKDF2(200k, SHA-256, passphrase) → generates 12-byte IV → AES-256-GCM encrypt → prepends IV → `POST /api/files/upload` (encrypted blob only) → `files._store_file_blob()` reads `request.files['file'].stream` in `CHUNK_SIZE_BYTES` slices → `telegram_service.upload_chunks_streaming(stream, filename, chat_id)` → returns ordered `message_ids[]` → inserts `files` + `file_versions` (jsonb). Never writes to disk (`io.BytesIO` only).

**Download:** `GET /api/files/<id>/download` → permission check → load `message_ids[]` → `telegram_service.download_chunks_streaming(chat_id, ids)` yields chunks → `Response(stream_with_context(generate()))` → browser decrypts with passphrase.

**Chunking:** Constant `CHUNK_SIZE_BYTES` defined in `config.py` (default `1900000000`), used by both modules.

## 4. Permissions & Audit (Preserved)

- Helper `utils._check_permission(sup, user_id, org_id, folder_id)` is the gate for every folder/file mutation. `master_admin` → full, `org_admin` → org-wide, `read_write`/`read_only` → folder-scoped via `permissions` table. Never trust `session["role"]` alone.
- `utils.log_action()` writes `audit_logs(org_id, actor_id, actor_role, action, target_type, target_id, details)` on every mutation.
- RLS remains source of truth: `supabase_client` calls `set_app_context(uid, urole)` via RPC; backend filters by `org_id` as well.
- Version limit: Postgres trigger `trg_enforce_version_limit` (max 5 FIFO) stays in `supabase_schema.sql`.

## 5. Secrets & Security (Baked In)

- `config.py` is the only place that reads env: `FLASK_SECRET_KEY` (canonical, `SECRET_KEY` legacy alias), `ENCRYPTION_VERIFIER_SALT`, `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`, `TG_API_ID`/`TG_API_HASH`/`TG_SESSION_NAME`/`TG_BOT_TOKEN`, `SMTP_*`, `BACKUP_CHANNEL_ID`, `MAX_UPLOAD_SIZE`, `SESSION_TIMEOUT_SECONDS`. Validates: `FLASK_SECRET_KEY` length ≥32, warns on markers `change_me`/`generate_a`/`choose_a`; warns if `ENCRYPTION_VERIFIER_SALT == FLASK_SECRET_KEY` or too short.
- Share salt: `utils._SHARE_PW_SALT` derived from `ENCRYPTION_VERIFIER_SALT` env (fallback static `teamvault-share-v1`). `hash_share_password` uses current salt; `verify_share_password` tries current then static then legacy plain-SHA256.
- `telegram_service.py` lazy-parses `TG_API_ID` (no `int(os.environ["…"])` at import), `is_configured()` gate, `_make_client()` raises clean error.
- `.gitignore` keeps `!.env.example` tracked, ignores `.env`, `.secret_key`, `*.session`, `teamvault.db`, `uploads/`, `backups/`, `*.exe`. `app/__init__.py:_BLOCKED_STATIC` 404s `/.env`, `/app.py`, etc. `500` handler generic, CSP/`X-Content-Type-Options`/`X-Frame-Options` set.

## 6. Frontend Split

- Keep `index.html`/`register.html` structure, add `type="module"` imports. Split 91k `app.js` into `frontend/*.js` by domain. Each module imports `api.js` for fetches. No visual change, just boundaries and smaller testable units. `register.js` similarly split.

## 7. Deployment

- `api/index.py`: `sys.path` insert + `from app import create_app; app = create_app()` (3 lines). `vercel.json` unchanged. Flask `create_app()` factory enables testing.

## 8. Testing & Verification

- Static: `py -m py_compile app/**/*.py`, `git check-ignore -v .env` → ignored, `grep -R 23957297 .env.example` → empty.
- Smoke sequence: org register → login → create folder → set permission → encrypt-upload → list → download → version restore → trash → restore → share link (password) → email — all via Supabase + Telegram streaming, no local files.
- Unit: `utils._check_permission` and `hash/verify_share_password` with static+env salts.

## 9. Risks / Mitigations

- Over-splitting → keep `files.py` + `versions.py` + `trash.py` separate despite overlap (clear ownership). 
- Duplicated permission checks → centralize in `utils`.
- Telethon session file remains the sole local artifact (`*.session` gitignored, `600`).

## 10. Out of Scope

- Rewriting git history (tracked in `SECURITY.md`), UI redesign, FastAPI migration.
