import logging
import traceback
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from models.database import get_db, SessionLocal
import models
import config
from config import get_text, get_magic_url

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
    def __str__(self):
        return str(config.BASE_URL)
    def __repr__(self):
        return repr(config.BASE_URL)
    def __getattr__(self, item):
        return getattr(config.BASE_URL, item)
    def rstrip(self, chars=None):
        return config.BASE_URL.rstrip(chars)
    def __add__(self, other):
        return config.BASE_URL + other
    def __radd__(self, other):
        return other + config.BASE_URL

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

router = APIRouter()

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
                        admin_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[FlexMessage(alt_text=get_text('meter_reading', lang), contents=FlexContainer.from_dict(flex_contents))]
                            )
                        )
                    return
                elif text == "สรุปรายรับ" or text == get_text('analytics', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"💰 {get_text('export_report', lang)}:\n{url}#billSection"
                elif text == "จัดการสัญญา" or text == get_text('manage_leases', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"📜 {get_text('manage_leases', lang)}:\n{url}#leaseSection"
                elif text == "ตั้งค่า" or text == get_text('settings', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"⚙️ {get_text('settings', lang)}:\n{url}#settingsSection"
                elif text == "รายการแจ้งซ่อม" or text == get_text('repairs', lang):
                    url = get_magic_url(owner, db)
                    reply_text = f"🛠️ {get_text('repair_requests', lang)}:\n{url}#repairSection"
                else:
                    reply_text = get_text('admin_menu_greeting', lang)
            
            if admin_bot_api and reply_text:
                admin_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        finally:
            if close_db:
                db.close()

    @admin_handler.add(PostbackEvent)
    def handle_admin_postback(event, *args, **kwargs):
        pass

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
            
        reply_text = ""
        try:
            tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id == user_id).all()
            active_tenants = [t for t in tenants if t.status == "Active"]
            lang = active_tenants[0].language if active_tenants else (tenants[0].language if tenants else "th")

            if text.lower() in ["language", "lang", "ภาษา", "เปลี่ยนภาษา"]:
                reply_text = get_text('tenant_language_choose', lang)
            elif text.upper() in ["TH", "EN", "JP"]:
                new_lang = text.lower()
                for t in tenants:
                    t.language = new_lang
                db.commit()
                lang = new_lang
                
                if active_tenants:
                    setup_personal_rich_menu(active_tenants[0], db, force=True)
                
                reply_text = get_text('language_changed', lang)

            elif text == "ย้ายเข้า" or text == get_text('move_in', lang) or not active_tenants:
                tenant = next((t for t in tenants if t.status != "Active"), None)
                
                if not tenant:
                    tenant = models.Tenant(line_user_id=user_id, status="AwaitingRegistration", language=lang)
                    db.add(tenant)
                    db.commit()
                    db.refresh(tenant)
                
                reg_url = f"{BASE_URL}/register/{tenant.uuid}?lang={lang}"
                
                if tenant_bot_api:
                    from linebot.v3.messaging import (
                        TemplateMessage,
                        ButtonsTemplate,
                        URIAction
                    )
                    buttons_template = ButtonsTemplate(
                        title=get_text('register_title', lang),
                        text=get_text('register_welcome_msg', lang),
                        actions=[
                            URIAction(label=get_text('register_btn_label', lang), uri=reg_url)
                        ]
                    )
                    try:
                        tenant_bot_api.reply_message(
                            ReplyMessageRequest(
                                reply_token=event.reply_token,
                                messages=[TemplateMessage(alt_text=get_text('register_alt_text', lang), template=buttons_template)]
                            )
                        )
                        return
                    except Exception: pass
                
                reply_text = f"{get_text('hello', lang)}! {get_text('register_welcome_msg', lang)}:\n{reg_url}"

            elif active_tenants:
                setup_personal_rich_menu(active_tenants[0], db)
                
                if text == "ดูค่าเช่า" or text == get_text('view_bill', lang):
                    messages = []
                    owner = db.query(models.Owner).first()
                    
                    for tenant in active_tenants:
                        if tenant.current_room_id:
                            invoice = db.query(models.Invoice).filter(models.Invoice.room_id == tenant.current_room_id).order_by(models.Invoice.id.desc()).first()
                        else:
                            invoice = db.query(models.Invoice).filter(models.Invoice.tenant_id == tenant.id).order_by(models.Invoice.id.desc()).first()
                        
                        room_no = tenant.room.room_number if tenant.room else "N/A"
                        if invoice:
                            status_map = {
                                "Unpaid": (get_text('status_unpaid', lang), "#e74c3c"),
                                "Pending Verification": (get_text('status_pending', lang), "#f39c12"),
                                "Draft": (get_text('status_draft', lang), "#95a5a6"),
                                "Paid": (get_text('status_paid', lang), "#3498db")
                            }
                            status_text, status_color = status_map.get(invoice.status, (invoice.status, "#3498db"))
                            
                            if not invoice.uuid:
                                invoice.uuid = str(uuid.uuid4())
                                db.commit()
                                
                            bill_url = f"{BASE_URL}/bill/{invoice.uuid}?lang={lang}"
                            total_fmt = "{:,.2f}".format(invoice.total_amount)
                            
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
                                                },
                                                {
                                                    "type": "box",
                                                    "layout": "horizontal",
                                                    "contents": [
                                                        {"type": "text", "text": get_text('rent_amount', lang), "size": "sm", "color": "#555555", "flex": 0},
                                                        {"type": "text", "text": f"฿{invoice.rent_amount:,.2f}", "size": "sm", "color": "#111111", "align": "end"}
                                                    ]
                                                },
                                                {
                                                    "type": "box",
                                                    "layout": "horizontal",
                                                    "contents": [
                                                        {"type": "text", "text": get_text('status', lang), "size": "sm", "color": "#555555", "flex": 0},
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
                                                {"type": "text", "text": get_text('total_sum_label', lang), "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
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
                                            "action": {"type": "uri", "label": get_text('view_details', lang), "uri": bill_url}
                                        }
                                    ]
                                }
                            }
                            messages.append(FlexMessage(alt_text=f"{get_text('bill_title', lang)} - {room_no}", contents=FlexContainer.from_dict(flex_contents)))
                        else:
                            messages.append(TextMessage(text=get_text('room_label_with_no', lang).format(no=room_no) + ": " + get_text('bill_not_issued', lang)))

                    if tenant_bot_api:
                        tenant_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=messages[:5]))
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
                            reply_text = f"{get_text('repairs', lang)} {get_text('room_label_with_no', lang).format(no=room_no)}:\n{BASE_URL}/repair/{t.uuid}?lang={lang}"
                        elif cmd == "ประวัติ":
                            reply_text = f"{get_text('history', lang)} {get_text('room_label_with_no', lang).format(no=room_no)}:\n{BASE_URL}/history/{t.uuid}?lang={lang}"
                        else:
                            reply_text = f"{get_text('move_out', lang)} {get_text('room_label_with_no', lang).format(no=room_no)}:\n{BASE_URL}/move-out/{t.uuid}?lang={lang}"
                    else:
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
                                    {"type": "text", "text": get_text('select_room_action', lang).format(action=text), "weight": "bold", "size": "md"},
                                    {"type": "box", "layout": "vertical", "margin": "lg", "contents": bubble_contents}
                                ]
                            }
                        }
                        if tenant_bot_api:
                            tenant_bot_api.reply_message(
                                ReplyMessageRequest(
                                    reply_token=event.reply_token,
                                    messages=[FlexMessage(alt_text=get_text('select_room_action', lang).format(action=text), contents=FlexContainer.from_dict(flex_contents))]
                                )
                            )
                            return

                elif text == "สนทนา" or text == get_text('chat_label', lang):
                    reply_text = get_text('tenant_chat_greeting', lang)
                else:
                    rooms_str = ", ".join([t.room.room_number for t in active_tenants if t.room])
                    reply_text = get_text('tenant_greeting', lang).format(rooms=rooms_str)
                
            if tenant_bot_api:
                tenant_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))
        finally:
            if close_db:
                db.close()

    @tenant_handler.add(PostbackEvent)
    def handle_tenant_postback(event, *args, **kwargs):
        data = event.postback.data
        if data == "action=chat":
            reply_text = "คุณสามารถพิมพ์ข้อความที่ต้องการสอบถามทิ้งไว้ได้เลยครับ เจ้าหน้าที่จะรีบมาตอบกลับโดยเร็วที่สุด"
            if tenant_bot_api:
                tenant_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=reply_text)]
                    )
                )

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

