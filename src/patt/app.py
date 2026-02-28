"""PATT platform application factory."""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import asyncpg
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from patt.config import get_settings
from sv_common.db.engine import get_engine, get_session_factory
from sv_common.db.seed import seed_ranks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# Surface discord.py logs at WARNING and above in production
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Rate limiter (in-process, simple sliding window)
# ---------------------------------------------------------------------------

# Maps IP → list of timestamps of recent requests
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX = 10       # max attempts
_RATE_LIMIT_WINDOW = 60    # seconds


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW
    hits = _rate_limit_store[ip]
    # Remove entries outside the window
    hits[:] = [t for t in hits if t >= window_start]
    if len(hits) >= _RATE_LIMIT_MAX:
        return False
    hits.append(now)
    return True


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds standard security headers to every response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' https://drive.google.com https://lh3.googleusercontent.com data:; "
                "connect-src 'self'; "
                "frame-ancestors 'none';"
            ),
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response
LEGACY_DIR = Path(__file__).parent / "static" / "legacy"
TEMPLATES_DIR = Path(__file__).parent / "templates"


async def _run_campaign_checker(database_url: str) -> None:
    """Wrapper that starts the campaign status checker with its own session factory."""
    from patt.services.campaign_service import check_campaign_statuses

    factory = get_session_factory(database_url)
    await check_campaign_statuses(factory)


async def _run_contest_agent(database_url: str) -> None:
    """Wrapper that starts the contest agent with its own session factory."""
    from patt.services.contest_agent import run_contest_agent

    factory = get_session_factory(database_url)
    await run_contest_agent(factory)


