from sqlalchemy.orm import Session
import models
from datetime import datetime
import json
import uuid

def get_late_fee(db: Session, invoice=None, billing_month=None, billing_year=None):
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
    except: pass
    return 0.0

def calculate_bill(db: Session, room_id: int, month: int, year: int, other_charges: list = None, save_only: bool = False):
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
        except: pass
        
    # 2. Room Specific Recurring
    room_recurring = []
    if room.recurring_charges:
        try:
            room_recurring = json.loads(room.recurring_charges)
            final_other_charges.extend(room_recurring)
        except: pass

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
        except:
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
    import calendar
    rent_to_charge = room.base_rent
    is_pro_rata = 0
    
    # Find current active lease
    lease = db.query(models.Lease).filter(
        models.Lease.room_id == room_id,
        models.Lease.status == "Active"
    ).first()
    
    if lease:
        lease_start = lease.start_date
        # Handle potential string dates from SQLite
        if isinstance(lease_start, str):
            try: lease_start = datetime.fromisoformat(lease_start.replace('Z', '').split('.')[0])
            except: pass
            
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
