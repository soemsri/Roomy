import sqlite3

def check_nulls():
    conn = sqlite3.connect('suk_anan.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) FROM invoices 
        WHERE total_amount IS NULL 
           OR rent_amount IS NULL 
           OR electricity_amount IS NULL 
           OR water_amount IS NULL
    """)
    print("NULL count:", cursor.fetchone()[0])
    conn.close()

if __name__ == "__main__":
    check_nulls()
