import uuid
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.ext.hybrid import hybrid_property
from database import Base
from security import encrypt_value, decrypt_value

class Owner(Base):
    __tablename__ = "owners"
    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String)
    password_hash = Column(String) # For admin login
    pairing_code = Column(String, unique=True, index=True) # Temporary code to link LINE
    magic_token = Column(String, unique=True, index=True) # For auto-login from LINE
    magic_token_expires = Column(DateTime)
    magic_link_duration_min = Column(Integer, default=5) # Duration of magic link in minutes
    promptpay_config = Column(Text, default='[]')
    promptpay_name = Column(String)
    qr_payment_enabled = Column(Integer, default=1)
    late_fee_enabled = Column(Integer, default=0)
    due_day = Column(Integer, default=5)
    late_fee_per_day = Column(Float, default=50.0)
    lease_template = Column(Text) # HTML Template for contracts
    move_in_fees_config = Column(Text, default='[]') # JSON: [{"name": "...", "value": 0, "is_multiplier": bool}]
    default_recurring_charges = Column(Text, default='[]') # Template for bulk setup

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)
    used = Column(Integer, default=0) # 0 = no, 1 = yes

class SystemConfig(Base):
    __tablename__ = "system_configs"
    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=False) # Encrypted value
    description = Column(String, nullable=True)

class Building(Base):
    __tablename__ = "buildings"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    
    rooms = relationship("Room", back_populates="building")

class RoomPaymentChannel(Base):
    __tablename__ = "room_payment_channels"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    channel_type = Column(String, default="PromptPay") # PromptPay, Bank, Cash
    channel_id = Column(String, nullable=False) # e.g. Phone number for PromptPay
    channel_name = Column(String) # Account owner name
    
    room = relationship("Room", back_populates="payment_channels")

class Room(Base):
    __tablename__ = "rooms"
    id = Column(Integer, primary_key=True, index=True)
    building_id = Column(Integer, ForeignKey("buildings.id"))
    room_number = Column(String, index=True, nullable=False)
    floor = Column(Integer)
    status = Column(String, default="Vacant")
    base_rent = Column(Float, default=0.0)
    electricity_rate = Column(Float, default=0.0)
    water_rate = Column(Float, default=0.0)
    promptpay_id = Column(String) # The specific PromptPay ID for this room
    recurring_charges = Column(Text) # JSON: [{"description": "...", "amount": 0}]

    building = relationship("Building", back_populates="rooms")
    assets = relationship("RoomAsset", back_populates="room", cascade="all, delete-orphan")
    payment_channels = relationship("RoomPaymentChannel", back_populates="room", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint('building_id', 'room_number', name='_building_room_uc'),)

class RoomAsset(Base):
    __tablename__ = "room_assets"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    name = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    
    room = relationship("Room", back_populates="assets")

class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    line_user_id = Column(String, index=True, nullable=False)
    full_name = Column(String)
    phone_number = Column(String)
    _citizen_id = Column("citizen_id", String) # Encrypted National ID

    @hybrid_property
    def citizen_id(self):
        return decrypt_value(self._citizen_id)

    @citizen_id.setter
    def citizen_id(self, value):
        self._citizen_id = encrypt_value(value)

    current_room_id = Column(Integer, ForeignKey("rooms.id"))
    rich_menu_id = Column(String)
    status = Column(String, default="Pending") # Pending, Active, Rejected, AwaitingBuilding, AwaitingRoom, AwaitingName, AwaitingPhone
    temp_building_id = Column(Integer, ForeignKey("buildings.id")) # Temporary storage during registration
    requested_move_in_date = Column(DateTime)
    move_out_date = Column(DateTime) # Requested move-out date
    move_out_reason = Column(String)
    
    room = relationship("Room")
    residents = relationship("Resident", back_populates="tenant", cascade="all, delete-orphan")

class MoveOutRequest(Base):
    __tablename__ = "move_out_requests"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    requested_date = Column(DateTime, nullable=False)
    reason = Column(Text)
    status = Column(String, default="Pending") # Pending, Approved, Cancelled
    created_at = Column(DateTime, server_default=func.now())
    
    room = relationship("Room")
    tenant = relationship("Tenant")

