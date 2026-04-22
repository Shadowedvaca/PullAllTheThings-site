"""
Unit tests for Phase 1.7-A — daily BIS update schema foundations.

Tests:
1. SiteConfig model includes all 7 new SMTP/email columns
2. BisScrapeTarget model includes is_active, check_interval_days, next_check_at
3. BisDailyRun model exists with all expected columns
4. SmtpConfig dataclass round-trips correctly
5. get_smtp_config() returns None when fields are missing
6. get_smtp_config() returns SmtpConfig when all fields present
7. get_bis_report_email() returns None when not set
8. get_bis_report_email() returns value when set
9. get_bis_encounter_baseline() returns None when not set
10. get_bis_encounter_baseline() returns int when set
"""

import pytest
from decimal import Decimal


# ---------------------------------------------------------------------------
# Model column presence tests
# ---------------------------------------------------------------------------


class TestSiteConfigNewColumns:
    def test_has_bis_encounter_count(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "bis_encounter_count")

    def test_has_bis_report_email(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "bis_report_email")

    def test_has_smtp_host(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "smtp_host")

    def test_has_smtp_port(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "smtp_port")

    def test_has_smtp_user(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "smtp_user")

    def test_has_smtp_password_encrypted(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "smtp_password_encrypted")

    def test_has_smtp_from_address(self):
        from sv_common.db.models import SiteConfig
        assert hasattr(SiteConfig, "smtp_from_address")


class TestBisScrapeTargetNewColumns:
    def test_has_is_active(self):
        from sv_common.db.models import BisScrapeTarget
        assert hasattr(BisScrapeTarget, "is_active")

    def test_has_check_interval_days(self):
        from sv_common.db.models import BisScrapeTarget
        assert hasattr(BisScrapeTarget, "check_interval_days")

    def test_has_next_check_at(self):
        from sv_common.db.models import BisScrapeTarget
        assert hasattr(BisScrapeTarget, "next_check_at")


class TestBisDailyRunModel:
    def test_model_exists(self):
        from sv_common.db.models import BisDailyRun
        assert BisDailyRun is not None

    def test_has_run_at(self):
        from sv_common.db.models import BisDailyRun
        assert hasattr(BisDailyRun, "run_at")

    def test_has_triggered_by(self):
        from sv_common.db.models import BisDailyRun
        assert hasattr(BisDailyRun, "triggered_by")

    def test_has_patch_signal(self):
        from sv_common.db.models import BisDailyRun
        assert hasattr(BisDailyRun, "patch_signal")

    def test_has_target_counts(self):
        from sv_common.db.models import BisDailyRun
        for col in ("targets_checked", "targets_changed", "targets_unchanged",
                    "targets_failed", "targets_skipped"):
            assert hasattr(BisDailyRun, col), f"missing column: {col}"

    def test_has_entry_counts(self):
        from sv_common.db.models import BisDailyRun
        for col in ("bis_entries_before", "bis_entries_after",
                    "trinket_ratings_before", "trinket_ratings_after"):
            assert hasattr(BisDailyRun, col), f"missing column: {col}"

    def test_has_delta_jsonb(self):
        from sv_common.db.models import BisDailyRun
        assert hasattr(BisDailyRun, "delta_added")
        assert hasattr(BisDailyRun, "delta_removed")

    def test_has_duration_and_email_fields(self):
        from sv_common.db.models import BisDailyRun
        assert hasattr(BisDailyRun, "duration_seconds")
        assert hasattr(BisDailyRun, "email_sent_at")
        assert hasattr(BisDailyRun, "notes")

    def test_correct_schema(self):
        from sv_common.db.models import BisDailyRun
        assert BisDailyRun.__table__.schema == "landing"


# ---------------------------------------------------------------------------
# SmtpConfig dataclass tests
# ---------------------------------------------------------------------------


class TestSmtpConfig:
    def test_round_trip(self):
        from sv_common.config_cache import SmtpConfig
        cfg = SmtpConfig(
            host="smtp.example.com",
            port=587,
            user="user@example.com",
            password="enc_secret",
            from_address="noreply@example.com",
        )
        assert cfg.host == "smtp.example.com"
        assert cfg.port == 587
        assert cfg.user == "user@example.com"
        assert cfg.password == "enc_secret"
        assert cfg.from_address == "noreply@example.com"


# ---------------------------------------------------------------------------
# config_cache getter tests
# ---------------------------------------------------------------------------


class TestGetSmtpConfig:
    def setup_method(self):
        import sv_common.config_cache as cc
        cc._cache.clear()

    def test_returns_none_when_empty(self):
        from sv_common.config_cache import get_smtp_config
        assert get_smtp_config() is None

    def test_returns_none_when_partial(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_smtp_config
        cc._cache.update({"smtp_host": "smtp.example.com"})
        assert get_smtp_config() is None

    def test_returns_smtp_config_when_complete(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_smtp_config, SmtpConfig
        cc._cache.update({
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_user": "user@example.com",
            "smtp_password_encrypted": "enc_secret",
            "smtp_from_address": "noreply@example.com",
        })
        result = get_smtp_config()
        assert isinstance(result, SmtpConfig)
        assert result.host == "smtp.example.com"
        assert result.port == 587

    def test_defaults_port_to_587_when_not_set(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_smtp_config
        cc._cache.update({
            "smtp_host": "smtp.example.com",
            "smtp_port": None,
            "smtp_user": "user@example.com",
            "smtp_password_encrypted": "enc_secret",
            "smtp_from_address": "noreply@example.com",
        })
        result = get_smtp_config()
        assert result is not None
        assert result.port == 587


class TestGetBisReportEmail:
    def setup_method(self):
        import sv_common.config_cache as cc
        cc._cache.clear()

    def test_returns_none_when_not_set(self):
        from sv_common.config_cache import get_bis_report_email
        assert get_bis_report_email() is None

    def test_returns_none_when_empty_string(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_bis_report_email
        cc._cache["bis_report_email"] = ""
        assert get_bis_report_email() is None

    def test_returns_email_when_set(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_bis_report_email
        cc._cache["bis_report_email"] = "trog@example.com"
        assert get_bis_report_email() == "trog@example.com"


class TestGetBisEncounterBaseline:
    def setup_method(self):
        import sv_common.config_cache as cc
        cc._cache.clear()

    def test_returns_none_when_not_set(self):
        from sv_common.config_cache import get_bis_encounter_baseline
        assert get_bis_encounter_baseline() is None

    def test_returns_int_when_set(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_bis_encounter_baseline
        cc._cache["bis_encounter_count"] = 42
        result = get_bis_encounter_baseline()
        assert result == 42
        assert isinstance(result, int)

    def test_returns_int_from_string(self):
        import sv_common.config_cache as cc
        from sv_common.config_cache import get_bis_encounter_baseline
        cc._cache["bis_encounter_count"] = "42"
        result = get_bis_encounter_baseline()
        assert result == 42
        assert isinstance(result, int)
