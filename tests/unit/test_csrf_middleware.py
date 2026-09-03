"""Regression tests for unsafe cookie-request same-origin enforcement."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from guild_portal.middleware.csrf import CookieSameOriginMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CookieSameOriginMiddleware)

    @app.post("/change")
    async def change():
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_cross_origin_cookie_write_is_rejected():
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/change",
            cookies={"patt_token": "synthetic"},
            headers={"Origin": "https://attacker.invalid"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_same_origin_cookie_write_is_allowed():
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/change",
            cookies={"patt_token": "synthetic"},
            headers={"Origin": "http://test"},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_bearer_only_write_is_not_a_csrf_request():
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/change", headers={"Authorization": "Bearer synthetic"}
        )
    assert response.status_code == 200
