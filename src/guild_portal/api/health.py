"""Health check endpoint."""

from fastapi import APIRouter
from sqlalchemy import text

from guild_portal.config import get_settings
from guild_portal.version import APP_VERSION
from sv_common.db.engine import get_session_factory

router = APIRouter()


@router.get("/health")
async def health_check():
    settings = get_settings()
    db_status = "disconnected"
    try:
        factory = get_session_factory(settings.database_url)
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "error"

    return {
        "ok": db_status == "connected",
        "data": {
            "db": db_status,
            "environment": settings.app_env,
            "version": APP_VERSION,
            "commit": settings.commit_sha,
        },
    }
