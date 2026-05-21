import os
import logging
from database import SessionLocal
import security
import models
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
    
    logger.info("Starting migration of .env secrets to database...")
    
    for key in keys_to_migrate:
        val = os.getenv(key)
        if val:
            logger.info(f"Migrating {key}...")
            security.set_system_config(db, key, val, description=f"Migrated from .env")
        else:
            logger.info(f"Skipping {key} (not found in .env)")
            
    db.close()
    logger.info("Migration complete.")

if __name__ == "__main__":
    migrate()
