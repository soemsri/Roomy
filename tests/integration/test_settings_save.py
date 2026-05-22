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

# Setup Mock Database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_settings.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# main.app.dependency_overrides[main.get_db] = override_get_db
# Move overrides to setUpClass to avoid global leakage

class TestSettingsSave(unittest.TestCase):
    def setUp(self):
        # Set overrides before each test
        main.app.dependency_overrides[get_db] = override_get_db
        main.app.dependency_overrides[main.get_db] = override_get_db
        main.app.dependency_overrides[main.get_admin] = lambda: True

    @classmethod
    def setUpClass(cls):
        
        # Stop any existing engine connections
        engine.dispose()
        # Delete the database file to ensure a clean start
        if os.path.exists("./test_settings.db"):
            try:
                os.remove("./test_settings.db")
            except:
                pass

        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        # Ensure owner exists
        owner = models.Owner(line_user_id="TEST_OWNER", display_name="Test Owner")
        db.add(owner)
        db.commit()
        db.close()

    @classmethod
    def tearDownClass(cls):
        # Clear overrides
        main.app.dependency_overrides.clear()
        
        # Properly close engine to avoid PermissionError
        engine.dispose()
        if os.path.exists("./test_settings.db"):
            try:
                os.remove("./test_settings.db")
            except:
                pass

    def test_save_promptpay_config(self):
        # Create client here
        local_client = TestClient(main.app)
        # 1. Define data to save
        test_config = [
            {"id": "0812345678", "name": "Account One"},
            {"id": "0998887777", "name": "Account Two"}
        ]
        config_json = json.dumps(test_config)
        
        # 2. Call save endpoint
        response = local_client.post(
            "/admin/settings/save",
            data={
                "display_name": "New Name",
                "promptpay_config": config_json,
                "qr_enabled": 1,
                "late_fee_enabled": 1,
                "due_day": 10,
                "late_fee_per_day": 100.0
            }
        )
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "Success")
        
        # 3. Verify in Database
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        db.close()
        
        print(f"\nStored Config: {owner.promptpay_config}")
        self.assertEqual(owner.display_name, "New Name")
        self.assertEqual(owner.promptpay_config, config_json)
        
        # 4. Verify parsing back
        parsed = json.loads(owner.promptpay_config)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["id"], "0812345678")

if __name__ == "__main__":
    unittest.main()
