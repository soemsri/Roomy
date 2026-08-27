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

8. **BookingRequests (Room Reservation & Screening)**
   - id: INT (PK)
   - uuid: TEXT (Unique, Index)
   - line_user_id: TEXT (Index)
   - full_name: TEXT
   - phone_number: TEXT
   - workplace_name: TEXT
   - job_position: TEXT
   - workplace_phone: TEXT
   - requested_move_in_date: DATETIME
   - preferred_building_id: INT (FK)
   - preferred_room_id: INT (FK)
   - assigned_room_id: INT (FK)
   - agreement_accepted: INT (1 = Accepted)
   - agreement_accepted_at: TIMESTAMP
   - status: TEXT (Pending, Approved, Rejected, Converted)
   - admin_notes: TEXT
   - language: TEXT
   - created_at: TIMESTAMP
   - updated_at: TIMESTAMP

9. **Parcels (ระบบจัดการพัสดุ)**
   - id: INT (PK, Auto Increment)
   - room_id: INT (FK -> rooms.id, Index)
   - tenant_id: INT (FK -> tenants.id, Nullable, Index)
   - carrier: TEXT (Flash, Kerry, SPX, J&T, ไปรษณีย์ไทย, etc.)
   - tracking_number: TEXT (Nullable)
   - parcel_image_url: TEXT (Nullable - URL รูปถ่ายพัสดุตอนรับเข้า)
   - status: TEXT (Default: 'pending', values: 'pending', 'received', 'cancelled')
   - arrived_at: TIMESTAMP (Default: CURRENT_TIMESTAMP)
   - received_at: TIMESTAMP (Nullable - วันเวลาที่ผู้เช่ามารับ)
   - received_by_name: TEXT (Nullable - ชื่อผู้มารับ Optional)
   - proof_image_url: TEXT (Nullable - รูปถ่ายหลักฐานตอนส่งมอบ Optional)
   - notes: TEXT (Nullable - หมายเหตุเพิ่มเติม)
   - created_by_user_id: INT (FK -> users.id, Nullable - เจ้าหน้าที่ผู้บันทึก)
   - created_at: TIMESTAMP (Default: CURRENT_TIMESTAMP)
   - updated_at: TIMESTAMP (Default: CURRENT_TIMESTAMP)

