"""Live test application fixture for Playwright journeys."""

from __future__ import annotations

import socket
import threading
import time
from urllib.request import urlopen

import pytest
import uvicorn


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        if self.value is None:
            raise AssertionError("Synthetic query unexpectedly returned no row")
        return self.value


class _E2EDatabase:
    """Bounded synthetic identity/session store for the browser auth journey."""

    def __init__(self):
        from sv_common.auth.passwords import hash_password
        from sv_common.db.models import GuildRank, Player, User

        self.rank = GuildRank(id=2, name="Member", level=2)
        self.user = User(
            id=1,
            email="synthetic-member",
            password_hash=hash_password("synthetic-password"),
            is_active=True,
            login_count=0,
        )
        self.player = Player(
            id=1,
            display_name="Synthetic Member",
            website_user_id=1,
            guild_rank_id=2,
        )
        self.player.guild_rank = self.rank
        self.player.characters = []
        self.player.discord_user = None
        self.user.player = self.player
        self.sessions = {}

    async def execute(self, statement):
        from sv_common.db.models import AuthSession, Player, User

        if getattr(statement, "is_update", False):
            from datetime import datetime, timezone

            for session in self.sessions.values():
                if session.revoked_at is None:
                    session.revoked_at = datetime.now(timezone.utc)
                    session.revoked_reason = "logout"
            return _ScalarResult(None)
        entity = statement.column_descriptions[0].get("entity")
        if entity is User:
            return _ScalarResult(self.user)
        if entity is Player:
            return _ScalarResult(self.player)
        if entity is AuthSession:
            return _ScalarResult(next(iter(self.sessions.values()), None))
        return _ScalarResult(None)

    def add(self, value):
        from sv_common.db.models import AuthSession

        if isinstance(value, AuthSession):
            value.user = self.user
            self.sessions[value.id] = value

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="session")
def live_test_url() -> str:
    """Run the real FastAPI routes over HTTP with external boundaries disabled."""

    from guild_portal.app import create_app
    from guild_portal.deps import get_db
    from guild_portal.templating import templates
    from sv_common.config_cache import get_site_config, set_site_config
    from sv_common.guild_sync.ah_service import copper_to_gold_str

    previous_config = get_site_config()
    set_site_config({**previous_config, "setup_complete": True})
    templates.env.globals["site"] = get_site_config
    templates.env.filters["gold"] = copper_to_gold_str
    templates.env.filters["format_gold"] = lambda value: (
        "—" if value is None else f"{int(value):,}g"
    )

    app = create_app()
    app.state.guild_sync_pool = None
    database = _E2EDatabase()

    async def test_db():
        yield database

    app.dependency_overrides[get_db] = test_db

    port = _unused_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    for _ in range(100):
        if not thread.is_alive():
            raise RuntimeError("Playwright test application exited during startup")
        try:
            with urlopen(f"{base_url}/login", timeout=0.25) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("Playwright test application did not become ready")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        set_site_config(previous_config)
