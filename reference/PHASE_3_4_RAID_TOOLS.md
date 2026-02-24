# Phase 3.4 — Admin Raid Tools Page

## Goal

Replace the dead `raid-admin.html` static page with a real admin page. Includes Raid-Helper config
stored in DB, availability-grid day selector, event builder with roster preview, and server-side
Raid-Helper API calls (no Google Apps Script proxy needed — FastAPI calls directly).

---

## Database Migration: 0014_raid_events_recurring

```sql
ALTER TABLE patt.raid_events
    ADD COLUMN recurring_event_id INTEGER REFERENCES patt.recurring_events(id),
    ADD COLUMN auto_booked BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN raid_helper_payload JSONB;
```

---

## ORM Updates (`src/sv_common/db/models.py`)

Update `RaidEvent` class:

```python
recurring_event_id = Column(Integer, ForeignKey("patt.recurring_events.id"), nullable=True)
auto_booked = Column(Boolean, nullable=False, default=False)
raid_helper_payload = Column(JSONB, nullable=True)

# Relationship
recurring_event = relationship("RecurringEvent", foreign_keys=[recurring_event_id])
```

---

## New Service: `src/patt/services/raid_helper_service.py`

### Purpose

All Raid-Helper HTTP calls go through this module. Uses `httpx.AsyncClient`. No CORS issues
since calls originate from FastAPI server.

### Spec → Raid-Helper class/spec name mapping

Port from legacy `raid-admin.html` lines 2420–2462. This maps WoW class+spec names to the
string values Raid-Helper's API expects.

```python
# Maps (class_name, spec_name) → (raid_helper_class, raid_helper_spec)
SPEC_TO_RAID_HELPER: dict[tuple[str, str], tuple[str, str]] = {
    ("Death Knight", "Blood"): ("Death Knight", "Blood"),
    ("Death Knight", "Frost"): ("Death Knight", "Frost"),
    ("Death Knight", "Unholy"): ("Death Knight", "Unholy"),
    ("Demon Hunter", "Havoc"): ("Demon Hunter", "Havoc"),
    ("Demon Hunter", "Vengeance"): ("Demon Hunter", "Vengeance"),
    ("Druid", "Balance"): ("Druid", "Balance"),
    ("Druid", "Feral"): ("Druid", "Feral"),
    ("Druid", "Guardian"): ("Druid", "Guardian"),
    ("Druid", "Restoration"): ("Druid", "Restoration"),
    ("Evoker", "Devastation"): ("Evoker", "Devastation"),
    ("Evoker", "Preservation"): ("Evoker", "Preservation"),
    ("Evoker", "Augmentation"): ("Evoker", "Augmentation"),
    ("Hunter", "Beast Mastery"): ("Hunter", "Beast Mastery"),
    ("Hunter", "Marksmanship"): ("Hunter", "Marksmanship"),
    ("Hunter", "Survival"): ("Hunter", "Survival"),
    ("Mage", "Arcane"): ("Mage", "Arcane"),
    ("Mage", "Fire"): ("Mage", "Fire"),
    ("Mage", "Frost"): ("Mage", "Frost"),
    ("Monk", "Brewmaster"): ("Monk", "Brewmaster"),
    ("Monk", "Mistweaver"): ("Monk", "Mistweaver"),
    ("Monk", "Windwalker"): ("Monk", "Windwalker"),
    ("Paladin", "Holy"): ("Paladin", "Holy"),
    ("Paladin", "Protection"): ("Paladin", "Protection"),
    ("Paladin", "Retribution"): ("Paladin", "Retribution"),
    ("Priest", "Discipline"): ("Priest", "Discipline"),
    ("Priest", "Holy"): ("Priest", "Holy"),
    ("Priest", "Shadow"): ("Priest", "Shadow"),
    ("Rogue", "Assassination"): ("Rogue", "Assassination"),
    ("Rogue", "Outlaw"): ("Rogue", "Outlaw"),
    ("Rogue", "Subtlety"): ("Rogue", "Subtlety"),
    ("Shaman", "Elemental"): ("Shaman", "Elemental"),
    ("Shaman", "Enhancement"): ("Shaman", "Enhancement"),
    ("Shaman", "Restoration"): ("Shaman", "Restoration"),
    ("Warlock", "Affliction"): ("Warlock", "Affliction"),
    ("Warlock", "Demonology"): ("Warlock", "Demonology"),
    ("Warlock", "Destruction"): ("Warlock", "Destruction"),
    ("Warrior", "Arms"): ("Warrior", "Arms"),
    ("Warrior", "Fury"): ("Warrior", "Fury"),
    ("Warrior", "Protection"): ("Warrior", "Protection"),
}
```

