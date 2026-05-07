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
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from database import SessionLocal, engine, get_db
import models
import billing
import promptpay

# Load env from src/.env
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

app = FastAPI()

# Important: directory is relative to where you run uvicorn
# If running from 'src' folder, directory="templates" is correct.
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

# LINE Credentials
LINE_ADMIN_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_ADMIN_CHANNEL_ACCESS_TOKEN")
LINE_ADMIN_CHANNEL_SECRET = os.getenv("LINE_ADMIN_CHANNEL_SECRET")
LINE_TENANT_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN")
LINE_TENANT_CHANNEL_SECRET = os.getenv("LINE_TENANT_CHANNEL_SECRET")

LINE_NOTIFY_TOKEN = os.getenv("LINE_NOTIFY_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin1234")

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
                reply_text = f"🏠 ดูผังห้องและจัดการผู้เช่า:\n{BASE_URL}/admin/dashboard"
            elif text == "จดมิเตอร์":
                reply_text = f"📊 จดบันทึกมิเตอร์น้ำ-ไฟ:\n{BASE_URL}/admin/dashboard"
            elif text == "สรุปรายรับ":
                reply_text = f"💰 สรุปรายรับและส่งออกรายงาน:\n{BASE_URL}/admin/dashboard"
            elif text == "จัดการสัญญา":
                reply_text = f"📜 จัดการสัญญาเช่า:\n{BASE_URL}/admin/dashboard"
            elif text == "ตั้งค่า":
                reply_text = f"⚙️ ตั้งค่าระบบและพร้อมเพย์:\n{BASE_URL}/admin/dashboard"
            elif text == "รายการแจ้งซ่อม":
                reply_text = f"🛠️ รายการแจ้งซ่อมจากผู้เช่า:\n{BASE_URL}/admin/dashboard"
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
        
    try:
        tenant = db.query(models.Tenant).filter(models.Tenant.line_user_id == user_id).first()
        
        if not tenant or tenant.status in ["Rejected", "AwaitingBuilding", "AwaitingRoom", "AwaitingName", "AwaitingPhone"]:
            # Initial Greeting or Restarting Registration
            if not tenant or tenant.status == "Rejected":
                buildings = db.query(models.Building).all()
                if len(buildings) > 1:
                    # Multiple buildings: Must select building first
                    if not tenant:
                        tenant = models.Tenant(line_user_id=user_id, uuid=str(uuid.uuid4()), status="AwaitingBuilding")
                        db.add(tenant)
                    else:
                        tenant.status = "AwaitingBuilding"
                    db.commit()
                    
                    # Send Buttons to select building
                    from linebot.models import ButtonsTemplate, TemplateSendMessage, MessageAction
                    actions = [MessageAction(label=b.name, text=f"อาคาร {b.name}") for b in buildings[:4]] # LINE limit 4 buttons
                    buttons_template = ButtonsTemplate(
                        title='กรุณาเลือกอาคาร',
                        text='พบหลายอาคารในระบบ กรุณาเลือกอาคารที่คุณพักอาศัย',
                        actions=actions
                    )
                    if tenant_bot_api:
                        tenant_bot_api.reply_message(event.reply_token, TemplateSendMessage(alt_text='กรุณาเลือกอาคาร', template=buttons_template))
                    return
                else:
                    # Only one building or no buildings (fallback to default)
                    bid = buildings[0].id if buildings else None
                    if not tenant:
                        tenant = models.Tenant(line_user_id=user_id, uuid=str(uuid.uuid4()), status="AwaitingRoom")
                        db.add(tenant)
                    else:
                        tenant.status = "AwaitingRoom"
                    
                    # If we have exactly one building, we can pre-assign it or just wait for room
                    # To keep it simple, we just move to AwaitingRoom. 
                    # If there's only one building, the room search will naturally find it.
                    db.commit()
                    reply_text = "สวัสดีครับ คุณพักอยู่ห้องหมายเลขอะไรครับ? (ตัวอย่าง: 101)"

            elif tenant.status == "AwaitingBuilding":
                # User should have typed/clicked "อาคาร [Name]"
                # If they click a button, text is "อาคาร [Name]"
                if text.startswith("อาคาร "):
                    building_name = text[6:].strip() # "อาคาร " is 6 chars
                else:
                    building_name = text.strip()
                
                building = db.query(models.Building).filter(models.Building.name == building_name).first()
                if building:
                    tenant.status = "AwaitingRoom"
                    tenant.temp_building_id = building.id
                    db.commit()
                    reply_text = f"คุณเลือก {building.name} กรุณาพิมพ์หมายเลขห้องของคุณ"
                else:
                    reply_text = "ขออภัย ไม่พบข้อมูลอาคารดังกล่าว กรุณาเลือกจากปุ่มที่ระบบส่งให้"

            elif tenant.status == "AwaitingRoom":
                # If we have a temp_building_id, search rooms in that building
                query = db.query(models.Room).filter(models.Room.room_number == text)
                if tenant.temp_building_id:
                    query = query.filter(models.Room.building_id == tenant.temp_building_id)
                
                rooms = query.all()
                
                if not rooms:
                    reply_text = f"ไม่พบห้องหมายเลข {text} กรุณาตรวจสอบอีกครั้ง"
                elif len(rooms) == 1:
                    room = rooms[0]
                    if room.status == "Vacant":
                        tenant.current_room_id = room.id
                        tenant.status = "AwaitingName"
                        db.commit()
                        reply_text = f"ห้อง {room.room_number} ({room.building.name if room.building else ''}) ว่าง! กรุณาพิมพ์ ชื่อ-นามสกุล ของคุณเพื่อลงทะเบียน"
                    else:
                        reply_text = f"ห้อง {text} ไม่ว่างในขณะนี้ หากข้อมูลผิดพลาดกรุณาติดต่อเจ้าของ"
                else:
                    # This happens if multiple rooms with same number and no building was selected yet
                    # Fallback to building selection
                    reply_text = "พบห้องหมายเลขนี้ในหลายอาคาร กรุณาพิมพ์ 'อาคาร [ชื่ออาคาร]' เพื่อเลือกอาคารก่อน"
                    tenant.status = "AwaitingBuilding"
                    db.commit()
            
            elif tenant.status == "AwaitingName":
                tenant.full_name = text
                tenant.status = "AwaitingPhone"
                db.commit()
                reply_text = f"ขอบคุณครับคุณ {text}, กรุณาพิมพ์ เบอร์โทรศัพท์ ของคุณ"
            
            elif tenant.status == "AwaitingPhone":
                tenant.phone_number = text
                tenant.status = "Pending"
                db.commit()
                db.refresh(tenant)
                room_number = tenant.room.room_number if tenant.room else "N/A"
                reply_text = f"บันทึกข้อมูลเรียบร้อย! กรุณารอเจ้าของอนุมัติการเข้าพักห้อง {room_number}"
                
                # Notify Owner via Admin Channel
                owner = db.query(models.Owner).first()
                if owner and owner.line_user_id and admin_bot_api:
                    try:
                        from linebot.models import ButtonsTemplate, TemplateSendMessage, MessageAction
                        buttons_template = ButtonsTemplate(
                            title='อนุมัติผู้เช่าใหม่',
                            text=f'ห้อง {room_number}: {tenant.full_name}',
                            actions=[
                                MessageAction(label='อนุมัติ', text=f'APPROVE_REG_{tenant.id}'),
                                MessageAction(label='ปฏิเสธ', text=f'REJECT_REG_{tenant.id}')
                            ]
                        )
                        admin_bot_api.push_message(owner.line_user_id, TemplateSendMessage(alt_text='มีผู้ขอลงทะเบียนใหม่', template=buttons_template))
                    except:
                        send_line_notify(f"🔔 มีผู้ขอลงทะเบียนห้อง {room_number}\nชื่อ: {tenant.full_name}")
                else:
                    send_line_notify(f"🔔 มีผู้ขอลงทะเบียนห้อง {room_number}")
        
        elif tenant.status == "Pending":
            reply_text = "คำขอของคุณอยู่ระหว่างการพิจารณา กรุณารอเจ้าของอนุมัติ"
        else: # Active
            room_number = tenant.room.room_number if tenant.room else "N/A"
            if text == "ดูค่าเช่า":
                invoice = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).first()
                if invoice:
                    reply_text = f"บิลเดือน {invoice.billing_month}/{invoice.billing_year}\nยอดรวม: {invoice.total_amount} บาท\nดูรายละเอียด: {BASE_URL}/bill/{invoice.uuid}"
                else:
                    reply_text = "ไม่พบข้อมูลบิลล่าสุด"
            elif text == "แจ้งซ่อม":
                reply_text = f"แจ้งซ่อมห้อง {room_number}:\n{BASE_URL}/repair/{tenant.uuid}"
            elif text == "ประวัติ":
                reply_text = f"ดูประวัติย้อนหลัง:\n{BASE_URL}/history/{tenant.uuid}"
            else:
                reply_text = f"สวัสดี ห้อง {room_number}!\nพิมพ์ 'ดูค่าเช่า', 'แจ้งซ่อม' หรือ 'ประวัติ'"
            
        if tenant_bot_api:
            tenant_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
    finally:
        if close_db:
            db.close()

