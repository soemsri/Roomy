import sqlite3
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_CONFIGS = {
    "LINE_ADMIN_CHANNEL_ACCESS_TOKEN": "Am0h1++54LfstMVl1EXbHMYfaaBj+Hk9OKcgIcW0cfOx3H/MNO8Ijd0w9jIGd0Ym0PJ7ByHrOr+5quQNyIp4DPORBZN/BwgMX/SWaNxt61nW19Z5bwDlEpI4il9vlkWYuP5BC7cpiqTXbUn45Ty/XgdB04t89/1O/w1cDnyilFU=",
    "LINE_ADMIN_CHANNEL_SECRET": "c365858c050cb6b0a2253ce0e2a49585",
    "LINE_TENANT_CHANNEL_ACCESS_TOKEN": "hoMaEApJucC37dk8UTqI5qWDTEAZMBUrIxenPVdCUdaz0rXi6piHXQ6Vcp8MyBQPuJx4LUf2GaiSR4wcjT5mLgBA1HgRF8BdN9pGjIMGdLiQscOLR2GMfZglS0Rf9iRPdwtvIT9XqeS7dnDroxOJVwdB04t89/1O/w1cDnyilFU=",
    "LINE_TENANT_CHANNEL_SECRET": "dc2e2616a566dcccc2d2fe534f978f89",
    "BASE_URL": "https://splatter-provolone-variety.ngrok-free.dev/",
    "ADMIN_PASSWORD": "roomy+-*/()[]"
}

def seed_system_configs(session):
    from services.security import set_system_config
    import models
    logger.info("Checking and seeding default system configs...")
    for key, value in DEFAULT_SYSTEM_CONFIGS.items():
        existing = session.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
        if not existing:
            logger.info(f"Seeding default value for {key}")
            set_system_config(session, key, value, description="Default seed configuration")

