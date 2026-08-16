import os
import shutil
import json
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from models.database import SQLALCHEMY_DATABASE_URL, engine, Base, SessionLocal
import models

logger = logging.getLogger(__name__)

# backups directory in project root
BACKUPS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backups"))

def ensure_backups_dir():
    if not os.path.exists(BACKUPS_DIR):
        os.makedirs(BACKUPS_DIR)
        logger.info(f"Created backups directory at: {BACKUPS_DIR}")

def get_db_path(db_session=None):
    """Extract SQLite DB file path if using SQLite."""
    db_url = SQLALCHEMY_DATABASE_URL
    if db_session:
        try:
            bind = db_session.get_bind()
            if bind and hasattr(bind, "url"):
                db_url = str(bind.url)
        except Exception:
            pass

    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite:///", "")
        if not os.path.isabs(db_path) and not db_path.startswith("./") and not db_path.startswith(".\\"):
            # Resolve relative path from src/models/database.py location or project root
            db_path_resolved = os.path.abspath(db_path)
            if not os.path.exists(db_path_resolved):
                db_path_resolved = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", db_path))
            return db_path_resolved
        return os.path.abspath(db_path)
    return None

def create_backup(db_session=None):
    """
    Creates a database backup.
    - If SQLite: Copies the .db file.
    - If PostgreSQL: Dumps all table rows into a structured JSON file in dependency order.
    """
    ensure_backups_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    close_session_at_end = False
    if db_session is None:
        db_session = SessionLocal()
        close_session_at_end = True

    try:
        if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
            # SQLite File Copy Backup
            db_path = get_db_path(db_session)
            if not db_path or not os.path.exists(db_path):
                # Fallback search in src/
                db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "roomy.db"))
                if not os.path.exists(db_path):
                    db_path = os.path.abspath("roomy.db")
            
            filename = f"backup_{timestamp}.db"
            dest_path = os.path.join(BACKUPS_DIR, filename)
            
            # Close session temporarily to release lock
            if close_session_at_end:
                db_session.close()
                close_session_at_end = False
                
            shutil.copy2(db_path, dest_path)
            logger.info(f"SQLite database backup created: {filename}")
            return filename
        else:
            # PostgreSQL JSON Table Dump Backup
            filename = f"backup_{timestamp}.json"
            dest_path = os.path.join(BACKUPS_DIR, filename)
            
            backup_data = {
                "metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "database_type": "postgresql",
                    "version": "1.0"
                },
                "data": {}
            }
            
            # Retrieve tables in dependency order
            tables = Base.metadata.sorted_tables
            for table in tables:
                logger.info(f"Backing up table: {table.name}")
                rows = db_session.execute(table.select()).fetchall()
                # Convert rows to dicts
                rows_list = [dict(row._mapping) for row in rows]
                
                # Format datetimes to ISO strings for JSON serialization
                for row_dict in rows_list:
                    for k, v in row_dict.items():
                        if isinstance(v, datetime):
                            row_dict[k] = v.isoformat()
                            
                backup_data["data"][table.name] = rows_list
                
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
                
            logger.info(f"PostgreSQL database backup created: {filename}")
            return filename
    except Exception as e:
        logger.error(f"Failed to create database backup: {e}")
        raise e
    finally:
        if close_session_at_end:
            db_session.close()

