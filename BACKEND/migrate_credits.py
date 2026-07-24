import sqlite3
import os

def migrate():
    db_path = os.path.join(os.path.dirname(__file__), "callinggen.db")
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}. No migration needed.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT 2000;")
        print("Added 'credits' column to 'users' table.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("'credits' column already exists in 'users' table.")
        else:
            print(f"Error adding 'credits' column: {e}")

    try:
        cursor.execute("ALTER TABLE calls ADD COLUMN credits_deducted INTEGER DEFAULT 0;")
        print("Added 'credits_deducted' column to 'calls' table.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("'credits_deducted' column already exists in 'calls' table.")
        else:
            print(f"Error adding 'credits_deducted' column: {e}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
