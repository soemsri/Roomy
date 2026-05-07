# PM Report: User Stories & Flow

## User Roles
1. **Owner (Admin)**: Manages rooms, tenants, billing, and system settings.
2. **Tenant**: Views bills, makes payments, and requests repairs.

## User Stories

### Onboarding & Mapping
- **US1.1**: As a tenant, I want to add the LINE OA and "register" with my room number so the system knows who I am.
- **US1.2**: As an owner, I want to see a list of unmapped LINE IDs and map them to specific rooms.

### Billing & Payments
- **US2.1**: As an owner, I want to record monthly meter readings (water/elec) for each room.
- **US2.2**: As an owner, I want to trigger "Send Bill" which pushes a message to the tenant's LINE.
- **US2.3**: As a tenant, I want to click a link in LINE to see my full bill on a web view.
- **US2.4**: As a tenant, I want to choose between "Cash" or "QR PromptPay" payment.
- **US2.5**: As an owner, I want to upload a signed receipt photo after a cash payment to confirm the transaction.

### Maintenance
- **US3.1**: As a tenant, I want to submit a repair request with photos via LINE.
- **US3.2**: As an owner, I want to track repair statuses and notify the tenant when fixed.

### System Settings
- **US4.1**: As an owner, I want to toggle QR Payment on/off and configure multiple PromptPay IDs.
