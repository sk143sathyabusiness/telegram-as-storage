import os

def test_config_parses_chunk_size():
    from app.config import CHUNK_SIZE_BYTES
    assert CHUNK_SIZE_BYTES == 1900000000

def test_create_app_has_security_headers():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        r = c.get("/")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert "TeamVault" in r.headers.get("Server", "")
        assert r.headers.get("X-Frame-Options") == "DENY"
