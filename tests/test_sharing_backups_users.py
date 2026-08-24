def test_share_requires_auth():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.post("/api/files/00000000-0000-0000-0000-000000000000/share", json={}).status_code == 401

def test_shared_info_404(monkeypatch):
    from app import create_app
    app = create_app()

    # Mock Supabase to avoid real network call — return empty link (404)
    class _FakeResult:
        data = None
    class _FakeQuery:
        def select(self, *a, **kw): return self
        def eq(self, *a, **kw): return self
        def maybe_single(self): return self
        def execute(self): return _FakeResult()
    class _FakeTable:
        def select(self, *a, **kw): return _FakeQuery()
    class _FakeSup:
        def table(self, name): return _FakeTable()

    # Patch both import sites
    import app.supabase_client as sc
    import app.sharing as sh
    monkeypatch.setattr(sc, "get_supabase", lambda: _FakeSup())
    monkeypatch.setattr(sh, "get_supabase", lambda: _FakeSup())

    with app.test_client() as c:
        assert c.get("/api/shared/invalidtoken123/info").status_code == 404
