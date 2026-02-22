# PATT Platform — Session Memory

## Project State
- Phase 0 complete
- Phase 1 complete as of 2026-02-21: identity services, admin/guild API, full test suite
- Phase 2 is next: Authentication & Discord Bot

## Key File Locations
- Phase plans: `reference/PHASE-N.md`
- Testing guide: `reference/TESTING.md`
- App entry: `src/patt/app.py` (factory: `create_app()`)
- Config: `src/patt/config.py` (pydantic-settings, reads .env)
- DB dependency: `src/patt/deps.py` — `get_db()` for FastAPI routes
- Models: `src/sv_common/db/models.py` (all ORM models)
- Engine/session: `src/sv_common/db/engine.py`
- Seed data: `src/sv_common/db/seed.py` + `data/seed/ranks.json`
- Identity services: `src/sv_common/identity/ranks.py`, `members.py`, `characters.py`
- Admin API: `src/patt/api/admin_routes.py`
- Guild API: `src/patt/api/guild_routes.py`
- Health route: `src/patt/api/health.py`
- Alembic migration: `alembic/versions/0001_initial_schema.py`
- Tests: `tests/unit/` (smoke + ranks/members/characters), `tests/integration/test_admin_api.py`
- Conftest: `tests/conftest.py`

## Dev Commands
- Run unit tests: `.venv/Scripts/pytest tests/unit/ -v`
- Run all tests: `.venv/Scripts/pytest tests/ -v`
- Dev server: `python scripts/run_dev.py` (needs .env)

## Architecture Notes
- Python 3.13 on Windows dev, Linux prod (Hetzner 5.78.114.224)
- pytest.ini sets `pythonpath = src` — no editable installs needed
- asyncio_mode = auto in pytest.ini
- Schemas: `common` (identity/auth) and `patt` (campaigns/votes)
- Alembic version table lives in `patt` schema
- Integration tests need TEST_DATABASE_URL env var set to patt_test_db

## Patterns Established
- API responses: `{"ok": true/false, "data": {...}}` or `{"ok": false, "error": "..."}`
- Settings singleton via `get_settings()` in config.py
- Engine singleton via `get_engine()` / `get_session_factory()` in engine.py
- Model schema assignment: `__table_args__ = {"schema": "common"}` or `"patt"`
