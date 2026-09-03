"""
Integration test fixtures for guild_identity (guild sync) tests.

Provides a `guild_db` fixture that truncates all guild_identity tables
before each test and yields the asyncpg pool — ensuring test isolation.
"""

import pytest_asyncio


async def _clean_guild_sync_tables(pool):
    """Reset raw-SQL test data, including shared rank/class references."""
    async with pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE
                common.guild_ranks,
                ref.classes,
                guild_identity.players,
                guild_identity.wow_characters,
                guild_identity.discord_users,
                guild_identity.player_characters,
                guild_identity.audit_issues,
                guild_identity.sync_log
            CASCADE
        """)


@pytest_asyncio.fixture
async def guild_db(guild_sync_pool):
    """
    Clean asyncpg pool for guild_identity integration tests.

    Truncates all guild_identity tables before each test, then yields the pool.
    Tests should use this instead of `guild_sync_pool` directly so that
    each test starts with a clean slate.
    """
    await _clean_guild_sync_tables(guild_sync_pool)
    try:
        yield guild_sync_pool
    finally:
        await _clean_guild_sync_tables(guild_sync_pool)
