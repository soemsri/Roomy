import sqlite3
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def check_utility():
    conn = sqlite3.connect('suk_anan.db')
    cursor = conn.cursor()
    
    logger.info("--- Income (Utility) ---")
    cursor.execute("""
        SELECT billing_month, SUM(electricity_amount), SUM(water_amount) 
        FROM invoices 
        WHERE billing_year = 2026 AND status = 'Paid' 
        GROUP BY billing_month
    """)
    for row in cursor.fetchall():
        logger.info(f"Month {row[0]}: Elec={row[1]}, Water={row[2]}, Total={row[1]+row[2]}")

    logger.info("\n--- Expense (Utility) ---")
    cursor.execute("""
        SELECT billing_month, SUM(amount) 
        FROM expenses 
        WHERE billing_year = 2026 AND category = 'Utility' 
        GROUP BY billing_month
    """)
    for row in cursor.fetchall():
        logger.info(f"Month {row[0]}: Total={row[1]}")
    
    conn.close()

if __name__ == "__main__":
    check_utility()
