"""
Generate TG_SESSION_STRING for Vercel from local session.session.

Vercel's filesystem is read-only, so Telethon's SQLite session file
(session.session) cannot be opened there ("unable to open database file").
Set TG_SESSION_STRING env var instead.

Usage:
  py -3 scripts/generate_string_session.py
  # or: py -3 scripts/generate_string_session.py --session session
  # then copy the printed string to Vercel env TG_SESSION_STRING

Requires: TG_API_ID/TG_API_HASH in .env and existing session.session (run `py -3 telegram_service.py` once locally to create it).
"""
import os
import argparse
from dotenv import load_dotenv

load_dotenv()

parser = argparse.ArgumentParser(description="Generate TG_SESSION_STRING from session file")
parser.add_argument("--session", default=os.getenv("TG_SESSION_NAME", "session"), help="session name without .session extension (default: from TG_SESSION_NAME or 'session')")
args = parser.parse_args()

session_path = f"{args.session}.session"
if not os.path.exists(session_path):
    print(f"ERROR: {session_path} not found. Run `py -3 telegram_service.py` locally first and complete phone/OTP login.")
    raise SystemExit(1)

try:
    from telethon.sessions import StringSession
    from telethon import TelegramClient
except ImportError as e:
    print(f"Missing telethon: {e}\nRun: pip install telethon python-dotenv")
    raise SystemExit(1)

api_id = os.getenv("TG_API_ID")
api_hash = os.getenv("TG_API_HASH")
if not api_id or not api_hash:
    print("ERROR: TG_API_ID/TG_API_HASH not set in .env")
    raise SystemExit(1)

# Load the file session and convert to StringSession
# StringSession.save() returns the base64-like string to store in env
client = TelegramClient(args.session, int(api_id), api_hash)
# We don't need to connect — just load the sqlite file into StringSession
# Telethon's StringSession can be created from existing session file by reading it
# Simplest: use StringSession.save() after loading file via SQLite
try:
    # Direct conversion: read session file via StringSession
    # This works because StringSession inherits SQLiteSession logic
    from telethon.sessions.sqlite import SQLiteSession
    # Create a StringSession and copy auth from SQLite file
    # Official method: StringSession.save(client.session)
    # We need to start client without network to load auth key
    import asyncio
    async def _convert():
        await client.connect()
        # client.session is SQLiteSession; export as StringSession
        s = StringSession.save(client.session)
        await client.disconnect()
        return s
    s = asyncio.run(_convert())
except Exception as e:
    print(f"Failed to convert via connect: {e}")
    print("Trying direct file read fallback...")
    # Fallback: read file bytes and base64 encode (not StringSession but still works via manual decode on Vercel)
    # However proper StringSession is preferred — suggest manual step
    raise SystemExit(1)

print("\n" + "="*70)
print("TG_SESSION_STRING (copy this entire line to Vercel):")
print("="*70)
print(s)
print("="*70)
print("\nVercel steps:")
print("  1. Vercel Dashboard → your project → Settings → Environment Variables")
print("  2. Add: TG_SESSION_STRING = <above string>  (all environments)")
print("  3. Redeploy (Deployments → ⋯ → Redeploy)")
print("  4. Hard-refresh your site and retry upload")
print("\nKeep this string secret — it is your Telegram login. Never commit it.")
