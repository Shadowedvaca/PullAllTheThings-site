"""Shared pytest fixtures for the PATT platform test suite."""

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Test database URL — separate from production
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://patt_user:CHANGEME@localhost:5432/patt_test_db",
)

# Override settings before any app import
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("APP_ENV", "testing")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create test database tables once per session.

    Skips all dependent tests if the test database is not available.
    Set TEST_DATABASE_URL env var to point to a running PostgreSQL instance.
    """
    from sqlalchemy import text as sa_text
    from sv_common.db.models import Base

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # Verify connectivity before doing anything else
    try:
        async with engine.connect() as conn:
            await conn.execute(sa_text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Test database not available ({TEST_DATABASE_URL}): {exc}")

    async with engine.begin() as conn:
        await conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS common"))
        await conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS patt"))
        await conn.execute(sa_text("CREATE SCHEMA IF NOT EXISTS guild_identity"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Per-test session that rolls back after each test."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            yield session
            await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client with database session override."""
    from patt.app import create_app
    from patt.deps import get_db

    app = create_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def admin_member(db_session: AsyncSession):
    """Creates a Guild Leader rank member for admin tests."""
    from sv_common.db.models import GuildMember, GuildRank

    rank = GuildRank(name="Guild Leader", level=5, description="Guild master")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(
        discord_username="trog",
        display_name="Trog",
        discord_id="111111111111111111",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest_asyncio.fixture
async def veteran_member(db_session: AsyncSession):
    """Creates a Veteran rank member for standard voting tests."""
    from sv_common.db.models import GuildMember, GuildRank

    rank = GuildRank(name="Veteran", level=3, description="Veteran member")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(
        discord_username="veteran_user",
        display_name="Veteran",
        discord_id="222222222222222222",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest_asyncio.fixture
async def initiate_member(db_session: AsyncSession):
    """Creates an Initiate rank member for permission denial tests."""
    from sv_common.db.models import GuildMember, GuildRank

    rank = GuildRank(name="Initiate", level=1, description="New member")
    db_session.add(rank)
    await db_session.flush()

    member = GuildMember(
        discord_username="initiate_user",
        display_name="Initiate",
        discord_id="333333333333333333",
        rank_id=rank.id,
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest.fixture
def mock_discord_bot():
    """Mocked Discord bot that captures sent DMs and channel messages."""
    bot = MagicMock()
    bot.send_dm = AsyncMock(return_value=True)
    bot.send_channel_message = AsyncMock(return_value=MagicMock(id="999999999999999999"))
    bot.sent_dms = []
    bot.sent_messages = []
    return bot


@pytest_asyncio.fixture(scope="session")
async def guild_sync_pool(test_engine):
    """
    Asyncpg connection pool for guild_identity tests.

    Depends on test_engine to ensure the schema is already created.
    Uses a raw asyncpg DSN (no SQLAlchemy dialect prefix).
    """
    raw_dsn = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        pool = await asyncpg.create_pool(raw_dsn, min_size=1, max_size=5)
    except Exception as exc:
        pytest.skip(f"asyncpg pool unavailable for guild_sync tests: {exc}")

    yield pool

    await pool.close()
