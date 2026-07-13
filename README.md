<div align="center">

# ⬡ TeamVault

**Private Encrypted File Storage · Backed by Telegram · Secured by Supabase**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0-000?style=flat&logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![Supabase](https://img.shields.io/badge/Supabase-Postgres%20%2B%20RLS-3FCF8E?style=flat&logo=supabase&logoColor=white)](https://supabase.com)
[![Telethon](https://img.shields.io/badge/Telethon-Userbot-26A5E4?style=flat&logo=telegram&logoColor=white)](https://docs.telethon.dev)
[![AES-256-GCM](https://img.shields.io/badge/Encryption-AES--256--GCM-FF6B6B?style=flat)](https://cryptography.io)
[![License](https://img.shields.io/badge/License-MIT-F5DEB3?style=flat)]()

---

</div>

## ✦ Overview

TeamVault lets your organisation store files securely without trusting any cloud provider. **Telegram channels** hold the encrypted ciphertext. **Supabase** manages metadata, permissions, and audit trails. Your browser encrypts everything with **AES-256-GCM** before it ever leaves your machine.

```
┌─────────────┐     AES-256-GCM     ┌──────────┐     Telethon     ┌──────────────────┐
│   Browser   │ ──────────────────→ │  Flask   │ ──────────────→ │  Telegram Channel │
│ (encrypts)  │                     │  API     │                  │  (ciphertext)     │
└─────────────┘                     └────┬─────┘                  └──────────────────┘
                                         │
                                         │ Supabase
                                         ▼
                                 ┌────────────────┐
                                 │   Supabase     │
                                 │  · metadata    │
                                 │  · permissions │
                                 │  · audit logs  │
                                 │  · RLS         │
                                 └────────────────┘
```

---

## ✦ Features

<div align="center">

| | Feature | Detail |
|---|---|---|
| 🔒 | **Zero-trust encryption** | AES-256-GCM client-side, server never sees plaintext |
| 📁 | **Multi-tenant** | Each Telegram channel = one isolated organisation |
| 🧩 | **Chunked uploads** | Files >2GB split into ~1.9GB chunks automatically |
| 📋 | **Version history** | 5 versions per file (FIFO), restore any version |
| 👥 | **Role-based access** | `master_admin` → `org_admin` → `read_write` → `read_only` |
| 📊 | **Folder-scoped permissions** | Granular access via `permissions` table |
| 🕐 | **Full audit trail** | Every mutation logged to `audit_logs` |
| 🚀 | **Upload progress** | Per-file ETA, speed, and overall progress |
| 📂 | **Folder upload** | Drag & drop or webkitdirectory support |
| 🗑 | **Soft delete + trash** | Restore or permanently destroy |

</div>

---

## ✦ Quick Start

### Prerequisites

```bash
pip install -r requirements.txt --break-system-packages
```

### Supabase Setup

1. Create a project at [supabase.com](https://supabase.com)
2. Open **SQL Editor** → paste & run [`supabase_schema.sql`](supabase_schema.sql)
3. Copy your **Project URL** and **`service_role` key** from Project Settings → API

### Telegram Setup

1. Go to [my.telegram.org/apps](https://my.telegram.org/apps)
2. Create an app → copy **`api_id`** and **`api_hash`**
3. Create a **private Telegram channel** → copy its chat ID (e.g. `-1001234567890`)

### Configure & Run

```bash
cp .env.example .env
# Fill in TELETHON_API_ID, TELETHON_API_HASH, SUPABASE_URL, SUPABASE_SERVICE_KEY

python3 app.py
```

Open **http://127.0.0.1:5000** — first run will prompt for Telegram phone + OTP
to create `session_name.session`.

---

## ✦ Role System

```
master_admin  ──→  Global access, all orgs
org_admin     ──→  Full control within one org
read_write    ──→  Upload, download, create folders
read_only     ──→  View and download only
```

| Action | master_admin | org_admin | read_write | read_only |
|---|---|---|---|---|
| View & download | ✓ | ✓ | ✓ | ✓ |
| Upload files | ✓ | ✓ | ✓ | — |
| Create folders | ✓ | ✓ | ✓ | — |
| Restore versions | ✓ | ✓ | ✓ | — |
| Soft delete | ✓ | ✓ | — | — |
| Manage trash | ✓ | ✓ | — | — |
| View audit logs | ✓ | ✓ | — | — |
| Manage users | ✓ | ✓ | — | — |
| Cross-org access | ✓ | — | — | — |

---

## ✦ API Reference

### Auth
```
POST /api/login                          Sign in
POST /api/logout                         Sign out
GET  /api/me                             Current user
```

### Files & Folders
```
GET    /api/folders                      List folders
POST   /api/folders                      Create folder
GET    /api/files?folder_id=             List files
POST   /api/files/upload                 Upload encrypted file
GET    /api/files/<uuid:id>/download     Download file
DELETE /api/files/<uuid:id>              Soft delete
GET    /api/files/<uuid:id>/versions     Version history
POST   /api/files/<uuid:id>/restore/<int:ver>  Restore version
```

### Organisation
```
POST /api/org/register                   Register new org
GET  /api/orgs                           List orgs
POST /api/orgs/<uuid:id>/approve         Approve org
POST /api/orgs/<uuid:id>/reject          Reject org
```

### Administration
```
GET    /api/users                        List users
POST   /api/users                        Create user
DELETE /api/users/<uuid:id>              Delete user
GET    /api/trash                        List trashed files
POST   /api/trash/<uuid:id>/restore     Restore from trash
DELETE /api/trash/<uuid:id>              Permanently delete
GET    /api/logs                         Activity log
GET    /api/versions/all                 All versions (org-wide)
```

---

## ✦ Encryption Flow

```
1. User enters team passphrase in the browser
2. PBKDF2 derives AES-256 key (200k iterations, SHA-256)
3. Random 12-byte IV generated
4. File encrypted with AES-256-GCM
5. IV prepended to ciphertext → sent to Flask API
6. Flask stores ciphertext in Telegram channel
7. On download: stream raw ciphertext → browser decrypts
```

> ⚠️ **Never send the passphrase to the server.** Share it with your team
> via a password manager or in person.

---

## ✦ Project Structure

```
├── app.py                    Flask API + Supabase queries
├── telegram_bot.py           Telethon chunked upload/download
├── supabase_schema.sql       Postgres schema + RLS + triggers
├── app.js / index.html       Dashboard frontend
├── register.js / register.html   Org registration flow
├── style.css / register.css  Styles
├── .env.example              Config template
├── requirements.txt          Python dependencies
├── AGENTS.md                 Agent instruction file
└── session_name.session      Telethon auth (gitignored)
```

---

## ✦ Development

```bash
python3 app.py                  # Start dev server on :5000
pip install -r requirements.txt --break-system-packages
pkill -f "python3 app.py"       # Kill stale processes
```

Flask debug mode auto-reloads on file changes. Session persistence is handled
by `.secret_key` (auto-generated on first run).

---

<div align="center">

**Built with** ⬡ **by the TeamVault project**

[Report Bug](https://github.com/sk143sathyabusiness/telegram-as-storage/issues) · [Request Feature](https://github.com/sk143sathyabusiness/telegram-as-storage/issues)

</div>
