# Phase 7: Polish & The Art Vote Goes Live

> **Prerequisites:** Read CLAUDE.md and TESTING.md first. Phases 0-6 must be complete.
> **Goal:** Everything polished, tested end-to-end, and the first campaign (Salt All The
> Things Profile Pic Contest) is configured and ready to launch.

---

## What This Phase Produces

1. End-to-end regression test suite covering the full platform
2. Visual polish on vote and results pages
3. The actual art vote campaign configured with all 10 images
4. Landing page with the group art (Pull_all_the_things_1.png)
5. Error pages (404, 500) styled with the PATT theme
6. Performance and security hardening
7. Deployment verification
8. Documentation for Mike on how to operate the platform

---

## Tasks

### 7.1 — End-to-End Regression Suite

Create `tests/regression/test_full_platform.py`:

A comprehensive test that exercises the entire flow:
1. Seed ranks
2. Create admin member (Guild Leader)
3. Create several guild members at various ranks
4. Generate invite codes for veteran+ members
5. Register those members
6. Create a campaign (ranked choice, 3 picks, veteran+ voting, public results)
7. Add 10 entries
8. Activate the campaign
9. Each eligible member casts their vote
10. Verify live standings update correctly after each vote
11. Verify ineligible members can't vote
12. Verify non-voters see vote form, not results
13. Verify voters see results after voting
14. When all eligible members have voted, verify early close triggers
15. Verify final results are correct
16. Verify contest agent logged the right events

This is the "pull a thread anywhere and this catches it" test.

### 7.2 — Configure the Art Vote Campaign

Create a script or admin action to set up the actual campaign:

**Campaign:**
- Title: "Salt All The Things Profile Pic Contest"
- Description: "Vote for your favorite character portrait! These will be used as profile pictures for the Salt All The Things podcast. Pick your top 3!"
- Type: ranked_choice
- Picks per voter: 3
- Minimum rank to vote: 3 (Veteran)
- Minimum rank to view: null (public — anyone can see results)
- Duration: Mike decides (suggest 1 week = 168 hours)
- Early close: yes
- Discord channel: Mike provides the channel ID

**Entries (10 images):**

| Name | Image | Associated Member |
|------|-------|-------------------|
| Trog | Google Drive link to Trog.png | Trog (Mike) |
| Rocket | Google Drive link to Rocket.png | Rocket |
| Mito | Google Drive link to Mito_Wall.png | Mito |
| Shodoom | Google Drive link to Shodoom.png | Shodoom |
| Skate | Google Drive link to Skate.png | Skate |
| Hit | Google Drive link to Hit.png | Hit |
| Kronas | Google Drive link to Kronas.png | Kronas |
| Porax | Google Drive link to Porax.png | Porax |
| Meggo | Google Drive link to Meggo.png | Meggo |
| Wyland | Google Drive link to Wyland.png | Wyland |

**Note:** The 11th image (Pull_all_the_things_1.png — the group shot) is NOT in the
vote. It goes on the landing page as the guild group photo.

Mike will need to:
1. Upload each image to Google Drive (already done at `J:\Shared drives\Salt All The Things\Marketing\Pull All The Things`)
2. Set sharing to "Anyone with the link can view"
3. Get the file ID from each share link
4. Enter the Google Drive direct URLs: `https://drive.google.com/uc?id={FILE_ID}&export=view`

Create a helper script or admin form to make this easy.

### 7.3 — Visual Polish

Focus on the vote and results pages since these are public-facing:

**Vote page:**
- Image cards should be generous — let the art breathe
- Selection animation (subtle gold glow when picked, number badge animates in)
- Smooth transitions between states (vote form → results)
- Mobile: images stack in a scrollable grid, pick bar is sticky at bottom

**Results page:**
- Winner should feel like a winner (larger card, gold border glow, confetti effect or similar)
- Score bars animate on page load
- Medal badges for top 3 (🥇🥈🥉)
- "X of Y members voted" with visual progress

