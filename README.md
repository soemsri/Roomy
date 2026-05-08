# Roomy - Apartment Management System

ระบบบริหารจัดการหอพักและอพาร์ทเม้นท์แบบครบวงจร เชื่อมต่อกับ LINE OA เพื่อความสะดวกของทั้งเจ้าของและผู้เช่า

## 🌟 ฟีเจอร์หลัก (Key Features)

### 1. ระบบจัดการอาคารและห้องพัก (Building & Room Management)
- **Multi-building Support:** รองรับการจัดการหลายอาคารในระบบเดียว
- **Room Configuration:** กำหนดค่าเช่า, เรทค่าน้ำ-ไฟ และสิ่งอำนวยความสะดวกแยกตามห้อง
- **Dynamic Payment Channels:** ผูกบัญชี PromptPay, ธนาคาร หรือเงินสด ได้หลายรายการทั้งแบบ Global และเจาะจงรายห้อง

### 2. ระบบผู้เช่าและการลงทะเบียน (Tenant & Registration)
- **LINE Registration:** ผู้เช่าลงทะเบียนเข้าพักผ่าน LINE OA ได้ด้วยตัวเอง
- **Approval System:** ระบบอนุมัติผู้เช่าโดยเจ้าของผ่าน Admin Channel
- **Resident List:** จัดการรายชื่อผู้พักอาศัยสำรองในแต่ละห้อง
- **Move-out Workflow:** ระบบแจ้งย้ายออกพร้อมคำนวณบิลสุดท้ายตามสัดส่วน (Pro-rated)

### 3. ระบบมิเตอร์และบิลค่าเช่า (Metering & Billing)
- **Smart Metering:** จดบันทึกมิเตอร์น้ำ-ไฟ ได้ทั้งแบบรายห้องและแบบ Bulk (จดทีละหลายห้อง)
- **Auto-Invoice:** ออกใบแจ้งหนี้อัตโนมัติพร้อมส่งเข้า LINE ผู้เช่าทันที
- **Late Fee System:** ระบบคำนวณค่าปรับกรณีค้างชำระอัตโนมัติ
- **CSV Export:** ส่งออกรายงานสรุปรายรับรายเดือนเป็นไฟล์ Excel/CSV

### 4. ระบบแจ้งชำระเงินและตรวจสอบ (Payments)
- **Dynamic QR Code:** สร้าง QR Code พร้อมเพย์ตามยอดชำระจริง (Dynamic Amount)
- **Slip Verification:** ผู้เช่าอัปโหลดสลิปผ่านมือถือ เจ้าของตรวจสอบและอนุมัติผ่าน Dashboard
- **Flex Receipts:** ส่งใบเสร็จรับเงินแบบ Flex Message สวยงามเข้า LINE ผู้เช่าเมื่อยืนยันยอดเงิน

### 5. ระบบแจ้งซ่อมและสื่อสาร (Maintenance & Communication)
- **Repair Reporting:** ผู้เช่าแจ้งซ่อมพร้อมแนบรูปถ่ายผ่าน LINE
- **Status Tracking:** ติดตามสถานะการซ่อมได้แบบ Real-time
- **Broadcast Message:** เจ้าของส่งข้อความประกาศถึงผู้เช่าทุกคนผ่าน LINE

### 6. ความปลอดภัยและเสถียรภาพ (Security & Stability)
- **Bcrypt Hashing:** ระบบจัดเก็บรหัสผ่านแบบ Hashed (Bcrypt) มาตรฐานสากล
- **Brute-force Protection:** ระบบ Lockout ป้องกันการสุ่มรหัสผ่าน
- **SMTP Reset:** ระบบลืมรหัสผ่านผ่านทาง Email

## 🛠️ เทคโนโลยีที่ใช้ (Tech Stack)
- **Backend:** FastAPI (Python)
- **Database:** SQLite / SQLAlchemy (ORM)
- **Integration:** LINE Messaging SDK, LINE Notify
- **Frontend:** HTML5, CSS3 (Vanilla), JavaScript (ES6+)
- **Security:** Bcrypt, JSON Web Tokens (Session-based)

## 📞 ติดต่อสอบถาม (Contact)
หากพบปัญหาการใช้งานหรือต้องการข้อมูลเพิ่มเติม:
- **Email:** [rangsarn@gmail.com](mailto:rangsarn@gmail.com)

---
License: MIT

บริจาคเป็นกำลังใจได้ที่

0xcCAe4BDA3F9A92dd14D4193680535128f7DEE842

