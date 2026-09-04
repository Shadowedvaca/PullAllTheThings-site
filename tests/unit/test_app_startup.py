"""Startup resilience contracts for optional external integrations."""

from unittest.mock import AsyncMock, Mock

import pytest

from guild_portal.app import _start_optional_guild_scheduler


@pytest.mark.asyncio
async def test_optional_guild_scheduler_success_is_retained():
    scheduler = Mock()
    scheduler.start = AsyncMock()
    scheduler.stop = AsyncMock()

    result = await _start_optional_guild_scheduler(scheduler)

    assert result is scheduler
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_guild_scheduler_failure_is_cleaned_up():
    scheduler = Mock()
    scheduler.start = AsyncMock(side_effect=TimeoutError("synthetic outage"))
    scheduler.stop = AsyncMock()

    result = await _start_optional_guild_scheduler(scheduler)

    assert result is None
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_awaited_once_with()
