import os
import io
import csv
import json
import uuid
import shutil
import secrets
import logging
import requests
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

import models
import services.security as security
import services.billing as billing
import services.promptpay as promptpay
from models.database import get_db
from utils import parse_sqlite_datetime
import config
from config import templates, get_text, refresh_configs

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
line_bot_api = BotApiProxy("line_bot_api")

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
from linebot.v3.messaging import (
    PushMessageRequest,
    TextMessage,
    ImageMessage,
    FlexMessage,
    FlexContainer
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

uploads_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
src_dir = os.path.dirname(os.path.dirname(__file__))

def get_current_user(request: Request, db: Session = Depends(get_db)):
    """
    FastAPI Dependency to retrieve the currently logged-in user.
    Supports both Google OAuth2 Users and legacy Owner/Admin sessions for backward compatibility.
    """
    user_count = db.query(models.User).count()
    owner_count = db.query(models.Owner).count()
    if user_count == 0 and owner_count == 0:
        # Web Bootstrap Wizard redirect if system has no users at all
        raise HTTPException(
            status_code=307,
            headers={"Location": "/admin/setup/bootstrap"},
            detail="Initial setup required"
        )

    admin_session = request.cookies.get("admin_session")
    if not admin_session:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 1. Check in the new multi-role 'users' table
    user = db.query(models.User).filter(models.User.session_token == admin_session, models.User.status == "Active").first()
    if user:
        return user

    # 2. Fallback: Check in the legacy 'owners' table (for backward-compatible tests)
    owner = db.query(models.Owner).filter(models.Owner.session_token == admin_session).first()
    if owner:
        return models.User(
            email="legacy_owner@system.local", 
            full_name=owner.display_name or "Owner", 
            role="Admin", 
            status="Active"
        )

    raise HTTPException(status_code=401, detail="Unauthorized")

def get_admin(current_user: models.User = Depends(get_current_user)):
    """
    Verifies that the current user has the Admin role.
    """
    if current_user.role != "Admin":
        raise HTTPException(status_code=403, detail="Forbidden: Admin role required")
    return True

class RoleChecker:
    """
    Enforces Role-Based Access Control (RBAC) on specific routes.
    """
    def __init__(self, allowed_roles: list):
        self.allowed_roles = allowed_roles

    def __call__(self, current_user: models.User = Depends(get_current_user)):
        if current_user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Access restricted. Requires one of roles: {self.allowed_roles}"
            )
        return current_user

@router.get("/setup/bootstrap", response_class=HTMLResponse)
async def setup_bootstrap_page(request: Request, db: Session = Depends(get_db)):
    """
    Renders the onboarding page (Web Bootstrap Wizard) for the first owner to claim ownership using Google.
    Returns 403 Forbidden if an Admin already exists.
    """
    admin_exists = db.query(models.User).filter(models.User.role == "Admin").first()
    if admin_exists:
        return HTMLResponse("<h2>Forbidden: Initial setup is already completed.</h2>", status_code=403)
        
    google_client_id = security.get_system_config(db, "GOOGLE_CLIENT_ID")
    lang = request.query_params.get("lang") or "th"
    return templates.TemplateResponse("bootstrap.html", {
        "request": request, 
        "lang": lang,
        "google_client_id": google_client_id
    })

@router.post("/auth/google")
async def auth_google(request: Request, data: dict, db: Session = Depends(get_db)):
    """
    Processes Google OAuth ID Token (JWT), verifies it, and logs the user in.
    If the system has no active Admins, the first signed-in user is automatically registered as Admin.
    """
    id_token = data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="Missing ID Token")
        
    try:
        claims = security.verify_google_id_token(id_token)
    except Exception as e:
        logger.error(f"Google OAuth verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid token verification")
        
    email = claims.get("email")
    full_name = claims.get("name")
    
    if not email:
        raise HTTPException(status_code=400, detail="Missing email claim in Google token")
        
    # Check if this is the Web Bootstrap Wizard claiming ownership
    admin_exists = db.query(models.User).filter(models.User.role == "Admin").first()
    
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        if not admin_exists:
            # Register the first Google account as Super Admin
            user = models.User(
                email=email,
                full_name=full_name or "Super Admin",
                role="Admin",
                status="Active"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"System bootstrapped successfully. First Admin: {email}")
        else:
            # Unauthorized access (not registered by Admin)
            raise HTTPException(status_code=403, detail="Gmail address not registered. Please contact the administrator.")
            
    if user.status != "Active":
        raise HTTPException(status_code=403, detail="Account is suspended.")
        
    # Generate secure session token
    token = secrets.token_hex(32)
    user.session_token = token
    db.commit()
    
    from fastapi.responses import JSONResponse
    res = JSONResponse(content={"status": "Success", "redirect": "/admin/dashboard"})
    res.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax"
    )
    return res

@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, db: Session = Depends(get_db)):
    google_client_id = security.get_system_config(db, "GOOGLE_CLIENT_ID")
    lang = request.cookies.get("lang") or request.query_params.get("lang") or "th"
    error = request.query_params.get("error")
    wait = request.query_params.get("wait")
    return templates.TemplateResponse("login.html", {
        "request": request, 
        "lang": lang, 
        "error": error, 
        "wait": wait,
        "google_client_id": google_client_id
    })

@router.post("/login")
async def admin_login(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    ip = request.client.host
    now = datetime.now()
    
    # Check for existing lockout
    attempt = db.query(models.LoginAttempt).filter(models.LoginAttempt.ip_address == ip).first()
    if attempt and attempt.locked_until and attempt.locked_until > now:
        remaining = int((attempt.locked_until - now).total_seconds() / 60) + 1
        return RedirectResponse(url=f"/admin/login?error=locked&wait={remaining}", status_code=303)

    owner = db.query(models.Owner).first()
    if owner and security.verify_password(password, owner.password_hash):
        # Reset attempts on success
        if attempt:
            attempt.attempts = 0
            attempt.locked_until = None
        
        # Generate secure session token
        token = secrets.token_hex(32)
        owner.session_token = token
        db.commit()
            
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(
            key="admin_session", 
            value=token, 
            httponly=True, 
            secure=True, 
            samesite="lax"
        )
        return response
    
    # Increment attempts on failure
    if not attempt:
        attempt = models.LoginAttempt(ip_address=ip, attempts=1)
        db.add(attempt)
    else:
        attempt.attempts += 1
        if attempt.attempts >= 3:
            attempt.locked_until = now + timedelta(minutes=30)
    
    db.commit()
    
    if attempt.attempts >= 3:
        return RedirectResponse(url="/admin/login?error=locked&wait=30", status_code=303)
        
    return RedirectResponse(url="/admin/login?error=1", status_code=303)

@router.post("/logout")
async def admin_logout(request: Request, db: Session = Depends(get_db)):
    admin_session = request.cookies.get("admin_session")
    if admin_session:
        owner = db.query(models.Owner).filter(models.Owner.session_token == admin_session).first()
        if owner:
            owner.session_token = None
            db.commit()
            
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    lang = request.cookies.get("lang") or request.query_params.get("lang") or "th"
    return templates.TemplateResponse("forgot_password.html", {"request": request, "lang": lang})

@router.post("/forgot-password")
async def request_password_reset(db: Session = Depends(get_db)):
    owner = db.query(models.Owner).first()
    if not owner or not owner.line_user_id or owner.line_user_id == "SYSTEM":
        return {"error": "No valid admin LINE ID found. Please contact support."}
    
    # Generate token
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
        lang = owner.language or "th"
        reset_link = f"{BASE_URL}/admin/magic-login?token={token}&redirect=/admin/reset-password"
        message = get_text('reset_link_msg', lang).format(link=reset_link)
        try:
            admin_bot_api.push_message(
                PushMessageRequest(
                    to=owner.line_user_id,
                    messages=[TextMessage(text=message)]
                )
            )
        except Exception as e:
            logger.error(f"Error sending reset link: {e}")
            return {"error": "Failed to send LINE message."}
            
    return {"message": get_text('reset_link_sent', lang)}

@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = None, db: Session = Depends(get_db)):
    owner = db.query(models.Owner).first()
    lang = owner.language if owner else "th"
    
    if token:
        # Check token validity
        reset_token = db.query(models.PasswordResetToken).filter(
            models.PasswordResetToken.token == token,
            models.PasswordResetToken.used == 0,
            models.PasswordResetToken.expires_at > datetime.now()
        ).first()
        
        if not reset_token:
            return HTMLResponse(content=f"<h2>{get_text('link_expired', lang)}</h2>", status_code=400)
    
    return templates.TemplateResponse("reset_password.html", {"request": request, "token": token, "lang": lang})

