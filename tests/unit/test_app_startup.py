"""Startup resilience contracts for optional external integrations."""

import asyncio
from types import SimpleNamespace

from unittest.mock import AsyncMock, Mock

import pytest

from guild_portal.app import (
    _finish_optional_guild_scheduler_start,
    _schedule_optional_guild_scheduler,
    _start_optional_guild_scheduler,
)
from sv_common.guild_sync.scheduler import GuildSyncScheduler


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


@pytest.mark.asyncio
async def test_optional_guild_scheduler_cleanup_failure_does_not_block_startup():
    scheduler = Mock()
    scheduler.start = AsyncMock(side_effect=TimeoutError("synthetic outage"))
    scheduler.stop = AsyncMock(side_effect=RuntimeError("synthetic cleanup failure"))

    result = await _start_optional_guild_scheduler(scheduler)

    assert result is None
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_optional_guild_scheduler_has_its_own_bounded_timeout():
    blocker = asyncio.Event()
    scheduler = Mock()
    scheduler.start = AsyncMock(side_effect=blocker.wait)
    scheduler.stop = AsyncMock()

    result = await _start_optional_guild_scheduler(
        scheduler,
        timeout_seconds=0.01,
    )

    assert result is None
    scheduler.start.assert_awaited_once_with()
    scheduler.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_optional_guild_scheduler_is_scheduled_without_blocking_readiness():
    gate = asyncio.Event()
    scheduler = Mock()
    scheduler.start = AsyncMock(side_effect=gate.wait)
    scheduler.stop = AsyncMock()
    app = SimpleNamespace(state=SimpleNamespace())

    task = _schedule_optional_guild_scheduler(app, scheduler)
    await asyncio.sleep(0)

    assert task.done() is False
    assert app.state.guild_sync_scheduler is None
    gate.set()
    assert await task is scheduler
    assert app.state.guild_sync_scheduler is scheduler


@pytest.mark.asyncio
async def test_pending_optional_start_is_cancelled_and_cleaned_up_on_shutdown():
    gate = asyncio.Event()
    scheduler = Mock()
    scheduler.start = AsyncMock(side_effect=gate.wait)
    scheduler.stop = AsyncMock()
    app = SimpleNamespace(state=SimpleNamespace())
    task = _schedule_optional_guild_scheduler(app, scheduler)
    await asyncio.sleep(0)

    result = await _finish_optional_guild_scheduler_start(task, scheduler)

    assert result is None
    assert task.cancelled()
    scheduler.stop.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_guild_scheduler_stop_shuts_down_running_scheduler_and_client():
    guild_scheduler = GuildSyncScheduler.__new__(GuildSyncScheduler)
    guild_scheduler.scheduler = Mock(running=True)
    guild_scheduler.blizzard_client = Mock()
    guild_scheduler.blizzard_client.close = AsyncMock()

    await guild_scheduler.stop()

    guild_scheduler.scheduler.shutdown.assert_called_once_with()
    guild_scheduler.blizzard_client.close.assert_awaited_once_with()
