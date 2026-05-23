import os
import sys
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ensure src is in path
sys.path.append(os.path.dirname(__file__))

from database import SessionLocal
import models
from utils import parse_sqlite_datetime

def check_leases():
    db = SessionLocal()
    try:
        leases = db.query(models.Lease).all()
        logger.info(f"Total leases found in DB: {len(leases)}")
        
        for l in leases:
            logger.info(f"\nProcessing Lease ID: {l.id}")
            logger.info(f"Room ID: {l.room_id}")
            logger.info(f"Tenant ID: {l.tenant_id}")
            logger.info(f"Start Date: {l.start_date} (Type: {type(l.start_date)})")
            
            try:
                room_no = l.room.room_number if l.room else "N/A"
                tenant_name = l.tenant.full_name if l.tenant else "N/A"
                
                # Check if it's a string instead of datetime
                s_date = parse_sqlite_datetime(l.start_date)
                
                date_str = s_date.strftime("%d/%m/%Y") if s_date else "-"
                
                logger.info(f"Success! Room: {room_no}, Tenant: {tenant_name}, Date: {date_str}")
            except Exception as e:
                logger.error(f"FAILED for Lease {l.id}: {e}")
                
    finally:
        db.close()

if __name__ == "__main__":
    check_leases()
