# Phase 0: Server Infrastructure, Project Scaffolding & Testing Framework

> **Prerequisites:** Read CLAUDE.md and TESTING.md first.
> **Goal:** A working project skeleton with database, testing framework, and deployment pipeline.
> Nothing user-facing yet — just the bones.

---

## What This Phase Produces

1. PostgreSQL installed and configured on Hetzner with `patt_db`, schemas, and roles
2. Nginx config for pullallthething.com (reverse proxy to app)
3. Python project scaffolding with all directories, dependencies, and config
4. SQLAlchemy models and Alembic migrations for the full schema
5. pytest framework with conftest, fixtures, factories, and test database
6. A trivial FastAPI app that starts, connects to the DB, and serves a health check
7. systemd service file for the app
8. All tests passing

---

## Tasks

### 0.1 — PostgreSQL Setup

Create the setup script at `deploy/setup_postgres.sql`:

```sql
-- Run as postgres superuser
CREATE USER patt_user WITH PASSWORD 'CHANGEME';
CREATE DATABASE patt_db OWNER patt_user;

-- Connect to patt_db and create schemas
\c patt_db
CREATE SCHEMA common AUTHORIZATION patt_user;
CREATE SCHEMA patt AUTHORIZATION patt_user;

-- Test database for pytest
CREATE DATABASE patt_test_db OWNER patt_user;
\c patt_test_db
CREATE SCHEMA common AUTHORIZATION patt_user;
CREATE SCHEMA patt AUTHORIZATION patt_user;
```

**Document for Mike:** The exact SSH commands to run this on Hetzner:
```bash
sudo apt install postgresql-16 postgresql-client-16
sudo -u postgres psql < deploy/setup_postgres.sql
```

Mike will set the actual password. Use environment variables, never hardcode credentials.

### 0.2 — Nginx Configuration

Create `deploy/nginx/pullallthething.com.conf`:

```nginx
server {
    listen 80;
    server_name pullallthething.com www.pullallthething.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name pullallthething.com www.pullallthething.com;

    # SSL certs managed by certbot — placeholder paths
    ssl_certificate /etc/letsencrypt/live/pullallthething.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pullallthething.com/privkey.pem;

    # Static files (CSS, JS, images) from the platform
    location /static/ {
        alias /opt/patt-platform/src/patt/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # Legacy HTML files served directly during Phases 0-4
    # These are the existing GitHub Pages files at repo root.
    # After Phase 5, these are removed and served by FastAPI instead.
    location ~ ^/(roster\.html|roster-view\.html|raid-admin\.html|mitos-corner\.html|patt-config\.json)$ {
        root /opt/patt-platform;
        try_files $uri =404;
    }

    # Everything else proxied to FastAPI
    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Note: The legacy file location block is temporary. Phase 5 removes it when the files
move under FastAPI's control. This ensures existing bookmarks and Discord links keep
working throughout the entire build process.

**Document for Mike:** The Nginx setup commands:
```bash
sudo cp deploy/nginx/pullallthething.com.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/pullallthething.com.conf /etc/nginx/sites-enabled/
sudo certbot --nginx -d pullallthething.com -d www.pullallthething.com
sudo nginx -t && sudo systemctl reload nginx
```

And the DNS records Mike needs to update at Bluehost:
```
Type    Name    Value               TTL
A       @       5.78.114.224        300
A       www     5.78.114.224        300
```
(Delete any existing CNAME records for www that point to GitHub Pages.)

### 0.3 — Python Project Scaffolding

**Important:** This repo (`Shadowedvaca/PullAllTheThings-site`) already contains legacy
HTML files at root (index.html, roster.html, raid-admin.html, mitos-corner.html, etc.).
Do NOT delete, move, or modify these files. The new platform structure is added alongside
them. They coexist until Phase 5 migrates them into the platform's serving structure.

Create the new directories and files as documented in CLAUDE.md's "New Platform Structure"
section. The legacy files at root stay untouched.

Update `.gitignore` to include:
```
.env
.venv/
__pycache__/
*.pyc
*.egg-info/
.pytest_cache/
```

Create `requirements.txt`:
```
# Web framework
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
jinja2>=3.1.0
python-multipart>=0.0.6

# Database
sqlalchemy[asyncio]>=2.0.25
asyncpg>=0.29.0
alembic>=1.13.0

# Auth
pyjwt>=2.8.0
bcrypt>=4.1.0

# Discord
discord.py>=2.3.0

# Config
pydantic-settings>=2.1.0
python-dotenv>=1.0.0

# HTTP client (for Google Drive, external APIs)
httpx>=0.26.0

# Testing
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=4.1.0
factory-boy>=3.3.0
```

Create `.env.example` with all environment variables documented in CLAUDE.md.

Create `src/patt/config.py` using Pydantic BaseSettings:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    discord_bot_token: str = ""
    discord_guild_id: str = ""
    app_env: str = "development"
    app_port: int = 8100
    app_host: str = "0.0.0.0"

    class Config:
        env_file = ".env"
```

### 0.4 — SQLAlchemy Models

Create `src/sv_common/db/models.py` with ORM models for every table in CLAUDE.md:
- GuildRank, User, GuildMember, Character, DiscordConfig, InviteCode (common schema)
- Campaign, CampaignEntry, Vote, CampaignResult, ContestAgentLog (patt schema)

All models must:
- Use `__tablename__` with schema prefix (e.g., `__table_args__ = {"schema": "common"}`)
- Include created_at/updated_at with server defaults
- Define relationships (e.g., GuildMember.rank, Campaign.entries, etc.)
- Use type hints for all columns

Create `src/sv_common/db/engine.py`:
- Async engine factory from DATABASE_URL
- Async session factory
- `get_db()` dependency for FastAPI route injection

