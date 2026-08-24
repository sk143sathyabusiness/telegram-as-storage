import os
import json
import hashlib
import uuid

# ── utils: hash / verify ──────────────────────────────────────────────

def test_hash_verify_current_and_static_salt():
    from app.utils import hash_share_password, verify_share_password
    h = hash_share_password("secret123")
    assert h.startswith("pbkdf2$")
    assert verify_share_password("secret123", h) is True
    assert verify_share_password("wrong", h) is False


def test_verify_static_salt_fallback():
    # Hash created with the historic static salt should still verify even
    # when the deployment salt differs — dual-salt behavior from design §5.
    from app.config import _SHARE_PW_SALT_STATIC
    from app.utils import verify_share_password
    import hashlib
    # Manually craft a hash with static salt
    dk = hashlib.pbkdf2_hmac("sha256", b"legacy_pw", _SHARE_PW_SALT_STATIC, 100_000)
    stored = "pbkdf2$" + dk.hex()
    assert verify_share_password("legacy_pw", stored) is True
    assert verify_share_password("wrong", stored) is False


def test_verify_legacy_sha256_fallback():
    from app.utils import verify_share_password
    import hashlib
    plain = hashlib.sha256(b"oldpass").hexdigest()
    assert verify_share_password("oldpass", plain) is True
    assert verify_share_password("wrong", plain) is False


# ── utils: _parse_message_ids ─────────────────────────────────────────

def test_parse_message_ids():
    from app.utils import _parse_message_ids
    assert _parse_message_ids([1, 2]) == [1, 2]
    assert _parse_message_ids("[1,2]") == [1, 2]
    assert _parse_message_ids(None) == []
    assert _parse_message_ids("") == [] or _parse_message_ids("") == []  # tolerant
    # JSON string variant with spaces
    assert _parse_message_ids("[1, 2, 3]") == [1, 2, 3]


# ── utils: fmt_size ────────────────────────────────────────────────────

def test_fmt_size():
    from app.utils import fmt_size
    assert fmt_size(None) == "—"
    assert "B" in fmt_size(500)
    assert "KB" in fmt_size(2048)
    assert "MB" in fmt_size(2 * 1024 * 1024)


# ── utils: _check_permission ─────────────────────────────────────────

def test_check_permission_master_admin(monkeypatch):
    from app import utils as utils_mod

    # Mock sup.table chain that returns master_admin for users query
    class FakeResult:
        def __init__(self, data):
            self.data = data

    class FakeMaybeSingle:
        def __init__(self, data):
            self._data = data
        def execute(self):
            return FakeResult(self._data)

    class FakeQuery:
        def __init__(self, role):
            self._role = role
        def select(self, *a, **kw):
            return self
        def eq(self, *a, **kw):
            return self
        def maybe_single(self):
            return FakeMaybeSingle({"role": "master_admin"})

    class FakeTable:
        def table(self, name):
            # users table returns master_admin; permissions would be irrelevant
            return FakeQuery("master_admin")

    fake_sup = FakeTable()
    result = utils_mod._check_permission(fake_sup, "uid", "org", folder_id=str(uuid.uuid4()))
    assert result == "org_admin"


def test_check_permission_folder_scoped(monkeypatch):
    from app import utils as utils_mod

    class FakeResult:
        def __init__(self, data):
            self.data = data

    folder_id = str(uuid.uuid4())

    def fake_table(name):
        class Q:
            def select(self, *a, **kw):
                return self
            def eq(self, *a, **kw):
                # store last table name for decision
                return self
            def maybe_single(self):
                return self
            def execute(self):
                # We need to know which table we are on; use closure var name
                if name == "users":
                    return FakeResult({"role": "read_only"})
                if name == "permissions":
                    return FakeResult({"permission_level": "read_write"})
                return FakeResult(None)
        return Q()

    class FakeSup:
        def table(self, name):
            return fake_table(name)

    result = utils_mod._check_permission(FakeSup(), "uid", "org", folder_id=folder_id)
    assert result == "read_write"


# ── utils: _require_active_org ───────────────────────────────────────

def test_require_active_org_blocks_inactive():
    from app.utils import _require_active_org
    from app import create_app
    app = create_app()

    class FakeResult:
        def __init__(self, data):
            self.data = data

    class FakeQuery:
        def __init__(self, status):
            self._status = status
        def select(self, *a, **kw):
            return self
        def eq(self, *a, **kw):
            return self
        def maybe_single(self):
            return self
        def execute(self):
            return FakeResult({"status": self._status})

    class FakeSup:
        def __init__(self, status):
            self._status = status
        def table(self, name):
            return FakeQuery(self._status)

    # inactive
    with app.app_context():
        resp, code = _require_active_org(FakeSup("suspended"), "org_id")
        assert code == 403
    # active
    with app.app_context():
        assert _require_active_org(FakeSup("active"), "org_id") is None


# ── utils: _resolve_folder_name ──────────────────────────────────────

def test_resolve_folder_name():
    from app.utils import _resolve_folder_name

    class FakeResult:
        def __init__(self, data):
            self.data = data

    class FakeQ:
        def select(self, *a, **kw):
            return self
        def eq(self, *a, **kw):
            return self
        def maybe_single(self):
            return self
        def execute(self):
            return FakeResult({"name": "MyFolder"})

    class FakeSup:
        def table(self, name):
            return FakeQ()

    assert _resolve_folder_name(FakeSup(), None) == "Root"
    assert _resolve_folder_name(FakeSup(), str(uuid.uuid4())) == "MyFolder"


# ── utils: log_action ─────────────────────────────────────────────────

def test_log_action_writes_audit_logs(monkeypatch):
    from app import utils as utils_mod

    inserted = {}

    class FakeInsert:
        def __init__(self, payload):
            inserted.update(payload)
        def execute(self):
            return None

    class FakeTable:
        def insert(self, payload):
            return FakeInsert(payload)

    class FakeSup:
        def table(self, name):
            assert name == "audit_logs"
            return FakeTable()

    monkeypatch.setattr(utils_mod, "get_supabase", lambda: FakeSup())
    # Mock current_user to return fake user
    monkeypatch.setattr(utils_mod, "current_user", lambda: {"id": "uid1", "org_id": "org1", "role": "org_admin"})

    utils_mod.log_action("test_action", target="myfile", detail="detail1", target_type="file", target_id=str(uuid.uuid4()))
    assert inserted["action"] == "test_action"
    assert inserted["org_id"] == "org1"
    assert inserted["actor_id"] == "uid1"


# ── auth blueprint ────────────────────────────────────────────────────

def test_auth_blueprint_routes_registered():
    from app import create_app
    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/login" in rules
    assert "/api/logout" in rules
    assert "/api/me" in rules


def test_me_requires_auth():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.get("/api/me")
        assert r.status_code == 401


def test_login_validation_400():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.post("/api/login", json={"username": "", "password": ""})
        assert r.status_code == 400


def test_logout_clears_session():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.post("/api/logout")
        assert r.status_code == 200
        assert r.get_json().get("ok") is True
