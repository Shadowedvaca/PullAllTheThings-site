"""Unit tests for attendance snapshot logic."""
import pytest


# ---------------------------------------------------------------------------
# _compute_auto_excused helper (mirrors admin_routes._compute_auto_excused)
# ---------------------------------------------------------------------------

def _compute_auto_excused(was_available, raid_helper_status, excuse_if_unavailable, excuse_if_discord_absent):
    auto = False
    if excuse_if_unavailable and was_available is False:
        auto = True
    if excuse_if_discord_absent and raid_helper_status == "absence":
        auto = True
    return auto


# ---------------------------------------------------------------------------
# test_auto_excuse_logic
# ---------------------------------------------------------------------------

class TestAutoExcuseLogic:
    def test_both_settings_off_never_excused(self):
        assert not _compute_auto_excused(False, "absence", False, False)
        assert not _compute_auto_excused(True, "accepted", False, False)
        assert not _compute_auto_excused(None, None, False, False)

    def test_excuse_unavailable_only_triggers_on_false(self):
        assert _compute_auto_excused(False, "accepted", True, False)
        assert not _compute_auto_excused(True, "accepted", True, False)
        assert not _compute_auto_excused(None, "accepted", True, False)  # NULL = no snapshot yet

    def test_excuse_discord_absent_only_triggers_on_absence(self):
        assert _compute_auto_excused(True, "absence", False, True)
        assert not _compute_auto_excused(True, "accepted", False, True)
        assert not _compute_auto_excused(True, "tentative", False, True)
        assert not _compute_auto_excused(True, "bench", False, True)
        assert not _compute_auto_excused(True, None, False, True)

    def test_both_settings_on_either_condition_triggers(self):
        # unavailable + not absent → excused (unavailable)
        assert _compute_auto_excused(False, "accepted", True, True)
        # available + absent → excused (discord absent)
        assert _compute_auto_excused(True, "absence", True, True)
        # both conditions → still excused
        assert _compute_auto_excused(False, "absence", True, True)
        # neither condition → not excused
        assert not _compute_auto_excused(True, "accepted", True, True)

    def test_null_snapshot_data_never_excused(self):
        # was_available=None means snapshot hasn't run for availability — should not trigger
        # the unavailable rule. But the discord_absent rule is independent.
        assert not _compute_auto_excused(None, None, True, True)
        # raid_helper_status=absence can trigger even if was_available is None
        assert _compute_auto_excused(None, "absence", True, True)
        # was_available=None does NOT trigger the unavailable rule
        assert not _compute_auto_excused(None, "accepted", True, False)


# ---------------------------------------------------------------------------
# test_raid_helper_status_mapping
# ---------------------------------------------------------------------------

from sv_common.guild_sync.attendance_processor import _map_rh_class_to_status


class TestRaidHelperStatusMapping:
    @pytest.mark.parametrize("class_name,expected", [
        ("Tank", "accepted"),
        ("Healer", "accepted"),
        ("Melee", "accepted"),
        ("Ranged", "accepted"),
        ("Tentative", "tentative"),
        ("Bench", "bench"),
        ("Absence", "absence"),
        (None, "unknown"),
        ("", "unknown"),
        ("SomeUnknownClass", "accepted"),  # unknown class treated as accepted (signed up somehow)
    ])
    def test_mapping(self, class_name, expected):
        assert _map_rh_class_to_status(class_name) == expected


# ---------------------------------------------------------------------------
# test_was_available_from_availability_rows
# ---------------------------------------------------------------------------

class TestWasAvailableLogic:
    """Test the simplified heuristic: player has availability row for event day_of_week → available."""

    def _check_available(self, available_player_ids: set, player_id: int) -> bool:
        return player_id in available_player_ids

    def test_player_with_row_is_available(self):
        available = {1, 2, 3}
        assert self._check_available(available, 1)
        assert self._check_available(available, 2)

    def test_player_without_row_is_not_available(self):
        available = {1, 2}
        assert not self._check_available(available, 99)

    def test_empty_set_no_one_available(self):
        available: set = set()
        assert not self._check_available(available, 1)

    def test_day_of_week_derivation(self):
        """event_date.weekday() returns 0=Mon … 6=Sun."""
        from datetime import date
        mon = date(2026, 3, 23)  # Known Monday
        wed = date(2026, 3, 25)  # Known Wednesday
        sun = date(2026, 3, 29)  # Known Sunday
        assert mon.weekday() == 0
        assert wed.weekday() == 2
        assert sun.weekday() == 6
