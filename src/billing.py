from sqlalchemy.orm import Session
import models
from datetime import datetime
import json
import uuid

import calendar

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

def calculate_pro_rated_bill(db: Session, room_id: int, move_out_date: datetime, final_elec: float, final_water: float, other_charges: list = None):
    room = db.query(models.Room).filter(models.Room.id == room_id).first()
    if not room:
        return None

    month = move_out_date.month
    year = move_out_date.year

    # Get the previous reading (any month)
    previous_reading = db.query(models.MeterReading).filter(
        models.MeterReading.room_id == room_id
    ).order_by(models.MeterReading.billing_year.desc(), models.MeterReading.billing_month.desc(), models.MeterReading.id.desc()).first()

    # Calculate units used
    elec_units = final_elec - (previous_reading.electricity_reading if previous_reading else 0)
    water_units = final_water - (previous_reading.water_reading if previous_reading else 0)

    # Ensure no negative units
    elec_units = max(0, elec_units)
    water_units = max(0, water_units)

    # Calculate amounts
    elec_amount = elec_units * room.electricity_rate
    water_amount = water_units * room.water_rate
    
    # Pro-rated rent
    _, days_in_month = calendar.monthrange(year, month)
    days_stayed = move_out_date.day
    # Ensure at least 1 day if moving out on the 1st
    days_stayed = max(1, days_stayed)
    rent_amount = (room.base_rent / days_in_month) * days_stayed

    # Determine the definitive other charges list
    final_other_charges = []
    
    # 1. Global Recurring (Common Fee) - Also pro-rated? 
    # For simplicity, we'll keep it full or pro-rate it similarly. Let's pro-rate it too.
    owner = db.query(models.Owner).first()
    if owner and owner.default_recurring_charges:
        try:
            global_recurring = json.loads(owner.default_recurring_charges)
            for c in global_recurring:
                c['amount'] = (float(c.get('amount', 0)) / days_in_month) * days_stayed
            final_other_charges.extend(global_recurring)
        except: pass
    
    # 2. Building Specific Recurring
    if room.building and room.building.recurring_charges:
        try:
            building_recurring = json.loads(room.building.recurring_charges)
            for c in building_recurring:
                c['amount'] = (float(c.get('amount', 0)) / days_in_month) * days_stayed
            final_other_charges.extend(building_recurring)
        except: pass
        
    # 3. Room Specific Recurring
    if room.recurring_charges:
        try:
            room_recurring = json.loads(room.recurring_charges)
            for c in room_recurring:
                c['amount'] = (float(c.get('amount', 0)) / days_in_month) * days_stayed
            final_other_charges.extend(room_recurring)
        except: pass

    # 3. Additional Manual Charges (Not pro-rated)
    if other_charges is not None:
        final_other_charges.extend(other_charges)

    # Other charges sum
    other_amount = sum(float(item.get('amount', 0)) for item in final_other_charges)

    total_amount = rent_amount + elec_amount + water_amount + other_amount

    # Create final invoice
    tenant = db.query(models.Tenant).filter(models.Tenant.current_room_id == room_id, models.Tenant.status == "Active").first()
    if not tenant:
        return None # No tenant, no bill
            
    invoice = models.Invoice(
        uuid=str(uuid.uuid4()),
        room_id=room_id,
        tenant_id=tenant.id,
        billing_month=month,
        billing_year=year,
        rent_amount=round(rent_amount, 2),
        electricity_amount=round(elec_amount, 2),
        water_amount=round(water_amount, 2),
        electricity_reading=final_elec,
        water_reading=final_water,
        prev_electricity_reading=previous_reading.electricity_reading if previous_reading else 0,
        prev_water_reading=previous_reading.water_reading if previous_reading else 0,
        other_charges=json.dumps(final_other_charges) if final_other_charges else None,
        late_fee=0.0,
        total_amount=round(total_amount, 2),
        status="Unpaid",
        payment_method="Final Bill"
    )
    db.add(invoice)
    
    # Also record the final meter reading so it's in the history
    final_reading_record = models.MeterReading(
        room_id=room_id,
        billing_month=month,
        billing_year=year,
        electricity_reading=final_elec,
        water_reading=final_water,
        recorded_at=move_out_date
    )
    db.add(final_reading_record)
    
    db.commit()
    db.refresh(invoice)
    return invoice

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
    
    # 2. Building Specific Recurring
    if room.building and room.building.recurring_charges:
        try:
            building_recurring = json.loads(room.building.recurring_charges)
            final_other_charges.extend(building_recurring)
        except: pass
        
    # 3. Room Specific Recurring
    room_recurring = []
    if room.recurring_charges:
        try:
            room_recurring = json.loads(room.recurring_charges)
            final_other_charges.extend(room_recurring)
        except: pass

    # 3. Additional Manual Charges
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
    subtotal = room.base_rent + elec_amount + water_amount + other_amount
    
    # Calculate Late Fee if applicable
    late_fee = get_late_fee(db, billing_month=month, billing_year=year)
    total_amount = subtotal + late_fee

    # Create invoice if it doesn't exist
    invoice = db.query(models.Invoice).filter(
        models.Invoice.room_id == room_id,
        models.Invoice.billing_month == month,
        models.Invoice.billing_year == year
    ).first()

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
            rent_amount=room.base_rent,
            electricity_amount=elec_amount,
            water_amount=water_amount,
            electricity_reading=current_reading.electricity_reading,
            water_reading=current_reading.water_reading,
            prev_electricity_reading=previous_reading.electricity_reading if previous_reading else 0,
            prev_water_reading=previous_reading.water_reading if previous_reading else 0,
            other_charges=json.dumps(final_other_charges) if final_other_charges else None,
            late_fee=late_fee,
            total_amount=total_amount,
            status="Draft" if save_only else "Unpaid"
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
    else:
        # Update existing invoice if recording again
        if invoice.status != "Paid":
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
            # If issuing bill, change Draft to Unpaid
            if not save_only and invoice.status == "Draft":
                invoice.status = "Unpaid"
            db.commit()

    return invoice
