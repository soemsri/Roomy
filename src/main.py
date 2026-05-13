import os
import shutil
import uuid
import requests
import csv
import io
import json
import warnings
from datetime import datetime
from dotenv import load_dotenv
from linebot.utils import LineBotSdkDeprecatedIn30
warnings.filterwarnings("ignore", category=LineBotSdkDeprecatedIn30)

from fastapi import FastAPI, Request, HTTPException, Depends, Form, File, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage, FlexContainer, PostbackEvent

from database import SessionLocal, engine, get_db
import models
import billing
import promptpay
import security

# Load env from src/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI()

# Important: directory is relative to where you run uvicorn
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

def from_json(value):
    try:
        return json.loads(value)
    except:
        return []
templates.env.filters['from_json'] = from_json

# Static files for images
uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
if not os.path.exists(uploads_dir):
    os.makedirs(uploads_dir)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

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
            "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", "admin1234")
        }

    db = SessionLocal()
    try:
        # These will try DB first, then .env via security.get_system_config
        return {
            "LINE_ADMIN_CHANNEL_ACCESS_TOKEN": security.get_system_config(db, "LINE_ADMIN_CHANNEL_ACCESS_TOKEN"),
            "LINE_ADMIN_CHANNEL_SECRET": security.get_system_config(db, "LINE_ADMIN_CHANNEL_SECRET"),
            "LINE_TENANT_CHANNEL_ACCESS_TOKEN": security.get_system_config(db, "LINE_TENANT_CHANNEL_ACCESS_TOKEN"),
            "LINE_TENANT_CHANNEL_SECRET": security.get_system_config(db, "LINE_TENANT_CHANNEL_SECRET"),
            "LINE_NOTIFY_TOKEN": security.get_system_config(db, "LINE_NOTIFY_TOKEN", ""),
            "BASE_URL": security.get_system_config(db, "BASE_URL", "http://localhost:8000").rstrip("/"),
            "ADMIN_PASSWORD": security.get_system_config(db, "ADMIN_PASSWORD", "admin1234")
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
            "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", "admin1234")
        }
    finally:
        db.close()

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
admin_bot_api = LineBotApi(LINE_ADMIN_CHANNEL_ACCESS_TOKEN) if LINE_ADMIN_CHANNEL_ACCESS_TOKEN else None
admin_handler = WebhookHandler(LINE_ADMIN_CHANNEL_SECRET) if LINE_ADMIN_CHANNEL_SECRET else None

# Tenant Channel
tenant_bot_api = LineBotApi(LINE_TENANT_CHANNEL_ACCESS_TOKEN) if LINE_TENANT_CHANNEL_ACCESS_TOKEN else None
tenant_handler = WebhookHandler(LINE_TENANT_CHANNEL_SECRET) if LINE_TENANT_CHANNEL_SECRET else None

# Compatibility shim (optional, if you want to keep old variable names for some internal logic)
line_bot_api = tenant_bot_api 

def send_line_notify(message: str):
    if not LINE_NOTIFY_TOKEN:
        print(f"LINE NOTIFY (Mock): {message}")
        return
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {LINE_NOTIFY_TOKEN}"}
    data = {"message": message}
    requests.post(url, headers=headers, data=data)

@app.get("/")
async def root():
    return {"message": "SukAnan Apartment API is running"}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(os.path.join(os.path.dirname(__file__), "favicon.ico"))

@app.post("/callback/admin")
async def callback_admin(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_str = body.decode("utf-8")
    
    if not admin_handler:
        print("Admin Handler not initialized. Check LINE_ADMIN_CHANNEL_SECRET.")
        return "OK"
        
    try:
        admin_handler.handle(body_str, signature)
    except InvalidSignatureError:
        print("Admin Webhook Error: Invalid Signature. Check LINE_ADMIN_CHANNEL_SECRET.")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"Admin Webhook Error: {e}")
        import traceback
        traceback.print_exc()
    return "OK"

@app.post("/callback/tenant")
async def callback_tenant(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_str = body.decode("utf-8")
    
    if not tenant_handler:
        print("Tenant Handler not initialized. Check LINE_TENANT_CHANNEL_SECRET.")
        return "OK"
        
    try:
        tenant_handler.handle(body_str, signature)
    except InvalidSignatureError:
        print("Tenant Webhook Error: Invalid Signature. Check LINE_TENANT_CHANNEL_SECRET.")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"Tenant Webhook Error: {e}")
        import traceback
        traceback.print_exc()
    return "OK"
@admin_handler.add(MessageEvent, message=TextMessage)
def handle_admin_message(event, *args, **kwargs):
    # Debug: See what was passed
    print(f"DEBUG: handle_admin_message called with {len(args)} positional args and {list(kwargs.keys())} keyword args")

    # Handle optional arguments from SDK or tests
    destination = args[0] if len(args) > 0 else None
    db = kwargs.get('db')

    # Check if destination was actually db (from old test style)
    if isinstance(destination, Session):
        db = destination
        destination = None

    # Check if it's a MessageEvent with TextMessage
    if not hasattr(event, "message") or not hasattr(event.message, "text"):
        return
        
    text = event.message.text.strip()
    user_id = event.source.user_id
    
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
        
    reply_text = ""
    try:
        # Check for pairing code FIRST if not already linked or even if linked (to re-pair)
        if text.isdigit() and len(text) == 6:
            owner_by_code = db.query(models.Owner).filter(models.Owner.pairing_code == text).first()
            if owner_by_code:
                owner_by_code.line_user_id = user_id
                owner_by_code.pairing_code = None # Clear after use
                db.commit()
                reply_text = "✅ เชื่อมต่อบัญชี LINE Admin เรียบร้อยแล้ว! คุณสามารถใช้ฟีเจอร์แจ้งเตือนและรีเซ็ตรหัสผ่านได้แล้วครับ"
                if admin_bot_api:
                    admin_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                return

        owner = db.query(models.Owner).filter(models.Owner.line_user_id == user_id).first()
        if not owner:
            if not db.query(models.Owner).first():
                owner = models.Owner(line_user_id=user_id, display_name="Owner")
                db.add(owner)
                db.commit()
                reply_text = "ยินดีด้วย! คุณได้รับการลงทะเบียนเป็นเจ้าของระบบใน Admin Channel เรียบร้อยแล้ว"
            else:
                reply_text = "ขออภัย คุณไม่ได้รับอนุญาตให้ใช้ Admin Channel นี้"
        else:
            if text.startswith("APPROVE_REG_") or text.startswith("REJECT_REG_"):
                parts = text.split("_")
                action = parts[0]
                tid = int(parts[2])
                target_tenant = db.query(models.Tenant).filter(models.Tenant.id == tid).first()
                if target_tenant:
                    if action == "APPROVE":
                        room = target_tenant.room
                        if room:
                            room.status = "Occupied"
                            new_lease = models.Lease(room_id=room.id, tenant_id=target_tenant.id, start_date=datetime.now())
                            db.add(new_lease)
                            target_tenant.status = "Active"
                            db.commit()
                            reply_text = f"อนุมัติผู้เช่าห้อง {room.room_number} เรียบร้อย"
                            if tenant_bot_api:
                                tenant_bot_api.push_message(target_tenant.line_user_id, TextSendMessage(text=f"ยินดีด้วย! การลงทะเบียนเข้าพักห้อง {room.room_number} ของคุณได้รับการอนุมัติแล้ว"))
                        else:
                            reply_text = "ไม่พบข้อมูลห้อง"
                    else:
                        target_tenant.status = "Rejected"
                        target_tenant.current_room_id = None
                        db.commit()
                        reply_text = "ปฏิเสธการลงทะเบียนเรียบร้อย"
                        if tenant_bot_api:
                            tenant_bot_api.push_message(target_tenant.line_user_id, TextSendMessage(text="ขออภัย การลงทะเบียนของคุณถูกปฏิเสธ กรุณาติดต่อเจ้าของหอพัก"))
                else:
                    reply_text = "ไม่พบข้อมูลผู้เช่า"
            elif text == "ผังห้อง":
                url = get_magic_url(owner, db)
                reply_text = f"🏠 ดูผังห้องและจัดการผู้เช่า:\n{url}"
            elif text == "จดมิเตอร์":
                url_single = get_magic_url(owner, db)
                url_bulk = url_single + "&mode=bulk" # simple append since get_magic_url has ?token=
                flex_contents = {
                    "type": "bubble",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": "📊 จดมิเตอร์น้ำ-ไฟ", "weight": "bold", "size": "xl", "color": "#FFFFFF"}
                        ],
                        "backgroundColor": "#0078d4",
                        "paddingAll": "20px"
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": "เลือกรูปแบบที่ต้องการจดมิเตอร์", "size": "sm", "color": "#888888"},
                            {
                                "type": "box",
                                "layout": "vertical",
                                "margin": "lg",
                                "spacing": "sm",
                                "contents": [
                                    {
                                        "type": "button",
                                        "style": "secondary",
                                        "height": "sm",
                                        "action": {
                                            "type": "uri",
                                            "label": "จดทีละห้อง",
                                            "uri": url_single + "#meterSection"
                                        }
                                    },
                                    {
                                        "type": "button",
                                        "style": "primary",
                                        "height": "sm",
                                        "color": "#27ae60",
                                        "action": {
                                            "type": "uri",
                                            "label": "จดรายอาคาร (แนะนำ)",
                                            "uri": url_bulk + "#meterSection"
                                        }
                                    }
                                ]
                            }
                        ],
                        "paddingAll": "20px"
                    }
                }
                if admin_bot_api:
                    admin_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="จดมิเตอร์น้ำ-ไฟ", contents=flex_contents))
                return
            elif text == "สรุปรายรับ":
                url = get_magic_url(owner, db)
                reply_text = f"💰 สรุปรายรับและส่งออกรายงาน:\n{url}#billSection"
            elif text == "จัดการสัญญา":
                url = get_magic_url(owner, db)
                reply_text = f"📜 จัดการสัญญาเช่า:\n{url}#leaseSection"
            elif text == "ตั้งค่า":
                url = get_magic_url(owner, db)
                reply_text = f"⚙️ ตั้งค่าระบบและพร้อมเพย์:\n{url}#settingsSection"
            elif text == "รายการแจ้งซ่อม":
                url = get_magic_url(owner, db)
                reply_text = f"🛠️ รายการแจ้งซ่อมจากผู้เช่า:\n{url}#repairSection"
            else:
                reply_text = "สวัสดีครับเจ้าของหอพัก! กรุณาเลือกเมนูจาก Rich Menu เพื่อดำเนินการ"
        
        if admin_bot_api and reply_text:
            admin_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    finally:
        if close_db:
            db.close()

