"""
Unit tests for Phase F.3 — POST /api/v1/feedback endpoint.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSubmitValidFeedback:
    @pytest.mark.asyncio
    async def test_valid_submission_returns_ok(self):
        mock_result = {"id": 1, "hub_feedback_id": 42, "program_name": "patt-guild-portal"}

        with patch(
            "guild_portal.api.feedback_routes.submit_feedback",
            new=AsyncMock(return_value=mock_result),
        ) as mock_submit:
            from guild_portal.api.feedback_routes import submit_feedback_endpoint, FeedbackBody

            mock_request = MagicMock()
            mock_request.app.state.guild_sync_pool = MagicMock()
            mock_request.cookies = {}
            mock_request.headers = {}

            body = FeedbackBody(score=7, feedback="Really useful!")
            result = await submit_feedback_endpoint(body=body, request=mock_request)

            assert result == {"ok": True}
            mock_submit.assert_awaited_once()
            call_kwargs = mock_submit.call_args.kwargs
            assert call_kwargs["score"] == 7
            assert call_kwargs["raw_feedback"] == "Really useful!"


class TestSubmitInvalidScore:
    def test_score_zero_rejected(self):
        from pydantic import ValidationError
        from guild_portal.api.feedback_routes import FeedbackBody

        with pytest.raises(ValidationError):
            FeedbackBody(score=0, feedback="test")

    def test_score_eleven_rejected(self):
        from pydantic import ValidationError
        from guild_portal.api.feedback_routes import FeedbackBody

        with pytest.raises(ValidationError):
            FeedbackBody(score=11, feedback="test")


class TestSubmitEmptyFeedback:
    def test_empty_feedback_rejected(self):
        from pydantic import ValidationError
        from guild_portal.api.feedback_routes import FeedbackBody

        with pytest.raises(ValidationError):
            FeedbackBody(score=5, feedback="")


class TestSubmitAnonymous:
    @pytest.mark.asyncio
    async def test_anonymous_flag_passed_through(self):
        mock_result = {"id": 2, "hub_feedback_id": None, "program_name": "patt-guild-portal"}

        with patch(
            "guild_portal.api.feedback_routes.submit_feedback",
            new=AsyncMock(return_value=mock_result),
        ) as mock_submit:
            from guild_portal.api.feedback_routes import submit_feedback_endpoint, FeedbackBody

            mock_request = MagicMock()
            mock_request.app.state.guild_sync_pool = MagicMock()
            mock_request.cookies = {}
            mock_request.headers = {}

            body = FeedbackBody(score=8, feedback="Great tool!", is_anonymous=True, contact_info="test@test.com")
            result = await submit_feedback_endpoint(body=body, request=mock_request)

            assert result == {"ok": True}
            call_kwargs = mock_submit.call_args.kwargs
            assert call_kwargs["is_anonymous"] is True


class TestSubmitNoAuthStillWorks:
    @pytest.mark.asyncio
    async def test_auth_exception_does_not_block_submission(self):
        mock_result = {"id": 3, "hub_feedback_id": None, "program_name": "patt-guild-portal"}

        with patch(
            "guild_portal.api.feedback_routes.submit_feedback",
            new=AsyncMock(return_value=mock_result),
        ) as mock_submit:
            with patch(
                "guild_portal.api.feedback_routes._decode_token",
                side_effect=Exception("auth failure"),
            ):
                from guild_portal.api.feedback_routes import submit_feedback_endpoint, FeedbackBody

                mock_request = MagicMock()
                mock_request.app.state.guild_sync_pool = MagicMock()
                mock_request.cookies = {"patt_token": "bad-token"}
                mock_request.headers = {}

                body = FeedbackBody(score=5, feedback="Works anonymously")
                result = await submit_feedback_endpoint(body=body, request=mock_request)

                assert result == {"ok": True}
                call_kwargs = mock_submit.call_args.kwargs
                assert call_kwargs["is_authenticated_user"] is False
