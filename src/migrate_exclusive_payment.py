import sqlite3
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def migrate():
    db_path = "suk_anan.db"
    if not os.path.exists(db_path):
        logger.error(f"Database {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        logger.info("Migrating 'owners' table...")
        cursor.execute("ALTER TABLE owners ADD COLUMN bank_config TEXT DEFAULT '[]'")
        logger.info("Added 'bank_config' to 'owners'.")
    except sqlite3.OperationalError as e:
        logger.warning(f"Note: {e}")

    try:
        logger.info("Migrating 'rooms' table...")
        cursor.execute("ALTER TABLE rooms ADD COLUMN primary_payment_type TEXT DEFAULT 'PromptPay'")
        cursor.execute("ALTER TABLE rooms ADD COLUMN primary_payment_id TEXT")
        logger.info("Added 'primary_payment_type' and 'primary_payment_id' to 'rooms'.")
        
        # Migrate existing data
        logger.info("Migrating existing PromptPay data to primary payment fields...")
        cursor.execute("UPDATE rooms SET primary_payment_id = promptpay_id WHERE promptpay_id IS NOT NULL AND primary_payment_id IS NULL")
        logger.info("Data migration completed.")
        
    except sqlite3.OperationalError as e:
        logger.warning(f"Note: {e}")

    conn.commit()
    conn.close()
    logger.info("Migration finished.")

if __name__ == "__main__":
    migrate()
