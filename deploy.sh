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
