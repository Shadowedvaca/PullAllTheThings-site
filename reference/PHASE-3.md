# Phase 3: Campaign Engine & Voting API

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phases 0-2 must be complete.
> **Goal:** Campaigns can be created, entries added, votes cast, and results calculated.
> The full voting lifecycle works end-to-end via the API.

---

## What This Phase Produces

1. Campaign service — create, configure, lifecycle management (draft → live → closed)
2. Vote service — cast votes, validate eligibility, calculate ranked-choice results
3. Campaign API endpoints (admin: create/manage, member: vote, public: results)
4. Automatic campaign status transitions (goes live at start_at, closes after duration)
5. Early close detection (all eligible voters have voted)
6. Comprehensive test coverage for all voting logic

---

## Context From Previous Phases

After Phase 2:
- Auth system with JWT, registration, login
- Discord bot running, role sync functional
- Guild members with ranks, characters
- Protected routes with rank-based access control

---

## Tasks

### 3.1 — Campaign Service (`patt/services/campaign_service.py`)

```python
async def create_campaign(db, title, description, type, picks_per_voter, min_rank_to_vote, min_rank_to_view, start_at, duration_hours, discord_channel_id, created_by) -> Campaign
async def get_campaign(db, campaign_id) -> Campaign  # includes entries
async def list_campaigns(db, status=None) -> list[Campaign]
async def update_campaign(db, campaign_id, **kwargs) -> Campaign  # only while draft
async def add_entry(db, campaign_id, name, description, image_url, associated_member_id=None) -> CampaignEntry
async def remove_entry(db, campaign_id, entry_id) -> bool  # only while draft
async def update_entry(db, entry_id, **kwargs) -> CampaignEntry  # only while draft
async def activate_campaign(db, campaign_id) -> Campaign  # draft → live (sets start_at if not set)
async def close_campaign(db, campaign_id) -> Campaign  # live → closed, triggers result calc
async def get_campaign_status(db, campaign_id) -> dict  # status, time remaining, votes cast, etc.
```

**Campaign status rules:**
- `draft` — can edit everything (title, entries, settings)
- `live` — no edits to entries or settings, votes accepted
- `closed` — no more votes, results are final
- `archived` — hidden from default views

**Time-based transitions:**
- A background task checks campaigns every minute:
  - If status=draft and current_time >= start_at → set status=live
  - If status=live and current_time >= start_at + duration_hours → close_campaign()

### 3.2 — Vote Service (`patt/services/vote_service.py`)

```python
async def cast_vote(db, campaign_id, member_id, picks: list[dict]) -> list[Vote]
    """
    picks = [{"entry_id": 5, "rank": 1}, {"entry_id": 3, "rank": 2}, {"entry_id": 9, "rank": 3}]
    Validates:
    - Campaign is live
    - Member hasn't already voted in this campaign
    - Member meets rank requirement
    - Correct number of picks (== picks_per_voter)
    - No duplicate entries in picks
    - No duplicate ranks in picks
    - All entry_ids belong to this campaign
    """

async def get_member_vote(db, campaign_id, member_id) -> list[Vote] | None
    """Returns the member's votes for this campaign, or None if they haven't voted."""

async def has_member_voted(db, campaign_id, member_id) -> bool

async def calculate_results(db, campaign_id) -> list[CampaignResult]
    """
    Ranked choice scoring:
    - 1st place pick = 3 points
    - 2nd place pick = 2 points
    - 3rd place pick = 1 point
    Total weighted_score = sum of all points received
    Entries ranked by weighted_score descending, then first_place_count as tiebreaker
    Results stored in campaign_results table.
    """

async def get_results(db, campaign_id) -> list[dict]
    """
    Returns results with entry info, shaped for display:
    [
        {
            "entry": {"id": 5, "name": "Trog", "image_url": "...", "associated_member": "Trog"},
            "first_place_count": 4,
            "second_place_count": 2,
            "third_place_count": 1,
            "weighted_score": 18,
            "final_rank": 1
        },
        ...
    ]
    """

async def get_vote_stats(db, campaign_id) -> dict
    """
    {
        "total_eligible": 12,
        "total_voted": 8,
        "percent_voted": 66.7,
        "all_voted": false
    }
    """

async def check_early_close(db, campaign_id) -> bool
    """
    If campaign.early_close_if_all_voted is true and all eligible members have voted,
    close the campaign and return True.
    """
```

### 3.3 — Campaign API Endpoints (`patt/api/campaign_routes.py`)

