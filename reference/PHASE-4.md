# Phase 4: Frontend — Vote UI, Results & Admin Pages

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phases 0-3 must be complete.
> **Goal:** Server-rendered pages for voting, results viewing, admin campaign management,
> and admin roster/invite management. Functional first — the skeleton wears clothes.

---

## What This Phase Produces

1. Public voting page — image gallery with ranked-choice selection (pick top 3)
2. Results page — live standings with images ranked by score
3. Admin: campaign management page (create, add entries, activate, close)
4. Admin: roster management page (members, ranks, Discord IDs, send invites)
5. Registration and login pages
6. Base template with PATT dark theme
7. Mobile-responsive layouts

---

## Context From Previous Phases

After Phase 3:
- Full campaign engine with voting API
- Auth system with registration, login, JWT
- Discord bot running with role sync
- Guild identity system with ranks, members, characters
- All business logic tested and working

---

## Design Direction

Refer to CLAUDE.md "Design Language" section. The aesthetic is dark fantasy WoW tavern:
dark backgrounds, gold accents, Cinzel for headers, Source Sans Pro for body text.

**Admin pages:** Functional and clean. Dark cards on dark background. Tables with
clear data. Forms that work. No polish needed — just make them usable and readable.
Think "raid leader's command screen" not "marketing landing page."

**Vote/Results pages:** These are the public-facing showcase. Still the dark theme,
but the images should be the stars. Cards that display the art prominently with
names and vote controls. The voting interaction (pick your top 3) should be
intuitive on both desktop and mobile.

---

## Tasks

### 4.1 — Base Template (`patt/templates/base.html`)

Standard HTML5 shell with:
- Google Fonts: Cinzel, Source Sans Pro, JetBrains Mono
- CSS custom properties for the full color palette
- Responsive viewport meta tag
- Navigation header with: site title, user status (logged in as / login link)
- Footer with guild info
- Block regions: `title`, `head`, `content`, `scripts`
- Toast/flash message display area

### 4.2 — Auth Pages

**`templates/auth/login.html`**
- Discord username + password form
- Link to registration page
- Error display for failed login

**`templates/auth/register.html`**
- Invite code + Discord username + password (+ confirm password) form
- Error display for invalid code, mismatched username, etc.
- Success redirects to the page they were trying to access (or home)

**Page routes (`patt/pages/auth_pages.py`):**
```
GET  /login     → render login form
POST /login     → validate, set JWT cookie, redirect
GET  /register  → render register form
POST /register  → validate, create account, set JWT cookie, redirect
GET  /logout    → clear cookie, redirect to home
```

Use HTTP-only secure cookies for JWT in the browser (not localStorage).

### 4.3 — Vote Page (`templates/vote/campaign.html`)

**Layout:** A page showing the campaign title, description, countdown timer, and
a grid of entry cards. Each card shows:
- The image (loaded from Google Drive URL)
- The character/entry name
- A number badge when selected (1st, 2nd, 3rd)

**Interaction — Ranked Choice Selection:**
- Click an image to add it to your picks (first click = 1st choice, second = 2nd, third = 3rd)
- Click a selected image to deselect it (shifts others up)
- A "Your Picks" summary bar shows current selections with entry names and rank
- "Submit Vote" button (disabled until exactly 3 picks selected)
- After submission, the page transitions to show their vote + live standings

**States:**
1. **Can vote, hasn't voted:** Shows image grid with selection interaction
2. **Can vote, has voted:** Shows their picks highlighted + live standings
3. **Can view, can't vote (rank too low):** Shows image grid (no selection) + results
4. **Public (not logged in, campaign is public):** Shows results only
5. **Campaign not yet live:** Shows "Coming soon" with countdown to start_at
6. **Campaign closed:** Shows final results with winner highlighted

**Page route (`patt/pages/vote_pages.py`):**
```
GET /vote/{campaign_id}  → determine user state, render appropriate view
```

### 4.4 — Results Display (`templates/vote/results.html`)

Can be embedded in the campaign page or a standalone view.

