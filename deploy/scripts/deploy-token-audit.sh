#!/bin/sh
set -eu

REMOTE=${REMOTE:-}
REMOTE_DIR=${REMOTE_DIR:-/opt/token-audit}
SSH_KEY=${SSH_KEY:-}

SSH="ssh"
if [ -n "$SSH_KEY" ]; then
  SSH="ssh -i $SSH_KEY -o IdentitiesOnly=yes"
fi

if [ -z "$REMOTE" ]; then
  echo "REMOTE is required, for example: REMOTE=ubuntu@your-server ./deploy/scripts/deploy-token-audit.sh" >&2
  exit 2
fi

$SSH "$REMOTE" "sudo mkdir -p '$REMOTE_DIR' && sudo chown -R \$USER:\$USER '$REMOTE_DIR'"
tar \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.pytest_cache' \
  --exclude 'frontend/admin/node_modules' \
  --exclude 'frontend/admin/dist' \
  --exclude 'token_audit/admin_dist' \
  --exclude 'data' \
  -czf - . | $SSH "$REMOTE" "cd '$REMOTE_DIR' && tar -xzf -"
$SSH "$REMOTE" "cd '$REMOTE_DIR' && mkdir -p data && chmod 700 data && docker compose -f deploy/docker-compose.yml up -d --build token-audit && docker inspect --format='{{json .State.Health}}' token-audit || true && docker logs --tail=80 token-audit"
