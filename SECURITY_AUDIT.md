# Roomy - SQL Injection & Input Validation Security Audit

This document summarizes the findings of a comprehensive security review performed on the Roomy Apartment Management System codebase. The audit was conducted to verify if inputs are properly sanitized and database queries are adequately protected against **SQL Injection (SQLi)** vulnerabilities.

---

## 📋 Executive Summary

The Roomy application has been evaluated as **highly secure and resilient against SQL Injection out-of-the-box**. 
- **100% of user-facing database queries** are performed using the **SQLAlchemy ORM**, which naturally generates parameterized queries.
- **100% of database actions in utility/migration scripts** utilize explicit parameterized bindings in standard Python `sqlite3`.
- No raw SQL string formatting, interpolation, or direct concatenation of user inputs was found in the database layer.

---

## 🔎 Scope & Detailed Findings

### 1. Web Application Layer (SQLAlchemy ORM)
All database interactions within the core API service ([src/main.py](file:///d:/Gemini/Project/Roomy/src/main.py) and [src/billing.py](file:///d:/Gemini/Project/Roomy/src/billing.py)) utilize the **SQLAlchemy ORM** engine.

- **Vulnerability Check**: We audited the codebase for patterns like `db.execute(text(...))` or raw SQL command constructions. None were found.
- **Verification**: Queries are structured exclusively using SQLAlchemy ORM constructs, e.g.:
  ```python
  tenant = db.query(models.Tenant).filter(models.Tenant.uuid == tenant_uuid).first()
  ```
- **Why it is secure**: In SQLAlchemy, operators like `==` translate expressions into parameterized parameters (`?` or `:param`) at compile-time. The database driver receives the SQL template and the values as completely separate entities. The values are never parsed as SQL code, eliminating SQL Injection entirely.

### 2. Utility & Migration Scripts Layer (Python `sqlite3`)
Local utilities and maintenance scripts (e.g., database schema alignment and UUID corrections) use standard Python `sqlite3` connections directly.

- **Vulnerability Check**: Audited direct query execution cursors to check for string formatting (`f"..."` or `+`).
- **Verification**: Parameterized SQL placeholder bindings (`?`) are rigorously used for inserting variable parameters, e.g.:
  ```python
  # Safe parameter binding in fix_uuids.py:
  cursor.execute("UPDATE tenants SET uuid = ? WHERE id = ?", (new_uuid, tenant_id))
  
  # Safe parameter binding in migrate_db.py:
  curr.execute("INSERT INTO buildings (name, description) VALUES (?, ?)", ("อาคารหลัก", "อาคารหลักของหอพัก"))
  ```
- **Why it is secure**: Variable inputs are bound through the SQLite engine's native parameter placeholder interface rather than parsed textually inside the query string.

### 3. FastAPI Input Validation
FastAPI endpoints leverage Pydantic and type declarations for all request routing.

- **Why it is secure**: Inputs are parsed and strictly typed (e.g., integers, floats, UUIDs, or email schemas) before being processed. Any attempt to pass unexpected payloads (like SQL syntax tokens) into numeric fields results in an automatic `422 Unprocessable Entity` response, acting as an upfront firewall.

---

## 🛡️ Mandatory Coding Guidelines for Developers

To maintain this standard of security as the Roomy project grows, future developers must strictly adhere to the following rules:

1. **Avoid Raw SQL Execution**:
   Always prefer the SQLAlchemy ORM or Core Query Builder over raw SQL strings.
   
2. **Never Interpolate Query Strings**:
   * **INCORRECT (VULNERABLE)**:
     ```python
     # NEVER DO THIS - VULNERABLE TO SQL INJECTION:
     db.execute(f"SELECT * FROM rooms WHERE room_number = '{room_number}'")
     ```
   * **CORRECT (SECURE)**:
     ```python
     # ALWAYS USE PARAMETERS:
     db.query(models.Room).filter(models.Room.room_number == room_number).first()
     ```

3. **Always Parameterize Direct SQLite Calls**:
   If direct SQLite utility scripts are written, never use string concatenation.
   * **INCORRECT (VULNERABLE)**:
     ```python
     cursor.execute(f"SELECT * FROM rooms WHERE status = '{status}'")
     ```
   * **CORRECT (SECURE)**:
     ```python
     cursor.execute("SELECT * FROM rooms WHERE status = ?", (status,))
     ```

---

## 🔑 3. Authentication, Authorization & Role-Based Access Control (RBAC)

The Roomy system implements a formal, highly secure **Dual-Role Access Control Model** divided into **Session-Based Administration (RBAC)** and **Capability-Based Tenant Space (CBAC)**. This completely isolates the administrative and resident layers.

### 1. Admin Role (Role-Based Access Control - RBAC)
* **Access Level**: Full read/write access to dashboards, invoices, expenses, tenant lists, repair logs, and database settings.
* **Mechanism**: Protected via FastAPI Dependency Injection using the custom `Depends(get_admin)` validator.
* **Token Validation**:
  - The admin's browser is issued a secure, cryptographically random 256-bit hexadecimal `session_token` upon successful login.
  - The token is checked on every administrative request against the database session store.
* **Cookie Protections**:
  - `httponly=True`: Prevents client-side scripts (XSS) from accessing or stealing the cookie.
  - `secure=True`: Enforces transmission strictly over HTTPS, neutralizing man-in-the-middle sniffing.
  - `samesite="lax"`: Mitigates Cross-Site Request Forgery (CSRF) attack vectors.
* **Brute-Force Bracing**:
  - Monitored by the `login_attempts` table.
  - **Lockout Rule**: Accumulating 3 consecutive failed login attempts locks the originating IP address completely out of the login endpoint for **30 minutes**, preventing dictionary and brute-force cracking.

### 2. Tenant Role (Capability-Based Access Control - CBAC)
To prevent residents from having to manage complex password databases on LINE, Roomy employs an elegant, highly secure **Capability-Based Access Model**.
* **Access Level**: Sandboxed read/write access strictly to the tenant's own bills, registration status, repair form submissions, and receipt history.
* **Mechanism**: Validated using unguessable, high-entropy **UUIDv4 tokens** generated natively upon tenant initialization.
* **Privilege Separation**:
  - Endpoints (e.g., `/bill/{invoice_uuid}`, `/repair/{tenant_uuid}`) check for exact resource matches in the database.
  - Because UUIDv4 is mathematically unguessable ($2^{128}$ combinations), it is computationally impossible for **Tenant A** to guess, access, or manipulate **Tenant B**'s data (completely mitigating horizontal privilege escalation).

### 3. Webhook Callbacks (Signature Authentication)
* **Mechanism**: The callback routing controllers (`/callback/admin` and `/callback/tenant`) verify the official LINE signature `X-Line-Signature` sent by LINE's servers.
* **Verification**: Checks the request payload against the channel's secret credentials using the official `linebot.v3.webhooks.WebhookHandler`. Unsigned or fake webhooks are automatically rejected with a `400 Bad Request` code, preventing endpoint spoofing.

---

## 🛡️ Mandatory Coding Guidelines for Developers
