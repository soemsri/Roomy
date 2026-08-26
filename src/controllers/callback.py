import logging
import os
import traceback
import re
import uuid
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from models.database import get_db, SessionLocal
import models
import config
from config import get_text, get_magic_url

def clean_html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    text = re.sub(r'<(br|/p|/li|/div|tr|/h[1-6])>', '\n', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)

class HandlerProxy:
    def __init__(self, name):
        self._name = name
    def __getattr__(self, item):
        handler = getattr(config, self._name)
        if handler is None:
            raise AttributeError(f"{self._name} is not initialized")
        return getattr(handler, item)
    def __bool__(self):
        return getattr(config, self._name) is not None

admin_handler = HandlerProxy("admin_handler")
tenant_handler = HandlerProxy("tenant_handler")
admin_bot_api = HandlerProxy("admin_bot_api")
tenant_bot_api = HandlerProxy("tenant_bot_api")

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
from services.billing import create_initial_invoice
from services.line_bot import send_initial_payment_flex, setup_personal_rich_menu
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    PostbackEvent
)

logger = logging.getLogger(__name__)

def safe_reply_or_push(bot_api, reply_token: str, user_id: str, messages: list):
    """
    Safely attempts to reply to a LINE event using reply_token.
    If reply_message fails (e.g. invalid/expired reply_token due to LINE auto-response),
    it automatically falls back to pushing the messages directly to the user_id.
    """
    if not bot_api or not messages:
        return
    try:
        bot_api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=messages))
    except Exception as e:
        logger.warning(f"reply_message failed ({e}), falling back to push_message for {user_id}")
        if user_id:
            try:
                bot_api.push_message(PushMessageRequest(to=user_id, messages=messages))
            except Exception as push_err:
                logger.error(f"push_message fallback also failed: {push_err}")

router = APIRouter()

