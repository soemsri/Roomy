import sqlite3

def check_utility():
    conn = sqlite3.connect('suk_anan.db')
    cursor = conn.cursor()
    
    print("--- Income (Utility) ---")
    cursor.execute("""
        SELECT billing_month, SUM(electricity_amount), SUM(water_amount) 
        FROM invoices 
        WHERE billing_year = 2026 AND status = 'Paid' 
        GROUP BY billing_month
    """)
    for row in cursor.fetchall():
        print(f"Month {row[0]}: Elec={row[1]}, Water={row[2]}, Total={row[1]+row[2]}")

    print("\n--- Expense (Utility) ---")
    cursor.execute("""
        SELECT billing_month, SUM(amount) 
        FROM expenses 
        WHERE billing_year = 2026 AND category = 'Utility' 
        GROUP BY billing_month
    """)
    for row in cursor.fetchall():
        print(f"Month {row[0]}: Total={row[1]}")
    
    conn.close()

if __name__ == "__main__":
    check_utility()
