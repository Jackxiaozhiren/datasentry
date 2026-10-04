import sqlite3
from pathlib import Path


DATABASE_PATH = Path(__file__).with_name("example.db")


connection = sqlite3.connect(DATABASE_PATH)

connection.execute("""
    CREATE TABLE customers (
        id INTEGER PRIMARY KEY,
        name TEXT,
        email TEXT,
        age INTEGER
    )
""")

connection.executemany(
    "INSERT INTO customers (name, email, age) VALUES (?, ?, ?)",
    [
        ("Meera", "meera@example.com", 28),
        ("Arjun", "arjun@example.com", 35),
        ("Rohan", "arjun@example.com", 41),
        ("Nisha", None, 29),
    ],
)

connection.commit()
connection.close()