# Tenant APIs
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
        promptpay_id = ""
        promptpay_name = ""
        bank_name = ""
        bank_account = ""
        
        # 1. Get room specific preference
        target_id = invoice.room.promptpay_id if invoice.room else None
        
    # 2. Parse config
    payment_config = {"cash": True, "promptpay": [], "bank_transfer": []}
    try:
        raw_config = json.loads(owner.promptpay_config)
        if isinstance(raw_config, dict) and "cash" in raw_config:
            payment_config = raw_config
        elif isinstance(raw_config, list):
            # Migration logic if needed, but for now just pass as is or wrap
            payment_config["promptpay"] = raw_config
    except: pass
    
    # Check room specific if needed (optional for this specific requirement change)
    
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
        "payment_config": payment_config,
        "qr_enabled": qr_enabled,
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
    invoices = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).all()
    return templates.TemplateResponse("history.html", {"request": request, "tenant": tenant, "invoices": invoices})

import bcrypt

def verify_password(plain_password, hashed_password):
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception as e:
        print(f"Password verification error: {e}")
        return False

def get_password_hash(password):
    # bcrypt has a 72-character limit, but for normal passwords this is fine.
    # We encode to utf-8 before hashing.
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

# Admin Security Dependency
def get_admin(request: Request, db: Session = Depends(get_db)):
    session_id = request.cookies.get("admin_session")
    if not session_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    owner = db.query(models.Owner).first()
    # Simple check: session_id matches the hash of current password (or we can use a dedicated session table)
    # For now, let's keep it simple: the session cookie will store a signed value or just a secure token.
    # To keep compatibility with existing structure but improved:
    if not owner or session_id != str(owner.id): # Using owner.id as a simple session for now, ideally use a token
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, db: Session = Depends(get_db)):
    owner = db.query(models.Owner).first()
    first_run = owner is None or owner.admin_email is None
    return templates.TemplateResponse("login.html", {"request": request, "first_run": first_run})

