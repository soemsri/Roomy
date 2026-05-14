import sqlite3
import os

def migrate():
    db_path = os.path.join(os.path.dirname(__file__), 'suk_anan.db')
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    curr = conn.cursor()
    
    commands = [
        # Owners table
        "ALTER TABLE owners ADD COLUMN late_fee_enabled INTEGER DEFAULT 0",
        "ALTER TABLE owners ADD COLUMN due_day INTEGER DEFAULT 5",
        "ALTER TABLE owners ADD COLUMN late_fee_per_day REAL DEFAULT 50.0",
        "ALTER TABLE owners ADD COLUMN promptpay_name TEXT",
        "ALTER TABLE owners ADD COLUMN lease_template TEXT",
        "ALTER TABLE owners ADD COLUMN move_in_fees_config TEXT DEFAULT '[]'",
        "ALTER TABLE owners ADD COLUMN default_recurring_charges TEXT DEFAULT '[]'",
        "ALTER TABLE owners ADD COLUMN meter_history_page_size INTEGER DEFAULT 10",
        
        # Invoices table
        "ALTER TABLE invoices ADD COLUMN late_fee REAL DEFAULT 0.0",
        
        # Tenants table
        "ALTER TABLE tenants ADD COLUMN status TEXT DEFAULT 'Active'", # Default to Active for existing tenants
        "ALTER TABLE tenants ADD COLUMN temp_building_id INTEGER REFERENCES buildings(id)",
        "ALTER TABLE tenants ADD COLUMN requested_move_in_date DATETIME",
        "ALTER TABLE tenants ADD COLUMN move_out_date DATETIME",
        "ALTER TABLE tenants ADD COLUMN move_out_reason TEXT",
        
        # Room PromptPay assignment
        "ALTER TABLE rooms ADD COLUMN promptpay_id TEXT",
        "ALTER TABLE rooms ADD COLUMN recurring_charges TEXT",
        
        # Invoices table extensions
        "ALTER TABLE invoices ADD COLUMN electricity_reading REAL",
        "ALTER TABLE invoices ADD COLUMN water_reading REAL",
        "ALTER TABLE invoices ADD COLUMN prev_electricity_reading REAL",
        "ALTER TABLE invoices ADD COLUMN prev_water_reading REAL",
        "ALTER TABLE invoices ADD COLUMN other_charges TEXT",
        
        # New: room_assets table
        "CREATE TABLE IF NOT EXISTS room_assets (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL, name TEXT NOT NULL, quantity INTEGER DEFAULT 1, FOREIGN KEY (room_id) REFERENCES rooms(id))",
        
        # New: buildings table
        "CREATE TABLE IF NOT EXISTS buildings (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT)",
        
        # Room building_id
        "ALTER TABLE rooms ADD COLUMN building_id INTEGER REFERENCES buildings(id)",

        # Lease table missing columns or creation
        "CREATE TABLE IF NOT EXISTS leases (id INTEGER PRIMARY KEY AUTOINCREMENT, room_id INTEGER NOT NULL, tenant_id INTEGER NOT NULL, start_date DATETIME NOT NULL, end_date DATETIME, status TEXT DEFAULT 'Active', lease_content TEXT, initial_fees TEXT, FOREIGN KEY (room_id) REFERENCES rooms(id), FOREIGN KEY (tenant_id) REFERENCES tenants(id))",
        "ALTER TABLE leases ADD COLUMN lease_content TEXT",
        "ALTER TABLE leases ADD COLUMN initial_fees TEXT",
        "ALTER TABLE leases ADD COLUMN security_deposit_amount REAL DEFAULT 0.0",
        "ALTER TABLE leases ADD COLUMN advance_rent_amount REAL DEFAULT 0.0",
        "ALTER TABLE leases ADD COLUMN initial_payment_status TEXT DEFAULT 'Pending'",
        "ALTER TABLE leases ADD COLUMN initial_payment_method TEXT",
        "ALTER TABLE leases ADD COLUMN initial_payment_date TIMESTAMP",
        "ALTER TABLE leases ADD COLUMN initial_payment_receipt TEXT",

        # Invoice pro-rata
        "ALTER TABLE invoices ADD COLUMN is_pro_rata INTEGER DEFAULT 0",

        # Settlements table
        "CREATE TABLE IF NOT EXISTS settlements (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, room_id INTEGER NOT NULL, lease_id INTEGER NOT NULL, settlement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, pro_rated_rent REAL DEFAULT 0.0, electricity_units REAL DEFAULT 0.0, electricity_amount REAL DEFAULT 0.0, water_units REAL DEFAULT 0.0, water_amount REAL DEFAULT 0.0, unpaid_invoices_amount REAL DEFAULT 0.0, cleaning_fee REAL DEFAULT 0.0, damage_fee REAL DEFAULT 0.0, other_fees REAL DEFAULT 0.0, total_deductions REAL DEFAULT 0.0, security_deposit_amount REAL DEFAULT 0.0, advance_rent_amount REAL DEFAULT 0.0, final_balance REAL DEFAULT 0.0, refund_method TEXT, refund_receipt_img TEXT, status TEXT DEFAULT 'Completed', notes TEXT, FOREIGN KEY (tenant_id) REFERENCES tenants(id), FOREIGN KEY (room_id) REFERENCES rooms(id), FOREIGN KEY (lease_id) REFERENCES leases(id))",
        "ALTER TABLE settlements ADD COLUMN advance_rent_amount REAL DEFAULT 0.0",

        # Room uniqueness migration (building-scoped)
        "DROP INDEX IF EXISTS ix_rooms_room_number",
        "CREATE UNIQUE INDEX IF NOT EXISTS _building_room_uc ON rooms (building_id, room_number)"
    ]
    
    for cmd in commands:
        try:
            print(f"Executing: {cmd}")
            curr.execute(cmd)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"  Column already exists, skipping.")
            elif "no such index" in str(e).lower():
                print(f"  Index not found, skipping.")
            else:
                print(f"  Error: {e}")

    # Initialization: Create default building and link rooms if needed
    curr.execute("SELECT COUNT(*) FROM buildings")
    if curr.fetchone()[0] == 0:
        print("Initializing default building...")
        curr.execute("INSERT INTO buildings (name, description) VALUES (?, ?)", ("อาคารหลัก", "อาคารหลักของหอพัก"))
        default_building_id = curr.lastrowid
        curr.execute("UPDATE rooms SET building_id = ?", (default_building_id,))
                
    conn.commit()
    conn.close()
    print("Migration completed.")

if __name__ == "__main__":
    migrate()
