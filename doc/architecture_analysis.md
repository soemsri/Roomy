# Roomy - Architectural Analysis & Connection Mapping

This document provides a comprehensive breakdown of the architectural layers, internal module boundaries, and external integration connection points (APIs, webhooks, and third-party systems) exposed by the Roomy application.

---

## 🏗️ Architectural Overview

Roomy is designed as a **Modular Layered Web Architecture** built on top of **FastAPI** (Python). To ensure scalability, testability, and clean separation of concerns, the codebase has been split from a monolithic format into structured service packages.

```mermaid
graph TD
    %% Architecture Layers
    subgraph Client ["Client Layer (Frontend)"]
        A1["Admin Dashboard (HTML5, JS, CSS)"]
        A2["Tenant Web App (HTML5, JS, CSS)"]
        A3["LINE Messaging Client"]
    end

    subgraph Entry ["App Entry Point"]
        B["src/main.py (Bootstrapper & Router registration)"]
    end

    subgraph Controllers ["Controller / Routing Layer"]
        C1["src/controllers/admin.py (Admin Panels & REST APIs)"]
        C2["src/controllers/tenant.py (Tenant Portal Views)"]
        C3["src/controllers/callback.py (LINE Webhook Callback Controllers)"]
    end

    subgraph Services ["Service Layer (Business Logic)"]
        D1["src/services/billing.py (Invoice Gen, Late Fees & Calculations)"]
        D2["src/services/promptpay.py (PromptPay QR Generator)"]
        D3["src/services/security.py (Google JWT Verify, Hashing, AES-256 Enc)"]
    end

    subgraph Models ["Data Access Layer"]
        E1["src/models/database.py (SQLAlchemy DB Engine & Dependency)"]
        E2["src/models/__init__.py (SQLAlchemy Declarative Schemas)"]
    end

    subgraph DB ["Persistence Storage"]
        F["SQLite DB (src/suk_anan.db)"]
    end

    %% Internal Data Flow Links
    Client -->|HTTP / WebSocket| Entry
    Entry -->|Mounts & Routes| Controllers
    Controllers -->|Consumes Business Services| Services
    Controllers -->|Direct DB Queries| Models
    Services -->|Operates on Data Entities| Models
    Models -->|SQLite Driver| DB
```

---

## 🔗 External Connections & Exposed Interfaces

Roomy communicates with several third-party platforms to orchestrate secure sign-ins, automatic payment configurations, and instant messaging push notifications.

```mermaid
graph LR
    subgraph Exposed ["Roomy Exposed Web Server (:8000)"]
        H1["Web Admin Panel (/admin)"]
        H2["Web Tenant Portal (/tenant)"]
        H3["LINE Webhook Admin (/callback/admin)"]
        H4["LINE Webhook Tenant (/callback/tenant)"]
    end

    subgraph External ["External Third-Party Platforms"]
        G1["Google Identity Services (OAuth 2.0 Token Verification)"]
        G2["LINE Messaging API Platform (Webhooks & Messaging Gateways)"]
        G3["PromptPay EMV Co System (Dynamic QR Codes)"]
    end

    %% Connections
    H1 <==>|1. Sign in with Google JWT| G1
    H3 <--- |2. Webhook Event Push| G2
    H4 <--- |2. Webhook Event Push| G2
    H2 --->|3. Push Msg API Send| G2
    H1 --->|3. Push Msg API Send| G2
    H2 --->|4. QR Code Rendering| G3
```

### 1. Google OAuth 2.0 Integration
* **Purpose**: Provides secure, zero-password Google Sign-in authentication for administrators and staff members.
* **Flow**:
  1. The client signs in via Google Identity Services button on the browser, producing a secure **ID Token (JWT)**.
  2. The JWT is transmitted to `/admin/setup/bootstrap` or `/admin/login`.
  3. The `src/services/security.py` verifies the token cryptographically against the Google signature keys endpoint: `https://oauth2.googleapis.com/tokeninfo`.
  4. If validated, the database matches the Google account email to register or authenticate the staff member session.

### 2. LINE Messaging Webhooks & Messaging API
* **Purpose**: Exposes endpoints for bidirectional chat, automated notifications, and alert broadcasts.
* **Exposed Callback Interfaces**:
  * `/callback/admin`: Connected to the **Admin LINE Bot**. Handles incoming messages from properties administrators.
  * `/callback/tenant`: Connected to the **Tenant LINE Bot**. Handles incoming tenant queries, maintenance uploads, and registration requests.
