# Roomy (SukAnan Apartment) - Master Handover Documentation
**Enterprise Property Management System & Automated LINE OA Bot Ecosystem**

---

## 📑 Executive Summary

**Roomy** (developed for **SukAnan Property**) is an advanced, enterprise-grade Apartment & Rental Management Platform. Designed for modern multi-building operations, Roomy automates end-to-end property management workflows: from tenant screening and digital lease management to smart meter recording, dynamic PromptPay QR billing, maintenance ticketing, simplified parcel reception, and financial analytics.

The system combines a high-performance **FastAPI (Python 3.10+)** backend with **SQLAlchemy ORM** supporting dual database engines (SQLite for development / PostgreSQL with dynamic connection pooling for production), **LINE Bot SDK v3** for dual-channel tenant and administrator communications, and modern glassmorphic web dashboards with tri-lingual internationalization (Thai, English, Japanese).

---

## 🏛️ System Architecture

```mermaid
graph TD
    subgraph Clients ["Client Layer"]
        LINE_Tenant["Tenant LINE App<br/>(Rich Menu & Flex Messages)"]
        LINE_Admin["Staff/Owner LINE App<br/>(Backoffice Rich Menu)"]
        Web_Admin["Admin Web Dashboard<br/>(Glassmorphic Responsive UI)"]
        Web_Tenant["Tenant Web Views<br/>(Bills, Repairs, Parcels, Booking)"]
    end

    subgraph AppServer ["Application Layer (FastAPI Backend)"]
        Router_Auth["Auth & RBAC Middleware<br/>(Google OAuth 2.0 / JWT / Cookies)"]
        Router_Admin["Admin Controller<br/>(/admin/*)"]
        Router_Tenant["Tenant Controller<br/>(/tenant/*, /bill/*, /repair/*)"]
        Router_Webhook["LINE Webhooks<br/>(/callback/admin, /callback/tenant)"]
        Router_API["REST & JSON APIs<br/>(/api/*, /settings/*)"]
        
        subgraph Services ["Background Daemons & Services"]
            Svc_Backup["Scheduled Backup Daemon<br/>(Dependency-order JSON / SQLite)"]
            Svc_LogCleanup["Log Retention Cleaner<br/>(Hourly Background Pruning)"]
            Svc_Billing["Billing & Meter Engine<br/>(PromptPay QR & Late Fees)"]
            Svc_LinePush["LINE Flex Message Builder<br/>(Bills, Parcels, Repairs)"]
        end
    end

    subgraph DataLayer ["Persistence Layer"]
        DB_Hybrid[("Database Engine<br/>(SQLite / PostgreSQL Pool)")]
        Storage_Uploads["File Storage<br/>(Slips, Receipts, Repairs, Parcels)"]
        System_Configs["Encrypted Configs<br/>(AES-256 SystemConfig)"]
    end

    LINE_Tenant --> Router_Webhook
    LINE_Admin --> Router_Webhook
    Web_Admin --> Router_Auth --> Router_Admin
    Web_Tenant --> Router_Tenant
    
    Router_Admin --> DB_Hybrid
    Router_Tenant --> DB_Hybrid
    Router_Webhook --> Svc_LinePush --> LINE_Tenant
    Router_Webhook --> Svc_LinePush --> LINE_Admin
    
    Svc_Backup -.-> DB_Hybrid
    Svc_LogCleanup -.-> DB_Hybrid
    Svc_Billing -.-> DB_Hybrid
```

---

## 👥 Multi-Role Role-Based Access Control (RBAC)

Roomy implements strict role-based access control with route-level decorators and UI permission filters:

| Role | Role Code | Capabilities & Operational Boundaries |
| :--- | :--- | :--- |
| **Super Admin** | `User ID = 1` | Permanent system owner. Unrestricted access to all modules, building configurations, database backup/restores, system configuration, staff management, and audit logs. Protected against deletion, role alteration, or suspension. |
| **Admin** | `Admin` | Full management of buildings, rooms, tenants, contracts, billing, payments, receipts, repairs, parcels, and staff accounts. |
| **Accountant** | `Accountant` | Dedicated financial access: billing cycles, meter calculation sheets, invoice adjustments, cash receipts recording, move-out deposit deductions/refunds, and CSV report exports. |
| **Clerk** | `Clerk` | Front-desk operations: room availability checks, tenant onboarding screening, lease contract generation, resident records, meter reading entry, and parcel logging. |
| **Technician** | `Technician` | Maintenance operations: viewing repair tickets, updating repair status (`Pending` → `In Progress` → `Fixed`), adding maintenance notes, and uploading completion photos. |
| **Housekeeper** | `Housekeeper` | Room turnover operations: room cleaning status inspections and parcel logging assistance. |

### Zero-Password Onboarding (Bootstrap Wizard)
- On a fresh database installation (0 registered users), any navigation to `/admin/*` automatically routes to `/admin/setup/bootstrap`.
- The first administrator authenticates via **Google OAuth 2.0 Sign-In** to claim the **Super Admin** role.
- Once registered, the bootstrap endpoint is locked (`403 Forbidden`).

