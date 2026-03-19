"""Smoke tests for profile_pages router."""

import os


def test_profile_pages_imports():
    """Module imports cleanly."""
    from guild_portal.pages import profile_pages  # noqa: F401


def test_profile_router_exists():
    """Router object is exported."""
    from guild_portal.pages.profile_pages import router
    assert router is not None


def test_profile_router_routes():
    """Router contains the expected route paths."""
    from guild_portal.pages.profile_pages import router

    paths = {route.path for route in router.routes}
    assert "/profile" in paths
    assert "/profile/info" in paths
    assert "/profile/characters" in paths
    assert "/profile/availability" in paths
    assert "/profile/password" in paths


def test_timezones_list():
    """COMMON_TIMEZONES is non-empty and contains Chicago."""
    from guild_portal.pages.profile_pages import COMMON_TIMEZONES

    assert len(COMMON_TIMEZONES) > 0
    assert "America/Chicago" in COMMON_TIMEZONES


def test_day_names():
    """DAY_NAMES has exactly 7 entries starting with Monday."""
    from guild_portal.pages.profile_pages import DAY_NAMES

    assert len(DAY_NAMES) == 7
    assert DAY_NAMES[0] == "Monday"
    assert DAY_NAMES[6] == "Sunday"


# ---------------------------------------------------------------------------
# Phase H.4 — Profile Battle.net section template tests
# ---------------------------------------------------------------------------

SETTINGS_TEMPLATE = os.path.join(
    os.path.dirname(__file__),
    "../../src/guild_portal/templates/profile/settings.html",
)


def _read_template() -> str:
    with open(SETTINGS_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def test_profile_bnet_section_linked_has_refresh_button():
    """Profile settings template includes Refresh Characters button in linked state."""
    html = _read_template()
    assert "btn-profile-refresh-chars" in html
    assert "Refresh Characters" in html


def test_profile_bnet_section_linked_has_unlink_button():
    """Profile settings template includes Unlink button in linked state."""
    html = _read_template()
    assert "confirmUnlink" in html
    assert "Unlink" in html


def test_profile_bnet_section_linked_has_note():
    """Profile settings template includes the 24-hour note in linked state."""
    html = _read_template()
    assert "bnet-note" in html
    assert "24 hours" in html


def test_profile_bnet_section_unlinked_has_link_button():
    """Profile settings template has Link/Connect button in unlinked state."""
    html = _read_template()
    assert "Connect Battle.net" in html


def test_profile_bnet_section_link_button_has_next_param():
    """Link Battle.net URL includes ?next=/profile so OAuth returns here."""
    html = _read_template()
    assert "/auth/battlenet?next=/profile" in html


def test_profile_refresh_js_calls_bnet_sync():
    """Profile refresh JS calls /api/v1/me/bnet-sync with next=/profile."""
    html = _read_template()
    assert "/api/v1/me/bnet-sync?next=/profile" in html


def test_profile_refresh_js_handles_redirect():
    """Profile refresh JS handles data.redirect response from bnet-sync."""
    html = _read_template()
    assert "data.redirect" in html
