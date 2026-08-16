import os
import json
import uuid
import shutil
import logging
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session, joinedload

import models
import services.promptpay as promptpay
import services.billing as billing
from models.database import get_db
import config
from config import templates, get_text

class SendLineNotifyProxy:
    def __call__(self, *args, **kwargs):
        return config.send_line_notify(*args, **kwargs)

send_line_notify = SendLineNotifyProxy()
class BotApiProxy:
    def __init__(self, name):
        self._name = name
    def __getattr__(self, item):
        bot = getattr(config, self._name)
        if bot is None:
            raise AttributeError(f"{self._name} is not initialized")
        return getattr(bot, item)
    def __bool__(self):
        return getattr(config, self._name) is not None

tenant_bot_api = BotApiProxy("tenant_bot_api")
admin_bot_api = BotApiProxy("admin_bot_api")

class BaseUrlProxy:
    def _get_val(self):
        val = getattr(config, 'BASE_URL', None) or os.getenv('BASE_URL', '')
        val = str(val).rstrip("/")
        if not val or not val.startswith("http"):
            val = "https://sukanan.kookai.cloud"
        return val
    def __str__(self):
        return self._get_val()
    def __repr__(self):
        return repr(self._get_val())
    def __format__(self, format_spec):
        return self._get_val().__format__(format_spec)
    def __getattr__(self, item):
        return getattr(self._get_val(), item)
    def rstrip(self, chars=None):
        return self._get_val().rstrip(chars)
    def __add__(self, other):
        return self._get_val() + str(other)
    def __radd__(self, other):
        return str(other) + self._get_val()

BASE_URL = BaseUrlProxy()
from linebot.v3.messaging import (
    PushMessageRequest,
    TextMessage,
    ImageMessage
)

logger = logging.getLogger(__name__)

router = APIRouter()

uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")

from fastapi.responses import RedirectResponse

@router.get("/booking")
async def start_booking(request: Request, uid: str = None, lang: str = "th", db: Session = Depends(get_db)):
    line_user_id = uid or request.query_params.get("uid") or f"guest_{uuid.uuid4().hex[:12]}"
    booking_uuid = str(uuid.uuid4())
    return RedirectResponse(url=f"/booking/{booking_uuid}?uid={line_user_id}&lang={lang}")

@router.get("/booking/{booking_uuid}", response_class=HTMLResponse)
async def view_booking_form(request: Request, booking_uuid: str, db: Session = Depends(get_db)):
    uid = request.query_params.get("uid") or ""
    lang = request.query_params.get("lang") or "th"
    
    owner = db.query(models.Owner).first()
    owner_name = owner.display_name if owner and owner.display_name else "SukAnan Apartment"
    lease_content = owner.lease_template if owner and owner.lease_template else ""
    buildings = db.query(models.Building).all()

    return templates.TemplateResponse("booking.html", {
        "request": request,
        "booking_uuid": booking_uuid,
        "line_user_id": uid,
        "owner_name": owner_name,
        "lease_content": lease_content,
        "buildings": buildings,
        "lang": lang
    })

@router.post("/booking/{booking_uuid}")
async def submit_booking_form(booking_uuid: str, data: dict, db: Session = Depends(get_db)):
    full_name = data.get("full_name")
    phone_number = data.get("phone_number")
    workplace_name = data.get("workplace_name")
    job_position = data.get("job_position")
    workplace_phone = data.get("workplace_phone")
    requested_move_in_date_str = data.get("requested_move_in_date")
    line_user_id = data.get("line_user_id") or "guest"
    preferred_building_id = data.get("preferred_building_id")
    agreement_accepted = data.get("agreement_accepted", False)
    language = data.get("language", "th")

    if not agreement_accepted:
        raise HTTPException(status_code=400, detail="คุณต้องยอมรับสัญญาและกฎระเบียบหอพักก่อนทำการจอง")

    if not all([full_name, phone_number, workplace_name, job_position, workplace_phone, requested_move_in_date_str]):
        raise HTTPException(status_code=400, detail=get_text('error_missing_fields', language))

    try:
        requested_move_in_date = datetime.strptime(requested_move_in_date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="รูปแบบวันที่ไม่ถูกต้อง")

    # Check if booking with this uuid already exists or create new
    booking = db.query(models.BookingRequest).filter(models.BookingRequest.uuid == booking_uuid).first()
    if not booking:
        booking = models.BookingRequest(
            uuid=booking_uuid,
            line_user_id=line_user_id,
            full_name=full_name,
            phone_number=phone_number,
            workplace_name=workplace_name,
            job_position=job_position,
            workplace_phone=workplace_phone,
            requested_move_in_date=requested_move_in_date,
            preferred_building_id=int(preferred_building_id) if preferred_building_id else None,
            agreement_accepted=1,
            language=language,
            status="Pending"
        )
        db.add(booking)
    else:
        booking.full_name = full_name
        booking.phone_number = phone_number
        booking.workplace_name = workplace_name
        booking.job_position = job_position
        booking.workplace_phone = workplace_phone
        booking.requested_move_in_date = requested_move_in_date
        booking.preferred_building_id = int(preferred_building_id) if preferred_building_id else None
        booking.agreement_accepted = 1
        booking.language = language
        booking.status = "Pending"

    db.commit()
    db.refresh(booking)

    # Notify Owner/Admin via LINE
    owner = db.query(models.Owner).first()
    if owner and owner.line_user_id and admin_bot_api:
        lang = owner.language or "th"
        msg = get_text('notify_new_booking', lang).format(
            name=full_name,
            phone=phone_number,
            workplace=workplace_name,
            position=job_position,
            work_phone=workplace_phone,
            date=requested_move_in_date_str
        )
        try:
            admin_bot_api.push_message(
                PushMessageRequest(
                    to=owner.line_user_id,
                    messages=[TextMessage(text=msg)]
                )
            )
        except Exception as e:
            logger.error(f"Failed to notify admin of new booking: {e}")

    return {"status": "Success", "booking_id": booking.id, "uuid": booking.uuid}

