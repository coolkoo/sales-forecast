#!/usr/bin/env bash
# Runs the pipeline once on startup, then serves API/dashboard + MCP.
# In production the nightly refresh is a cron/systemd-timer calling `python -m app.pipeline`.
set -euo pipefail

MODE="${1:-serve}"

if [ "$MODE" = "pipeline" ]; then
  echo "[entrypoint] pipeline-only run"
  exec python -m app.pipeline
fi

# Initial pipeline runs in the BACKGROUND so the UI/API come up immediately;
# with the Chronos-2 backend the first run loads the model + forecasts ~200 series,
# which takes a few minutes. Data appears in the dashboard as it completes.
echo "[entrypoint] launching initial pipeline in background (backend=${SF_FORECAST_BACKEND:-seasonal})"
( python -m app.pipeline > /tmp/pipeline.log 2>&1 && echo "[pipeline] done" >> /tmp/pipeline.log \
  || echo "[pipeline] FAILED (see /tmp/pipeline.log)" >> /tmp/pipeline.log ) &

echo "[entrypoint] starting intra-day anomaly scheduler (every ${SF_DETECT_INTERVAL_MIN:-60} min)"
python -m app.scheduler &

echo "[entrypoint] starting MCP server on :${SF_MCP_PORT:-8901}"
python -m app.mcp_server &

echo "[entrypoint] starting API + dashboard on :${SF_API_PORT:-8900}"
exec uvicorn app.api.server:api --host 0.0.0.0 --port "${SF_API_PORT:-8900}"