@tenant_handler.add(MessageEvent, message=TextMessage)
def handle_tenant_message(event, *args, **kwargs):
    # Handle optional arguments from SDK or tests
    destination = args[0] if len(args) > 0 else None
    db = kwargs.get('db')
    
    # Check if destination was actually db (from old test style)
    if isinstance(destination, Session):
        db = destination
        destination = None

    text = event.message.text.strip()
    user_id = event.source.user_id
    
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
        
    reply_text = ""
    try:
        tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id == user_id).all()
        active_tenants = [t for t in tenants if t.status == "Active"]
        
        # If not active at all or specifically asking to register a new room
        if text == "ย้ายเข้า" or not active_tenants:
            # ... (rest of registration logic)
            # Find a tenant record that is not Active (e.g. Pending or AwaitingRegistration)
            tenant = next((t for t in tenants if t.status != "Active"), None)
            
            if not tenant:
                tenant = models.Tenant(line_user_id=user_id, status="AwaitingRegistration")
                db.add(tenant)
                db.commit()
                db.refresh(tenant)
            
            reg_url = f"{BASE_URL}/register/{tenant.uuid}"
            
            # Send as Buttons if possible
            if tenant_bot_api:
                from linebot.models import ButtonsTemplate, TemplateSendMessage, URITemplateAction
                buttons_template = ButtonsTemplate(
                    title='ลงทะเบียนเข้าพัก',
                    text='ยินดีต้อนรับ! กรุณากดลงทะเบียนสำหรับห้องใหม่ของคุณ',
                    actions=[
                        URITemplateAction(label='ลงทะเบียนเข้าพัก', uri=reg_url)
                    ]
                )
                try:
                    tenant_bot_api.reply_message(event.reply_token, TemplateSendMessage(alt_text='ลงทะเบียนเข้าพัก', template=buttons_template))
                    return
                except: pass
            
            reply_text = f"สวัสดีครับ! กรุณาลงทะเบียนเข้าพักที่นี่:\n{reg_url}"

        elif active_tenants:
            # Multi-room support
            # Ensure personal rich menu is updated
            setup_personal_rich_menu(active_tenants[0], db)
            
            if text == "ดูค่าเช่า":
                messages = []
                for tenant in active_tenants:
                    # Robust check: search by room_id if available, as tenant_id might have changed during record management
                    if tenant.current_room_id:
                        invoice = db.query(models.Invoice).filter(models.Invoice.room_id == tenant.current_room_id).order_by(models.Invoice.id.desc()).first()
                    else:
                        invoice = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).first()
                    
                    room_no = tenant.room.room_number if tenant.room else "N/A"
                    if invoice:
                        # ... (rest of invoice logic)
                        status_map = {
                            "Unpaid": ("ยังไม่ชำระ", "#e74c3c"),
                            "Pending Verification": ("รอตรวจสอบ", "#f39c12"),
                            "Draft": ("รอดำเนินการ", "#95a5a6"),
                            "Paid": ("ชำระแล้ว", "#3498db")
                        }
                        status_text, status_color = status_map.get(invoice.status, (invoice.status, "#3498db"))
                        bill_url = f"{BASE_URL}/bill/{invoice.uuid}"
                        total_fmt = "{:,.2f}".format(invoice.total_amount)
                        
                        flex_contents = {
                            "type": "bubble",
                            "header": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {"type": "text", "text": "สรุปค่าเช่า", "weight": "bold", "size": "xl", "color": "#FFFFFF", "align": "center"}
                                ],
                                "backgroundColor": "#1DB446",
                                "paddingAll": "20px"
                            },
                            "body": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {"type": "text", "text": "หอพักสุขอนันต์", "weight": "bold", "size": "md", "margin": "md"},
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
                                                    {"type": "text", "text": "ห้อง", "size": "sm", "color": "#555555", "flex": 0},
                                                    {"type": "text", "text": room_no, "size": "sm", "color": "#111111", "align": "end"}
                                                ]
                                            },
                                            {
                                                "type": "box",
                                                "layout": "horizontal",
                                                "contents": [
                                                    {"type": "text", "text": "รอบบิล", "size": "sm", "color": "#555555", "flex": 0},
                                                    {"type": "text", "text": f"{invoice.billing_month}/{invoice.billing_year}", "size": "sm", "color": "#111111", "align": "end"}
                                                ]
                                            },
                                            {
                                                "type": "box",
                                                "layout": "horizontal",
                                                "contents": [
                                                    {"type": "text", "text": "สถานะ", "size": "sm", "color": "#555555", "flex": 0},
                                                    {"type": "text", "text": status_text, "size": "sm", "color": status_color, "align": "end", "weight": "bold"}
                                                ]
                                            }
                                        ]
                                    },
                                    {"type": "separator", "margin": "lg"},
                                    {
                                        "type": "box",
                                        "layout": "horizontal",
                                        "margin": "lg",
                                        "contents": [
                                            {"type": "text", "text": "ยอดรวมทั้งสิ้น", "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
                                            {"type": "text", "text": f"฿{total_fmt}", "size": "xl", "color": "#111111", "align": "end", "weight": "bold"}
                                        ]
                                    }
                                ],
                                "paddingAll": "20px"
                            },
                            "footer": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {
                                        "type": "button",
                                        "style": "primary",
                                        "color": "#1DB446",
                                        "height": "sm",
                                        "action": {"type": "uri", "label": "ดูรายละเอียด / ชำระเงิน", "uri": bill_url}
                                    }
                                ]
                            }
                        }
                        messages.append(FlexSendMessage(alt_text=f"บิลห้อง {room_no}", contents=flex_contents))
                    else:
                        messages.append(TextSendMessage(text=f"ห้อง {room_no}: ไม่พบข้อมูลบิลล่าสุด"))

                
                if tenant_bot_api:
                    # LINE reply_message supports up to 5 messages
                    tenant_bot_api.reply_message(event.reply_token, messages[:5])
                    return
            
            elif text in ["แจ้งซ่อม", "ประวัติ", "ย้ายออก"]:
                if len(active_tenants) == 1:
                    t = active_tenants[0]
                    room_no = t.room.room_number if t.room else "N/A"
                    if text == "แจ้งซ่อม":
                        reply_text = f"แจ้งซ่อมห้อง {room_no}:\n{BASE_URL}/repair/{t.uuid}"
                    elif text == "ประวัติ":
                        reply_text = f"ดูประวัติห้อง {room_no}:\n{BASE_URL}/history/{t.uuid}"
                    else: # ย้ายออก
                        reply_text = f"แจ้งย้ายออกห้อง {room_no}:\n{BASE_URL}/move-out/{t.uuid}"
                else:
                    # Multi-room: Show selection menu
                    bubble_contents = []
                    for t in active_tenants:
                        room_no = t.room.room_number if t.room else "N/A"
                        url_map = {
                            "แจ้งซ่อม": f"{BASE_URL}/repair/{t.uuid}",
                            "ประวัติ": f"{BASE_URL}/history/{t.uuid}",
                            "ย้ายออก": f"{BASE_URL}/move-out/{t.uuid}"
                        }
                        bubble_contents.append({
                            "type": "button",
                            "style": "secondary",
                            "margin": "sm",
                            "action": {"type": "uri", "label": f"ห้อง {room_no}", "uri": url_map[text]}
                        })
                    
                    flex_contents = {
                        "type": "bubble",
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {"type": "text", "text": f"กรุณาเลือกห้องที่ต้องการ {text}", "weight": "bold", "size": "md"},
                                {"type": "box", "layout": "vertical", "margin": "lg", "contents": bubble_contents}
                            ]
                        }
                    }
                    if tenant_bot_api:
                        tenant_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text=f"เลือกห้องสำหรับ{text}", contents=flex_contents))
                        return

            elif text == "สนทนา":
                reply_text = "คุณสามารถพิมพ์ข้อความที่ต้องการสอบถามทิ้งไว้ได้เลยครับ เจ้าหน้าที่จะรีบมาตอบกลับโดยเร็วที่สุด"
            else:
                rooms_str = ", ".join([t.room.room_number for t in active_tenants if t.room])
                reply_text = f"สวัสดีครับ! (ห้อง {rooms_str})\nพิมพ์ 'ดูค่าเช่า', 'แจ้งซ่อม' หรือ 'ประวัติ'"
            
        if tenant_bot_api:
            tenant_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    finally:
        if close_db:
            db.close()

