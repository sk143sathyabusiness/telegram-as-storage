def test_telegram_not_configured_when_env_missing(monkeypatch):
    import telegram_service
    monkeypatch.setattr(telegram_service, "API_ID", None)
    monkeypatch.setattr(telegram_service, "API_HASH", None)
    assert telegram_service.is_configured() is False


def test_files_requires_auth():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        assert c.get("/api/files").status_code == 401
