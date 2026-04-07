# Phase Z — Multi-Region & Multi-Language Expansion Notes

> **Status:** Future consideration — NOT in scope for Phase X launch.
> **Last updated:** 2026-04-05
> **Context:** Platform launches US/English only. This doc captures everything we need to think about before expanding. Read this before starting any multi-region work.

---

## The Short Version

WoW is a global game. Blizzard operates three regional clusters (US, EU, Asia) as largely independent systems with their own API endpoints, OAuth flows, realms, and guilds. A player can have characters across all three regions on a single Battle.net account. Guilds are always region-specific — there is no cross-region guild.

This creates real complexity at every layer: auth, character sync, guild discovery, scheduler, and infrastructure.

---

## 1. Can Players Have Characters Across Multiple Regions?

**Yes.** A single Battle.net account can have characters in US, EU, KR, and TW simultaneously. These are separate game clients but the same account.

Implications:
- `system.bnet_accounts` as currently designed is one-per-user. In a multi-region world, a player needs OAuth tokens **per region** — each region has its own OAuth endpoint and issues its own access token.
- The Blizzard API character endpoint is region-specific: `us.api.blizzard.com/profile/...` vs `eu.api.blizzard.com/profile/...`
- `player.wow_characters` already has a `region` column — this is already the right model. Realm slugs alone are not globally unique; region+realm_slug is the correct unique key.
- To fully sync a player's characters, we'd need to run the BNet sync against each region they've authorized separately.

**What this means for the OAuth flow:**
Currently we register one Blizzard API application with one set of redirect URIs. In multi-region, options are:
- (A) One API app registered with redirect URIs for all regions — Blizzard allows this; each region's OAuth endpoint issues tokens that work against that region's API.
- (B) Separate API app registrations per region — more isolation but more management overhead.

Option A is simpler. Blizzard's OAuth endpoints by region:
- US: `oauth.battle.net`
- EU: `eu.battle.net/oauth`
- KR: `kr.battle.net/oauth`
- TW: `tw.battle.net/oauth`

The player would need to complete the OAuth flow once per region they want to sync. This is a UX decision — do we prompt for all regions upfront, or let them add regions later from their profile?

---

## 2. Guilds Are Always Region-Specific

A WoW guild exists in exactly one region. US guilds cannot have EU characters as members. This simplifies the guild tenant model — every `system.tenants` row has a `region` column and it never changes.

However, a player might be a member of a US guild on one character and an EU guild on another. The platform already supports this (guild memberships are per-character, many-to-many). Region just becomes a filter/display attribute.

---

## 3. Blizzard API Changes Required

| Current (US only) | Multi-region |
|---|---|
| One set of platform credentials | Same credentials, but OAuth flow is region-parameterized |
| `us.api.blizzard.com` hardcoded | API base URL becomes a function of `region` |
| Single connected realm ID per tenant | Region+realm_slug as composite key everywhere |
| Scheduler runs one Blizzard sync | Scheduler runs per-region sync for each region a tenant/player has characters in |
| AH pricing: one `connected_realm_id` per query | AH pricing: `region` + `connected_realm_id` (already partially handled) |

**Rate limits:** Blizzard rate limits are per-API-key per region. At multi-region scale, each region's limits are independent. This is actually favorable — EU sync doesn't consume US rate limit budget.

---

## 4. Realm Slug Uniqueness

Realm slugs are not globally unique. `draenor` exists in both US and EU. The correct unique identifier is `region + realm_slug`.

`platform.realms` needs `region` as part of its primary key. This is a schema migration when multi-region support is added. Ensure any foreign key or display logic that joins on realm_slug alone is updated.

`platform.connected_realms` — connected realm IDs are also region-specific. A connected realm ID in US is unrelated to the same integer in EU.

---

## 5. Infrastructure & Server Location

The platform launches on Hetzner Hillsboro OR (US West). This is fine for US players. EU players connecting to a US-hosted platform will experience:
- ~100–150ms additional latency on API calls
- OAuth redirects that cross the Atlantic
- No GDPR data residency compliance (EU player data stored in US)

