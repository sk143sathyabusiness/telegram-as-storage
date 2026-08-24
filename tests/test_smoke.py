def test_smoke_routes_registered():
    from app import create_app
    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/login" in rules
    assert "/api/files/upload" in rules
    assert "/api/shared/<token>" in rules
    assert "/api/backup/list" in rules
