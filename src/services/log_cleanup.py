import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
import models
from services.activity import log_activity

logger = logging.getLogger(__name__)

last_cleanup_time = None

def prune_logs(db: Session, policy: str):
    """
    Prunes ApplicationLog entries based on the retention policy.
    Allowed policies: "forever", "1_day", "1_week", "1_month", "1_year"
    """
    if not policy or policy == "forever":
        return 0

    now = datetime.now()
    if policy == "1_day":
        cutoff = now - timedelta(days=1)
    elif policy == "1_week":
        cutoff = now - timedelta(weeks=1)
    elif policy == "1_month":
        cutoff = now - timedelta(days=30)
    elif policy == "1_year":
        cutoff = now - timedelta(days=365)
    else:
        logger.warning(f"Unknown log retention policy: {policy}")
        return 0

    try:
        deleted_count = db.query(models.ApplicationLog).filter(models.ApplicationLog.timestamp < cutoff).delete()
        db.commit()
        if deleted_count > 0:
            logger.info(f"Pruned {deleted_count} logs older than {cutoff} based on policy '{policy}'.")
            log_activity(
                db=db,
                actor="System (Log Cleanup)",
                action="Prune Logs",
                target="Database",
                details=f"Pruned {deleted_count} logs older than {cutoff.strftime('%Y-%m-%d %H:%M:%S')} based on policy '{policy}'"
            )
        return deleted_count
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to prune logs: {e}")
        return 0

def run_scheduled_log_cleanup():
    global last_cleanup_time
    from models.database import SessionLocal
    from services.security import get_system_config
    
    now = datetime.now()
    # Run once every hour to keep database performance optimal
    if last_cleanup_time and (now - last_cleanup_time).total_seconds() < 3600:
        return
        
    db = SessionLocal()
    try:
        policy = get_system_config(db, "LOG_RETENTION_POLICY", default="forever")
        if policy and policy != "forever":
            prune_logs(db, policy)
        last_cleanup_time = now
    except Exception as e:
        logger.error(f"Error in scheduled log cleanup: {e}")
    finally:
        db.close()
