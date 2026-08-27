# PM Report: User Flow Design

## 1. LINE User Mapping Flow (Onboarding)
1. **Tenant**: Adds LINE OA.
2. **Tenant**: Sends message "สมัคร" or Room Number (e.g., "A101").
3. **System**: Webhook captures `userId`.
4. **System**: Checks if `userId` is already mapped.
5. **System**: If not, sends "Registration link" (Web View) or adds to Owner's "Pending Mapping" list.
6. **Owner**: Accesses "Backoffice" via Rich Menu.
7. **Owner**: Selects "Pending Mappings" -> Clicks "Map to Room" -> Assigns `userId` to a Room ID.

## 2. Billing & Payment Flow
1. **Owner**: Records meter readings via Owner Rich Menu.
2. **System**: Calculates bill based on (Reading - Last Reading) * Rate.
3. **Owner**: Reviews and clicks "Send Bill".
4. **System**: Push Message to Tenant's LINE with a summary and "View Bill" button.
5. **Tenant**: Clicks "View Bill" -> Opens Web App.
6. **Tenant**: Chooses payment:
   - **QR PromptPay**: System generates QR based on `total_amount`. Tenant pays and notifies.
   - **Cash**: System shows "Please pay at the office".
7. **Owner (Post-Payment)**: 
   - If Cash: Owner signs paper bill -> Takes Photo -> Uploads via Owner App -> Status becomes "Paid".
   - If QR: System detects (or user uploads slip) -> Owner confirms -> Status becomes "Paid".

## 3. Maintenance Flow
1. **Tenant**: Clicks "แจ้งซ่อม" (Repair) in Rich Menu.
2. **Tenant**: Fills Form (Title, Description, Uploads Photo).
3. **System**: Notifies Owner (LINE Notify/Messaging API).
4. **Owner**: Updates status to "In Progress" -> "Fixed".
5. **System**: Notifies Tenant when status is "Fixed".

## 4. Parcel Management Flow (Simplified Flow - No QR/PIN)

### 4.1 Inward Parcel Registration (รับพัสดุเข้า)
1. **Owner/Staff**: Receives incoming parcel at front desk / office.
2. **Owner/Staff**: Fills parcel entry form (Select Room, Carrier e.g. Kerry/Flash/Shopee, Tracking No., optional parcel photo).
3. **System**: Creates parcel record (`status = 'pending_pickup'`).
4. **System**: Sends push notification (Flex Message with photo & details) to the tenant's LINE OA.

### 4.2 Parcel Pickup / Handover (ส่งมอบพัสดุให้ผู้เช่า - แบบลดขั้นตอน)
1. **Tenant**: Comes to front desk to collect parcel (informs room number).
2. **Owner/Staff**: Locates parcel in Admin Dashboard / Parcel List.
3. **Owner/Staff**: Clicks the **"รับพัสดุแล้ว (Mark as Received)"** button directly.
   - *No QR Code scan or PIN Code verification required* (เพื่อความสะดวกรวดเร็ว ไม่ยุ่งยาก).
   - *Optional Proof*: Staff can optionally attach a photo of the recipient or note, but can also submit immediately without it.
4. **System**: Updates parcel status to `received` with timestamp.
5. **System**: (Optional) Sends notification to Tenant LINE confirming receipt.
