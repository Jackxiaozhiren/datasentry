#!/usr/bin/env bash
# DataSentry quickstart demo generator — creates a deterministic dirty
# orders.csv and records the docs/demo/quickstart.gif with vhs.
#
# Usage:
#   ./docs/demo/record-demo.sh            # one-shot: CSV + GIF
#   ./docs/demo/record-demo.sh --csv      # only (re)create demo-data/orders.csv
#
# Requires: vhs (https://github.com/charmbracelet/vhs) — brew install vhs
# The repo venv must be synced (uv sync); the script puts .venv/bin on PATH
# so the recorded shell resolves the `datasentry` CLI.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

OUT="demo-data/orders.csv"
mkdir -p demo-data

# fresh workspace: every recording starts from the same clean state
rm -rf demo-data/.datasentry

python3 - "$OUT" <<'EOF'
import csv, random, sys
from datetime import date, timedelta

path = sys.argv[1]
rng = random.Random(42)

rows = []
now = date(2026, 8, 1)
for i in range(200):
    order_id = 1000 + i
    if i % 15 == 0:
        order_id = 1000 + i - 1          # exact duplicate
    customer = rng.choice(["alice", "bob", "carol", "dave", "erin", "ALICE ", " bob", "carol"])
    amount = round(rng.uniform(5, 500), 2)
    if i % 22 == 0:
        amount = round(rng.uniform(5000, 90000), 2)   # outlier
    if i % 30 == 0:
        amount = "n/a"                                  # missing token
    d = now - timedelta(days=rng.randint(0, 120))
    order_date = d.isoformat()
    if i % 18 == 0:
        order_date = "n/a"
    if i % 45 == 0:
        order_date = "2026-13-01"        # invalid month
    rows.append([order_id, customer, amount, order_date])

with open(path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["order_id", "customer", "amount", "order_date"])
    w.writerows(rows)

print(f"wrote {len(rows)} rows -> {path}")
EOF

if [[ "${1:-}" == "--csv" ]]; then
  exit 0
fi

export PATH="$REPO_DIR/.venv/bin:$PATH"
command -v datasentry >/dev/null || {
  echo "error: datasentry not found in $REPO_DIR/.venv/bin — run: uv sync" >&2
  exit 1
}

cd "$REPO_DIR/demo-data"
vhs "$SCRIPT_DIR/quickstart.tape"
echo "recorded $REPO_DIR/docs/demo/quickstart.gif"
