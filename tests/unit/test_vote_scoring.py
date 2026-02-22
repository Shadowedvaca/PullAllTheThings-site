"""Unit tests for ranked-choice vote scoring logic.

Pure function tests — no database, no network, no external services.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret-key-for-scoring")
os.environ.setdefault("APP_ENV", "testing")

import pytest
from patt.services.vote_service import compute_scores, rank_results


# ---------------------------------------------------------------------------
# compute_scores
# ---------------------------------------------------------------------------


class TestComputeScores:
    def test_ranked_choice_first_place_gets_three_points(self):
        votes = [{"entry_id": 1, "rank": 1}]
        scores = compute_scores(votes)
        assert scores[1]["weighted_score"] == 3
        assert scores[1]["first"] == 1
        assert scores[1]["second"] == 0
        assert scores[1]["third"] == 0

    def test_ranked_choice_second_place_gets_two_points(self):
        votes = [{"entry_id": 2, "rank": 2}]
        scores = compute_scores(votes)
        assert scores[2]["weighted_score"] == 2
        assert scores[2]["first"] == 0
        assert scores[2]["second"] == 1
        assert scores[2]["third"] == 0

    def test_ranked_choice_third_place_gets_one_point(self):
        votes = [{"entry_id": 3, "rank": 3}]
        scores = compute_scores(votes)
        assert scores[3]["weighted_score"] == 1
        assert scores[3]["first"] == 0
        assert scores[3]["second"] == 0
        assert scores[3]["third"] == 1

    def test_scoring_with_multiple_voters(self):
        """3 voters each pick different #1 choices; entry 1 gets 2 first-place votes."""
        votes = [
            {"entry_id": 1, "rank": 1},
            {"entry_id": 2, "rank": 2},
            {"entry_id": 3, "rank": 3},
            {"entry_id": 1, "rank": 1},
            {"entry_id": 3, "rank": 2},
            {"entry_id": 2, "rank": 3},
        ]
        scores = compute_scores(votes)
        # Entry 1: 2 firsts = 6 pts
        assert scores[1]["weighted_score"] == 6
        assert scores[1]["first"] == 2
        # Entry 2: 1 second + 1 third = 3 pts
        assert scores[2]["weighted_score"] == 3
        assert scores[2]["second"] == 1
        assert scores[2]["third"] == 1
        # Entry 3: 1 third + 1 second = 3 pts
        assert scores[3]["weighted_score"] == 3
        assert scores[3]["second"] == 1
        assert scores[3]["third"] == 1

    def test_empty_votes_returns_empty_scores(self):
        scores = compute_scores([])
        assert scores == {}

    def test_single_voter_results(self):
        votes = [
            {"entry_id": 5, "rank": 1},
            {"entry_id": 3, "rank": 2},
            {"entry_id": 9, "rank": 3},
        ]
        scores = compute_scores(votes)
        assert scores[5]["weighted_score"] == 3
        assert scores[3]["weighted_score"] == 2
        assert scores[9]["weighted_score"] == 1

    def test_ranks_beyond_three_not_counted(self):
        """Ranks beyond 3 don't contribute points (edge case protection)."""
        votes = [{"entry_id": 1, "rank": 4}]
        scores = compute_scores(votes)
        assert scores[1]["weighted_score"] == 0
        assert scores[1]["first"] == 0
        assert scores[1]["second"] == 0
        assert scores[1]["third"] == 0


# ---------------------------------------------------------------------------
# rank_results / tiebreaking
# ---------------------------------------------------------------------------


class TestRankResults:
    def test_tiebreaker_uses_first_place_count(self):
        """Two entries with equal weighted_score: the one with more firsts wins."""
        scores = {
            1: {"first": 1, "second": 0, "third": 3, "weighted_score": 6},
            2: {"first": 2, "second": 0, "third": 0, "weighted_score": 6},
        }
        ranked = rank_results(scores)
        # Entry 2 has more first-place votes (2 vs 1) — should be ranked first
        assert ranked[0][0] == 2
        assert ranked[1][0] == 1

    def test_all_entries_tied(self):
        """All entries with the same score maintain some consistent order."""
        scores = {
            1: {"first": 1, "second": 0, "third": 0, "weighted_score": 3},
            2: {"first": 1, "second": 0, "third": 0, "weighted_score": 3},
            3: {"first": 1, "second": 0, "third": 0, "weighted_score": 3},
        }
        ranked = rank_results(scores)
        assert len(ranked) == 3
        # All have same score — just verify they're all present
        ranked_ids = {r[0] for r in ranked}
        assert ranked_ids == {1, 2, 3}

    def test_higher_weighted_score_ranks_first(self):
        scores = {
            1: {"first": 3, "second": 0, "third": 0, "weighted_score": 9},
            2: {"first": 0, "second": 3, "third": 0, "weighted_score": 6},
            3: {"first": 0, "second": 0, "third": 3, "weighted_score": 3},
        }
        ranked = rank_results(scores)
        assert ranked[0][0] == 1
        assert ranked[1][0] == 2
        assert ranked[2][0] == 3

    def test_zero_score_entry_ranked_last(self):
        scores = {
            1: {"first": 2, "second": 0, "third": 0, "weighted_score": 6},
            2: {"first": 0, "second": 0, "third": 0, "weighted_score": 0},
        }
        ranked = rank_results(scores)
        assert ranked[0][0] == 1
        assert ranked[1][0] == 2

    def test_single_entry(self):
        scores = {42: {"first": 5, "second": 0, "third": 0, "weighted_score": 15}}
        ranked = rank_results(scores)
        assert len(ranked) == 1
        assert ranked[0][0] == 42
