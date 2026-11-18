#!/usr/bin/env bash
# AC-11b pre-tag gate. Run this in the clean workspace that will be tagged,
# before creating the release tag. It establishes detector-drift evidence by
# retaining a source-data fixture and re-scanning it with the current detectors.
#
# Usage:
#   bash scripts/ac11b_release_gate.sh [RELEASE_WORKSPACE]
#
# Defaults RELEASE_WORKSPACE to the current directory. Exits non-zero on any
# failure so a release checklist cannot silently skip this gate.
set -euo pipefail

WS="${1:-$PWD}"

echo "== AC-11b pre-tag gate: $WS =="

if [ ! -d "$WS" ]; then
  echo "FAIL: workspace does not exist: $WS" >&2
  exit 1
fi

cd "$WS"

# Evidence must come from a clean checkout; a dirty tree cannot be a release point.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "FAIL: workspace has uncommitted tracked changes; use a clean origin/main checkout" >&2
  exit 1
fi
echo "HEAD=$(git rev-parse HEAD)  branch=$(git rev-parse --abbrev-ref HEAD)"

if [ ! -f "$WS/demo-data/orders.csv" ]; then
  echo "FAIL: missing $WS/demo-data/orders.csv (release checkout must include demo data)" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "FAIL: uv is required but not on PATH" >&2
  exit 1
fi

# 1/3 Generate a real scan run from the repo's deterministic demo data.
echo "== 1/3 scan =="
uv run datasentry --project "$WS" --format json scan "$WS/demo-data/orders.csv" \
  > "$WS/.datasentry-scan.log" 2>&1 \
  || { tail -20 "$WS/.datasentry-scan.log" >&2; echo "FAIL: scan" >&2; exit 1; }

# 2/3 Retain the source data. covered=0 means no run can be re-checked offline.
echo "== 2/3 fixtures capture =="
uv run datasentry --project "$WS" --format json fixtures capture \
  > "$WS/.datasentry-capture.json" 2>&1 \
  || { tail -20 "$WS/.datasentry-capture.json" >&2; echo "FAIL: fixtures capture" >&2; exit 1; }
python3 - "$WS/.datasentry-capture.json" <<'PY'
import json
import sys

raw = open(sys.argv[1]).read()
d = json.loads(raw[raw.find("{"):])["data"]
print(f"  checked={d['checked']} captured={d['captured']} covered={d['covered']} skipped={d['skipped']}")
if d["covered"] < 1:
    sys.exit("FAIL: covered=0; no run can survive source removal")
PY

# 3/3 Re-scan from fixtures and fail the gate on any issue-set drift.
echo "== 3/3 rescan --fail-on-drift =="
uv run datasentry --project "$WS" --format json rescan --fail-on-drift \
  > "$WS/.datasentry-rescan.json" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
  tail -20 "$WS/.datasentry-rescan.json" >&2
  echo "FAIL: rescan --fail-on-drift exited $RC" >&2
  exit "$RC"
fi
python3 - "$WS/.datasentry-rescan.json" <<'PY'
import json
import sys

raw = open(sys.argv[1]).read()
d = json.loads(raw[raw.find("{"):])["data"]
print(f"  checked={d['checked']} comparable={d['comparable']} from_fixture={d['from_fixture']} "
      f"uncovered={d['uncovered']} drifted={d['drifted']}")
if d["comparable"] < 1 or d["from_fixture"] < 1 or d["uncovered"] != 0 or d["drifted"] != 0:
    sys.exit("FAIL: re-scan judgement not satisfied")
PY

echo "== OK: AC-11b coverage established; archives under $WS/.datasentry/{fixtures,rescan}/ =="
echo "== rescan archive: $(ls -t "$WS"/.datasentry/rescan/*.json | head -1) =="
