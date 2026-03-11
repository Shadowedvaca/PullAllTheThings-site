# Phase 4.2 — Docker Packaging & Environments

## Goal

Package Guild Portal as a Docker image that any guild can deploy with `docker compose up`.
Set up dev, test, and prod environments for PATT on subdomains with isolated databases.
Use Caddy as the reverse proxy with automatic SSL.

---

## Prerequisites

- Phase 4.0 complete (config extraction — templates work without hardcoded values)
- Hetzner server access (5.78.114.224)
- DNS control for pullallthethings.com

---

## Task 1: Dockerfile

### File: `Dockerfile` (repo root)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps for asyncpg (needs libpq) and bcrypt
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/
COPY alembic/ alembic/
COPY alembic.ini .

# Run migrations then start app
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8100

ENTRYPOINT ["./docker-entrypoint.sh"]
```

### File: `docker-entrypoint.sh` (repo root)

```bash
#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting Guild Portal..."
exec uvicorn patt.app:create_app \
    --host 0.0.0.0 \
    --port 8100 \
    --factory \
    --workers 1
```

**Note:** Single worker because the Discord bot runs as an in-process background task.
Multiple workers would spawn multiple bot connections (bad). For scaling, put a load
balancer in front and run the bot in only one worker — but that's a future concern.

---

## Task 2: Docker Compose (Generic — For Other Guilds)

### File: `docker-compose.yml` (repo root)

```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: guild_portal
      POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}
      POSTGRES_DB: guild_portal_db
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./deploy/setup_postgres.sql:/docker-entrypoint-initdb.d/01-setup.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U guild_portal"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build: .
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://guild_portal:${DB_PASSWORD:-changeme}@db:5432/guild_portal_db
      JWT_SECRET_KEY: ${JWT_SECRET_KEY:?JWT_SECRET_KEY is required}
      APP_ENV: production
      APP_PORT: "8100"
      APP_HOST: "0.0.0.0"
    env_file:
      - .env
    ports:
      - "8100:8100"

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - app

volumes:
  pgdata:
  caddy_data:
  caddy_config:
```

### File: `Caddyfile` (repo root)

```caddyfile
{$DOMAIN:localhost} {
    reverse_proxy app:8100
}
```

When `DOMAIN` env var is set (e.g., `DOMAIN=myguild.example.com`), Caddy auto-provisions
a Let's Encrypt cert. When unset, serves on localhost (dev mode).

### File: `.env.template` (repo root)

Annotated template for guild leaders:

```bash
# === REQUIRED ===

# Domain name for your guild portal (Caddy will auto-provision SSL)
DOMAIN=myguild.example.com

# Database password (pick something strong)
DB_PASSWORD=change-this-to-a-strong-password

# JWT secret (generate with: openssl rand -hex 32)
JWT_SECRET_KEY=generate-a-strong-random-key-min-32-chars

# === CONFIGURED VIA SETUP WIZARD ===
# The following are set during the setup wizard and stored in the database.
# You do NOT need to set them here unless you want env-var overrides.
#
# DISCORD_BOT_TOKEN=set-via-wizard
# DISCORD_GUILD_ID=set-via-wizard
# BLIZZARD_CLIENT_ID=set-via-wizard
# BLIZZARD_CLIENT_SECRET=set-via-wizard

# === OPTIONAL ===

# App environment (production/development)
APP_ENV=production

# Companion app API key (for PATTSync addon uploads)
# PATT_API_KEY=generate-if-using-addon
```

---

## Task 3: PATT Environment Setup

### DNS Records

Add A records for the Hetzner server:

```
dev.pullallthethings.com   A  5.78.114.224
test.pullallthethings.com  A  5.78.114.224
pullallthethings.com       A  5.78.114.224  (already exists)
```

### File: `docker-compose.patt.yml` (repo root)

PATT-specific compose file that runs all 3 environments on one server:

```yaml
services:
  # --- DATABASES ---
  db-prod:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: patt_user
      POSTGRES_PASSWORD: ${PROD_DB_PASSWORD}
      POSTGRES_DB: patt_db
    volumes:
      - patt_pgdata_prod:/var/lib/postgresql/data
      - ./deploy/setup_postgres.sql:/docker-entrypoint-initdb.d/01-setup.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U patt_user"]
      interval: 5s
      timeout: 3s
      retries: 5

  db-test:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: patt_user
      POSTGRES_PASSWORD: ${TEST_DB_PASSWORD}
      POSTGRES_DB: patt_db_test
    volumes:
      - patt_pgdata_test:/var/lib/postgresql/data
      - ./deploy/setup_postgres.sql:/docker-entrypoint-initdb.d/01-setup.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U patt_user"]
      interval: 5s
      timeout: 3s
      retries: 5

  db-dev:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: patt_user
      POSTGRES_PASSWORD: ${DEV_DB_PASSWORD}
      POSTGRES_DB: patt_db_dev
    volumes:
      - patt_pgdata_dev:/var/lib/postgresql/data
      - ./deploy/setup_postgres.sql:/docker-entrypoint-initdb.d/01-setup.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U patt_user"]
      interval: 5s
      timeout: 3s
      retries: 5

  # --- APPS ---
  app-prod:
    build: .
    restart: unless-stopped
    depends_on:
      db-prod:
        condition: service_healthy
    env_file:
      - .env.prod

  app-test:
    build: .
    restart: unless-stopped
    depends_on:
      db-test:
        condition: service_healthy
    env_file:
      - .env.test

  app-dev:
    build: .
    restart: unless-stopped
    depends_on:
      db-dev:
        condition: service_healthy
    env_file:
      - .env.dev

  # --- REVERSE PROXY ---
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile.patt:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - app-prod
      - app-test
      - app-dev

