import sqlite3
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    logger.info(f"NULL count: {cursor.fetchone()[0]}")
    conn.close()

if __name__ == "__main__":
    check_nulls()
