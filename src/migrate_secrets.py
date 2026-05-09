import os
from database import SessionLocal
import security
import models
from dotenv import load_dotenv

load_dotenv()

def migrate():
    db = SessionLocal()
    
    # List of keys to migrate
    keys_to_migrate = [
        "LINE_ADMIN_CHANNEL_ACCESS_TOKEN",
        "LINE_ADMIN_CHANNEL_SECRET",
        "LINE_TENANT_CHANNEL_ACCESS_TOKEN",
        "LINE_TENANT_CHANNEL_SECRET",
        "LINE_NOTIFY_TOKEN",
        "BASE_URL",
        "ADMIN_PASSWORD"
    ]
    
    print("Starting migration of .env secrets to database...")
    
    for key in keys_to_migrate:
        val = os.getenv(key)
        if val:
            print(f"Migrating {key}...")
            security.set_system_config(db, key, val, description=f"Migrated from .env")
        else:
            print(f"Skipping {key} (not found in .env)")
            
    db.close()
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
