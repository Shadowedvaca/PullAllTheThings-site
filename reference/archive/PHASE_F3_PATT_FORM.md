# Phase F.3 — PATT: Feedback Button, Form, and Submission API

> **Repo:** PullAllTheThings-site
> **Branch:** `phase-feedback` (continue from Phase F.2)
> **Migration:** none
> **Depends on:** Phase F.2 complete — `sv_common.feedback.submit_feedback()` works,
> `common.feedback_submissions` table exists
> **Produces:** Feedback badge on every page, `/feedback` form, `POST /api/v1/feedback`

---

## Goal

Wire `sv_common.feedback` into the live PATT site:

1. A small **"Feedback" badge** next to the guild name on every page (public + admin)
2. A `/feedback` **form page** — score question, free-text, optional contact
3. A **`POST /api/v1/feedback`** endpoint that calls `submit_feedback()` and returns
   a thank-you response

No changes to the data layer — that is all in Phase F.2.

---

## Prerequisites

- Phase F.2 complete and on the same branch
- `set_program_name("patt-guild-portal")` already called at startup (done in F.2)
- Familiar with both header templates
- Familiar with public page route pattern (`src/guild_portal/pages/public_pages.py`)
- Familiar with API route registration in `src/guild_portal/app.py`

---

## Key Files to Read Before Starting

- `src/guild_portal/templates/base.html` — add badge after `.site-title`
- `src/guild_portal/templates/base_admin.html` — add badge in the same spot
- `src/guild_portal/pages/public_pages.py` — add `GET /feedback` route
- `src/guild_portal/app.py` — register new API router
- `src/guild_portal/static/css/main.css` — add `.btn-feedback` style
- Any public template (e.g. `index.html`) — reference for base.html extension pattern

---

## Step 1: CSS — Feedback Badge

**File:** `src/guild_portal/static/css/main.css`

Add after existing `.site-title` rules:

```css
.btn-feedback {
    display: inline-block;
    padding: 0.18rem 0.5rem;
    font-size: 0.68rem;
    font-family: var(--font-body);
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--color-gold);
    background: rgba(212, 168, 75, 0.1);
    border: 1px solid rgba(212, 168, 75, 0.3);
    border-radius: 3px;
    text-decoration: none;
    vertical-align: middle;
    margin-left: 0.65rem;
    transition: background 0.15s, border-color 0.15s;
    white-space: nowrap;
}

.btn-feedback:hover {
    background: rgba(212, 168, 75, 0.2);
    border-color: rgba(212, 168, 75, 0.55);
    color: var(--color-gold);
    text-decoration: none;
}
```

---

## Step 2: Add Badge to Both Headers

In both files, locate the site title anchor and add the badge immediately after it,
before the `<nav>` element.

### `src/guild_portal/templates/base.html`
```html
<a href="/" class="site-title">{{ site().guild_name }}</a>
<a href="/feedback" class="btn-feedback">Feedback</a>
```

### `src/guild_portal/templates/base_admin.html`
```html
<a href="/" class="site-title">{{ site().guild_name }}</a>
<a href="/feedback" class="btn-feedback">Feedback</a>
```

---

## Step 3: Feedback Page Route

**File:** `src/guild_portal/pages/public_pages.py`

```python
@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request, db: AsyncSession = Depends(get_db)):
    current_member = await _get_current_member_optional(request, db)
    prefill_contact = current_member.display_name if current_member else None
    return templates.TemplateResponse(
        "feedback.html",
        {
            "request": request,
            "current_member": current_member,
            "is_authenticated": current_member is not None,
            "prefill_contact": prefill_contact,
        },
    )
```

Use `_get_current_member_optional` — the same helper already used by other public
pages that vary content based on login state.

---

## Step 4: Feedback Form Template

**File:** `src/guild_portal/templates/feedback.html`

Extends `base.html`. All page-specific styles in a `<style>` block in `{% block head %}`.
All JavaScript in a `<script>` block before `{% endblock %}`.

### HTML Structure

