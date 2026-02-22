"""PATT platform application factory."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from patt.config import get_settings
from sv_common.db.engine import get_engine, get_session_factory
from sv_common.db.seed import seed_ranks

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting PATT platform (env=%s)", settings.app_env)
        # Seed default ranks if needed
        factory = get_session_factory(settings.database_url)
        async with factory() as session:
            try:
                await seed_ranks(session)
            except Exception as exc:
                logger.warning("Seed skipped: %s", exc)
        yield
        engine = get_engine(settings.database_url)
        await engine.dispose()
        logger.info("PATT platform shutdown complete")

    app = FastAPI(
        title="Pull All The Things Guild Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Register routes
    from patt.api.health import router as health_router
    from patt.api.admin_routes import router as admin_router
    from patt.api.guild_routes import router as guild_router

    app.include_router(health_router, prefix="/api")
    app.include_router(admin_router)
    app.include_router(guild_router)

    return app


# Module-level app instance for uvicorn direct import (non-factory mode)
# Use create_app() factory for tests and development server.
