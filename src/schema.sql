-- SukAnan Apartment Database Schema (SQLite)

-- 1. Owners/Admins
CREATE TABLE owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_user_id TEXT UNIQUE NOT NULL,
    display_name TEXT,
    password_hash TEXT,
    session_token TEXT UNIQUE,
    pairing_code TEXT UNIQUE,
    magic_token TEXT UNIQUE,
    magic_token_expires TIMESTAMP,
    magic_link_duration_min INTEGER DEFAULT 5,
    promptpay_config TEXT DEFAULT '[]',
    promptpay_name TEXT,
    bank_config TEXT DEFAULT '[]',
    qr_payment_enabled INTEGER DEFAULT 1,
    late_fee_enabled INTEGER DEFAULT 0,
    due_day INTEGER DEFAULT 5,
    late_fee_per_day REAL DEFAULT 50.0,
    lease_template TEXT,
    move_in_fees_config TEXT DEFAULT '[{"name": "ค่าเช่าล่วงหน้า 1 เดือน", "value": 1, "is_multiplier": true}, {"name": "ค่าประกันทรัพย์สิน", "value": 5000, "is_multiplier": false}]',
    default_recurring_charges TEXT DEFAULT '[]',
    meter_history_page_size INTEGER DEFAULT 10
);

-- 1.1 Password Reset Tokens
CREATE TABLE password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    used INTEGER DEFAULT 0
);

-- 1.2 System Config
CREATE TABLE system_configs (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT
);

-- 1.3 Buildings
CREATE TABLE buildings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT
);

-- 2. Rooms
CREATE TABLE rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    building_id INTEGER,
    room_number TEXT NOT NULL,
    floor INTEGER,
    status TEXT DEFAULT 'Vacant',
    base_rent REAL DEFAULT 0.0,
    electricity_rate REAL DEFAULT 0.0,
    water_rate REAL DEFAULT 0.0,
    promptpay_id TEXT,
    primary_payment_type TEXT DEFAULT 'PromptPay',
    primary_payment_id TEXT,
    recurring_charges TEXT,
    FOREIGN KEY (building_id) REFERENCES buildings(id),
    UNIQUE(building_id, room_number)
);

-- 2.1 Room Assets
CREATE TABLE room_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);

-- 2.2 Room Payment Channels
CREATE TABLE room_payment_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    channel_type TEXT DEFAULT 'PromptPay',
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);

-- 3. Tenants
CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    line_user_id TEXT NOT NULL,
    full_name TEXT,
    phone_number TEXT,
    citizen_id TEXT,
    current_room_id INTEGER,
    rich_menu_id TEXT,
    language TEXT DEFAULT 'th',
    status TEXT DEFAULT 'Pending',
    temp_building_id INTEGER,
    requested_move_in_date TIMESTAMP,
    move_out_date TIMESTAMP,
    move_out_reason TEXT,
    FOREIGN KEY (current_room_id) REFERENCES rooms(id),
    FOREIGN KEY (temp_building_id) REFERENCES buildings(id)
);

-- 3.1 Residents
CREATE TABLE residents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    first_name TEXT,
    last_name TEXT,
    nickname TEXT NOT NULL,
    phone_number TEXT,
    workplace TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

-- 3.2 Move Out Requests
CREATE TABLE move_out_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    requested_date TIMESTAMP NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'Pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);

-- 3.3 Tenant History
CREATE TABLE tenant_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT,
    tenant_uuid TEXT,
    full_name TEXT,
    phone_number TEXT,
    start_date TIMESTAMP,
    end_date TIMESTAMP,
    residents_json TEXT
);

-- 4. Leases
CREATE TABLE leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    status TEXT DEFAULT 'Active',
    lease_content TEXT,
    initial_fees TEXT,
    security_deposit_amount REAL DEFAULT 0.0,
    advance_rent_amount REAL DEFAULT 0.0,
    initial_payment_status TEXT DEFAULT 'Pending',
    initial_payment_method TEXT,
    initial_payment_date TIMESTAMP,
    initial_payment_receipt TEXT,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

-- 5. Meter Readings
CREATE TABLE meter_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    billing_month INTEGER NOT NULL,
    billing_year INTEGER NOT NULL,
    electricity_reading REAL NOT NULL,
    water_reading REAL NOT NULL,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);

-- 6. Invoices
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    room_id INTEGER NOT NULL,
    tenant_id INTEGER,
    billing_month INTEGER NOT NULL,
    billing_year INTEGER NOT NULL,
    rent_amount REAL DEFAULT 0.0,
    electricity_reading REAL,
    prev_electricity_reading REAL,
    electricity_amount REAL DEFAULT 0.0,
    water_reading REAL,
    prev_water_reading REAL,
    water_amount REAL DEFAULT 0.0,
    other_charges TEXT,
    late_fee REAL DEFAULT 0.0,
    total_amount REAL DEFAULT 0.0,
    status TEXT DEFAULT 'Unpaid',
    payment_method TEXT,
    payment_receipt_img TEXT,
    paid_at TIMESTAMP,
    is_pro_rata INTEGER DEFAULT 0,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

-- 7. Settlements (Move-out accounting)
CREATE TABLE settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    lease_id INTEGER,
    settlement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pro_rated_rent REAL DEFAULT 0.0,
    electricity_units REAL DEFAULT 0.0,
    electricity_amount REAL DEFAULT 0.0,
    water_units REAL DEFAULT 0.0,
    water_amount REAL DEFAULT 0.0,
    unpaid_invoices_amount REAL DEFAULT 0.0,
    cleaning_fee REAL DEFAULT 0.0,
    damage_fee REAL DEFAULT 0.0,
    other_fees REAL DEFAULT 0.0,
    total_deductions REAL DEFAULT 0.0,
    security_deposit_amount REAL DEFAULT 0.0,
    advance_rent_amount REAL DEFAULT 0.0,
    final_balance REAL DEFAULT 0.0,
    refund_method TEXT,
    refund_receipt_img TEXT,
    status TEXT DEFAULT 'Completed',
    notes TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (lease_id) REFERENCES leases(id)
);

-- 8. Maintenance Requests
CREATE TABLE maintenance_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'Pending',
    image_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

-- 9. Expenses
CREATE TABLE expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT,
    billing_month INTEGER,
    billing_year INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 10. Login Attempts (Security)
CREATE TABLE login_attempts (
    ip_address TEXT PRIMARY KEY,
    attempts INTEGER DEFAULT 0,
    last_attempt TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    locked_until TIMESTAMP
);

-- 11. Booking Requests (Candidate Screening)
CREATE TABLE booking_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    line_user_id TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    workplace_name TEXT NOT NULL,
    job_position TEXT NOT NULL,
    workplace_phone TEXT NOT NULL,
    requested_move_in_date DATETIME NOT NULL,
    preferred_building_id INTEGER REFERENCES buildings(id),
    preferred_room_id INTEGER REFERENCES rooms(id),
    assigned_room_id INTEGER REFERENCES rooms(id),
    needs_bed INTEGER DEFAULT 0,
    needs_mattress INTEGER DEFAULT 0,
    agreement_accepted INTEGER DEFAULT 1,
    agreement_accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'Pending',
    admin_notes TEXT,
    language TEXT DEFAULT 'th',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
