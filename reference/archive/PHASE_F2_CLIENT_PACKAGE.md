# Phase F.2 — sv_common.feedback: Client Package + PATT Local DB

> **Repo:** PullAllTheThings-site
> **Branch:** `phase-feedback` (continue from wherever you are, or fresh from `main`)
> **Migration:** 0048
> **Depends on:** Phase F.1 deployed — Hub's `POST /api/feedback/ingest` must be live
> and `FEEDBACK_HUB_URL` + `FEEDBACK_INGEST_KEY` must be configured
> **Produces:** `common.feedback_submissions` local table, `sv_common.feedback` package,
> full unit tests. No UI — just the data layer and Hub client.

---

## Goal

Build the client-side half of the feedback system in `sv_common`:

1. A local `common.feedback_submissions` table that stores the raw record with PII
2. A `_privacy.py` module for one-way privacy token generation
3. A `_hub_client.py` module that POSTs to the Hub ingest endpoint
4. A `_store.py` module for asyncpg local DB operations
5. A `submit_feedback()` public function that orchestrates the full flow

The Hub is **fire-and-forget**: local record is always saved first. If the Hub is
unreachable or returns an error, the local record is still persisted and
`hub_feedback_id` remains NULL. No retries in this phase.

This phase has zero UI changes. Phase F.3 builds the form and routes that call
`submit_feedback()`.

---

## Prerequisites

- Phase F.1 deployed to Hub dev/prod
- `FEEDBACK_HUB_URL`, `FEEDBACK_INGEST_KEY`, `FEEDBACK_PRIVACY_SALT` in `.env`
- Familiar with `sv_common/errors/` package structure (direct parallel to follow)
- Familiar with asyncpg pool pattern (same as `errors._store`)
- Next Alembic migration number is 0048

---

## Key Files to Read Before Starting

- `src/sv_common/errors/__init__.py` — public API pattern to mirror exactly
- `src/sv_common/errors/_store.py` — asyncpg insert/query pattern
- `src/sv_common/db/models.py` — where to add `FeedbackSubmission` ORM model
- `alembic/versions/0047_error_routing.py` — migration pattern to follow
- `src/guild_portal/app.py` — where `set_program_name()` will be called at startup
- `src/sv_common/config_cache.py` — where to add `set_program_name` / `get_program_name`
- `TESTING.md` — test conventions

---

## Environment Variables

Add to server `.env` (all three required):
```bash
FEEDBACK_HUB_URL=https://hub.shadowedvaca.com   # Hub base URL
FEEDBACK_INGEST_KEY=<same value as Hub's FEEDBACK_INGEST_KEY>
FEEDBACK_PRIVACY_SALT=<unique per-app random string — NOT shared with Hub or other apps>
```

`FEEDBACK_PRIVACY_SALT` must be different for every app that uses `sv_common.feedback`.
This is what prevents cross-app identity correlation at the Hub.

---

## Database Migration: 0048

**File:** `alembic/versions/0048_feedback_submissions.py`

```python
"""local feedback submissions table

Revision ID: 0048
Revises: 0047
Create Date: <today>
"""
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE common.feedback_submissions (
            id                    SERIAL PRIMARY KEY,
            program_name          VARCHAR(80)  NOT NULL,
            submitted_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

            is_authenticated_user BOOLEAN      NOT NULL DEFAULT FALSE,
            is_anonymous          BOOLEAN      NOT NULL DEFAULT FALSE,
            contact_info          VARCHAR(255),
            privacy_token         VARCHAR(64),

            score                 INTEGER      CHECK (score BETWEEN 1 AND 10),
            raw_feedback          TEXT         NOT NULL,

            hub_feedback_id       INTEGER,
            hub_synced_at         TIMESTAMPTZ
        )
    """)
    op.execute("""
        CREATE INDEX idx_fs_program
            ON common.feedback_submissions (program_name)
    """)
    op.execute("""
        CREATE INDEX idx_fs_submitted
            ON common.feedback_submissions (submitted_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS common.feedback_submissions")
```

