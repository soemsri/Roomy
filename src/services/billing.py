import json
import uuid
import calendar
from datetime import datetime
from sqlalchemy.orm import Session

import models
from utils import parse_sqlite_datetime
from services.line_bot import setup_personal_rich_menu

def get_late_fee(db: Session, invoice=None, billing_month=None, billing_year=None):
    """
    Calculates the late fee accrued for a given invoice or billing period.
    The late fee is calculated based on the owner's due_day and late_fee_per_day configurations.
    
    Args:
        db (Session): The active SQLAlchemy database session.
        invoice (Invoice, optional): The invoice object to check late fees for.
        billing_month (int, optional): The billing month if invoice is not provided.
        billing_year (int, optional): The billing year if invoice is not provided.
        
    Returns:
        float: The total accrued late fee amount in Baht. Returns 0.0 if late fees are disabled or not applicable.
    """
    owner = db.query(models.Owner).first()
    if not owner or not owner.late_fee_enabled:
        return 0.0
    
    m = invoice.billing_month if invoice else billing_month
    y = invoice.billing_year if invoice else billing_year
    
    if not m or not y: return 0.0
    
    try:
        due_date = datetime(y, m, owner.due_day)
        today = datetime.now()
        
        if today > due_date:
            days_late = (today - due_date).days
            return days_late * owner.late_fee_per_day
    except Exception: pass
    return 0.0

