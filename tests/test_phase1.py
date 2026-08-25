"""Phase-1 feature tests: usage stats, org edit, password reset/change,
essential-folder toggle, daily backup, bulk file ops."""

import uuid

import werkzeug.security


def _login_session(client, role="master_admin", org_id=None):
    with client.session_transaction() as s:
        s["user_id"] = str(uuid.uuid4())
        s["role"] = role
        s["org_id"] = org_id
        s["username"] = "tester"


class _Result:
    def __init__(self, data=None, single=None):
        if single is not None:
            self.data = single
        else:
            self.data = data if data is not None else []


class _Table:
    def __init__(self, store, name):
        self.store = store
        self.name = name
        self._single = False

    def select(self, *a): return self
    def eq(self, *a): return self
    def ilike(self, *a, **k): return self
    def in_(self, *a): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self

    def maybe_single(self):
        self._single = True
        return self

    def insert(self, rows):
        self.store.setdefault(self.name, []).append(rows)
        return self

    def update(self, *a):
        self.store.setdefault("updates_" + self.name, []).append(a[0] if a else {})
        return self

    def delete(self): return self

    def execute(self):
        if self._single:
            q = self.store.get("_q_" + self.name)
            if q:
                return _Result(None, single=q.pop(0))
            return _Result(None, single=None)
        q = self.store.get("_q_" + self.name)
        if q is not None:
            return _Result(list(q))  # non-consuming copy (re-readable)
        if self.name == "files":
            return _Result([])
        if self.name in ("audit_logs", "backups"):
            return _Result([])
        return _Result([{"id": str(uuid.uuid4())}])


class _FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _Table(self.store, name)


# ── STATS ──────────────────────────────────────────────────────────────────
def test_stats_org_scoped(monkeypatch):
    from app import create_app, stats as stats_mod
    app = create_app()
    fake = _FakeSupabase()
    monkeypatch.setattr(stats_mod, "get_supabase", lambda: fake)
    with app.test_client() as c:
        oid = str(uuid.uuid4())
        _login_session(c, role="org_admin", org_id=oid)
        r = c.get("/api/stats")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["org"]["org_id"] == oid
        assert "file_count" in d["org"] and "storage_bytes" in d["org"] and "user_count" in d["org"]


def test_stats_master_global(monkeypatch):
    from app import create_app, stats as stats_mod
    app = create_app()
    fake = _FakeSupabase()
    monkeypatch.setattr(stats_mod, "get_supabase", lambda: fake)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.get("/api/stats")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert "orgs" in d and "totals" in d


# ── ORG EDIT ─────────────────────────────────────────────────────────────────
def test_edit_org_requires_master(monkeypatch):
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        r = c.put(f"/api/orgs/{uuid.uuid4()}", json={"name": "X", "telegram_chat_id": "-1"})
        assert r.status_code == 403


def test_edit_org_happy(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "name": "Old", "status": "active"}]
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        oid = str(uuid.uuid4())
        r = c.put(f"/api/orgs/{oid}", json={
            "name": "Renamed", "telegram_chat_id": "-100999", "status": "active",
        })
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_organizations"][0]["name"] == "Renamed"