---

## ORM Model

Add to `src/sv_common/db/models.py` (after `ErrorLog`, follow same pattern):

```python
class FeedbackSubmission(Base):
    __tablename__ = "feedback_submissions"
    __table_args__ = {"schema": "common"}

    id:                    Mapped[int]            = mapped_column(primary_key=True)
    program_name:          Mapped[str]            = mapped_column(String(80))
    submitted_at:          Mapped[datetime]       = mapped_column(
                               TIMESTAMP(timezone=True), server_default=func.now()
                           )
    is_authenticated_user: Mapped[bool]           = mapped_column(Boolean, default=False)
    is_anonymous:          Mapped[bool]           = mapped_column(Boolean, default=False)
    contact_info:          Mapped[Optional[str]]  = mapped_column(String(255))
    privacy_token:         Mapped[Optional[str]]  = mapped_column(String(64))
    score:                 Mapped[Optional[int]]  = mapped_column(Integer)
    raw_feedback:          Mapped[str]            = mapped_column(Text)
    hub_feedback_id:       Mapped[Optional[int]]  = mapped_column(Integer)
    hub_synced_at:         Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
```

---

## config_cache additions

**File:** `src/sv_common/config_cache.py`

Add at the bottom (after existing getters):

```python
_program_name: str = "unknown"

def set_program_name(name: str) -> None:
    global _program_name
    _program_name = name

def get_program_name() -> str:
    return _program_name
```

In `src/guild_portal/app.py`, in the `lifespan` startup block after `set_site_config()`:
```python
from sv_common.config_cache import set_program_name
set_program_name("patt-guild-portal")
```

---

## sv_common.feedback Package

### File Structure

```
src/sv_common/feedback/
├── __init__.py       — public API: submit_feedback()
├── _privacy.py       — privacy token generation
├── _store.py         — asyncpg local DB operations
└── _hub_client.py    — HTTP POST to Hub ingest endpoint
```

---

### `_privacy.py`

```python
"""
One-way privacy token generation.

The token is a SHA-256 hash of (FEEDBACK_PRIVACY_SALT + normalized_contact_info).
It cannot be reversed without the salt. Different apps should use different salts.

Returns None when contact_info is absent or when is_anonymous is True.
"""
import hashlib
import os
from typing import Optional


def make_privacy_token(contact_info: Optional[str], is_anonymous: bool) -> Optional[str]:
    """
    Generate a one-way privacy token from contact info.

    Returns None if:
    - is_anonymous is True
    - contact_info is None or empty
    - FEEDBACK_PRIVACY_SALT env var is not set
    """
    if is_anonymous or not contact_info or not contact_info.strip():
        return None

    salt = os.environ.get("FEEDBACK_PRIVACY_SALT", "")
    if not salt:
        return None

    normalized = contact_info.strip().lower()
    return hashlib.sha256(f"{salt}{normalized}".encode()).hexdigest()
```

---

### `_store.py`

```python
"""
asyncpg queries for common.feedback_submissions.
Internal — called only by sv_common.feedback.__init__.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

import asyncpg


async def _insert_submission(
    pool: asyncpg.Pool,
    program_name: str,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool,
    is_anonymous: bool,
    contact_info: Optional[str],
    privacy_token: Optional[str],
) -> int:
    """Insert a local feedback record. Returns new row id."""
    row = await pool.fetchrow(
        """
        INSERT INTO common.feedback_submissions
            (program_name, score, raw_feedback,
             is_authenticated_user, is_anonymous,
             contact_info, privacy_token)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        program_name,
        score,
        raw_feedback.strip(),
        is_authenticated_user,
        is_anonymous,
        contact_info,
        privacy_token,
    )
    return row["id"]


async def _update_hub_ref(
    pool: asyncpg.Pool,
    submission_id: int,
    hub_feedback_id: int,
) -> None:
    """Store the Hub's returned id after successful ingest."""
    await pool.execute(
        """
        UPDATE common.feedback_submissions
        SET hub_feedback_id = $1,
            hub_synced_at   = NOW()
        WHERE id = $2
        """,
        hub_feedback_id,
        submission_id,
    )
```

