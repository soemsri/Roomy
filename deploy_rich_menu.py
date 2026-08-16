import os
import sys
import json
import requests
import logging

sys.path.insert(0, '/var/www/sukanan/src')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), 'src')))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 1. Generate Image
from create_3btn_richmenu import create_3btn_rich_menu
image_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'src', 'tenant_richmenu.jpg'))
create_3btn_rich_menu(image_path)

# 2. Get LINE Token
import database
import models
from services.security import get_system_config

db = database.SessionLocal()
token = get_system_config(db, "LINE_TENANT_CHANNEL_ACCESS_TOKEN")
db.close()

if not token:
    token = os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN")

if not token:
    logger.error("Could not find LINE_TENANT_CHANNEL_ACCESS_TOKEN")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json"
}

# 3. Clean up existing Rich Menus
logger.info("Listing existing rich menus...")
res = requests.get("https://api.line.me/v2/bot/richmenu/list", headers={"Authorization": f"Bearer {token}"})
if res.status_code == 200:
    menus = res.json().get("richmenus", [])
    logger.info(f"Found {len(menus)} existing menus. Deleting...")
    for m in menus:
        mid = m["richMenuId"]
        del_res = requests.delete(f"https://api.line.me/v2/bot/richmenu/{mid}", headers={"Authorization": f"Bearer {token}"})
        logger.info(f"Deleted {mid}: {del_res.status_code}")

# 4. Create New 3-Button Rich Menu
rich_menu_data = {
    "size": {"width": 2500, "height": 843},
    "selected": True,
    "name": "Tenant 3-Button Menu",
    "chatBarText": "เมนู",
    "areas": [
        {
            "bounds": {"x": 0, "y": 0, "width": 833, "height": 843},
            "action": {
                "type": "postback",
                "data": "action=chat",
                "inputOption": "openKeyboard",
                "displayText": "สนทนา"
            }
        },
        {
            "bounds": {"x": 833, "y": 0, "width": 834, "height": 843},
            "action": {
                "type": "message",
                "text": "กฎระเบียบ"
            }
        },
        {
            "bounds": {"x": 1667, "y": 0, "width": 833, "height": 843},
            "action": {
                "type": "message",
                "text": "จองห้องพัก"
            }
        }
    ]
}

logger.info("Creating new 3-button rich menu...")
res = requests.post("https://api.line.me/v2/bot/richmenu", headers=headers, json=rich_menu_data)
if res.status_code not in [200, 201]:
    logger.error(f"Failed to create rich menu: {res.status_code} - {res.text}")
    sys.exit(1)

rich_menu_id = res.json()["richMenuId"]
logger.info(f"Created Rich Menu ID: {rich_menu_id}")

# 5. Upload Image
logger.info(f"Uploading rich menu image from {image_path}...")
with open(image_path, "rb") as f:
    img_res = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/jpeg"
        },
        data=f
    )
logger.info(f"Image upload status: {img_res.status_code} - {img_res.text}")

# 6. Set as Default Rich Menu for all users
def_res = requests.post(
    f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
    headers={"Authorization": f"Bearer {token}"}
)
logger.info(f"Set as Default Rich Menu status: {def_res.status_code} - {def_res.text}")

# 7. Clear rich_menu_id in database so nobody is locked to an old menu
db = database.SessionLocal()
db.query(models.Tenant).update({models.Tenant.rich_menu_id: rich_menu_id})
db.commit()
db.close()

print(f"SUCCESS: New 3-Button Rich Menu ID {rich_menu_id} is now ACTIVE as default!")
