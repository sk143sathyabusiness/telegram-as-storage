def test_frontend_modules_exist():
    import pathlib
    for name in ["api","auth","folders","files","sharing","admin"]:
        assert pathlib.Path(f"frontend/{name}.js").exists()
    html = pathlib.Path("index.html").read_text(encoding="utf-8")
    assert 'type="module"' in html
