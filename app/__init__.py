"""
app — Flask factory (Task 1 scaffold).

create_app() wires config, security, error handlers, blocked-static
and a minimal "/" index route. Later tasks will register blueprints
here (auth, orgs, folders, files, etc.).
"""

import os
from flask import Flask, jsonify, make_response, send_from_directory, Response

from app.config import MAX_CONTENT_LENGTH, SECRET_KEY, SESSION_TIMEOUT_SECONDS
from app.security import register_security


_BLOCKED_STATIC = {
    ".env",
    ".env.example",
    ".secret_key",
    ".git",
    ".gitignore",
    "app.py",
    "telegram_bot.py",
    "telegram_service.py",
    "supabase_schema.sql",
    "requirements.txt",
}


def create_app() -> Flask:
    # app package lives at CWD/app; project root is one level up.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Disable Flask's automatic static route (we serve via explicit
    # static_files with blocklist). template_folder points to project root
    # so render_template still resolves if ever used.
    app = Flask(__name__, static_folder=None, template_folder=project_root)

    # Core config
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    app.secret_key = SECRET_KEY

    # Security: cookies, headers, error handlers, timeout, UUID converter
    register_security(app)

    # Task 2 blueprints
    from app.auth import auth_bp
    app.register_blueprint(auth_bp)

    # Task 3 blueprints
    from app.orgs import orgs_bp
    from app.folders import folders_bp
    app.register_blueprint(orgs_bp)
    app.register_blueprint(folders_bp)

    # Task 4 blueprints
    from app.files import files_bp
    app.register_blueprint(files_bp)

    # Task 5 blueprints
    from app.versions import versions_bp
    from app.trash import trash_bp
    app.register_blueprint(versions_bp)
    app.register_blueprint(trash_bp)

    # Task 6 blueprints
    from app.sharing import sharing_bp
    from app.backups import backups_bp
    from app.users import users_bp
    from app.logs import logs_bp
    app.register_blueprint(sharing_bp)
    app.register_blueprint(backups_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(logs_bp)

    # Bootstrap (master_admin) — one-time
    from app.bootstrap import bootstrap_bp
    app.register_blueprint(bootstrap_bp)

    # Master acting as org
    from app.master import master_bp
    app.register_blueprint(master_bp)

    # Usage statistics (Phase-1 M3/O5)
    from app.stats import stats_bp
    app.register_blueprint(stats_bp)

    # ── Minimal routes required for Task 1 tests ──────────────────────

    @app.route("/")
    def index():
        # Try to serve the real index.html with session-timeout injection
        # (matches app.py:316-324). Fall back to a tiny placeholder for tests
        # or when the file is absent in ephemeral runners.
        candidate = os.path.join(project_root, "index.html")
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                html = f.read()
            html = html.replace(
                '<meta id="session-timeout" content="1800">',
                f'<meta id="session-timeout" content="{SESSION_TIMEOUT_SECONDS}">',
            )
            resp = make_response(html)
            resp.headers["X-Session-Timeout"] = str(SESSION_TIMEOUT_SECONDS)
            return resp
        except FileNotFoundError:
            resp = make_response("<!doctype html><html><body>TeamVault</body></html>")
            resp.headers["Content-Type"] = "text/html"
            return resp

    @app.route("/register")
    def register_page():
        return send_from_directory(project_root, "register.html")

    @app.route("/shared/<token>")
    def shared_page(token):
        return send_from_directory(project_root, "shared.html")

    @app.route("/favicon.ico")
    def favicon():
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
            "<text y='26' font-size='26'>\u2b21</text></svg>"
        )
        return Response(svg, mimetype="image/svg+xml")

    @app.route("/<path:filename>")
    def static_files(filename):
        parts = filename.replace("\\", "/").split("/")
        for part in parts:
            if part in _BLOCKED_STATIC or part.endswith(".session"):
                return jsonify({"error": "not found"}), 404
        return send_from_directory(project_root, filename)

    return app


# Legacy re-export: `from app import app` still works for api/index.py and
# any old import sites until the final cutover removes app.py.
# This keeps the import graph compatible during the redraft.
app = create_app()