---

### `_hub_client.py`

```python
"""
HTTP client for the Hub ingest endpoint.
Fire-and-forget: caller handles None return gracefully.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


async def post_to_hub(
    program_name: str,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool,
    is_anonymous: bool,
    privacy_token: Optional[str],
) -> Optional[int]:
    """
    POST de-identified payload to Hub ingest endpoint.

    Returns hub_feedback_id on success, None on any failure.
    Never raises — all exceptions are caught and logged.
    """
    hub_url = os.environ.get("FEEDBACK_HUB_URL", "").rstrip("/")
    ingest_key = os.environ.get("FEEDBACK_INGEST_KEY", "")

    if not hub_url or not ingest_key:
        logger.warning("FEEDBACK_HUB_URL or FEEDBACK_INGEST_KEY not set; skipping Hub sync")
        return None

    payload = {
        "program_name":          program_name,
        "score":                 score,
        "raw_feedback":          raw_feedback,
        "is_authenticated_user": is_authenticated_user,
        "is_anonymous":          is_anonymous,
        "privacy_token":         privacy_token,
    }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{hub_url}/api/feedback/ingest",
                json=payload,
                headers={"X-Ingest-Key": ingest_key},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("hub_feedback_id")

    except Exception as exc:
        logger.error("Hub feedback ingest failed (local record still saved): %s", exc)
        return None
```

---

### `__init__.py`

```python
"""
sv_common.feedback — client-side feedback collection.

Stores raw feedback locally (PII included), generates a one-way privacy token,
and forwards a de-identified payload to the Hub for AI processing.

Public API:
    submit_feedback(pool, ...)  →  dict

Design: no Discord, no FastAPI, no Jinja2. Pure asyncpg + httpx.
Requires: asyncpg.Pool, FEEDBACK_HUB_URL, FEEDBACK_INGEST_KEY, FEEDBACK_PRIVACY_SALT
"""
from __future__ import annotations
import logging
from typing import Optional

import asyncpg

from ._privacy import make_privacy_token
from ._store import _insert_submission, _update_hub_ref
from ._hub_client import post_to_hub
from sv_common.config_cache import get_program_name

logger = logging.getLogger(__name__)

__all__ = ["submit_feedback"]


async def submit_feedback(
    pool: asyncpg.Pool,
    score: int,
    raw_feedback: str,
    is_authenticated_user: bool = False,
    contact_info: Optional[str] = None,
    is_anonymous: bool = False,
    program_name: Optional[str] = None,
) -> dict:
    """
    Full feedback submission flow:
    1. Validate inputs
    2. Generate privacy token (one-way hash; None if anonymous or no contact)
    3. Insert local record (PII stored here)
    4. POST de-identified payload to Hub (fire-and-forget)
    5. If Hub responds, store hub_feedback_id on local record

    Returns a dict with the local record's id and hub_feedback_id (may be None).
    Raises ValueError on invalid inputs.
    Local record is always saved even if Hub call fails.
    """
    if not raw_feedback or not raw_feedback.strip():
        raise ValueError("raw_feedback must not be empty")
    if not (1 <= score <= 10):
        raise ValueError("score must be between 1 and 10")

    prog = program_name or get_program_name()
    privacy_token = make_privacy_token(contact_info, is_anonymous)
    stored_contact = None if is_anonymous else contact_info

    local_id = await _insert_submission(
        pool=pool,
        program_name=prog,
        score=score,
        raw_feedback=raw_feedback,
        is_authenticated_user=is_authenticated_user,
        is_anonymous=is_anonymous,
        contact_info=stored_contact,
        privacy_token=privacy_token,
    )

    hub_id = await post_to_hub(
        program_name=prog,
        score=score,
        raw_feedback=raw_feedback,
        is_authenticated_user=is_authenticated_user,
        is_anonymous=is_anonymous,
        privacy_token=privacy_token,
    )

    if hub_id is not None:
        await _update_hub_ref(pool, local_id, hub_id)
        logger.info("Feedback submitted: local_id=%d hub_id=%d", local_id, hub_id)
    else:
        logger.info("Feedback submitted locally: local_id=%d (Hub sync pending)", local_id)

    return {
        "id": local_id,
        "hub_feedback_id": hub_id,
        "program_name": prog,
    }
```