@router.post("/reset-password")
async def reset_password(token: str = Form(None), new_password: str = Form(...), db: Session = Depends(get_db)):
    owner = db.query(models.Owner).first()
    lang = owner.language if owner else "th"

    if token:
        reset_token = db.query(models.PasswordResetToken).filter(
            models.PasswordResetToken.token == token,
            models.PasswordResetToken.used == 0,
            models.PasswordResetToken.expires_at > datetime.now()
        ).first()
        
        if not reset_token:
            return HTMLResponse(content=f"<h2>{get_text('link_expired', lang)}</h2>", status_code=400)
        reset_token.used = 1

    if owner:
        owner.password_hash = security.hash_password(new_password)
        db.commit()
        return RedirectResponse(url="/admin/login?reset_success=1", status_code=303)
    
    return HTMLResponse(content=f"<h2>{get_text('reset_error', lang)}</h2>", status_code=500)

@router.post("/generate-pairing-code")
async def generate_pairing_code(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    # Generate 6-digit numeric code
    code = "".join([str(secrets.randbelow(10)) for _ in range(6)])
    owner = db.query(models.Owner).first()
    if owner:
        owner.pairing_code = code
        db.commit()
        return {"pairing_code": code}
    return {"error": "Owner not found"}

@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request, 
    month: int = None, 
    year: int = None, 
    building_id: str = None,
    db: Session = Depends(get_db), 
    admin: bool = Depends(get_admin)
):
    lang = request.cookies.get("lang", "th")
    stats = {
        "total_rooms": db.query(models.Room).count(),
        "vacant_rooms": db.query(models.Room).filter(models.Room.status == "Vacant").count(),
        "unpaid_invoices": db.query(models.Invoice).filter(models.Invoice.status == "Unpaid").count(),
        "pending_verification": db.query(models.Invoice).filter(models.Invoice.status == "Pending Verification").count(),
        "pending_repairs": db.query(models.MaintenanceRequest).filter(models.MaintenanceRequest.status == "Pending").count()
    }
    recent_invoices = db.query(models.Invoice).options(joinedload(models.Invoice.tenant)).order_by(models.Invoice.id.desc()).limit(10).all()
    recent_repairs = db.query(models.MaintenanceRequest).order_by(models.MaintenanceRequest.id.desc()).limit(5).all()
    # List tenants currently mapped to rooms, eager loading residents, newest first
    active_tenants = db.query(models.Tenant)\
        .options(joinedload(models.Tenant.residents))\
        .filter(models.Tenant.status == "Active", models.Tenant.current_room_id != None)\
        .order_by(models.Tenant.id.desc())\
        .all()
    pending_registrations = db.query(models.Tenant).filter(models.Tenant.status == "Pending").all()
    awaiting_payment_tenants = db.query(models.Tenant).filter(models.Tenant.status == "Awaiting Payment").all()
    move_out_requests = db.query(models.MoveOutRequest).filter(models.MoveOutRequest.status == "Pending").all()
    
    all_rooms = db.query(models.Room).options(joinedload(models.Room.tenant)).all()
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
        "awaiting_payment_tenants": awaiting_payment_tenants,
        "move_out_requests": move_out_requests,
        "all_rooms": all_rooms,
        "all_buildings": all_buildings,
        "owner": owner,
        "current_month": cur_m,
        "current_year": cur_y,
        "building_id": building_id,
        "lang": lang
    })

