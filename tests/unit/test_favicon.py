"""Regression coverage for the public favicon contract."""

from pathlib import Path

from fastapi.testclient import TestClient

from guild_portal.app import create_app


ROOT = Path(__file__).parents[2]
FAVICON = ROOT / "src" / "guild_portal" / "static" / "img" / "favicon.svg"


def test_favicon_route_serves_bundled_svg_without_setup_redirect():
    client = TestClient(create_app())
    response = client.get("/favicon.ico")
    declared_asset = client.get("/static/img/favicon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.content == FAVICON.read_bytes()
    assert declared_asset.status_code == 200
    assert declared_asset.headers["content-type"].startswith("image/svg+xml")
    assert declared_asset.content == FAVICON.read_bytes()


def test_shared_page_templates_declare_bundled_favicon():
    expected = '<link rel="icon" href="/static/img/favicon.svg" type="image/svg+xml">'
    templates = (
        "src/guild_portal/templates/base.html",
        "src/guild_portal/templates/base_admin.html",
        "src/guild_portal/templates/setup/base_setup.html",
    )

    assert FAVICON.read_text(encoding="utf-8").startswith("<svg")
    for template in templates:
        assert expected in (ROOT / template).read_text(encoding="utf-8")
