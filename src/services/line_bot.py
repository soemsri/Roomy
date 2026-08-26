import os
import logging
import requests
import json
import urllib.parse
from sqlalchemy.orm import Session

from config import BASE_URL, LINE_TENANT_CHANNEL_ACCESS_TOKEN, get_text
import models
import services.promptpay as promptpay

from linebot.v3.messaging import (
    PushMessageRequest,
    FlexMessage,
    FlexContainer,
    TextMessage
)

logger = logging.getLogger(__name__)

def send_initial_payment_flex(tenant, success_rooms, g_deposit, g_advance, g_other, g_total, owner, bot_api, invoice_uuid: str = None):
    if not bot_api:
        return
    
    lang = tenant.language or "th"
    rooms_str = ", ".join(success_rooms)
    
    # 1. Payment Method Logic
    qr_enabled = owner.qr_payment_enabled if owner else 1
    payment_instruction_contents = []
    
    if qr_enabled:
        # Get PromptPay ID and Name
        promptpay_id = "0812345678"
        promptpay_name = ""
        try:
            config_list = json.loads(owner.promptpay_config)
            if config_list and isinstance(config_list, list) and len(config_list) > 0:
                promptpay_id = config_list[0].get('id', promptpay_id)
                promptpay_name = config_list[0].get('name', "")
        except: pass
        
        payload = promptpay.generate_promptpay_payload(promptpay_id, g_total)
        encoded_payload = urllib.parse.quote(payload)
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_payload}"
        qr_large_url = f"https://api.qrserver.com/v1/create-qr-code/?size=1000x1000&data={encoded_payload}"
        
        if promptpay_name:
            payment_instruction_contents.append({
                "type": "text", 
                "text": f"{get_text('account_name', lang)} {promptpay_name}", 
                "size": "sm", 
                "color": "#0078d4", 
                "weight": "bold", 
                "align": "center", 
                "margin": "lg"
            })

        payment_instruction_contents.extend([
            {"type": "text", "text": "👇 " + ("Hold to save QR" if lang == "en" else "長押しでQRを保存" if lang == "jp" else "กดค้างที่รูป QR ด้านล่างเพื่อบันทึก") + " 👇", "size": "xs", "color": "#e74c3c", "align": "center", "margin": "lg", "weight": "bold"},
            {
                "type": "image",
                "url": qr_url,
                "size": "xl",
                "aspectMode": "fit",
                "margin": "md"
            },
            {
                "type": "button",
                "action": {
                    "type": "uri",
                    "label": get_text('download_csv', lang).replace("CSV", "QR"), # Hacky but download_csv is "Download ..."
                    "uri": qr_large_url
                },
                "style": "secondary",
                "height": "sm",
                "margin": "xs"
            },
            {"type": "text", "text": "💡 " + ("Scan in bank app to pay" if lang == "en" else "銀行アプリでスキャンして支払う" if lang == "jp" else "ท่านสามารถนำ QR ไปสแกนในแอปธนาคารได้ทันที"), "size": "xxs", "color": "#888888", "align": "center", "margin": "md"},
            {"type": "text", "text": f"{get_text('promptpay', lang)}: {promptpay_id}", "size": "xs", "color": "#888888", "align": "center", "margin": "sm"}
        ])

    # Always add cash notice at the bottom of instructions
    payment_instruction_contents.append({
        "type": "box",
        "layout": "vertical",
        "backgroundColor": "#f8f9fa",
        "paddingAll": "md",
        "margin": "lg",
        "contents": [
            {"type": "text", "text": get_text('pay_cash_note', lang), "size": "xs", "color": "#888888", "align": "center", "wrap": True}
        ]
    })

    # 2. Build Flex JSON
    flex_json = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": get_text('initial_payment_type', lang), "weight": "bold", "size": "xl", "color": "#FFFFFF", "align": "center"},
                {"type": "text", "text": "Approved / อนุมัติเรียบร้อยแล้ว", "size": "sm", "color": "#FFFFFF", "align": "center", "margin": "xs"}
            ],
            "backgroundColor": "#1DB446",
            "paddingAll": "20px"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": get_text('room', lang), "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": rooms_str, "size": "sm", "color": "#111111", "align": "end", "wrap": True}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": get_text('security_deposit', lang), "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": f"฿{g_deposit:,.2f}", "size": "sm", "color": "#111111", "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": get_text('advance_rent', lang), "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": f"฿{g_advance:,.2f}", "size": "sm", "color": "#111111", "align": "end"}
                            ]
                        }
                    ]
                }
            ]
        }
    }
    
    if g_other > 0:
        flex_json["body"]["contents"][0]["contents"].append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": get_text('other', lang), "size": "sm", "color": "#555555", "flex": 0},
                {"type": "text", "text": f"฿{g_other:,.2f}", "size": "sm", "color": "#111111", "align": "end"}
            ]
        })

    flex_json["body"]["contents"].extend([
        {"type": "separator", "margin": "lg"},
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "lg",
            "contents": [
                {"type": "text", "text": get_text('total_sum_label', lang), "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
                {"type": "text", "text": f"฿{g_total:,.2f}", "size": "xl", "color": "#111111", "align": "end", "weight": "bold"}
            ]
        },
        *payment_instruction_contents
    ])

    if invoice_uuid:
        upload_url = f"{BASE_URL}/bill/{invoice_uuid}?lang={lang}"
        flex_json["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#1DB446",
                    "action": {
                        "type": "uri",
                        "label": "แจ้งโอนเงิน / อัพโหลดสลิป" if lang == "th" else "Report Payment / Upload Slip" if lang == "en" else "支払いを報告 / スリップをアップロード",
                        "uri": upload_url
                    }
                },
                {
                    "type": "text",
                    "text": "เมื่อโอนเงินแล้ว กรุณากดปุ่มด้านบนเพื่อแนบหลักฐาน" if lang == "th" else "After transfer, please click above to attach proof." if lang == "en" else "送金後、上のボタンをクリックして証明書を添付してください。",
                    "size": "xs",
                    "color": "#888888",
                    "align": "center",
                    "margin": "sm"
                }
            ]
        }

    try:
        # Send Flex Message first
        bot_api.push_message(
            PushMessageRequest(
                to=tenant.line_user_id,
                messages=[FlexMessage(alt_text="ใบแจ้งยอดชำระแรกเข้า", contents=FlexContainer.from_dict(flex_json))]
            )
        )
    except Exception as e:
        logger.error(f"Error sending initial payment flex/image: {e}")
        # Fallback to text
        msg = get_text('approve_tenant_success', lang).format(room=rooms_str) + f"\n{get_text('total_sum_label', lang)}: {g_total:,.2f} {get_text('currency_baht', lang)}"
        try:
            bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=msg)]
                )
            )
        except: pass

