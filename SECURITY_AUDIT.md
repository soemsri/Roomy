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