### `create_event()` function

```python
async def create_event(
    config: dict,          # discord_config row fields
    title: str,
    event_type: str,
    start_time_utc: datetime,
    duration_minutes: int,
    channel_id: str,
    description: str,
    template_id: str = "wowretail2",
    signups: list[dict] | None = None,
) -> dict:
    """
    POST to Raid-Helper API to create event.
    Returns {"event_id": ..., "event_url": ...} on success.
    Raises RaidHelperError on failure.
    """
    base_url = "https://raid-helper.dev/api/v2"
    server_id = config["raid_helper_server_id"]
    api_key = config["raid_helper_api_key"]

    payload = {
        "leaderId": config["raid_creator_discord_id"],
        "templateId": template_id,
        "date": start_time_utc.strftime("%Y-%m-%d"),
        "time": start_time_utc.strftime("%H:%M"),
        "title": title,
        "description": description,
        "channelId": channel_id or config["raid_channel_id"],
        "duration": duration_minutes,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/servers/{server_id}/event",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=15.0,
        )
    resp.raise_for_status()
    data = resp.json()
    return {"event_id": data["id"], "event_url": data.get("url", ""), "payload": payload}
```

### `test_connection()` function

```python
async def test_connection(config: dict) -> bool:
    """Make a benign API call to validate Raid-Helper config. Returns True if valid."""
    ...
```

---

## Admin Page: `/admin/raid-tools`

**File:** `src/patt/templates/admin/raid_tools.html`
**Nav block:** `nav_raid_tools` (label: "Raid Tools")
**Auth:** Officer+ required

### Section 1 — Raid-Helper Configuration (collapsible)

Fields stored in `common.discord_config` (new columns from migration 0013):

| Field | Input type | Column |
|-------|-----------|--------|
| API Key | `<input type="password">` | `raid_helper_api_key` |
| Server ID | `<input type="text">` | `raid_helper_server_id` |
| Event Creator Discord ID | `<input type="text">` | `raid_creator_discord_id` |
| Signup Channel ID | `<input type="text">` | `raid_channel_id` |
| Voice Channel ID | `<input type="text">` | `raid_voice_channel_id` |
| Default Template | `<input type="text">` placeholder "wowretail2" | `raid_default_template_id` |

- "Save Config" button → PATCH `/api/v1/admin/raid-config`
- "Test Connection" button → GET `/api/v1/admin/raid-config/test` → shows success/failure banner

### Section 2 — Availability Grid (7-day cards, clickable)

Same data as Phase 3.1 availability page (reuse `GET /api/v1/admin/availability-by-day`).

Cards layout (7 cards in a row or 2-row grid):
- Day name
- Available count + percentage bar (green/amber/red)
- Weighted score
- Role breakdown mini-bar (T/H/M/R counts as colored segments)
- **If day has a `recurring_event`:** Show event label + default time at top of card

**On click:** Selected day highlighted with gold border.

Below the grid when a day is selected:
- Expandable table of available players: name, role, rank, auto_invite status, character
- Role count summary (Tank: N, Healer: N, etc.)

### Section 3 — Event Builder

Pre-populated when a day card is clicked. Uses `recurring_event` data where available.

