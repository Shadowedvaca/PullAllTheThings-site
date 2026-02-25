# Phase 3.2 — Index Page Revamp

## Goal

Replace hardcoded sections of `index.html` with live data from the DB. Officers, recruiting needs,
and weekly schedule all come from the database — no more hardcoded names or dates.

---

## Public Page Route Changes (`src/patt/pages/public_pages.py`)

The existing `GET /` route handler needs to load three new data sets:

### 1. Officers List

Query `guild_identity.players` where `guild_ranks.level >= 4`, ordered by `guild_ranks.level DESC`
then `display_name ASC`.

Eagerly load (via SQLAlchemy joinedload or subqueryload):
- `guild_rank` (the `GuildRank` ORM object for rank level + name)
- `main_character` → `WowCharacter` with `wow_class` (for class color/emoji), `active_spec`

Build armory URL for each officer's main character:
```python
armory_url = f"https://worldofwarcraft.blizzard.com/en-us/character/us/{char.realm_slug}/{char.character_name.lower()}"
```

Pass as `officers` list to template.

### 2. Recruiting Needs

**Role targets (hardcoded constants):**
```python
ROLE_TARGETS = {
    "Tank": 2,
    "Healer": 4,
    "Melee DPS": 6,
    "Ranged DPS": 6,
}
```

Query: count active players with `main_character_id IS NOT NULL` grouped by main role.

```sql
SELECT r.name as role_name, COUNT(p.id) as count
FROM guild_identity.players p
JOIN guild_identity.specializations s ON p.main_spec_id = s.id
JOIN guild_identity.roles r ON s.default_role_id = r.id
WHERE p.is_active = TRUE AND p.main_character_id IS NOT NULL
GROUP BY r.name
```

Build `recruiting_needs` dict: `{role_name: needed_count}` where needed_count > 0.
A role is "recruiting" if `count < target`. Pass as template variable.

### 3. Event Days (from recurring_events)

```sql
SELECT * FROM patt.recurring_events
WHERE display_on_public = TRUE AND is_active = TRUE
ORDER BY day_of_week ASC
```

Pass as `event_days` list. Template shows day name + label + time.

### Day name mapping (Python)
```python
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
```

Apply to `recurring_event.day_of_week` when building the context.

### Full context dict for `GET /`
```python
{
    "officers": [...],           # NEW
    "recruiting_needs": {...},   # NEW — dict of {role: needed_count}
    "event_days": [...],         # NEW
    "live_campaigns": [...],     # existing
    "closed_campaigns": [...],   # existing
    "mito_quote": ...,           # existing
    "mito_title": ...,           # existing
}
```

---

## Template Changes (`src/patt/templates/public/index.html`)

### Officers Section

Replace hardcoded officer list with:

```jinja2
<div class="officers-grid">
  {% for officer in officers %}
  <div class="officer-card">
    <span class="class-emoji">{{ officer.class_emoji }}</span>
    <div class="officer-info">
      <a href="{{ officer.armory_url }}" target="_blank" class="officer-name" style="color: {{ officer.class_color }}">
        {{ officer.main_character.character_name if officer.main_character else officer.display_name }}
      </a>
      {% if officer.guild_rank.level == 5 %}
      <span class="rank-badge rank-gl">👑 Guild Leader</span>
      {% else %}
      <span class="rank-badge rank-officer">{{ officer.guild_rank.name }}</span>
      {% endif %}
      {% if officer.main_character and officer.main_character.active_spec %}
      <span class="spec-name">{{ officer.main_character.active_spec.name }}</span>
      {% endif %}
    </div>
  </div>
  {% endfor %}
</div>
```

### Recruiting Section

Replace hardcoded recruiting rows with:

```jinja2
{% if recruiting_needs %}
  <div class="recruiting-grid">
    {% for role, needed in recruiting_needs.items() %}
    <div class="recruiting-card">
      <span class="role-icon role-{{ role | lower | replace(' ', '-') }}">{{ role_emojis[role] }}</span>
      <div class="role-info">
        <span class="role-name">{{ role }}</span>
        <span class="recruiting-badge">Recruiting {{ needed }} player{{ 's' if needed > 1 else '' }}</span>
      </div>
    </div>
    {% endfor %}
  </div>
{% else %}
  <p class="roster-full">✅ Roster Full — not actively recruiting</p>
{% endif %}
```

### Weekly Schedule Section

Replace hardcoded schedule rows with:

```jinja2
{% if event_days %}
  <div class="schedule-list">
    {% for event in event_days %}
    <div class="schedule-row">
      <span class="schedule-day">{{ day_names[event.day_of_week] }}</span>
      <span class="schedule-label">{{ event.label }}</span>
      <span class="schedule-time">{{ event.default_start_time.strftime('%I:%M %p') }} EST</span>
    </div>
    {% endfor %}
  </div>
{% else %}
  <p class="no-schedule">No events currently scheduled.</p>
{% endif %}
```

### Members Only Links

Update link targets:
- `/roster.html` → `/roster`
- `/roster-view.html` → `/roster`

### Class Emoji Map (pass to template as `class_emojis`)

```python
CLASS_EMOJIS = {
    "Druid": "🌿",
    "Paladin": "⚔️",
    "Warlock": "👁️",
    "Priest": "✨",
    "Mage": "🔮",
    "Hunter": "🏹",
    "Warrior": "⚔️",
    "Shaman": "⚡",
    "Monk": "☯️",
    "Death Knight": "💀",
    "Demon Hunter": "🦅",
    "Evoker": "🐉",
    "Rogue": "🗡️",
}
```

### Role Emoji Map (pass to template as `role_emojis`)

```python
ROLE_EMOJIS = {
    "Tank": "🛡️",
    "Healer": "💚",
    "Melee DPS": "⚔️",
    "Ranged DPS": "🏹",
}
```

---

## Helper function for page route

Add to `public_pages.py`:

```python
async def _load_index_data(db) -> dict:
    """Load all live data for the index page."""
    officers = await _get_officers(db)
    recruiting_needs = await _get_recruiting_needs(db)
    event_days = await _get_event_days(db)
    # ... existing campaign/mito queries
    return {
        "officers": officers,
        "recruiting_needs": recruiting_needs,
        "event_days": event_days,
        "class_emojis": CLASS_EMOJIS,
        "role_emojis": ROLE_EMOJIS,
        "day_names": DAY_NAMES,
        # existing keys...
    }
```

---

## Files Modified

| File | Action |
|------|--------|
| `src/patt/pages/public_pages.py` | ADD officer/recruiting/event_day queries; update GET / handler |
| `src/patt/templates/public/index.html` | REPLACE hardcoded officers, recruiting, schedule sections |

---

## Verification Checklist

- [ ] Navigate to `/` → Officers section shows real player names/chars/specs from DB (not hardcoded)
- [ ] Each officer card has correct rank badge (👑 for GL)
- [ ] Armory links go to correct characters
- [ ] Recruiting section reflects actual role counts vs targets
- [ ] If all roles met → "Roster Full" message shown
- [ ] Schedule section shows only event days from `recurring_events` with `display_on_public=TRUE`
- [ ] If no event days configured → "No events currently scheduled" shown
- [ ] Members only links point to `/roster`
- [ ] Page still loads fast (no N+1 queries — use eager loading)