@app.post("/admin/login")
async def admin_login(request: Request, db: Session = Depends(get_db), password: str = Form(...), email: str = Form(None)):
    client_ip = request.client.host
    
    # Check Lockout
    attempt = db.query(models.LoginAttempt).filter(models.LoginAttempt.ip_address == client_ip).first()
    if attempt and attempt.lockout_until and attempt.lockout_until > datetime.now():
        wait_min = int((attempt.lockout_until - datetime.now()).total_seconds() / 60)
        return RedirectResponse(url=f"/admin/login?error=locked&wait={wait_min}", status_code=303)

    owner = db.query(models.Owner).first()
    
    # First run setup
    if not owner or owner.admin_email is None:
        if not email:
            return RedirectResponse(url="/admin/login?error=email_required", status_code=303)
        
        if not owner:
            owner = models.Owner(line_user_id="ADMIN_TEMP") # Placeholder
            db.add(owner)
        
        owner.admin_email = email
        owner.password_hash = get_password_hash(password)
        db.commit()
        
        # Reset attempts on success
        if attempt:
            attempt.attempts = 0
            attempt.lockout_until = None
            db.commit()

        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(key="admin_session", value=str(owner.id), httponly=True)
        return response

    if verify_password(password, owner.password_hash):
        # Reset attempts on success
        if attempt:
            attempt.attempts = 0
            attempt.lockout_until = None
            db.commit()
            
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(key="admin_session", value=str(owner.id), httponly=True)
        return response
    
    # Failed Attempt Logic
    if not attempt:
        attempt = models.LoginAttempt(ip_address=client_ip, attempts=1)
        db.add(attempt)
    else:
        attempt.attempts += 1
        if attempt.attempts >= 3:
            attempt.lockout_until = datetime.now() + timedelta(hours=1)
            db.commit()
            return RedirectResponse(url="/admin/login?error=locked&wait=60", status_code=303)
    
    db.commit()
    return RedirectResponse(url="/admin/login?error=1", status_code=303)

