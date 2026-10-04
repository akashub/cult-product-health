#!/usr/bin/env bash
# Bundles the files that must NOT go through GitHub into data/handover-<date>.zip.
# Send the zip privately.
#   --with-data      also include data/amazon.db (reviews, ratings, photos, AI labels, trend history:
#                    public marketplace data only) so the new laptop doesn't start empty
#   --with-workbook  also include the local .xlsx (contains customer PII)
# Never bundled: cultph.db (order ids; rebuilt on the first import), Amazon session/profile
# (credentials; run `cultph amazon-login` on the new machine), data/.env (keys).
set -euo pipefail
cd "$(dirname "$0")/.."
out="data/handover-$(date +%Y%m%d).zip"
files=(config.private.yaml PLAN.private.md)
for arg in "$@"; do
  case "$arg" in
    --with-data) files+=(data/amazon.db) ;;
    --with-workbook) while IFS= read -r f; do files+=("$f"); done < <(ls *.xlsx 2>/dev/null || true) ;;
  esac
done
mkdir -p data
rm -f "$out"
zip "$out" "${files[@]}" -x "*.DS_Store"
echo "wrote $out:"
unzip -l "$out"
