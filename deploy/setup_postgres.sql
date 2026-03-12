-- Guild Portal PostgreSQL schema setup
-- For Docker: mounted as /docker-entrypoint-initdb.d/01-setup.sql
-- For manual setup: run as postgres superuser after creating the database and user
--
-- Docker handles user/database creation via POSTGRES_USER / POSTGRES_DB env vars.
-- This script only creates the schemas and grants permissions.

CREATE SCHEMA IF NOT EXISTS common;
CREATE SCHEMA IF NOT EXISTS guild_identity;
CREATE SCHEMA IF NOT EXISTS patt;

-- Grant permissions to the current user (set by POSTGRES_USER in Docker)
DO $$
BEGIN
    EXECUTE format('GRANT ALL ON SCHEMA common TO %I', current_user);
    EXECUTE format('GRANT ALL ON SCHEMA guild_identity TO %I', current_user);
    EXECUTE format('GRANT ALL ON SCHEMA patt TO %I', current_user);
END
$$;
