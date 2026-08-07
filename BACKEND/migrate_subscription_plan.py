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
        cursor.execute("ALTER TABLE users ADD COLUMN subscription_plan TEXT;")
        print("Added 'subscription_plan' column to 'users' table.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print("'subscription_plan' column already exists in 'users' table.")
        else:
            print(f"Error adding 'subscription_plan' column: {e}")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