**Admin routes (Officer+):**
```
POST   /api/v1/admin/campaigns                    — Create campaign (draft)
PATCH  /api/v1/admin/campaigns/{id}                — Update campaign settings
POST   /api/v1/admin/campaigns/{id}/entries        — Add an entry
DELETE /api/v1/admin/campaigns/{id}/entries/{eid}   — Remove an entry
PATCH  /api/v1/admin/campaign-entries/{eid}         — Update an entry
POST   /api/v1/admin/campaigns/{id}/activate       — Activate (draft → live)
POST   /api/v1/admin/campaigns/{id}/close          — Force close
GET    /api/v1/admin/campaigns/{id}/stats          — Vote statistics
```

**Vote routes (authenticated, rank-gated):**
```
POST /api/v1/campaigns/{id}/vote
    Body: { "picks": [{"entry_id": 5, "rank": 1}, {"entry_id": 3, "rank": 2}, {"entry_id": 9, "rank": 3}] }
    - Requires: member rank >= campaign.min_rank_to_vote
    - Returns: the submitted vote or error

GET  /api/v1/campaigns/{id}/my-vote
    - Returns the current member's vote, or 404 if not voted
```

**Public routes (rank-gated for visibility):**
```
GET  /api/v1/campaigns                       — List campaigns (filtered by viewer's rank)
GET  /api/v1/campaigns/{id}                  — Campaign detail with entries
GET  /api/v1/campaigns/{id}/results          — Results (only if campaign is closed OR member has voted)
GET  /api/v1/campaigns/{id}/results/live     — Live standings (only for members who have voted)
```

**Visibility rules:**
- If campaign.min_rank_to_view is null → anyone can see it (public)
- If campaign.min_rank_to_view is set → only members at that rank+ can see it
- Results: visible to public always if min_rank_to_view is null, otherwise rank-gated
- Live standings during voting: only visible to members who have already voted
- Non-voters who meet rank requirements see the vote form, not results
- Non-voters who don't meet rank requirements see results only (read-only observers)

### 3.4 — Background Task: Campaign Status Checker

A task that runs every 60 seconds:
```python
async def check_campaign_statuses(db):
    """
    1. Find draft campaigns where start_at <= now → activate them
    2. Find live campaigns where start_at + duration <= now → close them
    3. Find live campaigns with early_close_if_all_voted → check if all voted → close
    """
```

Integrate as a FastAPI background task or an asyncio loop.

### 3.5 — Tests

**Unit tests:**

`test_vote_scoring.py` (pure logic, no DB):
- test_ranked_choice_first_place_gets_three_points
- test_ranked_choice_second_place_gets_two_points
- test_ranked_choice_third_place_gets_one_point
- test_scoring_with_multiple_voters
- test_tiebreaker_uses_first_place_count
- test_all_entries_tied
- test_single_voter_results

`test_campaign_service.py`:
- test_create_campaign_defaults
- test_campaign_status_transitions_draft_to_live
- test_campaign_cannot_edit_entries_when_live
- test_campaign_cannot_vote_when_draft
- test_campaign_cannot_vote_when_closed
- test_activate_sets_start_time_if_not_set

**Integration tests:**

`test_campaign_flow.py`:
- test_full_campaign_lifecycle (create → add entries → activate → vote → close → results)
- test_cast_vote_validates_rank_requirement
- test_cast_vote_rejects_duplicate
- test_cast_vote_rejects_wrong_number_of_picks
- test_cast_vote_rejects_duplicate_entries
- test_cast_vote_rejects_entry_from_wrong_campaign
- test_results_hidden_until_voted (member sees vote form, not results)
- test_results_visible_after_voting
- test_public_campaign_results_visible_to_anonymous
- test_rank_gated_campaign_hidden_from_low_rank
- test_early_close_when_all_eligible_voted
- test_time_based_activation
- test_time_based_close

---

## Acceptance Criteria

- [ ] Campaigns can be created, configured, and activated
- [ ] Entries can be added/removed/updated while campaign is in draft
- [ ] Ranked-choice voting works correctly (3 picks, weighted scoring)
- [ ] Vote validation catches all edge cases (duplicate, wrong rank, closed, etc.)
- [ ] Results calculation is correct with tiebreaker logic
- [ ] Campaign auto-activates at start_at and auto-closes after duration
- [ ] Early close works when all eligible members have voted
- [ ] Visibility rules enforce rank requirements
- [ ] Voted members see live standings; non-voters see vote form
- [ ] All unit and integration tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-3: campaign engine and voting API"`
- [ ] Update CLAUDE.md "Current Build Status" section
