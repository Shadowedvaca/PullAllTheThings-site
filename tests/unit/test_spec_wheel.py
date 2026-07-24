"""Unit coverage for the seasonal specialization wheel."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from guild_portal.api.spec_wheel_routes import (
    AssignCharacterRequest,
    SpinRequest,
    assign_rolled_character,
    spin_spec_wheel,
)
from guild_portal.api.spec_wheel_routes import filter_eligible_specs
from guild_portal.api.guild_routes import get_spec_wheel_results
from guild_portal.services.roster_needs_service import calculate_open_role_needs
from sv_common.db.models import SpecWheelRoll


SPECS = [
    {"id": 1, "name": "Protection", "role": "Tank"},
    {"id": 2, "name": "Holy", "role": "Healer"},
    {"id": 3, "name": "Retribution", "role": "Melee DPS"},
    {"id": 4, "name": "Balance", "role": "Ranged DPS"},
]


def test_no_filters_keep_every_spec_once():
    eligible = filter_eligible_specs(
        SPECS,
        {"Tank"},
        {1, 2},
        only_open_roles=False,
        only_unrepresented=False,
    )
    assert eligible == SPECS
    assert len({spec["id"] for spec in eligible}) == len(eligible)


def test_open_role_filter_keeps_all_specs_in_open_roles():
    eligible = filter_eligible_specs(
        SPECS,
        {"Tank", "Healer"},
        set(),
        only_open_roles=True,
        only_unrepresented=False,
    )
    assert [spec["id"] for spec in eligible] == [1, 2]


def test_unrepresented_filter_removes_existing_main_specs():
    eligible = filter_eligible_specs(
        SPECS,
        set(),
        {1, 4},
        only_open_roles=False,
        only_unrepresented=True,
    )
    assert [spec["id"] for spec in eligible] == [2, 3]


def test_filters_intersect():
    eligible = filter_eligible_specs(
        SPECS,
        {"Tank", "Healer"},
        {1},
        only_open_roles=True,
        only_unrepresented=True,
    )
    assert [spec["id"] for spec in eligible] == [2]


def test_default_role_targets():
    assert calculate_open_role_needs({}) == {
        "Tank": 2,
        "Healer": 4,
        "Melee DPS": 7,
        "Ranged DPS": 7,
    }


def test_melee_surplus_reduces_ranged_target():
    needs = calculate_open_role_needs(
        {"Tank": 2, "Healer": 4, "Melee DPS": 9, "Ranged DPS": 4}
    )
    assert needs == {"Ranged DPS": 1}


def test_ranged_surplus_reduces_melee_target():
    needs = calculate_open_role_needs(
        {"Tank": 2, "Healer": 4, "Melee DPS": 3, "Ranged DPS": 10}
    )
    assert needs == {"Melee DPS": 1}


def test_spec_wheel_model_has_summary_fields():
    assert SpecWheelRoll.__table_args__[-1]["schema"] == "patt"
    assert {column.name for column in SpecWheelRoll.__table__.columns} == {
        "id",
        "player_id",
        "season_id",
        "slot",
        "first_spec_id",
        "first_rolled_at",
        "latest_spec_id",
        "latest_rolled_at",
        "roll_count",
    }


def test_migration_is_chained_and_bounded():
    migration = (
        Path(__file__).parents[2] / "alembic" / "versions" / "0181_spec_wheel.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "0180"' in migration
    assert "UNIQUE (player_id, season_id, slot)" in migration
    assert "CHECK (roll_count >= 1)" in migration
    assert "first_spec_id" in migration
    assert "latest_spec_id" in migration


def test_app_registers_spec_wheel_routes():
    app_source = (
        Path(__file__).parents[2] / "src" / "guild_portal" / "app.py"
    ).read_text(encoding="utf-8")
    assert "app.include_router(spec_wheel_router)" in app_source
    assert "app.include_router(spec_wheel_page_router)" in app_source


def query_result(*, mapping=None, scalar=None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.mappings.return_value.one_or_none.return_value = mapping
    return result


def rows_result(rows):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_repeat_spin_requires_explicit_replacement():
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            query_result(
                mapping={
                    "id": 9,
                    "expansion_name": "Midnight",
                    "season_number": 1,
                }
            ),
            query_result(scalar=44),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await spin_spec_wheel(
            SpinRequest(slot="main"),
            db=db,
            player=SimpleNamespace(id=7),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "replacement_required"
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_assignment_rejects_character_from_wrong_class():
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            query_result(
                mapping={
                    "id": 9,
                    "expansion_name": "Midnight",
                    "season_number": 1,
                }
            ),
            query_result(mapping={"latest_spec_id": 102, "class_id": 11}),
            query_result(
                mapping={
                    "id": 55,
                    "class_id": 2,
                    "character_name": "Wrongclass",
                    "realm": "Sen'jin",
                    "level": 90,
                }
            ),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await assign_rolled_character(
            AssignCharacterRequest(slot="offspec", character_id=55),
            db=db,
            player=SimpleNamespace(id=7),
        )

    assert exc.value.status_code == 400
    assert "does not match" in exc.value.detail
    assert db.execute.await_count == 3


def test_character_query_uses_required_sort_order():
    source = (
        Path(__file__).parents[2]
        / "src"
        / "guild_portal"
        / "api"
        / "spec_wheel_routes.py"
    ).read_text(encoding="utf-8")
    order = (
        "ORDER BY wc.level DESC NULLS LAST,\n"
        "                     LOWER(wc.character_name),\n"
        "                     LOWER(COALESCE(wc.realm_name, wc.realm_slug))"
    )
    assert order in source


@pytest.mark.asyncio
async def test_public_wheel_results_match_character_to_assigned_spec():
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=rows_result(
            [
                {
                    "season_id": 9,
                    "expansion_name": "Midnight",
                    "season_number": 1,
                    "player_id": 7,
                    "display_name": "Trog",
                    "rank_name": "Guild Leader",
                    "slot": "main",
                    "first_id": 101,
                    "first_name": "Holy",
                    "first_class_name": "Priest",
                    "first_color_hex": "#FFFFFF",
                    "first_role": "Healer",
                    "latest_id": 202,
                    "latest_name": "Balance",
                    "latest_class_name": "Druid",
                    "latest_color_hex": "#FF7C0A",
                    "latest_role": "Ranged DPS",
                    "assigned_spec_id": 202,
                    "assigned_character_id": 55,
                    "assigned_character_name": "Trogmoon",
                    "assigned_realm": "Sen'jin",
                    "assigned_level": 90,
                },
                {
                    "season_id": 9,
                    "expansion_name": "Midnight",
                    "season_number": 1,
                    "player_id": 8,
                    "display_name": "Hit",
                    "rank_name": "Member",
                    "slot": None,
                    "first_id": None,
                    "first_name": None,
                    "first_class_name": None,
                    "first_color_hex": None,
                    "first_role": None,
                    "latest_id": None,
                    "latest_name": None,
                    "latest_class_name": None,
                    "latest_color_hex": None,
                    "latest_role": None,
                    "assigned_spec_id": None,
                    "assigned_character_id": None,
                    "assigned_character_name": None,
                    "assigned_realm": None,
                    "assigned_level": None,
                },
            ]
        )
    )

    payload = await get_spec_wheel_results(db=db)
    players = {
        player["display_name"]: player for player in payload["data"]["players"]
    }
    main = players["Trog"]["main"]
    assert payload["data"]["season"]["name"] == "Midnight Season 1"
    assert main["first"]["name"] == "Holy"
    assert main["latest"]["name"] == "Balance"
    assert main["assigned_spec_id"] == main["latest"]["id"]
    assert main["assigned_character"]["name"] == "Trogmoon"
    assert players["Hit"]["main"] is None
    assert players["Hit"]["offspec"] is None


def test_ui_uses_hits_name_and_refreshes_after_assignment():
    root = Path(__file__).parents[2]
    wheel_template = (
        root / "src" / "guild_portal" / "templates" / "member" / "spec_wheel.html"
    ).read_text(encoding="utf-8")
    wheel_script = (
        root / "src" / "guild_portal" / "static" / "js" / "spec_wheel.js"
    ).read_text(encoding="utf-8")
    roster_template = (
        root / "src" / "guild_portal" / "templates" / "public" / "roster.html"
    ).read_text(encoding="utf-8")
    assert "Hit's Wheel of Fate" in wheel_template
    assert "Hit's Wheel of Fate" in roster_template
    assert "await loadState(false);" in wheel_script
    assert "const matches = slot.assigned_spec_id === result.id;" in roster_template
    assert 'fate-match fate-match--yes' in roster_template
    assert 'fate-match fate-match--no' in roster_template
    assert "Matches roster specialization" in roster_template
    assert "Does not match roster specialization" in roster_template
    roster_needs = '<h2 class="comp-section-title">Roster Needs</h2>'
    wheel_results = '<h2 class="comp-section-title">Hit\'s Wheel of Fate</h2>'
    assert roster_template.index(roster_needs) < roster_template.index(wheel_results)


def test_wheel_page_and_mutating_api_require_login():
    root = Path(__file__).parents[2]
    page_source = (
        root / "src" / "guild_portal" / "pages" / "spec_wheel_pages.py"
    ).read_text(encoding="utf-8")
    api_source = (
        root / "src" / "guild_portal" / "api" / "spec_wheel_routes.py"
    ).read_text(encoding="utf-8")
    assert 'RedirectResponse(url="/login?next=/spec-wheel"' in page_source
    assert api_source.count("Depends(get_current_player)") == 3


def test_site_navigation_uses_shared_hamburger_menu():
    root = Path(__file__).parents[2]
    base = (
        root / "src" / "guild_portal" / "templates" / "base.html"
    ).read_text(encoding="utf-8")
    base_admin = (
        root / "src" / "guild_portal" / "templates" / "base_admin.html"
    ).read_text(encoding="utf-8")
    partial = (
        root / "src" / "guild_portal" / "templates" / "partials" / "site_menu.html"
    ).read_text(encoding="utf-8")
    assert 'include "partials/site_menu.html"' in base
    assert 'include "partials/site_menu.html"' in base_admin
    assert 'aria-label="Open site menu"' in partial
    assert "Hit's Wheel of Fate" in partial


def test_profile_shows_first_and_latest_wheel_notes():
    template = (
        Path(__file__).parents[2]
        / "src"
        / "guild_portal"
        / "templates"
        / "profile"
        / "settings.html"
    ).read_text(encoding="utf-8")
    assert "Wheel History" in template
    assert "main_wheel.first" in template
    assert "main_wheel.latest" in template
    assert "offspec_wheel.first" in template
    assert "offspec_wheel.latest" in template
