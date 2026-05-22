import sqlite3
import uuid
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_invoice_uuids():
    conn = sqlite3.connect('suk_anan.db')
    cursor = conn.cursor()
    
    # Check if uuid column exists
    cursor.execute("PRAGMA table_info(invoices)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'uuid' not in columns:
        logger.info("Adding uuid column to invoices...")
        cursor.execute("ALTER TABLE invoices ADD COLUMN uuid TEXT")
    
    # Find invoices with null UUID
    cursor.execute("SELECT id FROM invoices WHERE uuid IS NULL OR uuid = ''")
    rows = cursor.fetchall()
    
    if rows:
        logger.info(f"Found {len(rows)} invoices with missing UUID. Fixing...")
        for (invoice_id,) in rows:
            new_uuid = str(uuid.uuid4())
            cursor.execute("UPDATE invoices SET uuid = ? WHERE id = ?", (new_uuid, invoice_id))
        conn.commit()
        logger.info("All missing UUIDs updated.")
    else:
        logger.info("No missing UUIDs found in invoices.")
    
    # Also check tenants just in case
    cursor.execute("SELECT id FROM tenants WHERE uuid IS NULL OR uuid = ''")
    rows = cursor.fetchall()
    if rows:
        logger.info(f"Found {len(rows)} tenants with missing UUID. Fixing...")
        for (tenant_id,) in rows:
            new_uuid = str(uuid.uuid4())
            cursor.execute("UPDATE tenants SET uuid = ? WHERE id = ?", (new_uuid, tenant_id))
        conn.commit()
        logger.info("All missing tenant UUIDs updated.")

    conn.close()

if __name__ == "__main__":
    fix_invoice_uuids()