def restore_backup(db_session, filename):
    """
    Restores the database from a backup file.
    - If SQLite: Replaces roomy.db file.
    - If PostgreSQL: Clears and imports table data from JSON structure in dependency order.
    """
    ensure_backups_dir()
    src_path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Backup file not found: {filename}")

    try:
        if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
            # SQLite File Restore
            if not filename.endswith(".db"):
                raise ValueError("SQLite restore requires a .db backup file.")
                
            db_path = get_db_path(db_session)
            if not db_path:
                db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "roomy.db"))
            
            # Close session/connections before restoring file
            db_session.close()
            engine.dispose()
            
            shutil.copy2(src_path, db_path)
            logger.info(f"SQLite database restored successfully from: {filename}")
            return True
        else:
            # PostgreSQL JSON Table Restore
            if not filename.endswith(".json"):
                raise ValueError("PostgreSQL restore requires a .json backup file.")
                
            with open(src_path, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
                
            tables = Base.metadata.sorted_tables
            
            # Delete existing tables in reverse dependency order
            logger.info("Clearing PostgreSQL database tables in reverse dependency order...")
            for table in reversed(tables):
                db_session.execute(table.delete())
            db_session.commit()
            
            # Restore tables in dependency order
            logger.info("Importing table data in dependency order...")
            for table in tables:
                rows = backup_data["data"].get(table.name, [])
                if not rows:
                    continue
                    
                logger.info(f"Restoring {len(rows)} rows into {table.name}...")
                for row_dict in rows:
                    # Convert ISO datetime strings back to datetime objects or let SQLAlchemy handle it
                    # SQLAlchemy normally handles ISO strings for DateTime columns natively, but we parse them defensively if needed
                    db_session.execute(table.insert().values(**row_dict))
            
            db_session.commit()
            logger.info(f"PostgreSQL database restored successfully from: {filename}")
            return True
    except Exception as e:
        db_session.rollback()
        logger.error(f"Failed to restore database from backup {filename}: {e}")
        raise e

def get_backups_list():
    """Returns a sorted list of backups in backups/ folder."""
    ensure_backups_dir()
    backups = []
    for file in os.listdir(BACKUPS_DIR):
        if file.startswith("backup_") and (file.endswith(".db") or file.endswith(".json")):
            file_path = os.path.join(BACKUPS_DIR, file)
            stat = os.stat(file_path)
            
            # Parse timestamp from filename
            try:
                parts = file.replace("backup_", "").split(".")[0].split("_")
                dt = datetime.strptime(f"{parts[0]}{parts[1]}", "%Y%m%d%H%M%S")
                formatted_date = dt.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                formatted_date = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M:%S")
                
            backups.append({
                "filename": file,
                "size_bytes": stat.st_size,
                "size_formatted": format_size(stat.st_size),
                "created_at": formatted_date,
                "mtime": stat.st_mtime
            })
            
    # Sort backups descending by modification time (newest first)
    backups.sort(key=lambda x: x["mtime"], reverse=True)
    return backups

def delete_backup_file(filename):
    """Deletes a backup file by filename."""
    ensure_backups_dir()
    file_path = os.path.join(BACKUPS_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"Deleted backup file: {filename}")
        return True
    return False

def format_size(size_bytes):
    """Formats size in bytes into human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"

def prune_backups(max_backups):
    """Keeps only the newest max_backups files and deletes the rest."""
    backups = get_backups_list()
    if len(backups) > max_backups:
        to_delete = backups[max_backups:]
        logger.info(f"Pruning {len(to_delete)} old backups...")
        for backup in to_delete:
            delete_backup_file(backup["filename"])

def run_scheduled_backup():
    """
    Called by background thread to verify and run scheduled backups.
    Configuration is retrieved from the `system_configs` table in database.
    """
    db = SessionLocal()
    try:
        # Load configuration from Database
        from services.security import get_system_config, set_system_config
        config_str = get_system_config(db, "BACKUP_SCHEDULE_CONFIG")
        
        if not config_str:
            # Default fallback configuration
            config = {
                "frequency": "disabled",
                "time": "02:00",
                "max_backups": 10,
                "last_run": None
            }
            # Save it so it's initialized
            set_system_config(db, "BACKUP_SCHEDULE_CONFIG", json.dumps(config), description="Database Backup Schedule Config")
        else:
            try:
                config = json.loads(config_str)
            except Exception:
                return

        frequency = config.get("frequency", "disabled")
        if frequency == "disabled":
            return

        scheduled_time_str = config.get("time", "02:00")
        max_backups = int(config.get("max_backups", 10))
        last_run_str = config.get("last_run")
        
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        
        # Parse scheduled hour and minute
        try:
            sched_h, sched_m = map(int, scheduled_time_str.split(":"))
        except Exception:
            return

        # Check if it's the scheduled time (matching hour and minute)
        if now.hour == sched_h and now.minute == sched_m:
            last_run_dt = None
            if last_run_str:
                try:
                    last_run_dt = datetime.fromisoformat(last_run_str)
                except Exception:
                    pass

            is_due = False
            if not last_run_dt:
                is_due = True
            else:
                diff = now - last_run_dt
                if frequency == "daily" and diff.total_seconds() > 20 * 3600:
                    # Over 20 hours since last run
                    is_due = True
                elif frequency == "weekly" and diff.days >= 7:
                    is_due = True
                elif frequency == "monthly" and diff.days >= 28:
                    is_due = True

            if is_due:
                logger.info(f"Triggering scheduled database backup ({frequency})...")
                filename = create_backup(db)
                prune_backups(max_backups)
                
                # Update last run timestamp
                config["last_run"] = now.isoformat()
                set_system_config(db, "BACKUP_SCHEDULE_CONFIG", json.dumps(config), description="Database Backup Schedule Config")
                logger.info(f"Scheduled backup completed successfully: {filename}")
    except Exception as e:
        logger.error(f"Error executing scheduled backup: {e}")
    finally:
        db.close()
