"""Pure URL builder for external WoW guide sites.

All functions are stateless and take only plain values — no DB, no async.
The service layer (guild_portal.services.guide_links_service) handles loading
site config and calling these functions.
"""


def _slug(name: str) -> str:
    """Convert a display name to a lowercase hyphenated URL slug."""
    return name.lower().replace(" ", "-")


def _resolve_role_slug(
    role_name: str,
    dps_slug: str,
    tank_slug: str,
    healer_slug: str,
) -> str:
    """Pick the correct site-specific role slug from the DB role name."""
    r = role_name.lower()
    if "tank" in r:
        return tank_slug
    if "heal" in r:
        return healer_slug
    return dps_slug  # covers Melee DPS, Ranged DPS, Support


def build_link_for_site(
    url_template: str,
    class_name: str,
    spec_name: str,
    role_name: str,
    role_dps_slug: str,
    role_tank_slug: str,
    role_healer_slug: str,
) -> str:
    """Return the URL for one guide site given spec and role metadata.

    Template placeholders: {class}, {spec}, {role}. Sites without {role}
    (e.g. u.gg) are unaffected — str.replace on a missing placeholder is a no-op.
    """
    cls = _slug(class_name)
    spec = _slug(spec_name)
    role = _resolve_role_slug(role_name, role_dps_slug, role_tank_slug, role_healer_slug)
    return (
        url_template
        .replace("{class}", cls)
        .replace("{spec}", spec)
        .replace("{role}", role)
    )
