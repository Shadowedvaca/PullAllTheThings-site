"""Unit tests for db_sync.py — Blizzard roster sync with stable character ID."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from sv_common.guild_sync.blizzard_client import CharacterProfileData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool():
    """Return a (pool, conn) pair of mocks wired for async context manager."""
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    # conn.transaction() must return an async context manager, not a coroutine.
    # AsyncMock makes all methods async, so override transaction as a plain MagicMock.
    txn_ctx = MagicMock()
    txn_ctx.__aenter__ = AsyncMock(return_value=None)
    txn_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_ctx)
    conn.fetch.return_value = []
    return pool, conn


def _char(
    name="Testchar",
    realm="senjin",
    cls="Druid",
    spec="Balance",
    level=80,
    item_level=600,
    guild_rank=3,
    blizzard_id=12345,
):
    return CharacterProfileData(
        character_name=name,
        realm_slug=realm,
        realm_name="Sen'jin",
        character_class=cls,
        active_spec=spec,
        level=level,
        item_level=item_level,
        guild_rank=guild_rank,
        blizzard_character_id=blizzard_id,
    )


# ---------------------------------------------------------------------------
# sync_blizzard_roster — basic update/insert paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_character_updated_by_name():
    """Character already in DB (no blizzard_id yet) matched by name+realm and updated."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "LOWER(character_name)" in query:
            return {"id": 42, "character_name": "Testchar", "realm_slug": "senjin", "removed_at": None}
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 99}],  # _build_rank_index_map
        [{"id": 42, "character_name": "Testchar", "realm_slug": "senjin"}],  # all_active
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Testchar", blizzard_id=None)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1
    assert stats["new"] == 0