**Landing page:**
- The group art (Pull_all_the_things_1.png) as a hero image
- Brief guild description
- Link to active campaigns
- Links to existing tools (roster, Mito's Corner, etc.)

### 7.4 — Error Pages

**404.html:** "You've wandered into uncharted territory..." with PATT theme
**500.html:** "Something broke. Blame Mito." with PATT theme

Configure FastAPI exception handlers to render these.

### 7.5 — Security Hardening

- [ ] Verify all admin routes require auth
- [ ] Verify JWT tokens expire correctly
- [ ] Verify invite codes expire and can't be reused
- [ ] Add rate limiting to login endpoint (prevent brute force)
- [ ] Add CSRF protection to form submissions
- [ ] Verify database credentials are never in code (only .env)
- [ ] Verify bot token is never logged or exposed
- [ ] Set secure cookie flags (httpOnly, secure, sameSite)
- [ ] Add Content-Security-Policy headers

### 7.6 — Performance

- [ ] Images load efficiently (proper sizing, lazy loading for below-fold)
- [ ] Static assets have cache headers (via Nginx)
- [ ] Database queries use indexes (campaign_id on votes, member_id on characters)
- [ ] No N+1 queries in roster or results views (use eager loading)
- [ ] Page load under 2 seconds on mobile

### 7.7 — Operations Documentation

Create `docs/OPERATIONS.md` — a guide for Mike:

**How to create a new campaign:**
1. Go to /admin/campaigns/new
2. Fill in the form
3. Add entries with Google Drive image URLs
4. Set the start date/time and duration
5. Click Activate (or let it auto-activate at start_at)

**How to invite a new member:**
1. Add them to the roster at /admin/roster
2. Enter their Discord ID
3. Click "Send Invite"
4. They'll get a DM with a code and link

**How to check on things:**
- Live campaign stats: /admin/campaigns/{id} (or the vote page itself)
- Bot status: check if PATT-Bot is online in Discord
- Logs: `journalctl -u patt -f` on the server
- Test DB connection: `curl https://pullallthething.com/api/health`

**How to restart the platform:**
```bash
ssh root@5.78.114.224
sudo systemctl restart patt
```

**How to deploy updates:**
```bash
./deploy.sh
```

### 7.8 — Final Deployment Verification

- [ ] Fresh deploy to Hetzner
- [ ] Run migrations: `alembic upgrade head`
- [ ] Seed data loads correctly
- [ ] App starts and health check passes
- [ ] SSL working (https://pullallthething.com)
- [ ] Bot connects to Discord and appears online
- [ ] Can log in as admin
- [ ] Can create and activate a test campaign
- [ ] Can vote from a different account
- [ ] Results display correctly
- [ ] Contest agent posts to Discord
- [ ] Legacy URLs work (roster form, etc.)

---

## Acceptance Criteria

- [ ] Full regression test suite passes
- [ ] Art vote campaign is configured and ready to launch
- [ ] Vote and results pages look polished and professional
- [ ] Landing page displays group art and links to active campaigns
- [ ] Error pages are styled
- [ ] Security checklist all green
- [ ] Performance targets met
- [ ] Operations documentation complete
- [ ] Deployment verification all green
- [ ] Mike can operate the platform independently

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-7: polish and art vote ready to launch"`
- [ ] Update CLAUDE.md "Current Build Status" section to:
  ```
  ### Completed Phases
  - Phase 0 through 7: Platform complete

  ### What Exists
  - Full guild platform at pullallthething.com
  - Auth system with Discord invite registration
  - Campaign engine with ranked-choice voting
  - Contest agent posting to Discord
  - Admin tools for campaigns, roster, ranks
  - All guild data migrated from Google Sheets
  - Comprehensive test suite

  ### Ready to Launch
  - Salt All The Things Profile Pic Contest configured
  - Mike activates when ready
  ```

---

## What's Next (Future Phases, Not Part of This Build)

- **Raid management:** Availability scheduling, event creation, Raid-Helper integration
- **Book club:** Leverage campaign engine for book nominations and voting
- **Salt All The Things site:** Convert to use common services
- **Advanced auth:** Discord OAuth as an alternative to invite codes
- **Analytics dashboard:** Vote patterns, participation trends, member activity
- **Mobile app:** If needed, the API is already there
