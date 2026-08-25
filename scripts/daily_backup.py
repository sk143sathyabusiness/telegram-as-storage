"""
Daily (essential-only) backup scheduler for telegram-as-storage.

Runs in-process against the Flask app using its test client so it can call the
master-only `/api/backup/daily` endpoint without exposing credentials over HTTP.

Usage:
    python scripts/daily_backup.py            # uses MASTER_USERNAME / MASTER_PASSWORD env
    MASTER_USERNAME=admin MASTER_PASSWORD=... python scripts/daily_backup.py

Intended to be invoked from cron, e.g.:
    17 3 * * *  cd /path/to/telegram-as-storage && python scripts/daily_backup.py >> /var/log/daily_backup.log 2>&1
"""
import os
import sys

# Make project root importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

APP = create_app()
APP.config["TESTING"] = True


def main():
    username = os.environ.get("MASTER_USERNAME") or os.environ.get("ADMIN_USERNAME")
    password = os.environ.get("MASTER_PASSWORD") or os.environ.get("ADMIN_PASSWORD")
    if not username or not password:
        print("[daily_backup] ERROR: set MASTER_USERNAME and MASTER_PASSWORD env vars")
        sys.exit(2)

    with APP.test_client() as client:
        r = client.post("/api/login", json={"username": username, "password": password})
        if r.status_code != 200:
            print(f"[daily_backup] ERROR: login failed: {r.get_json().get('error')}")
            sys.exit(3)
        if r.get_json().get("role") != "master_admin":
            print("[daily_backup] ERROR: account is not master_admin")
            sys.exit(3)

        r = client.post("/api/backup/daily")
        if r.status_code == 200:
            data = r.get_json()
            print(f"[daily_backup] OK: {data.get('summary')} | detail={data}")
            sys.exit(0)
        else:
            print(f"[daily_backup] ERROR: {r.get_json().get('error')}")
            sys.exit(4)


if __name__ == "__main__":
    main()
