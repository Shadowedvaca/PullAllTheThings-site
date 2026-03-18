# Phase X — SaaS Platform & Marketplace

> **Status:** DRAFT / Pre-planning
> **Priority:** TBD — not yet scheduled
> **Last updated:** 2026-03-17

This document captures the rough vision for evolving the PATT guild platform into a multi-tenant SaaS product. Nothing here is finalized — it exists so we can think it through before committing to implementation.

---

## The Vision

The platform we've built for PATT is generic enough to power any WoW guild. The SaaS angle is: **let guild leaders self-serve their own instance** rather than requiring a manual setup by Mike. There are two layers to this:

1. **Guild-tier product** — a full guild platform (roster, raids, crafting, discord bot, etc.) for a WoW guild leader to deploy and manage
2. **Player-tier product** — a personal dashboard (My Characters equivalent) for any individual WoW player, not tied to a specific guild

These could be priced and offered separately, or bundled together.

---

## Sub-Phases (rough breakdown)

### Phase X.1 — Public Player Dashboard (Individual Tier)

The `My Characters` page already exists as a member-only feature inside a guild instance. The idea here is to offer a **standalone player dashboard** — not tied to any guild — where any WoW player can:

- Link their Battle.net account via OAuth
- See their characters (all realms, not just one guild's realm)
- View item levels, progression, M+ scores, WCL parses
- Access the Market panel (AH prices relevant to their realm)

This is essentially `My Characters` extracted from the guild context and served as a standalone micro-app.

**Key questions to resolve:**
- Does this live on a subdomain of `pullallthethings.com`, a separate domain (e.g. `wowdash.gg`), or is it generated per-user?
- Authentication is Battle.net OAuth only — no guild invite code flow
- No Discord dependency for this tier
- Data access: Blizzard public API only (no guild-private data)
- Do player accounts persist (stored in our DB) or is it session-only?

**What already exists that can be reused:**
- `sv_common.guild_sync.bnet_character_sync` — character fetch via OAuth
- `sv_common.guild_sync.raiderio_client` — M+ scores
- `sv_common.guild_sync.warcraftlogs_client` — parse data
- `sv_common.guild_sync.ah_pricing` — AH prices
- `member_routes.py` panel API endpoints — extractable with auth layer swap
- `my_characters.html` + CSS/JS — reusable with minor changes

**ToS note:** Battle.net OAuth must remain manual — the player clicks "Connect Battle.net" themselves. No scripting the OAuth flow.

---

### Phase X.2 — Purchase & Provisioning Flow

For the guild-tier product, a guild leader should be able to:

1. Land on a marketing/product page explaining what they get
2. Choose a tier (see Phase X.4)
3. Complete purchase (Stripe or similar)
4. Receive onboarding instructions / access to their instance

**Setup options (ToS-safe):**

The auto-scripting path for Discord and Blizzard API setup is **off the table** — both platforms prohibit automated account/bot creation on behalf of users. Instead:

- **Self-serve tutorial path** — buyer gets a step-by-step guide: create their own Discord bot, register their own Blizzard API app, paste credentials into the setup wizard. The PATT setup wizard (Phase 4.1) already handles this flow. Cost: free / standard tier.
- **White-glove setup path** — Mike walks through setup with the buyer over Discord/voice. They still create their own credentials (ToS requirement), but Mike guides and configures everything else. Cost: one-time surcharge.

**Provisioning (what gets auto-created):**
- New PostgreSQL database (or schema namespace) for the tenant
- New Docker container spun up with that DB connection string
- Subdomain provisioned (e.g. `guildname.pullallthethings.com`) + SSL cert
- Setup wizard unlocked for their instance
- Welcome email / DM with their URL and first-login credentials

**What needs to be built:**
- Provisioning script / API (likely a separate admin-only management service)
- Stripe webhook handler to trigger provisioning on payment confirmation
- DNS + Nginx automation for per-tenant subdomains
- Tenant registry (track which instances exist, their status, billing state)

---

### Phase X.3 — Marketing / Product Landing Page

Currently `pullallthethings.com` is the PATT guild's own site. The product needs its own presence.

Options:
- A `/product` or `/platform` section on `pullallthethings.com` (simple, no new domain)
- A separate domain entirely (e.g. `guildportal.gg` or similar) — better for selling to other guilds who won't want "pull all the things" branding
- The PATT site becomes a live demo of the product

The landing page should show:
- What the platform does (roster, raids, crafting, discord bot, player dashboard)
- Tier comparison (see Phase X.4)
- Setup path options (self-serve vs white-glove)
- Live demo link (PATT itself, or a sandboxed demo instance)
- Purchase / sign-up CTA

---

### Phase X.4 — Tier Design (Simple vs Customizable)

Rough sketch of how tiers might work:

| Feature | **Free / Demo** | **Standard Guild** | **Premium Guild** |
|---|---|---|---|
| Player dashboard (My Characters) | ✓ | ✓ | ✓ |
| Guild roster + ranks | — | ✓ | ✓ |
| Discord bot (basic: role sync, DMs) | — | ✓ | ✓ |
| Raid tools + attendance | — | ✓ | ✓ |
| Crafting Corner | — | ✓ | ✓ |
| AH Market panel | ✓ | ✓ | ✓ |
| Voting campaigns / contests | — | — | ✓ |
| Custom branding (accent color, logo) | — | — | ✓ |
| Custom domain | — | — | ✓ |
| White-glove setup | — | add-on | included |
| Data export (portability) | ✓ | ✓ | ✓ |
| Multi-guild support | — | — | future |

**Key principle:** Every tier includes full data export. Non-negotiable part of the ethical framework (see Phase X.5).

Pricing model TBD — monthly subscription is the obvious choice. Annual discount optional.

---

### Phase X.5 — Data Portability & Export

**Every guild leader must be able to take their data and leave.** This is a core ethical commitment, not a feature.

What "their data" means:
- Guild roster (players, characters, ranks, Discord IDs)
- Raid history (events, attendance records, seasons)
- Crafting data (recipes, orders, sync history)
- Campaign/contest history and results
- Character progression snapshots (raid progress, M+ scores, parses — note: this data originally came from Blizzard/Raider.IO/WCL and may not be "owned" by us, but the aggregated snapshots are)
- Guild quotes, availability preferences
- Site configuration (guild name, tagline, rank structure, etc.)

**Export format:**
- JSON (structured, schema-documented) — machine-readable, importable
- CSV for tabular data (roster, attendance) — human-readable, Excel-friendly
- A full database dump option for technical guild leaders

**What portability enables:**
- Guild can move to a self-hosted instance of this codebase (it's their data)
- Guild can move to a different provider or roll their own solution
- Prevents lock-in — builds trust

**What needs to be built:**
- Export API endpoint(s) with scoped data selection
- Export format documentation
- Admin UI: "Export Guild Data" button with format/scope options
- Import flow (for guild leaders bringing data *in* from another source — stretch goal)

---

## Open Questions

1. **Multi-tenancy architecture** — isolated DB per tenant (safest, current model) vs. shared DB with tenant_id foreign keys (more complex, cheaper at scale). Current model is easier to start with.
2. **Separate product domain?** — Selling to other guilds on `pullallthethings.com` feels odd. Worth thinking about a neutral product brand.
3. **Player dashboard standalone vs bundled** — does the player dashboard only exist as part of a guild subscription, or can players subscribe independently?
4. **Bot token ownership** — each guild must register their own Discord bot application. This is non-negotiable for ToS compliance, but adds friction. The setup wizard already guides this. Good tutorial/video will help.
5. **Blizzard API rate limits** — each tenant needs their own Blizzard API app credentials, or we need a proxy/pool strategy. Self-serve credentials is the ToS-safe path.
6. **Stripe / payment provider** — Stripe is the obvious choice. Need to decide: does Mike handle this as a personal business, or does it need a separate entity?
7. **Support model** — what does a paying guild leader get for support? Discord server? Ticket system? Email?
8. **Demo instance** — PATT itself can serve as the live demo. Or a sandboxed demo with fake data might be better (PATT has real member data).

---

## Dependencies / Prerequisites

Before Phase X can begin, these should be stable:
- **Phase F.3** (feedback form on PATT site) — validates the feedback → improvement loop before opening to external guilds
- **Phase X is greenfield** relative to current PATT features — it's infrastructure and business logic, not guild feature work
- A decision on product domain/brand
- Stripe account and pricing finalized

---

## Related Files

- `reference/PHASE_F3_PATT_FORM.md` — feedback form (prerequisite)
- `reference/PHASE_FEEDBACK_ROADMAP.md` — feedback system roadmap
- `src/guild_portal/pages/setup_pages.py` — setup wizard (reusable for new instances)
- `src/guild_portal/api/member_routes.py` — My Characters API (extractable for player tier)
- `src/guild_portal/templates/member/my_characters.html` — player dashboard template
