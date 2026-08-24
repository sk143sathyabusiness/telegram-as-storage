"""
app.utils — shared helpers (Task 2).

Moved from app.py:181-285 (log_action, fmt_size, permission helpers,
message-id parser, share-link hashing). Keeps zero-local-storage rules
and dual-salt verify behaviour (design §5).
"""

import hashlib
import json
import secrets

from flask import jsonify

from app.config import _SHARE_PW_SALT, _SHARE_PW_SALT_STATIC
from app.supabase_client import get_supabase
from app.security import current_user


# ── Helpers pulled from app.security to avoid circular imports ──────────
# fmt_size is duplicated in app.security for the 413 handler; we expose
# the canonical copy here for callers that import from utils (brief §2).
# Both implementations are identical (app.py:199-204).

def fmt_size(n):
    if n is None:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


# ── Permission helpers (app.py:206-246) ──────────────────────────────────

def _check_permission(sup, user_id, org_id, folder_id=None):
    """Check user's effective permission for a folder. Returns level or None."""
    user_result = sup.table("users").select("role").eq("id", user_id).maybe_single().execute()
    if not user_result or not user_result.data:
        return None
    role = user_result.data["role"]
    if role == "master_admin":
        return "org_admin"  # full access
    if role == "org_admin":
        return "org_admin"
    if not folder_id:
        return role  # org-wide default
    perm = sup.table("permissions").select("permission_level").eq("org_id", org_id).eq("user_id", user_id).eq("folder_id", folder_id).maybe_single().execute()
    if perm and perm.data:
        return perm.data["permission_level"]
    return role  # fall back to user's org role


def _require_active_org(sup, org_id):
    """Return error response if org is not active, or None if OK."""
    org = sup.table("organizations").select("status").eq("id", org_id).maybe_single().execute()
    if not org or not org.data or org.data["status"] != "active":
        return jsonify({"error": "Organisation is not active"}), 403
    return None


def _resolve_folder_name(sup, folder_id):
    """Resolve a folder UUID to its name for audit log readability."""
    if not folder_id:
        return "Root"
    f = sup.table("folders").select("name").eq("id", folder_id).maybe_single().execute()
    return f.data["name"] if f and f.data else str(folder_id)


def _parse_message_ids(raw):
    """Safely parse message_ids from Supabase — handles both native list and legacy JSON string."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        return json.loads(raw)
    return []


# ── Share-link password hashing (app.py:252-279) ─────────────────────────

def hash_share_password(password: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _SHARE_PW_SALT, 100_000)
    return "pbkdf2$" + dk.hex()


def verify_share_password(password: str, stored: str) -> bool:
    if not stored or not stored.startswith("pbkdf2$"):
        # Legacy plain-SHA256 fallback (no salt) for old rows.
        return stored == hashlib.sha256(password.encode()).hexdigest()
    expected = stored.split("$", 1)[1]
    # Try current deployment salt first, then static fallback for links created before env was set
    for _salt in (_SHARE_PW_SALT, _SHARE_PW_SALT_STATIC):
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), _salt, 100_000)
        if secrets.compare_digest(dk.hex(), expected):
            return True
    return False


# ── Audit log (app.py:181-197) ───────────────────────────────────────────

def log_action(action, target=None, detail=None, target_type=None, target_id=None):
    sup = get_supabase()
    user = current_user()
    details = {}
    if target:
        details["target"] = target
    if detail:
        details["detail"] = detail
    sup.table("audit_logs").insert({
        "org_id": user["org_id"] if user else None,
        "actor_id": user["id"] if user else None,
        "actor_role": user["role"] if user else "system",
        "action": action,
        "target_type": target_type,
        "target_id": str(target_id) if target_id else None,
        "details": details if details else None,
    }).execute()
