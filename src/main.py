import os
import sys
import types
import logging
from fastapi import FastAPI, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("app.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# Import shared config and routes
import config
from config import app, templates, ADMIN_PASSWORD
import models
from models.database import get_db

# Import routers from our controllers
import controllers.callback as callback
import controllers.tenant as tenant
import controllers.admin as admin

# Expose key callback handlers and dependencies for legacy imports (e.g. tests)
from controllers.callback import handle_admin_message, handle_tenant_message
from controllers.admin import get_admin, get_super_admin

# Static files mount
uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Include the routers!
app.include_router(callback.router)
app.include_router(tenant.router)
app.include_router(admin.router)

from starlette.responses import JSONResponse
import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception handler caught: {exc}", exc_info=True)
    from models.database import SessionLocal
    from services.activity import log_activity
    import models
    
    db = SessionLocal()
    try:
        actor = "System (Error Handler)"
        try:
            admin_session = request.cookies.get("admin_session")
            if admin_session:
                user = db.query(models.User).filter(models.User.session_token == admin_session, models.User.status == "Active").first()
                if user:
                    actor = user.email
                else:
                    owner = db.query(models.Owner).filter(models.Owner.session_token == admin_session).first()
                    if owner:
                        actor = "legacy_owner@system.local"
        except Exception:
            pass
            
        tb = traceback.format_exc()
        details = f"Error Type: {type(exc).__name__}\nMessage: {str(exc)}\nTraceback:\n{tb[:1000]}"
        
        log_activity(
            db=db,
            actor=actor,
            action="Application Error",
            target=request.url.path,
            details=details
        )
    except Exception as log_err:
        logger.error(f"Failed to log unhandled exception to database: {log_err}")
    finally:
        db.close()
        
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"}
    )


import threading
import time

def start_backup_scheduler():
    def scheduler_loop():
        logger.info("Database backup scheduler thread started.")
        time.sleep(10)  # Wait for application to settle
        while True:
            try:
                import services.backup as backup_service
                backup_service.run_scheduled_backup()
            except Exception as e:
                logger.error(f"Error in backup scheduler loop: {e}")
            time.sleep(60)

    thread = threading.Thread(target=scheduler_loop, daemon=True, name="BackupScheduler")
    thread.start()

@app.on_event("startup")
async def startup_event():
    start_backup_scheduler()

@app.get("/")
async def root():
    return {"message": "SukAnan Apartment API is running"}

@app.post("/setup/demo")
async def setup_demo(db: Session = Depends(get_db)):
    if not db.query(models.Owner).first():
        db.add(models.Owner(line_user_id="Uf471c296504bb803caa0d0a83ea0b4f6", display_name="Owner", promptpay_config='["0812345678"]'))
    rooms = [{"room_number": "A101", "base_rent": 3500, "electricity_rate": 8, "water_rate": 18},
             {"room_number": "A102", "base_rent": 3500, "electricity_rate": 8, "water_rate": 18}]
    for r in rooms:
        if not db.query(models.Room).filter(models.Room.room_number == r["room_number"]).first():
            db.add(models.Room(**r))
    db.commit()
    return {"status": "Demo setup complete"}

# Metaclass/Subclass mapping for backward compatibility and test monkeypatching
class MainModule(types.ModuleType):
    @property
    def tenant_bot_api(self):
        return config.tenant_bot_api
    @tenant_bot_api.setter
    def tenant_bot_api(self, value):
        config.tenant_bot_api = value
        config.line_bot_api = value
    @tenant_bot_api.deleter
    def tenant_bot_api(self):
        config.tenant_bot_api = None

    @property
    def admin_bot_api(self):
        return config.admin_bot_api
    @admin_bot_api.setter
    def admin_bot_api(self, value):
        config.admin_bot_api = value
    @admin_bot_api.deleter
    def admin_bot_api(self):
        config.admin_bot_api = None

    @property
    def line_bot_api(self):
        return config.line_bot_api
    @line_bot_api.setter
    def line_bot_api(self, value):
        config.line_bot_api = value
    @line_bot_api.deleter
    def line_bot_api(self):
        config.line_bot_api = None

    @property
    def BASE_URL(self):
        return config.BASE_URL
    @BASE_URL.setter
    def BASE_URL(self, value):
        config.BASE_URL = value
    @BASE_URL.deleter
    def BASE_URL(self):
        config.BASE_URL = None

    @property
    def send_line_notify(self):
        return config.send_line_notify
    @send_line_notify.setter
    def send_line_notify(self, value):
        config.send_line_notify = value
    @send_line_notify.deleter
    def send_line_notify(self):
        # Restore the original config send_line_notify function if needed
        # Or just import the real function
        from config import send_line_notify as original_send_line_notify
        config.send_line_notify = original_send_line_notify

# Apply class modification to module
sys.modules[__name__].__class__ = MainModule

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