```html
{% extends "base.html" %}

{% block title %}Share Feedback — {{ site().guild_name }}{% endblock %}

{% block head %}
<style>
  /* styles here — see CSS section below */
</style>
{% endblock %}

{% block content %}
<div class="fb-wrap">
  <div class="fb-card" id="fb-form-card">

    <h1 class="fb-title">Share Your Feedback</h1>
    <p class="fb-intro">
      What's working, what's missing, what doesn't make sense? I read every
      response and use it to make this better. Anonymous feedback is absolutely welcome.
    </p>

    <!-- Score -->
    <div class="fb-section">
      <p class="fb-question">
        {% if is_authenticated %}
          On a scale of 1–10, how much are you enjoying this guild tool?
        {% else %}
          On a scale of 1–10, would you want to see a tool like this used by your guild?
        {% endif %}
      </p>
      <div class="fb-score-row" id="fb-score-row">
        {% for n in range(1, 11) %}
        <button type="button" class="fb-score-btn" data-score="{{ n }}">{{ n }}</button>
        {% endfor %}
      </div>
    </div>

    <!-- Free text -->
    <div class="fb-section">
      <label class="fb-label" for="fb-text">What's on your mind?</label>
      <textarea id="fb-text" class="fb-textarea" rows="6"
        placeholder="Anything at all — features you want, things that confused you, things you love..."></textarea>
    </div>

    <!-- Contact -->
    <div class="fb-section">
      {% if is_authenticated %}
        <label class="fb-label">
          Contact info
          <span class="fb-label-hint">(optional — for follow-up if you're open to it)</span>
        </label>
        <div class="fb-contact-row">
          <label class="fb-anon-label">
            <input type="checkbox" id="fb-anon"> Anonymous
          </label>
          <input type="text" id="fb-contact" class="fb-input"
            value="{{ prefill_contact or '' }}"
            placeholder="Email or Discord handle">
        </div>
      {% else %}
        <label class="fb-label" for="fb-contact">
          Contact info
          <span class="fb-label-hint">(optional — anonymous is totally fine)</span>
        </label>
        <input type="text" id="fb-contact" class="fb-input"
          placeholder="Email or Discord handle">
      {% endif %}
    </div>

    <div class="fb-actions">
      <button id="fb-submit" class="btn btn-primary">Send Feedback</button>
    </div>

  </div><!-- /fb-form-card -->

  <!-- Success state (hidden until submission) -->
  <div class="fb-card fb-success" id="fb-success" style="display:none">
    <div class="fb-success-icon">✓</div>
    <h2>Thanks for your feedback!</h2>
    <p>I read every response and use it to keep improving the platform.</p>
    <a href="/" class="btn btn-secondary" style="margin-top:1rem">Back to Home</a>
  </div>
</div>
{% endblock %}
```

### CSS (in `{% block head %}`)

```css
.fb-wrap { max-width: 620px; margin: 2.5rem auto; padding: 0 1rem; }
.fb-card {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border);
    border-radius: 8px;
    padding: 2rem 2.25rem;
}
.fb-title { font-family: var(--font-heading); margin-bottom: 0.5rem; }
.fb-intro { color: var(--color-text-muted); font-size: 0.9rem; margin-bottom: 1.75rem; }
.fb-section { margin-bottom: 1.5rem; }
.fb-question { font-weight: 600; margin-bottom: 0.75rem; }
.fb-label { display: block; font-weight: 600; margin-bottom: 0.5rem; }
.fb-label-hint { font-weight: 400; color: var(--color-text-muted); font-size: 0.85rem; }

.fb-score-row { display: flex; gap: 0.35rem; flex-wrap: wrap; }
.fb-score-btn {
    width: 2.6rem; height: 2.6rem;
    border: 1px solid var(--color-border);
    background: transparent;
    color: var(--color-text-muted);
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9rem;
    transition: border-color 0.12s, color 0.12s, background 0.12s;
}
.fb-score-btn:hover { border-color: var(--color-gold); color: var(--color-gold); }
.fb-score-btn.fb-score--active {
    background: var(--color-gold);
    border-color: var(--color-gold);
    color: #0a0a0b;
    font-weight: 700;
}

.fb-textarea, .fb-input {
    width: 100%;
    background: var(--color-bg);
    border: 1px solid var(--color-border);
    border-radius: 4px;
    color: var(--color-text);
    padding: 0.6rem 0.75rem;
    font-family: var(--font-body);
    font-size: 0.95rem;
    resize: vertical;
    box-sizing: border-box;
}
.fb-textarea:focus, .fb-input:focus {
    outline: none;
    border-color: var(--color-gold);
}

.fb-contact-row { display: flex; align-items: center; gap: 0.75rem; }
.fb-anon-label {
    display: flex; align-items: center; gap: 0.4rem;
    white-space: nowrap; cursor: pointer;
    font-size: 0.85rem; flex-shrink: 0;
}

.fb-actions { margin-top: 1.75rem; }

.fb-success { text-align: center; padding: 3rem 2rem; }
.fb-success-icon {
    font-size: 3rem; color: var(--color-gold);
    margin-bottom: 1rem; line-height: 1;
}
```

### JavaScript (inline `<script>` before `{% endblock %}`)

