"""Visibility policy for BIS and external guide providers.

These providers remain available to Gear Plan Admin for diagnostics and future
restoration work. Product-facing services and scheduled ingestion must exclude
them until a supported automated retrieval path is implemented.
"""

from __future__ import annotations


HIDDEN_BIS_SOURCE_ORIGINS: tuple[str, ...] = (
    "archon",
    "icy_veins",
    "ugg",
)

HIDDEN_GUIDE_SITE_NAMES: tuple[str, ...] = (
    "Archon",
    "Archon.gg",
    "Icy Veins",
    "U.GG",
    "u.gg",
)


def is_hidden_bis_source(origin: str | None) -> bool:
    """Return whether a BIS source is admin-only."""
    return bool(origin and origin in HIDDEN_BIS_SOURCE_ORIGINS)


def is_hidden_guide_site(name: str | None) -> bool:
    """Return whether an external guide site is admin-only."""
    return bool(name and name in HIDDEN_GUIDE_SITE_NAMES)
