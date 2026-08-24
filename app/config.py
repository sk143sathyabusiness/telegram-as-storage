"""
app.config — centralised configuration (Task 1).

Single source for chunk size, session timeout, Telegram/Supabase keys,
and Flask secret resolution. Mirrors the logic that previously lived
inline in app.py:23-115 and .env.example defaults.
"""

import os
import secrets

from dotenv import load_dotenv

load_dotenv()

# ── Chunking ─────────────────────────────────────────────────────────────
CHUNK_SIZE_BYTES = int(os.getenv("CHUNK_SIZE_BYTES", "1900000000"))

# ── Session timeout ──────────────────────────────────────────────────────
SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "43200"))  # 12h

# ── Backup channel ───────────────────────────────────────────────────────
# 0 means "not configured" — matches app.py:113 original default
try:
    BACKUP_CHANNEL_ID = int(os.getenv("BACKUP_CHANNEL_ID", "0") or 0)
except (ValueError, TypeError):
    BACKUP_CHANNEL_ID = 0

# ── Upload limit ─────────────────────────────────────────────────────────
# Flask's MAX_CONTENT_LENGTH (bytes). App.py default 10 GiB.
try:
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_SIZE", str(10 * 1024 * 1024 * 1024)))
except (ValueError, TypeError):
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024 * 1024

# ── Supabase ─────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

# ── Flask secret key ────────────────────────────────────────────────────
# Resolution order (matches app.py:81-107):
#   1. FLASK_SECRET_KEY env (canonical) or SECRET_KEY alias
#   2. .secret_key file in project root (auto-generated fallback)
#   3. generate ephemeral key (and try to persist to .secret_key)
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET_KEY_FILE = os.path.join(_project_root, ".secret_key")

_raw_secret = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
_WEAK_MARKERS = ("change_me", "generate_a", "choose_a", "Sathyamostpowerfuldeveloper")

if _raw_secret:
    if len(_raw_secret) < 32 or any(m in _raw_secret for m in _WEAK_MARKERS):
        print("[WARN] FLASK_SECRET_KEY looks weak or reused — generate a long random value (secrets.token_hex(32)) and keep it unique.")
    SECRET_KEY = _raw_secret
else:
    try:
        SECRET_KEY = open(SECRET_KEY_FILE, "r").read().strip()
        if len(SECRET_KEY) < 32:
            raise ValueError("weak file secret")
    except (OSError, IOError, ValueError):
        key = secrets.token_hex(32)
        try:
            with open(SECRET_KEY_FILE, "w") as _f:
                _f.write(key)
            os.chmod(SECRET_KEY_FILE, 0o600)
        except OSError:
            pass
        SECRET_KEY = key
        print("[INFO] Generated new .secret_key (store securely; overrides FLASK_SECRET_KEY if file exists).")

# ── Share-link password salt ────────────────────────────────────────────
# PBKDF2 salt for hash_share_password. Falls back to built-in static value
# when ENCRYPTION_VERIFIER_SALT is not set (legacy links). We also keep the
# static value exported so verify_share_password can try both.
_SHARE_PW_SALT_STATIC = b"teamvault-share-v1"
_raw_share_salt = (os.getenv("ENCRYPTION_VERIFIER_SALT") or "").strip()
if _raw_share_salt:
    if len(_raw_share_salt) < 16:
        print("[WARN] ENCRYPTION_VERIFIER_SALT is too short — use at least 16 random characters and do not reuse FLASK_SECRET_KEY.")
    _SHARE_PW_SALT = _raw_share_salt.encode()
    if _raw_share_salt == (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or ""):
        print("[WARN] ENCRYPTION_VERIFIER_SALT == FLASK_SECRET_KEY — use distinct random values.")
else:
    _SHARE_PW_SALT = _SHARE_PW_SALT_STATIC
    print("[INFO] ENCRYPTION_VERIFIER_SALT not set — using built-in salt. Set a unique value in .env for stronger isolation.")