def setup_personal_rich_menu(tenant, db: Session, force=False):
    if not tenant or not tenant.line_user_id:
        return None
    
    # Count active rooms for this LINE ID
    active_tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant.line_user_id, models.Tenant.status == "Active").all()
    if not active_tenants:
        return None
        
    multi_room = len(active_tenants) > 1

    # Check if we already have a menu and if it's still appropriate
    if tenant.rich_menu_id and not force:
        return tenant.rich_menu_id

    # Load Tenant Channel Access Token from config
    token = LINE_TENANT_CHANNEL_ACCESS_TOKEN
    if not token:
        token = os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN")
    
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Define the Rich Menu Structure
    # Localized Labels and Images
    lang = tenant.language or "th"
    
    # Text mapping for Rich Menu from i18n
    chat_bar_text = get_text('tenant_chat_bar', lang) or "Tenant Menu"
    bill_label = get_text('view_bill', lang)
    repair_label = get_text('repairs', lang)
    history_label = get_text('history', lang)
    chat_label = get_text('chat_label', lang)
    move_in_label = get_text('move_in', lang)
    move_out_label = get_text('move_out', lang)
    
    # Image filenames
    img_filename = f"tenant_richmenu_{lang}.jpg"
    if lang == "th" or not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "image", img_filename)):
        img_filename = "tenant_richmenu.jpg" # default/fallback

    if not multi_room:
        # 1-Click direct links for single-room users
        repair_action = {"type": "uri", "label": repair_label, "uri": f"{BASE_URL}/repair/{tenant.uuid}"}
        history_action = {"type": "uri", "label": history_label, "uri": f"{BASE_URL}/history/{tenant.uuid}"}
        move_out_action = {"type": "uri", "label": move_out_label, "uri": f"{BASE_URL}/move-out/{tenant.uuid}"}
        menu_name = f"Tenant Menu {lang.upper()} Single - {tenant.line_user_id[:10]}"
    else:
        # Message-based triggers for multi-room users to allow room selection
        repair_action = {"type": "message", "text": repair_label}
        history_action = {"type": "message", "text": history_label}
        move_out_action = {"type": "message", "text": move_out_label}
        menu_name = f"Tenant Menu {lang.upper()} Multi - {tenant.line_user_id[:10]}"
    
    rich_menu_data = {
        "size": {"width": 2500, "height": 1686},
        "selected": False,
        "name": menu_name,
        "chatBarText": chat_bar_text,
        "areas": [
            {
                "bounds": {"x": 0, "y": 0, "width": 833, "height": 843},
                "action": {
                    "type": "message",
                    "text": bill_label
                }
            },
            {
                "bounds": {"x": 833, "y": 0, "width": 834, "height": 843},
                "action": repair_action
            },
            {
                "bounds": {"x": 1667, "y": 0, "width": 833, "height": 843},
                "action": history_action
            },
            {
                "bounds": {"x": 0, "y": 843, "width": 833, "height": 843},
                "action": {
                    "type": "postback",
                    "data": "action=chat",
                    "inputOption": "openKeyboard",
                    "displayText": chat_label
                }
            },
            {
                "bounds": {"x": 833, "y": 843, "width": 834, "height": 843},
                "action": {"type": "message", "text": move_in_label}
            },
            {
                "bounds": {"x": 1667, "y": 843, "width": 833, "height": 843},
                "action": move_out_action
            }
        ]
    }

    try:
        # 1. Create Rich Menu
        res = requests.post("https://api.line.me/v2/bot/richmenu", headers=headers, json=rich_menu_data)
        if res.status_code not in [200, 201]:
            logger.error(f"Error creating personal rich menu: {res.text}")
            return None
        
        rich_menu_id = res.json()["richMenuId"]

        # 2. Upload Image
        image_path = os.path.join(os.path.dirname(__file__), "..", img_filename)
        # Fallback to default if localized image doesn't exist
        if not os.path.exists(image_path):
            image_path = os.path.join(os.path.dirname(__file__), "..", "tenant_richmenu.jpg")
        
        if not os.path.exists(image_path):
             image_path = os.path.join(os.path.dirname(__file__), "..", "tenant_richmenu.png")
        if not os.path.exists(image_path):
             image_path = os.path.join(os.path.dirname(__file__), "..", "image", "tenant_richmenu.jpg")

        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                content_type = "image/jpeg" if image_path.endswith(".jpg") else "image/png"
                requests.post(
                    f"https://api-data.line.me/v2/bot/richmenu/{rich_menu_id}/content",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": content_type
                    },
                    data=f
                )
        
        # 3. Link to User
        requests.post(
            f"https://api.line.me/v2/bot/user/{tenant.line_user_id}/richmenu/{rich_menu_id}",
            headers=headers
        )
        
        # Save to DB for all active tenants of this user
        for t in active_tenants:
            t.rich_menu_id = rich_menu_id
        db.commit()
        
        return rich_menu_id
    except Exception as e:
        logger.error(f"setup_personal_rich_menu Error: {e}")
        return None

