# AGENTS.md — Telegram-as-Storage

## Project

Multi-tenant file storage using Telegram channels as the backend.
Each Telegram channel = one Organisation. Metadata lives in Supabase.
Files are AES-256-GCM encrypted client-side — Telegram only sees ciphertext.

## Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python + Flask (`app.py`) |
| **Telegram client** | Telethon userbot (`telegram_bot.py`, session-based) |
| **Metadata DB** | Supabase (Postgres + RLS) — schema at `supabase_schema.sql` |
| **Encryption** | `cryptography` (AES-256-GCM), key derived client-side from team passphrase |
| **Config** | `python-dotenv` (`.env`, see `.env.example`) |
| **Frontend** | Plain JS/HTML (`app.js`, `register.js`, `index.html`, `register.html`) |

## Core Rules

0. **ZERO LOCAL STORAGE.** No file bytes are ever written to disk — not to
   `uploads/`, not to temp files, not to a cache. All upload/download/chunk/
   encrypt/decrypt operations use `io.BytesIO` in-memory buffers. Telegram
   channels are the only persistent file store. The single exception is
   `session_name.session` (Telethon's auth credential).
1. Never store unencrypted file bytes or raw passphrase server-side.
2. Files >2GB: split into chunks (~1.9GB) in memory, each chunk = one Telegram
   message, `message_ids[]` in `file_versions.message_ids` (jsonb).
3. Every file modify = new row in `file_versions`. Max 5 versions per file (FIFO,
   enforced by Postgres trigger `trg_enforce_version_limit`, in schema).
4. Every mutating action writes to `audit_logs` with actor_id, actor_role, org_id.
5. Permission checks are folder-scoped — check `permissions` table before any
   file/folder operation, never trust client role alone.
6. Daily backup only touches folders where `folders.is_essential = true`.
7. RLS is the source of truth for org isolation — backend passes the right
   JWT claims (`role`, `org_id`), not just filters in app code.

## Roles

`master_admin` (global, all orgs) > `org_admin` (one org) > `read_write` > `read_only`.

## Key files

| File | Role |
|------|------|
| `app.py` | Flask API — routes, queries, auth |
| `telegram_bot.py` | Telethon upload/download/chunk helpers |
| `supabase_schema.sql` | DB schema (source of truth — update this for any table/column change) |
| `app.js` / `index.html` | Dashboard frontend |
| `register.js` / `register.html` | Organisation registration flow |
| `style.css` / `register.css` | Styles |

## Run the server

```bash
pip install -r requirements.txt --break-system-packages  # Debian
cp .env.example .env     # fill in Supabase + Telegram credentials
python3 app.py           # http://127.0.0.1:5000
```

Flask debug mode auto-reloads. Sessions persist via `.secret_key` (auto-generated).

## Upload flow

1. Client encrypts file with AES-256-GCM (PBKDF2-derived key from passphrase)
2. POST `/api/files/upload` with encrypted blob
3. `_store_file_blob()` sends to org's Telegram channel via Telethon (chunked if >2GB)
4. Storage key: `file_versions.message_ids` (jsonb array of Telegram message IDs)

## Download flow

1. GET `/api/files/<id>/download` → `_load_file_blob()` fetches chunks from Telegram
2. Flask streams raw encrypted bytes → client decrypts with passphrase

## Conventions

- All Supabase calls through a single client module — no scattered `create_client()`.
- Chunk size constant in one config place, not hardcoded.
- New table/column: update `supabase_schema.sql` only (single source of truth).

## Gotchas

- **`cryptg` build failure**: `pip install cryptg` often fails on Debian. Optional —
  Telethon falls back to pure-Python MTProto (slower but works).
- **`.secret_key`**: Auto-generated on first run, persists in file. Delete to force
  regeneration (invalidates all sessions).
- **`session_name.session`**: Telethon auth session — required, keep safe, never commit.
- **`styles.css`**: Dead file from older version, not linked in `index.html`.

## Migration from v0 (SQLite + Bot API → target)

Current codebase is mid-migration. If you see SQLite files, Bot API calls, or
`uploads/`/`backups/` folders, they are v0 artifacts not yet removed. The target
is the architecture documented here. `supabase_schema.sql` is the single source
of truth for the target DB schema.

## Commands

```bash
python3 app.py                          # dev server
pip install -r requirements.txt --break-system-packages
pkill -f "python3 app.py"               # kill stale debug processes on port 5000
```
