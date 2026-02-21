# PATT Platform — Session Memory

## Project State
- Phase 0 complete as of 2026-02-21
- Phase 1 is next: Common Services — Identity & Guild Data Model

## Key File Locations
- Phase plans: `reference/PHASE-N.md`
- Testing guide: `reference/TESTING.md`
- App entry: `src/patt/app.py` (factory: `create_app()`)
- Config: `src/patt/config.py` (pydantic-settings, reads .env)
- Models: `src/sv_common/db/models.py` (all ORM models)
- Engine/session: `src/sv_common/db/engine.py`
- Seed data: `src/sv_common/db/seed.py` + `data/seed/ranks.json`
- Health route: `src/patt/api/health.py`
- Alembic migration: `alembic/versions/0001_initial_schema.py`
- Tests: `tests/unit/test_smoke.py`, `tests/integration/test_health.py`
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
