"""
app.supabase_client — single Supabase client instance (Task 1).

All Supabase calls pass through get_supabase(). The client is
lazily created once; check_supabase() validates env at startup.
"""

from supabase import create_client, Client

from app.config import SUPABASE_URL, SUPABASE_KEY

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def check_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        # Only print hint when run as main; tests call this directly
        # and expect SystemExit without noisy output.
        import __main__

        if getattr(__main__, "__name__", None) == "__main__":
            print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env (see .env.example)")
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