@router.get("/expenses")
async def list_expenses(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    return db.query(models.Expense).order_by(models.Expense.id.desc()).all()

@router.get("/revenue-details")
async def list_revenue_detail(month: int, year: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoices = db.query(models.Invoice).filter(
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year,
        models.Invoice.status == "Paid"
    ).all()
    
    results = []
    for inv in invoices:
        try:
            results.append({
                "id": inv.id,
                "room": inv.room.room_number if inv.room else "N/A",
                "tenant": inv.tenant.full_name if inv.tenant else "N/A",
                "rent": float(inv.rent_amount or 0),
                "elec": float(inv.electricity_amount or 0),
                "water": float(inv.water_amount or 0),
                "other": float((inv.total_amount or 0) - (inv.rent_amount or 0) - (inv.electricity_amount or 0) - (inv.water_amount or 0)),
                "total": float(inv.total_amount or 0),
                "date": inv.paid_at.strftime("%Y-%m-%d %H:%M") if inv.paid_at else "-"
            })
        except Exception as e:
            logger.error(f"Error processing invoice {inv.id}: {e}")
            continue
            
    return results

@router.get("/revenue")
async def list_revenue(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    # Aggregate income by month/year
    income_rows = db.query(
        models.Invoice.billing_month,
        models.Invoice.billing_year,
        func.coalesce(func.sum(models.Invoice.rent_amount), 0).label('rent'),
        func.coalesce(func.sum(models.Invoice.electricity_amount), 0).label('elec'),
        func.coalesce(func.sum(models.Invoice.water_amount), 0).label('water'),
        func.coalesce(func.sum(models.Invoice.total_amount - models.Invoice.rent_amount - models.Invoice.electricity_amount - models.Invoice.water_amount), 0).label('other'),
        func.coalesce(func.sum(models.Invoice.total_amount), 0).label('total')
    ).filter(models.Invoice.status == "Paid")\
     .group_by(models.Invoice.billing_year, models.Invoice.billing_month)\
     .order_by(models.Invoice.billing_year.desc(), models.Invoice.billing_month.desc())\
     .all()
    
    return [
        {
            "month": r.billing_month,
            "year": r.billing_year,
            "rent": r.rent,
            "elec": r.elec,
            "water": r.water,
            "other": r.other,
            "total": r.total
        } for r in income_rows
    ]

@router.post("/expenses/add")
async def add_expense(
    category: str = Form(...),
    amount: float = Form(...),
    description: str = Form(None),
    month: int = Form(...),
    year: int = Form(...),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    expense = models.Expense(
        category=category,
        amount=amount,
        description=description,
        billing_month=month,
        billing_year=year
    )
    db.add(expense)
    db.commit()
    return {"status": "Success"}

@router.delete("/expenses/{expense_id}")
async def delete_expense(expense_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    expense = db.query(models.Expense).filter(models.Expense.id == expense_id).first()
    if expense:
        db.delete(expense)
        db.commit()
    return {"status": "Success"}

@router.get("/report", response_class=HTMLResponse)
async def admin_report(request: Request, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    lang = request.cookies.get("lang", "th")
    # Income aggregation
    income_rows = db.query(
        models.Invoice.billing_month,
        models.Invoice.billing_year,
        func.coalesce(func.sum(models.Invoice.rent_amount), 0).label('rent'),
        func.coalesce(func.sum(models.Invoice.electricity_amount), 0).label('elec'),
        func.coalesce(func.sum(models.Invoice.water_amount), 0).label('water'),
        func.coalesce(func.sum(models.Invoice.total_amount - models.Invoice.rent_amount - models.Invoice.electricity_amount - models.Invoice.water_amount), 0).label('other'),
        func.coalesce(func.sum(models.Invoice.total_amount), 0).label('total')
    ).filter(models.Invoice.status == "Paid")\
     .group_by(models.Invoice.billing_year, models.Invoice.billing_month)\
     .order_by(models.Invoice.billing_year.asc(), models.Invoice.billing_month.asc())\
     .all()

    # Expense aggregation
    expense_rows = db.query(
        models.Expense.billing_month,
        models.Expense.billing_year,
        models.Expense.category,
        func.coalesce(func.sum(models.Expense.amount), 0).label('amount')
    ).group_by(models.Expense.billing_year, models.Expense.billing_month, models.Expense.category)\
     .order_by(models.Expense.billing_year.asc(), models.Expense.billing_month.asc())\
     .all()
     
    # Combine data for JSON
    report_data = {}
    total_breakdown = {"Common Area": 0, "Maintenance": 0, "Salary": 0, "Utility": 0, "Marketing": 0, "Other": 0}
    
    for row in income_rows:
        key = f"{row.billing_year}-{row.billing_month:02d}"
        if key not in report_data:
            report_data[key] = {
                "income": {"rent": 0, "elec": 0, "water": 0, "other": 0, "total": 0}, 
                "expense": {"Common Area": 0, "Maintenance": 0, "Salary": 0, "Utility": 0, "Marketing": 0, "Other": 0, "total": 0}
            }
        report_data[key]["income"] = {
            "rent": row.rent or 0,
            "elec": row.elec or 0,
            "water": row.water or 0,
            "other": row.other or 0,
            "total": row.total or 0
        }

    for row in expense_rows:
        key = f"{row.billing_year}-{row.billing_month:02d}"
        if key not in report_data:
            report_data[key] = {
                "income": {"rent": 0, "elec": 0, "water": 0, "other": 0, "total": 0}, 
                "expense": {"Common Area": 0, "Maintenance": 0, "Salary": 0, "Utility": 0, "Marketing": 0, "Other": 0, "total": 0}
            }
        cat = row.category
        if cat not in report_data[key]["expense"]:
            report_data[key]["expense"][cat] = 0
        report_data[key]["expense"][cat] += row.amount
        report_data[key]["expense"]["total"] += row.amount
        
        if cat in total_breakdown:
            total_breakdown[cat] += row.amount
        else:
            total_breakdown["Other"] += row.amount

    # Convert to sorted list for frontend
    sorted_keys = sorted(report_data.keys())
    # Limit to last 12 months for the chart
    recent_keys = sorted_keys[-12:] if len(sorted_keys) > 12 else sorted_keys
    
    chart_data = {
        "labels": recent_keys,
        "income": {
            "rent": [report_data[k]["income"]["rent"] for k in recent_keys],
            "elec": [report_data[k]["income"]["elec"] for k in recent_keys],
            "water": [report_data[k]["income"]["water"] for k in recent_keys],
            "other": [report_data[k]["income"]["other"] for k in recent_keys],
            "total": [report_data[k]["income"]["total"] for k in recent_keys],
        },
        "expense": {
            "common": [report_data[k]["expense"].get("Common Area", 0) for k in recent_keys],
            "maintenance": [report_data[k]["expense"].get("Maintenance", 0) for k in recent_keys],
            "salary": [report_data[k]["expense"].get("Salary", 0) for k in recent_keys],
            "utility": [report_data[k]["expense"].get("Utility", 0) for k in recent_keys],
            "marketing": [report_data[k]["expense"].get("Marketing", 0) for k in recent_keys],
            "other": [report_data[k]["expense"].get("Other", 0) for k in recent_keys],
            "total": [report_data[k]["expense"]["total"] for k in recent_keys],
        },
        "profit": [report_data[k]["income"]["total"] - report_data[k]["expense"]["total"] for k in recent_keys],
        "total_breakdown": total_breakdown,
        "utility_compare": {
            "labels": recent_keys,
            "collected": [report_data[k]["income"]["elec"] + report_data[k]["income"]["water"] for k in recent_keys],
            "actual_paid": [report_data[k]["expense"]["Utility"] for k in recent_keys]
        }
    }

    all_buildings = db.query(models.Building).all()
    owner = db.query(models.Owner).first()
    
    # Occupancy Stats
    total_rooms = db.query(models.Room).count()
    occupied_rooms = db.query(models.Room).filter(models.Room.status == "Occupied").count()
    occupancy_stats = {
        "total": total_rooms,
        "occupied": occupied_rooms,
        "vacant": total_rooms - occupied_rooms,
        "rate": (occupied_rooms / total_rooms * 100) if total_rooms > 0 else 0
    }
    
    # Historical Occupancy Trend (based on monthly invoices)
    occupancy_trend = []
    for k in recent_keys:
        y, m = map(int, k.split('-'))
        inv_count = db.query(models.Invoice).filter(
            models.Invoice.billing_month == m,
            models.Invoice.billing_year == y
        ).count()
        rate = (inv_count / total_rooms * 100) if total_rooms > 0 else 0
        occupancy_trend.append(round(rate, 1))

    # Aging Receivables
    unpaid_invoices = db.query(models.Invoice).filter(models.Invoice.status != "Paid").all()
    now = datetime.now()
    aging_data = {
        "buckets": {"1-15 days": 0, "16-30 days": 0, "30+ days": 0},
        "debtors": [] # List of {name, room, amount, days}
    }
    
    for inv in unpaid_invoices:
        # Assuming due date is the 5th of the billing month
        # If today is after the 5th, count days from the 5th
        due_date = datetime(inv.billing_year, inv.billing_month, 5)
        diff = now - due_date
        days = max(0, diff.days)
        
        if days > 0:
            if days <= 15: aging_data["buckets"]["1-15 days"] += inv.total_amount
            elif days <= 30: aging_data["buckets"]["16-30 days"] += inv.total_amount
            else: aging_data["buckets"]["30+ days"] += inv.total_amount
            
            aging_data["debtors"].append({
                "name": inv.tenant.full_name if inv.tenant else "N/A",
                "room": inv.room.room_number if inv.room else "N/A",
                "amount": inv.total_amount,
                "days": days
            })
    
    # Sort debtors by amount descending
    aging_data["debtors"].sort(key=lambda x: x["amount"], reverse=True)
    # Take top 10 for the table/chart
    aging_data["debtors"] = aging_data["debtors"][:10]

    return templates.TemplateResponse("report.html", {
        "request": request,
        "chart_data": json.dumps(chart_data),
        "occupancy_stats": json.dumps(occupancy_stats),
        "occupancy_trend": json.dumps(occupancy_trend),
        "aging_data": json.dumps(aging_data),
        "all_buildings": all_buildings,
        "owner": owner,
        "lang": lang
    })

@router.post("/registration/{tenant_id}/request-payment")
async def request_initial_payment(tenant_id: int, room_ids: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    id_list = [int(rid.strip()) for rid in room_ids.split(",") if rid.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="กรุณาเลือกอย่างน้อย 1 ห้อง")
    
    owner = db.query(models.Owner).first()
    success_rooms, g_deposit, g_advance, g_other, g_total = create_initial_invoice(db, tenant, id_list, owner)

    if not success_rooms:
        raise HTTPException(status_code=400, detail="ไม่สามารถสร้างบิลแรกเข้าสำหรับห้องที่เลือกได้")
        
    db.commit()
    
    # Notify tenant via LINE with Initial Payment Flex
    if tenant_bot_api:
        try:
            inv = db.query(models.Invoice).filter(
                models.Invoice.tenant_id == tenant.id,
                models.Invoice.invoice_type == "Initial",
                models.Invoice.status == "Unpaid"
            ).first()
            inv_uuid = inv.uuid if inv else None
            send_initial_payment_flex(tenant, success_rooms, g_deposit, g_advance, g_other, g_total, owner, tenant_bot_api, invoice_uuid=inv_uuid)
        except Exception as e:
            logger.error(f"Failed to notify tenant: {e}")
            
    return {"status": "Success"}

@router.get("/leases/list")
async def list_leases(page: int = 1, page_size: int = 10, q: str = None, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.Lease).join(models.Room).join(models.Tenant).options(
        joinedload(models.Lease.room),
        joinedload(models.Lease.tenant)
    )
    
    if q:
        query = query.filter(
            (models.Room.room_number.ilike(f"%{q}%")) |
            (models.Tenant.full_name.ilike(f"%{q}%"))
        )
    
    total_count = query.count()
    total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 1
    
    leases = query.order_by(models.Lease.id.desc())\
        .offset((page - 1) * page_size).limit(page_size).all()
    
    results = []
    for l in leases:
        try:
            # Defensive date handling for SQLite
            s_date = parse_sqlite_datetime(l.start_date)
            
            results.append({
                "id": l.id,
                "room_number": l.room.room_number if l.room else "N/A",
                "tenant_name": l.tenant.full_name if l.tenant else "N/A",
                "tenant_id": l.tenant_id,
                "start_date": s_date.strftime("%d/%m/%Y") if s_date else "-",
                "status": l.status,
                "initial_payment_status": l.initial_payment_status
            })
        except Exception as e:
            logger.error(f"Error processing lease {l.id}: {e}")
            continue
            
    return {
        "items": results,
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": page,
        "page_size": page_size
    }

@router.get("/leases/{lease_id}/view", response_class=HTMLResponse)
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

@router.get("/receipts/list")
async def list_receipts(page: int = 1, page_size: int = 10, q: str = "", db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.Invoice).options(joinedload(models.Invoice.room), joinedload(models.Invoice.tenant)).filter(models.Invoice.status == "Paid")
    
    if q:
        query = query.filter(
            or_(
                models.Room.room_number.ilike(f"%{q}%"),
                models.Tenant.full_name.ilike(f"%{q}%")
            )
        )
    
    total = query.count()
    items = query.order_by(models.Invoice.paid_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    
    return {
        "items": [{
            "id": inv.id,
            "room_number": inv.room.room_number if inv.room else "N/A",
            "tenant_name": inv.tenant.full_name if inv.tenant else "N/A",
            "period": f"{inv.billing_month}/{inv.billing_year}",
            "total_amount": inv.total_amount,
            "paid_at": inv.paid_at.strftime("%d/%m/%Y %H:%M") if inv.paid_at else "-",
            "type": inv.invoice_type
        } for inv in items],
        "total_pages": (total + page_size - 1) // page_size,
        "current_page": page
    }

@router.get("/receipts/{invoice_id}/print", response_class=HTMLResponse)
async def print_receipt(request: Request, invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).options(joinedload(models.Invoice.room), joinedload(models.Invoice.tenant)).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    owner = db.query(models.Owner).first()
    lang = invoice.tenant.language if invoice.tenant else "th"
    
    other_charges = []
    if invoice.other_charges:
        try:
            other_charges = json.loads(invoice.other_charges)
        except:
            pass

    return templates.TemplateResponse("receipt_print.html", {
        "request": request,
        "invoice": invoice,
        "owner": owner,
        "other_charges": other_charges,
        "lang": lang,
        "now": datetime.now()
    })

@router.post("/registration/{tenant_id}/reject")
async def reject_registration(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    tenant.status = "Rejected"
    tenant.current_room_id = None
    
    # Cancel unpaid initial invoices for this tenant
    unpaid_initial_invoices = db.query(models.Invoice).filter(
        models.Invoice.tenant_id == tenant.id,
        models.Invoice.invoice_type == "Initial",
        models.Invoice.status == "Unpaid"
    ).all()
    for inv in unpaid_initial_invoices:
        inv.status = "Cancelled"
        
    db.commit()
    
    # Notify tenant
    bot = tenant_bot_api or line_bot_api
    if bot:
        try:
            reject_msg = get_text('registration_rejected_msg', tenant.language or "th")
            bot.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=reject_msg)]
                )
            )
        except Exception: pass
        
    return {"status": "Success"}

@router.get("/settlement/preview/{tenant_id}")
async def preview_settlement(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    
    room = tenant.room
    if not room:
        return {"status": "ERROR", "message": "ไม่พบข้อมูลห้องพักที่ผูกกับผู้เช่ารายนี้ อาจมีการย้ายออกไปแล้ว"}
    
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id, models.Lease.status == "Active").first()
    
    # 1. Pro-rated Rent Calculation
    now = datetime.now()
    import calendar
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_stayed = now.day
    base_rent = room.base_rent if room else 0
    pro_rated_rent = round((base_rent / days_in_month) * days_stayed, 2)
    
    # 2. Utility Check
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
    
    # 3. Security Deposit & Advance Rent
    deposit = 0
    advance_rent = 0
    if lease:
        deposit = lease.security_deposit_amount or 0
        advance_rent = lease.advance_rent_amount or 0
        # Fallback to initial_fees if specific fields are empty (for old records)
        if deposit == 0 and lease.initial_fees:
            try:
                fees = json.loads(lease.initial_fees)
                deposit = sum(float(f.get('amount', 0)) for f in fees)
            except (json.JSONDecodeError, TypeError, ValueError): pass
        
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
        "security_deposit": deposit,
        "advance_rent": advance_rent
    }

@router.post("/leases/{lease_id}/record-payment")
async def record_initial_payment(
    lease_id: int,
    payment_method: str = Form(...),
    receipt: UploadFile = File(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    lease = db.query(models.Lease).filter(models.Lease.id == lease_id).first()
    if not lease: raise HTTPException(status_code=404, detail="Lease not found")
    
    receipt_url = None
    if receipt and receipt.filename:
        os.makedirs(uploads_dir, exist_ok=True)
        ext = os.path.splitext(receipt.filename)[1]
        filename = f"initial_{lease.id}_{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(uploads_dir, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(receipt.file, buffer)
        receipt_url = f"/uploads/{filename}"
        
    lease.initial_payment_status = "Paid"
    lease.initial_payment_method = payment_method
    lease.initial_payment_date = datetime.now()
    lease.initial_payment_receipt = receipt_url
    db.commit()
    
    return {"status": "Success", "receipt": receipt_url}

@router.get("/leases/{lease_id}/details")
async def get_lease_details(lease_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    lease = db.query(models.Lease).filter(models.Lease.id == lease_id).first()
    if not lease: raise HTTPException(status_code=404, detail="Lease not found")
    
    # Calculate total initial fees
    other_fees = []
    if lease.initial_fees:
        try:
            other_fees = json.loads(lease.initial_fees)
        except (json.JSONDecodeError, TypeError): pass

    return {
        "id": lease.id,
        "room_number": lease.room.room_number if lease.room else "N/A",
        "tenant_name": lease.tenant.full_name if lease.tenant else "N/A",
        "security_deposit": lease.security_deposit_amount,
        "advance_rent": lease.advance_rent_amount,
        "initial_fees": other_fees,
        "status": lease.initial_payment_status,
        "method": lease.initial_payment_method,
        "payment_date": lease.initial_payment_date.strftime("%d/%m/%Y %H:%M") if lease.initial_payment_date else None,
        "receipt": lease.initial_payment_receipt
    }

@router.post("/settlement/confirm/{tenant_id}")
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
    
    # Save Receipt Image
    receipt_url = None
    if receipt:
        os.makedirs(uploads_dir, exist_ok=True)
        ext = os.path.splitext(receipt.filename)[1]
        filename = f"refund_{tenant.id}_{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(uploads_dir, filename)
        with open(filepath, "wb") as buffer:
            shutil.copyfileobj(receipt.file, buffer)
        receipt_url = f"/uploads/{filename}"

    total_deductions = pro_rated_rent + elec_amt + water_amt + unpaid_amt + cleaning_fee + damage_fee + other_fees
    advance_rent = lease.advance_rent_amount if lease else 0
    
    # Determine room_id
    final_room_id = tenant.current_room_id
    if not final_room_id and lease:
        final_room_id = lease.room_id

    settlement = models.Settlement(
        tenant_id=tenant.id,
        room_id=final_room_id,
        lease_id=lease.id if lease else None,
        pro_rated_rent=pro_rated_rent,
        electricity_amount=elec_amt,
        water_amount=water_amt,
        unpaid_invoices_amount=unpaid_amt,
        cleaning_fee=cleaning_fee,
        damage_fee=damage_fee,
        other_fees=other_fees,
        total_deductions=total_deductions,
        security_deposit_amount=deposit_amt,
        advance_rent_amount=advance_rent,
        final_balance=final_balance,
        refund_method=refund_method,
        refund_receipt_img=receipt_url,
        notes=notes
    )
    db.add(settlement)
    
    # Close Lease and Room
    if lease:
        lease.status = "Closed"
        lease.end_date = datetime.now()
    
    room = tenant.room
    if room:
        room.status = "Vacant"
        
    # Determine start_date with fallback
    hist_start_date = datetime.now()
    if lease and lease.start_date:
        hist_start_date = lease.start_date

    # Preservation of History
    history = models.TenantHistory(
        room_number=room.room_number if room else "N/A",
        tenant_uuid=tenant.uuid,
        full_name=tenant.full_name,
        phone_number=tenant.phone_number,
        start_date=hist_start_date,
        end_date=datetime.now(),
        residents_json=json.dumps([{"nickname": r.nickname, "full_name": f"{r.first_name} {r.last_name}"} for r in tenant.residents])
    )
    db.add(history)
    
    # Mark MoveOutRequest as Approved if exists
    mo_req = db.query(models.MoveOutRequest).filter(
        models.MoveOutRequest.tenant_id == tenant.id,
        models.MoveOutRequest.status == "Pending"
    ).order_by(models.MoveOutRequest.id.desc()).first()
    if mo_req:
        mo_req.status = "Approved"

    # Soft delete/deactivate tenant
    tenant.status = "MovedOut"
    tenant.current_room_id = None
    
    db.commit()
    return {"status": "Success"}

@router.post("/unmap/{tenant_id}")
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
        
    # Mark MoveOutRequest as Approved if exists
    mo_req = db.query(models.MoveOutRequest).filter(
        models.MoveOutRequest.tenant_id == tenant.id,
        models.MoveOutRequest.status == "Pending"
    ).order_by(models.MoveOutRequest.id.desc()).first()
    if mo_req:
        mo_req.status = "Approved"

    tenant.current_room_id = None
    tenant.status = "MovedOut"
    db.commit()
    return {"status": "Success"}

@router.post("/move-out/cancel/{tenant_id}")
async def cancel_move_out(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Update latest pending move-out request
    req = db.query(models.MoveOutRequest).filter(
        models.MoveOutRequest.tenant_id == tenant.id,
        models.MoveOutRequest.status == "Pending"
    ).order_by(models.MoveOutRequest.id.desc()).first()

    if req:
        req.status = "Cancelled"

    # Clear tenant move-out info
    tenant.move_out_date = None
    tenant.move_out_reason = None

    db.commit()

    # Notify tenant via LINE
    if tenant.line_user_id and tenant_bot_api:
        try:
            msg = f"📢 แจ้งเตือน: คำขอย้ายออกของคุณได้รับการยกเลิกโดยเจ้าของหอพัก (สถานะห้อง {tenant.room.room_number if tenant.room else ''} ยังคงเป็นปกติ)"
            tenant_bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=msg)]
                )
            )
        except Exception as e:
            logger.error(f"LINE Push Error (Cancel Move-out): {e}")

    return {"status": "Success"}

@router.get("/buildings/list")
async def list_buildings(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    buildings = db.query(models.Building).all()
    return [{"id": b.id, "name": b.name, "description": b.description} for b in buildings]

@router.post("/buildings/add")
async def add_building(name: str = Form(...), description: str = Form(None), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    new_building = models.Building(name=name, description=description)
    db.add(new_building)
    db.commit()
    return {"status": "Success", "id": new_building.id}

@router.post("/buildings/{building_id}/edit")
async def edit_building(building_id: int, name: str = Form(...), description: str = Form(None), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    building = db.query(models.Building).filter(models.Building.id == building_id).first()
    if not building: raise HTTPException(status_code=404, detail="Building not found")
    building.name = name
    building.description = description
    db.commit()
    return {"status": "Success"}

@router.post("/buildings/{building_id}/delete")
async def delete_building(building_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    building = db.query(models.Building).filter(models.Building.id == building_id).first()
    if not building: raise HTTPException(status_code=404, detail="Building not found")
    
    # Check if building has rooms
    if len(building.rooms) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete building with rooms")
        
    db.delete(building)
    db.commit()
    return {"status": "Success"}

@router.post("/rooms/add")
async def add_room(
    room_number: str = Form(...),
    floor: int = Form(...),
    base_rent: float = Form(...),
    electricity_rate: float = Form(...),
    water_rate: float = Form(...),
    building_id: str = Form(None),
    primary_payment_type: str = Form(None),
    primary_payment_id: str = Form(None),
    recurring_charges: str = Form("[]"),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
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
        primary_payment_type=primary_payment_type,
        primary_payment_id=primary_payment_id,
        recurring_charges=recurring_charges,
        status="Vacant"
    )
    db.add(new_room)
    db.commit()
    return {"status": "Success"}

@router.post("/rooms/{room_id}/edit")
async def edit_room(
    room_id: int,
    room_number: str = Form(...),
    floor: int = Form(...),
    base_rent: float = Form(...),
    electricity_rate: float = Form(...),
    water_rate: float = Form(...),
    building_id: str = Form(None),
    primary_payment_type: str = Form(None),
    primary_payment_id: str = Form(None),
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
    room.primary_payment_type = primary_payment_type
    room.primary_payment_id = primary_payment_id
    room.recurring_charges = recurring_charges
    db.commit()
    return {"status": "Success"}

@router.post("/rooms/bulk-recurring")
async def bulk_recurring(charges_json: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    owner = db.query(models.Owner).first()
    if owner:
        owner.default_recurring_charges = charges_json
        db.commit()
    return {"status": "Success"}

@router.get("/rooms/{room_id}/details")
async def get_room_details(room_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room: raise HTTPException(status_code=404, detail="Room not found")

    tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == room_id, models.Tenant.status == "Active").first()
    meter_history = db.query(models.MeterReading).filter(models.MeterReading.room_id == room_id).order_by(models.MeterReading.id.desc()).limit(12).all()
    
    # Combined Payment History
    payment_history = db.query(models.Invoice).filter(models.Invoice.room_id == room_id).order_by(models.Invoice.id.desc()).limit(12).all()
    leases = db.query(models.Lease).filter(models.Lease.room_id == room_id).order_by(models.Lease.id.desc()).limit(5).all()
    
    payments = []
    for p in payment_history:
        payments.append({
            "type": "Monthly Bill",
            "month": p.billing_month,
            "year": p.billing_year,
            "total": p.total_amount,
            "status": p.status,
            "date": p.paid_at.strftime("%d/%m/%Y") if p.paid_at else "-"
        })
    
    for l in leases:
        if l.security_deposit_amount > 0 or l.advance_rent_amount > 0:
            payments.append({
                "type": "Initial Payment",
                "month": l.start_date.month,
                "year": l.start_date.year,
                "total": (l.security_deposit_amount + l.advance_rent_amount),
                "status": l.initial_payment_status,
                "date": l.initial_payment_date.strftime("%d/%m/%Y") if l.initial_payment_date else "-"
            })
    
    payments.sort(key=lambda x: (x['year'], x['month']), reverse=True)

    assets = db.query(models.RoomAsset).filter(models.RoomAsset.room_id == room_id).all()

    owner = db.query(models.Owner).first()
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
        "payments": payments,
        "assets": [{"id": a.id, "name": a.name, "quantity": a.quantity} for a in assets]
    }

@router.post("/rooms/{room_id}/assets/add")
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

@router.post("/assets/{asset_id}/edit")
async def edit_room_asset(asset_id: int, name: str = Form(...), quantity: int = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    asset = db.query(models.RoomAsset).filter(models.RoomAsset.id == asset_id).first()
    if not asset: raise HTTPException(status_code=404, detail="Asset not found")
    asset.name = name
    asset.quantity = quantity
    db.commit()
    return {"status": "Success"}

@router.post("/assets/{asset_id}/delete")
async def delete_room_asset(asset_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    asset = db.query(models.RoomAsset).filter(models.RoomAsset.id == asset_id).first()
    if not asset: raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(asset)
    db.commit()
    return {"status": "Success"}

async def send_invoice_paid_notification(db: Session, invoice: models.Invoice):
    tenant = invoice.tenant
    if tenant and tenant.line_user_id and tenant_bot_api:
        lang = tenant.language or "th"
        owner = db.query(models.Owner).first()
        apt_name = owner.display_name if owner and owner.display_name else "SukAnan Apartment"
        room_no = invoice.room.room_number if invoice.room else "N/A"
        period = f"{invoice.billing_month}/{invoice.billing_year}"
        paid_date = invoice.paid_at.strftime("%d/%m/%Y %H:%M") if invoice.paid_at else datetime.now().strftime("%d/%m/%Y %H:%M")
        total_fmt = f"{invoice.total_amount:,.2f}"
        
        # Safety check for missing UUID
        if not invoice.uuid:
            invoice.uuid = str(uuid.uuid4())
            db.commit()
            
        bill_url = f"{BASE_URL}/bill/{invoice.uuid}?lang={lang}"
        
        flex_json = {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": get_text('receipt_title', lang), "weight": "bold", "size": "xl", "color": "#FFFFFF", "align": "center"},
                    {"type": "text", "text": get_text('status_paid', lang), "size": "sm", "color": "#FFFFFF", "align": "center", "margin": "sm"}
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
                                    {"type": "text", "text": get_text('room_label', lang), "size": "sm", "color": "#555555", "flex": 0},
                                    {"type": "text", "text": room_no, "size": "sm", "color": "#111111", "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": get_text('bill_cycle', lang), "size": "sm", "color": "#555555", "flex": 0},
                                    {"type": "text", "text": period, "size": "sm", "color": "#111111", "align": "end"}
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {"type": "text", "text": get_text('payment_date_label', lang), "size": "sm", "color": "#555555", "flex": 0},
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
                            {"type": "text", "text": get_text('total_sum_label', lang), "size": "md", "color": "#555555", "flex": 0, "weight": "bold"},
                            {"type": "text", "text": f"฿{total_fmt}", "size": "xl", "color": "#27ae60", "align": "end", "weight": "bold"}
                        ]
                    },
                    {"type": "text", "text": get_text('thank_you', lang), "size": "sm", "color": "#aaaaaa", "margin": "xxl", "align": "center"}
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
                        "action": {"type": "uri", "label": get_text('view_details', lang), "uri": bill_url}
                    }
                ],
                "flex": 0
            }
        }
        
        try:
            tenant_bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[FlexMessage(alt_text="ใบเสร็จรับเงิน", contents=FlexContainer.from_dict(flex_json))]
                )
            )
        except Exception as e:
            logger.error(f"LINE Flex Error: {e}")
            try:
                tenant_bot_api.push_message(
                    PushMessageRequest(
                        to=tenant.line_user_id,
                        messages=[TextMessage(text=f"✅ ชำระเงินเรียบร้อย! บิลเดือน {period} ได้รับการตรวจสอบแล้ว ขอบคุณครับ")]
                    )
                )
            except: pass

        if invoice.invoice_type == "Initial":
            try:
                welcome_msg = get_text('move_in_approved_welcome_msg', lang)
                tenant_bot_api.push_message(
                    PushMessageRequest(
                        to=tenant.line_user_id,
                        messages=[TextMessage(text=welcome_msg)]
                    )
                )
            except Exception as e:
                logger.error(f"Failed to send welcome message: {e}")

@router.post("/invoice/{invoice_id}/confirm-cash")
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
    
    if invoice.invoice_type == "Initial":
        owner = db.query(models.Owner).first()
        perform_final_approval(db, invoice, owner)

    db.commit()
    
    await send_invoice_paid_notification(db, invoice)
    return {"status": "Success", "receipt": invoice.payment_receipt_img}

@router.post("/invoice/{invoice_id}/cancel")
async def cancel_invoice(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    if invoice.status == "Paid":
        raise HTTPException(status_code=400, detail="ไม่สามารถยกเลิกบิลที่ชำระเงินเรียบร้อยแล้วได้")
        
    db.delete(invoice)
    db.commit()
    return {"status": "Success"}

@router.get("/invoice/{invoice_id}/details")
async def get_invoice_details(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    other_charges = []
    if invoice.other_charges:
        try:
            other_charges = json.loads(invoice.other_charges)
        except: pass

    # Dynamic late fee update
    if invoice.status == "Unpaid":
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

@router.post("/invoice/{invoice_id}/approve")
async def approve_invoice(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    invoice.status = "Paid"
    if not invoice.paid_at:
        invoice.paid_at = datetime.now()
    
    if invoice.invoice_type == "Initial":
        owner = db.query(models.Owner).first()
        perform_final_approval(db, invoice, owner)

    db.commit()
    
    await send_invoice_paid_notification(db, invoice)
    return {"status": "Success"}

@router.post("/invoice/{invoice_id}/reject")
async def reject_invoice(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    invoice.status = "Unpaid"
    db.commit()
    
    tenant = invoice.tenant
    if tenant and tenant.line_user_id and line_bot_api:
        lang = tenant.language or "th"
        if invoice.invoice_type == "Initial":
            msg = "❌ แจ้งเตือน: สลิปการโอนเงินแรกเข้าไม่ถูกต้อง กรุณาตรวจสอบหรืออัปโหลดใหม่อีกครั้ง" if lang == "th" else "❌ Notification: Initial payment slip is incorrect. Please check or upload again."
        else:
            msg = f"❌ แจ้งเตือน: สลิปการโอนเงินของบิลเดือน {invoice.billing_month}/{invoice.billing_year} ไม่ถูกต้อง กรุณาตรวจสอบหรืออัปโหลดใหม่อีกครั้ง"
        
        try:
            line_bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=msg)]
                )
            )
        except Exception as e:
            logger.error(f"Failed to send rejection message: {e}")
        
    return {"status": "Success"}

@router.post("/invoice/{invoice_id}/send-line")
async def send_invoice_line(invoice_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    invoice = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    if not invoice: raise HTTPException(status_code=404, detail="Invoice not found")
    
    tenant = invoice.tenant
    if not tenant or not tenant.line_user_id:
        raise HTTPException(status_code=400, detail="ไม่สามารถส่งได้: ผู้เช่ายังไม่ได้ลงทะเบียน LINE")
    
    if not tenant_bot_api:
        raise HTTPException(status_code=500, detail="LINE Bot API not configured")

    lang = tenant.language or "th"
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
    room_number = invoice.room.room_number if invoice.room else "N/A"
    total_fmt = "{:,.2f}".format(invoice.total_amount)
    
    owner = db.query(models.Owner).first()
    apt_name = owner.display_name if owner and owner.display_name else "SukAnan Apartment"

    flex_contents = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": get_text('invoice_title', lang),
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
                    "text": apt_name,
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
                                {"type": "text", "text": get_text('room_label', lang), "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": room_number, "size": "sm", "color": "#111111", "align": "end"}
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
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#1DB446",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": get_text('view_details', lang),
                        "uri": bill_url
                    }
                }
            ],
            "flex": 0
        }
    }
    
    try:
        tenant_bot_api.push_message(
            PushMessageRequest(
                to=tenant.line_user_id,
                messages=[FlexMessage(alt_text="ใบแจ้งค่าเช่า", contents=FlexContainer.from_dict(flex_contents))]
            )
        )
        return {"status": "Success"}
    except Exception as e:
        msg = f"📄 ใบแจ้งค่าเช่าเดือน {invoice.billing_month}/{invoice.billing_year}\n"
        msg += f"ห้อง {room_number}\n"
        msg += f"ยอดรวม: {total_fmt} บาท\n\n"
        msg += f"ดูรายละเอียดและแจ้งชำระเงินได้ที่:\n{bill_url}"
        try:
            tenant_bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=msg)]
                )
            )
            return {"status": "Success"}
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"LINE Error: {str(e2)}")