def calculate_bill(db: Session, room_id: int, month: int, year: int, other_charges: list = None, save_only: bool = False):
    """
    Calculates the monthly rental, utility (electricity/water), and miscellaneous bills for a room.
    Generates or updates the corresponding Invoice record in the database.
    
    Args:
        db (Session): The active SQLAlchemy database session.
        room_id (int): The database ID of the room.
        month (int): The billing month (1-12).
        year (int): The billing year (e.g., 2026).
        other_charges (list, optional): List of manual one-off charge dictionaries to append.
        save_only (bool, optional): If True, saves the invoice with 'Draft' status instead of 'Unpaid'.
        
    Returns:
        Invoice: The generated or updated Invoice SQLAlchemy object, or None if calculation fails (e.g., room/tenant not found).
    """
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        return None

    # Get the latest reading for this month
    current_reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id,
        models.MeterReading.billing_month == month,
        models.MeterReading.billing_year == year
    ).first()

    if not current_reading:
        return None

    # Get the previous month's reading
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    
    previous_reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id,
        models.MeterReading.billing_month == prev_month,
        models.MeterReading.billing_year == prev_year
    ).order_by(models.MeterReading.id.desc()).first()

    # Calculate units used
    elec_units = current_reading.electricity_reading - (previous_reading.electricity_reading if previous_reading else 0)
    water_units = current_reading.water_reading - (previous_reading.water_reading if previous_reading else 0)

    # Ensure no negative units
    elec_units = max(0, elec_units)
    water_units = max(0, water_units)

    # Calculate amounts
    elec_amount = elec_units * room.electricity_rate
    water_amount = water_units * room.water_rate
    
    # Determine the definitive other charges list
    final_other_charges = []
    
    # 1. Global Recurring (Common Fee)
    owner = db.query(models.Owner).first()
    global_recurring = []
    if owner and owner.default_recurring_charges:
        try:
            global_recurring = json.loads(owner.default_recurring_charges)
            final_other_charges.extend(global_recurring)
        except (json.JSONDecodeError, TypeError): pass
        
    # 2. Room Specific Recurring
    room_recurring = []
    if room.recurring_charges:
        try:
            room_recurring = json.loads(room.recurring_charges)
            final_other_charges.extend(room_recurring)
        except (json.JSONDecodeError, TypeError): pass

    # Get existing invoice if any
    invoice = db.query(models.Invoice).filter(
        models.Invoice.room_id == room_id,
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year
    ).first()

    # 3. Additional Manual Charges
    if other_charges is None and invoice and invoice.other_charges:
        # Load existing charges from invoice and filter for manual ones
        try:
            existing_all = json.loads(invoice.other_charges)
            rec_keys = set((c.get('description'), c.get('amount')) for c in global_recurring + room_recurring)
            other_charges = [c for c in existing_all if (c.get('description'), c.get('amount')) not in rec_keys]
        except Exception:
            other_charges = []

    if other_charges is not None:
        # We want to avoid duplicating recurring charges if they were passed from the UI
        # We'll use a set of (description, amount) to track what's already included
        seen = set((c.get('description'), c.get('amount')) for c in final_other_charges)
        for c in other_charges:
            if (c.get('description'), c.get('amount')) not in seen:
                final_other_charges.append(c)

    # Other charges sum
    other_amount = sum(float(item.get('amount', 0)) for item in final_other_charges)

    # Initial Total
    # Calculate Pro-rata rent if it's the first month
    rent_to_charge = room.base_rent
    is_pro_rata = 0
    
    # Find current active lease
    lease = db.query(models.Lease).filter(
        models.Lease.room_id == room_id,
        models.Lease.status == "Active"
    ).first()
    
    if lease:
        lease_start = lease.start_date
        lease_start = parse_sqlite_datetime(lease_start)
            
        if lease_start and lease_start.month == month and lease_start.year == year:
            # First month! Calculate pro-rata if not starting on the 1st
            if lease_start.day > 1:
                days_in_month = calendar.monthrange(year, month)[1]
                days_stayed = days_in_month - lease_start.day + 1
                rent_to_charge = (room.base_rent / days_in_month) * days_stayed
                is_pro_rata = 1

    subtotal = rent_to_charge + elec_amount + water_amount + other_amount
    
    # Calculate Late Fee if applicable
    late_fee = get_late_fee(db, billing_month=month, billing_year=year)
    total_amount = subtotal + late_fee

    if not invoice:
        # Find current tenant for this room
        tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == room_id, models.Tenant.status == "Active").first()
        if not tenant:
            return None # No tenant, no bill
            
        invoice = models.Invoice(
            uuid=str(uuid.uuid4()),
            room_id=room_id,
            tenant_id=tenant.id,
            billing_month=month,
            billing_year=year,
            rent_amount=rent_to_charge,
            electricity_amount=elec_amount,
            water_amount=water_amount,
            electricity_reading=current_reading.electricity_reading,
            water_reading=current_reading.water_reading,
            prev_electricity_reading=previous_reading.electricity_reading if previous_reading else 0,
            prev_water_reading=previous_reading.water_reading if previous_reading else 0,
            other_charges=json.dumps(final_other_charges) if final_other_charges else None,
            late_fee=late_fee,
            total_amount=total_amount,
            status="Draft" if save_only else "Unpaid",
            is_pro_rata=is_pro_rata
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
    else:
        # Update existing invoice if recording again
        if invoice.status != "Paid":
            invoice.rent_amount = rent_to_charge
            invoice.electricity_amount = elec_amount
            invoice.water_amount = water_amount
            invoice.electricity_reading = current_reading.electricity_reading
            invoice.water_reading = current_reading.water_reading
            invoice.prev_electricity_reading = previous_reading.electricity_reading if previous_reading else 0
            invoice.prev_water_reading = previous_reading.water_reading if previous_reading else 0
            if final_other_charges:
                invoice.other_charges = json.dumps(final_other_charges)
            invoice.late_fee = late_fee
            invoice.total_amount = total_amount
            invoice.is_pro_rata = is_pro_rata
            # If issuing bill, change Draft to Unpaid
            if not save_only and invoice.status == "Draft":
                invoice.status = "Unpaid"
            db.commit()

    return invoice

def create_initial_invoice(db: Session, tenant, room_ids: list, owner, start_date=None):
    """
    Creates initial invoices for the selected rooms upon a tenant's registration.
    This calculates the initial fees (e.g., security deposit, advance rent) and saves the invoice.
    
    Args:
        db (Session): The active SQLAlchemy database session.
        tenant (Tenant): The tenant SQLAlchemy model object.
        room_ids (list): List of integer database IDs of the rooms to rent.
        owner (Owner): The owner configuration object containing move-in fee configs.
        start_date (datetime, optional): The lease start date. Defaults to tenant's requested date or today.
        
    Returns:
        tuple: A tuple containing:
            - list: List of room numbers successfully invoiced.
            - float: Total security deposit amount.
            - float: Total advance rent amount.
            - float: Total other initial charges.
            - float: Grand total of all initial fees.
    """
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
            # Create a clone for multi-room if needed
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
        
        # Calculate Initial Fees
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
        
        # Accumulate totals
        g_deposit += security_deposit
        g_advance += advance_rent
        g_other += (room_total - security_deposit - advance_rent)
        g_total += room_total

        # Create the Initial Invoice
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
    """
    Performs the final move-in approval for a tenant once their initial invoice is paid.
    Updates the room status to 'Occupied', generates a Lease contract from template, and activates the tenant on LINE.
    
    Args:
        db (Session): The active SQLAlchemy database session.
        invoice (Invoice): The paid initial Invoice object.
        owner (Owner): The owner object containing lease templates and rich menus.
        
    Returns:
        bool: True if approval succeeded, False if invoice lacks tenant or room references.
    """
    tenant = invoice.tenant
    room = invoice.room
    if not tenant or not room: return False

    # 1. Update Room Status
    room.status = "Occupied"

    # 2. Create Lease
    start_date = tenant.requested_move_in_date or datetime.now()
    lease_content = owner.lease_template or ""
    
    # Simple replacements
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

    # Parse applied fees from invoice to get deposit/advance for lease record
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