---

## 📦 Core Operational Modules

### 1. Room & Asset Management
- **Building Hierarchy**: Supports multi-building configurations (e.g. Building A, Building B) with customizable floor plans and room types.
- **Room Status Lifecycle**: `Vacant` (ว่าง), `Reserved` (จอง), `Occupied` (มีผู้เช่า), and `Maintenance` (ปิดปรับปรุง).
- **Asset Tracking**: Register room appliances (Air conditioner, Refrigerator, Water heater, Bed, etc.) with serial numbers and condition notes. Supports **Batch Building-Wide Asset Assignment** (1-click propagate asset lists to all rooms).

### 2. Tenant Lifecycle & Digital Lease Workflow
- **LINE User Mapping**:
  1. Tenant adds LINE OA and texts room number or "สมัคร".
  2. Webhook captures `userId` and displays interactive registration web form.
  3. Tenant submits name, nickname, phone number, ID card copy, and workplace.
  4. Admin reviews submission in **Pending Registrations** tab.
  5. Admin approves and triggers **Initial Payment Bill** (Security Deposit + Advance Rent).
  6. Upon payment verification, system automatically binds `userId` to the room and switches room status to `Occupied`.
- **Multiple Residents per Room**: Rooms can have multiple co-tenants while maintaining a primary billing contact.
- **HTML Lease Template Engine**: Dynamic WYSIWYG lease agreement editor (CKEditor 5) with placeholder tags (`{tenant_name}`, `{room_number}`, `{rent_amount}`, `{deposit}`, `{start_date}`). Creates permanent HTML snapshots upon approval.

### 3. Smart Metering & Billing Engine
- **Flexible Recording**: Supports both single-room meter logging and bulk-building meter entry sheets.
- **Automated Invoice Calculation**:
  $$\text{Total Bill} = \text{Room Rent} + (\Delta \text{Water Units} \times \text{Water Rate}) + (\Delta \text{Elec Units} \times \text{Elec Rate}) + \text{Recurring Fees} + \text{One-Time Charges} + \text{Late Fees}$$
- **Dynamic PromptPay QR Generator**: Invoices generate EMVCo-compliant PromptPay QR codes matching exact bill totals for instant mobile banking transfers.
- **Cash Payments**: For office walk-ins, administrators record cash receipt and upload signed physical receipt photo.

### 4. Simplified Parcel Management (No QR / PIN Friction)
- **Inward Registration**: Front desk staff selects room number, courier (Kerry, Flash, SPX, J&T, Thai Post), tracking number, and takes a box photo.
- **Instant LINE Flex Push**: System immediately delivers an interactive Flex Message to the tenant with parcel photo and courier details.
- **1-Click Pickup**: When the tenant arrives at the counter, staff searches by room number and clicks **"รับพัสดุแล้ว / Mark as Received"**.
- **Optional Proof**: Staff can optionally upload recipient photo or notes without impeding fast 1-click handovers.

### 5. Maintenance & Repair Ticketing
- **Tenant Submission**: Accessible directly via LINE Rich Menu / Web Form with category selection, issue description, and photo upload.
- **Real-Time Notification**: Immediate LINE alert pushed to the Administrator and Technician channels.
- **Status Workflow**: `Pending` → `In Progress` → `Completed`. Automatically alerts tenant via LINE upon completion.

### 6. Financial Analytics & Reporting
- **Interactive Bento Grid Dashboard**: Real-time revenue cards, occupancy gauge, and expense trackers.
- **Chart Visualizations (Chart.js)**:
  - Monthly Revenue vs. Expense Stacked Bar Charts with Net Profit Trendline.
  - Occupancy Rate Donut & 12-Month Historical Trendlines.
  - Expense Category Breakdown Pie / Doughnut Charts.
  - Accounts Receivable Aging Buckets (1-15 days, 16-30 days, 30+ days) and Top Debtor rankings.
- **CSV Data Export**: UTF-8 with BOM encoding compatible with Microsoft Excel and Google Sheets.

---

## 🗄️ Database Architecture & Migrations

The platform implements dynamic dialect routing supporting **SQLite** (for local/embedded instances) and **PostgreSQL** (for high-concurrency production deployments):

```python
# Dynamic Database Driver Detection
if DATABASE_URL.startswith("postgresql"):
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600
    )
else:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
```

### Core Database Entities (18 Tables)
1. `users` - Admin and staff credentials, roles, Google OAuth IDs, and status.
2. `buildings` - Property buildings and physical metadata.
3. `rooms` - Room numbers, floor, building ID, base rent, and status.
4. `room_assets` - Furniture, appliances, and fixtures per room.
5. `tenants` - Tenant records, contact info, LINE `userId`, and onboarding status.
6. `co_residents` - Secondary residents and room occupants.
7. `leases` - Lease agreements, dates, terms, and signed HTML snapshots.
8. `invoices` - Monthly billing records, rent, utility amounts, and payment statuses.
9. `invoice_items` - Granular invoice itemizations (recurring & one-time fees).
10. `payments` - Payment transaction records, PromptPay slips, and cash confirmations.
11. `receipts` - Official payment receipts with sequential running numbers.
12. `meters` - Historical water and electricity meter readings and photos.
13. `repairs` - Maintenance requests, photos, technician assignments, and status.
14. `parcels` - Incoming deliveries, tracking numbers, box photos, and handover timestamps.
15. `expenses` - Operating costs, maintenance purchases, and utility bills paid to authorities.
16. `system_configs` - AES-256 encrypted application settings, fees, and rates.
17. `lease_templates` - Customizable HTML templates for rental contracts.
18. `application_logs` - System audit logs, security events, and administrative actions.