def send_booking_invitation(user_id: str, lang: str = "th", bot_api = None):
    """Sends a welcoming Flex Message with a button to open the room booking web form."""
    if not bot_api:
        return
    
    booking_url = f"{BASE_URL}/booking?uid={user_id}&lang={lang}"
    
    flex_contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"🏢 {get_text('book_room', lang)}", "weight": "bold", "size": "xl", "color": "#FFFFFF", "align": "center"}
            ],
            "backgroundColor": "#0078d4",
            "paddingAll": "20px"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": get_text('book_room_subtitle', lang), "size": "sm", "color": "#555555", "wrap": True, "align": "center"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "contents": [
                        {
                            "type": "button",
                            "style": "primary",
                            "color": "#0078d4",
                            "height": "sm",
                            "action": {
                                "type": "uri",
                                "label": f"📝 {get_text('book_room', lang)}",
                                "uri": booking_url
                            }
                        }
                    ]
                }
            ],
            "paddingAll": "20px"
        }
    }
    
    try:
        bot_api.push_message(
            PushMessageRequest(
                to=user_id,
                messages=[FlexMessage(alt_text=get_text('book_room', lang), contents=FlexContainer.from_dict(flex_contents))]
            )
        )
    except Exception as e:
        logger.error(f"Failed to send booking invitation: {e}")

