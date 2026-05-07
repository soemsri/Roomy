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