```html
<form id="event-builder">
  <div class="form-row">
    <label>Title</label>
    <input type="text" id="event-title" placeholder="Heroic Raid Night">
  </div>
  <div class="form-row">
    <label>Event Type</label>
    <select id="event-type">
      <option value="raid">Heroic/Mythic Raid</option>
      <option value="oldraids">Old Raids / Achievements</option>
      <option value="mythicplus">Mythic+</option>
      <option value="pvp">PvP</option>
      <option value="farming">Farming</option>
      <option value="social">Social / Other</option>
    </select>
  </div>
  <div class="form-row">
    <label>Date</label>
    <input type="date" id="event-date">
    <!-- JS pre-fills to next occurrence of selected day_of_week -->
  </div>
  <div class="form-row">
    <label>Time (EST)</label>
    <input type="time" id="event-time" value="21:00">
  </div>
  <div class="form-row">
    <label>Duration</label>
    <select id="event-duration">
      <option value="60">1 hour</option>
      <option value="90">1.5 hours</option>
      <option value="120" selected>2 hours</option>
      <option value="150">2.5 hours</option>
      <option value="180">3 hours</option>
    </select>
  </div>
  <div class="form-row">
    <label>Channel ID</label>
    <input type="text" id="event-channel" placeholder="Discord channel ID">
  </div>
  <div class="form-row">
    <label>Description</label>
    <textarea id="event-description"></textarea>
  </div>
</form>
```

#### Roster Preview Sub-section

Table showing all active players with main characters. Auto-invite status column:

**Status logic:**
- `rank.level >= 2` AND `player.auto_invite_events = TRUE` → **Accepted** (green)
- `rank.level >= 2` AND `player.auto_invite_events = FALSE` → **Tentative** (amber)
- `rank.level = 1` (Initiate) → **Bench** (gray)

Each row has a status override `<select>` (Accepted / Tentative / Bench / Skip).
Overrides stored client-side in `playerOverrides` dict keyed by `player_id`.

#### "Create Event" Button

```javascript
async function createEvent() {
    const body = {
        title: document.getElementById('event-title').value,
        event_type: document.getElementById('event-type').value,
        event_date: document.getElementById('event-date').value,
        start_time: document.getElementById('event-time').value,
        timezone: "America/New_York",
        duration_minutes: parseInt(document.getElementById('event-duration').value),
        channel_id: document.getElementById('event-channel').value,
        description: document.getElementById('event-description').value,
        recurring_event_id: selectedRecurringEventId,  // from day card selection
        player_overrides: playerOverrides,
    };
    const res = await fetch('/api/v1/admin/raid-events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.ok) {
        showSuccess(`Event created! ${data.data.event_url}`);
    } else {
        showError(data.error);
    }
}
```

### Section 4 — Manual Fallback (collapsible)

Shows copy-paste Discord format for manual event creation. Auto-updates from event builder fields.
Useful as backup if Raid-Helper API is down.

---

## API Endpoints (add to `src/patt/api/admin_routes.py`)

### `GET /api/v1/admin/raid-config`

Returns current Raid-Helper config from `common.discord_config`. Masks API key (show first 4 chars + `****`).

```json
{
  "ok": true,
  "data": {
    "raid_helper_api_key": "abcd****",
    "raid_helper_server_id": "123456789",
    "raid_creator_discord_id": "987654321",
    "raid_channel_id": "111222333",
    "raid_voice_channel_id": null,
    "raid_default_template_id": "wowretail2"
  }
}
```

### `PATCH /api/v1/admin/raid-config`

Update any subset of Raid-Helper config fields. Only updates fields present in body.

```json
// Request body
{
  "raid_helper_api_key": "newkey...",
  "raid_channel_id": "111222333"
}
```

### `GET /api/v1/admin/raid-config/test`

Tests the Raid-Helper API connection. Makes a benign call (GET server info or similar).

