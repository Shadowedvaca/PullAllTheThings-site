"""PostgreSQL integration coverage for admin-only provider filtering."""

import pytest

from guild_portal.services.gear_plan_service import _resolve_member_bis_source
from guild_portal.services.guide_links_service import get_enabled_sites, invalidate_cache
from sv_common.db.models import GuideSite


@pytest.mark.asyncio
async def test_member_source_resolution_excludes_hidden_origins(guild_sync_pool):
    hidden_id = 990001
    visible_id = 990002
    async with guild_sync_pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO ref.bis_list_sources
                (id, name, short_label, origin, content_type,
                 is_default, is_active, sort_order)
            VALUES ($1, $2, $3, $4, 'overall', $5, TRUE, $6)
            ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    origin = EXCLUDED.origin,
                    is_default = EXCLUDED.is_default,
                    is_active = TRUE,
                    sort_order = EXCLUDED.sort_order
            """,
            [
                (hidden_id, "Integration U.GG", "U.GG", "ugg", True, 1),
                (visible_id, "Integration Wowhead", "Wowhead", "wowhead", True, 2),
            ],
        )
        try:
            source = await _resolve_member_bis_source(conn, hidden_id)
            assert source is not None
            assert source["id"] == visible_id

            assert await _resolve_member_bis_source(
                conn, hidden_id, allow_fallback=False
            ) is None
        finally:
            await conn.execute(
                "DELETE FROM ref.bis_list_sources WHERE id = ANY($1::int[])",
                [hidden_id, visible_id],
            )


@pytest.mark.asyncio
async def test_member_guide_query_excludes_hidden_sites(db_session):
    db_session.add_all(
        [
            GuideSite(
                name="Icy Veins",
                badge_label="IV",
                url_template="https://www.icy-veins.com/wow/{spec}-{class}",
                role_dps_slug="dps",
                role_tank_slug="tank",
                role_healer_slug="healing",
                slug_separator="-",
                badge_bg_color="#000000",
                badge_text_color="#ffffff",
                enabled=True,
                sort_order=1,
            ),
            GuideSite(
                name="Integration Wowhead",
                badge_label="Wowhead",
                url_template="https://www.wowhead.com/guide/classes/{class}/{spec}",
                role_dps_slug="dps",
                role_tank_slug="tank",
                role_healer_slug="healer",
                slug_separator="-",
                badge_bg_color="#000000",
                badge_text_color="#ffffff",
                enabled=True,
                sort_order=2,
            ),
        ]
    )
    await db_session.flush()
    invalidate_cache()

    sites = await get_enabled_sites(db_session)

    labels = [site["badge_label"] for site in sites]
    assert "IV" not in labels
    assert "Wowhead" in labels
    invalidate_cache()
