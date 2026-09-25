import sqlite3
import os

def migrate():
    # Use absolute path to the database
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "callinggen.db")
    print(f"Migrating database at: {db_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Add sip_was_active column (default 0)
        cursor.execute("ALTER TABLE calls ADD COLUMN sip_was_active BOOLEAN DEFAULT 0")
        print("Added sip_was_active column")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("sip_was_active column already exists")
        else:
            raise e
            
    try:
        # Add answered_at column
        cursor.execute("ALTER TABLE calls ADD COLUMN answered_at DATETIME")
        print("Added answered_at column")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("answered_at column already exists")
        else:
            raise e

    # Apply historical backfill rules
    
    # 1. Any historical call with duration > 0 must have been answered
    cursor.execute("""
        UPDATE calls 
        SET sip_was_active = 1 
        WHERE duration > 0
    """)
    print(f"Updated {cursor.rowcount} calls with duration > 0 to sip_was_active=1")
    
    # 2. Historical fail calls that should be NO ANSWER
    # If duration = 0, status = failed, and failure_reason is null
    cursor.execute("""
        UPDATE calls
        SET status = 'ended', outcome = 'no_answer'
        WHERE status = 'failed' 
          AND duration = 0 
          AND failure_reason IS NULL
    """)
    print(f"Updated {cursor.rowcount} stuck failed calls to ended/no_answer")

    conn.commit()
    conn.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
