import os
import json
import logging
import requests
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

# Configure logging
logger = logging.getLogger(__name__)

# Load env from src/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI()

# Important: directory is relative to where you run uvicorn
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Multi-language support
translations = {}
for lang in ["en", "th", "jp"]:
    try:
        with open(os.path.join(os.path.dirname(__file__), f"i18n/{lang}.json"), "r", encoding="utf-8") as f:
            translations[lang] = json.load(f)
    except Exception as e:
        logger.error(f"Error loading {lang} translation: {e}")
        translations[lang] = {}

def get_text(key, lang="th"):
    return translations.get(lang, translations.get("th", {})).get(key, key)

templates.env.globals['get_text'] = get_text

def from_json(value):
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
templates.env.filters['from_json'] = from_json

# Fetch configurations from Database (with .env fallback)
def load_db_configs():
    # If testing, always use env
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("TESTING"):
        return {
            "LINE_ADMIN_CHANNEL_ACCESS_TOKEN": os.getenv("LINE_ADMIN_CHANNEL_ACCESS_TOKEN"),
            "LINE_ADMIN_CHANNEL_SECRET": os.getenv("LINE_ADMIN_CHANNEL_SECRET"),
            "LINE_TENANT_CHANNEL_ACCESS_TOKEN": os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN"),
            "LINE_TENANT_CHANNEL_SECRET": os.getenv("LINE_TENANT_CHANNEL_SECRET"),
            "LINE_NOTIFY_TOKEN": os.getenv("LINE_NOTIFY_TOKEN", ""),
            "BASE_URL": (os.getenv("BASE_URL") or "http://localhost:8000").rstrip("/"),
            "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", "roomy+-*/()[]")
        }

    from models.database import SessionLocal
    from services.security import get_system_config

    db = SessionLocal()
    try:
        # These will try DB first, then .env via security.get_system_config
        return {
            "LINE_ADMIN_CHANNEL_ACCESS_TOKEN": get_system_config(db, "LINE_ADMIN_CHANNEL_ACCESS_TOKEN"),
            "LINE_ADMIN_CHANNEL_SECRET": get_system_config(db, "LINE_ADMIN_CHANNEL_SECRET"),
            "LINE_TENANT_CHANNEL_ACCESS_TOKEN": get_system_config(db, "LINE_TENANT_CHANNEL_ACCESS_TOKEN"),
            "LINE_TENANT_CHANNEL_SECRET": get_system_config(db, "LINE_TENANT_CHANNEL_SECRET"),
            "LINE_NOTIFY_TOKEN": get_system_config(db, "LINE_NOTIFY_TOKEN", ""),
            "BASE_URL": get_system_config(db, "BASE_URL", "http://localhost:8000").rstrip("/"),
            "ADMIN_PASSWORD": get_system_config(db, "ADMIN_PASSWORD", "roomy+-*/()[]")
        }
    except Exception as e:
        # Fallback to env if DB is not ready or table missing
        return {
            "LINE_ADMIN_CHANNEL_ACCESS_TOKEN": os.getenv("LINE_ADMIN_CHANNEL_ACCESS_TOKEN"),
            "LINE_ADMIN_CHANNEL_SECRET": os.getenv("LINE_ADMIN_CHANNEL_SECRET"),
            "LINE_TENANT_CHANNEL_ACCESS_TOKEN": os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN"),
            "LINE_TENANT_CHANNEL_SECRET": os.getenv("LINE_TENANT_CHANNEL_SECRET"),
            "LINE_NOTIFY_TOKEN": os.getenv("LINE_NOTIFY_TOKEN", ""),
            "BASE_URL": (os.getenv("BASE_URL") or "http://localhost:8000").rstrip("/"),
            "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", "roomy+-*/()[]")
        }
    finally:
        db.close()

# LINE SDK v3 imports
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob
)

# Global bot instances
configs = {}
LINE_ADMIN_CHANNEL_ACCESS_TOKEN = None
LINE_ADMIN_CHANNEL_SECRET = None
LINE_TENANT_CHANNEL_ACCESS_TOKEN = None
LINE_TENANT_CHANNEL_SECRET = None
LINE_NOTIFY_TOKEN = None
BASE_URL = None
ADMIN_PASSWORD = None

