# Roomy - Advanced Apartment & Staff Role Management System

Roomy is an enterprise-grade, comprehensive **Apartment, Property, and Staff Management System** built with **FastAPI** (Python). It features a secure **Multi-Role Role-Based Access Control (RBAC)** authorization scheme, seamless **Google OAuth 2.0 Identity Sign-In**, and dual **LINE Official Account (LINE OA) Bot integrations** for real-time tenant communications, automated meter recordings, maintenance workflows, and dynamic scan-to-pay PromptPay invoicing.

---

## 🌟 Key Features

### 1. Enterprise Multi-Role Staff Authorization (RBAC)
Exposes tailored interfaces and route guards for different organization roles, keeping operational boundaries secure:
*   **Super Admin (User ID = 1)**: Ultimate system owner. Permanent, absolute system administration privileges. Protected from self-deletion, role modification, or suspension in both backend and UI.
*   **Admin**: Full property control, building configuration, system settings adjustments, and junior staff registration/management.
*   **Accountant**: Accesses billing routes, issue invoices, records manual cash/payment receipts, processes tenant move-out refunds/deductions, and downloads CSV financial reports.
*   **Clerk**: Manages building rooms, tenant registrations, resident backups lists, lease agreements, and logs active water/electricity meters.
*   **Technician**: Dedicated access to maintenance requests logs, updates repair status, and records utility fixes costs.
*   **Housekeeper**: Manages room occupancy status, cleanliness, and inspection records.

### 2. Google OAuth 2.0 & Web Setup Bootstrap Wizard
*   **Zero-Password Onboarding**: Secured with Google Sign-In, eliminating password storage vulnerability.
*   **Web Setup Wizard**: If the database is fresh (zero registered users), any attempt to access `/admin/*` redirects to `/admin/setup/bootstrap`. The first Google account to sign in dynamically claims the **Super Admin** role. Subsequent access to the setup wizard is strictly forbidden (`403 Forbidden`).
*   **Secure JWT Verification**: Verify Google ID tokens directly against Google's secure public key signature endpoints (`https://oauth2.googleapis.com/tokeninfo`).

### 3. Smart Metering & Billing System
*   **Water & Electricity Meters**: Logs active meters via dedicated single-room or bulk-building sheets.
*   **Auto-Invoice Invoicing**: Auto-calculates utility usage, pro-rated rent figures (for mid-term check-in/outs), late fees penalties, and dynamic recurring charges.
*   **PromptPay Dynamic QR Generator**: Embeds EMVCo-compliant QR codes showing the exact invoice billing total on the printed receipt for rapid scan-to-pay.
*   **Dynamic Print Receipts**: Automatically fetches the property owner's business address, hiding placeholder taglines, and supports multi-line white-space formatting.

### 4. Fully Localized Internationalization (TH / EN / JP)
*   Supports dynamic switching between **Thai (TH)**, **English (EN)**, and **Japanese (JP)** across all interfaces based on the client's language cookie.
*   Dynamically localizes staff management sections, dropdown select choices, and printed receipts (including `Payer Signature` and `Receiver Signature` labels).

### 5. LINE OA Webhooks & Communication
*   Exposes secure dual webhooks `/callback/admin` and `/callback/tenant` mapping to Admin and Tenant bots.
*   Pushes automatic Flex Messages containing receipt summaries, meter readings, and repair status progress updates directly to the tenant's LINE chat.

---

## 🛠️ Technology Stack
*   **Backend**: FastAPI (Python 3.10+)
*   **Database & ORM**: SQLite & SQLAlchemy (with pre-built automated migrations)
*   **Security & Encryption**: Google Identity Services OAuth 2.0 (JWT), Bcrypt, Cryptography (AES-256 for system configurations values)
*   **Frontend**: HTML5, Vanilla CSS3 (modern glassmorphism cards, glowing gradients, responsive layout transitions), ES6+ JavaScript (Fetch API)
*   **Linting**: Ruff (for Python code styling compliance)
*   **Testing Suite**: Pytest (covering 29 unit and integration workflows)

---

## 🚀 Developer Setup Instructions

Follow this step-by-step walkthrough to configure your local developer environment.

### 1. Prerequisites
Ensure you have the following installed on your system:
*   [Python 3.10 or higher](https://www.python.org/downloads/)
*   A Google Cloud Developer Account (to obtain a **Google Client ID** for sign-in)
*   A LINE Developer account (if testing LINE bots; optional for core system dev)

### 2. Clone the Repository & Configure Directory
Open your terminal (PowerShell, Bash, or Command Prompt) and set up the repository:
```bash
git clone <repository_url>
cd Roomy
```

### 3. Create a Virtual Environment & Install Dependencies
It is highly recommended to isolate project dependencies inside a Python virtual environment:
```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Windows (CMD):
.\venv\Scripts\activate.bat
# On macOS/Linux:
source venv/bin/activate

# Install the required packages
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a file named `.env` in the root of the project directory and supply the following variables:
```env
# Database Settings
DATABASE_URL=sqlite:///src/suk_anan.db

# Google OAuth Credentials (Mandatory for Admin Sign-In)
GOOGLE_CLIENT_ID=your-google-client-id-here.apps.googleusercontent.com

# LINE OA Credentials (Optional for local testing, mandatory for LINE features)
ADMIN_CHANNEL_ACCESS_TOKEN=your-admin-channel-access-token
ADMIN_CHANNEL_SECRET=your-admin-channel-secret
TENANT_CHANNEL_ACCESS_TOKEN=your-tenant-channel-access-token
TENANT_CHANNEL_SECRET=your-tenant-channel-secret

# AES Encryption Key (Required to encrypt configurations, must be a 32-byte urlsafe base64-encoded key)
# You can generate one in Python using: cryptography.fernet.Fernet.generate_key().decode()
SECRET_KEY=generate-your-key-here-with-fernet
```

### 5. Run Database Migrations
Roomy uses an automated database migration script to generate all database structures, configure constraints, and seed properties schemas safely:
```bash
python src/migrate_db.py
```
*   *Note: If you want to start fresh or re-test the onboarding setup bootstrap wizard, simply delete the generated `src/suk_anan.db` file and run `python src/migrate_db.py` again.*

### 6. Run the Application Local Server
Boot the web server locally using Uvicorn:
```bash
python src/main.py
```
The server will start running on **`http://localhost:8000`** (or `http://127.0.0.1:8000`).

---

## 🧪 Verification & Testing

### 1. Execute the Automated Test Suite (`pytest`)
Confirm all integration, unit, and authentication test suites pass successfully:
```bash
pytest
```
*All 29 tests should execute and show green passed status.*

### 2. Run the Linter & Formatting Checks (`ruff`)
Ensure your code changes comply with standard Python formatting rules:
```bash
ruff check .
```

---

## 📞 Support & License
*   **License**: MIT License
*   **Developer Contact**: [rangsarn@gmail.com](mailto:rangsarn@gmail.com)
*   **Donations**: `0xcCAe4BDA3F9A92dd14D4193680535128f7DEE842`
