from sqlalchemy.orm import Session
import models
import logging

logger = logging.getLogger(__name__)

def log_activity(db: Session, actor: str, action: str, target: str, details: str = None):
    """
    Saves an activity log entry to the database.
    """
    try:
        log_entry = models.ApplicationLog(
            actor=actor,
            action=action,
            target=target,
            details=details
        )
        db.add(log_entry)
        db.commit()
        logger.info(f"Activity Logged - Actor: {actor}, Action: {action}, Target: {target}")
        return log_entry
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to write activity log: {e}")
        return None
