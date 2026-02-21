"""Integration test — health check endpoint."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "db" in data["data"]
    assert "version" in data["data"]
    assert data["data"]["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_health_endpoint_db_connected(client: AsyncClient):
    """Health check reports DB as connected when test DB is available."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["data"]["db"] == "connected"