---

## 🔄 Disaster Recovery & Background Services

### 1. Automated Scheduled Backup Service (`services/backup.py`)
- Background daemon thread runs continuously with configurable schedules (e.g. daily at 02:00 AM).
- **SQLite Engine**: Generates atomic file snapshot backups in `/backups/`.
- **PostgreSQL Engine**: Exports full JSON data structures with explicit **foreign-key dependency ordering** to guarantee safe restoration without referential integrity errors.
- **Retention Pruning**: Automatically retains the latest N backups (default: 10 backups) to prevent disk exhaustion.

### 2. Log Retention & Pruning Service (`services/log_cleanup.py`)
- Automatically cleans old `ApplicationLog` entries once per hour based on the configured policy:
  - `1_day`, `1_week`, `1_month`, `1_year`, or `forever`.
- Accessible via Admin Settings UI (`Backup & System` tab) and secured endpoints (`GET /settings/log-policy`, `POST /settings/log-policy/save`).

---

## 📱 LINE Official Account Ecosystem & Rich Menus

### Dual-Bot Architecture
1. **Tenant Bot (`/callback/tenant`)**:
   - 6-Action Per-User Rich Menu:
     - 💳 **ดูบิลค่าเช่า** (`/tenant/bill/latest`)
     - 🛠️ **แจ้งซ่อม** (`/tenant/repair`)
     - 📦 **พัสดุของฉัน** (`/tenant/parcels`)
     - 📜 **ประวัติการจ่าย** (`/tenant/history`)
     - 🚪 **แจ้งย้ายออก** (`/tenant/move-out`)
     - 💬 **ติดต่อเจ้าหน้าที่** (Direct chat bridge)
2. **Admin Bot (`/callback/admin`)**:
   - Per-User Admin Rich Menu deployed specifically to authorized administrator LINE IDs for quick meter reading, parcel receiving, and repair ticket management.

---

## 🚀 Operations & Deployment Runbook

### Environment Variables (`.env`)

```env
# Application Settings
APP_NAME=Roomy
PORT=8000
SECRET_KEY=your_super_secret_jwt_encryption_key_here

# Database Configuration
# SQLite (Local Development):
DATABASE_URL=sqlite:///src/roomy.db
# PostgreSQL (Production):
# DATABASE_URL=postgresql+psycopg2://roomy_user:password@localhost:5432/roomy_db

# Google Identity Services OAuth 2.0
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com

# LINE Official Account Credentials
LINE_CHANNEL_ACCESS_TOKEN=your_line_channel_access_token
LINE_CHANNEL_SECRET=your_line_channel_secret

# Encryption Key (AES-256 for SystemConfig)
CONFIG_ENCRYPTION_KEY=your-32-byte-base64-encoded-encryption-key
```

### Production Deployment via Systemd / Gunicorn + Uvicorn

```bash
# 1. Clone & setup environment
git clone git@github.com:soemsri/Roomy.git /var/www/roomy
cd /var/www/roomy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Run Database Migrations
python src/migrate_db.py

# 3. Launch with Gunicorn (Uvicorn Workers)
gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

### Testing & Code Quality Assurance

```bash
# Run complete test suite (Unit & Integration tests)
pytest -v

# Run linting and formatting check
ruff check .
```

---

## 🏆 Project Completion Sign-Off

| Phase | Description | Status | Verification |
| :--- | :--- | :--- | :--- |
| **Phase 1** | Foundation, Architecture & DB Schema Design | **Completed** | Full schema definition & ERD documented |
| **Phase 2** | Room, Asset & Multi-Tenant Management | **Completed** | CRUD, Batch asset assignment, Onboarding workflow |
| **Phase 3** | Smart Metering, Invoicing & PromptPay QR | **Completed** | Dynamic QR generation, pro-rated calculations |
| **Phase 4** | Maintenance System & LINE Push Notifications | **Completed** | Photo uploads, technician tracker, LINE Flex |
| **Phase 5** | Dashboard, Bento Analytics & CSV Export | **Completed** | Stacked bar, Donut, Aging charts, Excel BOM CSV |
| **Phase 6** | System Validation, Migration Suite & Final Polish | **Completed** | 68 automated tests green, responsive drawer UI, Master Handover documentation |
| **Phase 7** | Simplified Parcel Management Flow | **Completed** | 1-click pickup, box photo, LINE Flex alert |

*Maintained and delivered by the SukAnan Property Engineering Team.*
