"""
One-shot migration: adds customer_name, appointment_date, appointment_time
to the contacts table (and any other missing columns).
Safe to run multiple times — already-existing columns are skipped.
"""
import sqlite3

conn = sqlite3.connect("callinggen.db")
cur = conn.cursor()

new_columns = [
    ("contacts", "customer_name",    "TEXT"),
    ("contacts", "appointment_date", "TEXT"),
    ("contacts", "appointment_time", "TEXT"),
    ("contacts", "metadata_fields",  "TEXT"),
    ("calls",    "recording_url",   "TEXT"),
    ("calls",    "notes",           "TEXT"),
    ("calls",    "summary",         "TEXT"),
    ("calls",    "category",        "TEXT"),
]

for table, col, col_type in new_columns:
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        print(f"  Added  : {table}.{col}")
    except sqlite3.OperationalError as e:
        print(f"  Skipped: {table}.{col} ({e})")

conn.commit()
conn.close()
print("Migration complete.")