@pytest.mark.asyncio
async def test_new_character_inserted():
    """Character not in DB → new row inserted with blizzard_character_id."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "blizzard_character_id = $1" in query:
            return None
        if "LOWER(character_name)" in query:
            return None  # no existing row
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 99}],  # _build_rank_index_map
        [],  # all_active — nothing to remove
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Newcomer", blizzard_id=55555)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["new"] == 1
    assert stats["updated"] == 0

    # Verify INSERT was called with blizzard_character_id
    insert_calls = [c for c in conn.execute.call_args_list if "INSERT" in str(c)]
    assert len(insert_calls) == 1
    assert 55555 in insert_calls[0].args


# ---------------------------------------------------------------------------
# sync_blizzard_roster — rename detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_detected_updates_name_and_records_history():
    """When stable ID matches but name differs, old name is written to history and row updated."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "guild_ranks" in query:
            return None
        if "blizzard_character_id = $1" in query:
            # Stable ID lookup: returns old name
            return {
                "id": 42,
                "character_name": "Oldname",
                "realm_slug": "senjin",
                "removed_at": None,
            }
        if "LOWER(character_name)" in query:
            return None  # shouldn't be reached in rename path
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        # _build_rank_index_map
        [{"wow_rank_index": 3, "id": 99}],
        # all_active rows: return the NOW-updated name so removal check passes
        [{"id": 42, "character_name": "Newname", "realm_slug": "senjin"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    # Character has blizzard_id=99999 but new name "Newname"
    chars = [_char(name="Newname", blizzard_id=99999)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1
    assert stats["removed"] == 0  # Should NOT be marked removed

    # Verify history INSERT was called
    history_inserts = [
        c for c in conn.execute.call_args_list
        if "character_name_history" in str(c)
    ]
    assert len(history_inserts) == 1
    history_args = history_inserts[0].args
    assert "Oldname" in history_args  # old name recorded


@pytest.mark.asyncio
async def test_same_name_same_stable_id_no_history_written():
    """Stable ID matches and name is unchanged — no history entry created."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "guild_ranks" in query:
            return None
        if "blizzard_character_id = $1" in query:
            return {
                "id": 42,
                "character_name": "Samename",
                "realm_slug": "senjin",
                "removed_at": None,
            }
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 99}],
        [{"id": 42, "character_name": "Samename", "realm_slug": "senjin"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Samename", blizzard_id=99999)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1

    history_inserts = [
        c for c in conn.execute.call_args_list
        if "character_name_history" in str(c)
    ]
    assert len(history_inserts) == 0


@pytest.mark.asyncio
async def test_renamed_character_not_marked_removed():
    """A renamed character (detected via stable ID) is NOT counted as removed."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "guild_ranks" in query:
            return None
        if "blizzard_character_id = $1" in query:
            return {
                "id": 7,
                "character_name": "Wyland",
                "realm_slug": "senjin",
                "removed_at": None,
            }
        if "classes" in query:
            return {"id": 10}
        if "specializations" in query:
            return {"id": 5}
        return None

    conn.fetch.side_effect = [
        # rank map
        [{"wow_rank_index": 2, "id": 50}],
        # all_active: row is now named "Wylandmonk" after update
        [{"id": 7, "character_name": "Wylandmonk", "realm_slug": "senjin"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Wylandmonk", blizzard_id=777777)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["removed"] == 0
    assert stats["updated"] == 1


# ---------------------------------------------------------------------------
# sync_blizzard_roster — character without stable ID falls back to name+realm
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# sync_blizzard_roster — stale-row eviction before rename/realm-transfer UPDATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_evicts_stale_removed_row():
    """Rename detected + stale removed row at target name/realm → row deleted before UPDATE."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    fetchrow_calls = []

    async def fake_fetchrow(query, *args):
        fetchrow_calls.append((query, args))
        if "blizzard_character_id = $1" in query:
            return {"id": 10, "character_name": "Oldname", "realm_slug": "bloodscalp", "removed_at": None}
        if "LOWER(character_name)" in query and "id != $3" in query:
            # Conflict check: stale removed row with target name+realm
            from datetime import datetime, timezone
            return {"id": 99, "removed_at": datetime(2025, 1, 1, tzinfo=timezone.utc)}
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 50}],  # _build_rank_index_map
        [{"id": 10, "character_name": "Maplehoof", "realm_slug": "bloodscalp"}],  # all_active
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Maplehoof", realm="bloodscalp", blizzard_id=42)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1

    # Stale row id=99 must have been deleted
    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert len(delete_calls) == 1
    assert 99 in delete_calls[0].args


@pytest.mark.asyncio
async def test_realm_transfer_back_evicts_stale_removed_row():
    """Character transfers back to original realm; stale removed row evicted before UPDATE."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "blizzard_character_id = $1" in query:
            # Found by stable ID, but currently on a different realm
            return {"id": 10, "character_name": "Maplehoof", "realm_slug": "other-realm", "removed_at": None}
        if "LOWER(character_name)" in query and "id != $3" in query:
            # Conflict check: stale removed row on bloodscalp
            from datetime import datetime, timezone
            return {"id": 77, "removed_at": datetime(2025, 6, 1, tzinfo=timezone.utc)}
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 50}],
        [{"id": 10, "character_name": "Maplehoof", "realm_slug": "bloodscalp"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Maplehoof", realm="bloodscalp", blizzard_id=42)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1

    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert len(delete_calls) == 1
    assert 77 in delete_calls[0].args


@pytest.mark.asyncio
async def test_no_conflict_no_eviction():
    """Rename detected but no conflicting row exists — no DELETE issued."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "blizzard_character_id = $1" in query:
            return {"id": 10, "character_name": "Oldname", "realm_slug": "senjin", "removed_at": None}
        if "LOWER(character_name)" in query and "id != $3" in query:
            return None  # no conflict
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 50}],
        [{"id": 10, "character_name": "Newname", "realm_slug": "senjin"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Newname", realm="senjin", blizzard_id=42)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1
    delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in str(c)]
    assert len(delete_calls) == 0


@pytest.mark.asyncio
async def test_no_name_or_realm_change_skips_conflict_check():
    """Stable ID found, name and realm unchanged — conflict check query not issued."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    conflict_queries = []

    async def fake_fetchrow(query, *args):
        if "blizzard_character_id = $1" in query:
            return {"id": 10, "character_name": "Testchar", "realm_slug": "senjin", "removed_at": None}
        if "id != $3" in query:
            conflict_queries.append(query)
            return None
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 50}],
        [{"id": 10, "character_name": "Testchar", "realm_slug": "senjin"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Testchar", realm="senjin", blizzard_id=42)]
    await sync_blizzard_roster(pool, chars)

    assert len(conflict_queries) == 0


@pytest.mark.asyncio
async def test_no_blizzard_id_falls_back_to_name_lookup():
    """Character with no blizzard_character_id still matches by name+realm."""
    from sv_common.guild_sync.db_sync import sync_blizzard_roster

    pool, conn = _make_pool()

    async def fake_fetchrow(query, *args):
        if "guild_ranks" in query:
            return None
        if "LOWER(character_name)" in query:
            return {"id": 10, "character_name": "Noida", "realm_slug": "senjin", "removed_at": None}
        if "classes" in query:
            return {"id": 11}
        if "specializations" in query:
            return {"id": 3}
        return None

    conn.fetch.side_effect = [
        [{"wow_rank_index": 3, "id": 99}],
        [{"id": 10, "character_name": "Noida", "realm_slug": "senjin"}],
    ]
    conn.fetchrow.side_effect = fake_fetchrow

    chars = [_char(name="Noida", blizzard_id=None)]
    stats = await sync_blizzard_roster(pool, chars)

    assert stats["updated"] == 1
    assert stats["new"] == 0