import smtplib
from email.mime.text import MIMEText
from datetime import timedelta

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

def send_reset_email(email: str, token: str):
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"MOCK EMAIL to {email}: Reset link is {BASE_URL}/admin/reset-password?token={token}")
        return
    
    msg = MIMEText(f"คลิกที่ลิงก์เพื่อรีเซ็ตรหัสผ่าน: {BASE_URL}/admin/reset-password?token={token}\n\nลิงก์จะหมดอายุใน 1 ชั่วโมง")
    msg['Subject'] = "Reset Password - SukAnan Apartment"
    msg['From'] = SMTP_USER
    msg['To'] = email
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"Email Error: {e}")

@app.get("/admin/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})

@app.post("/admin/forgot-password")
async def forgot_password(db: Session = Depends(get_db), email: str = Form(...)):
    owner = db.query(models.Owner).filter(models.Owner.admin_email == email).first()
    if owner:
        token = uuid.uuid4().hex
        owner.reset_token = token
        owner.reset_token_expiry = datetime.now() + timedelta(hours=1)
        db.commit()
        send_reset_email(email, token)
        
    # Always redirect to success to prevent email enumeration
    return RedirectResponse(url="/admin/forgot-password?sent=1", status_code=303)

@app.get("/admin/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str, db: Session = Depends(get_db)):
    owner = db.query(models.Owner).filter(
        models.Owner.reset_token == token,
        models.Owner.reset_token_expiry > datetime.now()
    ).first()
    if not owner:
        return HTMLResponse("ลิงก์รีเซ็ตรหัสผ่านไม่ถูกต้องหรือหมดอายุ", status_code=400)
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token})

@app.post("/admin/reset-password")
async def reset_password(
    token: str = Form(...), 
    password: str = Form(...), 
    db: Session = Depends(get_db)
):
    owner = db.query(models.Owner).filter(
        models.Owner.reset_token == token,
        models.Owner.reset_token_expiry > datetime.now()
    ).first()
    
    if not owner:
        return HTMLResponse("ลิงก์รีเซ็ตรหัสผ่านไม่ถูกต้องหรือหมดอายุ", status_code=400)
    
    owner.password_hash = get_password_hash(password)
    owner.reset_token = None
    owner.reset_token_expiry = None
    db.commit()
    
    return RedirectResponse(url="/admin/login?reset=1", status_code=303)

@app.get("/admin/logout")
async def admin_logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response

# Update admin_dashboard
@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
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
    
    all_rooms = db.query(models.Room).all()
    all_buildings = db.query(models.Building).all()
    owner = db.query(models.Owner).first()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "recent_invoices": recent_invoices,
        "recent_repairs": recent_repairs,
        "active_tenants": active_tenants,
        "pending_registrations": pending_registrations,
        "all_rooms": all_rooms,
        "all_buildings": all_buildings,
        "owner": owner,
        "current_month": datetime.now().month,
        "current_year": datetime.now().year
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
        lease_content = owner.lease_template.format(
            tenant_name=tenant.full_name,
            room_number=room.room_number,
            floor=room.floor,
            base_rent=f"{room.base_rent:,.2f}",
            start_date=datetime.now().strftime("%d/%m/%Y"),
            initial_fees=fees_text
        )

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
        
        # Save History for all residents
        for res in tenant.residents:
            history = models.TenantHistory(
                room_number=room.room_number if room else "N/A",
                tenant_uuid=tenant.uuid,
                first_name=res.first_name,
                last_name=res.last_name,
                nickname=res.nickname,
                phone_number=res.phone_number,
                workplace=res.workplace,
                start_date=lease.start_date,
                end_date=lease.end_date
            )
            db.add(history)
        
    tenant.current_room_id = None
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


