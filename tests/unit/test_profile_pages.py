"""Smoke tests for profile_pages router."""


def test_profile_pages_imports():
    """Module imports cleanly."""
    from patt.pages import profile_pages  # noqa: F401


def test_profile_router_exists():
    """Router object is exported."""
    from patt.pages.profile_pages import router
    assert router is not None


def test_profile_router_routes():
    """Router contains the expected route paths."""
    from patt.pages.profile_pages import router

    paths = {route.path for route in router.routes}
    assert "/profile" in paths
    assert "/profile/info" in paths
    assert "/profile/characters" in paths
    assert "/profile/availability" in paths
    assert "/profile/password" in paths


def test_timezones_list():
    """COMMON_TIMEZONES is non-empty and contains Chicago."""
    from patt.pages.profile_pages import COMMON_TIMEZONES

    assert len(COMMON_TIMEZONES) > 0
    assert "America/Chicago" in COMMON_TIMEZONES


def test_day_names():
    """DAY_NAMES has exactly 7 entries starting with Monday."""
    from patt.pages.profile_pages import DAY_NAMES

    assert len(DAY_NAMES) == 7
    assert DAY_NAMES[0] == "Monday"
    assert DAY_NAMES[6] == "Sunday"
