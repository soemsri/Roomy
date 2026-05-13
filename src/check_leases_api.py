import os
import sys
from datetime import datetime

# Ensure src is in path
sys.path.append(os.path.dirname(__file__))

from database import SessionLocal
import models

def check_leases():
    db = SessionLocal()
    try:
        leases = db.query(models.Lease).all()
        print(f"Total leases found in DB: {len(leases)}")
        
        for l in leases:
            print(f"\nProcessing Lease ID: {l.id}")
            print(f"Room ID: {l.room_id}")
            print(f"Tenant ID: {l.tenant_id}")
            print(f"Start Date: {l.start_date} (Type: {type(l.start_date)})")
            
            try:
                room_no = l.room.room_number if l.room else "N/A"
                tenant_name = l.tenant.full_name if l.tenant else "N/A"
                
                # Check if it's a string instead of datetime
                s_date = l.start_date
                if isinstance(s_date, str):
                    # Try to parse it
                    try:
                        s_date = datetime.fromisoformat(s_date.split('.')[0])
                    except:
                        s_date = None
                
                date_str = s_date.strftime("%d/%m/%Y") if s_date else "-"
                
                print(f"Success! Room: {room_no}, Tenant: {tenant_name}, Date: {date_str}")
            except Exception as e:
                print(f"FAILED for Lease {l.id}: {e}")
                
    finally:
        db.close()

if __name__ == "__main__":
    check_leases()
