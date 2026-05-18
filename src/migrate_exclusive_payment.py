import sqlite3
import os

def migrate():
    db_path = "suk_anan.db"
    if not os.path.exists(db_path):
        print(f"Database {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        print("Migrating 'owners' table...")
        cursor.execute("ALTER TABLE owners ADD COLUMN bank_config TEXT DEFAULT '[]'")
        print("Added 'bank_config' to 'owners'.")
    except sqlite3.OperationalError as e:
        print(f"Note: {e}")

    try:
        print("Migrating 'rooms' table...")
        cursor.execute("ALTER TABLE rooms ADD COLUMN primary_payment_type TEXT DEFAULT 'PromptPay'")
        cursor.execute("ALTER TABLE rooms ADD COLUMN primary_payment_id TEXT")
        print("Added 'primary_payment_type' and 'primary_payment_id' to 'rooms'.")
        
        # Migrate existing data
        print("Migrating existing PromptPay data to primary payment fields...")
        cursor.execute("UPDATE rooms SET primary_payment_id = promptpay_id WHERE promptpay_id IS NOT NULL AND primary_payment_id IS NULL")
        print("Data migration completed.")
        
    except sqlite3.OperationalError as e:
        print(f"Note: {e}")

    conn.commit()
    conn.close()
    print("Migration finished.")

if __name__ == "__main__":
    migrate()