@router.post("/callback")
@router.post("/callback/admin")
async def callback_admin(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_str = body.decode("utf-8")
    
    if not admin_handler:
        logger.warning("Admin Handler not initialized. Check LINE_ADMIN_CHANNEL_SECRET.")
        return "OK"
        
    try:
        admin_handler.handle(body_str, signature)
    except InvalidSignatureError:
        logger.error("Admin Webhook Error: Invalid Signature. Check LINE_ADMIN_CHANNEL_SECRET.")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Admin Webhook Error: {e}")
        traceback.print_exc()
    return "OK"

@router.post("/callback/tenant")
async def callback_tenant(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    body_str = body.decode("utf-8")
    
    if not tenant_handler:
        logger.warning("Tenant Handler not initialized. Check LINE_TENANT_CHANNEL_SECRET.")
        return "OK"
        
    try:
        tenant_handler.handle(body_str, signature)
    except InvalidSignatureError:
        logger.error("Tenant Webhook Error: Invalid Signature. Check LINE_TENANT_CHANNEL_SECRET.")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Tenant Webhook Error: {e}")
        traceback.print_exc()
    return "OK"

# Hook handlers to the handlers imported from config
if admin_handler:
    @admin_handler.add(MessageEvent, message=TextMessageContent)
    def handle_admin_message(event, *args, **kwargs):
        destination = args[0] if len(args) > 0 else None
        db = kwargs.get('db')

        if isinstance(destination, Session):
            db = destination
            destination = None

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
            if text.isdigit() and len(text) == 6:
                owner_by_code = db.query(models.Owner).filter(models.Owner.pairing_code == text).first()
                if owner_by_code:
                    owner_by_code.line_user_id = user_id
                    owner_by_code.pairing_code = None 
                    db.commit()
                    lang = owner_by_code.language or "th"
                    reply_text = get_text('admin_connected_success', lang)
                    if admin_bot_api:
                        admin_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TextMessage(text=reply_text)]
                            )
                        )
                    return

            owner = db.query(models.Owner).filter(models.Owner.line_user_id == user_id).first()
            lang = owner.language if owner else "th"

            if not owner:
                if not db.query(models.Owner).first():
                    owner = models.Owner(line_user_id=user_id, display_name="Owner")
                    db.add(owner)
                    db.commit()
                    reply_text = get_text('owner_registered_success', lang)
                else:
                    reply_text = get_text('unauthorized_admin', lang)
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
                                success_rooms, g_deposit, g_advance, g_other, g_total = create_initial_invoice(db, target_tenant, [room.id], owner)
                                db.commit()

                                if success_rooms:
                                    reply_text = get_text('approve_tenant_success', lang).format(room=room.room_number)
                                    if tenant_bot_api:
                                        try:
                                            inv = db.query(models.Invoice).filter(
                                                models.Invoice.tenant_id == target_tenant.id,
                                                models.Invoice.invoice_type == "Initial",
                                                models.Invoice.status == "Unpaid"
                                            ).first()
                                            inv_uuid = inv.uuid if inv else None
                                            send_initial_payment_flex(target_tenant, success_rooms, g_deposit, g_advance, g_other, g_total, owner, tenant_bot_api, invoice_uuid=inv_uuid)
                                        except Exception as e:
                                            logger.error(f"Failed to notify tenant from LINE: {e}")
                                else:
                                    reply_text = get_text('approve_tenant_error', lang).format(room=room.room_number)
                            else:
                                reply_text = get_text('room_not_found', lang)
                        else: # REJECT_REG_
                            target_tenant.status = "Rejected"
                            target_tenant.current_room_id = None
                            db.commit()
                            reply_text = get_text('reject_registration_success', lang)
                            if tenant_bot_api:
                                tenant_bot_api.push_message(
                                    PushMessageRequest(
                                        to=target_tenant.line_user_id,
                                        messages=[TextMessage(text=get_text('registration_rejected_msg', target_tenant.language or "th"))]
                                    )
                                )
                    else:
                        reply_text = get_text('tenant_not_found', lang)

                elif text == "ผังห้อง" or text == get_text('rooms', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"🏠 {get_text('manage_rooms', lang)}:\n{url}"
                elif text == "จดมิเตอร์" or text == get_text('meter_reading', lang):
                    url_single = get_magic_url(owner, db)
                    url_bulk = url_single + "&mode=bulk" 
                    flex_contents = {
                        "type": "bubble",
                        "header": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {"type": "text", "text": f"📊 {get_text('meter_billing', lang)}", "weight": "bold", "size": "xl", "color": "#FFFFFF"}
                            ],
                            "backgroundColor": "#0078d4",
                            "paddingAll": "20px"
                        },
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {"type": "text", "text": get_text('bulk_meter_instruction', lang), "size": "sm", "color": "#888888"},
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
                                                "label": get_text('single_reading', lang),
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
                                                "label": get_text('bulk_reading', lang),
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
                        safe_reply_or_push(
                            admin_bot_api,
                            event.reply_token,
                            user_id,
                            [FlexMessage(alt_text=get_text('meter_reading', lang), contents=FlexContainer.from_dict(flex_contents))]
                        )
                    return
                elif text == "สรุปรายรับ" or text == get_text('analytics', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"💰 {get_text('export_report', lang)}:\n{url}#billSection"
                elif text == "จัดการสัญญา" or text == get_text('manage_leases', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"📜 {get_text('manage_leases', lang)}:\n{url}#leaseSection"
                elif text in ["สรุป", "ภาพรวม", "แดชบอร์ด"]:
                    total_rooms = db.query(models.Room).count()
                    occupied_rooms = db.query(models.Room).filter(models.Room.status == "Occupied").count()
                    vacant_rooms = db.query(models.Room).filter(models.Room.status == "Vacant").count()
                    
                    unpaid_invs = db.query(models.Invoice).filter(models.Invoice.status.in_(["Unpaid", "Pending Verification"])).all()
                    unpaid_count = len(unpaid_invs)
                    unpaid_total = sum([inv.total_amount for inv in unpaid_invs])
                    
                    pending_repairs = db.query(models.MaintenanceRequest).filter(models.MaintenanceRequest.status == "Pending").count()
                    pending_regs = db.query(models.Tenant).filter(models.Tenant.status == "Pending").count()
                    pending_bookings = db.query(models.BookingRequest).filter(models.BookingRequest.status == "Pending").count()
                    
                    flex_contents = {
                        "type": "bubble",
                        "header": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {"type": "text", "text": "📊 สรุปภาพรวมหอพัก", "weight": "bold", "size": "lg", "color": "#FFFFFF"}
                            ],
                            "backgroundColor": "#1E3A8A",
                            "paddingAll": "15px"
                        },
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "contents": [
                                {
                                    "type": "box",
                                    "layout": "horizontal",
                                    "contents": [
                                        {"type": "text", "text": "ห้องพักทั้งหมด", "size": "sm", "color": "#555555"},
                                        {"type": "text", "text": f"{total_rooms} ห้อง", "size": "sm", "color": "#111111", "align": "end", "weight": "bold"}
                                    ]
                                },
                                {
                                    "type": "box",
                                    "layout": "horizontal",
                                    "margin": "md",
                                    "contents": [
                                        {"type": "text", "text": "มีผู้เช่า / ว่าง", "size": "sm", "color": "#555555"},
                                        {"type": "text", "text": f"{occupied_rooms} / {vacant_rooms} ห้อง", "size": "sm", "color": "#111111", "align": "end"}
                                    ]
                                },
                                {"type": "separator", "margin": "lg"},
                                {
                                    "type": "box",
                                    "layout": "horizontal",
                                    "margin": "lg",
                                    "contents": [
                                        {"type": "text", "text": "บิลรอชำระ/ตรวจสอบ", "size": "sm", "color": "#E11D48"},
                                        {"type": "text", "text": f"{unpaid_count} บิล (฿{unpaid_total:,.2f})", "size": "sm", "color": "#E11D48", "align": "end", "weight": "bold"}
                                    ]
                                },
                                {
                                    "type": "box",
                                    "layout": "horizontal",
                                    "margin": "md",
                                    "contents": [
                                        {"type": "text", "text": "แจ้งซ่อมรอดำเนินการ", "size": "sm", "color": "#D97706"},
                                        {"type": "text", "text": f"{pending_repairs} รายการ", "size": "sm", "color": "#D97706", "align": "end", "weight": "bold"}
                                    ]
                                },
                                {
                                    "type": "box",
                                    "layout": "horizontal",
                                    "margin": "md",
                                    "contents": [
                                        {"type": "text", "text": "รอคัดเลือกจองห้อง", "size": "sm", "color": "#0284C7"},
                                        {"type": "text", "text": f"{pending_bookings} คน", "size": "sm", "color": "#0284C7", "align": "end", "weight": "bold"}
                                    ]
                                }
                            ]
                        }
                    }
                    if admin_bot_api:
                        safe_reply_or_push(
                            admin_bot_api,
                            event.reply_token,
                            user_id,
                            [FlexMessage(alt_text="📊 สรุปภาพรวมหอพัก", contents=FlexContainer.from_dict(flex_contents))]
                        )
                        return
                    reply_text = f"📊 สรุปภาพรวม:\n- ห้องพัก: {occupied_rooms}/{total_rooms} (ว่าง {vacant_rooms})\n- บิลรอชำระ: {unpaid_count} รายการ (฿{unpaid_total:,.2f})\n- แจ้งซ่อมค้าง: {pending_repairs} รายการ"
                else:
                    reply_text = f"สวัสดีครับผู้ดูแลระบบ\nคุณสามารถพิมพ์:\n- 'สรุป' เพื่อดูภาพรวมหอพัก\n- 'ห้อง [เลขห้อง]' เช่น 'ห้อง 101' เพื่อดูสถานะห้อง\n- หรือจัดการผ่านเว็บแอดมิน: {BASE_URL}/admin"
                
                if admin_bot_api:
                    safe_reply_or_push(admin_bot_api, event.reply_token, user_id, [TextMessage(text=reply_text)])
        finally:
            if close_db:
                db.close()