```json
// Success
{"ok": true, "data": {"connected": true, "server_name": "Pull All The Things"}}

// Failure
{"ok": false, "error": "Invalid API key"}
```

### `POST /api/v1/admin/raid-events`

Create a raid event in Raid-Helper AND in `patt.raid_events`.

**Request body:**
```json
{
  "title": "Heroic Raid Night",
  "event_type": "raid",
  "event_date": "2026-03-06",
  "start_time": "21:00",
  "timezone": "America/New_York",
  "duration_minutes": 120,
  "channel_id": "123456789",
  "description": "Weekly heroic clear. Sign up below!",
  "recurring_event_id": 1,
  "player_overrides": {
    "42": "bench",
    "17": "skip"
  }
}
```

**Server-side logic:**
1. Load Raid-Helper config from `discord_config` (fail fast if not configured)
2. Convert `event_date + start_time + timezone` → UTC `datetime`
3. Build roster signups:
   - Query active players with main characters + discord_users (for discord_id)
   - Apply auto-invite rules: `rank >= 2 + auto_invite = True` → Accepted, etc.
   - Apply `player_overrides` from request body
4. Call `raid_helper_service.create_event()` with signups list
5. On RH success: INSERT `patt.raid_events` row (with `raid_helper_payload`)
6. Batch-INSERT `patt.raid_attendance` rows (source='auto')
7. Return event ID and Raid-Helper URL

**Response:**
```json
{
  "ok": true,
  "data": {
    "raid_event_id": 1,
    "raid_helper_event_id": "RH_ABC123",
    "event_url": "https://raid-helper.dev/event/RH_ABC123"
  }
}
```

### `GET /api/v1/admin/availability-by-day`

(Defined in Phase 3.1 — reused here. No new implementation needed.)

---

## "Next occurrence" date calculation (JavaScript)

When a day card is clicked, auto-fill the date picker with the next occurrence of that day_of_week:

```javascript
function nextOccurrenceOf(dayOfWeek) {
    // dayOfWeek: 0=Mon, 1=Tue, ..., 6=Sun (ISO)
    const today = new Date();
    const todayDow = (today.getDay() + 6) % 7; // JS: 0=Sun → convert to ISO
    let daysUntil = (dayOfWeek - todayDow + 7) % 7;
    if (daysUntil === 0) daysUntil = 7; // Always next week if same day
    const next = new Date(today);
    next.setDate(today.getDate() + daysUntil);
    return next.toISOString().split('T')[0]; // "YYYY-MM-DD"
}
```

---

## Files Created/Modified

| File | Action |
|------|--------|
| `alembic/versions/0014_raid_events_recurring.py` | NEW — adds 3 columns to patt.raid_events |
| `src/sv_common/db/models.py` | UPDATE RaidEvent model |
| `src/patt/services/raid_helper_service.py` | NEW — API client + spec mapping |
| `src/patt/api/admin_routes.py` | ADD raid-events, raid-config, raid-config/test endpoints |
| `src/patt/pages/admin_pages.py` | ADD GET /admin/raid-tools route |
| `src/patt/templates/admin/raid_tools.html` | NEW |
| `src/patt/templates/base_admin.html` | ADD Raid Tools nav item (nav_raid_tools block) |

---

## Verification Checklist

- [ ] `/admin/raid-tools` loads as officer
- [ ] Raid-Helper config form saves to DB (masked API key shown correctly)
- [ ] "Test Connection" shows success/failure
- [ ] 7-day availability grid renders with scores
- [ ] Clicking a day card highlights it and shows player list
- [ ] Event builder pre-fills from recurring event when day selected
- [ ] Roster preview shows correct accepted/tentative/bench breakdown
- [ ] Per-player status override works (dropdown changes without page reload)
- [ ] "Create Event" POST triggers Raid-Helper API call (verify in RH dashboard)
- [ ] `patt.raid_events` row inserted after successful creation
- [ ] `patt.raid_attendance` rows batch-inserted for all non-skip players
- [ ] Migration 0014 runs clean
