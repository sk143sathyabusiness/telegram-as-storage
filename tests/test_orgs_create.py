"""Tests for master-admin organisation creation + per-org backup channel."""

import uuid


def _login_session(client, role="master_admin", org_id=None):
    with client.session_transaction() as s:
        s["user_id"] = str(uuid.uuid4())
        s["role"] = role
        s["org_id"] = org_id
        s["username"] = "tester"


class _Result:
    def __init__(self, data=None):
        self.data = data


class _Table:
    def __init__(self, name, store):
        self.name = name
        self.store = store

    def select(self, *a):
        return self

    def eq(self, *a):
        return self

    def in_(self, *a):
        return self

    def order(self, *a):
        return self

    def maybe_single(self):
        self._single = True
        return self

    def insert(self, rows):
        self.store.setdefault(self.name, []).append(rows)
        return self

    def delete(self):
        return self

    def update(self, *a):
        return self

    def execute(self):
        # Pre-programmed results per table for the create flow
        queued = self.store.get(f"_q_{self.name}")
        if queued:
            return _Result(queued.pop(0))
        if self.name == "organizations" and getattr(self, "_single", False):
            return _Result(None)
        if self.name == "users":
            return _Result([])
        return _Result([{"id": str(uuid.uuid4())}])


class _FakeSupabase:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _Table(name, self.store)


def test_orgs_create_requires_login():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.post("/api/orgs/create", json={"org_name": "X"})
        assert r.status_code == 401


def test_orgs_create_requires_master():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        _login_session(c, role="org_admin", org_id=str(uuid.uuid4()))
        r = c.post("/api/orgs/create", json={"org_name": "X"})
        assert r.status_code == 403


def test_orgs_create_missing_fields():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post("/api/orgs/create", json={})
        assert r.status_code == 400
        assert "org_name" in r.get_json()["error"].lower()


def test_orgs_create_happy_manual_backup(monkeypatch):
    from app import create_app, orgs
    app = create_app()
    fake = _FakeSupabase()
    monkeypatch.setattr(orgs, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs, "log_action", lambda *a, **k: None)
    # manual backup channel id provided -> no telegram call
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post("/api/orgs/create", json={
            "org_name": "Acme",
            "chat_id": "-1001234567890",
            "username": "admin_acme",
            "password": "secret123",
            "backup_channel_id": "-100999888777",
        })
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["backup_channel_id"] == "-100999888777"
        # organization inserted with backup_channel_id
        org_insert = fake.store["organizations"][0]
        assert org_insert["backup_channel_id"] == "-100999888777"
        assert org_insert["status"] == "active"
        # admin user created
        user_insert = fake.store["users"][0]
        assert user_insert["role"] == "org_admin"
        assert user_insert["username"] == "admin_acme"


def test_orgs_create_happy_auto_backup(monkeypatch):
    from app import create_app, orgs
    app = create_app()
    fake = _FakeSupabase()
    monkeypatch.setattr(orgs, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(orgs, "create_backup_channel", lambda title: 1234567890)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post("/api/orgs/create", json={
            "org_name": "Beta",
            "chat_id": "-100111222333",
            "username": "admin_beta",
            "password": "secret123",
        })
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["backup_channel_id"] == "1234567890"


def test_orgs_create_duplicate_name(monkeypatch):
    from app import create_app, orgs
    app = create_app()
    fake = _FakeSupabase()
    # organizations select(maybe_single) returns an existing active org
    fake.store["_q_organizations"] = [{"id": str(uuid.uuid4()), "status": "active"}]
    monkeypatch.setattr(orgs, "get_supabase", lambda: fake)
    monkeypatch.setattr(orgs, "log_action", lambda *a, **k: None)
    with app.test_client() as c:
        _login_session(c, role="master_admin", org_id=None)
        r = c.post("/api/orgs/create", json={
            "org_name": "Dup",
            "chat_id": "-100111222333",
            "username": "admin_dup",
            "password": "secret123",
        })
        assert r.status_code == 409


def test_orgs_set_backup_channel_requires_login():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.put(f"/api/orgs/{uuid.uuid4()}/backup-channel", json={"backup_channel_id": "1"})
        assert r.status_code == 401
