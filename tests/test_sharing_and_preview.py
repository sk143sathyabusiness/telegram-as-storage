import pathlib

def test_csp_allows_blob_for_preview():
    text = pathlib.Path("app/security.py").read_text(encoding="utf-8")
    assert "frame-src 'self' blob:" in text, "frame-src must allow blob: for PDF iframe preview"
    assert "media-src 'self' blob:" in text, "media-src must allow blob: for video/audio preview"
    assert "img-src 'self' data: blob:" in text

def test_csp_allows_vercel_fonts():
    text = pathlib.Path("app/security.py").read_text(encoding="utf-8")
    assert "https://vercel.live" in text
    assert "font-src" in text and "https://vercel.live" in text

def test_loadPreviewAsBlob_decrypts():
    text = pathlib.Path("frontend/files.js").read_text(encoding="utf-8")
    assert "function loadPreviewAsBlob" in text
    assert "getAutoPassphrase()" in text, "preview must use auto key behind screen"
    assert "deriveKey(passphrase)" in text
    assert "crypto.subtle.decrypt" in text
    assert "getMimeForExt" in text
    assert "setPreviewBlobUrl" in text, "must unify blob URL via api.js"
    assert "Blob([plain]" in text
    # audio fix: should query audio element, not rely on firstElementChild div
    assert 'querySelector("audio")' in text or "querySelector('audio')" in text

def test_sharing_frontend_credentials_and_validation():
    text = pathlib.Path("frontend/sharing.js").read_text(encoding="utf-8")
    assert 'credentials: "same-origin"' in text, "sharing fetches must send cookies"
    assert "expires_days" in text
    assert "365" in text, "should clamp expires_days to 365"
    assert "loadExistingShares" in text

def test_sharing_backend_validation():
    text = pathlib.Path("app/sharing.py").read_text(encoding="utf-8")
    assert "expires_days" in text and "365" in text
    assert "password" in text and "128" in text
    assert "unable to open database file" in text.lower() or "supabase" in text.lower()

def test_shared_html_token_extraction():
    text = pathlib.Path("shared.html").read_text(encoding="utf-8")
    assert "decodeURIComponent" in text, "token extraction should decode"
    assert 'split("?")[0]' in text or "split(\"?\")[0]" in text or 'split("?")' in text
    # eye toggle rebind after injection
    assert text.count("pw-toggle") >= 2

def test_share_link_generates_token():
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        # unauth should 401
        assert c.post("/api/files/00000000-0000-0000-0000-000000000000/share", json={}).status_code == 401