admin_bot_api = None
admin_bot_blob_api = None
admin_handler = None
tenant_bot_api = None
tenant_bot_blob_api = None
tenant_handler = None
line_bot_api = None

def refresh_configs():
    global configs, LINE_ADMIN_CHANNEL_ACCESS_TOKEN, LINE_ADMIN_CHANNEL_SECRET
    global LINE_TENANT_CHANNEL_ACCESS_TOKEN, LINE_TENANT_CHANNEL_SECRET
    global LINE_NOTIFY_TOKEN, BASE_URL, ADMIN_PASSWORD
    global admin_bot_api, admin_bot_blob_api, admin_handler, tenant_bot_api, tenant_bot_blob_api, tenant_handler, line_bot_api

    configs = load_db_configs()

    # LINE Credentials
    LINE_ADMIN_CHANNEL_ACCESS_TOKEN = configs["LINE_ADMIN_CHANNEL_ACCESS_TOKEN"]
    LINE_ADMIN_CHANNEL_SECRET = configs["LINE_ADMIN_CHANNEL_SECRET"]
    LINE_TENANT_CHANNEL_ACCESS_TOKEN = configs["LINE_TENANT_CHANNEL_ACCESS_TOKEN"]
    LINE_TENANT_CHANNEL_SECRET = configs["LINE_TENANT_CHANNEL_SECRET"]

    LINE_NOTIFY_TOKEN = configs["LINE_NOTIFY_TOKEN"]
    BASE_URL = configs["BASE_URL"]
    ADMIN_PASSWORD = configs["ADMIN_PASSWORD"]

    # Admin Channel
    if LINE_ADMIN_CHANNEL_ACCESS_TOKEN:
        admin_config = Configuration(access_token=LINE_ADMIN_CHANNEL_ACCESS_TOKEN)
        admin_api_client = ApiClient(admin_config)
        admin_bot_api = MessagingApi(admin_api_client)
        admin_bot_blob_api = MessagingApiBlob(admin_api_client)
    else:
        admin_bot_api = None
        admin_bot_blob_api = None
    
    if admin_handler is None:
        admin_handler = WebhookHandler(LINE_ADMIN_CHANNEL_SECRET) if LINE_ADMIN_CHANNEL_SECRET else None
    elif LINE_ADMIN_CHANNEL_SECRET:
        admin_handler.channel_secret = LINE_ADMIN_CHANNEL_SECRET

    # Tenant Channel
    if LINE_TENANT_CHANNEL_ACCESS_TOKEN:
        tenant_config = Configuration(access_token=LINE_TENANT_CHANNEL_ACCESS_TOKEN)
        tenant_api_client = ApiClient(tenant_config)
        tenant_bot_api = MessagingApi(tenant_api_client)
        tenant_bot_blob_api = MessagingApiBlob(tenant_api_client)
    else:
        tenant_bot_api = None
        tenant_bot_blob_api = None
        
    if tenant_handler is None:
        tenant_handler = WebhookHandler(LINE_TENANT_CHANNEL_SECRET) if LINE_TENANT_CHANNEL_SECRET else None
    elif LINE_TENANT_CHANNEL_SECRET:
        tenant_handler.channel_secret = LINE_TENANT_CHANNEL_SECRET

    # Compatibility shim
    line_bot_api = tenant_bot_api

# Initial load
refresh_configs()

def send_line_notify(message: str):
    if not LINE_NOTIFY_TOKEN:
        logger.info(f"LINE NOTIFY (Mock): {message}")
        return
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"}
    data = {"message": message}
    try:
        requests.post(url, headers=headers, data=data)
    except Exception as e:
        logger.error(f"Error sending LINE Notify: {e}")

# Helper for LINE Bot to generate magic links
def get_magic_url(owner, db, path=""):
    import secrets
    from datetime import datetime, timedelta
    token = secrets.token_urlsafe(16)
    owner.magic_token = token
    owner.magic_token_expires = datetime.now() + timedelta(minutes=owner.magic_link_duration_min or 5)
    db.commit()
    
    url = f"{BASE_URL}/admin/magic-login?token={token}"
    return url
