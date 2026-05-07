import os
import json
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_ADMIN_CHANNEL_ACCESS_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

def create_owner_rich_menu():
    # 1. Define the Rich Menu Structure
    rich_menu_data = {
        "size": {"width": 2500, "height": 1686},
        "selected": False,
        "name": "Owner Professional Menu",
        "chatBarText": "เมนูเจ้าของหอพัก",
        "areas": [
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "ผังห้อง"}},
            {"bounds": {"x": 833, "y": 0, "width": 834, "height": 843}, "action": {"type": "message", "text": "จดมิเตอร์"}},
            {"bounds": {"x": 1667, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "สรุปรายรับ"}},
            {"bounds": {"x": 0, "y": 843, "width": 833, "height": 843}, "action": {"type": "message", "text": "จัดการสัญญา"}},
            {"bounds": {"x": 833, "y": 843, "width": 834, "height": 843}, "action": {"type": "message", "text": "ตั้งค่า"}},
            {"bounds": {"x": 1667, "y": 843, "width": 833, "height": 843}, "action": {"type": "message", "text": "รายการแจ้งซ่อม"}}
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
    image_path = "image/ownerrichmenu.jpg"
    with open(image_path, "rb") as f:
        img_res = requests.post(
            f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
            headers={
                "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
                "Content-Type": "image/jpeg"
            },
            data=f
        )
    print("Image upload status:", img_res.status_code)
    
    return rich_menu_id

def link_to_user(user_id, rich_menu_id):
    # 4. Link Rich Menu to specific User ID
    user_id = user_id.strip()
    link_res = requests.post(
        f"https://api.line.me/v2/bot/user/{user_id}/richmenu/{rich_menu_id}",
        headers=HEADERS
    )
    if link_res.status_code == 200:
        print(f"Successfully linked Rich Menu to User: {user_id}")
    else:
        print("Error linking rich menu:", link_res.text)
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
    import sys
    from database import SessionLocal
    import models

    # Check if ID passed as argument
    manual_id = sys.argv[1] if len(sys.argv) > 1 else None

    if manual_id:
        print("Cleaning up old rich menus...")
        delete_all_rich_menus()

        db = SessionLocal()
        # ...

        # Create or Update Owner
        owner = db.query(models.Owner).first()
        if not owner:
            owner = models.Owner(line_user_id=manual_id, display_name="Owner")
            db.add(owner)
        else:
            owner.line_user_id = manual_id
        db.commit()
        print(f"Database updated with Owner ID: {manual_id}")
        
        menu_id = create_owner_rich_menu()
        if menu_id:
            link_to_user(manual_id, menu_id)
    else:
        owner = db.query(models.Owner).first()
        if not owner or not owner.line_user_id:
            print("Error: Owner Line ID not found. Usage: python setup_owner_menu.py YOUR_LINE_USER_ID")
        else:
            menu_id = create_owner_rich_menu()
            if menu_id:
                link_to_user(owner.line_user_id, menu_id)
    db.close()