---

## Tests

**File:** `tests/unit/test_feedback_package.py`

### `test_make_privacy_token_deterministic`
- Same contact_info + same salt (patch env var) → same token both calls

### `test_make_privacy_token_case_insensitive`
- `"Mike@Example.com"` and `"mike@example.com"` → same token

### `test_make_privacy_token_anonymous_returns_none`
- `make_privacy_token("mike@example.com", is_anonymous=True)` → `None`

### `test_make_privacy_token_no_contact_returns_none`
- `make_privacy_token(None, False)` → `None`
- `make_privacy_token("", False)` → `None`

### `test_make_privacy_token_no_salt_returns_none`
- Patch env: `FEEDBACK_PRIVACY_SALT=""` → `None`

### `test_submit_feedback_stores_locally`
- Mock `_insert_submission` returns `42`
- Mock `post_to_hub` returns `99`
- Mock `_update_hub_ref`
- Call `submit_feedback(pool, score=8, raw_feedback="Great!", is_authenticated_user=True)`
- Assert `_insert_submission` called with `score=8`, `is_anonymous=False`
- Assert returned dict has `id=42, hub_feedback_id=99`

### `test_submit_feedback_anonymous_clears_contact`
- Call with `contact_info="mike@test.com"`, `is_anonymous=True`
- Assert `_insert_submission` called with `contact_info=None`
- Assert `post_to_hub` called with `privacy_token=None`

### `test_submit_feedback_hub_failure_still_saves`
- Mock `post_to_hub` returns `None` (simulates Hub unreachable)
- Assert `_insert_submission` still called (local record saved)
- Assert `_update_hub_ref` NOT called
- Assert returned dict has `hub_feedback_id=None`

### `test_submit_feedback_empty_text_raises`
- `raw_feedback="   "` → `ValueError`

### `test_submit_feedback_invalid_score_raises`
- `score=0` or `score=11` → `ValueError`

### `test_post_to_hub_returns_none_when_unconfigured`
- Patch env: `FEEDBACK_HUB_URL=""` → returns `None` without raising

### `test_post_to_hub_returns_none_on_http_error`
- Mock httpx to raise `httpx.ConnectError`
- Returns `None` without raising

---

## Deliverables Checklist

- [ ] `alembic/versions/0048_feedback_submissions.py` — table + 2 indexes
- [ ] `src/sv_common/db/models.py` — `FeedbackSubmission` ORM model added
- [ ] `src/sv_common/config_cache.py` — `set_program_name()` / `get_program_name()`
- [ ] `src/guild_portal/app.py` — `set_program_name("patt-guild-portal")` at startup
- [ ] `src/sv_common/feedback/__init__.py` — `submit_feedback()`
- [ ] `src/sv_common/feedback/_privacy.py` — `make_privacy_token()`
- [ ] `src/sv_common/feedback/_store.py` — `_insert_submission()`, `_update_hub_ref()`
- [ ] `src/sv_common/feedback/_hub_client.py` — `post_to_hub()`
- [ ] Server `.env` — `FEEDBACK_HUB_URL`, `FEEDBACK_INGEST_KEY`, `FEEDBACK_PRIVACY_SALT` set
- [ ] `tests/unit/test_feedback_package.py` — all tests pass
- [ ] `pytest tests/unit/ -v` — all existing tests still pass

---

## What This Phase Does NOT Do

- No feedback button or form (Phase F.3)
- No API routes in guild_portal (Phase F.3)
- No retry mechanism for failed Hub syncs (future phase)
- No admin page for viewing local feedback records (future phase)
