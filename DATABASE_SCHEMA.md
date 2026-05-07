# CTO Report: Database Schema (SQLite)

## Entities

1. **Owners (Admins)**
   - id: INT (PK)
   - line_user_id: TEXT (Unique)
   - name: TEXT
   - promptpay_accounts: TEXT (JSON)

2. **Rooms**
   - id: INT (PK)
   - room_number: TEXT (Unique)
   - floor: INT
   - status: TEXT (Vacant, Occupied, Maintenance)
   - base_rent: FLOAT
   - electricity_rate: FLOAT
   - water_rate: FLOAT

3. **Tenants**
   - id: INT (PK)
   - line_user_id: TEXT (Unique)
   - name: TEXT
   - phone: TEXT
   - current_room_id: INT (FK)

4. **Leases (Contracts)**
   - id: INT (PK)
   - room_id: INT (FK)
   - tenant_id: INT (FK)
   - start_date: DATE
   - end_date: DATE
   - status: TEXT (Active, Closed)

5. **Meters**
   - id: INT (PK)
   - room_id: INT (FK)
   - month: INT
   - year: INT
   - electricity_reading: FLOAT
   - water_reading: FLOAT
   - recorded_at: TIMESTAMP

6. **Invoices (Bills)**
   - id: INT (PK)
   - room_id: INT (FK)
   - tenant_id: INT (FK)
   - month: INT
   - year: INT
   - rent_amount: FLOAT
   - electricity_amount: FLOAT
   - water_amount: FLOAT
   - other_fees: FLOAT
   - total_amount: FLOAT
   - status: TEXT (Unpaid, Paid, Overdue)
   - payment_method: TEXT (Cash, QR)
   - payment_receipt_img: TEXT (Path to signature/receipt photo)

7. **MaintenanceRequests**
   - id: INT (PK)
   - tenant_id: INT (FK)
   - room_id: INT (FK)
   - issue_title: TEXT
   - issue_detail: TEXT
   - image_url: TEXT
   - status: TEXT (Pending, In Progress, Completed)
   - created_at: TIMESTAMP
