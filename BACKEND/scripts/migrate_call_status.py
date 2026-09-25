import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "callinggen.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Add columns if they don't exist
    try:
        cursor.execute("ALTER TABLE calls ADD COLUMN outcome VARCHAR")
        print("Added 'outcome' column.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("'outcome' column already exists.")
        else:
            raise e

    try:
        cursor.execute("ALTER TABLE calls ADD COLUMN failure_reason VARCHAR")
        print("Added 'failure_reason' column.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("'failure_reason' column already exists.")
        else:
            raise e

    # 2. Dry run / Report
    cursor.execute("""
        SELECT c.id, c.duration, c.recording_url, cnt.response, c.status
        FROM calls c
        LEFT JOIN contacts cnt ON c.contact_id = cnt.id
        WHERE c.status = 'failed'
    """)
    failed_calls = cursor.fetchall()
    
    to_migrate_no_answer = []
    to_migrate_declined = []
    to_migrate_customer_hangup = []
    true_failures = []

    for row in failed_calls:
        call_id, duration, recording_url, response, status = row
        response = response or ""
        
        # Rule: No duration and no recording and response indicates drop/cut/no answer
        if duration == 0 and not recording_url:
            if "no answer" in response.lower() or "declined" in response.lower():
                to_migrate_no_answer.append(call_id)
            elif "cut" in response.lower() or "disconnected" in response.lower():
                to_migrate_customer_hangup.append(call_id)
            else:
                true_failures.append(call_id)
        else:
            true_failures.append(call_id)

    print("\n--- MIGRATION DRY RUN REPORT ---")
    print(f"Total 'failed' calls found: {len(failed_calls)}")
    print(f"Calls to reclassify as 'ended' -> 'no_answer': {len(to_migrate_no_answer)}")
    print(f"Calls to reclassify as 'ended' -> 'customer_hangup': {len(to_migrate_customer_hangup)}")
    print(f"Calls to KEEP as 'failed': {len(true_failures)}")
    print("--------------------------------\n")

    # 3. Apply updates
    if to_migrate_no_answer:
        cursor.execute(f"""
            UPDATE calls 
            SET status = 'ended', outcome = 'no_answer' 
            WHERE id IN ({','.join('?' * len(to_migrate_no_answer))})
        """, to_migrate_no_answer)

    if to_migrate_customer_hangup:
        cursor.execute(f"""
            UPDATE calls 
            SET status = 'ended', outcome = 'customer_hangup' 
            WHERE id IN ({','.join('?' * len(to_migrate_customer_hangup))})
        """, to_migrate_customer_hangup)

    # For existing completed calls, let's backfill outcome='answered'
    cursor.execute("UPDATE calls SET outcome = 'answered' WHERE status = 'completed' AND outcome IS NULL")

    conn.commit()
    print("Migration successfully applied!")
    conn.close()

if __name__ == "__main__":
    migrate()
