# Security — Sensitive Data Exposure Remediation

> This file documents the exposure found on 2026-08-24 and the fixes applied in this branch. Follow the ROTATION STEPS immediately if this repo is or was public.

## What was exposed

| Secret | Where it was exposed | Current status |
|---|---|---|
| `TELEGRAM_BOT_TOKEN=8998834073:AAG…` | Git history (`12b44ad`, `ecbf492`) — pushed to `origin/main` | **MUST ROTATE** — history still contains it until `filter-repo` + force-push |
| `TELEGRAM_CHANNEL_ID=-4358806946` | Same commits as above | Rotate / move to private channel |
| `SUPABASE_URL=https://xtprkywrwmdzjejxbbae.supabase.co` + `SUPABASE_ANON_KEY=eyJ…xvjqcPAb…` | Git history (`12b44ad`) | **MUST ROTATE** — revoke anon key in Supabase dashboard |
| `TG_API_ID`, `TG_API_HASH`, `SUPABASE_SERVICE_ROLE_KEY` (current project — values in local `.env`) | Local `.env` only — `git log -S <api_id>` has **no hits**, so not in pushed history. `.env` is now correctly gitignored. | Still rotate if `.env` was ever copied, screenshared, or backed up |
| `FLASK_SECRET_KEY` / `ENCRYPTION_VERIFIER_SALT` / `MASTER_ADMIN_BOOTSTRAP_PASSWORD` all equal same weak string (reused 3×) | Local `.env` (weak, reused 3×) | **Fixed locally** — replaced with three distinct `secrets.token_hex` values in `.env`. Rotate to these new values in deployment. |

## Fixes applied in this patch (codebase)

1. **`.gitignore`** — removed ` .env.example` from ignore list (template must stay tracked, placeholders only), tightened patterns: `*.session`, `*.session-journal`, `teamvault.db`, `uploads/`, `backups/`, `*.exe`, `*.bin`; kept `!.env.example`.
2. **Tracked artifact removed** — `git rm --cached vcpkg_installer.exe` (binary should never be versioned; `.gitignore` now covers `*.exe`).
3. **`app.py:81`** — `FLASK_SECRET_KEY` is now canonical (with `SECRET_KEY` legacy alias). Weak-value warning (checks length <32 and markers `change_me`, `Sathyamostpowerfuldeveloper`). File fallback `.secret_key` is 0o600 and only used when env is absent.
4. **`app.py:249`** — `ENCRYPTION_VERIFIER_SALT` is now wired: `hash_share_password` / `verify_share_password` use env salt, verify tries both current + static fallback so old links stay valid. Warns on short/reused salt.
5. **`telegram_bot.py:28`** — no longer `int(os.environ["TG_API_ID"])` at import (which raised `KeyError` + traceback). Now lazy-parsed with `os.getenv`, `is_configured()` is the gate, `_make_client()` raises a clean message without leaking the hash.
6. **`app.py:110`** — `check_supabase()` message no longer leaks key names/values.
7. **Local `.env`** — regenerated 3 weak reused values with distinct `secrets.token_hex(32)` / `token_urlsafe` values (verified `count(Sathyamostpowerfuldeveloper)==0`).
8. **`.env.example` verified** — contains only `your_*` / `generate_*` placeholders (scan for `23957297`, `8998834`, `xxbusk` → 0 hits).

## Immediate rotation steps (you must do)

```bash
# 1. Telegram — BotFather & my.telegram.org
#    - Revoke old bot token (the one starting 8998834…), generate new one, update .env TG_BOT_TOKEN
#    - Optionally regenerate TG_API_ID/TG_API_HASH for the userbot

# 2. Supabase — both projects (legacy + current)
#    - Dashboard → Project Settings → API → Reset service_role key and anon key
#    - Update Vercel env vars + local .env (SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY)
#    - Revoke old JWTs (they remain valid until exp if not revoked)

# 3. Flask — already rotated locally; copy new values to Vercel:
#    vercel env add FLASK_SECRET_KEY
#    vercel env add ENCRYPTION_VERIFIER_SALT
#    vercel env add MASTER_ADMIN_BOOTSTRAP_PASSWORD
```

## Purge secrets from git history (required if repo was public)

The old bot token and Supabase keys are still reachable via `git show 12b44ad:.env`. History must be rewritten:

```bash
# Option A: git filter-repo (recommended)
pip install git-filter-repo
git clone --mirror https://github.com/sk143sathyabusiness/telegram-as-storage.git
cd telegram-as-storage.git
git filter-repo --invert-paths --path .env --path teamvault.db --path __pycache__
# or to scrub just the leaked file content:
git filter-repo --replace-text <(echo "8998834073:AAG==>REDACTED")
git push --force --mirror

# Option B: BFG Repo-Cleaner
# bfg --delete-files .env --delete-files teamvault.db

# After rewriting, every collaborator must re-clone.
# Rotate keys BEFORE force-push so old history values are already invalid.
```

Also delete the 417 commits' leaked artifact cache on GitHub: Settings → Actions → Caches if needed.

## Hardening checklist

- [ ] `.env` is `600` / ACL-restricted (`icacls .env /inheritance:r` on Windows, `chmod 600 .env` on Linux)
- [ ] `.secret_key` and `*.session` are `600` and gitignored (verified: `git check-ignore -v .env .secret_key session.session` → matched)
- [ ] `.env.example` stays tracked with placeholders only (CI check: `grep -R 23957297 .env.example` must be empty)
- [ ] Vercel env vars are set via dashboard, never committed
- [ ] Add secret scanning to CI: `gitleaks detect --source . --redact` or GitHub → Settings → Code security → Secret scanning → Enable
- [ ] Consider `git-secrets` pre-commit hook: `git secrets --install && git secrets --register-aws`
- [ ] Verify no API leaks: `_BLOCKED_STATIC` blocks `/.env`, `/.secret_key`, `*.session`, `app.py` etc. (test: `curl / .env` → 404)

## Verification performed in this patch

```
py -3 -m py_compile app.py telegram_bot.py  → PASS
grep placeholders in .env.example            → 0 hits (PASS)
git check-ignore .env/.secret_key/*.session → ignored (PASS)
git status shows .env untracked, .env.example tracked (PASS)
FLASK_SECRET_KEY/ENCRYPTION reuse count     → 0 (PASS, was 3)
```

If you need help rotating, open an issue and reference this file — do NOT paste real keys in issues.
