"""Live test application fixture for Playwright journeys."""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import AsyncMock
from urllib.request import urlopen

import pytest
import uvicorn


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="session")
def live_test_url() -> str:
    """Run the real FastAPI routes over HTTP with external boundaries disabled."""

    from guild_portal.app import create_app
    from guild_portal.deps import get_db, get_page_member
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

    async def test_db():
        yield AsyncMock()

    async def anonymous_player():
        return None

    app.dependency_overrides[get_db] = test_db
    app.dependency_overrides[get_page_member] = anonymous_player

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