@router.post("/repair/{repair_id}/status")
async def update_repair_status(repair_id: int, status: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    repair = db.query(models.MaintenanceRequest).filter(models.MaintenanceRequest.id == repair_id).first()
    if not repair: raise HTTPException(status_code=404, detail="Repair request not found")
    
    repair.status = status
    db.commit()
    
    tenant = repair.tenant
    if tenant and tenant.line_user_id and line_bot_api:
        try:
            message = f"🛠️ อัปเดตสถานะการแจ้งซ่อม: {repair.title}\nสถานะ: {status}"
            line_bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=message)]
                )
            )
        except Exception as e:
            logger.error(f"LINE Push Error: {e}")
            
    return {"status": "Success"}

@router.get("/settings/configs")
async def get_all_configs(db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    configs = db.query(models.SystemConfig).all()
    return [{
        "key": c.key,
        "value": security.decrypt_value(c.value),
        "description": c.description
    } for c in configs]

@router.post("/settings/configs/save")
async def save_config(
    key: str = Form(...),
    value: str = Form(...),
    description: str = Form(None),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    security.set_system_config(db, key, value, description)
    refresh_configs()
    return {"status": "Success"}

@router.post("/settings/save")
async def save_settings(
    display_name: str = Form(None),
    promptpay_config: str = Form("[]"),
    bank_config: str = Form("[]"),
    qr_enabled: str = Form("1"),
    late_fee_enabled: str = Form("0"),
    due_day: str = Form("5"),
    late_fee_per_day: str = Form("50.0"),
    lease_template: str = Form(None),
    move_in_fees_config: str = Form("[]"),
    magic_link_duration_min: int = Form(5),
    meter_history_page_size: int = Form(10),
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
    owner.bank_config = bank_config

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
    owner.meter_history_page_size = meter_history_page_size
    
    db.commit()
    return {"status": "Success"}

@router.post("/settings/upload-bank-qr")
async def upload_bank_qr(
    image: UploadFile = File(...),
    admin: bool = Depends(get_admin)
):
    if not os.path.exists(uploads_dir):
        os.makedirs(uploads_dir)
        
    file_ext = os.path.splitext(image.filename)[1]
    file_name = f"bank_qr_{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(uploads_dir, file_name)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
    
    return {"url": f"/uploads/{file_name}"}

@router.get("/magic-login")
async def magic_login(request: Request, token: str, db: Session = Depends(get_db)):
    owner = db.query(models.Owner).filter(
        models.Owner.magic_token == token,
        models.Owner.magic_token_expires > datetime.now()
    ).first()
    
    if not owner:
        return HTMLResponse(content="<h2>ลิงก์หมดอายุหรือไม่ถูกต้อง กรุณากดใหม่จาก LINE Admin</h2>", status_code=400)
    
    params = dict(request.query_params)
    if 'token' in params: del params['token']
    
    import urllib.parse
    query_string = urllib.parse.urlencode(params)
    target_url = "/admin/dashboard"
    if query_string:
        target_url += "?" + query_string
    
    token = secrets.token_hex(32)
    owner.session_token = token
    owner.magic_token = None
    db.commit()
    
    response = RedirectResponse(url=target_url, status_code=303)
    response.set_cookie(
        key="admin_session", 
        value=token, 
        httponly=True, 
        secure=True, 
        samesite="lax"
    )
    return response

@router.get("/promptpay/preview")
async def preview_promptpay(pp_id: str, admin: bool = Depends(get_admin)):
    try:
        payload = promptpay.generate_promptpay_payload(pp_id)
        return {"payload": payload}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/broadcast")
async def broadcast_announcement(message: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id != None).all()
    count = 0
    if tenant_bot_api:
        for t in tenants:
            try:
                lang = t.language or "th"
                prefix = get_text('broadcast_prefix', lang)
                tenant_bot_api.push_message(
                    PushMessageRequest(
                        to=t.line_user_id,
                        messages=[TextMessage(text=f"{prefix}\n{message}")]
                    )
                )
                count += 1
            except Exception as e:
                logger.error(f"Broadcast Error to {t.line_user_id}: {e}")
    else:
        logger.info(f"MOCK BROADCAST: {message}")
        count = len(tenants)

    return {"status": "Success", "sent_count": count}

@router.post("/tenants/{tenant_id}/send-line")
async def send_direct_line(tenant_id: int, message: str = Form(...), db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if not tenant.line_user_id:
        raise HTTPException(status_code=400, detail=get_text('error_not_registered_line', "th"))

    if tenant_bot_api:
        try:
            tenant_bot_api.push_message(
                PushMessageRequest(
                    to=tenant.line_user_id,
                    messages=[TextMessage(text=message)]
                )
            )
        except Exception as e:
            logger.error(f"Direct Message Error to {tenant.line_user_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to send LINE message: {str(e)}")
    else:
        logger.info(f"MOCK DIRECT MESSAGE to {tenant.line_user_id}: {message}")

    return {"status": "Success"}

@router.get("/report/export")
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

@router.get("/tenants/{tenant_id}/residents")
async def get_residents(tenant_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    tenant = db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()
    if not tenant: raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant.residents

@router.post("/tenants/{tenant_id}/residents/add")
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

@router.post("/residents/{resident_id}/edit")
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

@router.post("/residents/{resident_id}/delete")
async def delete_resident(resident_id: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    resident = db.query(models.Resident).filter(models.Resident.id == resident_id).first()
    if not resident: raise HTTPException(status_code=404, detail="Resident not found")
    
    tenant = resident.tenant
    if tenant.line_user_id and len(tenant.residents) <= 1:
        raise HTTPException(status_code=400, detail="ไม่สามารถลบได้ ต้องมีอย่างน้อย 1 รายชื่อสำหรับห้องที่ลงทะเบียนแล้ว")
        
    db.delete(resident)
    db.commit()
    return {"status": "Success"}

@router.get("/tenants/history")
async def get_tenant_history(page: int = 1, page_size: int = 10, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    query = db.query(models.TenantHistory)
    
    total_count = query.count()
    total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 1
    
    history = query.order_by(models.TenantHistory.end_date.desc())\
        .offset((page - 1) * page_size).limit(page_size).all()
        
    results = []
    for h in history:
        tenant = db.query(models.Tenant).filter(models.Tenant.uuid == h.tenant_uuid).first()
        settlement_data = None
        if tenant:
            settlement = db.query(models.Settlement).filter(
                models.Settlement.tenant_id == tenant.id
            ).order_by(models.Settlement.settlement_date.desc()).first()
            
            if settlement:
                settlement_data = {
                    "settlement_date": settlement.settlement_date.strftime("%d/%m/%Y %H:%M") if settlement.settlement_date else "-",
                    "pro_rated_rent": settlement.pro_rated_rent,
                    "electricity_units": settlement.electricity_units,
                    "electricity_amount": settlement.electricity_amount,
                    "water_units": settlement.water_units,
                    "water_amount": settlement.water_amount,
                    "unpaid_invoices_amount": settlement.unpaid_invoices_amount,
                    "cleaning_fee": settlement.cleaning_fee,
                    "damage_fee": settlement.damage_fee,
                    "other_fees": settlement.other_fees,
                    "total_deductions": settlement.total_deductions,
                    "security_deposit_amount": settlement.security_deposit_amount,
                    "advance_rent_amount": getattr(settlement, 'advance_rent_amount', 0),
                    "final_balance": settlement.final_balance,
                    "refund_method": settlement.refund_method,
                    "refund_receipt_img": settlement.refund_receipt_img,
                    "notes": settlement.notes
                }

        results.append({
            "id": h.id,
            "room_number": h.room_number,
            "full_name": h.full_name,
            "phone_number": h.phone_number,
            "start_date": h.start_date.strftime("%d/%m/%Y") if h.start_date else "-",
            "end_date": h.end_date.strftime("%d/%m/%Y") if h.end_date else "-",
            "residents": json.loads(h.residents_json) if h.residents_json else [],
            "settlement": settlement_data
        })
        
    return {
        "items": results,
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": page,
        "page_size": page_size
    }

@router.get("/tenants/search")
async def search_tenants(q: str = "", db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
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
            models.TenantHistory.full_name.ilike(f"%{q}%"),
            models.TenantHistory.phone_number.ilike(f"%{q}%")
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
            "name": f"{r.full_name or ''}",
            "phone": r.phone_number,
            "workplace": "",
            "period": f"{r.start_date.strftime('%d/%m/%Y') if r.start_date else '?'} - {r.end_date.strftime('%d/%m/%Y') if r.end_date else '?'}"
        })
        
    return results

@router.get("/meters/current")
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
    
    try:
        global_recurring = json.loads(owner.default_recurring_charges) if owner and owner.default_recurring_charges else []
    except:
        global_recurring = []
        
    try:
        room_recurring = json.loads(room.recurring_charges) if room.recurring_charges else []
    except:
        room_recurring = []

    manual_charges = []
    if invoice and invoice.other_charges:
        try:
            all_saved = json.loads(invoice.other_charges)
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

@router.get("/meters/history")
async def get_meter_history(room_id: int = None, page: int = 1, page_size: int = None, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    owner = db.query(models.Owner).first()
    if page_size is None:
        page_size = owner.meter_history_page_size if owner else 10
        
    query = db.query(models.MeterReading).join(models.Room)
    if room_id:
        query = query.filter(models.MeterReading.room_id == room_id)

    total_count = query.count()
    total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 1

    readings = query.order_by(models.MeterReading.billing_year.desc(), models.MeterReading.billing_month.desc())\
        .offset((page - 1) * page_size).limit(page_size).all()

    results = []
    for r in readings:
        room = db.query(models.Room).filter(models.Room.id == r.room_id).first()
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
            "invoice_id": invoice.id if invoice else None,
            "invoice_status": invoice.status if invoice else "No Invoice"
        })
        
    return {
        "items": results,
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": page,
        "page_size": page_size
    }

@router.get("/repair/history")
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

@router.post("/invoice/preview")
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
        
    # 3. Add manual charges
    seen = set((c.get('description'), c.get('amount')) for c in final_other_charges)
    for c in parsed_charges:
        if (c.get('description'), c.get('amount')) not in seen:
            final_other_charges.append(c)

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

@router.post("/meters/bulk-record")
async def bulk_record_meters(
    data: str = Form(...),
    month: int = Form(...),
    year: int = Form(...),
    issue_bill: bool = Form(False),
    db: Session = Depends(get_db),
    admin: bool = Depends(get_admin)
):
    try:
        readings = json.loads(data)
    except:
        raise HTTPException(status_code=400, detail="Invalid data format")
        
    results = []
    for r in readings:
        room_id = r.get("room_id")
        elec_raw = r.get("elec")
        water_raw = r.get("water")
        other_charges = r.get("other_charges")
        
        if room_id is None:
            continue
            
        if (elec_raw == '' or elec_raw is None) and (water_raw == '' or water_raw is None):
            continue
            
        try:
            elec = float(elec_raw)
            water = float(water_raw)
        except (ValueError, TypeError):
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
        
        inv = billing.calculate_bill(db, room_id, month, year, other_charges=other_charges, save_only=(not issue_bill))
        results.append({"room_id": room_id, "status": "Success", "invoice_uuid": inv.uuid if inv else None})
        
    return {"status": "Complete", "results": results}

@router.get("/meters/bulk-context")
async def get_bulk_context(building_id: int, month: int, year: int, db: Session = Depends(get_db), admin: bool = Depends(get_admin)):
    rooms_query = db.query(models.Room).filter(models.Room.building_id == building_id).all()
    
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
        tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == r.id, models.Tenant.line_user_id != None).first()
        
        prev_reading = db.query(models.MeterReading).filter(
            models.MeterReading.room_id == r.id,
            models.MeterReading.billing_month == prev_month,
            models.MeterReading.billing_year == prev_year
        ).first()
        
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

@router.post("/meters/record")
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

# Helpers from main.py

def get_magic_url(owner, db, path=""):
    token = secrets.token_urlsafe(16)
    owner.magic_token = token
    owner.magic_token_expires = datetime.now() + timedelta(minutes=owner.magic_link_duration_min or 5)
    db.commit()
    
    url = f"{BASE_URL}/admin/magic-login?token={token}"
    return url

def create_initial_invoice(db: Session, tenant, room_ids: list, owner, start_date=None):
    if not start_date:
        start_date = tenant.requested_move_in_date if tenant.requested_move_in_date else datetime.now()
    
    success_rooms = []
    g_total = 0.0
    g_deposit = 0.0
    g_advance = 0.0
    g_other = 0.0
    
    first_iter = True
    for rid in room_ids:
        room = db.query(models.Room).filter(models.Room.id == rid).first()
        if not room or room.status != "Vacant":
            continue
            
        target_tenant = tenant
        if not first_iter:
            target_tenant = models.Tenant(
                line_user_id=tenant.line_user_id,
                full_name=tenant.full_name,
                phone_number=tenant.phone_number,
                citizen_id=tenant.citizen_id,
                requested_move_in_date=tenant.requested_move_in_date,
                status="Awaiting Payment",
                language=tenant.language
            )
            db.add(target_tenant)
            db.flush()
        else:
            tenant.status = "Awaiting Payment"
            tenant.current_room_id = room.id
        
        security_deposit = 0.0
        advance_rent = 0.0
        applied_fees = []
        room_total = 0.0
        
        config_str = owner.move_in_fees_config if owner and owner.move_in_fees_config else "[]"
        try: config = json.loads(config_str)
        except: config = []
 
        if not config:
            config = [
                {"name": "ค่าเช่าล่วงหน้า 1 เดือน", "value": 1, "is_multiplier": True},
                {"name": "ค่าประกันทรัพย์สิน", "value": 5000, "is_multiplier": False}
            ]
        
        for f in config:
            amt = f['value'] * room.base_rent if f.get('is_multiplier') else f['value']
            applied_fees.append({"name": f['name'], "amount": amt})
            room_total += amt
            if "ประกัน" in f['name']: security_deposit += amt
            elif "ล่วงหน้า" in f['name']: advance_rent += amt
        
        g_deposit += security_deposit
        g_advance += advance_rent
        g_other += (room_total - security_deposit - advance_rent)
        g_total += room_total

        new_invoice = models.Invoice(
            room_id=room.id,
            tenant_id=target_tenant.id,
            billing_month=start_date.month,
            billing_year=start_date.year,
            rent_amount=advance_rent,
            electricity_amount=0.0,
            water_amount=0.0,
            other_charges=json.dumps(applied_fees),
            total_amount=room_total,
            status="Unpaid",
            invoice_type="Initial"
        )
        db.add(new_invoice)
        
        success_rooms.append(room.room_number)
        first_iter = False
    
    return success_rooms, g_deposit, g_advance, g_other, g_total

def perform_final_approval(db: Session, invoice, owner):
    tenant = invoice.tenant
    room = invoice.room
    if not tenant or not room: return False

    # 1. Update Room Status
    room.status = "Occupied"

    # 2. Create Lease
    start_date = tenant.requested_move_in_date or datetime.now()
    lease_content = owner.lease_template or ""
    
    replacements = {
        "{tenant_name}": tenant.full_name,
        "{room_number}": room.room_number,
        "{floor}": str(room.floor),
        "{base_rent}": f"{room.base_rent:,.2f}",
        "{start_date}": start_date.strftime("%d/%m/%Y"),
        "{initial_fees}": invoice.other_charges
    }
    for placeholder, value in replacements.items():
        if value is not None:
            lease_content = lease_content.replace(placeholder, str(value))

    security_deposit = 0.0
    advance_rent = 0.0
    try:
        fees = json.loads(invoice.other_charges)
        for f in fees:
            if "ประกัน" in f['name']: security_deposit += f['amount']
            elif "ล่วงหน้า" in f['name']: advance_rent += f['amount']
    except: pass

    new_lease = models.Lease(
        room_id=room.id,
        tenant_id=tenant.id,
        start_date=start_date,
        lease_content=lease_content,
        initial_fees=invoice.other_charges,
        security_deposit_amount=security_deposit,
        advance_rent_amount=advance_rent,
        initial_payment_status="Paid",
        initial_payment_method=invoice.payment_method,
        initial_payment_date=invoice.paid_at,
        initial_payment_receipt=invoice.payment_receipt_img
    )
    db.add(new_lease)

    # 3. Activate Tenant
    tenant.status = "Active"
    setup_personal_rich_menu(tenant, db)
    return True

def send_initial_payment_flex(tenant, success_rooms, g_deposit, g_advance, g_other, g_total, owner, bot_api, invoice_uuid: str = None):
    if not bot_api:
        return
    
    import urllib.parse
    
    lang = tenant.language or "th"
    rooms_str = ", ".join(success_rooms)
    
    qr_enabled = owner.qr_payment_enabled if owner else 1
    payment_instruction_contents = []
    
    if qr_enabled:
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
                    "label": get_text('download_csv', lang).replace("CSV", "QR"),
                    "uri": qr_large_url
                },
                "style": "secondary",
                "height": "sm",
                "margin": "xs"
            },
            {"type": "text", "text": "💡 " + ("Scan in bank app to pay" if lang == "en" else "銀行アプリでสแกนして支払う" if lang == "jp" else "ท่านสามารถนำ QR ไปสแกนในแอปธนาคารได้ทันที"), "size": "xxs", "color": "#888888", "align": "center", "margin": "md"},
            {"type": "text", "text": f"{get_text('promptpay', lang)}: {promptpay_id}", "size": "xs", "color": "#888888", "align": "center", "margin": "sm"}
        ])

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
        bot_api.push_message(
            PushMessageRequest(
                to=tenant.line_user_id,
                messages=[FlexMessage(alt_text="ใบแจ้งยอดชำระแรกเข้า", contents=FlexContainer.from_dict(flex_json))]
            )
        )
    except Exception as e:
        logger.error(f"Error sending initial payment flex: {e}")
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
    
    active_tenants = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant.line_user_id, models.Tenant.status == "Active").all()
    if not active_tenants:
        return None
        
    multi_room = len(active_tenants) > 1

    if tenant.rich_menu_id and not force:
        return tenant.rich_menu_id

    token = config.LINE_TENANT_CHANNEL_ACCESS_TOKEN
    if not token:
        token = os.getenv("LINE_TENANT_CHANNEL_ACCESS_TOKEN")
    
    if not token:
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    lang = tenant.language or "th"
    chat_bar_text = get_text('tenant_chat_bar', lang) or "Tenant Menu"
    bill_label = get_text('view_bill', lang)
    repair_label = get_text('repairs', lang)
    history_label = get_text('history', lang)
    chat_label = get_text('chat_label', lang)
    move_in_label = get_text('move_in', lang)
    move_out_label = get_text('move_out', lang)
    
    img_filename = f"tenant_richmenu_{lang}.jpg"
    if lang == "th" or not os.path.exists(os.path.join(src_dir, img_filename)):
        img_filename = "tenant_richmenu.jpg"

    if not multi_room:
        repair_action = {"type": "uri", "label": repair_label, "uri": f"{BASE_URL}/repair/{tenant.uuid}"}
        history_action = {"type": "uri", "label": history_label, "uri": f"{BASE_URL}/history/{tenant.uuid}"}
        move_out_action = {"type": "uri", "label": move_out_label, "uri": f"{BASE_URL}/move-out/{tenant.uuid}"}
        menu_name = f"Tenant Menu {lang.upper()} Single - {tenant.line_user_id[:10]}"
    else:
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
            {"bounds": {"x": 0, "y": 0, "width": 833, "height": 843}, "action": {"type": "message", "text": bill_label}},
            {"bounds": {"x": 833, "y": 0, "width": 834, "height": 843}, "action": repair_action},
            {"bounds": {"x": 1667, "y": 0, "width": 833, "height": 843}, "action": history_action},
            {"bounds": {"x": 0, "y": 843, "width": 833, "height": 843}, "action": {
                "type": "postback",
                "data": "action=chat",
                "inputOption": "openKeyboard"
            }},
            {"bounds": {"x": 833, "y": 843, "width": 834, "height": 843}, "action": {"type": "message", "text": move_in_label}},
            {"bounds": {"x": 1667, "y": 843, "width": 833, "height": 843}, "action": move_out_action}
        ]
    }

    try:
        res = requests.post("https://api.line.me/v2/bot/richmenu", headers=headers, json=rich_menu_data)
        if res.status_code not in [200, 201]:
            logger.error(f"Error creating personal rich menu: {res.text}")
            return None
        
        rich_menu_id = res.json()["richMenuId"]

        image_path = os.path.join(src_dir, img_filename)
        if not os.path.exists(image_path):
            image_path = os.path.join(src_dir, "tenant_richmenu.jpg")
        if not os.path.exists(image_path):
             image_path = os.path.join(src_dir, "tenant_richmenu.png")
        if not os.path.exists(image_path):
             image_path = os.path.join(src_dir, "image", "tenantrichmenu.jpg")

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
        
        requests.post(
            f"https://api.line.me/v2/bot/user/{tenant.line_user_id}/richmenu/{rich_menu_id}",
            headers=headers
        )
        
        for t in active_tenants:
            t.rich_menu_id = rich_menu_id
        db.commit()
        
        return rich_menu_id
    except Exception as e:
        logger.error(f"setup_personal_rich_menu Error: {e}")
        return None
