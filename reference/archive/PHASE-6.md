# Phase 6: Contest Agent — Discord Campaign Updates

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phases 0-5 must be complete.
> **Goal:** PATT-Bot posts fun, dynamic updates to Discord during live campaigns.
> Lead changes, milestones, final stretch warnings, and results announcements.

---

## What This Phase Produces

1. Contest agent service that monitors live campaigns and generates update messages
2. Message generation informed by the contest agent personality file
3. Discord channel posting at campaign milestones
4. Launch announcements when campaigns go live
5. Results announcements when campaigns close
6. Configurable chattiness level per campaign
7. Agent activity logged in contest_agent_log table
8. Full test coverage with Discord mocked

---

## Context From Previous Phases

After Phase 5:
- Full working platform with auth, campaigns, voting, admin, data migrated
- PATT-Bot is running and can post to Discord channels
- Campaigns have a discord_channel_id for announcements
- Vote stats and results can be calculated on demand

---

## Tasks

### 6.1 — Contest Agent Service (`patt/services/contest_agent.py`)

A service that runs as a periodic background task (every 5 minutes while any campaign is live).

```python
async def check_campaign_updates(db):
    """
    For each live campaign:
    1. Calculate current vote stats
    2. Calculate current standings
    3. Compare to last known state (from contest_agent_log)
    4. Determine if any milestone has been hit
    5. If so, generate a message and post it
    """
```

### 6.2 — Milestone Triggers

The agent checks for these events, in priority order:

| Trigger | Condition | Example |
|---------|-----------|---------|
| **Launch** | Campaign status just changed to live | "🎉 Voting is NOW OPEN for..." |
| **Lead change** | #1 entry has changed since last check | "Skate just overtook Bam for the lead!" |
| **First vote** | First vote of the campaign was cast | "And we're off! The first vote has been cast..." |
| **25% voted** | 25% of eligible members have voted | "Quarter of the guild has spoken..." |
| **50% voted** | 50% of eligible members have voted | "We're at the halfway mark!..." |
| **75% voted** | 75% of eligible members have voted | "Three quarters in!..." |
| **Final stretch** | 24 hours remaining | "⏰ Only 24 hours left to vote!..." |
| **Last call** | 1 hour remaining | "🚨 LAST CALL! Voting closes in 1 hour!" |
| **All voted** | All eligible members have voted, early close | "Every vote is in! The results are..." |
| **Campaign closed** | Campaign has ended (time or manual close) | "🏆 The results are in!..." |

**De-duplication:** Before posting, check `contest_agent_log` to see if this
event_type has already been posted for this campaign. Never post the same milestone twice.

**Priority:** If multiple milestones are hit in the same check (e.g., 50% AND a lead change),
post the more exciting one (lead change > participation milestone).

### 6.3 — Message Generation

Messages should be fun, personality-driven, and specific to the actual data.

Rather than hardcoded templates, the agent uses a structured approach:
1. Determine the event type and gather the relevant data (who's leading, by how much, etc.)
2. Select a message template from a pool (variety prevents repetition)
3. Fill in the specifics

Create `data/contest_agent_personality.md` (see separate file delivered with this phase).
This file defines the bot's personality and message templates. It's designed so that
in the future, an AI could generate more creative messages from this context.

**For now, implement with template pools:**

```python
LEAD_CHANGE_TEMPLATES = [
    "🔥 {new_leader} just took the lead from {old_leader}! The score is {score} to {old_score}.",
    "Plot twist! {new_leader} surges ahead of {old_leader}! Make sure to cast your vote!",
    "{old_leader}'s in the rear view mirror now! {new_leader} takes the top spot with {score} points!",
]

LAUNCH_TEMPLATES = [
    "🎉 **{campaign_title}** is NOW OPEN for voting!\n\n{description}\n\n🗳️ Cast your vote: {vote_url}\n📅 Voting closes: {close_date}",
]

RESULTS_TEMPLATES = [
    "🏆 **{campaign_title}** — THE RESULTS ARE IN!\n\n🥇 **{first_name}** — {first_score} points\n🥈 {second_name} — {second_score} points\n🥉 {third_name} — {third_score} points\n\n{total_voters} members voted. See full results: {results_url}",
]
```

### 6.4 — Channel Posting Integration

Use the existing `sv_common.discord.channels` module to post messages.

Each campaign has a `discord_channel_id` specifying where updates go.
If not set, fall back to the `default_announcement_channel_id` from `discord_config`.

Messages should use Discord embed formatting for rich display:
```python
embed = discord.Embed(
    title="🏆 Salt All The Things Profile Pic Contest",
    description="Voting is NOW OPEN!",
    color=0xd4a84b  # PATT gold
)
embed.set_thumbnail(url=leading_entry_image_url)
embed.add_field(name="Cast Your Vote", value=vote_url, inline=False)
embed.set_footer(text="Voting closes Feb 28 at 9pm EST")
```

### 6.5 — Admin Configuration

Add to the campaign create/edit form (Phase 4 admin pages):
- Checkbox: "Enable contest agent updates" (default: on)
- Dropdown: "Chattiness level" — Quiet (launch + results only), Normal (+ milestones),
  Hype (+ lead changes, + participation updates)

Map chattiness to which triggers are active:
- **Quiet:** launch, campaign_closed
- **Normal:** launch, 50% voted, final_stretch, last_call, all_voted, campaign_closed
- **Hype:** All triggers

Store as a field on the campaign: `agent_chattiness VARCHAR(10) DEFAULT 'normal'`

Add Alembic migration for this new column.

### 6.6 — Tests

**Unit tests:**

`test_contest_agent.py`:
- test_lead_change_detected_when_first_place_changes
- test_lead_change_not_detected_when_first_place_same
- test_milestone_25_percent_detected
- test_milestone_50_percent_detected
- test_milestone_not_re_triggered (check dedup)
- test_message_template_fills_correctly
- test_chattiness_quiet_only_launch_and_close
- test_chattiness_normal_includes_milestones
- test_chattiness_hype_includes_everything
- test_priority_lead_change_over_milestone

**Integration tests:**

`test_contest_agent_flow.py` (with mocked Discord):
- test_agent_posts_launch_message_when_campaign_activates
- test_agent_posts_lead_change_on_next_check
- test_agent_posts_results_when_campaign_closes
- test_agent_respects_chattiness_setting
- test_agent_does_not_duplicate_messages
- test_agent_logs_all_posted_messages

---

## Acceptance Criteria

- [ ] Agent posts launch announcement when campaign goes live
- [ ] Agent detects and posts lead changes
- [ ] Agent posts participation milestones (25%, 50%, 75%)
- [ ] Agent posts final stretch and last call warnings
- [ ] Agent posts results when campaign closes (or early closes)
- [ ] Chattiness levels work correctly (quiet/normal/hype)
- [ ] Messages are never duplicated
- [ ] All activity logged in contest_agent_log
- [ ] Messages use Discord embeds with PATT gold branding
- [ ] All tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-6: contest agent discord campaign updates"`
- [ ] Update CLAUDE.md "Current Build Status" section