# Tenant APIs
@app.get("/register/{tenant_uuid}", response_class=HTMLResponse)
async def view_registration(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    buildings = db.query(models.Building).all()
    return templates.TemplateResponse("register.html", {"request": request, "tenant_uuid": tenant_uuid, "buildings": buildings})

@app.post("/register/{tenant_uuid}")
async def submit_registration(tenant_uuid: str, data: dict, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    room_id = data.get("room_id")
    full_name = data.get("full_name")
    phone_number = data.get("phone_number")
    
    if not all([room_id, full_name, phone_number]):
        raise HTTPException(status_code=400, detail="กรุณากรอกข้อมูลให้ครบถ้วน")
        
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room or room.status != "Vacant":
        raise HTTPException(status_code=400, detail="ห้องไม่ว่างหรือไม่มีอยู่จริง")
        
    tenant.current_room_id = room.id
    tenant.full_name = full_name
    tenant.phone_number = phone_number
    tenant.status = "Pending"
    db.commit()
    
    # Notify Owner
    owner = db.query(models.Owner).first()
    if owner and owner.line_user_id and admin_bot_api:
        msg = f"🔔 มีผู้ลงทะเบียนใหม่!\nห้อง: {room.room_number}\nชื่อ: {full_name}\nเบอร์โทร: {phone_number}\nกรุณาตรวจสอบใน Dashboard"
        try: admin_bot_api.push_message(owner.line_user_id, TextSendMessage(text=msg))
        except: pass
        
    return {"status": "Success"}

@app.get("/move-out/{tenant_uuid}", response_class=HTMLResponse)
async def view_move_out(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant or tenant.status != "Active":
        raise HTTPException(status_code=404, detail="Tenant not found or not active")
    
    return templates.TemplateResponse("move_out.html", {"request": request, "tenant": tenant})

@app.post("/move-out/{tenant_uuid}")
async def submit_move_out(tenant_uuid: str, data: dict, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant or tenant.status != "Active":
        raise HTTPException(status_code=404, detail="Tenant not found or not active")
    
    requested_date_str = data.get("requested_date")
    reason = data.get("reason")
    
    if not requested_date_str:
        raise HTTPException(status_code=400, detail="กรุณาระบุวันที่ต้องการย้ายออก")
        
    requested_date = datetime.strptime(requested_date_str, "%Y-%m-%d")
    
    # Create request record
    req = models.MoveOutRequest(
        tenant_id=tenant.id,
        room_id=tenant.current_room_id,
        requested_date=requested_date,
        reason=reason
    )
    db.add(req)
    
    # Also update tenant record for quick view
    tenant.move_out_date = requested_date
    tenant.move_out_reason = reason
    db.commit()
    
    # Notify Owner
    owner = db.query(models.Owner).first()
    if owner and owner.line_user_id and admin_bot_api:
        msg = f"🚪 แจ้งย้ายออกใหม่!\nห้อง: {tenant.room.room_number if tenant.room else 'N/A'}\nชื่อ: {tenant.full_name}\nวันที่ต้องการย้าย: {requested_date.strftime('%d/%m/%Y')}"
        try: admin_bot_api.push_message(owner.line_user_id, TextSendMessage(text=msg))
        except: pass
        
    return {"status": "Success"}

@app.get("/api/buildings/{bid}/vacant-rooms")
async def get_vacant_rooms(bid: int, db: Session = Depends(get_db)):
    rooms = db.query(models.Room).filter(models.Room.building_id == bid, models.Room.status == "Vacant").all()
    return [{"id": r.id, "room_number": r.room_number} for r in rooms]

@app.get("/bill/{invoice_uuid}", response_class=HTMLResponse)
async def view_bill(request: Request, invoice_uuid: str, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.uuid == invoice_uuid).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    owner = db.query(models.Owner).first()
    
    # Late fee calculation logic
    other_amount = 0
    if invoice.other_charges:
        try:
            other_amount = sum(float(item.get('amount', 0)) for item in json.loads(invoice.other_charges))
        except: pass
        
    # Initial subtotal
    subtotal = invoice.rent_amount + invoice.electricity_amount + invoice.water_amount + other_amount
    
    import billing
    late_fee = billing.get_late_fee(db, invoice=invoice)
    
    if invoice.status == "Unpaid" and late_fee != invoice.late_fee:
        invoice.late_fee = late_fee
        invoice.total_amount = subtotal + late_fee
        db.commit()
    else:
        late_fee = invoice.late_fee

    promptpay_id = None
    promptpay_name = None
    qr_enabled = 1
    
    if owner:
        qr_enabled = owner.qr_payment_enabled
        # 1. Get room specific preference
        target_id = invoice.room.promptpay_id if invoice.room else None
        
        # 2. Parse config
        config_list = []
        try:
            config_list = json.loads(owner.promptpay_config)
        except: pass
        
        if target_id and isinstance(config_list, list):
            match = next((c for c in config_list if c.get('id') == target_id), None)
            if match:
                promptpay_id = match.get('id')
                promptpay_name = match.get('name')
        
        # 3. Fallback
        if not promptpay_id and isinstance(config_list, list) and len(config_list) > 0:
            promptpay_id = config_list[0].get('id')
            promptpay_name = config_list[0].get('name')

    if not promptpay_id: promptpay_id = "0812345678"

    try:
        payload = promptpay.generate_promptpay_payload(promptpay_id, invoice.total_amount)
    except Exception as e:
        print(f"PromptPay Generation Error: {e}")
        payload = ""

    return templates.TemplateResponse("bill.html", {
        "request": request,
        "invoice": invoice,
        "invoice_uuid": invoice_uuid,
        "room_number": invoice.room.room_number if invoice.room else "N/A",
        "month": invoice.billing_month,
        "year": invoice.billing_year,
        "rent_amount": invoice.rent_amount,
        "water_amount": invoice.water_amount,
        "electricity_amount": invoice.electricity_amount,
        "electricity_reading": invoice.electricity_reading,
        "water_reading": invoice.water_reading,
        "prev_electricity_reading": invoice.prev_electricity_reading,
        "prev_water_reading": invoice.prev_water_reading,
        "other_charges": invoice.other_charges,
        "late_fee": invoice.late_fee,
        "total_amount": invoice.total_amount,
        "status": invoice.status,
        "promptpay_payload": payload,
        "qr_enabled": qr_enabled,
        "promptpay_name": promptpay_name,
        "room": invoice.room
    })

@app.post("/bill/{invoice_uuid}/upload-slip")
async def upload_slip(
    invoice_uuid: str, 
    image: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    invoice = db.query(models.Invoice).filter(models.Invoice.uuid == invoice_uuid).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Ensure directory exists
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
        
    file_ext = os.path.splitext(image.filename)[1]
    file_name = f"slip_{invoice.id}_{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(uploads_dir, file_name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
    
    invoice.status = "Pending Verification"
    invoice.payment_method = "PromptPay"
    invoice.payment_receipt_img = f"/uploads/{file_name}"
    invoice.paid_at = datetime.now()
    db.commit()
    
    # Notify Owner
    room_number = invoice.room.room_number if invoice.room else "N/A"
    send_line_notify(f"💰 สลิปใหม่: ห้อง {room_number}\nยอด: {invoice.total_amount:,.2f} บาท\nตรวจสอบได้ที่ dashboard")
    
    return {"status": "Success", "receipt": invoice.payment_receipt_img}

@app.get("/repair/{tenant_uuid}", response_class=HTMLResponse)
async def repair_form(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    return templates.TemplateResponse("repair.html", {
        "request": request,
        "tenant_id": tenant.id,
        "room_id": tenant.current_room_id,
        "room_number": tenant.room.room_number if tenant.room else "N/A"
    })

@app.get("/history/{tenant_uuid}", response_class=HTMLResponse)
async def view_history(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Robust check: fetch by room_id so history is complete for the specific room
    if tenant.current_room_id:
        invoices = db.query(models.Invoice).filter(models.Invoice.room_id == tenant.current_room_id).order_by(models.Invoice.id.desc()).all()
    else:
        invoices = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).all()
        
    return templates.TemplateResponse("history.html", {"request": request, "tenant": tenant, "invoices": invoices})

# Admin Security Dependency
def get_admin(request: Request, db: Session = Depends(get_db)):
    admin_session = request.cookies.get("admin_session")
    if not admin_session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Check if the session is the hashed password of the owner
    owner = db.query(models.Owner).first()
    if not owner or admin_session != owner.password_hash:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/admin/login")
async def admin_login(password: str = Form(...), db: Session = Depends(get_db)):
    owner = db.query(models.Owner).first()
    if owner and security.verify_password(password, owner.password_hash):
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(key="admin_session", value=owner.password_hash, httponly=True)
        return response
    return RedirectResponse(url="/admin/login?error=1", status_code=303)

@app.get("/admin/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response

@app.get("/admin/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})

@app.post("/admin/forgot-password")
async def request_password_reset(db: Session = Depends(get_db)):
    owner = db.query(models.Owner).first()
    if not owner or not owner.line_user_id or owner.line_user_id == "SYSTEM":
        return {"error": "No valid admin LINE ID found. Please contact support."}
    
    # Generate token
    import secrets
    from datetime import datetime, timedelta
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(minutes=5)
    
    reset_token = models.PasswordResetToken(
        token=token,
        expires_at=expires_at
    )
    db.add(reset_token)
    db.commit()
    
    # Send LINE message
    if admin_bot_api:
        reset_link = f"{BASE_URL}/admin/reset-password?token={token}"
        message = f"คุณได้ทำการขอรีเซ็ตรหัสผ่าน Admin\n\nกรุณากดลิงก์ด้านล่างเพื่อตั้งรหัสผ่านใหม่ (ลิงก์มีอายุ 5 นาที):\n{reset_link}"
        try:
            admin_bot_api.push_message(owner.line_user_id, TextSendMessage(text=message))
        except Exception as e:
            print(f"Error sending reset link: {e}")
            return {"error": "Failed to send LINE message."}
            
    return {"message": "ส่งลิงก์รีเซ็ตรหัสผ่านไปยัง LINE Admin แล้ว"}

@app.get("/admin/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    from datetime import datetime
    reset_token = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token == token,
        models.PasswordResetToken.used == 0,
        models.PasswordResetToken.expires_at > datetime.now()
    ).first()
    
    if not reset_token:
        return HTMLResponse(content="<h2>ลิงก์หมดอายุหรือไม่ถูกต้อง</h2>", status_code=400)
    
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token})

@app.post("/admin/reset-password")
async def reset_password(token: str = Form(...), new_password: str = Form(...), db: Session = Depends(get_db)):
    from datetime import datetime
    reset_token = db.query(models.PasswordResetToken).filter(
        models.PasswordResetToken.token == token,
        models.PasswordResetToken.used == 0,
        models.PasswordResetToken.expires_at > datetime.now()
    ).first()
    
    if not reset_token:
        return HTMLResponse(content="<h2>ลิงก์หมดอายุหรือไม่ถูกต้อง</h2>", status_code=400)
    
    owner = db.query(models.Owner).first()
    if owner:
        owner.password_hash = security.hash_password(new_password)
        reset_token.used = 1
        db.commit()
        return RedirectResponse(url="/admin/login?reset_success=1", status_code=303)
    
    return HTMLResponse(content="<h2>เกิดข้อผิดพลาดในการรีเซ็ตรหัสผ่าน</h2>", status_code=500)

@app.post("/admin/generate-pairing-code")
async def generate_pairing_code(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    import secrets
    # Generate 6-digit numeric code
    code = "".join([str(secrets.randbelow(10)) for _ in range(6)])
    owner = db.query(models.Owner).first()
    if owner:
        owner.pairing_code = code
        db.commit()
        return {"pairing_code": code}
    return {"error": "Owner not found"}

# Update admin_dashboard
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request, 
    month: int = None, 
    year: int = None, 
    building_id: str = None,
    db: Session = Depends(get_db), 
    admin: bool = Depends(get_admin)
):
    from sqlalchemy.orm import joinedload
    stats = {
        "total_rooms": db.query(models.Room).count(),
        "vacant_rooms": db.query(models.Room).filter(models.Room.status == "Vacant").count(),
        "unpaid_invoices": db.query(models.Invoice).filter(models.Invoice.status == "Unpaid").count(),
        "pending_verification": db.query(models.Invoice).filter(models.Invoice.status == "Pending Verification").count(),
        "pending_repairs": db.query(models.MaintenanceRequest).filter(models.MaintenanceRequest.status == "Pending").count()
    }
    recent_invoices = db.query(models.Invoice).options(joinedload(models.Invoice.tenant)).order_by(models.Invoice.id.desc()).limit(10).all()
    recent_repairs = db.query(models.MaintenanceRequest).order_by(models.MaintenanceRequest.id.desc()).limit(5).all()
    # List tenants currently mapped to rooms, eager loading residents
    active_tenants = db.query(models.Tenant).options(joinedload(models.Tenant.residents)).filter(models.Tenant.status == "Active", models.Tenant.current_room_id != None).all()
    pending_registrations = db.query(models.Tenant).filter(models.Tenant.status == "Pending").all()
    move_out_requests = db.query(models.MoveOutRequest).filter(models.MoveOutRequest.status == "Pending").all()
    
    all_rooms = db.query(models.Room).all()
    all_buildings = db.query(models.Building).all()
    owner = db.query(models.Owner).first()
    
    cur_m = month if month else datetime.now().month
    cur_y = year if year else datetime.now().year
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "recent_invoices": recent_invoices,
        "recent_repairs": recent_repairs,
        "active_tenants": active_tenants,
        "pending_registrations": pending_registrations,
        "move_out_requests": move_out_requests,
        "all_rooms": all_rooms,
        "all_buildings": all_buildings,
        "owner": owner,
        "current_month": cur_m,
        "current_year": cur_y,
        "building_id": building_id
    })

@app.post("/admin/registration/{tenant_id}/approve")
async def approve_registration(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    room = tenant.room
    if not room: raise HTTPException(status_code=400, detail="No room assigned to this request")
    
    owner = db.query(models.Owner).first()
    
    # Calculate Initial Fees
    applied_fees = []
    total_initial = 0
    fees_text = ""
    if owner and owner.move_in_fees_config:
        try:
            config = json.loads(owner.move_in_fees_config)
            for f in config:
                amt = f['value'] * room.base_rent if f.get('is_multiplier') else f['value']
                applied_fees.append({"name": f['name'], "amount": amt})
                total_initial += amt
                fees_text += f"<li>{f['name']}: {amt:,.2f} บาท</li>"
        except: pass
    
    if fees_text:
        fees_text = f"<ul>{fees_text}</ul><p><strong>รวมเงินมัดจำและค่าแรกเข้า: {total_initial:,.2f} บาท</strong></p>"

    lease_content = ""
    if owner and owner.lease_template:
        lease_content = owner.lease_template
        replacements = {
            "{tenant_name}": tenant.full_name,
            "{room_number}": room.room_number,
            "{floor}": str(room.floor),
            "{base_rent}": f"{room.base_rent:,.2f}",
            "{start_date}": datetime.now().strftime("%d/%m/%Y"),
            "{initial_fees}": fees_text
        }
        for placeholder, value in replacements.items():
            lease_content = lease_content.replace(placeholder, value)

    room.status = "Occupied"
    new_lease = models.Lease(
        room_id=room.id, 
        tenant_id=tenant.id, 
        start_date=datetime.now(),
        lease_content=lease_content,
        initial_fees=json.dumps(applied_fees)
    )
    db.add(new_lease)
    tenant.status = "Active"
    db.commit()
    
    # Setup Personal Rich Menu (1-Click)
    setup_personal_rich_menu(tenant, db)
    
    # Notify tenant
    if line_bot_api:
        from linebot.models import TextSendMessage
        try:
            line_bot_api.push_message(tenant.line_user_id, TextSendMessage(text=f"ยินดีด้วย! การลงทะเบียนเข้าพักห้อง {room.room_number} ของคุณได้รับการอนุมัติแล้ว"))
        except: pass
        
    return {"status": "Success"}

@app.get("/admin/leases/list")
async def list_leases(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    leases = db.query(models.Lease).order_by(models.Lease.id.desc()).all()
    results = []
    for l in leases:
        try:
            results.append({
                "id": l.id,
                "room_number": l.room.room_number if l.room else "N/A",
                "tenant_name": l.tenant.full_name if l.tenant else "N/A",
                "tenant_id": l.tenant_id,
                "start_date": l.start_date.strftime("%d/%m/%Y") if l.start_date else "-",
                "status": l.status
            })
        except Exception as e:
            print(f"Error processing lease {l.id}: {e}")
            continue
    return results

@app.get("/admin/leases/{lease_id}/view", response_class=HTMLResponse)
async def view_lease_contract(lease_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    lease = db.query(models.Lease).filter(models.Lease.id == lease_id).first()
    if not lease: raise HTTPException(status_code=404, detail="Lease not found")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>สัญญาเช่า - ห้อง {lease.room.room_number}</title>
        <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            body {{ font-family: 'Sarabun', sans-serif; padding: 50px; background: #f0f0f0; }}
            .paper {{ background: white; width: 210mm; min-height: 297mm; margin: auto; padding: 20mm; box-shadow: 0 0 10px rgba(0,0,0,0.1); box-sizing: border-box; }}
            @media print {{ body {{ padding: 0; background: white; }} .paper {{ box-shadow: none; margin: 0; }} .no-print {{ display: none; }} }}
        </style>
    </head>
    <body>
        <div class="no-print" style="text-align: center; margin-bottom: 20px;">
            <button onclick="window.print()" style="padding: 10px 20px; cursor: pointer;">พิมพ์สัญญานี้</button>
        </div>
        <div class="paper">
            {lease.lease_content or "ไม่มีข้อมูลเนื้อหาสัญญา"}
        </div>
    </body>
    </html>
    """
    return html

@app.post("/admin/registration/{tenant_id}/reject")
async def reject_registration(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    tenant.status = "Rejected"
    tenant.current_room_id = None
    db.commit()
    
    # Notify tenant
    if line_bot_api:
        from linebot.models import TextSendMessage
        try:
            line_bot_api.push_message(tenant.line_user_id, TextSendMessage(text="ขออภัย การลงทะเบียนของคุณถูกปฏิเสธ กรุณาติดต่อเจ้าของหอพัก"))
        except: pass
        
    return {"status": "Success"}

@app.get("/admin/settlement/preview/{tenant_id}")
async def preview_settlement(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    room = tenant.room
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id, models.Lease.status == "Active").first()
    
    # 1. Pro-rated Rent Calculation
    now = datetime.now()
    import calendar
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_stayed = now.day
    base_rent = room.base_rent if room else 0
    pro_rated_rent = round((base_rent / days_in_month) * days_stayed, 2)
    
    # 2. Utility Check
    # Check if meters are recorded for this month
    reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room.id,
        models.MeterReading.billing_month == now.month,
        models.MeterReading.billing_year == now.year
    ).first()
    
    if not reading:
        return {"status": "NEED_METERS", "room_number": room.room_number if room else "N/A"}
        
    # Calculate utility costs based on the reading
    # Need previous reading for units
    prev_reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room.id
    ).filter(
        (models.MeterReading.billing_year < now.year) | 
        ((models.MeterReading.billing_year == now.year) & (models.MeterReading.billing_month < now.month))
    ).order_by(models.MeterReading.billing_year.desc(), models.MeterReading.billing_month.desc()).first()
    
    prev_elec = prev_reading.electricity_reading if prev_reading else 0
    prev_water = prev_reading.water_reading if prev_reading else 0
    
    elec_units = max(0, reading.electricity_reading - prev_elec)
    water_units = max(0, reading.water_reading - prev_water)
    elec_amt = round(elec_units * (room.electricity_rate or 0), 2)
    water_amt = round(water_units * (room.water_rate or 0), 2)
    
    # 3. Security Deposit
    deposit = 0
    if lease and lease.initial_fees:
        try:
            fees = json.loads(lease.initial_fees)
            # Find fee that looks like deposit
            for f in fees:
                if "ประกัน" in f.get('name', '') or "deposit" in f.get('name', '').lower():
                    deposit = f.get('amount', 0)
                    break
        except: pass
        
    # 4. Unpaid Invoices
    unpaid_total = db.query(func.sum(models.Invoice.total_amount)).filter(
        models.Invoice.tenant_id == tenant.id,
        models.Invoice.status == "Unpaid"
    ).scalar() or 0
    
    return {
        "status": "READY",
        "room_number": room.room_number if room else "N/A",
        "tenant_name": tenant.full_name,
        "month": now.month,
        "year": now.year,
        "days_stayed": days_stayed,
        "days_in_month": days_in_month,
        "pro_rated_rent": pro_rated_rent,
        "elec_units": elec_units,
        "elec_amount": elec_amt,
        "water_units": water_units,
        "water_amount": water_amt,
        "unpaid_invoices": unpaid_total,
        "security_deposit": deposit
    }

@app.post("/admin/settlement/confirm/{tenant_id}")
async def confirm_settlement(
    tenant_id: int, 
    pro_rated_rent: float = Form(...),
    elec_amt: float = Form(...),
    water_amt: float = Form(...),
    unpaid_amt: float = Form(...),
    cleaning_fee: float = Form(...),
    damage_fee: float = Form(...),
    other_fees: float = Form(...),
    deposit_amt: float = Form(...),
    final_balance: float = Form(...),
    refund_method: str = Form(...),
    notes: str = Form(None),
    receipt: UploadFile = File(None),
    db: Session = Depends(get_db), 
    admin: bool = Depends(get_admin)
):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id, models.Lease.status == "Active").first()
    if not lease: raise HTTPException(status_code=400, detail="No active lease found")

    # Save Receipt Image
    receipt_url = None
    if receipt:
        os.makedirs("uploads", exist_ok=True)
        ext = os.path.splitext(receipt.filename)[1]
        filename = f"refund_{tenant.id}_{uuid.uuid4().hex}{ext}"
        filepath = os.path.join("uploads", filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(receipt.file, buffer)
        receipt_url = f"/uploads/{filename}"

    # Create Settlement Record
    total_deductions = pro_rated_rent + elec_amt + water_amt + unpaid_amt + cleaning_fee + damage_fee + other_fees
    
    settlement = models.Settlement(
        tenant_id=tenant.id,
        room_id=tenant.current_room_id,
        lease_id=lease.id,
        pro_rated_rent=pro_rated_rent,
        electricity_amount=elec_amt,
        water_amount=water_amt,
        unpaid_invoices_amount=unpaid_amt,
        cleaning_fee=cleaning_fee,
        damage_fee=damage_fee,
        other_fees=other_fees,
        total_deductions=total_deductions,
        security_deposit_amount=deposit_amt,
        final_balance=final_balance,
        refund_method=refund_method,
        refund_receipt_img=receipt_url,
        notes=notes
    )
    db.add(settlement)
    
    # Close Lease and Room
    lease.status = "Closed"
    lease.end_date = datetime.now()
    
    room = tenant.room
    if room:
        room.status = "Vacant"
        
    # Preservation of History
    history = models.TenantHistory(
        room_number=room.room_number if room else "N/A",
        tenant_uuid=tenant.uuid,
        full_name=tenant.full_name,
        phone_number=tenant.phone_number,
        start_date=lease.start_date,
        end_date=datetime.now(),
        residents_json=json.dumps([{"nickname": r.nickname, "full_name": f"{r.first_name} {r.last_name}"} for r in tenant.residents])
    )
    db.add(history)
    
    # Soft delete/deactivate tenant
    tenant.status = "MovedOut"
    tenant.current_room_id = None
    
    db.commit()
    return {"status": "Success"}

@app.post("/admin/unmap/{tenant_id}")
async def unmap_tenant(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    room = tenant.room
    if room:
        room.status = "Vacant"
    
    # Close active lease
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id, models.Lease.status == "Active").first()
    if lease:
        lease.status = "Closed"
        lease.end_date = datetime.now()
        
        # Save History
        history = models.TenantHistory(
            room_number=room.room_number if room else "N/A",
            tenant_uuid=tenant.uuid,
            full_name=tenant.full_name,
            phone_number=tenant.phone_number,
            start_date=lease.start_date,
            end_date=datetime.now(),
            residents_json=json.dumps([{"nickname": r.nickname, "full_name": f"{r.first_name} {r.last_name}"} for r in tenant.residents])
        )
        db.add(history)
        
    tenant.current_room_id = None
    tenant.status = "MovedOut"
    db.commit()
    return {"status": "Success"}

@app.get("/admin/buildings/list")
async def list_buildings(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    buildings = db.query(models.Building).all()
    return [{"id": b.id, "name": b.name, "description": b.description} for b in buildings]

@app.post("/admin/buildings/add")
async def add_building(name: str = Form(...), description: str = Form(None), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    new_building = models.Building(name=name, description=description)
    db.add(new_building)
    db.commit()
    return {"status": "Success", "id": new_building.id}

@app.post("/admin/buildings/{building_id}/edit")
async def edit_building(building_id: int, name: str = Form(...), description: str = Form(None), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    building = db.query(models.Building).filter(models.Building.id == building_id).first()
    if not building: raise HTTPException(status_code=404, detail="Building not found")
    building.name = name
    building.description = description
    db.commit()
    return {"status": "Success"}

@app.post("/admin/buildings/{building_id}/delete")
async def delete_building(building_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    building = db.query(models.Building).filter(models.Building.id == building_id).first()
    if not building: raise HTTPException(status_code=404, detail="Building not found")
    
    # Check if building has rooms
    if len(building.rooms) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete building with rooms")
        
    db.delete(building)
    db.commit()
    return {"status": "Success"}

@app.post("/admin/rooms/add")
async def add_room(
    room_number: str = Form(...),
    floor: int = Form(...),
    base_rent: float = Form(...),
    electricity_rate: float = Form(...),
    water_rate: float = Form(...),
    building_id: str = Form(None),
    promptpay_id: str = Form(None),
    recurring_charges: str = Form("[]"),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    # Handle empty building_id string
    bid = int(building_id) if building_id and building_id.strip() != "" else None
    
    # Validation: room_number should be unique within the same building
    existing = db.query(models.Room).filter(models.Room.room_number == room_number, models.Room.building_id == bid).first()
    if existing:
        raise HTTPException(status_code=400, detail="Room number already exists in this building")
        
    new_room = models.Room(
        room_number=room_number,
        floor=floor,
        base_rent=base_rent,
        electricity_rate=electricity_rate,
        water_rate=water_rate,
        building_id=bid,
        promptpay_id=promptpay_id,
        recurring_charges=recurring_charges,
        status="Vacant"
    )
    db.add(new_room)
    db.commit()
    return {"status": "Success"}

@app.post("/admin/rooms/{room_id}/edit")
async def edit_room(
    room_id: int,
    room_number: str = Form(...),
    floor: int = Form(...),
    base_rent: float = Form(...),
    electricity_rate: float = Form(...),
    water_rate: float = Form(...),
    building_id: str = Form(None),
    promptpay_id: str = Form(None),
    recurring_charges: str = Form("[]"),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room: raise HTTPException(status_code=404, detail="Room not found")
    
    bid = int(building_id) if building_id and building_id.strip() != "" else None

    # Validation: room_number should be unique within the same building (excluding itself)
    existing = db.query(models.Room).filter(
        models.Room.room_number == room_number, 
        models.Room.building_id == bid,
        models.Room.id != room_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Room number already exists in this building")
    
    room.room_number = room_number
    room.floor = floor
    room.base_rent = base_rent
    room.electricity_rate = electricity_rate
    room.water_rate = water_rate
    room.building_id = bid
    room.promptpay_id = promptpay_id
    room.recurring_charges = recurring_charges
    db.commit()
    return {"status": "Success"}

@app.post("/admin/rooms/bulk-recurring")
async def bulk_recurring(charges_json: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    # Save to owner as a global template/common fee
    owner = db.query(models.Owner).first()
    if owner:
        owner.default_recurring_charges = charges_json
        db.commit()
    return {"status": "Success"}

@app.get("/admin/rooms/{room_id}/details")
async def get_room_details(room_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room: raise HTTPException(status_code=404, detail="Room not found")

    tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == room_id, models.Tenant.status == "Active").first()
    meter_history = db.query(models.MeterReading).filter(models.MeterReading.room_id == room_id).order_by(models.MeterReading.id.desc()).limit(12).all()
    payment_history = db.query(models.Invoice).filter(models.Invoice.room_id == room_id).order_by(models.Invoice.id.desc()).limit(12).all()
    assets = db.query(models.RoomAsset).filter(models.RoomAsset.room_id == room_id).all()

    owner = db.query(models.Owner).first()
    import json
    try:
        room_recurring = json.loads(room.recurring_charges) if room.recurring_charges else []
    except: room_recurring = []

    try:
        global_recurring = json.loads(owner.default_recurring_charges) if owner and owner.default_recurring_charges else []
    except: global_recurring = []

    return {
        "room": {
            "id": room.id,
            "room_number": room.room_number,
            "status": room.status,
            "base_rent": room.base_rent,
            "room_recurring": room_recurring,
            "global_recurring": global_recurring
        },
        "tenant": {
            "full_name": tenant.full_name if tenant else None,
            "phone_number": tenant.phone_number if tenant else None,
            "residents": [{"nickname": r.nickname, "full_name": f"{r.first_name} {r.last_name}"} for r in tenant.residents] if tenant else []
        },
        "meters": [{"month": m.billing_month, "year": m.billing_year, "elec": m.electricity_reading, "water": m.water_reading, "date": m.recorded_at.strftime("%d/%m/%Y")} for m in meter_history],
        "payments": [{"month": p.billing_month, "year": p.billing_year, "total": p.total_amount, "status": p.status, "date": p.paid_at.strftime("%d/%m/%Y") if p.paid_at else "-"} for p in payment_history],
        "assets": [{"id": a.id, "name": a.name, "quantity": a.quantity} for a in assets]
    }
@app.post("/admin/rooms/{room_id}/assets/add")
async def add_room_asset(
    room_id: int, 
    name: str = Form(...), 
    quantity: int = Form(...), 
    to_all: bool = Form(False),
    db: Session = Depends(get_db), 
    admin: bool = Depends(get_admin)
):
    if to_all:
        rooms = db.query(models.Room).all()
        for r in rooms:
            asset = models.RoomAsset(room_id=r.id, name=name, quantity=quantity)
            db.add(asset)
    else:
        asset = models.RoomAsset(room_id=room_id, name=name, quantity=quantity)
        db.add(asset)
    
    db.commit()
    return {"status": "Success"}

@app.post("/admin/assets/{asset_id}/edit")
async def edit_room_asset(asset_id: int, name: str = Form(...), quantity: int = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    asset = db.query(models.RoomAsset).filter(models.RoomAsset.id == asset_id).first()
    if not asset: raise HTTPException(status_code=404, detail="Asset not found")
    asset.name = name
    asset.quantity = quantity
    db.commit()
    return {"status": "Success"}

@app.post("/admin/assets/{asset_id}/delete")
async def delete_room_asset(asset_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    asset = db.query(models.RoomAsset).filter(models.RoomAsset.id == asset_id).first()
    if not asset: raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(asset)
    db.commit()
    return {"status": "Success"}


@app.post("/admin/invoice/{invoice_id}/confirm-cash")
async def confirm_cash_payment(
    invoice_id: int, 
    image: UploadFile = File(...), 
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    file_ext = os.path.splitext(image.filename)[1]
    file_name = f"receipt_{invoice_id}_{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(uploads_dir, file_name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
    
    invoice.status = "Paid"
    invoice.payment_method = "Cash"
    invoice.payment_receipt_img = f"/uploads/{file_name}"
    invoice.paid_at = datetime.now()
    db.commit()
    return {"status": "Success", "receipt": invoice.payment_receipt_img}

@app.post("/admin/invoice/{invoice_id}/cancel")
async def cancel_invoice(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    if invoice.status == "Paid":
        raise HTTPException(status_code=400, detail="ไม่สามารถยกเลิกบิลที่ชำระเงินเรียบร้อยแล้วได้")
        
    # We delete the invoice so it can be re-recorded/re-calculated correctly
    db.delete(invoice)
    db.commit()
    return {"status": "Success"}

@app.get("/admin/invoice/{invoice_id}/details")
async def get_invoice_details(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    import json
    other_charges = []
    if invoice.other_charges:
        try:
            other_charges = json.loads(invoice.other_charges)
        except: pass

    # Dynamic late fee update
    if invoice.status == "Unpaid":
        import billing
        late_fee = billing.get_late_fee(db, invoice=invoice)
        if late_fee != invoice.late_fee:
            other_amt = sum(float(c.get('amount', 0)) for c in other_charges)
            subtotal = invoice.rent_amount + invoice.electricity_amount + invoice.water_amount + other_amt
            invoice.late_fee = late_fee
            invoice.total_amount = subtotal + late_fee
            db.commit()

    return {
        "id": invoice.id,
        "uuid": invoice.uuid,
        "room_number": invoice.room.room_number if invoice.room else "N/A",
        "tenant_name": invoice.tenant.full_name if invoice.tenant else "N/A",
        "month": invoice.billing_month,
        "year": invoice.billing_year,
        "rent": invoice.rent_amount,
        "elec_reading": invoice.electricity_reading,
        "prev_elec_reading": invoice.prev_electricity_reading,
        "elec_amount": invoice.electricity_amount,
        "water_reading": invoice.water_reading,
        "prev_water_reading": invoice.prev_water_reading,
        "water_amount": invoice.water_amount,
        "other_charges": other_charges,
        "late_fee": invoice.late_fee,
        "total": invoice.total_amount,
        "status": invoice.status,
        "paid_at": invoice.paid_at.strftime("%d/%m/%Y %H:%M") if invoice.paid_at else None,
        "receipt_img": invoice.payment_receipt_img
    }

@app.post("/admin/invoice/{invoice_id}/approve")
async def approve_invoice(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    invoice.status = "Paid"
    if not invoice.paid_at:
        invoice.paid_at = datetime.now()
    db.commit()
    
    # Notify tenant via Flex Message
    tenant = invoice.tenant
    if tenant and tenant.line_user_id and tenant_bot_api:
        owner = db.query(models.Owner).first()
        apt_name = owner.display_name if owner and owner.display_name else "SukAnan Apartment"
        room_no = invoice.room.room_number if invoice.room else "N/A"
        period = f"{invoice.billing_month}/{invoice.billing_year}"
        paid_date = invoice.paid_at.strftime("%d/%m/%Y %H:%M")
        total_fmt = f"{invoice.total_amount:,.2f}"
        bill_url = f"{BASE_URL}/bill/{invoice.uuid}"
        
        flex_json = {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "ใบเสร็จรับเงิน", "weight": "bold", "size": "xl", "color": "#FFFFFF", "align": "center"},
                    {"type": "text", "text": "ชำระเงินเรียบร้อยแล้ว", "size": "sm", "color": "#FFFFFF", "align": "center", "margin": "sm"}
                ],
                "backgroundColor": "#27ae60",
                "paddingAll": "20px"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": apt_name, "weight": "bold", "size": "md", "margin": "md"},
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
                                    {"type": "text", "text": "ห้อง", "size": "sm", "color": "#555555", "flex": 0},
                                    {"type": "text", "text": room_no, "size": "sm", "color": "#111111", "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "รอบบิล", "size": "sm", "color": "#555555", "flex": 0},
                                    {"type": "text", "text": period, "size": "sm", "color": "#111111", "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": "วันที่ชำระ", "size": "sm", "color": "#555555", "flex": 0},
                                    {"type": "text", "text": paid_date, "size": "sm", "color": "#111111", "align": "end"}
                                ]
                            }
                        ]
                    },
                    {"type": "separator", "margin": "lg"},
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "lg",
                        "contents": [
                            {"type": "text", "text": "ยอดชำระสุทธิ", "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
                            {"type": "text", "text": f"฿{total_fmt}", "size": "xl", "color": "#27ae60", "align": "end", "weight": "bold"}
                        ]
                    },
                    {"type": "text", "text": "ขอบคุณที่ใช้บริการค่ะ", "size": "sm", "color": "#aaaaaa", "margin": "xxl", "align": "center"}
                ],
                "paddingAll": "20px"
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {"type": "uri", "label": "ดูรายละเอียดบิล", "uri": bill_url}
                    }
                ],
                "flex": 0
            }
        }
        
        try:
            tenant_bot_api.push_message(tenant.line_user_id, FlexSendMessage(alt_text="ใบเสร็จรับเงิน", contents=flex_json))
        except Exception as e:
            print(f"LINE Flex Error: {e}")
            # Fallback to text
            try:
                tenant_bot_api.push_message(tenant.line_user_id, TextSendMessage(text=f"✅ ชำระเงินเรียบร้อย! บิลเดือน {period} ได้รับการตรวจสอบแล้ว ขอบคุณครับ"))
            except: pass
            
    return {"status": "Success"}

@app.post("/admin/invoice/{invoice_id}/reject")
async def reject_invoice(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    invoice.status = "Unpaid"
    # Optional: Keep the failed receipt but maybe clear it from being "the" receipt
    # invoice.payment_receipt_img = None 
    db.commit()
    
    # Notify tenant
    tenant = invoice.tenant
    if tenant and tenant.line_user_id and line_bot_api:
        from linebot.models import TextSendMessage
        try:
            line_bot_api.push_message(tenant.line_user_id, TextSendMessage(text=f"❌ แจ้งเตือน: สลิปการโอนเงินของบิลเดือน {invoice.billing_month}/{invoice.billing_year} ไม่ถูกต้อง กรุณาตรวจสอบหรืออัปโหลดใหม่อีกครั้ง"))
        except: pass
        
    return {"status": "Success"}

@app.post("/admin/invoice/{invoice_id}/send-line")
async def send_invoice_line(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    tenant = invoice.tenant
    if not tenant or not tenant.line_user_id:
        raise HTTPException(status_code=400, detail="ไม่สามารถส่งได้: ผู้เช่ายังไม่ได้ลงทะเบียน LINE")
    
    if not tenant_bot_api:
        raise HTTPException(status_code=500, detail="LINE Bot API not configured")

    status_map = {
        "Unpaid": ("ยังไม่ชำระ", "#e74c3c"),
        "Pending Verification": ("รอตรวจสอบ", "#f39c12"),
        "Draft": ("รอดำเนินการ", "#95a5a6"),
        "Paid": ("ชำระแล้ว", "#3498db")
    }
    status_text, status_color = status_map.get(invoice.status, (invoice.status, "#3498db"))
    bill_url = f"{BASE_URL}/bill/{invoice.uuid}"
    room_number = invoice.room.room_number if invoice.room else "N/A"
    total_fmt = "{:,.2f}".format(invoice.total_amount)
    
    flex_contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "ใบแจ้งค่าเช่า",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#FFFFFF",
                    "align": "center"
                }
            ],
            "backgroundColor": "#1DB446",
            "paddingAll": "20px"
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "หอพักสุขอนันต์",
                    "weight": "bold",
                    "size": "md",
                    "margin": "md"
                },
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
                                {"type": "text", "text": "ห้อง", "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": room_number, "size": "sm", "color": "#111111", "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "รอบบิล", "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": f"{invoice.billing_month}/{invoice.billing_year}", "size": "sm", "color": "#111111", "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "สถานะ", "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": status_text, "size": "sm", "color": status_color, "align": "end", "weight": "bold"}
                            ]
                        }
                    ]
                },
                {"type": "separator", "margin": "lg"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "margin": "lg",
                    "contents": [
                        {"type": "text", "text": "ยอดรวมทั้งสิ้น", "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
                        {"type": "text", "text": f"฿{total_fmt}", "size": "xl", "color": "#111111", "align": "end", "weight": "bold"}
                    ]
                }
            ],
            "paddingAll": "20px"
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#1DB446",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "ดูรายละเอียด / ชำระเงิน",
                        "uri": bill_url
                    }
                }
            ],
            "flex": 0
        }
    }
    
    try:
        tenant_bot_api.push_message(tenant.line_user_id, FlexSendMessage(alt_text="ใบแจ้งค่าเช่า", contents=flex_contents))
        return {"status": "Success"}
    except Exception as e:
        # Fallback to text
        msg = f"📄 ใบแจ้งค่าเช่าเดือน {invoice.billing_month}/{invoice.billing_year}\n"
        msg += f"ห้อง {room_number}\n"
        msg += f"ยอดรวม: {total_fmt} บาท\n\n"
        msg += f"ดูรายละเอียดและแจ้งชำระเงินได้ที่:\n{bill_url}"
        try:
            tenant_bot_api.push_message(tenant.line_user_id, TextSendMessage(text=msg))
            return {"status": "Success"}
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"LINE Error: {str(e2)}")

@app.post("/admin/repair/{repair_id}/status")
async def update_repair_status(repair_id: int, status: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    repair = db.query(models.MaintenanceRequest).filter(models.MaintenanceRequest.id == repair_id).first()
    if not repair: raise HTTPException(status_code=404, detail="Repair request not found")
    
    repair.status = status
    db.commit()
    
    # Notify tenant via LINE if possible
    tenant = repair.tenant
    if tenant and tenant.line_user_id and line_bot_api:
        from linebot.models import TextSendMessage
        try:
            message = f"🛠️ อัปเดตสถานะการแจ้งซ่อม: {repair.title}\nสถานะ: {status}"
            line_bot_api.push_message(tenant.line_user_id, TextSendMessage(text=message))
        except Exception as e:
            print(f"LINE Push Error: {e}")
            
    return {"status": "Success"}

@app.get("/admin/settings/configs")
async def get_all_configs(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    configs = db.query(models.SystemConfig).all()
    # Return decrypted values for the UI
    return [{
        "key": c.key,
        "value": security.decrypt_value(c.value),
        "description": c.description
    } for c in configs]

@app.post("/admin/settings/configs/save")
async def save_config(
    key: str = Form(...),
    value: str = Form(...),
    description: str = Form(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    security.set_system_config(db, key, value, description)
    return {"status": "Success"}

@app.post("/admin/settings/save")
async def save_settings(
    display_name: str = Form(None),
    promptpay_config: str = Form("[]"),
    qr_enabled: str = Form("1"),
    late_fee_enabled: str = Form("0"),
    due_day: str = Form("5"),
    late_fee_per_day: str = Form("50.0"),
    lease_template: str = Form(None),
    move_in_fees_config: str = Form("[]"),
    magic_link_duration_min: int = Form(5),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    owner = db.query(models.Owner).first()
    if not owner:
        owner = models.Owner(line_user_id="SYSTEM")
        db.add(owner)
    
    if display_name is not None:
        owner.display_name = display_name
    
    owner.promptpay_config = promptpay_config
    
    # Safe conversions
    try: owner.qr_payment_enabled = int(qr_enabled)
    except: owner.qr_payment_enabled = 1
    
    try: owner.late_fee_enabled = 1 if late_fee_enabled in ["1", "true", "on", "checked"] else 0
    except: owner.late_fee_enabled = 0
    
    try: owner.due_day = int(due_day)
    except: owner.due_day = 5
    
    try: owner.late_fee_per_day = float(late_fee_per_day)
    except: owner.late_fee_per_day = 50.0
    
    if move_in_fees_config:
        owner.move_in_fees_config = move_in_fees_config
    
    if lease_template:
        owner.lease_template = lease_template
    
    owner.magic_link_duration_min = magic_link_duration_min
    
    db.commit()
    return {"status": "Success"}

@app.get("/admin/magic-login")
async def magic_login(request: Request, token: str, db: Session = Depends(get_db)):
    owner = db.query(models.Owner).filter(
        models.Owner.magic_token == token,
        models.Owner.magic_token_expires > datetime.now()
    ).first()
    
    if not owner:
        return HTMLResponse(content="<h2>ลิงก์หมดอายุหรือไม่ถูกต้อง กรุณากดใหม่จาก LINE Admin</h2>", status_code=400)
    
    # Capture all query params except 'token' to pass to dashboard
    params = dict(request.query_params)
    if 'token' in params: del params['token']
    
    import urllib.parse
    query_string = urllib.parse.urlencode(params)
    target_url = "/admin/dashboard"
    if query_string:
        target_url += "?" + query_string
    
    # Set session cookie (use the password hash as session token)
    response = RedirectResponse(url=target_url, status_code=303)
    response.set_cookie(key="admin_session", value=owner.password_hash, httponly=True)
    
    return response

# Helper for LINE Bot to generate magic links
def get_magic_url(owner, db, path=""):
    import secrets
    from datetime import timedelta
    token = secrets.token_urlsafe(16)
    owner.magic_token = token
    owner.magic_token_expires = datetime.now() + timedelta(minutes=owner.magic_link_duration_min or 5)
    db.commit()
    
    url = f"{BASE_URL}/admin/magic-login?token={token}"
    if path:
        # After magic-login, redirect to specific section if needed
        # We need to handle this in magic_login endpoint if we want redirect.
        # For now, let's keep it simple and just go to dashboard.
        pass
    return url

def setup_personal_rich_menu(tenant, db: Session, force=False):
    if not tenant or not tenant.line_user_id:
        return None
    
    # Count active rooms for this LINE ID
    active_tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant.line_user_id, models.Tenant.status == "Active").all()
    if not active_tenants:
        return None
        
    multi_room = len(active_tenants) > 1

    # Check if we already have a menu and if it's still appropriate
    # (Simplified check: if they have a rich_menu_id, we assume it's correct 
    # unless force=True or we want to get fancy with counting rooms in the menu name)
    if tenant.rich_menu_id and not force:
        return tenant.rich_menu_id

    # Load Tenant Channel Access Token
    token = os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN")
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Define the Rich Menu Structure
    if not multi_room:
        # 1-Click direct links for single-room users
        repair_action = {"type": "uri", "label": "แจ้งซ่อม", "uri": f"{BASE_URL}/repair/{tenant.uuid}"}
        history_action = {"type": "uri", "label": "ประวัติ", "uri": f"{BASE_URL}/history/{tenant.uuid}"}
        move_out_action = {"type": "uri", "label": "ย้ายออก", "uri": f"{BASE_URL}/move-out/{tenant.uuid}"}
        menu_name = f"Tenant Menu Single - {tenant.line_user_id[:10]}"
    else:
        # Message-based triggers for multi-room users to allow room selection
        repair_action = {"type": "message", "text": "แจ้งซ่อม"}
        history_action = {"type": "message", "text": "ประวัติ"}
        move_out_action = {"type": "message", "text": "ย้ายออก"}
        menu_name = f"Tenant Menu Multi - {tenant.line_user_id[:10]}"
    
    rich_menu_data = {
        "size": {"width": 2500, "height": 1686},
        "selected": False,
        "name": menu_name,
        "chatBarText": "เมนูผู้เช่า",
        "areas": [
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": "ดูค่าเช่า"}},
            {"bounds": {"x": 833, "y": 0, "width": 834, "height": 843}, "action": repair_action},
            {"bounds": {"x": 1667, "y": 0, "width": 833, "height": 843}, "action": history_action},
            {"bounds": {"x": 0, "y": 843, "width": 833, "height": 843}, "action": {
                "type": "postback",
                "data": "action=chat",
                "inputOption": "openKeyboard"
            }},
            {"bounds": {"x": 833, "y": 843, "width": 834, "height": 843}, "action": {"type": "message", "text": "ย้ายเข้า"}},
            {"bounds": {"x": 1667, "y": 843, "width": 833, "height": 843}, "action": move_out_action}
        ]
    }

    try:
        # 1. Create Rich Menu
        res = requests.post("https://api.line.me/v2/bot/richmenu", headers=headers, json=rich_menu_data)
        if res.status_code not in [200, 201]:
            print(f"Error creating personal rich menu: {res.text}")
            return None
        
        rich_menu_id = res.json()["richMenuId"]

        # 2. Upload Image
        image_path = os.path.join(os.path.dirname(__file__), "tenant_richmenu.png")
        if not os.path.exists(image_path):
             image_path = os.path.join(os.path.dirname(__file__), "image", "tenantrichmenu.jpg")

        if os.path.exists(image_path):
            with open(image_path, "rb") as f:
                content_type = "image/png" if image_path.endswith(".png") else "image/jpeg"
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
        print(f"setup_personal_rich_menu Error: {e}")
        return None

@app.get("/admin/promptpay/preview")
async def preview_promptpay(pp_id: str, admin: bool = Depends(get_admin)):
    try:
        payload = promptpay.generate_promptpay_payload(pp_id)
        return {"payload": payload}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/broadcast")
async def broadcast_announcement(message: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id != None).all()
    count = 0
    if line_bot_api:
        from linebot.models import TextSendMessage
        for t in tenants:
            try:
                line_bot_api.push_message(t.line_user_id, TextSendMessage(text=f"📢 ประกาศจากหอพัก:\n{message}"))
                count += 1
            except Exception as e:
                print(f"Broadcast Error to {t.line_user_id}: {e}")
    else:
        print(f"MOCK BROADCAST: {message}")
        count = len(tenants)
        
    return {"status": "Success", "sent_count": count}

@app.get("/admin/report/export")
async def export_report(month: int, year: int, building_id: int = None, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.Invoice).filter(
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year,
        models.Invoice.status == "Paid"
    )
    
    b_name = "All"
    if building_id:
        query = query.join(models.Room).filter(models.Room.building_id == building_id)
        building = db.query(models.Building).filter(models.Building.id == building_id).first()
        if building:
            b_name = building.name
            
    invoices = query.all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Building", "Room", "Month", "Year", "Rent", "Water", "Elec", "Total", "Paid At"])
    
    total_income = 0
    for inv in invoices:
        writer.writerow([
            inv.room.building.name if inv.room and inv.room.building else "N/A",
            inv.room.room_number if inv.room else "N/A",
            inv.billing_month,
            inv.billing_year,
            inv.rent_amount,
            inv.water_amount,
            inv.electricity_amount,
            inv.total_amount,
            inv.paid_at.strftime("%Y-%m-%d %H:%M") if inv.paid_at else ""
        ])
        total_income += inv.total_amount
    
    writer.writerow([])
    writer.writerow(["Total Income", "", "", "", "", "", "", total_income, ""])
    
    output.seek(0)
    filename = f"report_{b_name}_{year}_{month}.csv".replace(" ", "_")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.get("/admin/tenants/{tenant_id}/residents")
async def get_residents(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.residents

@app.post("/admin/tenants/{tenant_id}/residents/add")
async def add_resident(
    tenant_id: int,
    first_name: str = Form(None),
    last_name: str = Form(None),
    nickname: str = Form(...),
    phone_number: str = Form(None),
    workplace: str = Form(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    new_resident = models.Resident(
        tenant_id=tenant_id,
        first_name=first_name,
        last_name=last_name,
        nickname=nickname,
        phone_number=phone_number,
        workplace=workplace
    )
    db.add(new_resident)
    db.commit()
    return {"status": "Success"}

@app.post("/admin/residents/{resident_id}/edit")
async def edit_resident(
    resident_id: int,
    first_name: str = Form(None),
    last_name: str = Form(None),
    nickname: str = Form(...),
    phone_number: str = Form(None),
    workplace: str = Form(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    resident = db.query(models.Resident).filter(models.Resident.id == resident_id).first()
    if not resident: raise HTTPException(status_code=404, detail="Resident not found")
    
    resident.first_name = first_name
    resident.last_name = last_name
    resident.nickname = nickname
    resident.phone_number = phone_number
    resident.workplace = workplace
    db.commit()
    return {"status": "Success"}

@app.post("/admin/residents/{resident_id}/delete")
async def delete_resident(resident_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    resident = db.query(models.Resident).filter(models.Resident.id == resident_id).first()
    if not resident: raise HTTPException(status_code=404, detail="Resident not found")
    
    tenant = resident.tenant
    # Constraint: at least one person's name must remain if the room is already mapped
    if tenant.line_user_id and len(tenant.residents) <= 1:
        raise HTTPException(status_code=400, detail="ไม่สามารถลบได้ ต้องมีอย่างน้อย 1 รายชื่อสำหรับห้องที่ลงทะเบียนแล้ว")
        
    db.delete(resident)
    db.commit()
    return {"status": "Success"}

@app.get("/admin/tenants/search")
async def search_tenants(q: str = "", db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    # ... (existing search code)
    from sqlalchemy import or_
    current_residents = db.query(models.Resident).filter(
        or_(
            models.Resident.first_name.ilike(f"%{q}%"),
            models.Resident.last_name.ilike(f"%{q}%"),
            models.Resident.phone_number.ilike(f"%{q}%"),
            models.Resident.workplace.ilike(f"%{q}%")
        )
    ).all()
    
    # Search history
    past_residents = db.query(models.TenantHistory).filter(
        or_(
            models.TenantHistory.first_name.ilike(f"%{q}%"),
            models.TenantHistory.last_name.ilike(f"%{q}%"),
            models.TenantHistory.phone_number.ilike(f"%{q}%"),
            models.TenantHistory.workplace.ilike(f"%{q}%")
        )
    ).all()
    
    results = []
    for r in current_residents:
        results.append({
            "type": "Current",
            "room": r.tenant.room.room_number if r.tenant and r.tenant.room else "N/A",
            "name": f"{r.first_name or ''} {r.last_name or ''} ({r.nickname})",
            "phone": r.phone_number,
            "workplace": r.workplace,
            "period": "ปัจจุบัน"
        })
    
    for r in past_residents:
        results.append({
            "type": "Past",
            "room": r.room_number,
            "name": f"{r.first_name or ''} {r.last_name or ''} ({r.nickname})",
            "phone": r.phone_number,
            "workplace": r.workplace,
            "period": f"{r.start_date.strftime('%d/%m/%Y') if r.start_date else '?'} - {r.end_date.strftime('%d/%m/%Y') if r.end_date else '?'}"
        })
        
    return results

@app.get("/admin/meters/current")
async def get_current_meter(room_id: int, month: int, year: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    owner = db.query(models.Owner).first()
    
    reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id,
        models.MeterReading.billing_month == month,
        models.MeterReading.billing_year == year
    ).first()
    
    invoice = db.query(models.Invoice).filter(
        models.Invoice.room_id == room_id,
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year
    ).first()
    
    import json
    try:
        global_recurring = json.loads(owner.default_recurring_charges) if owner and owner.default_recurring_charges else []
    except:
        global_recurring = []
        
    try:
        room_recurring = json.loads(room.recurring_charges) if room.recurring_charges else []
    except:
        room_recurring = []

    # If invoice exists, use its charges. 
    # We will try to filter out the recurring ones for the UI if we want to show them separately,
    # but for now, let's just return what's in the invoice as 'manual' if they are not recurring.
    manual_charges = []
    if invoice and invoice.other_charges:
        try:
            all_saved = json.loads(invoice.other_charges)
            # Simple heuristic: if it matches a recurring charge (desc and amt), it's recurring.
            # But the user wants recurring to be READ-ONLY.
            # So we only show those in manual_charges that are NOT in recurring.
            rec_keys = set((c['description'], c['amount']) for c in global_recurring + room_recurring)
            for c in all_saved:
                if (c.get('description'), c.get('amount')) not in rec_keys:
                    manual_charges.append(c)
        except:
            pass

    # Fetch previous reading for context
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id,
        models.MeterReading.billing_month == prev_month,
        models.MeterReading.billing_year == prev_year
    ).first()

    return {
        "found": True if reading else False,
        "electricity": reading.electricity_reading if reading else 0,
        "water": reading.water_reading if reading else 0,
        "prev_electricity": prev_reading.electricity_reading if prev_reading else 0,
        "prev_water": prev_reading.water_reading if prev_reading else 0,
        "recorded_at": reading.recorded_at.strftime("%d/%m/%Y %H:%M") if reading and reading.recorded_at else None,
        "global_recurring": global_recurring,
        "room_recurring": room_recurring,
        "manual_charges": manual_charges,
        "invoice_status": invoice.status if invoice else "No Invoice"
    }

@app.get("/admin/meters/history")
async def get_meter_history(room_id: int = None, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.MeterReading).join(models.Room)
    if room_id:
        query = query.filter(models.MeterReading.room_id == room_id)

    readings = query.order_by(models.MeterReading.billing_year.desc(), models.MeterReading.billing_month.desc()).all()

    results = []
    for r in readings:
        room = db.query(models.Room).filter(models.Room.id == r.room_id).first()
        # Find associated invoice to check status
        invoice = db.query(models.Invoice).filter(
            models.Invoice.room_id == r.room_id,
            models.Invoice.billing_month == r.billing_month,
            models.Invoice.billing_year == r.billing_year
        ).first()

        results.append({
            "room_id": r.room_id,
            "room_number": room.room_number if room else "N/A",
            "month": r.billing_month,
            "year": r.billing_year,
            "electricity": r.electricity_reading,
            "water": r.water_reading,
            "recorded_at": r.recorded_at.strftime("%d/%m/%Y %H:%M"),
            "invoice_status": invoice.status if invoice else "No Invoice"
        })
    return results
@app.get("/admin/repair/history")
async def get_repair_history(room_id: int = None, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.MaintenanceRequest).join(models.Room)
    if room_id:
        query = query.filter(models.MaintenanceRequest.room_id == room_id)
    
    repairs = query.order_by(models.MaintenanceRequest.id.desc()).all()
    
    results = []
    for r in repairs:
        results.append({
            "id": r.id,
            "room_number": r.room.room_number if r.room else "N/A",
            "tenant_name": r.tenant.full_name if r.tenant else "N/A",
            "title": r.title,
            "description": r.description,
            "status": r.status,
            "image_url": r.image_url,
            "created_at": r.created_at.strftime("%d/%m/%Y %H:%M")
        })
    return results

# Standard Utility APIs
@app.post("/repair/submit")
async def submit_repair(
    tenant_id: int = Form(...),
    room_id: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    image: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    # Validation
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
        
    image_url = None
    if image and image.filename:
        file_ext = os.path.splitext(image.filename)[1]
        file_name = f"repair_{uuid.uuid4().hex}{file_ext}"
        file_path = os.path.join(uploads_dir, file_name)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)
        image_url = f"/uploads/{file_name}"
    
    request_obj = models.MaintenanceRequest(tenant_id=tenant_id, room_id=room_id, title=title, description=description, image_url=image_url)
    db.add(request_obj)
    db.commit()
    
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    room_number = room.room_number if room else "N/A"
    
    # Notify Owner via Admin Channel
    msg = f"🛠️ แจ้งซ่อมใหม่: ห้อง {room_number}\nเรื่อง: {title}\nรายละเอียด: {description}\nตรวจสอบได้ที่: {BASE_URL}/admin/dashboard"
    
    owner = db.query(models.Owner).first()
    if owner and owner.line_user_id and admin_bot_api:
        from linebot.models import ImageSendMessage
        try:
            # Send text message first
            admin_bot_api.push_message(owner.line_user_id, TextSendMessage(text=msg))
            
            # If there's an image, send it too
            if image_url:
                full_image_url = f"{BASE_URL}{image_url}"
                admin_bot_api.push_message(owner.line_user_id, ImageSendMessage(
                    original_content_url=full_image_url,
                    preview_image_url=full_image_url
                ))
        except Exception as e:
            print(f"Admin Push Error (Repair): {e}")
            send_line_notify(msg)
    else:
        send_line_notify(msg)

    return {"status": "Success"}

@app.post("/admin/invoice/preview")
async def preview_invoice(
    room_id: int = Form(...), 
    month: int = Form(...), 
    year: int = Form(...), 
    elec: float = Form(...), 
    water: float = Form(...), 
    other_charges: str = Form("[]"), 
    db: Session = Depends(get_db), 
    admin: bool = Depends(get_admin)
):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room: raise HTTPException(status_code=404, detail="Room not found")
    
    tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == room_id, models.Tenant.status == "Active").first()
    if not tenant: raise HTTPException(status_code=400, detail="ห้องนี้ไม่มีผู้เช่าที่ใช้งานอยู่")

    import json
    try:
        parsed_charges = json.loads(other_charges)
    except:
        parsed_charges = []

    # Merge Recurring Charges
    owner = db.query(models.Owner).first()
    final_other_charges = []
    
    # 1. Global Recurring
    if owner and owner.default_recurring_charges:
        try:
            final_other_charges.extend(json.loads(owner.default_recurring_charges))
        except: pass
    
    # 2. Room Recurring
    if room.recurring_charges:
        try:
            final_other_charges.extend(json.loads(room.recurring_charges))
        except: pass
        
    # 3. Add manual charges (avoiding duplicates)
    seen = set((c.get('description'), c.get('amount')) for c in final_other_charges)
    for c in parsed_charges:
        if (c.get('description'), c.get('amount')) not in seen:
            final_other_charges.append(c)

    # Get previous reading for preview calculation
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id,
        models.MeterReading.billing_month == prev_month,
        models.MeterReading.billing_year == prev_year
    ).order_by(models.MeterReading.id.desc()).first()

    prev_elec = prev_reading.electricity_reading if prev_reading else 0
    prev_water = prev_reading.water_reading if prev_reading else 0
    
    elec_units = max(0, elec - prev_elec)
    water_units = max(0, water - prev_water)
    
    elec_amt = elec_units * room.electricity_rate
    water_amt = water_units * room.water_rate
    
    other_amt = sum(float(c.get('amount', 0)) for c in final_other_charges)
    import billing
    late_fee = billing.get_late_fee(db, billing_month=month, billing_year=year)
    total = room.base_rent + elec_amt + water_amt + other_amt + late_fee

    return {
        "room_number": room.room_number,
        "tenant_name": tenant.full_name,
        "month": month,
        "year": year,
        "rent": room.base_rent,
        "elec_units": elec_units,
        "elec_amount": elec_amt,
        "water_units": water_units,
        "water_amount": water_amt,
        "other_charges": final_other_charges,
        "late_fee": late_fee,
        "total": total
    }

@app.post("/admin/meters/bulk-record")
async def bulk_record_meters(
    data: str = Form(...), # JSON: [{"room_id": 1, "elec": 10.5, "water": 5.0, "other_charges": [...]}, ...]
    month: int = Form(...),
    year: int = Form(...),
    issue_bill: bool = Form(False),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    import json
    try:
        readings = json.loads(data)
    except:
        raise HTTPException(status_code=400, detail="Invalid data format")
        
    results = []
    for r in readings:
        room_id = r.get("room_id")
        elec_raw = r.get("elec")
        water_raw = r.get("water")
        other_charges = r.get("other_charges") # List of dicts
        
        if room_id is None:
            continue
            
        # Skip if both are empty (just saving partial building)
        if (elec_raw == '' or elec_raw is None) and (water_raw == '' or water_raw is None):
            continue
            
        try:
            elec = float(elec_raw)
            water = float(water_raw)
        except (ValueError, TypeError):
            continue # Skip invalid numbers
            
        # Check if paid
        invoice = db.query(models.Invoice).filter(
            models.Invoice.room_id == room_id,
            models.Invoice.billing_month == month,
            models.Invoice.billing_year == year
        ).first()
        
        if invoice and invoice.status == "Paid":
            results.append({"room_id": room_id, "status": "Error", "message": "Paid"})
            continue

        # Update or Create MeterReading
        reading = db.query(models.MeterReading).filter(
            models.MeterReading.room_id == room_id,
            models.MeterReading.billing_month == month,
            models.MeterReading.billing_year == year
        ).first()
        
        if reading:
            reading.electricity_reading = elec
            reading.water_reading = water
            reading.recorded_at = datetime.now()
        else:
            reading = models.MeterReading(
                room_id=room_id, 
                billing_month=month, 
                billing_year=year, 
                electricity_reading=elec, 
                water_reading=water
            )
            db.add(reading)
        
        db.commit()
        
        # Calculate bill (passing save_only as inverse of issue_bill)
        inv = billing.calculate_bill(db, room_id, month, year, other_charges=other_charges, save_only=(not issue_bill))
        results.append({"room_id": room_id, "status": "Success", "invoice_uuid": inv.uuid if inv else None})
        
    return {"status": "Complete", "results": results}

@app.get("/admin/meters/bulk-context")
async def get_bulk_context(building_id: int, month: int, year: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    rooms_query = db.query(models.Room).filter(models.Room.building_id == building_id).all()
    
    # Natural sort in Python
    import re
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower()
                for text in re.split('([0-9]+)', s.room_number)]
    
    rooms = sorted(rooms_query, key=natural_sort_key)
    
    # Previous period
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    
    results = []
    for r in rooms:
        # Get active tenant
        tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == r.id, models.Tenant.line_user_id != None).first()
        
        # Get previous readings
        prev_reading = db.query(models.MeterReading).filter(
            models.MeterReading.room_id == r.id,
            models.MeterReading.billing_month == prev_month,
            models.MeterReading.billing_year == prev_year
        ).first()
        
        # Get current (already recorded) readings if any
        curr_reading = db.query(models.MeterReading).filter(
            models.MeterReading.room_id == r.id,
            models.MeterReading.billing_month == month,
            models.MeterReading.billing_year == year
        ).first()
        
        results.append({
            "id": r.id,
            "room_number": r.room_number,
            "tenant_name": tenant.full_name if tenant else None,
            "prev_elec": prev_reading.electricity_reading if prev_reading else 0,
            "prev_water": prev_reading.water_reading if prev_reading else 0,
            "curr_elec": curr_reading.electricity_reading if curr_reading else None,
            "curr_water": curr_reading.water_reading if curr_reading else None,
            "is_recorded": curr_reading is not None
        })
        
    return results

@app.post("/admin/meters/record")
async def record_meter(
    room_id: int = Form(...), 
    month: int = Form(...), 
    year: int = Form(...), 
    elec: float = Form(...), 
    water: float = Form(...), 
    other_charges: str = Form(None), 
    issue_bill: bool = Form(False),
    db: Session = Depends(get_db), 
    admin: bool = Depends(get_admin)
):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
        
    # Check if invoice exists and is already paid
    invoice = db.query(models.Invoice).filter(
        models.Invoice.room_id == room_id,
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year
    ).first()
    
    if invoice and invoice.status == "Paid":
        raise HTTPException(status_code=400, detail="บิลเดือนนี้ชำระเงินเรียบร้อยแล้ว ไม่สามารถแก้ไขค่ามิเตอร์ได้")

    # Update or Create MeterReading
    reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id,
        models.MeterReading.billing_month == month,
        models.MeterReading.billing_year == year
    ).first()
    
    if reading:
        reading.electricity_reading = elec
        reading.water_reading = water
        reading.recorded_at = datetime.now()
    else:
        reading = models.MeterReading(
            room_id=room_id, 
            billing_month=month, 
            billing_year=year, 
            electricity_reading=elec, 
            water_reading=water
        )
        db.add(reading)
    
    db.commit()
    
    import json
    parsed_charges = None
    if other_charges:
        try:
            parsed_charges = json.loads(other_charges)
        except:
            pass
            
    invoice = billing.calculate_bill(db, room_id, month, year, other_charges=parsed_charges, save_only=(not issue_bill))
    if not invoice:
        raise HTTPException(status_code=400, detail="ไม่สามารถสร้างบิลได้ (อาจยังไม่มีผู้เช่าในห้องนี้)")
        
    return {"status": "Success", "invoice_uuid": invoice.uuid}

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
