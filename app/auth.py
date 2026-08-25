"""
app.auth — authentication blueprint (Task 2).

Moved from app.py:281-414 (login rate-limit, POST /api/login,
GET /api/me, POST /api/logout). Uses app.security.login_required
and app.supabase_client.get_supabase.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash

from app.supabase_client import get_supabase
from app.security import login_required

auth_bp = Blueprint("auth", __name__)

# ── Login brute-force protection (app.py:281-313) ─────────────────────────
# In-memory rate limiter: per-IP + per-username, with exponential lockout.
_LOGIN_ATTEMPTS = {}        # key -> {"count": int, "until": float, "first": float}
_LOGIN_WINDOW = 60          # seconds
_LOGIN_MAX = 5              # attempts before lockout
_LOGIN_LOCKOUT = 300        # seconds of lockout


def _login_rate_limit_allowed(key: str) -> bool:
    now = datetime.utcnow().timestamp()
    rec = _LOGIN_ATTEMPTS.get(key)
    if not rec:
        return True
    if now < rec["until"]:
        return False
    if now - rec.get("first", now) > _LOGIN_WINDOW:
        # window expired, reset
        _LOGIN_ATTEMPTS.pop(key, None)
        return True
    return rec["count"] < _LOGIN_MAX


def _login_rate_limit_register_failure(key: str):
    now = datetime.utcnow().timestamp()
    rec = _LOGIN_ATTEMPTS.get(key)
    if not rec or now - rec.get("first", now) > _LOGIN_WINDOW:
        _LOGIN_ATTEMPTS[key] = {"count": 1, "first": now, "until": 0}
        return
    rec["count"] += 1
    if rec["count"] >= _LOGIN_MAX:
        rec["until"] = now + _LOGIN_LOCKOUT


def _login_rate_limit_register_success(key: str):
    _LOGIN_ATTEMPTS.pop(key, None)


# ── Routes ────────────────────────────────────────────────────────────────

@auth_bp.route("/api/me", methods=["GET"])
@login_required
def api_me():
    from app.security import current_user
    user = current_user()
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"], "org_id": user["org_id"]})


@auth_bp.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    username = data.get("username", "").strip()
    password = data.get("password", "")
    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    # Brute-force protection (keyed by IP + username).
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    rl_key = f"{client_ip}|{username}"
    if not _login_rate_limit_allowed(rl_key):
        return jsonify({"error": "Too many attempts. Please try again later."}), 429

    sup = get_supabase()
    result = sup.table("users").select("*").eq("username", username).execute()
    if not result.data:
        _login_rate_limit_register_failure(rl_key)
        return jsonify({"error": "Invalid credentials"}), 401
    user = result.data[0]
    if not check_password_hash(user["password_hash"], password):
        _login_rate_limit_register_failure(rl_key)
        return jsonify({"error": "Invalid credentials"}), 401
    _login_rate_limit_register_success(rl_key)

    # Block logins for orgs that are not active — master_admin is global (org_id NULL) and bypasses
    if user["role"] != "master_admin" and user["org_id"]:
        org = sup.table("organizations").select("status").eq("id", user["org_id"]).maybe_single().execute()
        if not org or not org.data or org.data["status"] != "active":
            return jsonify({"error": "Account is not active. Contact an administrator."}), 403

    session["user_id"] = user["id"]
    session["org_id"] = user["org_id"]
    session["role"] = user["role"]
    session["username"] = user["username"]
    session["_last_activity"] = datetime.utcnow().timestamp()
    session.permanent = True
    print(f"[AUTH] Login: user='{user['username']}' role={user['role']} org_id={user['org_id']}")
    # Write audit log
    sup.table("audit_logs").insert({
        "org_id": user["org_id"],
        "actor_id": user["id"],
        "actor_role": user["role"],
        "action": "login",
    }).execute()
    return jsonify({"id": user["id"], "username": user["username"], "role": user["role"], "org_id": user["org_id"]})


@auth_bp.route("/api/logout", methods=["GET", "POST"])
def api_logout():
    session.clear()
    # Explicitly expire the session cookie — Vercel edge needs multiple variants
    from flask import current_app, make_response
    resp = make_response(jsonify({"ok": True}))
    # Prevent caching of logout response (bfcache, CDN)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    # Canonical delete (matches register_security)
    secure = current_app.config.get("SESSION_COOKIE_SECURE", False)
    name = current_app.config.get("SESSION_COOKIE_NAME", "session")
    path = current_app.config.get("SESSION_COOKIE_PATH", "/")
    # Try all combinations so browser deletes regardless of original Secure/SameSite
    for sec in (secure, False):
        for same in ("Lax", "None", None):
            try:
                resp.delete_cookie(name, path=path, secure=sec, httponly=True, samesite=same)
            except Exception:
                pass
            try:
                resp.delete_cookie(name, path="/", secure=sec, httponly=True, samesite=same)
            except Exception:
                pass
    # Max-age 0 fallback
    resp.set_cookie(name, "", expires=0, max_age=0, path="/", secure=False, httponly=True, samesite="Lax")
    resp.set_cookie(name, "", expires=0, max_age=0, path=path, secure=secure, httponly=True, samesite="Lax")
    return resp