def send_receipt_flex(tenant_line_id: str, invoice: models.Invoice, db: Session):
    if not tenant_line_id or not tenant_bot_api:
        return

    from linebot.models import FlexSendMessage
    import json
    
    room_number = invoice.room.room_number if invoice.room else "N/A"
    billing_period = f"{invoice.billing_month}/{invoice.billing_year}"
    total_str = f"{invoice.total_amount:,.2f}"
    paid_date = invoice.paid_at.strftime("%d/%m/%Y %H:%M") if invoice.paid_at else datetime.now().strftime("%d/%m/%Y %H:%M")
    
    # Detailed charges for receipt
    other_charges_list = []
    if invoice.other_charges:
        try:
            other_charges_list = json.loads(invoice.other_charges)
        except: pass

    # Build charge rows for Flex
    charge_rows = [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "ค่าเช่าห้อง", "size": "sm", "color": "#555555", "flex": 0},
                {"type": "text", "text": f"{invoice.rent_amount:,.2f} ฿", "size": "sm", "color": "#111111", "align": "end"}
            ]
        },
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "ค่าไฟ", "size": "sm", "color": "#555555", "flex": 0},
                {"type": "text", "text": f"{invoice.electricity_amount:,.2f} ฿", "size": "sm", "color": "#111111", "align": "end"}
            ]
        },
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "ค่าน้ำ", "size": "sm", "color": "#555555", "flex": 0},
                {"type": "text", "text": f"{invoice.water_amount:,.2f} ฿", "size": "sm", "color": "#111111", "align": "end"}
            ]
        }
    ]
    
    for c in other_charges_list:
        charge_rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": c.get('description', 'อื่นๆ'), "size": "sm", "color": "#555555", "flex": 0},
                {"type": "text", "text": f"{float(c.get('amount', 0)):,.2f} ฿", "size": "sm", "color": "#111111", "align": "end"}
            ]
        })
        
    if invoice.late_fee > 0:
        charge_rows.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": "ค่าปรับจ่ายช้า", "size": "sm", "color": "#e74c3c", "flex": 0},
                {"type": "text", "text": f"{invoice.late_fee:,.2f} ฿", "size": "sm", "color": "#e74c3c", "align": "end"}
            ]
        })

    flex_contents = {
      "type": "bubble",
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {"type": "text", "text": "RECEIPT", "weight": "bold", "color": "#1DB446", "size": "sm"},
          {"type": "text", "text": "สุขอนันต์ อพาร์ทเม้นท์", "weight": "bold", "size": "xxl", "margin": "md"},
          {"type": "text", "text": f"ห้อง {room_number}", "size": "xs", "color": "#aaaaaa", "wrap": True},
          {"type": "separator", "margin": "xxl"},
          {
            "type": "box",
            "layout": "vertical",
            "margin": "xxl",
            "spacing": "sm",
            "contents": charge_rows
          },
          {"type": "separator", "margin": "xxl"},
          {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
              {"type": "text", "text": "รวมทั้งสิ้น", "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
              {"type": "text", "text": f"{total_str} ฿", "size": "lg", "color": "#111111", "align": "end", "weight": "bold"}
            ]
          },
          {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
              {"type": "text", "text": "วันที่ชำระ", "size": "xs", "color": "#aaaaaa", "flex": 0},
              {"type": "text", "text": paid_date, "size": "xs", "color": "#aaaaaa", "align": "end"}
            ]
          }
        ]
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
            "action": {
              "type": "uri",
              "label": "ดูรายละเอียดบิล",
              "uri": f"{BASE_URL}/bill/{invoice.uuid}"
            }
          },
          {"type": "spacer", "size": "sm"}
        ],
        "flex": 0
      }
    }
    
    try:
        tenant_bot_api.push_message(tenant_line_id, FlexSendMessage(alt_text=f"ใบเสร็จรับเงิน ห้อง {room_number}", contents=flex_contents))
    except Exception as e:
        print(f"Flex Message Error: {e}")

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
    
    # Notify tenant with Flex Receipt
    if invoice.tenant and invoice.tenant.line_user_id:
        send_receipt_flex(invoice.tenant.line_user_id, invoice, db)

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
    
    # Notify tenant with Flex Receipt
    if invoice.tenant and invoice.tenant.line_user_id:
        send_receipt_flex(invoice.tenant.line_user_id, invoice, db)
        
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

    if invoice.status == "Paid":
        send_receipt_flex(tenant.line_user_id, invoice, db)
        return {"status": "Success", "type": "receipt"}

    msg = f"📄 ใบแจ้งค่าเช่าเดือน {invoice.billing_month}/{invoice.billing_year}\n"
    msg += f"ห้อง {invoice.room.room_number if invoice.room else 'N/A'}\n"
    msg += f"ยอดรวม: {invoice.total_amount:,.2f} บาท\n\n"
    msg += f"ดูรายละเอียดและแจ้งชำระเงินได้ที่:\n{BASE_URL}/bill/{invoice.uuid}"
    
    try:
        tenant_bot_api.push_message(tenant.line_user_id, TextSendMessage(text=msg))
        return {"status": "Success", "type": "invoice"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LINE Error: {str(e)}")

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

@app.post("/admin/settings/save")
async def save_settings(
    display_name: str = Form(...),
    promptpay_config: str = Form(...),
    qr_enabled: int = Form(...),
    late_fee_enabled: int = Form(0),
    due_day: int = Form(5),
    late_fee_per_day: float = Form(50.0),
    lease_template: str = Form(None),
    move_in_fees_config: str = Form(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    owner = db.query(models.Owner).first()
    if not owner:
        owner = models.Owner(line_user_id="Uf471c296504bb803caa0d0a83ea0b4f6")
        db.add(owner)
    
    owner.display_name = display_name
    owner.promptpay_config = promptpay_config
    owner.qr_payment_enabled = qr_enabled
    owner.late_fee_enabled = late_fee_enabled
    owner.due_day = due_day
    owner.late_fee_per_day = late_fee_per_day
    
    if move_in_fees_config:
        owner.move_in_fees_config = move_in_fees_config
    
    if lease_template:
        owner.lease_template = lease_template
    elif not owner.lease_template:
        owner.lease_template = """
        <div style="font-family: 'Sarabun', sans-serif; line-height: 1.6;">
            <h2 style="text-align: center;">สัญญาเช่าที่พักอาศัย</h2>
            <p>ทำขึ้นเมื่อวันที่ {start_date}</p>
            <p><strong>ผู้เช่า:</strong> {tenant_name}</p>
            <p><strong>ห้องพักเลขที่:</strong> {room_number} ชั้น {floor}</p>
            <p><strong>อัตราค่าเช่า:</strong> {base_rent} บาท/เดือน</p>
            <p><strong>เงื่อนไขเพิ่มเติม:</strong> ...</p>
        </div>
        """
    
    db.commit()
    db.refresh(owner)
    print(f"DEBUG: Saved PP Config: {owner.promptpay_config}")
    return {"status": "Success"}

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
async def export_report(month: int, year: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoices = db.query(models.Invoice).filter(
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year,
        models.Invoice.status == "Paid"
    ).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Room", "Month", "Year", "Rent", "Water", "Elec", "Total", "Paid At"])
    
    total_income = 0
    for inv in invoices:
        writer.writerow([
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
    writer.writerow(["Total Income", "", "", "", "", "", total_income, ""])
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=report_{year}_{month}.csv"}
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

    return {
        "found": True if reading else False,
        "electricity": reading.electricity_reading if reading else None,
        "water": reading.water_reading if reading else None,
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
async def get_repair_history(room_id: int = None, exclude_fixed: bool = False, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.MaintenanceRequest).join(models.Room)
    if room_id:
        query = query.filter(models.MaintenanceRequest.room_id == room_id)
    if exclude_fixed:
        query = query.filter(models.MaintenanceRequest.status != 'Fixed')
    
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
        elec = r.get("elec")
        water = r.get("water")
        other_charges = r.get("other_charges") # List of dicts
        
        if room_id is None or elec is None or water is None:
            continue
            
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
