import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_mock_engine
from sqlalchemy.orm import sessionmaker
import os
import sys

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import main
import models
from database import Base, get_db

# Setup Mock Database
from sqlalchemy import create_engine
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_repair.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# main.app.dependency_overrides[main.get_db] = override_get_db
# Also try main.get_db just in case
# main.app.dependency_overrides[main.get_db] = override_get_db

class TestRepairNotification(unittest.TestCase):
    def setUp(self):
        # Set overrides before each test
        main.app.dependency_overrides[get_db] = override_get_db
        main.app.dependency_overrides[main.get_db] = override_get_db

    @classmethod
    def setUpClass(cls):
        # Debug: check function objects
        print(f"DEBUG: main.get_db is get_db: {main.get_db is get_db}")
        
        # Stop any existing engine connections
        engine.dispose()
        # Delete the database file to ensure a clean start
        if os.path.exists("./test_repair.db"):
            try:
                os.remove("./test_repair.db")
            except:
                pass
        
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        # Setup Owner
        owner = models.Owner(line_user_id="ADMIN_ID", display_name="Admin")
        db.add(owner)
        # Setup Room
        room = models.Room(room_number="R101", floor=1, base_rent=3000, status="Occupied")
        db.add(room)
        db.commit()
        # Setup Tenant
        tenant = models.Tenant(line_user_id="TENANT_ID", current_room_id=room.id, status="Active", uuid="test-uuid")
        db.add(tenant)
        db.commit()
        cls.tenant_id = tenant.id
        cls.room_id = room.id
        db.close()

    @classmethod
    def tearDownClass(cls):
        # Properly close engine to avoid PermissionError on Windows
        engine.dispose()
        if os.path.exists("./test_repair.db"):
            try:
                os.remove("./test_repair.db")
            except:
                pass

    @patch("main.admin_bot_api")
    @patch("main.send_line_notify")
    def test_submit_repair_notifies_admin_with_image(self, mock_notify, mock_bot):
        # Create client here
        local_client = TestClient(main.app)
        mock_bot.push_message = MagicMock()
        from linebot.models import ImageSendMessage

        # Debug: Check DB state
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        room = db.query(models.Room).filter(models.Room.id == self.room_id).first()
        print(f"DEBUG: Owner ID in DB: {owner.line_user_id if owner else 'None'}")
        print(f"DEBUG: Room Number in DB for ID {self.room_id}: {room.room_number if room else 'None'}")        
        db.close()

        # Simulate file upload
        import io
        fake_file = io.BytesIO(b"fake image data")

        response = local_client.post(
            "/repair/submit",
            data={
                "tenant_id": self.tenant_id,
                "room_id": self.room_id,
                "title": "Broken Window",
                "description": "Glass is everywhere"
            },
            files={"image": ("test.jpg", fake_file, "image/jpeg")}
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify bot was called twice (once for text, once for image)
        self.assertEqual(mock_bot.push_message.call_count, 2)
        
        # Check second call (ImageSendMessage)
        args, kwargs = mock_bot.push_message.call_args_list[1]
        # The code might be returning the ID from the DB
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        db.close()
        self.assertEqual(args[0], owner.line_user_id)
        self.assertIsInstance(args[1], ImageSendMessage)
        self.assertTrue(args[1].original_content_url.startswith("http"))
        self.assertIn("/uploads/repair_", args[1].original_content_url)

    @patch("main.admin_bot_api", None)
    @patch("main.send_line_notify")
    def test_submit_repair_notifies_line_notify_fallback(self, mock_notify):
        # Create client here
        local_client = TestClient(main.app)
        response = local_client.post(
            "/repair/submit",
            data={
                "tenant_id": self.tenant_id,
                "room_id": self.room_id,
                "title": "Light Bulb",
                "description": "Need replacement"
            }
        )
        
        self.assertEqual(response.status_code, 200)
        mock_notify.assert_called_once()
        args, kwargs = mock_notify.call_args
        self.assertIn("Light Bulb", args[0])
        self.assertIn("R101", args[0])

if __name__ == "__main__":
    unittest.main()
