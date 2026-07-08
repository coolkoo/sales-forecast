#!/usr/bin/env bash
# Deploy sales-forecast to the GPU server (parallel to traderific at /root/Kronos).
#   ./deploy/deploy.sh
# Requires SSH access to the host and Docker + compose on it.
set -euo pipefail

HOST="${SF_DEPLOY_HOST:-root@192.168.50.85}"
DEST="${SF_DEPLOY_DIR:-/root/sales-forecast}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Syncing $HERE  ->  $HOST:$DEST"
rsync -az --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '*.db' --exclude '.pytest_cache' --exclude 'deploy/.env' \
  "$HERE"/ "$HOST:$DEST"/

echo "==> Ensuring deploy/.env exists on host"
ssh "$HOST" "test -f $DEST/deploy/.env || cp $DEST/deploy/.env.example $DEST/deploy/.env"

echo "==> Building + starting stack"
ssh "$HOST" "cd $DEST && docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build"

echo "==> Waiting for API health"
ssh "$HOST" "for i in \$(seq 1 30); do curl -sf http://localhost:8900/api/health && break || sleep 2; done; echo"

cat <<EOF

==> Done.
    Dashboard : http://192.168.50.85:8900/
    API       : http://192.168.50.85:8900/api/summary
    MCP       : http://192.168.50.85:8901/mcp   (Authorization: Bearer \$SF_MCP_TOKEN)

    Re-run the pipeline on the host any time:
      ssh $HOST "cd $DEST && docker compose -f deploy/docker-compose.yml exec app python -m app.pipeline"

    Upgrade to real Chronos-2 (GPU):
      1) edit deploy/.env: SF_FORECAST_BACKEND=chronos  SF_CHRONOS_DEVICE=cuda:0
      2) uncomment the GPU 'deploy.resources' block + install requirements-forecast.txt in the image
      3) ./deploy/deploy.sh
EOF
