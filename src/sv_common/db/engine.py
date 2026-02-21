"""Async database engine and session factory."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


_engine = None
_session_factory = None


def get_engine(database_url: str):
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url, echo=False)
    return _engine


def get_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(database_url)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def get_db(database_url: str) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a database session per request."""
    factory = get_session_factory(database_url)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
