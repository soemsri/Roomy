-- SukAnan Apartment Database Schema (SQLite)

-- 1. Owners/Admins
CREATE TABLE owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_user_id TEXT UNIQUE NOT NULL,
    display_name TEXT,
    promptpay_config TEXT DEFAULT '[]', -- JSON string of PromptPay accounts
    qr_payment_enabled INTEGER DEFAULT 1, -- 0: Disabled, 1: Enabled
    late_fee_enabled INTEGER DEFAULT 0,
    due_day INTEGER DEFAULT 5,
    late_fee_per_day REAL DEFAULT 50.0
);

-- 2. Rooms
CREATE TABLE rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT UNIQUE NOT NULL,
    floor INTEGER,
    status TEXT DEFAULT 'Vacant', -- Vacant, Occupied, Maintenance
    base_rent REAL DEFAULT 0.0,
    electricity_rate REAL DEFAULT 0.0,
    water_rate REAL DEFAULT 0.0
);

-- 3. Tenants
CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    line_user_id TEXT UNIQUE NOT NULL,
    full_name TEXT,
    phone_number TEXT,
    current_room_id INTEGER,
    status TEXT DEFAULT 'Pending', -- Pending, Active, Rejected
    FOREIGN KEY (current_room_id) REFERENCES rooms(id)
);

-- 3.1 Residents (Individuals in a room)
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

-- 3.2 Tenant History (Archive)
CREATE TABLE tenant_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT,
    tenant_uuid TEXT,
    first_name TEXT,
    last_name TEXT,
    nickname TEXT,
    phone_number TEXT,
    workplace TEXT,
    start_date TIMESTAMP,
    end_date TIMESTAMP
);

-- 4. Leases (Contracts)
CREATE TABLE leases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    status TEXT DEFAULT 'Active', -- Active, Closed
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
    tenant_id INTEGER NOT NULL,
    billing_month INTEGER NOT NULL,
    billing_year INTEGER NOT NULL,
    rent_amount REAL NOT NULL,
    electricity_amount REAL NOT NULL,
    water_amount REAL NOT NULL,
    late_fee REAL DEFAULT 0.0,
    total_amount REAL NOT NULL,
    status TEXT DEFAULT 'Unpaid', -- Unpaid, Paid, Overdue
    payment_method TEXT, -- Cash, QR
    payment_receipt_img TEXT, -- File path
    paid_at TIMESTAMP,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

-- 7. Maintenance Requests
CREATE TABLE maintenance_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id INTEGER NOT NULL,
    room_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    image_url TEXT,
    status TEXT DEFAULT 'Pending', -- Pending, In Progress, Fixed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id),
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);

-- 8. Room Assets
CREATE TABLE room_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    quantity INTEGER DEFAULT 1,
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);
