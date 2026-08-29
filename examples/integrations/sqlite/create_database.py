"""Create a tiny SQLite database with deliberately flawed rows.

Synthetic throughout: the names, emails and dates below are invented, and
nothing here reaches the network. Every flaw is placed on purpose so the scan
finds something real to report rather than relying on random corruption.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DATABASE = Path(__file__).parent / "shop.db"

# Column order: order_id, customer_email, order_date, quantity, unit_price, country
ORDERS = [
    (1, "ana@example.com", "2026-01-05", 2, 19.99, "ES"),
    (2, "bruno@example.com", "2026-01-06", 1, 24.50, "PT"),
    (3, "chen@example.com", "2026-01-07", 3, 12.00, "SG"),
    (4, "dara@example.com", "2026-01-08", 1, 45.00, "IE"),
    (5, "eli@example.com", "2026-01-09", 2, 19.99, "US"),
    (6, "farah@example.com", "2026-01-10", 1, 31.25, "MA"),
    (7, "gus@example.com", "2026-01-11", 4, 8.75, "BR"),
    (8, "hana@example.com", "2026-01-12", 1, 52.00, "JP"),
    # The flaws, one kind each so a finding maps to an obvious cause.
    (9, "ivan@example.com", "2026-02-30", 1, 22.00, "PL"),  # a date that does not exist
    (10, "not-an-email", "2026-01-14", 1, 17.00, "NL"),  # malformed email
    (11, "kai@example.com", "2026-01-15", -2, 30.00, "KE"),  # negative quantity
    (12, "lena@example.com", "2026-01-16", 1, None, "DE"),  # missing price
    (13, "ana@example.com", "2026-01-05", 2, 19.99, "ES"),  # exact duplicate of row 1
    (14, "mira@example.com", "2026-01-17", 1, 26.00, ""),  # empty country
]


def main() -> None:
    DATABASE.unlink(missing_ok=True)
    with sqlite3.connect(DATABASE) as connection:
        connection.execute(
            """
            CREATE TABLE orders (
                order_id       INTEGER PRIMARY KEY,
                customer_email TEXT,
                order_date     TEXT,
                quantity       INTEGER,
                unit_price     REAL,
                country        TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)",
            ORDERS,
        )
    print(f"wrote {DATABASE} with {len(ORDERS)} rows")


if __name__ == "__main__":
    main()
