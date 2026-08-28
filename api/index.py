import os
import sys
import traceback

# Ensure the project root (one level above api/) is importable so that
# `import app` resolves to the app/ package rather than any stray module.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

app = None
_bootstrap_error = None

try:
    from app import create_app
    app = create_app()
except Exception as _exc:  # surfaced to Vercel logs AND the browser
    _bootstrap_error = traceback.format_exc()
    # Fallback WSGI app so the function still deploys and shows the real
    # error instead of failing silently at import time on Vercel.
    from flask import Flask, Response

    _fallback = Flask(__name__)

    @_fallback.route("/<path:_p>")
    @_fallback.route("/")
    def _show_error(_p=""):
        return Response(
            "Application failed to start on Vercel:\n\n" + _bootstrap_error,
            mimetype="text/plain",
            status=500,
        )

    app = _fallback