def test_reset_admin_happy(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "name": "Acme"}]
    fake.store["_q_users"] = [{"id": str(uuid.uuid4()), "username": "admin_acme", "role": "org_admin"}]
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post(f"/api/orgs/{uuid.uuid4()}/reset-admin", json={"password": "newpass123"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["username"] == "admin_acme"
        # users update carried the new hash
        upd = fake.store["updates_users"][0]
        assert "password_hash" in upd and upd["password_hash"]


# ── SELF PASSWORD CHANGE ──────────────────────────────────────────────────────
def test_me_password_wrong_current(monkeypatch):
    from app import create_app, users as users_mod
    app = create_app()
    fake = _FakeSupabase()
    uid = str(uuid.uuid4())
    fake.store["_q_users"] = [{
        "id": uid, "username": "me", "role": "org_admin",
        "password_hash": werkzeug.security.generate_password_hash("oldpass"),
    }]
    monkeypatch.setattr(users_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(users_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()), )
        # override user_id to match queued user
        with c.session_transaction() as s:
            s["user_id"] = uid
        r = c.post("/api/users/me/password", json={
            "current_password": "wrong", "new_password": "newpass1",
        })
        assert r.status_code == 400


def test_me_password_happy(monkeypatch):
    from app import create_app, users as users_mod
    app = create_app()
    fake = _FakeSupabase()
    uid = str(uuid.uuid4())
    fake.store["_q_users"] = [{
        "id": uid, "username": "me", "role": "org_admin",
        "password_hash": werkzeug.security.generate_password_hash("oldpass"),
    }]
    monkeypatch.setattr(users_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(users_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        with c.session_transaction() as s:
            s["user_id"] = uid
        r = c.post("/api/users/me/password", json={
            "current_password": "oldpass", "new_password": "newpass1",
        })
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_users"][0]["password_hash"]


# ── ESSENTIAL FOLDER TOGGLE ───────────────────────────────────────────────────
def test_toggle_essential_folder(monkeypatch):
    from app import create_app, folders as folders_mod
    app = create_app()
    fake = _FakeSupabase()
    fid = str(uuid.uuid4())
    oid = str(uuid.uuid4())
    fake.store["_q_folders"] = [{"id": fid, "name": "Docs", "org_id": oid, "is_essential": False}]
    monkeypatch.setattr(folders_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(folders_mod, "_check_permission", lambda *a, **k: True)
    monkeypatch.setattr(folders_mod, "_require_active_org", lambda *a, **k: None)
    monkeypatch.setattr(folders_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=oid)
        r = c.post(f"/api/folders/{fid}/essential", json={})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["is_essential"] is True


# ── DAILY BACKUP ──────────────────────────────────────────────────────────────
def test_daily_backup_runs(monkeypatch):
    from app import create_app, backups as backups_mod
    app = create_app()
    fake = _FakeSupabase()
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "status": "active"}]
    monkeypatch.setattr(backups_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(backups_mod, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(backups_mod, "_make_backup", lambda *a, **k: {"name": "bk_test"})
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post("/api/backup/daily")
        assert r.status_code == 200, r.get_json()
        assert r.get_json().get("ok") is True


# ── BULK FILE OPS ─────────────────────────────────────────────────────────────
def test_bulk_delete(monkeypatch):
    from app import create_app, files as files_mod
    app = create_app()
    fake = _FakeSupabase()
    monkeypatch.setattr(files_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(files_mod, "_check_permission", lambda *a, **k: True)
    monkeypatch.setattr(files_mod, "_require_active_org", lambda *a, **k: None)
    monkeypatch.setattr(files_mod, "_resolve_folder_name", lambda *a, **k: "~")
    monkeypatch.setattr(files_mod, "_valid_org_file_ids", lambda sup, oid, ids: set(ids))
    monkeypatch.setattr(files_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        r = c.post("/api/files/bulk-delete", json={"ids": ids})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["deleted"] == 2


# ── SUSPEND / QUOTA / DELETE ORG (M2/M7/M9) ────────────────────────────────────
def test_suspend_org(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "name": "Acme"}]
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.put(f"/api/orgs/{uuid.uuid4()}", json={"status": "suspended"})
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_organizations"][0]["status"] == "suspended"


def test_set_org_quota(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "name": "Acme"}]
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.put(f"/api/orgs/{uuid.uuid4()}", json={"storage_quota_bytes": 12345})
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_organizations"][0]["storage_quota_bytes"] == 12345


def test_delete_org(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "name": "Acme"}]
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.delete(f"/api/orgs/{uuid.uuid4()}")
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_organizations"][0]["status"] == "deleted"


def test_delete_org_requires_master(monkeypatch):
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        r = c.delete(f"/api/orgs/{uuid.uuid4()}")
        assert r.status_code == 403


# ── MASTER GLOBAL SEARCH (M5) ──────────────────────────────────────────────────
def test_master_search(monkeypatch):
    from app import create_app, master as master_mod
    app = create_app()
    fake = _FakeSupabase()
    oid = str(uuid.uuid4())
    fake.store["_q_files"] = [{"id": str(uuid.uuid4()), "name": "report.pdf", "org_id": oid, "folder_id": None, "is_deleted": False}]
    fake.store["_q_users"] = [{"id": str(uuid.uuid4()), "username": "alice", "role": "read_write", "org_id": oid}]
    fake.store["_q_organizations"] = [{"id": oid, "name": "Acme"}]
    monkeypatch.setattr(master_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(master_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.get("/api/master/search?q=rep")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert any("report.pdf" in f["name"] for f in d["files"])
        assert any(u["username"] == "alice" for u in d["users"])


def test_master_search_requires_master(monkeypatch):
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        r = c.get("/api/master/search?q=rep")
        assert r.status_code == 403


# ── MASTER RESET ANY USER PASSWORD (M6) ─────────────────────────────────────────
def test_reset_any_user_password(monkeypatch):
    from app import create_app, users as users_mod
    app = create_app()
    fake = _FakeSupabase()
    uid = str(uuid.uuid4())
    fake.store["_q_users"] = [{"id": uid, "username": "bob", "org_id": str(uuid.uuid4())}]
    monkeypatch.setattr(users_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(users_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post(f"/api/users/{uid}/reset-password", json={"password": "newpass123"})
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_users"][0]["password_hash"]


# ── RENAME FOLDER (O4) ──────────────────────────────────────────────────────────
def test_rename_folder(monkeypatch):
    from app import create_app, folders as folders_mod
    app = create_app()
    fake = _FakeSupabase()
    fid = str(uuid.uuid4())
    oid = str(uuid.uuid4())
    fake.store["_q_folders"] = [{"id": fid, "name": "Old", "org_id": oid}]
    monkeypatch.setattr(folders_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(folders_mod, "_check_permission", lambda *a, **k: True)
    monkeypatch.setattr(folders_mod, "_require_active_org", lambda *a, **k: None)
    monkeypatch.setattr(folders_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=oid)
        r = c.put(f"/api/folders/{fid}", json={"name": "New"})
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_folders"][0]["name"] == "New"


# ── SHARE LINKS (O6) ────────────────────────────────────────────────────────────
def test_org_admin_sets_own_backup_channel(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    oid = str(uuid.uuid4())
    fake.store["_q_organizations"] = [{"id": oid, "name": "Acme"}]
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=oid)
        r = c.put(f"/api/orgs/{oid}/backup-channel", json={"backup_channel_id": "-100555"})
        assert r.status_code == 200, r.get_json()
        assert fake.store["updates_organizations"][0]["backup_channel_id"] == "-100555"


def test_org_admin_cannot_set_other_org_backup_channel(monkeypatch):
    from app import create_app, orgs as orgs_mod
    app = create_app()
    fake = _FakeSupabase()
    monkeypatch.setattr(orgs_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        r = c.put(f"/api/orgs/{uuid.uuid4()}/backup-channel", json={"backup_channel_id": "-100555"})
        assert r.status_code == 403


def test_shares_list_and_revoke(monkeypatch):
    from app import create_app, sharing as sharing_mod
    app = create_app()
    fake = _FakeSupabase()
    oid = str(uuid.uuid4())
    fid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    fake.store["_q_shared_links"] = [{
        "id": sid, "token": "tok123", "file_id": fid, "created_at": "2026-01-01T00:00:00",
        "expires_at": None, "download_count": 3, "password_hash": None,
        "files": {"name": "doc.pdf", "org_id": oid},
    }]
    fake.store["_q_files"] = [{"id": fid, "name": "doc.pdf", "org_id": oid}]
    monkeypatch.setattr(sharing_mod, "get_supabase", lambda: fake)
    monkeypatch.setattr(sharing_mod, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=oid)
        r = c.get("/api/shares")
        assert r.status_code == 200, r.get_json()
        assert any(s["id"] == sid for s in r.get_json())
        r2 = c.delete(f"/api/shares/{sid}")
        assert r2.status_code == 200, r2.get_json()
