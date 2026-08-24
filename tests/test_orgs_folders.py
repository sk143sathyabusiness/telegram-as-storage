def test_org_register_requires_fields():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.post("/api/org/register", json={})
        assert r.status_code == 400
        assert "org_name" in r.get_json()["error"].lower()
