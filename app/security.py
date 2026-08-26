"""
app.security — security headers, session-cookie hardening, error handlers,
UUID converter, and idle-session timeout (Task 1).

Logic copied verbatim from app.py:23-72,133-161 and split into
register_security(app) for the factory.
"""

import os
import uuid as uuid_lib
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, request, session
from werkzeug.routing import BaseConverter

from app.config import SESSION_TIMEOUT_SECONDS


# ── Helpers for error responses ─────────────────────────────────────────

def fmt_size(n):
    if n is None:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class UUIDConverter(BaseConverter):
    def to_python(self, value):
        return uuid_lib.UUID(value)

    def to_url(self, value):
        return str(value)


# ── Auth helpers (moved from app.py:168-180) ─────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped


def current_user():
    if "user_id" not in session:
        return None
    # Master can act as org admin via session["act_as_org_id"]
    org_id = session.get("org_id")
    if session.get("role") == "master_admin" and session.get("act_as_org_id"):
        org_id = session.get("act_as_org_id")
    return {
        "id": session["user_id"],
        "org_id": org_id,
        "role": session["role"],
        "username": session.get("username"),
        "real_org_id": session.get("org_id"),
        "act_as_org_id": session.get("act_as_org_id"),
    }


# ── Security headers (after_request) ────────────────────────────────────

def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: blob: https://vercel.live https://*.vercel.live; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://vercel.live https://*.vercel.live; "
        "font-src 'self' https://fonts.gstatic.com https://vercel.live https://*.vercel.live data:; "
        "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://vercel.live https://*.vercel.live; "
        "script-src-elem 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://vercel.live https://*.vercel.live; "
        "connect-src 'self' https://vercel.live https://*.vercel.live wss://vercel.live wss://*.vercel.live https://*.supabase.co wss://*.supabase.co https://vitals.vercel-analytics.com; "
        "frame-src 'self' blob: https://vercel.live https://*.vercel.live; "
        "media-src 'self' blob: https://vercel.live https://*.vercel.live; "
        "object-src 'self' blob:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    # Drop the verbose Flask Server header.
    resp.headers.setdefault("Server", "TeamVault")
    return resp


# ── Idle session timeout (before_request) ────────────────────────────────

def _enforce_session_timeout():
    # Only act on requests that already carry a logged-in session.
    if "user_id" not in session:
        return
    # Whitelist endpoints that must work even on a (possibly) expired session.
    rule = request.endpoint or ""
    # Support both bare names (legacy app.py) and blueprint-prefixed names (auth.api_login)
    bare = rule.split(".")[-1] if rule else ""
    if rule in (
        "api_login",
        "api_logout",
        "static_files",
        "index",
        "register_page",
        "shared_page",
        "favicon",
        "api_shared_download",
        "api_shared_info",
        "api_shared_preview",
        "auth.api_login",
        "auth.api_logout",
    ) or bare in (
        "api_login",
        "api_logout",
        "static_files",
        "index",
        "register_page",
        "shared_page",
        "favicon",
        "api_shared_download",
        "api_shared_info",
        "api_shared_preview",
    ):
        return
    now = datetime.utcnow().timestamp()
    last = session.get("_last_activity")
    if last is None:
        # Legacy session created before timeout tracking — give it a fresh window.
        session["_last_activity"] = now
        session.permanent = True
        return
    if (now - float(last)) > SESSION_TIMEOUT_SECONDS:
        session.clear()
        if request.path.startswith("/api/"):
            return jsonify({"error": "Session expired. Please sign in again.", "session_expired": True}), 401
        return
    session["_last_activity"] = now
    session.permanent = True


# ── Registration helper ──────────────────────────────────────────────────

def register_security(app: Flask):
    """Wire cookie hardening, security headers, error handlers, converter, timeout."""
    # Secure session cookies: never expose to JS, send over HTTPS only in prod,
    # and protect against CSRF with SameSite=Lax. (app.py:29-32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV", "development") != "development"
    app.config["PERMANENT_SESSION_LIFETIME"] = SESSION_TIMEOUT_SECONDS
    app.config["SESSION_COOKIE_PATH"] = "/"

    # URL converter
    app.url_map.converters["uuid"] = UUIDConverter

    # After-request security headers
    app.after_request(_security_headers)

    # Before-request idle timeout
    app.before_request(_enforce_session_timeout)

    # Error handlers (app.py:59-71)
    @app.errorhandler(404)
    def _not_found(e):
        return jsonify({"error": "not found"}), 404

    @app.errorhandler(413)
    def _too_large(e):
        limit = request.max_content_length
        return jsonify({"error": f"Upload too large. Maximum allowed size is {fmt_size(limit) if limit else 'the configured limit'}."}), 413

    @app.errorhandler(500)
    def _server_error(e):
        return jsonify({"error": "Internal server error. Please try again later."}), 500