* **Messaging Gateway (Outbound)**:
  * Roomy makes outbound HTTP POST requests to the LINE API gateways (`https://api.line.me/v2/bot/message/push` / `broadcast`) to send real-time alerts when billing invoices are issued, maintenance tasks are updated, or verification is completed.

### 3. PromptPay EMV-Co QR Engine
* **Purpose**: Dynamic generation of PromptPay-compliant payment QR codes on invoices for instant scan-to-pay.
* **Implementation**: Uses `src/services/promptpay.py` to compile payment amounts and target national IDs or phone numbers into standard string formats and output clean, zero-dependency payment QR code blocks in the browser.

---

## 📂 Project Directory Structure

```text
d:\Gemini\Project\Roomy\
├── src/
│   ├── controllers/             # HTTP Route handlers and API Routers
│   │   ├── admin.py             # Admin views, Staff CRUD, and settings endpoints
│   │   ├── callback.py          # LINE incoming webhook callback handlers
│   │   └── tenant.py            # Tenant billing, check-in, and repairs views
│   │
│   ├── models/                  # Database schema definitions & persistence layer
│   │   ├── database.py          # DB Engine startup, session generator, dependency
│   │   └── __init__.py          # SQLAlchemy Models (Owner, User, Tenant, Room, Lease, etc.)
│   │
│   ├── services/                # Standalone business logic modules
│   │   ├── billing.py           # Late fees, bill calculator, initial invoice gen
│   │   ├── promptpay.py         # PromptPay EMVCo payment QR generators
│   │   └── security.py          # Google JWT verifier, bcrypt hash, AES-256 encrypter
│   │
│   ├── templates/               # Jinja2 HTML layout components
│   │   ├── dashboard.html       # Super Admin/Staff central application control panel
│   │   ├── bootstrap.html       # Setup wizard and Super Admin claim setup
│   │   ├── receipt_print.html   # Dynamically localized printed invoices & receipts
│   │   └── tenant_portal.html   # Responsive mobile-first tenant dashboard
│   │
│   ├── i18n/                    # Localized Translation JSON records
│   │   ├── th.json              # Thai language translation mappings
│   │   ├── en.json              # English language translation mappings
│   │   └── jp.json              # Japanese language translation mappings
│   │
│   ├── config.py                # Central FastAPI initialization & LINE bot credentials
│   └── main.py                  # Lean application bootstrap and server configuration
│
└── tests/                       # Complete automated unit and integration suite
```

---

## ⚡ Core Business Workflows

### A. Staff Role Verification (RBAC) Flow
```mermaid
sequenceDiagram
    autonumber
    actor Admin as Staff User (e.g., Accountant)
    participant Route as src/controllers/admin.py
    participant Guard as RoleChecker(allowed_roles)
    participant DB as SQLite Database

    Admin->>Route: Request GET /admin/report (financials)
    Route->>Guard: Intercept request & verify credentials
    Guard->>Guard: Extract active User record from JWT Cookie
    alt User has role "Accountant" or "Admin"
        Guard->>Route: Access Granted
        Route->>DB: Query financial invoice history
        DB-->>Route: Return raw records
        Route-->>Admin: Render localized report template with financials
    else User has other roles (e.g. Clerk / Housekeeper)
        Guard-->>Admin: 403 Forbidden (Access Denied)
    end
```

### B. Bill Calculation & Invoice Issuance Flow
```mermaid
sequenceDiagram
    autonumber
    actor Staff as Property Administrator
    participant Controller as src/controllers/admin.py
    participant BillService as src/services/billing.py
    participant DB as SQLite Database
    participant LineService as LINE Bot Outbound API

    Staff->>Controller: Click "Issue Invoice" for Room 101
    Controller->>DB: Query Room 101 lease rules & active meter readings
    DB-->>Controller: Return rates, tenant ID & meters data
    Controller->>BillService: calculate_bill(base_rent, water_rate, elec_rate, etc.)
    BillService-->>Controller: Return total bill amount & pro-rated figures
    Controller->>DB: Write new Invoice entry (status: "Unpaid")
    DB-->>Controller: Record Committed
    Controller->>LineService: Dispatch instant billing alert to Tenant's LINE ID
    LineService-->>Staff: Invoice issued successfully
```
