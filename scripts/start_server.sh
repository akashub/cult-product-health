#!/bin/sh
# Container entrypoint. Env vars:
#   CULTPH_CONFIG_B64       base64 of config.private.yaml (keeps Cult details off GitHub)
#   CULTPH_SCHEDULE_MINUTES run `cultph watch` every N minutes in the background (unset = dashboard only)
#   DASHBOARD_PASSWORD      password gate for the dashboard (set this on any public URL)
#   PORT                    set by the host
set -e
cd /app
mkdir -p data
if [ -n "$CULTPH_CONFIG_B64" ]; then
  echo "$CULTPH_CONFIG_B64" | base64 -d > config.private.yaml
fi
# one-time seed of the persistent volume from a bundled snapshot, if present
if [ -d seed ] && [ ! -f data/.seeded ]; then
  cp -R seed/. data/
  date > data/.seeded
  echo "seeded data volume from bundled snapshot"
fi
if [ -n "$CULTPH_SCHEDULE_MINUTES" ]; then
  uv run --no-dev cultph watch --every "$CULTPH_SCHEDULE_MINUTES" >> data/watch.log 2>&1 &
  echo "scheduler started: every $CULTPH_SCHEDULE_MINUTES min (log: data/watch.log)"
fi
exec uv run --no-dev streamlit run app/dashboard.py \
  --server.port "${PORT:-8501}" --server.address 0.0.0.0 --server.headless true \
  --browser.gatherUsageStats false