async def _auto_book_loop(pool: asyncpg.Pool) -> None:
    """Background loop: checks every 5 minutes for events to auto-book."""
    from patt.services.raid_booking_service import check_and_auto_book, POLL_INTERVAL_SECONDS
    logger.info("Auto-booking scheduler started")
    while True:
        try:
            await check_and_auto_book(pool)
        except Exception as e:
            logger.error("Auto-booking loop error: %s", e, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


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

        # Start the Discord bot in a background task (skipped if no token)
        bot_task = None
        if settings.discord_bot_token:
            from sv_common.discord.bot import start_bot
            bot_task = asyncio.create_task(start_bot(settings.discord_bot_token))
            logger.info("Discord bot task started")
        else:
            logger.info("No DISCORD_BOT_TOKEN — bot not started")

        # Wire db_pool into the bot after the pool is available (below)

        # Set up asyncpg pool for guild_sync (raw SQL, separate from SQLAlchemy)
        # Converts postgresql+asyncpg:// DSN to plain postgresql:// for asyncpg
        raw_dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        try:
            guild_sync_pool = await asyncpg.create_pool(raw_dsn, min_size=2, max_size=10)
            app.state.guild_sync_pool = guild_sync_pool
            logger.info("Guild sync asyncpg pool created")
            # Give the bot access to the pool for DM gate checks and onboarding
            from sv_common.discord.bot import set_db_pool
            set_db_pool(guild_sync_pool)
        except Exception as exc:
            logger.warning("Guild sync pool not created (DB may not be available): %s", exc)
            guild_sync_pool = None
            app.state.guild_sync_pool = None

        # Start auto-booking scheduler (requires guild_sync_pool)
        auto_book_task = None
        if guild_sync_pool:
            auto_book_task = asyncio.create_task(_auto_book_loop(guild_sync_pool))

        # Start campaign status checker background task
        campaign_checker_task = asyncio.create_task(
            _run_campaign_checker(settings.database_url)
        )
        logger.info("Campaign status checker task started")

        # Start contest agent background task
        contest_agent_task = asyncio.create_task(
            _run_contest_agent(settings.database_url)
        )
        logger.info("Contest agent task started")

        # Start guild sync scheduler (skipped if Blizzard creds or Discord bot missing)
        guild_scheduler = None
        audit_channel_id_str = None
        audit_channel_id_int = None
        if guild_sync_pool:
            async with guild_sync_pool.acquire() as _conn:
                audit_channel_id_str = await _conn.fetchval(
                    "SELECT audit_channel_id FROM common.discord_config LIMIT 1"
                )
            if audit_channel_id_str:
                try:
                    audit_channel_id_int = int(audit_channel_id_str)
                except (ValueError, TypeError):
                    logger.warning("Invalid audit_channel_id in DB (%r) — scheduler will not start", audit_channel_id_str)
        if (
            guild_sync_pool
            and settings.blizzard_client_id
            and settings.blizzard_client_secret
            and settings.discord_bot_token
            and audit_channel_id_int
        ):
            from sv_common.guild_sync.scheduler import GuildSyncScheduler
            from sv_common.discord.bot import get_bot
            discord_bot = get_bot()
            guild_scheduler = GuildSyncScheduler(
                db_pool=guild_sync_pool,
                discord_bot=discord_bot,
                audit_channel_id=audit_channel_id_int,
            )
            await guild_scheduler.start()
            app.state.guild_sync_scheduler = guild_scheduler
            logger.info("Guild sync scheduler started")
        else:
            app.state.guild_sync_scheduler = None
            logger.info("Guild sync scheduler skipped (missing credentials or audit channel not configured)")

        yield

        # Graceful shutdown
        if auto_book_task is not None:
            auto_book_task.cancel()
            try:
                await auto_book_task
            except asyncio.CancelledError:
                pass

        campaign_checker_task.cancel()
        try:
            await campaign_checker_task
        except asyncio.CancelledError:
            pass

        contest_agent_task.cancel()
        try:
            await contest_agent_task
        except asyncio.CancelledError:
            pass

        if guild_scheduler is not None:
            await guild_scheduler.stop()

        if guild_sync_pool is not None:
            await guild_sync_pool.close()

        if bot_task is not None:
            from sv_common.discord.bot import stop_bot
            await stop_bot()
            bot_task.cancel()

        engine = get_engine(settings.database_url)
        await engine.dispose()
        logger.info("PATT platform shutdown complete")

    app = FastAPI(
        title="Pull All The Things Guild Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Security headers on every response
    app.add_middleware(SecurityHeadersMiddleware)

    # ---------------------------------------------------------------------------
    # Exception handlers
    # ---------------------------------------------------------------------------

    from patt.templating import templates as _templates

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc):
        # Only render HTML for browser requests; return JSON for API paths
        if request.url.path.startswith("/api/"):
            return Response(
                content='{"ok":false,"error":"Not found"}',
                status_code=404,
                media_type="application/json",
            )
        return _templates.TemplateResponse(
            "public/404.html",
            {"request": request, "current_member": None, "active_campaigns": []},
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error_handler(request: Request, exc):
        error_id = str(uuid.uuid4())[:8]
        logger.error("Server error %s on %s: %s", error_id, request.url.path, exc)
        if request.url.path.startswith("/api/"):
            return Response(
                content=f'{{"ok":false,"error":"Internal server error","error_id":"{error_id}"}}',
                status_code=500,
                media_type="application/json",
            )
        return _templates.TemplateResponse(
            "public/500.html",
            {
                "request": request,
                "current_member": None,
                "active_campaigns": [],
                "error_id": error_id,
            },
            status_code=500,
        )

    # Rate limiting for login endpoint
    @app.middleware("http")
    async def rate_limit_login(request: Request, call_next: Callable) -> Response:
        if request.url.path == "/api/v1/auth/login" and request.method == "POST":
            client_ip = request.client.host if request.client else "unknown"
            if not _check_rate_limit(client_ip):
                logger.warning("Rate limit hit for IP %s on login", client_ip)
                return Response(
                    content='{"ok":false,"error":"Too many login attempts. Try again in a minute."}',
                    status_code=429,
                    media_type="application/json",
                )
        return await call_next(request)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Serve legacy HTML files at their original URL paths (Phase 5)
    # These were previously at repo root served by Nginx; now served by FastAPI.
    if LEGACY_DIR.exists():
        _legacy_files = {
            "raid-admin": "raid-admin.html",
            "mitos-corner": "mitos-corner.html",
        }

        def _make_legacy_handler(filename: str):
            filepath = LEGACY_DIR / filename

            async def _handler():
                return FileResponse(filepath, media_type="text/html")

            return _handler

        for _route, _file in _legacy_files.items():
            app.add_api_route(
                f"/{_route}",
                _make_legacy_handler(_file),
                methods=["GET"],
                include_in_schema=False,
            )
            # Also serve with .html extension for backwards compat
            app.add_api_route(
                f"/{_file}",
                _make_legacy_handler(_file),
                methods=["GET"],
                include_in_schema=False,
            )

        # Redirects for legacy roster pages → new dynamic /roster route
        @app.get("/roster.html", include_in_schema=False)
        async def roster_html_redirect():
            return RedirectResponse(url="/roster", status_code=301)

        @app.get("/roster-view.html", include_in_schema=False)
        async def roster_view_html_redirect():
            return RedirectResponse(url="/roster", status_code=301)

        # Serve patt-config.json
        _config_path = LEGACY_DIR / "patt-config.json"

        async def _serve_patt_config():
            return FileResponse(_config_path, media_type="application/json")

        app.add_api_route(
            "/patt-config.json",
            _serve_patt_config,
            methods=["GET"],
            include_in_schema=False,
        )

    # Register API routes
    from patt.api.health import router as health_router
    from patt.api.admin_routes import router as admin_router
    from patt.api.guild_routes import router as guild_router
    from patt.api.auth_routes import router as auth_router
    from patt.api.campaign_routes import (
        admin_campaign_router,
        vote_router,
        public_campaign_router,
    )
    from sv_common.guild_sync.api.routes import guild_sync_router, identity_router
    from sv_common.guild_sync.api.crafting_routes import crafting_router

    app.include_router(health_router, prefix="/api")
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(admin_campaign_router)
    app.include_router(guild_router)
    app.include_router(vote_router)
    app.include_router(public_campaign_router)
    app.include_router(guild_sync_router)
    app.include_router(identity_router)
    app.include_router(crafting_router)

    # Register page routes (server-rendered HTML)
    from patt.pages.auth_pages import router as auth_page_router
    from patt.pages.vote_pages import router as vote_page_router
    from patt.pages.admin_pages import router as admin_page_router
    from patt.pages.public_pages import router as public_page_router
    from patt.pages.profile_pages import router as profile_page_router

    app.include_router(public_page_router)
    app.include_router(auth_page_router)
    app.include_router(vote_page_router)
    app.include_router(admin_page_router)
    app.include_router(profile_page_router)

    return app


# Module-level app instance for uvicorn direct import (non-factory mode)
# Use create_app() factory for tests and development server.
