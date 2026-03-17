# Customer Feedback System — Full Roadmap

> **Branch:** `phase-feedback` (create fresh from `main` in each repo)
> **Status:** Planned

---

## Design Philosophy

**PII stays with the app that collected it. The Hub never sees it.**

Each client app (PATT, podcast tool, future apps) owns its users' data. The Hub is the
AI processor and analytics store — it receives only de-identified payloads. A one-way
privacy token derived from contact info lets the Hub detect repeat feedback from the
same person without being able to identify them.

This satisfies GDPR data minimization, scales cleanly to multiple apps serving
different audiences, and means no cross-tenant data leakage if apps are ever
operated by different businesses.

---

## Architecture

```
User submits on a client app (e.g. PATT)
  │
  ├─ 1. Client stores raw record locally (contact_info lives here ONLY)
  ├─ 2. Client generates privacy_token = sha256(APP_SALT + normalize(contact_info))
  │         → one-way; Hub cannot reverse it
  ├─ 3. Client POSTs to Hub:
  │       { program_name, score, raw_feedback,
  │         is_authenticated_user, is_anonymous, privacy_token }
  │       NOTE: contact_info is NEVER sent to Hub
  │
  └─ Hub ingest endpoint
       ├─ Stores de-identified record
       ├─ Runs Claude AI → summary, sentiment, tags
       └─ Returns { ok: true, hub_feedback_id: N }

  Client stores hub_feedback_id on local record for cross-reference.
  If Hub is unreachable, local record is still saved; hub_feedback_id stays NULL.

Hub display module reads its own local DB — no proxy, no cross-app join.
```

---

## Privacy Token Design

```python
import hashlib

def make_privacy_token(contact_info: str, app_salt: str) -> str:
    normalized = contact_info.strip().lower()
    return hashlib.sha256(f"{app_salt}{normalized}".encode()).hexdigest()
```

- `app_salt` is a per-app secret env var (`FEEDBACK_PRIVACY_SALT`)
- If anonymous or no contact info: `privacy_token = NULL`
- **Different apps use different salts** — same person on PATT and podcast tool
  produce different tokens. No unintended cross-app identity correlation.

**What the Hub can answer with the token:**
- "This token has left 3 feedback items on this program."
- "These two feedback records came from the same person."

**What the Hub cannot answer:**
- "Who is this person?" (requires the client app's salt + the original contact info)

---

## Database Schemas

### Hub DB (shadowedvaca-site, `shadowedvaca` schema)

```sql
CREATE TABLE shadowedvaca.customer_feedback (
    id                    SERIAL PRIMARY KEY,
    program_name          VARCHAR(80)  NOT NULL,
    received_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- De-identified context only
    is_authenticated_user BOOLEAN      NOT NULL DEFAULT FALSE,
    is_anonymous          BOOLEAN      NOT NULL DEFAULT FALSE,
    privacy_token         VARCHAR(64),          -- NULL if anonymous

    -- Raw inputs
    score                 INTEGER      CHECK (score BETWEEN 1 AND 10),
    raw_feedback          TEXT         NOT NULL,

    -- AI-enriched
    summary               TEXT,
    sentiment             VARCHAR(20),          -- positive|neutral|negative|mixed
    tags                  JSONB,
    processed_at          TIMESTAMPTZ,
    processing_error      TEXT
);
```

### Client DB (PATT, `common` schema — per-app copy)

```sql
CREATE TABLE common.feedback_submissions (
    id                    SERIAL PRIMARY KEY,
    program_name          VARCHAR(80)  NOT NULL DEFAULT 'patt-guild-portal',
    submitted_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- PII lives here only
    is_authenticated_user BOOLEAN      NOT NULL DEFAULT FALSE,
    is_anonymous          BOOLEAN      NOT NULL DEFAULT FALSE,
    contact_info          VARCHAR(255),         -- real value; never sent to Hub
    privacy_token         VARCHAR(64),          -- sha256 hash; sent to Hub

    -- Raw inputs
    score                 INTEGER      CHECK (score BETWEEN 1 AND 10),
    raw_feedback          TEXT         NOT NULL,

    -- Hub cross-reference
    hub_feedback_id       INTEGER,             -- NULL until Hub confirms
    hub_synced_at         TIMESTAMPTZ
);
```

---

## AI Processing Tag Vocabulary

Fixed list; Claude must choose only from these:

`new feature request` · `bug report` · `praise` · `improvement suggestion` ·
`missing content` · `performance issue` · `ui/ux` · `documentation` ·
`confusing/unclear` · `other`

---

## Phase Table

| Phase | Repo | Description |
|-------|------|-------------|
| F.1 | shadowedvaca-site | Hub ingest endpoint + DB + AI processing |
| F.2 | PullAllTheThings-site | `sv_common.feedback` client package + PATT DB table |
| F.3 | PullAllTheThings-site | Feedback button + form + PATT submission API |
| F.4 | shadowedvaca-site | Hub display module — card grid + filters + tool card |

Sequential: F.1 → F.2 → F.3. F.4 can run in parallel with F.3 once F.1 is deployed.

---

## Sub-Phase Documents

| Phase | File | Repo |
|-------|------|------|
| F.1 | `reference/PHASE_F1_HUB_INGEST.md` | shadowedvaca-site |
| F.2 | `reference/PHASE_F2_CLIENT_PACKAGE.md` | PullAllTheThings-site |
| F.3 | `reference/PHASE_F3_PATT_FORM.md` | PullAllTheThings-site |
| F.4 | `reference/PHASE_F4_HUB_DISPLAY.md` | shadowedvaca-site |

---

## Environment Variables

### Hub (shadowedvaca-site)
```bash
FEEDBACK_INGEST_KEY=<32+ byte random string>   # clients must send this to POST /api/feedback/ingest
ANTHROPIC_API_KEY=<your key>                   # for AI processing
```

### Each Client App (e.g. PATT)
```bash
FEEDBACK_HUB_URL=https://hub.shadowedvaca.com  # or wherever the Hub lives
FEEDBACK_INGEST_KEY=<same value as Hub>        # shared ingest secret
FEEDBACK_PRIVACY_SALT=<per-app random string>  # DIFFERENT for each app; never share
```

---

## Acceptance Criteria (Full System)

- [ ] Hub stores feedback with no PII — no contact_info field, only privacy_token
- [ ] Client stores full raw record locally including contact_info
- [ ] privacy_token is NULL for anonymous submissions; never derived when is_anonymous=TRUE
- [ ] Different apps with different salts produce different tokens for the same person
- [ ] Hub AI processing populates summary, sentiment, tags
- [ ] Hub degrades gracefully when ANTHROPIC_API_KEY absent (stores record, skips AI)
- [ ] Client saves local record even when Hub is unreachable; hub_feedback_id stays NULL
- [ ] Feedback button visible on every PATT page (public and admin)
- [ ] Form works for logged-in and anonymous visitors; contact field pre-filled if logged in
- [ ] Hub display page shows all programs' feedback; cards sorted newest first
- [ ] Filters work: program, sentiment, tag, score range
- [ ] `customer_feedback` Hub tool card is admin-only