def send_booking_approved_flex(booking, room_number: str = "N/A", building_name: str = "อาคารหลัก", owner = None, bot_api = None):
    """Sends a celebratory approval Flex Message to the candidate's LINE."""
    if not bot_api or not booking or not booking.line_user_id:
        return
    
    lang = booking.language or "th"
    move_in_date_str = booking.requested_move_in_date.strftime("%d/%m/%Y") if booking.requested_move_in_date else "-"
    apt_name = "SukAnan Apartment"
    if owner and owner.display_name:
        apt_name = owner.display_name
        
    flex_contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "🎉 " + ("Approval Confirmed!" if lang == "en" else "合格通知" if lang == "jp" else "ผ่านการคัดเลือกเข้าพัก!"), "weight": "bold", "size": "lg", "color": "#FFFFFF", "align": "center"}
            ],
            "backgroundColor": "#22c55e",
            "paddingAll": "20px"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"สวัสดีคุณ {booking.full_name}", "weight": "bold", "size": "md", "color": "#1e293b"},
                {"type": "text", "text": f"ยินดีด้วยครับ! คุณได้รับการคัดเลือกให้เข้าพักที่ {apt_name}", "size": "sm", "color": "#475569", "wrap": True, "margin": "sm"},
                {"type": "separator", "margin": "lg"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "🏢 " + get_text('building', lang), "size": "xs", "color": "#888888", "flex": 2},
                                {"type": "text", "text": building_name, "size": "xs", "color": "#1e293b", "weight": "bold", "flex": 3, "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "🚪 " + get_text('room', lang), "size": "xs", "color": "#888888", "flex": 2},
                                {"type": "text", "text": room_number, "size": "xs", "color": "#0078d4", "weight": "bold", "flex": 3, "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "📅 " + get_text('move_in_date', lang), "size": "xs", "color": "#888888", "flex": 2},
                                {"type": "text", "text": move_in_date_str, "size": "xs", "color": "#1e293b", "flex": 3, "align": "end"}
                            ]
                        }
                    ]
                },
                {"type": "separator", "margin": "lg"},
                {"type": "text", "text": "📌 เจ้าหน้าที่จะติดต่อกลับเพื่อประสานงานการทำสัญญาและการชำระเงินแรกเข้าต่อไปครับ", "size": "xs", "color": "#64748b", "wrap": True, "margin": "lg"}
            ],
            "paddingAll": "20px"
        }
    }
    
    try:
        bot_api.push_message(
            PushMessageRequest(
                to=booking.line_user_id,
                messages=[FlexMessage(alt_text="ผลการคัดเลือกเข้าพักหอพัก", contents=FlexContainer.from_dict(flex_contents))]
            )
        )
    except Exception as e:
        logger.error(f"Failed to send booking approved flex: {e}")

def send_booking_rejected_flex(booking, owner = None, bot_api = None):
    """Sends a polite rejection notification message."""
    if not bot_api or not booking or not booking.line_user_id:
        return
    lang = booking.language or "th"
    apt_name = (owner.display_name if owner and owner.display_name else "SukAnan Apartment")
    msg = get_text('booking_rejected_msg', lang).format(name=booking.full_name, apartment=apt_name)
    try:
        bot_api.push_message(
            PushMessageRequest(
                to=booking.line_user_id,
                messages=[TextMessage(text=msg)]
            )
        )
    except Exception as e:
        logger.error(f"Failed to send booking rejected message: {e}")
