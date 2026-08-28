# Vercel/WSGI entrypoint.
# Vercel's Flask preset only detects entrypoints named app.py / index.py /
# server.py / main.py / wsgi.py / asgi.py at the repo root (or src/ or app/).
# This file satisfies that so Vercel builds the app as a single Python Function.
from app import create_app

app = create_app()
