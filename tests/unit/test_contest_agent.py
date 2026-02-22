"""Unit tests for contest agent milestone detection and message generation.

Pure function tests — no database, no Discord, no external services.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-agent")
os.environ.setdefault("APP_ENV", "testing")

import pytest
from patt.services.contest_agent import (
    CHATTINESS_TRIGGERS,
    detect_milestone,
    generate_message,
    get_allowed_events,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stats(total_eligible=10, total_voted=0, all_voted=False):
    percent = round(total_voted / total_eligible * 100, 1) if total_eligible else 0
    return {
        "total_eligible": total_eligible,
        "total_voted": total_voted,
        "percent_voted": percent,
        "all_voted": all_voted,
    }


# ---------------------------------------------------------------------------
# get_allowed_events / chattiness config
# ---------------------------------------------------------------------------


class TestChattiness:
    def test_chattiness_quiet_only_launch_and_close(self):
        allowed = get_allowed_events("quiet")
        assert allowed == {"campaign_launch", "campaign_closed"}

    def test_chattiness_normal_includes_milestones(self):
        allowed = get_allowed_events("normal")
        assert "milestone_50" in allowed
        assert "final_stretch" in allowed
        assert "last_call" in allowed
        assert "lead_change" not in allowed
        assert "milestone_25" not in allowed
        assert "milestone_75" not in allowed

    def test_chattiness_hype_includes_everything(self):
        allowed = get_allowed_events("hype")
        for event in [
            "campaign_launch", "first_vote", "lead_change",
            "milestone_25", "milestone_50", "milestone_75",
            "final_stretch", "last_call", "all_voted", "campaign_closed",
        ]:
            assert event in allowed

    def test_unknown_chattiness_defaults_to_normal(self):
        allowed = get_allowed_events("extreme")
        assert allowed == get_allowed_events("normal")


# ---------------------------------------------------------------------------
# detect_milestone — launch
# ---------------------------------------------------------------------------


class TestDetectMilestoneLaunch:
    def test_campaign_launch_detected_when_no_logged_events(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=168,
            logged_events=set(),
            chattiness="normal",
        )
        assert event == "campaign_launch"

    def test_campaign_launch_not_repeated_if_already_logged(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=168,
            logged_events={"campaign_launch"},
            chattiness="normal",
        )
        assert event is None

    def test_launch_not_fired_for_quiet_before_any_event(self):
        # Quiet allows launch — it should still fire
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=168,
            logged_events=set(),
            chattiness="quiet",
        )
        assert event == "campaign_launch"


# ---------------------------------------------------------------------------
# detect_milestone — participation milestones
# ---------------------------------------------------------------------------


class TestDetectMilestoneParticipation:
    def test_milestone_25_detected_at_25_percent(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=1),
            time_remaining_hours=100,
            logged_events={"campaign_launch"},
            chattiness="hype",
        )
        assert event == "milestone_25"

    def test_milestone_50_detected_at_50_percent(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=2),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote", "milestone_25"},
            chattiness="hype",
        )
        assert event == "milestone_50"

    def test_milestone_50_detected_in_normal_mode(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=2),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote"},
            chattiness="normal",
        )
        assert event == "milestone_50"

    def test_milestone_25_not_available_in_normal_mode(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=1),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote"},
            chattiness="normal",
        )
        # 25% milestone not in normal chattiness — next applicable is None
        # (no 50% yet, no time warnings)
        assert event is None

    def test_milestone_not_re_triggered_after_logged(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=2),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote", "milestone_25", "milestone_50"},
            chattiness="hype",
        )
        assert event is None

    def test_milestone_75_detected_at_75_percent(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=3),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote", "milestone_25", "milestone_50"},
            chattiness="hype",
        )
        assert event == "milestone_75"


# ---------------------------------------------------------------------------
# detect_milestone — time warnings
# ---------------------------------------------------------------------------


class TestDetectMilestoneTimeWarnings:
    def test_final_stretch_detected_at_24_hours(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=23.9,
            logged_events={"campaign_launch"},
            chattiness="normal",
        )
        assert event == "final_stretch"

    def test_final_stretch_not_detected_above_24_hours(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=25,
            logged_events={"campaign_launch"},
            chattiness="normal",
        )
        assert event is None

    def test_last_call_detected_at_1_hour(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=0.9,
            logged_events={"campaign_launch", "final_stretch"},
            chattiness="normal",
        )
        assert event == "last_call"

    def test_last_call_not_fired_in_quiet_mode(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=0.9,
            logged_events={"campaign_launch"},
            chattiness="quiet",
        )
        # last_call not in quiet triggers
        assert event is None

    def test_final_stretch_not_re_triggered(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(),
            time_remaining_hours=10,
            logged_events={"campaign_launch", "final_stretch"},
            chattiness="normal",
        )
        assert event is None


# ---------------------------------------------------------------------------
# detect_milestone — lead change
# ---------------------------------------------------------------------------


class TestDetectLeadChange:
    def test_lead_change_detected_when_first_place_changes(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=5, total_voted=3),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote"},
            chattiness="hype",
            current_leader_id=2,
            previous_leader_id=1,
        )
        assert event == "lead_change"

    def test_lead_change_not_detected_when_first_place_same(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=5, total_voted=3),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote"},
            chattiness="hype",
            current_leader_id=1,
            previous_leader_id=1,
        )
        assert event != "lead_change"

    def test_lead_change_not_detected_without_previous_leader(self):
        # No previous leader means we can't detect a change
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=5, total_voted=3),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote"},
            chattiness="hype",
            current_leader_id=1,
            previous_leader_id=None,
        )
        assert event != "lead_change"

    def test_lead_change_not_available_in_normal_mode(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=5, total_voted=3),
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote"},
            chattiness="normal",
            current_leader_id=2,
            previous_leader_id=1,
        )
        assert event != "lead_change"

    def test_lead_change_not_available_in_quiet_mode(self):
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=5, total_voted=3),
            time_remaining_hours=100,
            logged_events={"campaign_launch"},
            chattiness="quiet",
            current_leader_id=2,
            previous_leader_id=1,
        )
        assert event != "lead_change"


# ---------------------------------------------------------------------------
# detect_milestone — priority: lead change beats participation milestone
# ---------------------------------------------------------------------------


class TestDetectMilestonePriority:
    def test_priority_lead_change_over_milestone(self):
        """When both lead_change and a participation milestone fire, lead_change wins."""
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=2),  # 50%
            time_remaining_hours=100,
            logged_events={"campaign_launch", "first_vote", "milestone_25"},
            chattiness="hype",
            current_leader_id=2,
            previous_leader_id=1,
        )
        assert event == "lead_change"

    def test_priority_last_call_over_milestone(self):
        """last_call beats milestone_50 in priority."""
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=2),  # 50%
            time_remaining_hours=0.5,
            logged_events={"campaign_launch", "first_vote", "milestone_25", "final_stretch"},
            chattiness="normal",
        )
        assert event == "last_call"

    def test_all_voted_beats_milestone_75(self):
        """all_voted takes priority over milestone_75."""
        event = detect_milestone(
            campaign_status="live",
            stats=_stats(total_eligible=4, total_voted=4, all_voted=True),
            time_remaining_hours=50,
            logged_events={"campaign_launch", "first_vote", "milestone_25", "milestone_50"},
            chattiness="hype",
        )
        assert event == "all_voted"


# ---------------------------------------------------------------------------
# detect_milestone — closed campaign
# ---------------------------------------------------------------------------


class TestDetectMilestoneClosed:
    def test_campaign_closed_event_fires_after_close(self):
        event = detect_milestone(
            campaign_status="closed",
            stats=_stats(total_eligible=10, total_voted=7),
            time_remaining_hours=0,
            logged_events={"campaign_launch"},
            chattiness="normal",
        )
        assert event == "campaign_closed"

    def test_all_voted_fires_if_all_voted_on_close(self):
        event = detect_milestone(
            campaign_status="closed",
            stats=_stats(total_eligible=4, total_voted=4, all_voted=True),
            time_remaining_hours=0,
            logged_events={"campaign_launch"},
            chattiness="normal",
        )
        assert event == "all_voted"

    def test_closed_events_not_repeated(self):
        event = detect_milestone(
            campaign_status="closed",
            stats=_stats(total_eligible=10, total_voted=7),
            time_remaining_hours=0,
            logged_events={"campaign_launch", "campaign_closed"},
            chattiness="normal",
        )
        assert event is None

    def test_closed_campaign_no_live_milestones(self):
        """Closed campaigns don't check first_vote, lead_change, etc."""
        event = detect_milestone(
            campaign_status="closed",
            stats=_stats(total_eligible=10, total_voted=1),
            time_remaining_hours=0,
            logged_events={"campaign_launch", "campaign_closed"},
            chattiness="hype",
        )
        assert event is None

    def test_campaign_closed_not_in_quiet_but_still_fires(self):
        # campaign_closed IS in quiet triggers
        event = detect_milestone(
            campaign_status="closed",
            stats=_stats(total_eligible=10, total_voted=7),
            time_remaining_hours=0,
            logged_events=set(),
            chattiness="quiet",
        )
        assert event in ("campaign_closed", "all_voted")


