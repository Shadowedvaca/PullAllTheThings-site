"""
Shared in-process cache for site_config DB row.

Populated during app lifespan startup (load_site_config in app.py).
Provides zero-import-cycle access to guild-specific config for all modules.
"""

_cache: dict = {}


def set_site_config(config: dict) -> None:
    """Populate the cache from a site_config DB row dict."""
    _cache.clear()
    _cache.update(config)
    # Ensure computed fields
    hex_val = config.get("accent_color_hex", "#d4a84b")
    try:
        _cache["accent_color_int"] = int(hex_val.lstrip("#"), 16)
    except (ValueError, AttributeError):
        _cache["accent_color_int"] = 0xD4A84B


def get_site_config() -> dict:
    """Return a copy of the cached site config dict."""
    return dict(_cache)


def get_accent_color_int() -> int:
    """Return the accent color as an integer (for Discord embeds)."""
    return _cache.get("accent_color_int", 0xD4A84B)


def get_guild_name() -> str:
    """Return the guild's display name."""
    return _cache.get("guild_name", "Guild Portal")


def get_home_realm_slug() -> str:
    """Return the home realm API slug (e.g. 'senjin')."""
    return _cache.get("home_realm_slug", "")


def get_guild_name_slug() -> str:
    """Return the guild API slug (e.g. 'pull-all-the-things')."""
    return _cache.get("guild_name_slug", "")


def get_discord_invite_url() -> str:
    """Return the Discord invite URL."""
    return _cache.get("discord_invite_url", "")


def is_guild_quotes_enabled() -> bool:
    """Return True if the Guild Quotes feature is enabled."""
    return bool(_cache.get("enable_guild_quotes", False))


def is_contests_enabled() -> bool:
    """Return True if the Contests feature is enabled."""
    return bool(_cache.get("enable_contests", True))


def is_onboarding_enabled() -> bool:
    """Return True if the onboarding flow is enabled (on_member_join triggers DM flow)."""
    return bool(_cache.get("enable_onboarding", True))


def set_app_url(url: str) -> None:
    """Store the app URL (set at startup from settings) so sv_common modules can read it."""
    _cache["_app_url"] = url.rstrip("/") if url else ""


def get_app_url() -> str:
    """Return the app's base URL (e.g. 'https://pullallthethings.com')."""
    return _cache.get("_app_url", "")