```javascript
(function () {
    let selectedScore = null;
    let savedContact = "";

    // Score button selection
    document.querySelectorAll(".fb-score-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".fb-score-btn")
                    .forEach(b => b.classList.remove("fb-score--active"));
            btn.classList.add("fb-score--active");
            selectedScore = parseInt(btn.dataset.score, 10);
        });
    });

    // Anonymous checkbox (logged-in users only)
    const anonCheckbox = document.getElementById("fb-anon");
    const contactInput = document.getElementById("fb-contact");

    if (anonCheckbox && contactInput) {
        anonCheckbox.addEventListener("change", () => {
            if (anonCheckbox.checked) {
                savedContact = contactInput.value;
                contactInput.value = "";
                contactInput.disabled = true;
                contactInput.placeholder = "—";
            } else {
                contactInput.disabled = false;
                contactInput.placeholder = "Email or Discord handle";
                contactInput.value = savedContact;
            }
        });
    }

    // Submission
    document.getElementById("fb-submit").addEventListener("click", async () => {
        const text = document.getElementById("fb-text").value.trim();

        if (!selectedScore) {
            alert("Please select a score (1–10) before submitting.");
            return;
        }
        if (!text) {
            alert("Please enter some feedback before submitting.");
            return;
        }

        const isAnon = anonCheckbox ? anonCheckbox.checked : false;
        const contact = (contactInput && !isAnon && contactInput.value.trim())
            ? contactInput.value.trim()
            : null;

        const btn = document.getElementById("fb-submit");
        btn.disabled = true;
        btn.textContent = "Sending…";

        try {
            const resp = await fetch("/api/v1/feedback", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    score: selectedScore,
                    feedback: text,
                    contact_info: contact,
                    is_anonymous: isAnon,
                }),
            });
            const data = await resp.json();
            if (data.ok) {
                document.getElementById("fb-form-card").style.display = "none";
                document.getElementById("fb-success").style.display = "block";
            } else {
                btn.disabled = false;
                btn.textContent = "Send Feedback";
                alert("Something went wrong. Please try again.");
            }
        } catch {
            btn.disabled = false;
            btn.textContent = "Send Feedback";
            alert("Network error. Please try again.");
        }
    });
})();
```

---

## Step 5: Submission API

**File:** `src/guild_portal/api/feedback_routes.py` (new file)

```python
"""
POST /api/v1/feedback
Public — no auth required. Accepts submissions from any visitor.
Calls sv_common.feedback.submit_feedback() which handles local storage + Hub sync.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from sv_common.feedback import submit_feedback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


class FeedbackBody(BaseModel):
    score:        int           = Field(..., ge=1, le=10)
    feedback:     str           = Field(..., min_length=1, max_length=5000)
    contact_info: Optional[str] = Field(None, max_length=255)
    is_anonymous: bool          = False


@router.post("")
async def submit_feedback_endpoint(body: FeedbackBody, request: Request):
    pool = request.app.state.guild_sync_pool

    # Best-effort: determine if user is logged in; never block anonymous
    is_authenticated = False
    try:
        from guild_portal.auth.helpers import get_current_member_optional
        member = await get_current_member_optional(request)
        is_authenticated = member is not None
    except Exception:
        pass

    await submit_feedback(
        pool=pool,
        score=body.score,
        raw_feedback=body.feedback,
        is_authenticated_user=is_authenticated,
        contact_info=body.contact_info,
        is_anonymous=body.is_anonymous,
    )
    return {"ok": True}
```

**Register in `src/guild_portal/app.py`:**
```python
from guild_portal.api.feedback_routes import router as feedback_router
app.include_router(feedback_router)
```

---

## Tests

**File:** `tests/unit/test_feedback_routes.py`

### `test_submit_valid_feedback`
- Mock `submit_feedback` returns `{"id": 1, "hub_feedback_id": 42, "program_name": "patt-guild-portal"}`
- POST `{"score": 7, "feedback": "Really useful!"}` to `/api/v1/feedback`
- Assert 200, `{"ok": True}`
- Assert `submit_feedback` called with `score=7`, `raw_feedback="Really useful!"`

### `test_submit_invalid_score`
- POST with `score=0` or `score=11` → 422

### `test_submit_empty_feedback`
- POST with `feedback=""` → 422

### `test_submit_anonymous`
- POST with `is_anonymous=True, contact_info="test@test.com"`
- Assert `submit_feedback` called with `is_anonymous=True`
  (privacy enforcement is inside `submit_feedback`, not here — don't duplicate)

### `test_submit_no_auth_still_works`
- Mock auth helper to raise an exception
- POST still returns 200 (anonymous path works fine)

---

## Deliverables Checklist

- [ ] `src/guild_portal/static/css/main.css` — `.btn-feedback` styles added
- [ ] `src/guild_portal/templates/base.html` — badge after `.site-title`
- [ ] `src/guild_portal/templates/base_admin.html` — badge after `.site-title`
- [ ] `src/guild_portal/pages/public_pages.py` — `GET /feedback` route
- [ ] `src/guild_portal/templates/feedback.html` — full form template
- [ ] `src/guild_portal/api/feedback_routes.py` — `POST /api/v1/feedback`
- [ ] `src/guild_portal/app.py` — `feedback_router` registered
- [ ] `tests/unit/test_feedback_routes.py` — all tests pass
- [ ] `pytest tests/unit/ -v` — all existing tests still pass
- [ ] Manual check: badge visible on `/`, `/roster`, `/admin/players`, etc.
- [ ] Manual check: form submits, success state appears, Hub receives the record

---

## What This Phase Does NOT Do

- No Hub display (Phase F.4)
- No PATT admin page for officers to browse local submissions (future)
- No rate-limiting on submission endpoint (add if abuse arises)
- No retry for failed Hub syncs (future — scan `hub_feedback_id IS NULL` records)
