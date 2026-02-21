"""Health check endpoint."""

from fastapi import APIRouter
from sqlalchemy import text

from patt.config import get_settings
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
    except Exception as exc:
        db_status = f"error: {exc}"

    return {
        "ok": True,
        "data": {
            "db": db_status,
            "version": "0.1.0",
        },
    }