### 0.5 — Alembic Setup

```bash
alembic init alembic
```

Configure `alembic.ini` and `alembic/env.py` to:
- Read DATABASE_URL from environment
- Import all models so autogenerate works
- Handle the common and patt schemas

Create initial migration:
```bash
alembic revision --autogenerate -m "initial schema"
```

### 0.6 — Testing Framework

Create `tests/conftest.py` with all shared fixtures per TESTING.md:
- `test_engine` (session-scoped, creates/drops tables)
- `db_session` (per-test, with rollback)
- `client` (FastAPI test client via httpx)
- `admin_member`, `veteran_member`, `initiate_member` fixtures
- `mock_discord_bot` fixture

Create `tests/unit/test_smoke.py`:
```python
def test_app_imports():
    """Verify the app module can be imported without errors."""
    from patt.app import create_app
    app = create_app()
    assert app is not None

def test_settings_load():
    """Verify settings can be constructed with defaults."""
    from patt.config import Settings
    settings = Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        jwt_secret_key="test-secret"
    )
    assert settings.app_port == 8100
```

Create `tests/integration/test_health.py`:
```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "db" in data["data"]
```

### 0.7 — Minimal FastAPI App

Create `src/patt/app.py`:
- App factory function `create_app()`
- Mounts static files directory
- Sets up Jinja2 template directory
- Includes a `/api/health` route that checks DB connectivity
- Returns `{"ok": true, "data": {"db": "connected", "version": "0.1.0"}}`

Create `scripts/run_dev.py`:
```python
"""Development server runner. Usage: python scripts/run_dev.py"""
import uvicorn
uvicorn.run("patt.app:create_app", host="127.0.0.1", port=8100, reload=True, factory=True)
```

### 0.8 — Seed Data

Create `data/seed/ranks.json`:
```json
[
    {"name": "Initiate", "level": 1, "description": "New member, proving reliability and social fit"},
    {"name": "Member", "level": 2, "description": "Regular attendee who engages with the guild"},
    {"name": "Veteran", "level": 3, "description": "Key performer, helps others, brings the guild together"},
    {"name": "Officer", "level": 4, "description": "Guild leadership team"},
    {"name": "Guild Leader", "level": 5, "description": "Guild master"}
]
```

Create `src/sv_common/db/seed.py`:
- Loads ranks.json and inserts into guild_ranks table (upsert — don't duplicate on re-run)
- Called during app startup if the ranks table is empty

### 0.9 — systemd Service

Create `deploy/systemd/patt.service`:
```ini
[Unit]
Description=PATT Guild Platform
After=network.target postgresql.service

[Service]
Type=exec
User=www-data
Group=www-data
WorkingDirectory=/opt/patt-platform
EnvironmentFile=/opt/patt-platform/.env
ExecStart=/opt/patt-platform/.venv/bin/uvicorn patt.app:create_app --host 0.0.0.0 --port 8100 --factory
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 0.10 — Deploy Script

Create `deploy.sh`:
```bash
#!/bin/bash
# Deploy PATT platform to Hetzner
# Usage: ./deploy.sh
# Repo: Shadowedvaca/PullAllTheThings-site

set -e

SERVER="root@5.78.114.224"
REMOTE_DIR="/opt/patt-platform"

echo "Syncing files..."
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.env' --exclude='.git' \
    ./ $SERVER:$REMOTE_DIR/

echo "Installing dependencies..."
ssh $SERVER "cd $REMOTE_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

echo "Running migrations..."
ssh $SERVER "cd $REMOTE_DIR && .venv/bin/alembic upgrade head"

echo "Restarting service..."
ssh $SERVER "sudo systemctl restart patt"

echo "Done!"
```

Note: The repo is cloned/synced to `/opt/patt-platform` on the server, regardless of
the repo name. The legacy HTML files come along for the ride — they'll be served by
FastAPI once the platform is running (Phase 5 handles the serving setup).

---

## Acceptance Criteria

- [ ] PostgreSQL is running on Hetzner with patt_db, patt_test_db, common and patt schemas
- [ ] `alembic upgrade head` creates all tables successfully
- [ ] Seed data loads ranks into guild_ranks table
- [ ] FastAPI app starts and `/api/health` returns `{"ok": true, ...}`
- [ ] Nginx proxies pullallthething.com to the app (after DNS switch)
- [ ] `pytest tests/ -v` passes all smoke and health tests
- [ ] Test database creates/destroys cleanly per session

---

## End of Phase Checklist

- [ ] All acceptance criteria met
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Commit: `git commit -m "phase-0: infrastructure, scaffolding, testing framework"`
- [ ] Update CLAUDE.md "Current Build Status" section:
  ```
  ### Completed Phases
  - Phase 0: Server infrastructure, project scaffolding, testing framework

  ### Current Phase
  - Phase 1: Common Services — Identity & Guild Data Model

  ### What Exists on the Server
  - PostgreSQL 16 running with patt_db
  - Nginx configured for pullallthething.com (pending DNS)
  - FastAPI app running via systemd on port 8100
  - Health check endpoint working
  - Test framework operational
  ```

---

## DNS Note for Mike

When ready to switch pullallthething.com from GitHub Pages to Hetzner:

**At Bluehost DNS management:**
1. Delete any CNAME record for `www` pointing to GitHub
2. Delete any A records pointing to GitHub IPs (185.199.108-111.153)
3. Add: `A @ 5.78.114.224` (TTL 300)
4. Add: `A www 5.78.114.224` (TTL 300)

Then on the server:
```bash
sudo certbot --nginx -d pullallthething.com -d www.pullallthething.com
```

This can happen any time after Phase 0 is deployed. The app will be ready.
