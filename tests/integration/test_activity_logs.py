import unittest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import sys
import json

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import main
import models
from database import Base, get_db
from controllers.admin import get_current_user

# Setup Mock Database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_activity.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

class TestActivityLogs(unittest.TestCase):
    def setUp(self):
        # Set overrides before each test
        main.app.dependency_overrides[get_db] = override_get_db
        main.app.dependency_overrides[main.get_db] = override_get_db
        main.app.dependency_overrides[main.get_admin] = lambda: True
        main.app.dependency_overrides[main.get_super_admin] = lambda: True
        main.app.dependency_overrides[get_current_user] = lambda: models.User(email="test_admin@system.local", role="Admin", status="Active")

        # Isolated clean database for each test
        import models.database
        self.original_session_local = models.database.SessionLocal
        models.database.SessionLocal = TestingSessionLocal
        
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        import security
        owner = models.Owner(
            line_user_id="TEST_OWNER_LOGS", 
            display_name="Test Owner",
            password_hash=security.hash_password("admin123")
        )
        db.add(owner)
        
        # Add 25 sample logs to test pagination
        for i in range(25):
            log = models.ApplicationLog(
                actor="test_admin@system.local",
                action=f"Action {i+1}",
                target=f"Target {i+1}",
                details=f"Details for action {i+1}"
            )
            db.add(log)
            
        db.commit()
        db.close()

    def tearDown(self):
        import models.database
        models.database.SessionLocal = self.original_session_local
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)

    def test_logs_retrieval_and_pagination(self):
        client = TestClient(main.app)

        # 1. Fetch page 1 (should have 10 items)
        response = client.get("/admin/activity/list?page=1&page_size=10")
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        
        self.assertIn("logs", res_data)
        self.assertEqual(len(res_data["logs"]), 10)
        self.assertEqual(res_data["total"], 25)
        self.assertEqual(res_data["page"], 1)
        self.assertEqual(res_data["pages"], 3)
        self.assertEqual(res_data["page_size"], 10)

        # 2. Fetch page 3 (should have 5 items)
        response_p3 = client.get("/admin/activity/list?page=3&page_size=10")
        self.assertEqual(response_p3.status_code, 200)
        res_data_p3 = response_p3.json()
        self.assertEqual(len(res_data_p3["logs"]), 5)

    def test_logs_search(self):
        client = TestClient(main.app)

        # Search for exact action
        response = client.get("/admin/activity/list?search=Action 15")
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(len(res_data["logs"]), 1)
        self.assertEqual(res_data["logs"][0]["action"], "Action 15")
        self.assertEqual(res_data["logs"][0]["target"], "Target 15")

        # Search with non-existent keyword
        response_empty = client.get("/admin/activity/list?search=NonExistentKeywordXYZ")
        self.assertEqual(response_empty.status_code, 200)
        res_data_empty = response_empty.json()
        self.assertEqual(len(res_data_empty["logs"]), 0)
        self.assertEqual(res_data_empty["total"], 0)

    def test_log_creation_on_action(self):
        client = TestClient(main.app)

        # Perform a settings save operation
        test_config = [
            {"id": "0812345678", "name": "Account One"}
        ]
        response = client.post(
            "/admin/settings/save",
            data={
                "display_name": "New Owner Name",
                "promptpay_config": json.dumps(test_config),
                "qr_enabled": 1,
                "late_fee_enabled": 1,
                "due_day": 10,
                "late_fee_per_day": 100.0
            }
        )
        self.assertEqual(response.status_code, 200)

        # Query activity logs list to verify that "Update Settings" has been logged!
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        self.assertEqual(response_logs.status_code, 200)
        logs = response_logs.json()["logs"]
        
        # Verify that the top log (newest) is the "Update Settings" action
        # Because we seeded 25, the settings save log should be at index 0 (newest)
        settings_logs = [l for l in logs if l["action"] == "Update Settings"]
        self.assertTrue(len(settings_logs) >= 1)
        self.assertEqual(settings_logs[0]["actor"], "test_admin@system.local")
        self.assertEqual(settings_logs[0]["target"], "System Settings")
        self.assertIn("New Owner Name", settings_logs[0]["details"])

    def test_legacy_login_success_logging(self):
        client = TestClient(main.app)
        
        # Success login
        response = client.post("/admin/login", data={"password": "admin123"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].endswith("/admin/dashboard"))
        
        # Verify activity log
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        login_logs = [l for l in logs if l["action"] == "Legacy Login"]
        self.assertTrue(len(login_logs) >= 1)
        self.assertEqual(login_logs[0]["actor"], "legacy_owner@system.local")
        self.assertEqual(login_logs[0]["target"], "Admin Authentication")
        self.assertIn("Logged in successfully", login_logs[0]["details"])

    def test_legacy_login_failure_logging(self):
        client = TestClient(main.app)
        
        # Attempt 1: Failed password
        response = client.post("/admin/login", data={"password": "wrongpassword"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        
        # Verify activity log shows failure attempt
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        fail_logs = [l for l in logs if l["action"] == "Failed Login Attempt"]
        self.assertTrue(len(fail_logs) >= 1)
        self.assertEqual(fail_logs[0]["target"], "Admin Authentication")
        self.assertIn("Lockout counter: 1/3", fail_logs[0]["details"])

    def test_legacy_logout_logging(self):
        client = TestClient(main.app)
        
        # Seed Owner session token in database
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        owner.session_token = "test_owner_logout_token"
        db.commit()
        db.close()
        
        # Request logout
        response = client.post("/admin/logout", cookies={"admin_session": "test_owner_logout_token"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        
        # Verify the activity log shows "Logout"
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        logout_logs = [l for l in logs if l["action"] == "Logout"]
        self.assertTrue(len(logout_logs) >= 1)
        self.assertEqual(logout_logs[0]["actor"], "legacy_owner@system.local")
        self.assertEqual(logout_logs[0]["target"], "System Session")

    def test_user_logout_logging(self):
        client = TestClient(main.app)
        
        # Create User in database
        db = TestingSessionLocal()
        user = models.User(email="staff@system.local", role="Clerk", status="Active", session_token="test_user_logout_token")
        db.add(user)
        db.commit()
        db.close()
        
        # Request logout
        response = client.post("/admin/logout", cookies={"admin_session": "test_user_logout_token"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        
        # Verify the activity log shows "Logout"
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        logout_logs = [l for l in logs if l["action"] == "Logout"]
        self.assertTrue(len(logout_logs) >= 1)
        self.assertEqual(logout_logs[0]["actor"], "staff@system.local")
        self.assertEqual(logout_logs[0]["target"], "System Session")

    def test_magic_login_logging(self):
        client = TestClient(main.app)
        
        # Create a magic token for the owner
        import datetime
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        owner.magic_token = "valid_magic_token"
        owner.magic_token_expires = datetime.datetime.now() + datetime.timedelta(minutes=5)
        db.commit()
        db.close()
        
        # Successful magic login
        response = client.get("/admin/magic-login?token=valid_magic_token", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        
        # Failed magic login
        response_fail = client.get("/admin/magic-login?token=invalid_magic_token", follow_redirects=False)
        self.assertEqual(response_fail.status_code, 400)
        
        # Verify logs
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        
        magic_success_logs = [l for l in logs if l["action"] == "Magic Login"]
        self.assertTrue(len(magic_success_logs) >= 1)
        self.assertEqual(magic_success_logs[0]["actor"], "legacy_owner@system.local")
        
        magic_fail_logs = [l for l in logs if l["action"] == "Failed Magic Login"]
        self.assertTrue(len(magic_fail_logs) >= 1)
        self.assertIn("rejected", magic_fail_logs[0]["details"])

    def test_clear_activity_logs(self):
        client = TestClient(main.app)
        
        # Verify initial logs exist (we seeded 25 in setUp)
        response_initial = client.get("/admin/activity/list?page=1&page_size=50")
        self.assertEqual(response_initial.json()["total"], 25)
        
        # Request clear
        response_clear = client.post("/admin/activity/clear")
        self.assertEqual(response_clear.status_code, 200)
        self.assertEqual(response_clear.json()["status"], "Success")
        
        # Verify that only the "Clear Logs" activity log remains
        response_after = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_after.json()["logs"]
        self.assertEqual(response_after.json()["total"], 1)
        self.assertEqual(logs[0]["action"], "Clear Logs")
        self.assertEqual(logs[0]["actor"], "test_admin@system.local")
        self.assertEqual(logs[0]["details"], "All previous activity logs cleared successfully")

    def test_export_activity_logs(self):
        client = TestClient(main.app)
        
        # Request export CSV (without filter)
        response = client.get("/admin/activity/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertIn("attachment; filename=activity_logs_export.csv", response.headers["content-disposition"])
        
        # Verify content
        csv_content = response.content.decode("utf-8-sig") # Remove BOM
        lines = csv_content.splitlines()
        self.assertTrue(len(lines) > 25) # Should contain header + seeded records + any additional logs
        self.assertEqual(lines[0], "Timestamp,Actor,Action,Target,Details")
        
        # Request export CSV (with filter for Action 15)
        response_filtered = client.get("/admin/activity/export?search=Action 15")
        self.assertEqual(response_filtered.status_code, 200)
        filtered_content = response_filtered.content.decode("utf-8-sig")
        filtered_lines = filtered_content.splitlines()
        self.assertEqual(len(filtered_lines), 2) # Header + 1 match
        self.assertIn("Action 15", filtered_lines[1])

    def test_global_exception_handling_logs_error(self):
        # Register a temporary crash route on the app for this test
        @main.app.get("/admin/test-error-endpoint")
        async def trigger_test_error():
            raise ValueError("Simulated system crash for log testing")
            
        client = TestClient(main.app, raise_server_exceptions=False)
        
        # Request the crash route
        response = client.get("/admin/test-error-endpoint")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Internal Server Error"})
        
        # Query activity logs list to verify that the unhandled exception has been logged!
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        
        error_logs = [l for l in logs if l["action"] == "Application Error"]
        self.assertTrue(len(error_logs) >= 1)
        self.assertEqual(error_logs[0]["actor"], "System (Error Handler)")
        self.assertEqual(error_logs[0]["target"], "/admin/test-error-endpoint")
        self.assertIn("ValueError", error_logs[0]["details"])
        self.assertIn("Simulated system crash", error_logs[0]["details"])

    def test_unauthenticated_access_violation_logging(self):
        client = TestClient(main.app)
        
        # Remove dependencies overrides temporarily to test actual authentication blocking
        overrides_to_remove = [get_current_user, main.get_admin, main.get_super_admin]
        backup_overrides = {}
        for dep in overrides_to_remove:
            if dep in main.app.dependency_overrides:
                backup_overrides[dep] = main.app.dependency_overrides[dep]
                del main.app.dependency_overrides[dep]
            
        # Request access anonymously
        response = client.get("/admin/activity/list")
        self.assertEqual(response.status_code, 401)
        
        # Restore overrides
        for dep, val in backup_overrides.items():
            main.app.dependency_overrides[dep] = val
            
        # Verify access violation log
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        violation_logs = [l for l in logs if l["action"] == "Access Violation"]
        self.assertTrue(len(violation_logs) >= 1)
        self.assertEqual(violation_logs[0]["target"], "System Security")
        self.assertIn("Blocked unauthenticated anonymous request", violation_logs[0]["details"])

    def test_unauthorized_role_access_violation_logging(self):
        client = TestClient(main.app)
        
        # Remove get_super_admin override temporarily to trigger actual RBAC validation
        backup_super_admin = None
        if main.get_super_admin in main.app.dependency_overrides:
            backup_super_admin = main.app.dependency_overrides[main.get_super_admin]
            del main.app.dependency_overrides[main.get_super_admin]
            
        # Override get_current_user to return a low-privilege staff User (Technician)
        main.app.dependency_overrides[get_current_user] = lambda: models.User(email="malicious_tech@system.local", role="Technician", status="Active")
        
        # Request super-admin endpoint (activity list) which requires Admin role
        response = client.get("/admin/activity/list")
        self.assertEqual(response.status_code, 403)
        
        # Restore overrides
        main.app.dependency_overrides[get_current_user] = lambda: models.User(email="test_admin@system.local", role="Admin", status="Active")
        if backup_super_admin:
            main.app.dependency_overrides[main.get_super_admin] = backup_super_admin
        
        # Verify access violation log
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        violation_logs = [l for l in logs if l["action"] == "Access Violation" and l["actor"] == "malicious_tech@system.local"]
        self.assertTrue(len(violation_logs) >= 1)
        self.assertEqual(violation_logs[0]["target"], "System Security")
        self.assertIn("Blocked role 'Technician' from accessing super-admin endpoint", violation_logs[0]["details"])

    def test_broadcast_announcement_logging(self):
        client = TestClient(main.app)
        
        # Seed active tenant with line_user_id
        db = TestingSessionLocal()
        tenant = models.Tenant(line_user_id="U123456789", full_name="Tenant One", current_room_id=1, status="Active")
        db.add(tenant)
        db.commit()
        db.close()
        
        # Trigger broadcast
        response = client.post("/admin/broadcast", data={"message": "Important announcement!"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Success")
        
        # Verify activity log
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        broadcast_logs = [l for l in logs if l["action"] == "Broadcast Message"]
        self.assertTrue(len(broadcast_logs) >= 1)
        self.assertEqual(broadcast_logs[0]["actor"], "test_admin@system.local")
        self.assertEqual(broadcast_logs[0]["target"], "LINE Broadcast")
        self.assertIn("Important announcement!", broadcast_logs[0]["details"])

    def test_send_direct_line_logging(self):
        client = TestClient(main.app)
        
        # Seed a tenant
        db = TestingSessionLocal()
        tenant = models.Tenant(line_user_id="U123456789", full_name="Tenant One", current_room_id=1, status="Active")
        db.add(tenant)
        db.commit()
        tenant_id = tenant.id
        db.close()
        
        # Trigger direct message
        response = client.post(f"/admin/tenants/{tenant_id}/send-line", data={"message": "Hello tenant!"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Success")
        
        # Verify activity log
        response_logs = client.get("/admin/activity/list?page=1&page_size=50")
        logs = response_logs.json()["logs"]
        dm_logs = [l for l in logs if l["action"] == "Send Direct Message"]
        self.assertTrue(len(dm_logs) >= 1)
        self.assertEqual(dm_logs[0]["actor"], "test_admin@system.local")
        self.assertEqual(dm_logs[0]["target"], "Tenant One")
        self.assertIn("Hello tenant!", dm_logs[0]["details"])

if __name__ == "__main__":
    unittest.main()
