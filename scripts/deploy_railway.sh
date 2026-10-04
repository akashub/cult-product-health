#!/usr/bin/env bash
# Deploy the current commit to Railway without GitHub (the private config lives in
# the CULTPH_CONFIG_B64 service variable). Needs RAILWAY_TOKEN (project token) in the env.
# The bundle is assembled under data/ (gitignored). The volume is already seeded, so no seed is sent.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RAILWAY_TOKEN:?set RAILWAY_TOKEN to the Railway project token}"
SERVICE="${RAILWAY_SERVICE:-cult-product-health}"
B="data/deploy_bundle"
rm -rf "$B" && mkdir -p "$B"
git archive HEAD | tar -x -C "$B"
cd "$B"
railway up --service "$SERVICE" --environment "${RAILWAY_ENVIRONMENT:-production}" --detach
