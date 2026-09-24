"""Execution-path coverage for member-visible provider policy boundaries."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from guild_portal.api import admin_routes, bis_routes, gear_plan_routes
from guild_portal.services import season_service
from guild_portal.services.gear_plan_service import (
    _contextual_sources,
    _equipped_matches_goal,
    _normalize_legacy_catalyst_goals,
    _recommendation_matches_goal,
    get_or_create_plan,
    update_plan_config,
    update_slot,
)
from sv_common.guild_sync.bis_sync import (
    _resolve_active_tier_result,
    stage_active_bis_item_metadata,
)
from sv_common.guild_sync.simc_parser import SimcSlot


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _response_json(response):
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_member_routes_apply_fallback_and_reject_hidden_source_updates():
    player = SimpleNamespace(id=7)
    request = MagicMock()
    request.json = AsyncMock(
        return_value={
            "bis_source_id": 30,
            "blizzard_item_id": 271457,
            "recommendation_type": "catalyst",
            "catalyst_base_item_id": 251214,
            "catalyst_base_item_name": "Bonds of the Hash'ura",
        }
    )
    pool = MagicMock()

    with (
        patch.object(
            gear_plan_routes, "_verify_ownership", AsyncMock(return_value=True)
        ),
        patch.object(gear_plan_routes, "_get_pool", AsyncMock(return_value=pool)),
        patch.object(
            gear_plan_routes.svc,
            "get_or_create_plan",
            AsyncMock(
                side_effect=[
                    {"id": 1, "_member_source_fallback_applied": True},
                    {"id": 1, "_member_source_fallback_applied": True},
                ]
            ),
        ),
        patch.object(
            gear_plan_routes.svc, "populate_from_bis", AsyncMock()
        ) as populate,
        patch.object(
            gear_plan_routes.svc,
            "get_plan_detail",
            AsyncMock(return_value={"plan": {"id": 1}}),
        ),
        patch.object(
            gear_plan_routes.svc,
            "update_plan_config",
            AsyncMock(side_effect=ValueError("BIS source is not available")),
        ),
        patch.object(
            gear_plan_routes.svc,
            "update_slot",
            AsyncMock(
                side_effect=ValueError("Catalyst goals require a distinct base item")
            ),
        ),
        patch.object(gear_plan_routes.svc, "WOW_SLOTS", {"hands"}),
    ):
        get_response = await gear_plan_routes.get_gear_plan(
            11, request, current_player=player, db=MagicMock()
        )
        create_response = await gear_plan_routes.create_gear_plan(
            11, request, current_player=player, db=MagicMock()
        )
        config_response = await gear_plan_routes.update_plan_config(
            11, request, current_player=player, db=MagicMock()
        )
        slot_response = await gear_plan_routes.update_slot(
            11, "hands", request, current_player=player, db=MagicMock()
        )

    assert _response_json(get_response)["ok"] is True
    assert _response_json(create_response)["data"]["plan"]["id"] == 1
    assert _response_json(config_response)["error"] == "BIS source is not available"
    assert _response_json(slot_response)["error"].startswith("Catalyst goals")
    assert populate.await_count == 2


@pytest.mark.asyncio
async def test_season_update_normalizes_nullable_configuration():
    db = MagicMock()
    seasons = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    result = MagicMock()
    result.scalars.return_value.all.return_value = seasons
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    season = SimpleNamespace(id=2)

    assert await season_service.get_all_seasons(db) == seasons
    updated = await season_service.update_season(
        db,
        season,
        {
            "is_active": True,
            "current_raid_ids": [],
            "current_instance_ids": [],
            "tier_set_ids": None,
            "quality_ilvl_map": {},
            "crafted_ilvl_map": {},
        },
    )

    assert updated.is_active is True
    assert updated.current_raid_ids is None
    assert updated.current_instance_ids is None
    assert updated.tier_set_ids == []
    assert updated.quality_ilvl_map is None
    assert updated.crafted_ilvl_map is None
    assert db.execute.await_count == 2
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_rejects_hidden_source_and_validates_catalyst_routes():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire(conn))

    with pytest.raises(ValueError, match="BIS source is not available"):
        await update_plan_config(pool, 1, 2, bis_source_id=30)

    conn.fetchrow = AsyncMock(return_value={"id": 9})
    with pytest.raises(ValueError, match="must be direct or catalyst"):
        await update_slot(pool, 1, 2, "hands", 100, recommendation_type="other")
    with pytest.raises(ValueError, match="distinct base item"):
        await update_slot(
            pool,
            1,
            2,
            "hands",
            100,
            recommendation_type="catalyst",
            catalyst_base_item_id=100,
        )

    conn.fetchrow = AsyncMock(
        side_effect=[{"id": 9}, {"name": "Catalyst Base"}, {"is_locked": True}]
    )
    assert await update_slot(
        pool,
        1,
        2,
        "hands",
        200,
        item_name="Tier Result",
        recommendation_type="catalyst",
        catalyst_base_item_id=100,
    )
    assert conn.execute.await_args.args[-3:] == ("catalyst", 100, "Catalyst Base")


@pytest.mark.asyncio
async def test_new_plan_uses_member_visible_default_source():
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            None,
            {"active_spec_id": 73},
            {"id": 20, "name": "Wowhead", "origin": "wowhead"},
            {
                "id": 99,
                "player_id": 1,
                "character_id": 2,
                "spec_id": 73,
                "hero_talent_id": None,
                "bis_source_id": 20,
                "simc_profile": None,
                "is_active": True,
                "simc_imported_at": None,
                "equipped_source": None,
            },
        ]
    )
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire(conn))

    plan = await get_or_create_plan(pool, 1, 2)

    assert plan["spec_id"] == 73
    assert plan["bis_source_id"] == 20


@pytest.mark.asyncio
async def test_active_tier_result_requires_one_candidate():
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"blizzard_item_id": 1}, {"blizzard_item_id": 2}]
    )

    assert await _resolve_active_tier_result(conn, 73, "hands") is None


@pytest.mark.asyncio
async def test_admin_updates_use_provider_and_season_policy_filters():
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    site_response = await admin_routes.update_guide_site(
        1, admin_routes.GuideSiteUpdate(enabled=False), db=db
    )
    assert site_response == {"ok": False, "error": "Guide site not found"}

    season = SimpleNamespace(
        id=2,
        expansion_name="Midnight",
        season_number=2,
        display_name="Season 2",
        start_date=date(2026, 8, 18),
        is_new_expansion=False,
        is_active=True,
        blizzard_mplus_season_id=18,
        current_raid_ids=[1317, 1320],
        current_instance_ids=[1],
        tier_set_ids=[2055],
        quality_ilvl_map={},
        crafted_ilvl_map={},
    )
    result.scalar_one_or_none.return_value = season
    db.commit = AsyncMock()
    with patch.object(
        admin_routes.season_service,
        "update_season",
        AsyncMock(return_value=season),
    ) as update:
        response = await admin_routes.update_season(
            2, admin_routes.SeasonUpdate(display_name="Season 2"), db=db
        )
    assert response["ok"] is True
    update.assert_awaited_once()


@pytest.mark.asyncio
async def test_enrich_pipeline_stages_guide_items_before_rebuild():
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={"items": 10, "sources": 20, "missing_icons": 0}
    )
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire(conn))
    request = MagicMock()
    client = MagicMock()
    tasks = []
    bis_routes._enrich_classify_status = {"running": False}

    def capture_task(coro):
        tasks.append(coro)
        return MagicMock()

    with (
        patch.object(bis_routes, "_pool", return_value=pool),
        patch.object(
            bis_routes, "_get_blizzard_client", AsyncMock(return_value=client)
        ),
        patch("asyncio.create_task", side_effect=capture_task),
        patch(
            "sv_common.guild_sync.bis_sync.stage_active_bis_item_metadata",
            AsyncMock(return_value={"staged": 2, "missing": 3, "errors": ["one"]}),
        ) as stage,
        patch(
            "sv_common.guild_sync.item_source_sync.sync_current_season_tier_token_attrs",
            AsyncMock(return_value={"tokens_found": 4}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync.rebuild_bis_from_landing",
            AsyncMock(return_value={"bis_entries_inserted": 5}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync.rebuild_trinket_ratings_from_landing",
            AsyncMock(return_value={"trinket_ratings_inserted": 6}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync.rebuild_item_popularity_from_landing",
            AsyncMock(return_value={"rows_inserted": 7}),
        ),
    ):
        response = await bis_routes.enrich_and_classify(request, player=MagicMock())
        await tasks[0]

    assert response == {"ok": True, "started": True}
    stage.assert_awaited_once_with(pool, client)
    assert bis_routes._enrich_classify_status["phase_label"] == "Complete"
    assert "2 guide items staged" in bis_routes._enrich_classify_status["detail"]


def test_catalyst_helper_guard_paths_and_contextual_source():
    assert not _recommendation_matches_goal({}, None)
    assert not _equipped_matches_goal(None, {"blizzard_item_id": 1})

    desired = {
        "hands": {"blizzard_item_id": 1, "recommendation_type": "catalyst"},
        "head": {"blizzard_item_id": None},
        "chest": {"blizzard_item_id": 2},
    }
    _normalize_legacy_catalyst_goals(desired, {}, None)
    _normalize_legacy_catalyst_goals(desired, {}, 9)
    assert desired["chest"]["blizzard_item_id"] == 2

    catalyst = {"instance_type": "catalyst", "display_name": "Revival Catalyst"}
    assert _contextual_sources([catalyst], ["M"]) == [catalyst]


@pytest.mark.asyncio
async def test_metadata_staging_parses_every_supported_provider():
    rows = [
        {
            "source": source,
            "content": "{}",
            "url": f"https://example.test/{source}",
            "source_id": index,
            "spec_id": 73,
            "content_type": "overall",
        }
        for index, source in enumerate(
            ["wowhead", "method", "icy_veins", "archon", "unsupported"], start=1
        )
    ]
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[rows, []])
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire(conn))
    client = MagicMock()
    client.get_item = AsyncMock(side_effect=lambda item_id: {"id": item_id})

    parsed = {
        "wowhead": [SimcSlot("head", 101)],
        "method": [SimcSlot("hands", 102)],
        "icy_veins": [SimcSlot("feet", 103, catalyst_tier_item_id=203)],
        "archon": [SimcSlot("chest", 104)],
    }

    with (
        patch(
            "sv_common.guild_sync.bis_sync._load_slot_labels",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_wowhead_invtypes",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_raid_instance_names",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._parse_wowhead_html",
            return_value=(parsed["wowhead"], []),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._resolve_method_bis_from_db",
            AsyncMock(return_value=parsed["method"]),
        ),
        patch("sv_common.guild_sync.bis_sync._iv_parse_sections", return_value=[]),
        patch(
            "sv_common.guild_sync.bis_sync._resolve_iv_section",
            AsyncMock(return_value=parsed["icy_veins"]),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._parse_archon_page",
            return_value=(parsed["archon"], []),
        ),
    ):
        result = await stage_active_bis_item_metadata(pool, client)

    assert result == {"referenced": 5, "missing": 5, "staged": 5, "errors": []}
    assert client.get_item.await_count == 5
    assert conn.execute.await_count == 5


@pytest.mark.asyncio
async def test_metadata_staging_reports_parser_failure_without_replacing_cache():
    row = {
        "source": "wowhead",
        "content": "broken",
        "url": "https://example.test/wowhead",
        "source_id": 1,
        "spec_id": 73,
        "content_type": "overall",
    }
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[row])
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire(conn))

    with (
        patch(
            "sv_common.guild_sync.bis_sync._load_slot_labels",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_wowhead_invtypes",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._load_raid_instance_names",
            AsyncMock(return_value={}),
        ),
        patch(
            "sv_common.guild_sync.bis_sync._parse_wowhead_html",
            side_effect=ValueError("invalid guide"),
        ),
    ):
        result = await stage_active_bis_item_metadata(pool, MagicMock())

    assert result["referenced"] == 0
    assert result["staged"] == 0
    assert "invalid guide" in result["errors"][0]
