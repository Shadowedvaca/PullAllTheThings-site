"""Unit tests for sv_common.feedback package (Phase F.2)."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sv_common.feedback._privacy import make_privacy_token
from sv_common.feedback._hub_client import HubNotConfiguredError, HubSyncError
from sv_common.feedback import submit_feedback


# ---------------------------------------------------------------------------
# _privacy.py tests
# ---------------------------------------------------------------------------


def test_make_privacy_token_deterministic():
    with patch.dict(os.environ, {"FEEDBACK_PRIVACY_SALT": "testsalt"}):
        t1 = make_privacy_token("mike@example.com", is_anonymous=False)
        t2 = make_privacy_token("mike@example.com", is_anonymous=False)
    assert t1 is not None
    assert t1 == t2


def test_make_privacy_token_case_insensitive():
    with patch.dict(os.environ, {"FEEDBACK_PRIVACY_SALT": "testsalt"}):
        t1 = make_privacy_token("Mike@Example.com", is_anonymous=False)
        t2 = make_privacy_token("mike@example.com", is_anonymous=False)
    assert t1 == t2


def test_make_privacy_token_anonymous_returns_none():
    with patch.dict(os.environ, {"FEEDBACK_PRIVACY_SALT": "testsalt"}):
        result = make_privacy_token("mike@example.com", is_anonymous=True)
    assert result is None


def test_make_privacy_token_no_contact_returns_none():
    with patch.dict(os.environ, {"FEEDBACK_PRIVACY_SALT": "testsalt"}):
        assert make_privacy_token(None, False) is None
        assert make_privacy_token("", False) is None


def test_make_privacy_token_no_salt_returns_none():
    with patch.dict(os.environ, {"FEEDBACK_PRIVACY_SALT": ""}, clear=False):
        result = make_privacy_token("mike@example.com", is_anonymous=False)
    assert result is None


# ---------------------------------------------------------------------------
# submit_feedback() — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_feedback_stores_locally_and_resolves():
    pool = MagicMock()

    with (
        patch("sv_common.feedback._insert_submission", new_callable=AsyncMock, return_value=42) as mock_insert,
        patch("sv_common.feedback._update_hub_ref", new_callable=AsyncMock) as mock_update,
        patch("sv_common.feedback.post_to_hub", new_callable=AsyncMock, return_value=99),
        patch("sv_common.feedback.resolve_issue", new_callable=AsyncMock) as mock_resolve,
        patch("sv_common.feedback.report_error", new_callable=AsyncMock) as mock_report,
    ):
        result = await submit_feedback(
            pool,
            score=8,
            raw_feedback="Great guild!",
            is_authenticated_user=True,
        )

    mock_insert.assert_called_once()
    assert mock_insert.call_args.kwargs["score"] == 8
    assert mock_insert.call_args.kwargs["is_anonymous"] is False
    mock_update.assert_called_once()
    mock_resolve.assert_called_once_with(pool, "feedback_hub_sync_failed")
    mock_report.assert_not_called()
    assert result["id"] == 42
    assert result["hub_feedback_id"] == 99


@pytest.mark.asyncio
async def test_submit_feedback_anonymous_clears_contact():
    pool = MagicMock()

    with (
        patch("sv_common.feedback._insert_submission", new_callable=AsyncMock, return_value=1) as mock_insert,
        patch("sv_common.feedback._update_hub_ref", new_callable=AsyncMock),
        patch("sv_common.feedback.post_to_hub", new_callable=AsyncMock, return_value=5),
        patch("sv_common.feedback.resolve_issue", new_callable=AsyncMock),
        patch("sv_common.feedback.report_error", new_callable=AsyncMock),
    ):
        await submit_feedback(
            pool,
            score=5,
            raw_feedback="Feedback text",
            contact_info="mike@test.com",
            is_anonymous=True,
        )

    insert_kwargs = mock_insert.call_args.kwargs
    assert insert_kwargs["contact_info"] is None
    assert insert_kwargs["privacy_token"] is None


# ---------------------------------------------------------------------------
# submit_feedback() — Hub failure paths: errors go through the framework
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_feedback_hub_not_configured_reports_error():
    pool = MagicMock()

    with (
        patch("sv_common.feedback._insert_submission", new_callable=AsyncMock, return_value=7),
        patch("sv_common.feedback._update_hub_ref", new_callable=AsyncMock) as mock_update,
        patch("sv_common.feedback.post_to_hub", new_callable=AsyncMock,
              side_effect=HubNotConfiguredError("FEEDBACK_HUB_URL not set")),
        patch("sv_common.feedback.report_error", new_callable=AsyncMock) as mock_report,
        patch("sv_common.feedback.resolve_issue", new_callable=AsyncMock) as mock_resolve,
    ):
        result = await submit_feedback(pool, score=6, raw_feedback="Good experience")

    mock_update.assert_not_called()
    mock_resolve.assert_not_called()
    mock_report.assert_called_once()
    report_kwargs = mock_report.call_args.kwargs
    assert report_kwargs["issue_type"] == "feedback_hub_sync_failed"
    assert report_kwargs["severity"] == "warning"
    assert report_kwargs["source_module"] == "sv_common.feedback"
    assert result["hub_feedback_id"] is None


@pytest.mark.asyncio
async def test_submit_feedback_hub_sync_error_reports_error():
    pool = MagicMock()

    with (
        patch("sv_common.feedback._insert_submission", new_callable=AsyncMock, return_value=7),
        patch("sv_common.feedback._update_hub_ref", new_callable=AsyncMock) as mock_update,
        patch("sv_common.feedback.post_to_hub", new_callable=AsyncMock,
              side_effect=HubSyncError("connection refused")),
        patch("sv_common.feedback.report_error", new_callable=AsyncMock) as mock_report,
        patch("sv_common.feedback.resolve_issue", new_callable=AsyncMock) as mock_resolve,
    ):
        result = await submit_feedback(pool, score=3, raw_feedback="Needs work")

    mock_update.assert_not_called()
    mock_resolve.assert_not_called()
    mock_report.assert_called_once()
    report_kwargs = mock_report.call_args.kwargs
    assert report_kwargs["issue_type"] == "feedback_hub_sync_failed"
    assert report_kwargs["severity"] == "warning"
    assert "connection refused" in report_kwargs["summary"]
    assert result["hub_feedback_id"] is None


@pytest.mark.asyncio
async def test_submit_feedback_local_record_saved_on_hub_failure():
    """Local insert happens before Hub call; always saved regardless of Hub outcome."""
    pool = MagicMock()

    with (
        patch("sv_common.feedback._insert_submission", new_callable=AsyncMock, return_value=99) as mock_insert,
        patch("sv_common.feedback._update_hub_ref", new_callable=AsyncMock),
        patch("sv_common.feedback.post_to_hub", new_callable=AsyncMock,
              side_effect=HubSyncError("timeout")),
        patch("sv_common.feedback.report_error", new_callable=AsyncMock),
        patch("sv_common.feedback.resolve_issue", new_callable=AsyncMock),
    ):
        result = await submit_feedback(pool, score=5, raw_feedback="Decent")

    mock_insert.assert_called_once()
    assert result["id"] == 99


# ---------------------------------------------------------------------------
# submit_feedback() — input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_feedback_empty_text_raises():
    pool = MagicMock()
    with pytest.raises(ValueError, match="raw_feedback"):
        await submit_feedback(pool, score=5, raw_feedback="   ")


@pytest.mark.asyncio
async def test_submit_feedback_invalid_score_raises():
    pool = MagicMock()
    with pytest.raises(ValueError, match="score"):
        await submit_feedback(pool, score=0, raw_feedback="Fine")
    with pytest.raises(ValueError, match="score"):
        await submit_feedback(pool, score=11, raw_feedback="Fine")


# ---------------------------------------------------------------------------
# _hub_client.py — raises, never swallows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_to_hub_raises_when_unconfigured():
    from sv_common.feedback._hub_client import post_to_hub

    with patch.dict(os.environ, {"FEEDBACK_HUB_URL": "", "FEEDBACK_INGEST_KEY": ""}):
        with pytest.raises(HubNotConfiguredError):
            await post_to_hub(
                program_name="patt",
                score=7,
                raw_feedback="Good",
                is_authenticated_user=False,
                is_anonymous=False,
                privacy_token=None,
            )


@pytest.mark.asyncio
async def test_post_to_hub_raises_hub_sync_error_on_http_failure():
    import httpx
    from sv_common.feedback._hub_client import post_to_hub

    with (
        patch.dict(os.environ, {
            "FEEDBACK_HUB_URL": "https://hub.example.com",
            "FEEDBACK_INGEST_KEY": "testkey",
        }),
        patch("sv_common.feedback._hub_client.httpx.AsyncClient") as mock_client_class,
    ):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("connection refused")
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(HubSyncError):
            await post_to_hub(
                program_name="patt",
                score=7,
                raw_feedback="Good",
                is_authenticated_user=False,
                is_anonymous=False,
                privacy_token=None,
            )


@pytest.mark.asyncio
async def test_post_to_hub_raises_hub_sync_error_on_missing_id():
    """Hub returns 200 but omits hub_feedback_id — treated as a sync error."""
    from sv_common.feedback._hub_client import post_to_hub

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"ok": True}  # missing hub_feedback_id

    with (
        patch.dict(os.environ, {
            "FEEDBACK_HUB_URL": "https://hub.example.com",
            "FEEDBACK_INGEST_KEY": "testkey",
        }),
        patch("sv_common.feedback._hub_client.httpx.AsyncClient") as mock_client_class,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(HubSyncError, match="hub_feedback_id"):
            await post_to_hub(
                program_name="patt",
                score=7,
                raw_feedback="Good",
                is_authenticated_user=False,
                is_anonymous=False,
                privacy_token=None,
            )
