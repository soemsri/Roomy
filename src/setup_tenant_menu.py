
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

def create_tenant_rich_menu():
    # 1. Define the Rich Menu Structure
    rich_menu_data = {
        "size": {"width": 2500, "height": 1686},
        "selected": True, # Make it default
        "name": "Tenant Professional Menu",
        "chatBarText": "เมนูผู้เช่า",
        "areas": [
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "ดูค่าเช่า"}},
            {"bounds": {"x": 833, "y": 0, "width": 834, "height": 843}, "action": {"type": "message", "text": "แจ้งซ่อม"}},
            {"bounds": {"x": 1667, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "ประวัติ"}},
            {"bounds": {"x": 0, "y": 843, "width": 833, "height": 843}, "action": {"type": "message", "text": "สนทนา"}},
            {"bounds": {"x": 833, "y": 843, "width": 834, "height": 843}, "action": {"type": "message", "text": "ย้ายเข้า"}},
            {"bounds": {"x": 1667, "y": 843, "width": 833, "height": 843}, "action": {"type": "message", "text": "ย้ายออก"}}
        ]
    }

    # 2. Create Rich Menu
    res = requests.post("https://api.line.me/v2/bot/richmenu", headers=HEADERS, data=json.dumps(rich_menu_data))
    if res.status_code not in [200, 201]:
        print("Error creating rich menu:", res.text)
        return None
    
    rich_menu_id = res.json()["richMenuId"]
    print(f"Successfully created Rich Menu ID: {rich_menu_id}")

    # 3. Upload Image
    image_path = "tenant_richmenu.png"
    with open(image_path, "rb") as f:
        img_res = requests.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={
                "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "image/png"
            },
            data=f
        )
    print("Image upload status:", img_res.status_code)
    
    # 4. Set as Default Rich Menu
    def_res = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{rich_menu_id}",
        headers=HEADERS
    )
    print("Set default status:", def_res.status_code)
    
    return rich_menu_id

def delete_all_rich_menus():
    # List all rich menus
    res = requests.get("https://api.line.me/v2/bot/richmenu/list", headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"})
    if res.status_code == 200:
        menus = res.json().get("richmenus", [])
        for m in menus:
            mid = m["richMenuId"]
            requests.delete(f"https://api.line.me/v2/bot/richmenu/{mid}", headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"})
            print(f"Deleted old rich menu: {mid}")

if __name__ == "__main__":
    print("Cleaning up old rich menus for Tenant channel...")
    delete_all_rich_menus()
    menu_id = create_tenant_rich_menu()
    if menu_id:
        print(f"Tenant Rich Menu setup complete. ID: {menu_id}")
