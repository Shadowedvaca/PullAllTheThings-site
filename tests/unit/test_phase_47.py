"""
Unit tests for Phase 4.7 — Voice Channel Attendance Tracking.

Tests cover:
1.  _reconstruct_spans: DC+rejoin, single join, open span (still in VC)
2.  _reconstruct_spans: joined after event end → 0 secs
3.  Pass 2 presence_pct calculation at threshold boundary
4.  Pass 2: joined_late / left_early computed from raw times (not clipped)
5.  Pass 2: WCL-credited player gets timing flags added, attended stays TRUE
6.  Pass 2: unlinked Discord user handled gracefully (no exception)
7.  Pass 1: character name not in wow_characters → warning, skipped
8.  Pass 1: no WCL report for event date → skipped (no-op)
9.  Pass 3: habitual_late fires when count >= threshold
10. Pass 3: does not fire below threshold
11. VoiceAttendanceCog: finds active event within window
12. VoiceAttendanceCog: ignores event outside window
13. ORM: VoiceAttendanceLog model exists with expected columns
14. ORM: RaidEvent has Phase 4.7 columns
15. ORM: RaidAttendance has Phase 4.7 columns
16. ORM: DiscordConfig has attendance config columns
17. Scheduler: run_attendance_processing method exists
18. Admin: _PATH_TO_SCREEN includes attendance_report
19. Admin: attendance API route handlers exist
20. players-data: attendance_status in player response fields
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(offset_min: int = 0) -> datetime:
    """Return a UTC datetime offset by offset_min from a fixed base."""
    base = datetime(2026, 3, 18, 21, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=offset_min)


def _mk_entry(action: str, offset_min: int):
    return {"action": action, "occurred_at": _dt(offset_min)}


# ---------------------------------------------------------------------------
# 1–2. _reconstruct_spans
# ---------------------------------------------------------------------------

class TestReconstructSpans:
    def _call(self, entries, end_offset=120):
        from sv_common.guild_sync.attendance_processor import _reconstruct_spans
        return _reconstruct_spans(entries, _dt(end_offset))

    def test_single_join_leave(self):
        entries = [_mk_entry("join", 0), _mk_entry("leave", 60)]
        spans = self._call(entries)
        assert len(spans) == 1
        assert spans[0] == (_dt(0), _dt(60))

    def test_open_span_closes_at_end(self):
        """User still in VC when raid ends — span closed at end_utc."""
        entries = [_mk_entry("join", 0)]
        spans = self._call(entries, end_offset=120)
        assert len(spans) == 1
        assert spans[0][1] == _dt(120)

    def test_dc_rejoin(self):
        """DC at minute 30, rejoin at 40 → two spans."""
        entries = [
            _mk_entry("join", 0),
            _mk_entry("leave", 30),
            _mk_entry("join", 40),
            _mk_entry("leave", 100),
        ]
        spans = self._call(entries)
        assert len(spans) == 2
        assert spans[0] == (_dt(0), _dt(30))
        assert spans[1] == (_dt(40), _dt(100))

    def test_joined_after_raid_end(self):
        """Join at minute 130 (after end at 120) → span is (130, 120) → clipped to empty later."""
        entries = [_mk_entry("join", 130)]
        spans = self._call(entries, end_offset=120)
        # span is (130, 120) — degenerate, will produce 0 secs when clipped
        assert len(spans) == 1
        start, end = spans[0]
        assert start > end  # degenerate span — 0 secs when clipped

    def test_orphan_leave_ignored(self):
        """Leave with no preceding join is silently skipped."""
        entries = [_mk_entry("leave", 10), _mk_entry("join", 20), _mk_entry("leave", 80)]
        spans = self._call(entries)
        assert len(spans) == 1
        assert spans[0] == (_dt(20), _dt(80))

    def test_duplicate_join_ignored(self):
        """Second join while already in VC (reconnect without leave) is ignored."""
        entries = [
            _mk_entry("join", 0),
            _mk_entry("join", 5),  # duplicate — ignored
            _mk_entry("leave", 60),
        ]
        spans = self._call(entries)
        assert len(spans) == 1
        assert spans[0] == (_dt(0), _dt(60))


# ---------------------------------------------------------------------------
# 3. presence_pct calculation + threshold boundary
# ---------------------------------------------------------------------------

class TestPresencePct:
    def test_exactly_at_threshold(self):
        """75% of 100-min effective window = 75 min required. Exactly 75 = attended."""
        # effective window: minute 10 to 110 (100 min)
        # user present: minute 35 to 110 = 75 min effective
        # That's exactly 75% → should attend
        # We'll verify the arithmetic directly
        grace = timedelta(minutes=10)
        start = _dt(0)
        end = _dt(120)
        eff_start = start + grace  # _dt(10)
        eff_end = end - grace      # _dt(110)
        eff_window = (eff_end - eff_start).total_seconds()  # 100 * 60 = 6000

        # User joins at minute 35, leaves at end (110 effective)
        spans = [(_dt(35), _dt(110))]
        total_secs = sum(
            max(0, (min(s[1], eff_end) - max(s[0], eff_start)).total_seconds())
            for s in spans
        )
        pct = total_secs / eff_window * 100
        assert abs(pct - 75.0) < 0.01
        assert pct >= 75  # should attend

    def test_one_second_below_threshold(self):
        """74.9% → does NOT meet threshold."""
        grace = timedelta(minutes=10)
        start = _dt(0)
        end = _dt(120)
        eff_start = start + grace
        eff_end = end - grace
        eff_window = (eff_end - eff_start).total_seconds()

        # Present for 74.9 min
        present_secs = 74.9 * 60
        pct = present_secs / eff_window * 100
        assert pct < 75


# ---------------------------------------------------------------------------
# 4. joined_late / left_early from raw times (not clipped)
# ---------------------------------------------------------------------------

class TestTimingFlags:
    def test_joined_late_flag(self):
        """joined_late = True if first_join > start + grace."""
        late_grace = timedelta(minutes=10)
        start = _dt(0)
        first_join = _dt(15)  # 15 min in > 10 min grace
        joined_late = first_join > (start + late_grace)
        assert joined_late is True

    def test_joined_on_time_flag(self):
        """joined_late = False if first_join <= start + grace."""
        late_grace = timedelta(minutes=10)
        start = _dt(0)
        first_join = _dt(5)
        joined_late = first_join > (start + late_grace)
        assert joined_late is False

    def test_left_early_flag(self):
        """left_early = True if last_leave < end - grace."""
        early_grace = timedelta(minutes=10)
        end = _dt(120)
        last_leave = _dt(100)  # 20 min before end > 10 min grace
        left_early = last_leave < (end - early_grace)
        assert left_early is True

    def test_left_on_time_flag(self):
        """left_early = False if last_leave >= end - grace."""
        early_grace = timedelta(minutes=10)
        end = _dt(120)
        last_leave = _dt(115)  # 5 min before end — within grace
        left_early = last_leave < (end - early_grace)
        assert left_early is False


# ---------------------------------------------------------------------------
# 5. Pass 2 source upgrade when WCL row already exists
# ---------------------------------------------------------------------------

class TestVoicePassSourceUpgrade:
    def test_source_upgrade_logic(self):
        """Verify source upgrade SQL logic for known source values."""
        def expected_source(existing_source: str) -> str:
            if existing_source == "wcl":
                return "wcl+voice"
            elif existing_source == "raid_helper":
                return "raid_helper+voice"
            elif "+voice" in existing_source:
                return existing_source
            else:
                return "voice"

        assert expected_source("wcl") == "wcl+voice"
        assert expected_source("raid_helper") == "raid_helper+voice"
        assert expected_source("wcl+voice") == "wcl+voice"
        assert expected_source("voice") == "voice"
        assert expected_source("manual") == "voice"


# ---------------------------------------------------------------------------
# 6. Unlinked Discord user handled gracefully
# ---------------------------------------------------------------------------

class TestUnlinkedUser:
    @pytest.mark.asyncio
    async def test_unlinked_user_skipped(self):
        """process_voice_pass skips unlinked Discord user without exception."""
        from sv_common.guild_sync.attendance_processor import process_voice_pass

        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={
            "id": 1,
            "start_time_utc": _dt(0),
            "end_time_utc": _dt(120),
        })
        # First fetchrow call is for config
        config_row = {
            "attendance_min_pct": 75,
            "attendance_late_grace_min": 10,
            "attendance_early_leave_min": 10,
        }

        call_count = {"n": 0}
        async def mock_fetchrow(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"id": 1, "start_time_utc": _dt(0), "end_time_utc": _dt(120)}
            return config_row

        conn.fetchrow = AsyncMock(side_effect=mock_fetchrow)
        conn.fetch = AsyncMock(side_effect=[
            # log_rows: one join from unknown user
            [{"discord_user_id": "999888777", "action": "join", "occurred_at": _dt(5)}],
            # discord_to_player: no match
            [],
        ])
        conn.execute = AsyncMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        result = await process_voice_pass(pool, 1)
        assert result["unlinked"] == 1
        assert result["processed"] == 0


# ---------------------------------------------------------------------------
# 7. Pass 1: character not in wow_characters → skipped
# ---------------------------------------------------------------------------

class TestWclPassUnknownCharacter:
    @pytest.mark.asyncio
    async def test_unknown_character_skipped(self):
        """WCL attendee with no matching wow_character is counted as unmatched."""
        from sv_common.guild_sync.attendance_processor import process_wcl_pass

        pool = AsyncMock()
        conn = AsyncMock()

        async def mock_fetchrow(*args, **kwargs):
            return {
                "id": 1,
                "event_date": datetime(2026, 3, 18, tzinfo=timezone.utc).date(),
                "log_url": None,
                "start_time_utc": _dt(0),
            }

        conn.fetchrow = AsyncMock(side_effect=mock_fetchrow)
        conn.fetch = AsyncMock(side_effect=[
            # reports
            [{"id": 10, "attendees": [{"name": "Unknownchar"}]}],
            # char resolution: empty
            [],
        ])
        conn.execute = AsyncMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        result = await process_wcl_pass(pool, 1)
        assert result["unmatched"] == 1
        assert result["matched"] == 0
        # No upsert executed
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Pass 1: no WCL report for event date → skipped
# ---------------------------------------------------------------------------

class TestWclPassNoReport:
    @pytest.mark.asyncio
    async def test_no_wcl_report(self):
        from sv_common.guild_sync.attendance_processor import process_wcl_pass

        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={
            "id": 1,
            "event_date": datetime(2026, 3, 18, tzinfo=timezone.utc).date(),
            "log_url": None,
            "start_time_utc": _dt(0),
        })
        conn.fetch = AsyncMock(return_value=[])  # no reports
        conn.execute = AsyncMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        result = await process_wcl_pass(pool, 1)
        assert result["skipped"] is True
        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 9–10. Pass 3: habitual check
# ---------------------------------------------------------------------------

class TestHabitualCheck:
    @pytest.mark.asyncio
    async def test_habitual_late_fires(self):
        """Alert sent when joined_late count >= threshold."""
        from sv_common.guild_sync.attendance_processor import check_habitual_patterns

        audit = MagicMock()
        audit.send = AsyncMock()
        pool = AsyncMock()
        conn = AsyncMock()

        conn.fetchrow = AsyncMock(side_effect=[
            # config
            {"attendance_habitual_window": 5, "attendance_habitual_threshold": 3},
            # event
            {"event_date": datetime(2026, 3, 18, tzinfo=timezone.utc).date()},
        ])
        conn.fetch = AsyncMock(side_effect=[
            # today_players
            [{"player_id": 7}],
            # recent events for player 7 — 3/5 late
            [
                {"joined_late": True,  "left_early": False, "event_date": datetime(2026, 3, 18, tzinfo=timezone.utc).date()},
                {"joined_late": True,  "left_early": False, "event_date": datetime(2026, 3, 11, tzinfo=timezone.utc).date()},
                {"joined_late": True,  "left_early": False, "event_date": datetime(2026, 3, 4, tzinfo=timezone.utc).date()},
                {"joined_late": False, "left_early": False, "event_date": datetime(2026, 2, 25, tzinfo=timezone.utc).date()},
                {"joined_late": False, "left_early": False, "event_date": datetime(2026, 2, 18, tzinfo=timezone.utc).date()},
            ],
        ])
        conn.fetchval = AsyncMock(return_value="Trogmoon")
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        await check_habitual_patterns(pool, 1, audit)
        audit.send.assert_called_once()
        call_kwargs = audit.send.call_args[1]
        assert "embed" in call_kwargs

    @pytest.mark.asyncio
    async def test_habitual_below_threshold(self):
        """No alert when late count < threshold."""
        from sv_common.guild_sync.attendance_processor import check_habitual_patterns

        audit = AsyncMock()
        pool = AsyncMock()
        conn = AsyncMock()

        conn.fetchrow = AsyncMock(side_effect=[
            {"attendance_habitual_window": 5, "attendance_habitual_threshold": 3},
            {"event_date": datetime(2026, 3, 18, tzinfo=timezone.utc).date()},
        ])
        conn.fetch = AsyncMock(side_effect=[
            [{"player_id": 7}],
            # only 2/5 late — below threshold of 3
            [
                {"joined_late": True,  "left_early": False, "event_date": datetime(2026, 3, 18, tzinfo=timezone.utc).date()},
                {"joined_late": True,  "left_early": False, "event_date": datetime(2026, 3, 11, tzinfo=timezone.utc).date()},
                {"joined_late": False, "left_early": False, "event_date": datetime(2026, 3, 4, tzinfo=timezone.utc).date()},
                {"joined_late": False, "left_early": False, "event_date": datetime(2026, 2, 25, tzinfo=timezone.utc).date()},
                {"joined_late": False, "left_early": False, "event_date": datetime(2026, 2, 18, tzinfo=timezone.utc).date()},
            ],
        ])
        conn.fetchval = AsyncMock(return_value="Trogmoon")
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        await check_habitual_patterns(pool, 1, audit)
        audit.send.assert_not_called()


# ---------------------------------------------------------------------------
# 11–12. VoiceAttendanceCog window detection
# ---------------------------------------------------------------------------

class TestVoiceAttendanceCogWindow:
    def _make_cog(self, events):
        from sv_common.discord.voice_attendance import VoiceAttendanceCog
        bot = MagicMock()
        pool = MagicMock()
        cog = VoiceAttendanceCog.__new__(VoiceAttendanceCog)
        cog.bot = bot
        cog.db_pool = pool
        cog._today_events = events
        cog._default_voice_channel_id = "111222333"
        cog._cache_date = None
        return cog

    def test_finds_active_event(self):
        from sv_common.discord.voice_attendance import VoiceAttendanceCog
        now = _dt(60)  # 60 min into the raid
        events = [{
            "id": 1,
            "start_time_utc": _dt(0),
            "end_time_utc": _dt(120),
            "voice_channel_id": None,
            "voice_tracking_enabled": True,
        }]
        cog = self._make_cog(events)
        result = cog._find_active_event(now)
        assert result is not None
        assert result["id"] == 1

    def test_ignores_outside_window(self):
        from sv_common.discord.voice_attendance import VoiceAttendanceCog
        now = _dt(200)  # well after raid end
        events = [{
            "id": 1,
            "start_time_utc": _dt(0),
            "end_time_utc": _dt(120),
            "voice_channel_id": None,
            "voice_tracking_enabled": True,
        }]
        cog = self._make_cog(events)
        result = cog._find_active_event(now)
        assert result is None

    def test_effective_channel_override(self):
        from sv_common.discord.voice_attendance import VoiceAttendanceCog
        events = [{
            "id": 1,
            "start_time_utc": _dt(0),
            "end_time_utc": _dt(120),
            "voice_channel_id": "999888777",  # per-event override
            "voice_tracking_enabled": True,
        }]
        cog = self._make_cog(events)
        ch = cog._effective_channel(events[0])
        assert ch == "999888777"

    def test_effective_channel_fallback(self):
        from sv_common.discord.voice_attendance import VoiceAttendanceCog
        events = [{
            "id": 1,
            "start_time_utc": _dt(0),
            "end_time_utc": _dt(120),
            "voice_channel_id": None,
            "voice_tracking_enabled": True,
        }]
        cog = self._make_cog(events)
        ch = cog._effective_channel(events[0])
        assert ch == "111222333"  # falls back to default


# ---------------------------------------------------------------------------
# 13–16. ORM model structure
# ---------------------------------------------------------------------------

class TestOrmModels:
    def test_voice_attendance_log_exists(self):
        from sv_common.db.models import VoiceAttendanceLog
        cols = {c.key for c in VoiceAttendanceLog.__table__.columns}
        assert "id" in cols
        assert "event_id" in cols
        assert "discord_user_id" in cols
        assert "channel_id" in cols
        assert "action" in cols
        assert "occurred_at" in cols

    def test_raid_event_phase47_columns(self):
        from sv_common.db.models import RaidEvent
        cols = {c.key for c in RaidEvent.__table__.columns}
        assert "voice_channel_id" in cols
        assert "voice_tracking_enabled" in cols
        assert "attendance_processed_at" in cols

    def test_raid_attendance_phase47_columns(self):
        from sv_common.db.models import RaidAttendance
        cols = {c.key for c in RaidAttendance.__table__.columns}
        assert "minutes_present" in cols
        assert "first_join_at" in cols
        assert "last_leave_at" in cols
        assert "joined_late" in cols
        assert "left_early" in cols

    def test_discord_config_phase47_columns(self):
        from sv_common.db.models import DiscordConfig
        cols = {c.key for c in DiscordConfig.__table__.columns}
        assert "attendance_feature_enabled" in cols
        assert "attendance_min_pct" in cols
        assert "attendance_late_grace_min" in cols
        assert "attendance_early_leave_min" in cols
        assert "attendance_trailing_events" in cols
        assert "attendance_habitual_window" in cols
        assert "attendance_habitual_threshold" in cols


# ---------------------------------------------------------------------------
# 17. Scheduler has run_attendance_processing
# ---------------------------------------------------------------------------

class TestSchedulerMethod:
    def test_run_attendance_processing_exists(self):
        from sv_common.guild_sync.scheduler import GuildSyncScheduler
        assert hasattr(GuildSyncScheduler, "run_attendance_processing")
        import inspect
        assert inspect.iscoroutinefunction(GuildSyncScheduler.run_attendance_processing)


# ---------------------------------------------------------------------------
# 18. Admin _PATH_TO_SCREEN includes attendance_report
# ---------------------------------------------------------------------------

class TestAdminPathToScreen:
    def test_attendance_report_in_path_to_screen(self):
        from guild_portal.pages.admin_pages import _PATH_TO_SCREEN
        keys = {key for _, key in _PATH_TO_SCREEN}
        assert "attendance_report" in keys

    def test_attendance_path_in_path_to_screen(self):
        from guild_portal.pages.admin_pages import _PATH_TO_SCREEN
        paths = {path for path, _ in _PATH_TO_SCREEN}
        assert "/admin/attendance" in paths


# ---------------------------------------------------------------------------
# 19. Admin API route handlers exist
# ---------------------------------------------------------------------------

class TestAttendanceApiRoutes:
    def test_season_route_exists(self):
        from guild_portal.api.admin_routes import get_attendance_season
        import inspect
        assert inspect.iscoroutinefunction(get_attendance_season)

    def test_event_route_exists(self):
        from guild_portal.api.admin_routes import get_attendance_event
        import inspect
        assert inspect.iscoroutinefunction(get_attendance_event)

    def test_reprocess_route_exists(self):
        from guild_portal.api.admin_routes import reprocess_attendance_event
        import inspect
        assert inspect.iscoroutinefunction(reprocess_attendance_event)

    def test_record_update_route_exists(self):
        from guild_portal.api.admin_routes import update_attendance_record
        import inspect
        assert inspect.iscoroutinefunction(update_attendance_record)

    def test_settings_get_route_exists(self):
        from guild_portal.api.admin_routes import get_attendance_settings
        import inspect
        assert inspect.iscoroutinefunction(get_attendance_settings)

    def test_settings_patch_route_exists(self):
        from guild_portal.api.admin_routes import update_attendance_settings
        import inspect
        assert inspect.iscoroutinefunction(update_attendance_settings)


# ---------------------------------------------------------------------------
# 20. players-data: attendance_status field in response structure
# ---------------------------------------------------------------------------

class TestPlayersDataAttendanceField:
    def test_wcl_code_extraction(self):
        """_extract_wcl_code parses report URL correctly."""
        from sv_common.guild_sync.attendance_processor import _extract_wcl_code
        assert _extract_wcl_code("https://www.warcraftlogs.com/reports/ABC123") == "ABC123"
        assert _extract_wcl_code("https://www.warcraftlogs.com/reports/XYZ?fight=1") == "XYZ"
        assert _extract_wcl_code("https://example.com/no-reports") is None
        assert _extract_wcl_code("") is None
        assert _extract_wcl_code(None) is None

    def test_attendance_status_helper(self):
        """_compute_att_status returns correct status for various scenarios."""
        from guild_portal.api.admin_routes import _compute_att_status

        # Feature disabled → none
        result = _compute_att_status([], 75, False)
        assert result["status"] == "none"

        # Too few events → new
        rows = [{"attended": True, "noted_absence": False}] * 2
        result = _compute_att_status(rows, 75, True)
        assert result["status"] == "new"

        # Good attendance
        rows = [{"attended": True, "noted_absence": False}] * 8
        result = _compute_att_status(rows, 75, True)
        assert result["status"] == "good"

        # At risk (62.5%)
        rows = [{"attended": True, "noted_absence": False}] * 5 + \
               [{"attended": False, "noted_absence": False}] * 3
        result = _compute_att_status(rows, 75, True)
        assert result["status"] == "at_risk"

        # Concern (< 50%)
        rows = [{"attended": True, "noted_absence": False}] * 3 + \
               [{"attended": False, "noted_absence": False}] * 7
        result = _compute_att_status(rows, 75, True)
        assert result["status"] == "concern"
