import uuid
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

class Owner(Base):
    __tablename__ = "owners"
    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String)
    promptpay_config = Column(Text, default='[]')
    promptpay_name = Column(String)
    qr_payment_enabled = Column(Integer, default=1)
    late_fee_enabled = Column(Integer, default=0)
    due_day = Column(Integer, default=5)
    late_fee_per_day = Column(Float, default=50.0)
    lease_template = Column(Text) # HTML Template for contracts
    move_in_fees_config = Column(Text, default='[]') # JSON: [{"name": "...", "value": 0, "is_multiplier": bool}]
    default_recurring_charges = Column(Text, default='[]') # Template for bulk setup

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
    line_user_id = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String)
    phone_number = Column(String)
    current_room_id = Column(Integer, ForeignKey("rooms.id"))
    status = Column(String, default="Pending") # Pending, Active, Rejected, AwaitingBuilding, AwaitingRoom, AwaitingName, AwaitingPhone
    temp_building_id = Column(Integer, ForeignKey("buildings.id")) # Temporary storage during registration
    
    room = relationship("Room")
    residents = relationship("Resident", back_populates="tenant", cascade="all, delete-orphan")

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
    first_name = Column(String)
    last_name = Column(String)
    nickname = Column(String)
    phone_number = Column(String)
    workplace = Column(String)
    start_date = Column(DateTime)
    end_date = Column(DateTime)

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