**For a meaningful EU expansion, a dedicated EU deployment is needed.** This is not just a nice-to-have — GDPR requires that EU resident data can be stored in the EU if requested. Options:
- Separate EU deployment (separate DB, separate app, separate bot token) — highest isolation, highest operational cost
- EU database replica with US app — doesn't solve GDPR data residency
- Hetzner Falkenstein (EU) already exists as dev/test server — a prod EU tier there would be straightforward

The multi-region scheduler needs to know which server is authoritative for which region's data. This is a non-trivial coordination problem if the same guild has members in US and EU regions.

---

## 6. GDPR & Data Privacy (EU)

Any EU deployment must address:
- **Data residency:** EU player data stored on EU servers
- **Right to erasure:** player can request full data deletion (not just deactivation)
- **Data portability:** player can export all their data (already planned in the export system)
- **Consent:** explicit consent for data collection, especially for non-obvious data (parse history, guild activity)
- **Privacy policy & terms of service** must be GDPR-compliant for EU users

This is legal/compliance work, not just engineering. Get legal review before launching in the EU.

KR/TW regions likely have their own data privacy requirements (Korea's PIPA, Taiwan's PDPA). Research required before expanding to Asia.

---

## 7. Multi-Language Support

### Launch posture (US/English only, i18n stubbed)

The codebase ships with i18n infrastructure in place but only EN-US strings. This means:
- All user-facing strings in Jinja2 templates wrapped in `_("string")` translation calls via Babel/gettext
- No hardcoded locale-specific formatting (dates, numbers, currencies go through locale-aware formatting functions)
- All datetimes stored as UTC internally; displayed in user's configured timezone
- The translation catalog exists (EN-US only) — adding a new language is adding a catalog file, not touching code

### WoW-specific localization

Blizzard provides localized versions of game data (class names, spec names, zone names, item names, achievement names) via their API. The `locale` parameter on Blizzard API requests returns data in the requested language:
- `en_US`, `en_GB`, `de_DE`, `es_ES`, `es_MX`, `fr_FR`, `it_IT`, `pt_BR`, `ru_RU`, `ko_KR`, `zh_TW`, `zh_CN`

`platform.*` tables (classes, specs, zones, items, etc.) currently store one name per row. Multi-language support means storing localized names — either as JSONB `{"en_US": "Druid", "de_DE": "Druide"}` or a separate `platform.localizations` table. The JSONB approach is simpler. The separate table is more queryable.

### What doesn't translate cleanly

- **Guild names** — user-defined, already in whatever language the GL chose
- **Character names** — same
- **Realm names** — Blizzard provides localized realm names via API
- **WoW jargon** (BIS, M+, WCL, ilvl) — these are community terms, often kept in English even in non-English communities. Don't force-translate them.

### Priority languages for expansion (rough order)

1. **German (de_DE)** — large WoW playerbase, EU-focused, well-supported by Blizzard
2. **French (fr_FR)** — same rationale
3. **Spanish (es_ES / es_MX)** — large playerbase across EU and Americas
4. **Portuguese (pt_BR)** — significant Brazilian playerbase
5. **Russian (ru_RU)** — historically large WoW community (note: Blizzard exited Russia in 2022; still relevant for non-Russian CIS players)
6. **Korean / Chinese** — large markets but require right-to-left/CJK rendering support and separate legal compliance

---

## 8. Things to Decide Before Starting Multi-Region Work

| Decision | Notes |
|---|---|
| One platform deployment or region-isolated deployments? | Isolated is simpler; shared is more efficient |
| How does a player authorize multiple regions? | One OAuth flow prompt or progressive discovery |
| Which region is the platform "home"? | Affects where system.* lives |
| How are cross-region players displayed in a US guild? | They have US characters in the guild, EU chars are separate |
| GDPR compliance approach | Legal review required before EU launch |
| Translation vendor / community translation? | Community (Crowdin) is cost-effective for WoW platform |
| WoW name localization storage: JSONB or lookup table? | Decide before platform.* is populated at scale |

---

## Related Files

- `reference/PHASE_X_LAUNCH_PLAN.md` — main launch plan (US/English only)
- `src/sv_common/guild_sync/bnet_character_sync.py` — will need region parameter
- `src/sv_common/guild_sync/scheduler.py` — will need per-region job contexts
- `alembic/` — realm slug unique key will need migration when multi-region ships
