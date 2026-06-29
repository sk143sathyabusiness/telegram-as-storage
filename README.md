# TeamVault — Setup Guide

Private encrypted file storage for small teams.  
**Storage backend: self-hosted MinIO** (replaces Telegram).

---

## What changed from the Telegram version

| | Telegram version | MinIO version |
|---|---|---|
| Storage | Telegram bot API | Self-hosted MinIO (S3-compatible) |
| Max file size | 4 GB (chunked across messages) | 4 GB (single object, no chunking needed) |
| Chunking logic | Required | Removed — MinIO handles any size natively |
| Setup | Bot token + chat ID | MinIO endpoint + access key + secret key |
| DB schema | Unchanged | Unchanged — `telegram_file_id` column now stores MinIO object key |

Everything else (auth, roles, versioning, encryption, logs) is identical.

---

## 1. Run MinIO

The fastest way is Docker:

```bash
docker run -d \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=changeme \
  -v /data/minio:/data \
  --name minio \
  quay.io/minio/minio server /data --console-address ":9001"
```

- **File API**: `http://your-server:9000`  
- **Admin console**: `http://your-server:9001`

Then open the console, sign in, and create a **private bucket** named `teamvault`  
(or whatever you set `TEAMVAULT_MINIO_BUCKET` to).

---

## 2. Configure

```bash
export TEAMVAULT_MINIO_ENDPOINT="your-server:9000"
export TEAMVAULT_MINIO_ACCESS_KEY="minioadmin"
export TEAMVAULT_MINIO_SECRET_KEY="changeme"
export TEAMVAULT_MINIO_BUCKET="teamvault"
export TEAMVAULT_MINIO_SECURE="false"   # "true" if MinIO is behind HTTPS/TLS
```

---

## 3. Install & run

```bash
pip install flask boto3 cryptography
python app.py
```

Open `http://localhost:5000` — Flask serves `index.html` directly.

---

## 4. Bootstrap the first admin (one-time)

```bash
curl -X POST http://localhost:5000/api/bootstrap \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"choose-a-strong-password"}'
```

Log in, then use the **Team** panel to create accounts for teammates  
with roles: `admin`, `read_write`, or `read_only`.

---

## 5. The team encryption passphrase

This is separate from login passwords. It's used by the browser to  
**AES-256-GCM encrypt files before they leave your machine** — MinIO only  
ever stores ciphertext. Share it with your team via a password manager  
or in person, not over chat. Anyone without it cannot read downloaded files  
even if they have direct MinIO access.

---

## What's in the UI

| Feature | Who can use it |
|---|---|
| Upload files (encrypted) | admin, read_write |
| Download & decrypt files | all roles |
| Per-file upload progress + ETA | all roles |
| Folder tree navigation | all roles |
| Version history + restore | admin, read_write |
| Trash (soft-delete → restore or destroy) | admin |
| Activity log (all actions) | admin |
| Team member management | admin |

---

## Role permissions

| Action | admin | read_write | read_only |
|---|---|---|---|
| View & download files | ✓ | ✓ | ✓ |
| Upload files | ✓ | ✓ | — |
| Create folders | ✓ | ✓ | — |
| Restore previous version | ✓ | ✓ | — |
| Delete files (to trash) | ✓ | — | — |
| Manage trash (restore/destroy) | ✓ | — | — |
| View activity log | ✓ | — | — |
| Manage team members | ✓ | — | — |

---

## For production

- **HTTPS**: run Flask behind nginx or Caddy, or deploy to a host that provides TLS.  
  Set `TEAMVAULT_MINIO_SECURE=true` and point MinIO behind the same reverse proxy.
- **Daily DB backup**: `teamvault.db` holds all metadata/permissions — copy it off-box  
  (MinIO already durably stores file bytes). A simple cron:
  ```bash
  0 3 * * * cp /path/to/teamvault.db /backup/teamvault-$(date +\%F).db
  ```
- **MinIO backups**: enable MinIO's built-in replication or use `mc mirror` to sync  
  the bucket to a second location.
- **Rate limits**: none — MinIO is your own infrastructure.
