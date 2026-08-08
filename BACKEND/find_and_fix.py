import sqlite3

def fix_test02():
    conn = sqlite3.connect('callinggen.db')
    cur = conn.cursor()
    
    # Get all tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cur.fetchall()]
    print("Tables in database:", tables)
    
    updated_records = []
    
    for table in tables:
        # Get column names
        cur.execute(f"PRAGMA table_info({table});")
        columns = [c[1] for c in cur.fetchall()]
        
        # Check if table has a status column
        if 'status' in columns:
            # Check for any row where name, title, or room_name or id matches 'test-02' or contains 'test-02'
            where_clauses = []
            for col in ['name', 'title', 'room_name', 'campaign_name', 'id']:
                if col in columns:
                    where_clauses.append(f"{col} LIKE '%test-02%'")
            
            if where_clauses:
                query = f"SELECT * FROM {table} WHERE " + " OR ".join(where_clauses)
                rows = cur.execute(query).fetchall()
                if rows:
                    print(f"Found in {table}:", rows)
                    update_query = f"UPDATE {table} SET status = 'completed' WHERE " + " OR ".join(where_clauses)
                    cur.execute(update_query)
                    updated_records.append((table, len(rows)))
    
    conn.commit()
    conn.close()
    print("Updated records:", updated_records)

if __name__ == "__main__":
    fix_test02()
