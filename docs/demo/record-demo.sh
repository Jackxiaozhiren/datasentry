#!/usr/bin/env bash
# DataSentry quickstart demo data generator — creates a deterministic dirty
# orders.csv so the demo GIF always shows the same story.
#
# Usage:
#   ./docs/demo/record-demo.sh          # create orders.csv in ./demo-data
#   cd demo-data && vhs ../quickstart.tape   # record docs/demo/quickstart.gif
#
# Requires: vhs (https://github.com/charmbracelet/vhs) — brew install vhs

set -euo pipefail

OUT="demo-data/orders.csv"
mkdir -p demo-data

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
