#!/usr/bin/env bash
# Bundles the files that must NOT go through GitHub (Cult-internal config and
# notes) into data/handover-<date>.zip. Send the zip privately.
# Pass --with-workbook to include the local .xlsx (contains customer PII).
set -euo pipefail
cd "$(dirname "$0")/.."
out="data/handover-$(date +%Y%m%d).zip"
files=(config.private.yaml PLAN.private.md)
if [[ "${1:-}" == "--with-workbook" ]]; then
  while IFS= read -r f; do files+=("$f"); done < <(ls *.xlsx 2>/dev/null || true)
fi
mkdir -p data
rm -f "$out"
zip -j "$out" "${files[@]}"
echo "wrote $out:"
unzip -l "$out"
