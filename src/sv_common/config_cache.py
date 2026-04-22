"""
Shared in-process cache for site_config DB row.

Populated during app lifespan startup (load_site_config in app.py).
Provides zero-import-cycle access to guild-specific config for all modules.
"""

from dataclasses import dataclass
from typing import Optional

_cache: dict = {}


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_address: str


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


def get_realm_display_name() -> str:
    """Return the realm's human-readable display name (e.g. \"Sen'jin\")."""
    return _cache.get("realm_display_name", "")


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


_program_name: str = "unknown"


def set_program_name(name: str) -> None:
    """Set the program name used by sv_common.feedback when submitting to the Hub."""
    global _program_name
    _program_name = name


def get_program_name() -> str:
    """Return the program name (e.g. 'patt-guild-portal')."""
    return _program_name


# ---------------------------------------------------------------------------
# Phase 1.7-A — BIS daily email + patch probe getters
# ---------------------------------------------------------------------------


def get_smtp_config() -> Optional[SmtpConfig]:
    """Return SMTP config if all required fields are set, else None."""
    host = _cache.get("smtp_host")
    user = _cache.get("smtp_user")
    password = _cache.get("smtp_password_encrypted")  # caller decrypts
    from_addr = _cache.get("smtp_from_address")
    if not (host and user and password and from_addr):
        return None
    port = int(_cache.get("smtp_port") or 587)
    return SmtpConfig(host=host, port=port, user=user, password=password, from_address=from_addr)


def get_bis_report_email() -> Optional[str]:
    """Return the recipient address for daily BIS email reports, or None."""
    val = _cache.get("bis_report_email")
    return val if val else None


def get_bis_encounter_baseline() -> Optional[int]:
    """Return the cached raid encounter count baseline for the patch probe, or None."""
    val = _cache.get("bis_encounter_count")
    return int(val) if val is not None else None
