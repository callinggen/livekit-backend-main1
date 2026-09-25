import sqlite3

conn = sqlite3.connect("callinggen.db")
cur = conn.cursor()

# Show current state
cur.execute("SELECT id, status, room_name FROM calls WHERE status IN ('dialing', 'in_progress')")
stuck_calls = cur.fetchall()
print("Stuck calls:", stuck_calls)

cur.execute("SELECT id, status FROM jobs WHERE status IN ('queued', 'processing')")
active_jobs = cur.fetchall()
print("Active jobs:", active_jobs)

# Reset stuck calls to 'failed'
cur.execute("UPDATE calls SET status = 'failed', billing_status = 'not_billable' WHERE status IN ('dialing', 'in_progress')")
print(f"Reset {cur.rowcount} stuck call(s) to 'failed'")

# Reset stuck jobs to 'queued' so they retry
cur.execute("UPDATE jobs SET status = 'queued' WHERE status = 'processing'")
print(f"Reset {cur.rowcount} stuck job(s) to 'queued'")

conn.commit()
conn.close()
print("Done. Restart the worker now.")
