# Directory Structure

> Full annotated tree for the `Shadowedvaca/PullAllTheThings-site` repo.

```
PullAllTheThings-site/          (repo root)
├── CLAUDE.md                          ← Master context (read first)
├── TESTING.md                         ← Testing strategy and conventions
├── INDEX.md                           ← Context files quick reference
├── requirements.txt                   ← Python dependencies
├── alembic.ini                        ← Database migration config
├── .env.example                       ← Template for environment variables
│
├── alembic/                           ← Migration scripts
│   └── versions/
│
├── src/
│   ├── sv_common/                     ← Shared services package
│   │   ├── auth/
│   │   │   ├── jwt.py
│   │   │   ├── passwords.py
│   │   │   └── invite_codes.py
│   │   ├── discord/
│   │   │   ├── bot.py
│   │   │   ├── role_sync.py
│   │   │   ├── dm.py
│   │   │   └── channels.py
│   │   ├── identity/
│   │   │   ├── members.py
│   │   │   ├── ranks.py
│   │   │   └── characters.py
│   │   ├── notify/
│   │   │   └── dispatch.py
│   │   ├── db/
│   │   │   ├── engine.py
│   │   │   ├── models.py
│   │   │   └── seed.py
│   │   ├── config_cache.py            ← In-process cache for site_config
│   │   ├── crypto.py                  ← Fernet encryption helpers
│   │   └── guild_sync/
│   │       ├── blizzard_client.py
│   │       ├── bnet_character_sync.py ← Battle.net OAuth character auto-claim
│   │       ├── crafting_sync.py
│   │       ├── crafting_service.py
│   │       ├── discord_sync.py
│   │       ├── addon_processor.py
│   │       ├── identity_engine.py
│   │       ├── integrity_checker.py
│   │       ├── progression_sync.py    ← Raid/M+/achievements/Raider.IO sync
│   │       ├── raiderio_client.py
│   │       ├── warcraftlogs_client.py
│   │       ├── wcl_sync.py
│   │       ├── reporter.py
│   │       ├── scheduler.py
│   │       ├── db_sync.py
│   │       ├── sync_logger.py
│   │       ├── drift_scanner.py
│   │       ├── raid_booking_service.py
│   │       ├── api/
│   │       │   ├── routes.py
│   │       │   └── crafting_routes.py
│   │       ├── matching_rules/        ← Registry now returns [] (rules retired)
│   │       └── onboarding/
│   │           ├── conversation.py
│   │           ├── provisioner.py
│   │           ├── deadline_checker.py
│   │           └── commands.py
│   │
│   └── guild_portal/                  ← Guild platform application package
│       ├── app.py                     ← FastAPI app factory (create_app)
│       ├── config.py                  ← Pydantic settings
│       ├── deps.py                    ← Auth deps (get_page_player, require_page_rank)
│       ├── api/
│       │   ├── auth_routes.py
│       │   ├── bnet_auth_routes.py    ← Battle.net OAuth endpoints
│       │   ├── campaign_routes.py
│       │   ├── vote_routes.py
│       │   ├── admin_routes.py
│       │   ├── guild_routes.py
│       │   └── setup_routes.py        ← First-run wizard API (404 after setup)
│       ├── pages/
│       │   ├── auth_pages.py
│       │   ├── vote_pages.py
│       │   ├── admin_pages.py
│       │   ├── public_pages.py
│       │   ├── profile_pages.py
│       │   └── setup_pages.py
│       ├── templates/
│       │   ├── base.html              ← Public page base
│       │   ├── base_admin.html        ← Admin page base (extend this, not base.html)
│       │   ├── admin/
│       │   ├── vote/
│       │   ├── public/
│       │   │   └── crafting_corner.html
│       │   └── setup/
│       ├── static/
│       │   ├── css/
│       │   │   ├── main.css           ← Global styles + CSS custom properties
│       │   │   └── setup.css
│       │   ├── js/
│       │   │   ├── players.js         ← Player Manager drag-and-drop
│       │   │   └── setup.js
│       │   └── legacy/               ← Old GitHub Pages HTML files (served at original URLs)
│       ├── services/
│       │   ├── campaign_service.py
│       │   ├── vote_service.py
│       │   └── contest_agent.py
│       └── bot/
│           ├── contest_cog.py
│           └── guild_quote_commands.py
│
├── wow_addon/
│   └── GuildSync/
│       ├── GuildSync.toc
│       ├── GuildSync.lua
│       └── README.md
│
├── companion_app/
│   ├── guild_sync_watcher.py
│   ├── requirements.txt
│   └── README.md
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── regression/                   ← Requires live DB (TEST_DATABASE_URL)
│
├── deploy/
│   ├── nginx/
│   ├── systemd/                      ← Legacy (app now runs in Docker)
│   └── setup_postgres.sql
│
├── data/
│   ├── contest_agent_personality.md
│   └── reference/
│
├── seed/
│   └── ranks.json
│
├── scripts/
│   ├── setup_art_vote.py
│   └── run_dev.py
│
├── docs/
│   ├── DISCORD-BOT-SETUP.md
│   ├── OPERATIONS.md                 ← Day-to-day ops guide for Mike
│   └── SERVER-IP-MIGRATION.md        ← /etc/hosts + migration checklist
│
├── reference/                        ← Phase plans and context docs
│   ├── INDEX.md
│   ├── SCHEMA.md                     ← Full DDL for all tables
│   ├── PHASE_HISTORY.md              ← Completed phases + recent changes
│   ├── DESIGN.md                     ← Color palette, typography, layout
│   ├── DIRECTORY.md                  ← YOU ARE HERE
│   ├── DEPLOY.md                     ← CI/CD, Docker environments, local dev
│   └── archive/                      ← Old phase plan docs
│
└── memory/
    └── MEMORY.md
```

---

## Notes

### Legacy Files
Root-level HTML files (`index.html`, `roster.html`, etc.) are legacy GitHub Pages files.
They are served by FastAPI from `src/guild_portal/static/legacy/` at their original URLs.

### Google Drive Images
Campaign entry images are stored in Google Drive and referenced by direct URL:
```
https://drive.google.com/uc?id={FILE_ID}&export=view
```
Images for the art vote live at: `J:\Shared drives\Salt All The Things\Marketing\Pull All The Things`
