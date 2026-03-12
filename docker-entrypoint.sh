#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting Guild Portal..."
exec uvicorn guild_portal.app:create_app \
    --host 0.0.0.0 \
    --port 8100 \
    --factory \
    --workers 1
