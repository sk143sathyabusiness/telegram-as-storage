"""
Legacy shim — original 1890-line monolith has been modularized into app/ package.
This file now re-exports create_app() for backward compat (`python app.py` still works).
Next commit may delete this file entirely; `from app import create_app` will resolve to package.
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