# ---------------------------------------------------------------------------
# generate_message — template filling
# ---------------------------------------------------------------------------


class TestGenerateMessage:
    def test_message_template_fills_correctly(self):
        data = {
            "title": "Guild Art Vote",
            "description": "Pick your favorite!",
            "vote_url": "https://example.com/vote/1",
            "close_date": "Feb 28 at 9:00 PM UTC",
        }
        msg = generate_message("campaign_launch", data)
        assert "Guild Art Vote" in msg
        assert "https://example.com/vote/1" in msg

    def test_lead_change_template_fills_correctly(self):
        data = {
            "new_leader": "Trogmoon",
            "old_leader": "Skatefarm",
            "new_score": "15",
            "old_score": "12",
            "vote_url": "https://example.com/vote/1",
        }
        msg = generate_message("lead_change", data)
        assert "Trogmoon" in msg
        assert "Skatefarm" in msg

    def test_results_template_fills_correctly(self):
        data = {
            "title": "Guild Art Vote",
            "total_voters": 10,
            "first_name": "Trogmoon",
            "first_score": "30",
            "second_name": "Skatefarm",
            "second_score": "24",
            "third_name": "Mito",
            "third_score": "18",
            "results_url": "https://example.com/vote/1",
        }
        msg = generate_message("campaign_closed", data)
        assert "Guild Art Vote" in msg
        assert "Trogmoon" in msg
        assert "30" in msg

    def test_unknown_event_type_does_not_crash(self):
        msg = generate_message("nonexistent_event", {"key": "value"})
        # Should return something without raising
        assert isinstance(msg, str)

    def test_missing_template_key_does_not_crash(self):
        # Provide empty data — format will fail gracefully
        msg = generate_message("campaign_launch", {})
        assert isinstance(msg, str)
