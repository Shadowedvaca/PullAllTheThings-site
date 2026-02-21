-- PATT Platform PostgreSQL setup
-- Run as postgres superuser: sudo -u postgres psql < deploy/setup_postgres.sql

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
