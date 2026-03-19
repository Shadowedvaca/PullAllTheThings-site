"""Service layer for guide site config.

Caches the guide_sites rows for 5 minutes. On admin save, call invalidate_cache()
so the next request picks up the new config.
"""

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sv_common.db.models import GuideSite
from sv_common.guide_links import build_link_for_site

_TTL = 300.0  # seconds
_cache: list[dict] | None = None
_cache_at: float = 0.0


def invalidate_cache() -> None:
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def get_enabled_sites(db: AsyncSession) -> list[dict]:
    """Return enabled guide site configs, ordered by sort_order. Cached for 5 min."""
    global _cache, _cache_at
    if _cache is not None and (time.monotonic() - _cache_at) < _TTL:
        return _cache
    result = await db.execute(
        select(GuideSite)
        .where(GuideSite.enabled == True)  # noqa: E712
        .order_by(GuideSite.sort_order, GuideSite.id)
    )
    rows = result.scalars().all()
    _cache = [
        {
            "id":                 s.id,
            "badge_label":        s.badge_label,
            "url_template":       s.url_template,
            "role_dps_slug":      s.role_dps_slug,
            "role_tank_slug":     s.role_tank_slug,
            "role_healer_slug":   s.role_healer_slug,
            "badge_bg_color":     s.badge_bg_color,
            "badge_text_color":   s.badge_text_color,
            "badge_border_color": s.badge_border_color or s.badge_bg_color,
        }
        for s in rows
    ]
    _cache_at = time.monotonic()
    return _cache


def build_links_for_spec(
    sites: list[dict],
    class_name: str,
    spec_name: str,
    role_name: str,
) -> list[dict]:
    """Build a badge-ready link list for one spec across all enabled sites.

    Returns a list ordered by site sort_order. Each entry contains everything
    the JS needs to render a badge: label, colors, and resolved URL.
    """
    return [
        {
            "site_id":            s["id"],
            "badge_label":        s["badge_label"],
            "badge_bg_color":     s["badge_bg_color"],
            "badge_text_color":   s["badge_text_color"],
            "badge_border_color": s["badge_border_color"],
            "url": build_link_for_site(
                url_template     = s["url_template"],
                class_name       = class_name,
                spec_name        = spec_name,
                role_name        = role_name,
                role_dps_slug    = s["role_dps_slug"],
                role_tank_slug   = s["role_tank_slug"],
                role_healer_slug = s["role_healer_slug"],
            ),
        }
        for s in sites
    ]