**Layout:** Entries sorted by score (first to last). Each entry shows:
- Rank badge (#1, #2, #3 in gold, silver, bronze; rest numbered)
- The image
- Entry name
- Score bar (visual width proportional to score)
- Vote breakdown: "X first • Y second • Z third" and total weighted score
- Vote progress: "8 of 12 members have voted" with progress bar

For the winner, add visual emphasis (larger card, gold border glow, "WINNER" badge).

### 4.5 — Admin: Campaign Management (`templates/admin/campaigns.html`)

**Campaign list view:**
- Table: title, status, start date, duration, votes cast, actions
- "Create Campaign" button

**Campaign create/edit form:**
- Title (text input)
- Description (textarea)
- Type: dropdown (ranked_choice for now)
- Picks per voter (number, default 3)
- Minimum rank to vote (dropdown of rank names)
- Minimum rank to view (dropdown of rank names, plus "Public" option)
- Start date/time (datetime picker)
- Duration (hours input, or preset buttons: 24h, 48h, 1 week, 2 weeks)
- Early close when all voted (checkbox, default on)
- Discord announcement channel ID (text input)

**Entry management (within campaign edit view):**
- List of current entries with: thumbnail, name, description, associated member
- "Add Entry" form: name, description, image URL, associated member (dropdown)
- Remove/edit buttons per entry
- Drag handles for sort order (or simple up/down arrows)

**Campaign actions:**
- "Activate" button (draft → live) with confirmation
- "Close" button (live → closed) with confirmation
- Link to view live results

**Page routes (`patt/pages/admin_pages.py`):**
```
GET  /admin/campaigns                     → list all campaigns
GET  /admin/campaigns/new                 → create form
POST /admin/campaigns/new                 → create campaign
GET  /admin/campaigns/{id}/edit           → edit form with entries
POST /admin/campaigns/{id}/edit           → update campaign
POST /admin/campaigns/{id}/entries        → add entry
POST /admin/campaigns/{id}/entries/{eid}/delete  → remove entry
POST /admin/campaigns/{id}/activate       → activate
POST /admin/campaigns/{id}/close          → close
```

### 4.6 — Admin: Roster Management (`templates/admin/roster.html`)

**Roster table showing:**
- Display name / Discord username
- Discord ID (editable inline or via form)
- Current rank (dropdown to change)
- Rank source (manual / discord_sync)
- Registration status (registered / invite sent / not invited)
- "Send Invite" button (for unregistered members with Discord IDs)
- Characters (expandable row showing character list)

**Add member form:**
- Discord username (required)
- Discord ID (optional)
- Display name (optional)
- Rank (dropdown)

**Page routes:**
```
GET  /admin/roster                → roster table
POST /admin/roster/add            → add member
POST /admin/roster/{id}/update    → update member fields
POST /admin/roster/{id}/invite    → generate code + DM
```

### 4.7 — Public Landing Page (`templates/public/index.html`)

Simple landing page for pullallthething.com:
- Guild name and tagline
- The group art image (Pull_all_the_things_1.png — from Google Drive)
- Links to active campaigns
- Guild roster (public view)
- Login link

### 4.8 — JavaScript

All JS is vanilla (no frameworks). Keep it minimal:

**vote-interaction.js:**
- Click handlers for image cards (add/remove from picks)
- Pick summary bar updates
- Submit button enable/disable
- POST to vote API endpoint
- Transition to results view after successful vote

**countdown.js:**
- Displays time remaining for active campaigns
- Updates every second
- Shows "Voting has ended" when time expires

**admin-forms.js:**
- Form validation
- Confirmation dialogs for destructive actions (activate, close, delete)
- Inline editing for roster Discord IDs

### 4.9 — Tests

**Integration tests (`tests/integration/`):**

`test_page_rendering.py`:
- test_login_page_renders (GET /login → 200)
- test_register_page_renders (GET /register → 200)
- test_vote_page_renders_for_eligible_member (GET /vote/{id} → 200, contains vote form)
- test_vote_page_shows_results_for_ineligible_member
- test_vote_page_shows_results_after_voting
- test_admin_campaigns_requires_officer_rank
- test_admin_roster_requires_officer_rank
- test_admin_campaigns_accessible_by_officer
- test_public_landing_page_renders
- test_campaign_not_found_returns_404

---

## Acceptance Criteria

- [ ] Users can register and log in through web forms
- [ ] Vote page displays campaign images in a gallery layout
- [ ] Ranked-choice selection works (click to pick top 3, visual feedback)
- [ ] Vote submission works and transitions to results view
- [ ] Results display shows scores, rankings, and vote breakdowns
- [ ] Campaign countdown timer works
- [ ] Admin can create campaigns, add entries, activate, and close
- [ ] Admin can manage roster (add members, set Discord IDs, send invites)
- [ ] All pages are mobile-responsive
- [ ] Auth pages properly redirect after login/register
- [ ] Protected pages redirect to login when not authenticated
- [ ] All tests pass

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Manual visual testing on desktop and mobile
- [ ] Commit: `git commit -m "phase-4: frontend vote UI, results, and admin pages"`
- [ ] Update CLAUDE.md "Current Build Status" section