if tenant_handler:
    @tenant_handler.add(MessageEvent, message=TextMessageContent)
    def handle_tenant_message(event, *args, **kwargs):
        destination = args[0] if len(args) > 0 else None
        db = kwargs.get('db')
        
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
            tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id == user_id).all()
            active_tenants = [t for t in tenants if t.status == "Active"]
            lang = active_tenants[0].language if active_tenants else (tenants[0].language if tenants else "th")

            # 1. Language Switching
            if text.lower() in ["language", "lang", "ภาษา", "เปลี่ยนภาษา"]:
                safe_reply_or_push(tenant_bot_api, event.reply_token, user_id, [TextMessage(text=get_text('tenant_language_choose', lang))])
                return

            elif text.upper() in ["TH", "EN", "JP"]:
                new_lang = text.lower()
                for t in tenants:
                    t.language = new_lang
                db.commit()
                lang = new_lang
                
                if active_tenants:
                    setup_personal_rich_menu(active_tenants[0], db, force=True)
                
                safe_reply_or_push(tenant_bot_api, event.reply_token, user_id, [TextMessage(text=get_text('language_changed', lang))])
                return

            # 2. Room Booking keywords (Available to everyone)
            elif text in ["จองห้องพัก", "จองห้อง", "จอง", "booking", "book"] or text == get_text('book_room', lang):
                booking_url = f"{BASE_URL}/booking?uid={user_id}&lang={lang}"
                flex_contents = {
                    "type": "bubble",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": "🏠 SukAnan Apartment", "weight": "bold", "size": "sm", "color": "#0284c7"}
                        ]
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": get_text('book_room_card_title', lang), "weight": "bold", "size": "lg", "color": "#0f172a"},
                            {"type": "text", "text": get_text('book_room_subtitle', lang), "size": "sm", "color": "#64748b", "wrap": True, "margin": "md"}
                        ]
                    },
                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "button",
                                "style": "primary",
                                "height": "sm",
                                "color": "#0284c7",
                                "action": {
                                    "type": "uri",
                                    "label": f"📝 {get_text('book_room', lang)}",
                                    "uri": booking_url
                                }
                            }
                        ]
                    }
                }
                safe_reply_or_push(
                    tenant_bot_api,
                    event.reply_token,
                    user_id,
                    [FlexMessage(alt_text=get_text('book_room', lang), contents=FlexContainer.from_dict(flex_contents))]
                )
                return

            # 3. Dorm Rules / Policy keywords (Available to everyone)
            elif text in ["กฎระเบียบ", "ระเบียบ", "กฎหอพัก", "ข้อปฏิบัติ", "สัญญา", "rules", "rule", "policy"] or text == get_text('dorm_rules', lang):
                owner = db.query(models.Owner).first()
                raw_rules = (owner.lease_template if owner and owner.lease_template else "").strip()
                rules_text = clean_html_to_text(raw_rules) if raw_rules else get_text('default_lease_agreement_text', lang)
                
                rule_lines = [l.strip() for l in rules_text.split('\n') if l.strip()][:12]
                rule_contents = []
                for line in rule_lines:
                    rule_contents.append({
                        "type": "text",
                        "text": line,
                        "size": "sm",
                        "color": "#334155",
                        "wrap": True
                    })

                owner_name = owner.display_name if owner and owner.display_name else "สุขอนันต์ อพาร์ทเม้นท์"
                booking_url = f"{BASE_URL}/booking?uid={user_id}&lang={lang}"

                flex_contents = {
                    "type": "bubble",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": "📜 สัญญาและกฎระเบียบหอพัก", "weight": "bold", "size": "lg", "color": "#FFFFFF"}
                        ],
                        "backgroundColor": "#0284C7",
                        "paddingAll": "18px"
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": owner_name, "weight": "bold", "size": "md", "color": "#0f172a"},
                            {"type": "separator", "margin": "md"},
                            {
                                "type": "box",
                                "layout": "vertical",
                                "margin": "md",
                                "spacing": "sm",
                                "contents": rule_contents
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
                                "style": "primary",
                                "height": "sm",
                                "color": "#0284C7",
                                "action": {
                                    "type": "uri",
                                    "label": "📝 จองห้องพักออนไลน์",
                                    "uri": booking_url
                                }
                            }
                        ]
                    }
                }
                safe_reply_or_push(
                    tenant_bot_api,
                    event.reply_token,
                    user_id,
                    [FlexMessage(alt_text="📜 สัญญาและกฎระเบียบหอพัก", contents=FlexContainer.from_dict(flex_contents))]
                )
                return

            # 4. Active Tenant Actions
            if active_tenants:
                setup_personal_rich_menu(active_tenants[0], db)
                
                if text == "ดูค่าเช่า" or text == get_text('view_bill', lang) or text in ["บิล", "ใบแจ้งหนี้", "ค่าเช่า"]:
                    messages = []
                    owner = db.query(models.Owner).first()
                    
                    for tenant in active_tenants:
                        if tenant.current_room_id:
                            invoice = db.query(models.Invoice).filter(models.Invoice.room_id == tenant.current_room_id).order_by(models.Invoice.id.desc()).first()
                        else:
                            invoice = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).first()
                        
                        room_no = tenant.room.room_number if tenant.room else "N/A"
                        if invoice:
                            if not invoice.uuid:
                                invoice.uuid = str(uuid.uuid4())
                                db.commit()
                                
                            bill_url = f"{BASE_URL}/bill/{invoice.uuid}?lang={lang}"
                            
                            flex_contents = {
                                "type": "bubble",
                                "header": {
                                    "type": "box",
                                    "layout": "vertical",
                                    "contents": [
                                        {"type": "text", "text": get_text('bill_details', lang), "weight": "bold", "size": "xl", "color": "#FFFFFF", "align": "center"}
                                    ],
                                    "backgroundColor": "#1DB446",
                                    "paddingAll": "20px"
                                },
                                "body": {
                                    "type": "box",
                                    "layout": "vertical",
                                    "contents": [
                                        {"type": "text", "text": owner.display_name if owner and owner.display_name else "SukAnan Apartment", "weight": "bold", "size": "md", "margin": "md"},
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
                                                        {"type": "text", "text": get_text('room_label', lang), "size": "sm", "color": "#555555", "flex": 0},
                                                        {"type": "text", "text": room_no, "size": "sm", "color": "#111111", "align": "end"}
                                                    ]
                                                },
                                                {
                                                    "type": "box",
                                                    "layout": "horizontal",
                                                    "contents": [
                                                        {"type": "text", "text": get_text('bill_cycle', lang), "size": "sm", "color": "#555555", "flex": 0},
                                                        {"type": "text", "text": f"{invoice.billing_month}/{invoice.billing_year}", "size": "sm", "color": "#111111", "align": "end"}
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
                                                {"type": "text", "text": get_text('total_payment', lang), "size": "md", "weight": "bold", "color": "#111111"},
                                                {"type": "text", "text": f"฿{invoice.total_amount:,.2f}", "size": "lg", "weight": "bold", "color": "#1DB446", "align": "end"}
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
                                            "style": "primary",
                                            "height": "sm",
                                            "action": {"type": "uri", "label": get_text('view_bill_btn', lang), "uri": bill_url},
                                            "color": "#1DB446"
                                        }
                                    ]
                                }
                            }
                            messages.append(FlexMessage(alt_text=f"{get_text('bill_title', lang)} - {room_no}", contents=FlexContainer.from_dict(flex_contents)))
                        else:
                            messages.append(TextMessage(text=get_text('room_label_with_no', lang).format(no=room_no) + ": " + get_text('bill_not_issued', lang)))

                    safe_reply_or_push(tenant_bot_api, event.reply_token, user_id, messages[:5])
                    return
                
                elif text in ["แจ้งซ่อม", "ประวัติ", "ย้ายออก"] or text in [get_text('repairs', lang), get_text('history', lang), get_text('move_out', lang)]:
                    cmd = text
                    if text == get_text('repairs', lang): cmd = "แจ้งซ่อม"
                    elif text == get_text('history', lang): cmd = "ประวัติ"
                    elif text == get_text('move_out', lang): cmd = "ย้ายออก"

                    if len(active_tenants) == 1:
                        t = active_tenants[0]
                        room_no = t.room.room_number if t.room else "N/A"
                        if cmd == "แจ้งซ่อม":
                            reply_text = f"🛠️ {get_text('repair_single_room_header', lang)} ({get_text('room_label_with_no', lang).format(no=room_no)}):\n{BASE_URL}/repair/{t.uuid}?lang={lang}"
                        elif cmd == "ประวัติ":
                            reply_text = f"📜 {get_text('history_single_room_header', lang)} ({get_text('room_label_with_no', lang).format(no=room_no)}):\n{BASE_URL}/history/{t.uuid}?lang={lang}"
                        else:
                            reply_text = f"🚪 {get_text('move_out_single_room_header', lang)} ({get_text('room_label_with_no', lang).format(no=room_no)}):\n{BASE_URL}/move-out/{t.uuid}?lang={lang}"
                        safe_reply_or_push(tenant_bot_api, event.reply_token, user_id, [TextMessage(text=reply_text)])
                        return
                    else:
                        if cmd == "แจ้งซ่อม":
                            action_title = get_text('select_room_repair', lang)
                        elif cmd == "ประวัติ":
                            action_title = get_text('select_room_history', lang)
                        elif cmd == "ย้ายออก":
                            action_title = get_text('select_room_move_out', lang)
                        else:
                            action_title = get_text('select_room_action', lang)

                        bubble_contents = []
                        for t in active_tenants:
                            room_no = t.room.room_number if t.room else "N/A"
                            url_map = {
                                "แจ้งซ่อม": f"{BASE_URL}/repair/{t.uuid}?lang={lang}",
                                "ประวัติ": f"{BASE_URL}/history/{t.uuid}?lang={lang}",
                                "ย้ายออก": f"{BASE_URL}/move-out/{t.uuid}?lang={lang}"
                            }
                            bubble_contents.append({
                                "type": "button",
                                "style": "secondary",
                                "margin": "sm",
                                "action": {"type": "uri", "label": get_text('room_label_with_no', lang).format(no=room_no), "uri": url_map[cmd]}
                            })
                        
                        flex_contents = {
                            "type": "bubble",
                            "body": {
                                "type": "box",
                                "layout": "vertical",
                                "contents": [
                                    {"type": "text", "text": action_title, "weight": "bold", "size": "md"},
                                    {"type": "box", "layout": "vertical", "margin": "lg", "contents": bubble_contents}
                                ]
                            }
                        }
                        safe_reply_or_push(
                            tenant_bot_api,
                            event.reply_token,
                            user_id,
                            [FlexMessage(alt_text=action_title, contents=FlexContainer.from_dict(flex_contents))]
                        )
                        return

                elif text == "สนทนา" or text == get_text('chat_label', lang):
                    reply_text = get_text('tenant_chat_greeting', lang)
                else:
                    rooms_str = ", ".join([t.room.room_number for t in active_tenants if t.room])
                    reply_text = get_text('tenant_greeting', lang).format(rooms=rooms_str)
                
                safe_reply_or_push(tenant_bot_api, event.reply_token, user_id, [TextMessage(text=reply_text)])
                return

            # 4. User is NOT an active tenant yet (New user / Applicant / Pending tenant)
            else:
                tenant = next((t for t in tenants if t.status != "Active"), None)
                if not tenant:
                    tenant = models.Tenant(line_user_id=user_id, status="AwaitingRegistration", language=lang)
                    db.add(tenant)
                    db.commit()
                    db.refresh(tenant)

                booking_url = f"{BASE_URL}/booking?uid={user_id}&lang={lang}"

                approved_booking = db.query(models.BookingRequest).filter(
                    models.BookingRequest.line_user_id == user_id,
                    models.BookingRequest.status == "Approved"
                ).order_by(models.BookingRequest.id.desc()).first()

                pending_action = (
                    text in ["ประวัติ", "ดูค่าเช่า", "แจ้งซ่อม", "ย้ายเข้า", "ย้ายออก"]
                    or text in [
                        get_text('history', lang), get_text('view_bill', lang),
                        get_text('repairs', lang), get_text('move_in', lang),
                        get_text('move_out', lang)
                    ]
                )

                footer_label = f"📝 {get_text('book_room', lang)}"
                footer_url = booking_url
                status_detail = None

                if approved_booking and pending_action:
                    room = approved_booking.assigned_room
                    room_no = room.room_number if room else "รอจัดสรร"
                    building_name = room.building.name if room and room.building else "-"
                    title_text = "ผ่านการคัดเลือกแล้ว"

                    latest_invoice = db.query(models.Invoice).filter(
                        models.Invoice.tenant_id == tenant.id
                    ).order_by(models.Invoice.id.desc()).first()

                    if latest_invoice:
                        if not latest_invoice.uuid:
                            latest_invoice.uuid = str(uuid.uuid4())
                            db.commit()
                        desc_text = "มีใบแจ้งยอดสำหรับขั้นตอนเข้าพักแล้ว กรุณากดปุ่มด้านล่างเพื่อตรวจสอบและชำระเงิน"
                        footer_label = "ดูใบแจ้งยอดแรกเข้า"
                        footer_url = f"{BASE_URL}/bill/{latest_invoice.uuid}?lang={lang}"
                    elif tenant.status == "AwaitingRegistration":
                        desc_text = "ห้องของคุณอยู่ระหว่างขั้นตอนกรอกข้อมูลทำสัญญาและชำระเงินแรกเข้า จึงยังไม่มีใบแจ้งค่าเช่า"
                        footer_label = "กรอกข้อมูลเพื่อทำสัญญา"
                        footer_url = f"{BASE_URL}/register/{tenant.uuid}?lang={lang}"
                    else:
                        desc_text = "ระบบได้รับข้อมูลทำสัญญาแล้ว ขณะนี้กำลังรอผู้ดูแลตรวจสอบและออกใบแจ้งยอดแรกเข้า"
                        footer_label = "ตรวจสอบข้อมูลทำสัญญา"
                        footer_url = f"{BASE_URL}/register/{tenant.uuid}?lang={lang}"

                    status_detail = f"อาคาร {building_name} • ห้อง {room_no}"
                elif pending_action:
                    title_text = "ยังไม่มีห้องพักในระบบ"
                    desc_text = "ไม่พบห้องพักที่เปิดใช้งานของคุณในระบบ หากคุณสนใจเข้าพัก สามารถกดจองห้องพักออนไลน์ได้ที่ปุ่มด้านล่างครับ"
                else:
                    title_text = "ยินดีต้อนรับสู่ สุขอนันต์ อพาร์ทเม้นท์"
                    desc_text = "สวัสดีครับ คุณสามารถกดจองห้องพักออนไลน์ หรือแตะเมนูด้านล่างเพื่อดูข้อมูลกฎระเบียบและสอบถามเพิ่มเติมได้เลยครับ"

                flex_contents = {
                    "type": "bubble",
                    "header": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": "🏠 SukAnan Apartment", "weight": "bold", "size": "sm", "color": "#0284c7"}
                        ]
                    },
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {"type": "text", "text": title_text, "weight": "bold", "size": "lg", "color": "#0f172a"},
                            *([{"type": "text", "text": status_detail, "weight": "bold", "size": "sm", "color": "#0284c7", "wrap": True, "margin": "sm"}] if status_detail else []),
                            {"type": "text", "text": desc_text, "size": "sm", "color": "#64748b", "wrap": True, "margin": "md"}
                        ]
                    },
                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "button",
                                "style": "primary",
                                "height": "sm",
                                "color": "#0284c7",
                                "action": {
                                    "type": "uri",
                                    "label": footer_label,
                                    "uri": footer_url
                                }
                            }
                        ]
                    }
                }
                
                safe_reply_or_push(
                    tenant_bot_api,
                    event.reply_token,
                    user_id,
                    [FlexMessage(alt_text=title_text, contents=FlexContainer.from_dict(flex_contents))]
                )
                return

        finally:
            if close_db:
                db.close()

    @tenant_handler.add(PostbackEvent)
    def handle_tenant_postback(event, *args, **kwargs):
        data = event.postback.data
        user_id = event.source.user_id if hasattr(event, 'source') else None
        if data == "action=chat":
            reply_text = "คุณสามารถพิมพ์ข้อความที่ต้องการสอบถามทิ้งไว้ได้เลยครับ เจ้าหน้าที่จะรีบมาตอบกลับโดยเร็วที่สุด"
            safe_reply_or_push(tenant_bot_api, event.reply_token, user_id, [TextMessage(text=reply_text)])

# Fallback stubs for tests or environments where handlers are not initialized
if not admin_handler:
    def handle_admin_message(event, *args, **kwargs):
        pass
    def handle_admin_postback(event, *args, **kwargs):
        pass

if not tenant_handler:
    def handle_tenant_message(event, *args, **kwargs):
        pass
    def handle_tenant_postback(event, *args, **kwargs):
        pass
