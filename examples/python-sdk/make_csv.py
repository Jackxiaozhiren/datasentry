"""Write the small synthetic CSV `quickstart.py` scans.

Every value is invented. The last four rows each carry one deliberate flaw, so a
finding traces back to an obvious cause and the output is the same on every
machine.
"""

import csv
from pathlib import Path

ROWS = [
    ("1", "ana@example.com", "2026-01-05", "2", "19.99"),
    ("2", "bruno@example.com", "2026-01-06", "1", "24.50"),
    ("3", "chen@example.com", "2026-01-07", "3", "12.00"),
    ("4", "dara@example.com", "2026-01-08", "1", "45.00"),
    ("5", "eli@example.com", "2026-01-09", "2", "19.99"),
    ("6", "farah@example.com", "2026-01-10", "1", "31.25"),
    ("7", "not-an-email", "2026-01-11", "1", "17.00"),  # malformed email
    ("8", "hana@example.com", "2026-02-30", "1", "22.00"),  # date that cannot exist
    ("9", "ivan@example.com", "2026-01-13", "1", ""),  # missing price
    ("10", "ana@example.com", "2026-01-05", "2", "19.99"),  # duplicate of row 1
]

path = Path(__file__).parent / "orders.csv"
with path.open("w", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(["order_id", "customer_email", "order_date", "quantity", "unit_price"])
    writer.writerows(ROWS)
print(f"wrote {path} with {len(ROWS)} rows")
