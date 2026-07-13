# TeamVault — Encrypted File Storage via Telegram

Multi-tenant file storage using **Telegram channels** as the encrypted backend.
Each Telegram channel = one Organisation. Metadata lives in **Supabase** (Postgres + RLS).
Files are AES-256-GCM encrypted client-side — Telegram only ever sees ciphertext.

## Architecture

```
Browser ──AES-256-GCM──→ Flask API ──Telethon──→ Telegram Channel
                              │
                              └──→ Supabase (metadata, permissions, audit logs)
```

- **No local disk storage** — all file bytes flow through in-memory buffers (`io.BytesIO`)
- **Files >2GB** are split into ~1.9GB chunks, each stored as one Telegram message
- **5 versions max** per file (FIFO, enforced by Postgres trigger)
- **Folder-scoped permissions** via `permissions` table
- **Full audit trail** — every mutation writes to `audit_logs`

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python + Flask (`app.py`) |
| Telegram | Telethon userbot (`telegram_bot.py`) |
| Metadata | Supabase (Postgres + RLS) |
| Encryption | `cryptography` (AES-256-GCM, PBKDF2) |
| Frontend | Plain JS/HTML (`app.js`, `index.html`) |
| Config | `python-dotenv` (`.env`) |

## Setup

### 1. Prerequisites

```bash
pip install -r requirements.txt --break-system-packages  # Debian
```

### 2. Supabase

Create a project at [supabase.com](https://supabase.com), then run `supabase_schema.sql`
in the SQL editor. Copy your project URL and `service_role` key.

### 3. Telegram

Get `api_id` and `api_hash` from [my.telegram.org/apps](https://my.telegram.org/apps).
Create a private channel and note its chat ID (e.g. `-1001234567890`).

### 4. Configure

```bash
cp .env.example .env
```

Fill in `.env`:
```env
TELETHON_API_ID=your_api_id
TELETHON_API_HASH=your_api_hash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your_service_role_key
```

### 5. Start

```bash
python3 app.py
```

Open `http://127.0.0.1:5000`. Register your organisation — the first user
becomes `org_admin`. On first run, Telethon will prompt for your phone number
and OTP to create `session_name.session`.

## Roles

| Role | Scope | Permissions |
|------|-------|-------------|
| `master_admin` | Global (all orgs) | Everything |
| `org_admin` | One org | Full control within org |
| `read_write` | One org | Upload, download, create folders |
| `read_only` | One org | View and download only |

## API

### Auth
- `POST /api/login` — sign in
- `POST /api/logout` — sign out
- `GET /api/me` — current user

### Files
- `GET /api/files?folder_id=` — list files
- `POST /api/files/upload` — upload (encrypted blob)
- `GET /api/files/<uuid:file_id>/download` — download
- `DELETE /api/files/<uuid:file_id>` — soft delete
- `GET /api/files/<uuid:file_id>/versions` — version history
- `POST /api/files/<uuid:file_id>/restore/<int:version_no>` — restore version

### Folders
- `GET /api/folders` — list folders
- `POST /api/folders` — create folder

### Organisation
- `POST /api/org/register` — register new org
- `GET /api/orgs` — list orgs (master_admin only)
- `POST /api/orgs/<uuid:org_id>/approve` — approve org
- `POST /api/orgs/<uuid:org_id>/reject` — reject org

### Admin
- `GET /api/users` — list users
- `POST /api/users` — create user
- `DELETE /api/users/<uuid:user_id>` — remove user
- `GET /api/trash` — list trashed files
- `POST /api/trash/<uuid:file_id>/restore` — restore from trash
- `DELETE /api/trash/<uuid:file_id>` — permanently delete
- `GET /api/logs` — activity log
- `GET /api/versions/all` — all versions across org

## Encryption

Files are encrypted in the browser before upload:
- Key derived from team passphrase via PBKDF2 (200k iterations, SHA-256)
- AES-256-GCM with random 12-byte IV
- IV prepended to ciphertext; server never sees plaintext or key
- The passphrase must be shared with your team out-of-band

## Key files

| File | Purpose |
|------|---------|
| `app.py` | Flask API, Supabase queries, auth |
| `telegram_bot.py` | Telethon chunked upload/download |
| `supabase_schema.sql` | Postgres schema + RLS policies + triggers |
| `app.js` / `index.html` | Dashboard |
| `register.js` / `register.html` | Org registration |
| `style.css` / `register.css` | Styles |
| `session_name.session` | Telethon auth session (gitignored) |
| `.secret_key` | Flask session key (auto-generated, gitignored) |

## Commands

```bash
python3 app.py                  # dev server (port 5000)
pip install -r requirements.txt --break-system-packages
pkill -f "python3 app.py"       # kill stale processes
```
