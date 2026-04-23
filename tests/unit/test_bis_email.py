"""
Unit tests for Phase 1.7-E — BIS email composition and SMTP send.

Tests:
1.  compose_bis_report: subject contains date
2.  compose_bis_report: no-change subject says "No changes"
3.  compose_bis_report: change subject includes added/removed counts
4.  compose_bis_report: patch_signal=True subject reflects changes
5.  compose_bis_report: delta_added items appear in HTML body
6.  compose_bis_report: delta_removed items appear in HTML body
7.  compose_bis_report: zero failures → no failure section in HTML
8.  compose_bis_report: nonzero failures → failure section present
9.  compose_bis_report: patch_signal → alert present in HTML
10. compose_bis_report: quiet run → quiet confirmation present in HTML
11. compose_bis_report: delta_added as JSON string is parsed correctly
12. send_email: calls aiosmtplib.send with correct args
13. send_email: SMTP exception raises EmailSendError
14. send_email: port 465 uses use_tls=True, start_tls=False
15. send_email: port 587 uses use_tls=False, start_tls=True
16. SMTP password round-trip: encrypt then decrypt gives original value
17. run_bis_daily_sync: does not crash when SMTP not configured
18. run_bis_daily_sync: skips email when smtp_config is None
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_data(
    targets_checked=5,
    targets_changed=2,
    targets_failed=0,
    targets_skipped=1,
    bis_entries_before=1000,
    bis_entries_after=1005,
    trinket_ratings_before=200,
    trinket_ratings_after=200,
    delta_added=None,
    delta_removed=None,
    patch_signal=False,
    duration_seconds=42.5,
    notes=None,
    triggered_by="scheduled",
):
    return {
        "run_at": datetime(2026, 4, 22, 4, 0, 0, tzinfo=timezone.utc),
        "triggered_by": triggered_by,
        "targets_checked": targets_checked,
        "targets_changed": targets_changed,
        "targets_failed": targets_failed,
        "targets_skipped": targets_skipped,
        "bis_entries_before": bis_entries_before,
        "bis_entries_after": bis_entries_after,
        "trinket_ratings_before": trinket_ratings_before,
        "trinket_ratings_after": trinket_ratings_after,
        "delta_added": delta_added or [],
        "delta_removed": delta_removed or [],
        "patch_signal": patch_signal,
        "duration_seconds": duration_seconds,
        "notes": notes,
    }


def _added_item(spec_id=1, source_id=2, slot="head", item_id=100, name="Test Helm"):
    return {
        "spec_id": spec_id,
        "source_id": source_id,
        "slot": slot,
        "blizzard_item_id": item_id,
        "item_name": name,
    }


# ---------------------------------------------------------------------------
# 1–4. Subject line
# ---------------------------------------------------------------------------


class TestComposeBisReportSubject:
    def _compose(self, **kwargs):
        from sv_common.guild_sync.bis_email import compose_bis_report
        run_data = _make_run_data(**kwargs)
        subject, _ = compose_bis_report(run_data, guild_name="PATT", app_url="https://example.com")
        return subject

    def test_subject_contains_date(self):
        subject = self._compose()
        assert "2026-04-22" in subject

    def test_no_change_subject(self):
        subject = self._compose(targets_changed=0, delta_added=[], delta_removed=[])
        assert "No changes" in subject

    def test_change_subject_includes_counts(self):
        added = [_added_item()]
        removed = [_added_item(item_id=99, name="Old Helm")]
        subject = self._compose(
            targets_changed=1,
            delta_added=added,
            delta_removed=removed,
        )
        assert "+1" in subject
        assert "1" in subject  # removed count

    def test_patch_signal_subject_shows_changes(self):
        subject = self._compose(patch_signal=True, targets_changed=0)
        # patch_signal forces the "changes" branch
        assert "No changes" not in subject


# ---------------------------------------------------------------------------
# 5–10. HTML body content
# ---------------------------------------------------------------------------


class TestComposeBisReportHtml:
    def _html(self, **kwargs):
        from sv_common.guild_sync.bis_email import compose_bis_report
        run_data = _make_run_data(**kwargs)
        _, html = compose_bis_report(run_data, guild_name="PATT", app_url="https://example.com")
        return html

    def test_added_item_name_in_html(self):
        added = [_added_item(name="Legendary Helm of Testing")]
        html = self._html(delta_added=added, targets_changed=1)
        assert "Legendary Helm of Testing" in html

    def test_removed_item_name_in_html(self):
        removed = [_added_item(item_id=50, name="Old Garbage Chest")]
        html = self._html(delta_removed=removed, targets_changed=1)
        assert "Old Garbage Chest" in html

    def test_no_failure_section_when_zero_failures(self):
        html = self._html(targets_failed=0)
        assert "Target Failures" not in html

    def test_failure_section_present_when_failures(self):
        html = self._html(targets_failed=3)
        assert "Target Failures" in html

    def test_patch_signal_alert_in_html(self):
        html = self._html(patch_signal=True)
        assert "Patch signal" in html

    def test_quiet_confirmation_when_no_changes(self):
        html = self._html(targets_changed=0, delta_added=[], delta_removed=[], patch_signal=False)
        assert "No changes detected" in html

    def test_delta_added_as_json_string_parsed(self):
        added = [_added_item(name="Parsed Helm")]
        html = self._html(delta_added=json.dumps(added), targets_changed=1)
        assert "Parsed Helm" in html

    def test_guild_name_in_html(self):
        from sv_common.guild_sync.bis_email import compose_bis_report
        run_data = _make_run_data()
        _, html = compose_bis_report(run_data, guild_name="My Awesome Guild", app_url="")
        assert "My Awesome Guild" in html

    def test_admin_url_in_html(self):
        from sv_common.guild_sync.bis_email import compose_bis_report
        run_data = _make_run_data()
        _, html = compose_bis_report(run_data, guild_name="PATT", app_url="https://patt.test")
        assert "patt.test" in html


# ---------------------------------------------------------------------------
# 12–15. send_email SMTP behaviour
# ---------------------------------------------------------------------------


class TestSendEmail:
    def _make_smtp_config(self, port=587):
        from sv_common.config_cache import SmtpConfig
        return SmtpConfig(
            host="smtp.example.com",
            port=port,
            user="user@example.com",
            password="plaintext_password",
            from_address="noreply@example.com",
        )

    @pytest.mark.asyncio
    async def test_calls_aiosmtplib_send(self):
        from sv_common.email import send_email

        cfg = self._make_smtp_config()
        with patch("sv_common.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email(cfg, "to@example.com", "Subject", "<p>Body</p>")

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        assert kwargs["hostname"] == "smtp.example.com"
        assert kwargs["username"] == "user@example.com"
        assert kwargs["password"] == "plaintext_password"

    @pytest.mark.asyncio
    async def test_smtp_exception_raises_email_send_error(self):
        from sv_common.email import send_email, EmailSendError
        import aiosmtplib

        cfg = self._make_smtp_config()
        with patch("sv_common.email.aiosmtplib.send",
                   new_callable=AsyncMock,
                   side_effect=aiosmtplib.SMTPException("connection refused")):
            with pytest.raises(EmailSendError):
                await send_email(cfg, "to@example.com", "Subject", "<p>Body</p>")

    @pytest.mark.asyncio
    async def test_port_465_uses_ssl(self):
        from sv_common.email import send_email

        cfg = self._make_smtp_config(port=465)
        with patch("sv_common.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email(cfg, "to@example.com", "Subject", "<p>Body</p>")

        kwargs = mock_send.call_args.kwargs
        assert kwargs.get("use_tls") is True
        assert kwargs.get("start_tls") is False

    @pytest.mark.asyncio
    async def test_port_587_uses_starttls(self):
        from sv_common.email import send_email

        cfg = self._make_smtp_config(port=587)
        with patch("sv_common.email.aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await send_email(cfg, "to@example.com", "Subject", "<p>Body</p>")

        kwargs = mock_send.call_args.kwargs
        assert kwargs.get("use_tls") is False
        assert kwargs.get("start_tls") is True


# ---------------------------------------------------------------------------
# 16. SMTP password round-trip
# ---------------------------------------------------------------------------


class TestSmtpPasswordEncryption:
    def test_encrypt_decrypt_round_trip(self):
        from sv_common.crypto import encrypt_secret, decrypt_secret

        jwt_secret = "test-jwt-secret-key-for-unit-tests-only"
        original = "my_smtp_password_123!"

        encrypted = encrypt_secret(original, jwt_secret)
        assert encrypted != original

        decrypted = decrypt_secret(encrypted, jwt_secret)
        assert decrypted == original


# ---------------------------------------------------------------------------
# 17–18. run_bis_daily_sync email integration
# ---------------------------------------------------------------------------


def _make_scheduler():
    from sv_common.guild_sync.scheduler import GuildSyncScheduler
    import os

    db_pool = MagicMock()
    bot = MagicMock()

    with patch("sv_common.guild_sync.scheduler.BlizzardClient"), \
         patch("sv_common.guild_sync.scheduler.get_site_config", return_value={}), \
         patch.dict(os.environ, {"BLIZZARD_CLIENT_ID": "x", "BLIZZARD_CLIENT_SECRET": "y"}):
        scheduler = GuildSyncScheduler(db_pool, bot, 99999)

    scheduler.scheduler = MagicMock()
    return scheduler


class TestBisDailySyncEmailIntegration:
    @pytest.mark.asyncio
    async def test_no_crash_when_smtp_not_configured(self):
        """Job completes normally when no SMTP config is set."""
        scheduler = _make_scheduler()

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=1)
        conn.execute = AsyncMock()

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_smtp_config", return_value=None), \
             patch("sv_common.guild_sync.scheduler.get_bis_report_email", return_value=None), \
             patch("sv_common.guild_sync.scheduler._snapshot_bis_entries",
                   new_callable=AsyncMock, return_value={}), \
             patch("sv_common.guild_sync.scheduler._rebuild_bis_from_landing",
                   new_callable=AsyncMock), \
             patch("sv_common.guild_sync.scheduler._rebuild_trinket_ratings_from_landing",
                   new_callable=AsyncMock), \
             patch("sv_common.guild_sync.scheduler._rebuild_item_popularity_from_landing",
                   new_callable=AsyncMock):
            await scheduler.run_bis_daily_sync()

    @pytest.mark.asyncio
    async def test_email_skipped_when_no_smtp_config(self):
        """When smtp_config is None, send_email is never called."""
        scheduler = _make_scheduler()

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchval = AsyncMock(return_value=1)
        conn.execute = AsyncMock()

        pool = MagicMock()
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        pool.acquire = MagicMock(return_value=cm)
        scheduler.db_pool = pool

        with patch("sv_common.guild_sync.scheduler.get_smtp_config", return_value=None), \
             patch("sv_common.guild_sync.scheduler.get_bis_report_email", return_value=None), \
             patch("sv_common.guild_sync.scheduler._snapshot_bis_entries",
                   new_callable=AsyncMock, return_value={}), \
             patch("sv_common.guild_sync.scheduler._rebuild_bis_from_landing",
                   new_callable=AsyncMock), \
             patch("sv_common.guild_sync.scheduler._rebuild_trinket_ratings_from_landing",
                   new_callable=AsyncMock), \
             patch("sv_common.guild_sync.scheduler._rebuild_item_popularity_from_landing",
                   new_callable=AsyncMock), \
             patch("sv_common.email.send_email", new_callable=AsyncMock) as mock_send:
            await scheduler.run_bis_daily_sync()

        mock_send.assert_not_called()