def migrate():
    """
    Executes the database migration commands. Supports both SQLite and PostgreSQL.
    """
    from models.database import SQLALCHEMY_DATABASE_URL, engine, Base
    import models
    from sqlalchemy.orm import sessionmaker

    logger.info(f"Detected DATABASE_URL: {SQLALCHEMY_DATABASE_URL}")

    if not SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
        # PostgreSQL Migration Flow
        logger.info("Initializing PostgreSQL schema using SQLAlchemy create_all()...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema synchronized successfully.")

        # Initialize default building using a session
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            building_count = session.query(models.Building).count()
            if building_count == 0:
                logger.info("Initializing default building in PostgreSQL...")
                default_building = models.Building(name="อาคารหลัก", description="อาคารหลักของหอพัก")
                session.add(default_building)
                session.commit()
                
                # Link existing rooms to the default building if any
                session.query(models.Room).update({models.Room.building_id: default_building.id})
                session.commit()
                logger.info("Default building initialized and rooms linked.")
            
            # Seed default system configs
            seed_system_configs(session)
        except Exception as e:
            session.rollback()
            logger.error(f"Error during default building initialization/seeding: {e}")
        finally:
            session.close()
        
        logger.info("PostgreSQL migration completed successfully.")
        return

    # SQLite Migration Flow (Backward Compatible)
    if "sqlite:///" in SQLALCHEMY_DATABASE_URL:
        db_path = SQLALCHEMY_DATABASE_URL.replace("sqlite:///", "")
        # Resolve path relative to current file if it's relative
        if not os.path.isabs(db_path) and not db_path.startswith("./") and not db_path.startswith(".\\"):
            db_path_resolved = os.path.abspath(db_path)
            if not os.path.exists(db_path_resolved):
                db_path_resolved = os.path.join(os.path.dirname(__file__), os.path.basename(db_path))
            db_path = db_path_resolved
    else:
        db_path = os.path.join(os.path.dirname(__file__), 'roomy.db')

    logger.info(f"Using SQLite database path: {db_path}")
    if not os.path.exists(db_path):
        logger.info(f"Database not found at {db_path}. Initializing new SQLite database...")
        from sqlalchemy import create_engine
        engine_sqlite = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine_sqlite)
        logger.info("New SQLite database schema created successfully.")

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
        "ALTER TABLE owners ADD COLUMN session_token TEXT",
        "ALTER TABLE owners ADD COLUMN address TEXT",
        
        # Invoices table
        "ALTER TABLE invoices ADD COLUMN late_fee REAL DEFAULT 0.0",
        
        # Tenants table
        "ALTER TABLE tenants ADD COLUMN status TEXT DEFAULT 'Active'", # Default to Active for existing tenants
        "ALTER TABLE tenants ADD COLUMN language TEXT DEFAULT 'th'",
        "ALTER TABLE tenants ADD COLUMN temp_building_id INTEGER REFERENCES buildings(id)",
        "ALTER TABLE tenants ADD COLUMN requested_move_in_date DATETIME",
        "ALTER TABLE tenants ADD COLUMN move_out_date DATETIME",
        "ALTER TABLE tenants ADD COLUMN move_out_reason TEXT",
        
        # Tenant History language
        "ALTER TABLE tenant_history ADD COLUMN language TEXT DEFAULT 'th'",
        
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
        "CREATE TABLE IF NOT EXISTS settlements (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, room_id INTEGER NOT NULL, lease_id INTEGER, settlement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, pro_rated_rent REAL DEFAULT 0.0, electricity_units REAL DEFAULT 0.0, electricity_amount REAL DEFAULT 0.0, water_units REAL DEFAULT 0.0, water_amount REAL DEFAULT 0.0, unpaid_invoices_amount REAL DEFAULT 0.0, cleaning_fee REAL DEFAULT 0.0, damage_fee REAL DEFAULT 0.0, other_fees REAL DEFAULT 0.0, total_deductions REAL DEFAULT 0.0, security_deposit_amount REAL DEFAULT 0.0, advance_rent_amount REAL DEFAULT 0.0, final_balance REAL DEFAULT 0.0, refund_method TEXT, refund_receipt_img TEXT, status TEXT DEFAULT 'Completed', notes TEXT, FOREIGN KEY (tenant_id) REFERENCES tenants(id), FOREIGN KEY (room_id) REFERENCES rooms(id), FOREIGN KEY (lease_id) REFERENCES leases(id))",
        "ALTER TABLE settlements ADD COLUMN advance_rent_amount REAL DEFAULT 0.0",

        # Room uniqueness migration (building-scoped)
        "DROP INDEX IF EXISTS ix_rooms_room_number",
        "CREATE UNIQUE INDEX IF NOT EXISTS _building_room_uc ON rooms (building_id, room_number)",

        # Foreign Key Optimization Indexes
        "CREATE INDEX IF NOT EXISTS ix_rooms_building_id ON rooms (building_id)",
        "CREATE INDEX IF NOT EXISTS ix_tenants_current_room_id ON tenants (current_room_id)",
        "CREATE INDEX IF NOT EXISTS ix_leases_room_id ON leases (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_leases_tenant_id ON leases (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_invoices_room_id ON invoices (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_invoices_tenant_id ON invoices (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_meter_readings_room_id ON meter_readings (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_residents_tenant_id ON residents (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_maintenance_requests_room_id ON maintenance_requests (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_maintenance_requests_tenant_id ON maintenance_requests (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_room_payment_channels_room_id ON room_payment_channels (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_room_assets_room_id ON room_assets (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_move_out_requests_tenant_id ON move_out_requests (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_move_out_requests_room_id ON move_out_requests (room_id)",
        "CREATE INDEX IF NOT EXISTS ix_settlements_tenant_id ON settlements (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_settlements_room_id ON settlements (room_id)",

        # New: users table for Google OAuth & Multi-role Staff
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, full_name TEXT, role TEXT NOT NULL DEFAULT 'Admin', status TEXT DEFAULT 'Active', session_token TEXT UNIQUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)",
        "CREATE INDEX IF NOT EXISTS ix_users_session_token ON users (session_token)"
    ]
    
    for cmd in commands:
        try:
            logger.info(f"Executing: {cmd}")
            curr.execute(cmd)
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                logger.info("  Column already exists, skipping.")
            elif "no such index" in str(e).lower():
                logger.info("  Index not found, skipping.")
            else:
                logger.warning(f"  Error: {e}")

    # Initialization: Create default building and link rooms if needed
    curr.execute("SELECT COUNT(*) FROM buildings")
    if curr.fetchone()[0] == 0:
        logger.info("Initializing default building...")
        curr.execute("INSERT INTO buildings (name, description) VALUES (?, ?)", ("อาคารหลัก", "อาคารหลักของหอพัก"))
        default_building_id = curr.lastrowid
        curr.execute("UPDATE rooms SET building_id = ?", (default_building_id,))
                
    conn.commit()
    conn.close()
    
    # Open a SQLAlchemy session to seed default system configs for SQLite
    from models.database import engine as engine_sqlite, Base
    # Automatically create any new tables (like application_logs) in SQLite
    Base.metadata.create_all(bind=engine_sqlite)
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine_sqlite)
    session = Session()
    try:
        seed_system_configs(session)
    except Exception as e:
        session.rollback()
        logger.error(f"Error seeding system configs in SQLite: {e}")
    finally:
        session.close()

    logger.info("Migration completed.")

if __name__ == "__main__":
    migrate()
