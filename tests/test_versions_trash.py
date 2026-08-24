def test_versions_requires_permission():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.get("/api/files/00000000-0000-0000-0000-000000000000/versions").status_code == 401