volumes:
  patt_pgdata_prod:
  patt_pgdata_test:
  patt_pgdata_dev:
  caddy_data:
  caddy_config:
```

### File: `Caddyfile.patt`

```caddyfile
pullallthethings.com {
    reverse_proxy app-prod:8100
}

test.pullallthethings.com {
    reverse_proxy app-test:8100
    basicauth {
        # Protect test env from public access
        {$TEST_AUTH_HASH}
    }
}

dev.pullallthethings.com {
    reverse_proxy app-dev:8100
    basicauth {
        {$DEV_AUTH_HASH}
    }
}
```

Dev and test are behind basic auth to prevent public access. Generate hash with
`caddy hash-password`.

### Environment Files

Create three env files on the server:

- `.env.prod` — production Discord bot token, production Blizzard creds, `DATABASE_URL` pointing to `db-prod`
- `.env.test` — test Discord bot token (from test server), test Blizzard creds, `DATABASE_URL` pointing to `db-test`
- `.env.dev` — dev Discord bot token (same test server or separate), `DATABASE_URL` pointing to `db-dev`

**Important:** Dev and test should use a **separate Discord server** (the test server Mike
creates) with its own bot application. This prevents dev/test bot instances from interfering
with the production Discord server.

---

## Task 4: Production Migration Strategy

The current production runs via systemd directly on the host. Migration to Docker:

1. **Backup prod database:** `pg_dump patt_db > patt_db_backup.sql`
2. **Stop systemd service:** `systemctl stop patt`
3. **Start Docker prod:** The new `db-prod` container uses a fresh volume.
   Restore the backup: `docker exec -i <db-container> psql -U patt_user patt_db < patt_db_backup.sql`
4. **Verify:** Hit pullallthethings.com, confirm everything works
5. **Disable old systemd unit:** `systemctl disable patt`
6. **Clean up:** Remove old `/opt/patt-platform` systemd setup (or keep as rollback)

### Rollback Plan

If Docker has issues, revert:
1. `docker compose -f docker-compose.patt.yml down`
2. `systemctl start patt`
3. Prod is back on systemd within 30 seconds

---

## Task 5: GitHub Actions Update

### File: `.github/workflows/deploy.yml`

Update the deploy workflow for Docker-based deployment:

```yaml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to production
        uses: appleboy/ssh-action@v1
        with:
          host: 5.78.114.224
          username: root
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            cd /opt/guild-portal
            git pull origin main
            docker compose -f docker-compose.patt.yml build app-prod
            docker compose -f docker-compose.patt.yml up -d app-prod
            sleep 5
            curl -sf https://pullallthethings.com/ > /dev/null || echo "Health check failed!"
```

**Note:** Only rebuilds and restarts `app-prod`. Dev and test are manually deployed or
have their own triggers.

### Optional: Deploy to Test on PR Merge

Add a second workflow that deploys to test on pushes to a `test` branch, or manually
via `workflow_dispatch`.

---

## Task 6: Update setup_postgres.sql

### File: `deploy/setup_postgres.sql`

Update to be more generic and work with Docker's entrypoint:

```sql
-- Schemas (run on the database specified by POSTGRES_DB)
CREATE SCHEMA IF NOT EXISTS common;
CREATE SCHEMA IF NOT EXISTS guild_identity;
CREATE SCHEMA IF NOT EXISTS patt;

-- Grant permissions to the user specified by POSTGRES_USER
DO $$
BEGIN
    EXECUTE format('GRANT ALL ON SCHEMA common TO %I', current_user);
    EXECUTE format('GRANT ALL ON SCHEMA guild_identity TO %I', current_user);
    EXECUTE format('GRANT ALL ON SCHEMA patt TO %I', current_user);
END
$$;
```

Remove hardcoded `patt_user` / `patt_db` — Docker Compose handles user/database creation
via `POSTGRES_USER` / `POSTGRES_DB` env vars.

---

## Task 7: .dockerignore

### File: `.dockerignore` (repo root)

```
.git
.venv
__pycache__
*.pyc
.env
.env.*
*.md
tests/
reference/
memory/
docs/
wow_addon/
companion_app/
node_modules/
.github/
```

Keep the image lean — tests, docs, addon, and companion app don't ship in the container.

---

## Local Development

For developers who want to run locally without Docker:

```bash
# Option 1: Docker for DB only, app on host
docker compose up db -d
source .venv/bin/activate
python scripts/run_dev.py

# Option 2: Full Docker
docker compose up --build
```

No changes to `scripts/run_dev.py` — it continues to work as-is for local development.

---

## Tests

- Dockerfile builds successfully (`docker build -t guild-portal .`)
- Container starts, runs migrations, serves on port 8100
- Health check endpoint responds (`GET /` returns 200)
- Generic docker-compose.yml brings up full stack (app + db + caddy)
- PATT compose file brings up all 3 environments
- Each environment has isolated database (no cross-contamination)
- Dev/test behind basic auth

---

## Deliverables Checklist

- [ ] Dockerfile
- [ ] docker-entrypoint.sh
- [ ] docker-compose.yml (generic, for other guilds)
- [ ] docker-compose.patt.yml (PATT 3-environment setup)
- [ ] Caddyfile (generic)
- [ ] Caddyfile.patt (PATT subdomains)
- [ ] .env.template (annotated)
- [ ] .dockerignore
- [ ] Updated setup_postgres.sql (generic)
- [ ] DNS records for dev/test subdomains
- [ ] .env.prod, .env.test, .env.dev on server
- [ ] Production migration from systemd to Docker
- [ ] Updated GitHub Actions deploy workflow
- [ ] Local dev instructions verified
