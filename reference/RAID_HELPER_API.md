# Raid-Helper API Reference

> Hard-won knowledge from the PATT integration. Read this before touching anything
> related to Raid-Helper. The official docs are thin and some of the behaviour is
> non-obvious. This document covers everything we actually use and every mistake we
> already made so you don't repeat them.

---

## Table of Contents

1. [Key Facts](#key-facts)
2. [Authentication](#authentication)
3. [Test the Connection](#test-the-connection)
4. [Create an Event](#create-an-event)
5. [Add Signups to an Event](#add-signups-to-an-event)
6. [wowretail2 Template — Class & Spec Reference](#wowretail2-template--class--spec-reference)
7. [Status Slot Classes](#status-slot-classes)
8. [Complete Worked Examples](#complete-worked-examples)
9. [Errors We Hit and What They Mean](#errors-we-hit-and-what-they-mean)
10. [Our Implementation](#our-implementation)
11. [Configuration in the Platform](#configuration-in-the-platform)

---

## Key Facts

| Property | Value |
|---|---|
| Base URL | `https://raid-helper.dev/api/v2` |
| Auth header | `Authorization: {api_key}` (no "Bearer" prefix) |
| Content-Type | `application/json` |
| Date format | `D-M-YYYY` — **not zero-padded**, not ISO (`3-18-2026` not `03-18-2026`) |
| Time format | `HH:MM` in the **local timezone** you want displayed in Discord |
| Rate limit | Undocumented. Use 200 ms between signup calls to be safe |
| Official docs | https://raid-helper.dev/documentation/api |

**The two most important non-obvious things:**

1. Dates and times go to Raid-Helper in **local time**, not UTC. Raid-Helper's Discord
   bot converts them to Discord timestamps using its own server-side timezone setting.
   Send the time you want raiders to see in Discord.

2. When adding signups, `className` is **not the WoW class name**. For `wowretail2` it is
   the **role slot** — `Tank`, `Melee`, `Ranged`, `Healer` — or a status slot like
   `Tentative`, `Bench`, `Absence`. Sending `"Paladin"` returns `{"error":"invalid className"}`.

---

## Authentication

All requests use the server API key set via `/apikey create` in Discord.
See `RAID-HELPER-API-KEY.md` for how to generate one.

```
Authorization: KbfhAKAP2LHt2gVrFJntXpnhkfJeUb67j36Haprk
```

The key is stored in `common.discord_config.raid_helper_api_key` (encrypted at rest).
The server ID is in `common.discord_config.raid_helper_server_id`.
Both are loaded from the DB at request time — never hard-coded.

---

## Test the Connection

```http
GET https://raid-helper.dev/api/v2/servers/{serverId}/events
Authorization: {api_key}
```

Returns the list of posted events for the server. A 200 response with `postedEvents`
array means the key and server ID are valid.

**curl:**
```bash
curl -s -H "Authorization: KbfhAKAP2LHt2gVrFJntXpnhkfJeUb67j36Haprk" \
  "https://raid-helper.dev/api/v2/servers/1288213206938026069/events"
```

**Expected response:**
```json
{
  "postedEvents": [
    { "id": "1482924660516716656", "title": "Progression Raid Night", ... }
  ]
}
```

Our implementation: `raid_helper_service.test_connection()` in
`src/guild_portal/services/raid_helper_service.py`.

---

## Create an Event

```http
POST https://raid-helper.dev/api/v2/servers/{serverId}/channels/{channelId}/event/
Authorization: {api_key}
Content-Type: application/json
```

Note the channel ID is in the **path**, not the body.

> ⚠️ **Trailing slash required.** Raid-Helper's nginx redirects `/event` → `/event/` (301).
> Because HTTP 301 converts POST to GET, following the redirect breaks the call.
> Always include the trailing slash in code to hit the endpoint directly.

### Request Body

| Field | Type | Required | Notes |
|---|---|---|---|
| `leaderId` | string | Yes | Discord user ID of the event leader |
| `templateId` | string | Yes | Template name — we use `wowretail2` |
| `date` | string | Yes | `D-M-YYYY` — local date, not zero-padded |
| `time` | string | Yes | `HH:MM` — local time (24h) |
| `title` | string | Yes | Event title shown in Discord |
| `description` | string | No | Body text of the embed |
| `duration` | integer | No | Duration in minutes |

### Example 1 — Wednesday heroic prog raid

```json
POST /api/v2/servers/1288213206938026069/channels/1327719564842237993/event

{
  "leaderId": "195547238959677441",
  "templateId": "wowretail2",
  "date": "19-3-2026",
  "time": "20:00",
  "title": "Progression Raid Night — Heroic",
  "description": "Auto-scheduled raid. Sign up below!",
  "duration": 120
}
```

### Example 2 — Saturday normal clear, different time

```json
POST /api/v2/servers/1288213206938026069/channels/1327719564842237993/event

{
  "leaderId": "195547238959677441",
  "templateId": "wowretail2",
  "date": "21-3-2026",
  "time": "19:30",
  "title": "Normal Clear — Alt Night",
  "description": "Bring your alts. Going fast.",
  "duration": 90
}
```

### Example 3 — Single-digit month and day (no zero-padding)

```json
{
  "leaderId": "195547238959677441",
  "templateId": "wowretail2",
  "date": "5-1-2026",
  "time": "21:00",
  "title": "New Year Raid"
}
```

`"05-01-2026"` would be rejected. Always strip leading zeros.

### Response

```json
{
  "event": {
    "id": "1482924660516716656",
    "title": "Progression Raid Night — Heroic",
    ...
  }
}
```

The event ID is at `response["event"]["id"]` or sometimes `response["id"]` directly.
Our code checks both: `data.get("event", data).get("id")`.

The event URL for Discord is:
```
https://discord.com/channels/{serverId}/{channelId}/{eventId}
```

---

## Add Signups to an Event

Signups are added **one at a time** after event creation, not as part of the creation
call (the creation payload silently ignores any signup data you include).

```http
POST https://raid-helper.dev/api/v2/events/{eventId}/signups
Authorization: {api_key}
Content-Type: application/json
```

### Request Body

| Field | Type | Required | Notes |
|---|---|---|---|
| `userId` | string | Yes | Discord user ID of the player |
| `className` | string | Yes | **Role slot or status slot** — see below |
| `specName` | string | No | Spec name as defined in the template |

`className` is **not the WoW class**. It is the role bucket defined by the template:
`Tank`, `Healer`, `Melee`, `Ranged` for active raiders, or `Tentative`, `Bench`,
`Absence` for status slots. See the full reference table below.

### Example 1 — Balance Druid, accepted as ranged DPS

```json
POST /api/v2/events/1482924660516716656/signups

{
  "userId": "195547238959677441",
  "className": "Ranged",
  "specName": "Balance"
}
```

### Example 2 — Protection Paladin tanking

```json
{
  "userId": "967560340328038420",
  "className": "Tank",
  "specName": "Protection"
}
```

### Example 3 — Protection Warrior (different specName than Paladin Protection)

```json
{
  "userId": "1181217051570880516",
  "className": "Tank",
  "specName": "Protection1"
}
```

`Protection1` is Warrior Protection. `Protection` is Paladin Protection.
The template disambiguates duplicate spec names by appending `1`.

### Example 4 — DK Frost (melee, different from Mage Frost)

```json
{
  "userId": "139777140118781952",
  "className": "Melee",
  "specName": "Frost1"
}
```

`Frost1` = Death Knight Frost (melee). `Frost` = Mage Frost (ranged).

### Example 5 — Holy Paladin vs Holy Priest

```json
{ "userId": "149815743066669056", "className": "Healer", "specName": "Holy1" }
{ "userId": "209115292444524547", "className": "Healer", "specName": "Holy" }
```

`Holy1` = Paladin, `Holy` = Priest. Same disambiguation pattern.

### Example 6 — Beast Mastery Hunter (spec name has no space)

```json
{
  "userId": "68097839854522368",
  "className": "Ranged",
  "specName": "Beastmastery"
}
```

Our DB stores it as `"Beast Mastery"` (two words). The template uses `"Beastmastery"`
(one word, no space). The `SPEC_TO_RAID_HELPER` map handles this translation.

### Example 7 — Tentative signup (no spec needed)

```json
{
  "userId": "328047843526836228",
  "className": "Tentative"
}
```

Status slot classes (`Tentative`, `Bench`, `Absence`) don't take a `specName`.
The player lands in the corresponding section of the Raid-Helper embed.

### Example 8 — Player marked Absence (raid hiatus or not available)

```json
{
  "userId": "617906641232199740",
  "className": "Absence"
}
```

### Rate limiting

Send each signup as a separate call with ~200 ms between them:

```python
for signup in signups:
    await client.post(url, json=body)
    await asyncio.sleep(0.2)
```

Firing all signups concurrently causes rate-limit failures for larger rosters.

---

## wowretail2 Template — Class & Spec Reference

This is the complete spec list as returned by the Raid-Helper API for the `wowretail2`
template. **Always use these exact strings** — they are case-sensitive.

### Tank

`className: "Tank"`

| WoW Class | WoW Spec | `specName` to send |
|---|---|---|
| Paladin | Protection | `Protection` |
| Warrior | Protection | `Protection1` |
| Death Knight | Blood | `Blood` |
| Demon Hunter | Vengeance | `Vengeance` |
| Monk | Brewmaster | `Brewmaster` |
| Druid | Guardian | `Guardian` |

### Healer

`className: "Healer"`

| WoW Class | WoW Spec | `specName` to send |
|---|---|---|
| Priest | Holy | `Holy` |
| Paladin | Holy | `Holy1` |
| Druid | Restoration | `Restoration` |
| Shaman | Restoration | `Restoration1` |
| Monk | Mistweaver | `Mistweaver` |
| Evoker | Preservation | `Preservation` |
| Priest | Discipline | `Discipline` |

### Melee DPS

`className: "Melee"`

| WoW Class | WoW Spec | `specName` to send |
|---|---|---|
| Warrior | Arms | `Arms` |
| Warrior | Fury | `Fury` |
| Rogue | Assassination | `Assassination` |
| Rogue | Outlaw | `Outlaw` |
| Rogue | Subtlety | `Subtlety` |
| Death Knight | Frost | `Frost1` |
| Death Knight | Unholy | `Unholy` |
| Demon Hunter | Havoc | `Havoc` |
| Monk | Windwalker | `Windwalker` |
| Druid | Feral | `Feral` |
| Hunter | Survival | `Survival` |
| Paladin | Retribution | `Retribution` |
| Shaman | Enhancement | `Enhancement` |

### Ranged DPS

`className: "Ranged"`

| WoW Class | WoW Spec | `specName` to send |
|---|---|---|
| Mage | Arcane | `Arcane` |
| Mage | Fire | `Fire` |
| Mage | Frost | `Frost` |
| Warlock | Affliction | `Affliction` |
| Warlock | Demonology | `Demonology` |
| Warlock | Destruction | `Destruction` |
| Hunter | Beast Mastery | `Beastmastery` ⚠️ no space |
| Hunter | Marksmanship | `Marksmanship` |
| Druid | Balance | `Balance` |
| Priest | Shadow | `Shadow` |
| Shaman | Elemental | `Elemental` |
| Evoker | Devastation | `Devastation` |
| Evoker | Augmentation | `Augmentation` |

> **How to verify the template live:** fetch any existing event via
> `GET /api/v2/events/{eventId}` and inspect the `classes` array. Each class object
> has `name` (the `className` to send) and a `specs` array where each spec has `name`
> (the `specName` to send) and `roleName` (human-readable label).

---

## Status Slot Classes

For players who are not actively signing up as their role, use a status class instead.
These don't need a `specName`.

| Status | `className` | When to use |
|---|---|---|
| Signed up, role assigned | `Tank` / `Healer` / `Melee` / `Ranged` | Player is available and raiding |
| Available but unconfirmed | `Tentative` | Available that day, no auto-invite |
| Low-priority hold | `Bench` | Rarely used now — use Tentative for initiates |
| Not available | `Absence` | Not available that day, or on raid hiatus |
| Hidden from event | _(don't send a signup)_ | Override `"skip"` in admin UI |

### Our status assignment logic

```
if player.on_raid_hiatus OR player not in availability list for that day:
    → Absence

elif rank >= 2 AND auto_invite_events:
    → Accepted (role-based className + specName)

else:
    → Tentative
```

The `available that day` check uses `patt.player_availability` where
`day_of_week` is 0=Monday … 6=Sunday (Python's `date.weekday()` convention).

---

## Complete Worked Examples

### Full flow: create event + pre-populate roster

This is the sequence our platform runs when you click "Create Event in Raid-Helper"
on the Admin → Raid Tools page.

**Step 1: Create the event**

```http
POST https://raid-helper.dev/api/v2/servers/1288213206938026069/channels/1327719564842237993/event
Authorization: KbfhAKAP2LHt2gVrFJntXpnhkfJeUb67j36Haprk
Content-Type: application/json

{
  "leaderId": "195547238959677441",
  "templateId": "wowretail2",
  "date": "18-3-2026",
  "time": "20:00",
  "title": "Progression Raid Night — Heroic",
  "description": "Auto-scheduled raid. Sign up below!",
  "duration": 120
}
```

Response gives `event.id` → `"1482924660516716656"`.

**Step 2: Add signups one at a time with 200 ms delay**

```http
POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "195547238959677441", "className": "Ranged",   "specName": "Balance" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "967560340328038420", "className": "Tank",     "specName": "Protection" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "149815743066669056", "className": "Healer",   "specName": "Holy1" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "617906641232199740", "className": "Melee",    "specName": "Fury" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "328047843526836228", "className": "Melee",    "specName": "Outlaw" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "125394622489821184", "className": "Healer",   "specName": "Preservation" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "92127779528654848",  "className": "Ranged",   "specName": "Affliction" }

POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "68097839854522368",  "className": "Ranged",   "specName": "Marksmanship" }

# Player unavailable Wednesday — goes to Absence section
POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "356120440105861120", "className": "Absence" }

# Initiate (rank 1) — Tentative section
POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "1353785807785431060", "className": "Tentative" }

# Player on raid hiatus — also Absence
POST https://raid-helper.dev/api/v2/events/1482924660516716656/signups
{ "userId": "190301107271106561", "className": "Absence" }
```

---

## Errors We Hit and What They Mean

These are real errors from production logs, documented so future debugging is faster.

### `{"error": "invalid className"}`

**Cause:** Sending a WoW class name (`"Paladin"`, `"Druid"`) instead of a role slot
(`"Tank"`, `"Ranged"`). This was our primary bug during initial integration.

**Fix:** Use the role-based `className` from the `SPEC_TO_RAID_HELPER` map, not the
raw class name from the DB.

**Diagnostic command** — fetch the event to see the exact class names the template defines:
```bash
curl -s -H "Authorization: {api_key}" \
  "https://raid-helper.dev/api/v2/events/{eventId}" \
  | python3 -c "
import json,sys
data=json.load(sys.stdin)
for cls in data.get('classes',[]):
    print(cls['name'], '->', [s['name'] for s in cls['specs']])
"
```

---

### `{"error": "missing className"}`

**Cause:** Sending a signup with no `className` field. This happens when a player has
no main spec set in the DB so the `SPEC_TO_RAID_HELPER` lookup returns `None`.

**Fix:** Always send a `className`. If class/spec lookup fails, fall back to
`"Tentative"` rather than omitting the field.

---

### `404 Not Found` on signup endpoint

**Cause:** Using the wrong endpoint. We originally tried
`PUT /api/v2/events/{id}/signup/{userId}` (singular, user ID in path). That endpoint
doesn't exist.

**Correct endpoint:** `POST /api/v2/events/{id}/signups` (plural, user ID in body).

---

### `NameError: name 'signups' is not defined`

**Cause:** Python-side — a leftover `logger.info(... len(signups) ...)` inside
`create_event()` after the `signups` parameter was removed from its signature.

**Lesson:** When removing a parameter from a function, search the entire function body
for references to it, not just the signature line.

---

### Wrong date (one day ahead)

**Cause:** JavaScript's `toISOString()` converts to UTC. In any timezone behind UTC
(all of the US), calling `new Date().toISOString().split('T')[0]` after ~7pm local
time gives tomorrow's UTC date.

**Fix:** Build the date string from local date parts:
```javascript
const y = date.getFullYear();
const m = String(date.getMonth() + 1).padStart(2, '0');
const d = String(date.getDate()).padStart(2, '0');
return `${y}-${m}-${d}`;
```

---

### Git deploy fails: `cannot lock ref 'refs/remotes/origin/main'`

Not a Raid-Helper issue, but it blocked every prod deploy during this integration.
The server's local git clone had a stale remote-tracking ref. Fix:
```bash
ssh hetzner "cd /opt/guild-portal && git remote prune origin"
```
This is now prevented by adding `--prune` to the `git fetch` call in
`.github/workflows/deploy-prod.yml`.

---

## Our Implementation

All Raid-Helper logic lives in two files:

### `src/guild_portal/services/raid_helper_service.py`

| Symbol | Purpose |
|---|---|
| `SPEC_TO_RAID_HELPER` | Maps `(wow_class, wow_spec)` → `(rh_className, rh_specName)` |
| `create_event()` | `POST /servers/{id}/channels/{id}/event` — returns `{event_id, event_url, payload}` |
| `add_signups_to_event()` | `POST /events/{id}/signups` for each player, 200 ms apart |
| `test_connection()` | `GET /servers/{id}/events` — validates credentials |

### `src/guild_portal/services/raid_booking_service.py`

Auto-booking scheduler that fires 10–20 minutes after a recurring raid event starts,
creates next week's event, and adds signups. Uses the same `create_event` +
`add_signups_to_event` flow.

### `src/guild_portal/api/admin_routes.py`

The `POST /api/v1/admin/raid-events` route handles manual event creation from the
Raid Tools admin page. It runs the same two-step flow but reads event details from
the request body and player overrides from the UI.

### Status flow (server-side)

```python
# admin_routes.py and raid_booking_service.py both use this logic:

if player.on_raid_hiatus or player_id not in available_on_day:
    status = "absence"
elif rank_level >= 2 and player.auto_invite_events:
    status = "accepted"   # → role className + specName
else:
    status = "tentative"  # → className: "Tentative", no specName

# availability check: patt.player_availability WHERE day_of_week = raid_dow
# day_of_week: 0=Monday … 6=Sunday  (Python date.weekday() convention)
```

---

## Configuration in the Platform

All Raid-Helper settings are stored in `common.discord_config` and managed via
**Admin → Bot Settings**. Never hard-code these values.

| Column | What it is |
|---|---|
| `raid_helper_server_id` | Your Discord server's numeric ID |
| `raid_helper_api_key` | Server API key from `/apikey create` |
| `raid_creator_discord_id` | Discord user ID set as event `leaderId` |
| `raid_default_template_id` | Defaults to `wowretail2` if blank |
| `raid_channel_id` | Default channel for auto-booked events |

To get these values at runtime:
```python
config = await conn.fetchrow("SELECT * FROM common.discord_config LIMIT 1")
api_key = config["raid_helper_api_key"]
server_id = config["raid_helper_server_id"]
```
