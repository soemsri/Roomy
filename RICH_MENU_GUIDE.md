# Rich Menu Design Specifications

## 1. Tenant Rich Menu (Standard User)
Layout: 2x3 Grid (2500 x 1686 pixels)

| Icon 1: ดูค่าเช่า (View Bill) | Icon 2: จ่ายเงิน (Pay QR/Cash) | Icon 3: ประวัติการจ่าย (History) |
|-----------------------------|-------------------------------|----------------------------------|
| Icon 4: แจ้งซ่อม (Repair)     | Icon 5: มิเตอร์น้ำไฟ (Meters)   | Icon 6: ประกาศ (Announcements)   |

**Actions:**
- **View Bill**: Link to `https://your-domain.com/bill?userId={userId}`
- **Pay**: Link to `https://your-domain.com/pay?userId={userId}`
- **History**: Postback `action=view_history`
- **Repair**: Link to `https://your-domain.com/repair?userId={userId}`
- **Meters**: Postback `action=view_meters`
- **Announcements**: Postback `action=view_news`

---

## 2. Owner Rich Menu (Admin Only)
Layout: 2x3 Grid (2500 x 1686 pixels)

| Icon 1: ผังห้อง (Room Map) | Icon 2: จดมิเตอร์ (Read Meters) | Icon 3: สรุปรายรับ (Income) |
|---------------------------|---------------------------------|-----------------------------|
| Icon 4: จัดการสัญญา (Lease) | Icon 5: ตั้งค่า (Settings/QR)    | Icon 6: แจ้งซ่อม (Admin Repair) |

**Actions:**
- **Room Map**: Link to `https://your-domain.com/admin/rooms`
- **Read Meters**: Link to `https://your-domain.com/admin/meters`
- **Income**: Link to `https://your-domain.com/admin/reports`
- **Lease**: Link to `https://your-domain.com/admin/tenants`
- **Settings**: Postback `action=admin_settings`
- **Admin Repair**: Postback `action=admin_repair_list`
