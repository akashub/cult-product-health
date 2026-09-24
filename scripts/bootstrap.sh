#!/usr/bin/env bash
# One-time install on macOS/Linux: uv, Python deps, headless browser.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python package manager)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync
uv run playwright install chromium
mkdir -p data
if [ ! -f config.private.yaml ]; then
  echo "NOTE: config.private.yaml is missing. Copy it from the handover bundle into $(pwd)."
fi
echo
uv run cultph doctor || true