class Resident(Base):
    __tablename__ = "residents"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    first_name = Column(String)
    last_name = Column(String)
    nickname = Column(String, nullable=False)
    phone_number = Column(String)
    workplace = Column(String)
    
    tenant = relationship("Tenant", back_populates="residents")

class TenantHistory(Base):
    __tablename__ = "tenant_history"
    id = Column(Integer, primary_key=True, index=True)
    room_number = Column(String)
    tenant_uuid = Column(String)
    full_name = Column(String)
    phone_number = Column(String)
    _citizen_id = Column("citizen_id", String)

    @hybrid_property
    def citizen_id(self):
        return decrypt_value(self._citizen_id)

    @citizen_id.setter
    def citizen_id(self, value):
        self._citizen_id = encrypt_value(value)

    start_date = Column(DateTime)
    end_date = Column(DateTime)
    residents_json = Column(Text) # JSON list of residents at time of move-out

class Settlement(Base):
    __tablename__ = "settlements"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    lease_id = Column(Integer, ForeignKey("leases.id"), nullable=False)
    settlement_date = Column(DateTime, server_default=func.now())
    
    # Financial Details
    pro_rated_rent = Column(Float, default=0.0)
    electricity_units = Column(Float, default=0.0)
    electricity_amount = Column(Float, default=0.0)
    water_units = Column(Float, default=0.0)
    water_amount = Column(Float, default=0.0)
    unpaid_invoices_amount = Column(Float, default=0.0)
    
    cleaning_fee = Column(Float, default=0.0)
    damage_fee = Column(Float, default=0.0)
    other_fees = Column(Float, default=0.0)
    
    total_deductions = Column(Float, default=0.0)
    security_deposit_amount = Column(Float, default=0.0)
    final_balance = Column(Float, default=0.0) # Refund if positive, Payment due if negative
    
    refund_method = Column(String) # Cash, PromptPay, Transfer
    refund_receipt_img = Column(String)
    status = Column(String, default="Completed")
    notes = Column(Text)

    room = relationship("Room")
    tenant = relationship("Tenant")
    lease = relationship("Lease")

class Lease(Base):
    __tablename__ = "leases"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime)
    status = Column(String, default="Active")
    lease_content = Column(Text) # Snapshotted contract content
    initial_fees = Column(Text) # JSON of fees applied at start
    
    room = relationship("Room")
    tenant = relationship("Tenant")

class MeterReading(Base):
    __tablename__ = "meter_readings"
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    billing_month = Column(Integer, nullable=False)
    billing_year = Column(Integer, nullable=False)
    electricity_reading = Column(Float, nullable=False)
    water_reading = Column(Float, nullable=False)
    recorded_at = Column(DateTime, server_default=func.now())

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String, unique=True, index=True, default=lambda: str(uuid.uuid4()))
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    billing_month = Column(Integer, nullable=False)
    billing_year = Column(Integer, nullable=False)
    rent_amount = Column(Float, nullable=False)
    electricity_amount = Column(Float, nullable=False)
    water_amount = Column(Float, nullable=False)
    electricity_reading = Column(Float)
    water_reading = Column(Float)
    prev_electricity_reading = Column(Float)
    prev_water_reading = Column(Float)
    other_charges = Column(Text) # JSON list of {description, amount}
    late_fee = Column(Float, default=0.0)
    total_amount = Column(Float, nullable=False)
    status = Column(String, default="Unpaid")
    payment_method = Column(String)
    payment_receipt_img = Column(String)
    paid_at = Column(DateTime)
    
    room = relationship("Room")
    tenant = relationship("Tenant")

class MaintenanceRequest(Base):
    __tablename__ = "maintenance_requests"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    room_id = Column(Integer, ForeignKey("rooms.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    image_url = Column(String)
    status = Column(String, default="Pending")
    created_at = Column(DateTime, server_default=func.now())
    
    room = relationship("Room")
    tenant = relationship("Tenant")