@router.get("/register/{tenant_uuid}", response_class=HTMLResponse)
async def view_registration(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Try to find default data from previous stays
    default_data = None

    prev_tenant = db.query(models.Tenant).filter(
        models.Tenant.line_user_id == tenant.line_user_id,
        models.Tenant.id != tenant.id
    ).order_by(models.Tenant.id.desc()).first()

    if prev_tenant:
        default_data = {
            "full_name": prev_tenant.full_name,
            "phone_number": prev_tenant.phone_number,
            "citizen_id": prev_tenant.citizen_id
        }

    return templates.TemplateResponse("register.html", {
        "request": request, 
        "tenant_uuid": tenant_uuid,
        "default_data": default_data,
        "lang": request.query_params.get("lang", "th")
    })

@router.post("/register/{tenant_uuid}")
async def submit_registration(tenant_uuid: str, data: dict, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    full_name = data.get("full_name")
    phone_number = data.get("phone_number")
    citizen_id = data.get("citizen_id")
    requested_move_in_date_str = data.get("requested_move_in_date")
    language = data.get("language", "th")
    
    if not all([full_name, phone_number, citizen_id, requested_move_in_date_str]):
        raise HTTPException(status_code=400, detail=get_text('error_missing_fields', language))
        
    tenant.full_name = full_name
    tenant.phone_number = phone_number
    tenant.citizen_id = citizen_id
    tenant.requested_move_in_date = datetime.strptime(requested_move_in_date_str, "%Y-%m-%d")
    tenant.language = language
    tenant.status = "Pending"
    db.commit()
    
    # Notify Owner
    owner = db.query(models.Owner).first()
    if owner and owner.line_user_id and admin_bot_api:
        lang = owner.language or "th"
        msg = get_text('notify_new_registration', lang).format(
            name=full_name, 
            phone=phone_number, 
            date=requested_move_in_date_str
        )
        try: 
            admin_bot_api.push_message(
                PushMessageRequest(
                    to=owner.line_user_id,
                    messages=[TextMessage(text=msg)]
                )
            )
        except Exception: pass
        
    return {"status": "Success"}

@router.get("/move-out/{tenant_uuid}", response_class=HTMLResponse)
async def view_move_out(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant or tenant.status != "Active":
        raise HTTPException(status_code=404, detail="Tenant not found or not active")
    
    lang = request.query_params.get("lang") or tenant.language or "th"
    return templates.TemplateResponse("move_out.html", {"request": request, "tenant": tenant, "lang": lang})

@router.post("/move-out/{tenant_uuid}")
async def submit_move_out(tenant_uuid: str, data: dict, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant or tenant.status != "Active":
        raise HTTPException(status_code=404, detail="Tenant not found or not active")
    
    requested_date_str = data.get("requested_date")
    reason = data.get("reason")
    
    if not requested_date_str:
        raise HTTPException(status_code=400, detail=get_text('error_missing_fields', tenant.language or "th"))
        
    requested_date = datetime.strptime(requested_date_str, "%Y-%m-%d")
    
    # Ensure room_id is present (safety for data integrity issues)
    room_id = tenant.current_room_id
    if not room_id:
        # Try to recover from active lease
        active_lease = db.query(models.Lease).filter(
            models.Lease.tenant_id == tenant.id,
            models.Lease.status == "Active"
        ).first()
        if active_lease:
            room_id = active_lease.room_id
            tenant.current_room_id = room_id
        else:
            raise HTTPException(status_code=400, detail=get_text('error_tenant_unmapped', tenant.language or "th"))

    # Prevent duplicate pending requests
    existing_req = db.query(models.MoveOutRequest).filter(
        models.MoveOutRequest.tenant_id == tenant.id,
        models.MoveOutRequest.status == "Pending"
    ).first()
    if existing_req:
        # Update existing request instead of creating new one
        existing_req.requested_date = requested_date
        existing_req.reason = reason
        existing_req.room_id = room_id
        req = existing_req
    else:
        # Create request record
        req = models.MoveOutRequest(
            tenant_id=tenant.id,
            room_id=room_id,
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
        lang = owner.language or "th"
        msg = get_text('notify_move_out', lang).format(
            room=tenant.room.room_number if tenant.room else 'N/A',
            name=tenant.full_name,
            date=requested_date.strftime('%d/%m/%Y')
        )
        try: admin_bot_api.push_message(PushMessageRequest(to=owner.line_user_id, messages=[TextMessage(text=msg)]))
        except Exception: pass
        
    return {"status": "Success"}

@router.get("/api/buildings/{bid}/vacant-rooms")
async def get_vacant_rooms(bid: int, db: Session = Depends(get_db)):
    rooms = db.query(models.Room).filter(models.Room.building_id == bid, models.Room.status == "Vacant").all()
    return [{"id": r.id, "room_number": r.room_number} for r in rooms]

@router.get("/bill/{invoice_uuid}", response_class=HTMLResponse)
async def view_bill(request: Request, invoice_uuid: str, db: Session = Depends(get_db)):
    invoice = db.query(models.Invoice).filter(models.Invoice.uuid == invoice_uuid).first()
    if not invoice:
        lang = request.query_params.get("lang", "th")
        if lang not in ["en", "th", "jp"]:
            lang = "th"
        return templates.TemplateResponse("bill_not_found.html", {
            "request": request,
            "lang": lang
        }, status_code=404)

    # Multi-language: Use lang from query param or tenant's profile
    lang = request.query_params.get("lang")
    if not lang and invoice.tenant:
        lang = invoice.tenant.language
    if not lang:
        lang = "th"

    if invoice.invoice_type == "Initial":
        return templates.TemplateResponse("uploadslip.html", {
            "request": request,
            "invoice": invoice,
            "invoice_uuid": invoice_uuid,
            "status": invoice.status,
            "lang": lang
        })
    
    owner = db.query(models.Owner).first()
    
    # Late fee calculation logic
    other_amount = 0
    if invoice.other_charges:
        try:
            other_amount = sum(float(item.get('amount', 0)) for item in json.loads(invoice.other_charges))
        except (json.JSONDecodeError, TypeError, ValueError): pass
        
    # Initial subtotal
    subtotal = invoice.rent_amount + invoice.electricity_amount + invoice.water_amount + other_amount
    
    late_fee = billing.get_late_fee(db, invoice=invoice)
    
    if invoice.status == "Unpaid" and late_fee != invoice.late_fee:
        invoice.late_fee = late_fee
        invoice.total_amount = subtotal + late_fee
        db.commit()
    else:
        late_fee = invoice.late_fee

    promptpay_id = None
    promptpay_name = None
    bank_info = None
    qr_enabled = 1
    
    if owner:
        qr_enabled = owner.qr_payment_enabled
        
        p_type = invoice.room.primary_payment_type if invoice.room else "PromptPay"
        p_id = invoice.room.primary_payment_id if invoice.room else None

        if p_type == "Bank":
            try:
                bank_list = json.loads(owner.bank_config)
                bank_info = next((b for b in bank_list if b.get('id') == p_id), None)
            except (json.JSONDecodeError, TypeError): pass
        else:
            # PromptPay logic
            target_id = p_id or (invoice.room.promptpay_id if invoice.room else None)
            config_list = []
            try:
                config_list = json.loads(owner.promptpay_config)
            except (json.JSONDecodeError, TypeError): pass
            
            if target_id and isinstance(config_list, list):
                match = next((c for c in config_list if c.get('id') == target_id), None)
                if match:
                    promptpay_id = match.get('id')
                    promptpay_name = match.get('name')
            
            if not promptpay_id and isinstance(config_list, list) and len(config_list) > 0:
                promptpay_id = config_list[0].get('id')
                promptpay_name = config_list[0].get('name')

    if not promptpay_id and not bank_info:
        promptpay_id = "0812345678"

    payload = ""
    if promptpay_id:
        try:
            payload = promptpay.generate_promptpay_payload(promptpay_id, invoice.total_amount)
        except Exception as e:
            logger.error(f"PromptPay Generation Error: {e}")

    # Multi-language: Use lang from query param or tenant's profile
    lang = request.query_params.get("lang")
    if not lang and invoice.tenant:
        lang = invoice.tenant.language
    if not lang:
        lang = "th"

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
        "bank_info": bank_info,
        "room": invoice.room,
        "lang": lang
    })

@router.post("/bill/{invoice_uuid}/upload-slip")
async def upload_slip(
    invoice_uuid: str, 
    image: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    invoice = db.query(models.Invoice).options(joinedload(models.Invoice.room)).filter(models.Invoice.uuid == invoice_uuid).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
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
    owner = db.query(models.Owner).first()
    lang = owner.language if owner else "th"
    send_line_notify(get_text('notify_new_slip', lang).format(room=room_number, amount=f"{invoice.total_amount:,.2f}"))
    
    if invoice.invoice_type == "Initial":
        if owner and owner.line_user_id and admin_bot_api:
            try:
                # Text message:
                msg_text = f"ผู้เช่าโอนเงินแรกเข้า กรุณาตรวจสอบ\nห้อง: {room_number}\nยอดเงิน: {invoice.total_amount:,.2f} บาท"
                admin_bot_api.push_message(
                    PushMessageRequest(
                        to=owner.line_user_id,
                        messages=[TextMessage(text=msg_text)]
                    )
                )
                
                # Image message with slip image:
                full_image_url = f"{BASE_URL}{invoice.payment_receipt_img}"
                admin_bot_api.push_message(
                    PushMessageRequest(
                        to=owner.line_user_id,
                        messages=[ImageMessage(
                            original_content_url=full_image_url,
                            preview_image_url=full_image_url
                        )]
                    )
                )
            except Exception as e:
                logger.error(f"Failed to push initial slip notification to admin bot: {e}")
                
    return {"status": "Success", "receipt": invoice.payment_receipt_img}

@router.get("/repair/{tenant_uuid}", response_class=HTMLResponse)
async def repair_form(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    lang = request.query_params.get("lang") or tenant.language or "th"
    return templates.TemplateResponse("repair.html", {
        "request": request,
        "tenant_id": tenant.id,
        "room_id": tenant.current_room_id,
        "room_number": tenant.room.room_number if tenant.room else "N/A",
        "lang": lang
    })

@router.post("/repair/submit")
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
    
    request_obj = models.MaintenanceRequest(
        tenant_id=tenant_id, 
        room_id=room_id, 
        title=title, 
        description=description, 
        image_url=image_url
    )
    db.add(request_obj)
    db.commit()
    
    room_number = room.room_number
    
    # Notify Owner via Admin Channel
    owner = db.query(models.Owner).first()
    lang = owner.language if owner else "th"
    msg = get_text('notify_new_repair_admin', lang).format(
        room=room_number,
        title=title,
        description=description,
        url=f"{BASE_URL}/admin/dashboard"
    )
    
    if owner and owner.line_user_id and admin_bot_api:
        try:
            # Send text message first
            admin_bot_api.push_message(PushMessageRequest(to=owner.line_user_id, messages=[TextMessage(text=msg)]))
            
            # If there's an image, send it too
            if image_url:
                full_image_url = f"{BASE_URL}{image_url}"
                admin_bot_api.push_message(
                    PushMessageRequest(
                        to=owner.line_user_id,
                        messages=[ImageMessage(
                            original_content_url=full_image_url,
                            preview_image_url=full_image_url
                        )]
                    )
                )
        except Exception as e:
            logger.error(f"Admin Push Error (Repair): {e}")
            send_line_notify(msg)
    else:
        send_line_notify(msg)

    return {"status": "Success"}

@router.get("/history/{tenant_uuid}", response_class=HTMLResponse)
async def view_history(request: Request, tenant_uuid: str, db: Session = Depends(get_db)):
    tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    lang = request.query_params.get("lang") or tenant.language or "th"

    # Robust check: fetch by room_id so history is complete for the specific room
    if tenant.current_room_id:
        invoices = db.query(models.Invoice).filter(models.Invoice.room_id == tenant.current_room_id).order_by(models.Invoice.id.desc()).all()
    else:
        invoices = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).all()

    return templates.TemplateResponse("history.html", {"request": request, "tenant": tenant, "invoices": invoices, "lang": lang})
