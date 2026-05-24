import unittest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
import sys
import json
import shutil

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import main
import models
from database import Base, get_db
import services.backup as backup_service

# Setup Mock Database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_backup_db.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

class TestBackupSystem(unittest.TestCase):
    def setUp(self):
        # Set overrides before each test
        main.app.dependency_overrides[get_db] = override_get_db
        main.app.dependency_overrides[main.get_db] = override_get_db
        main.app.dependency_overrides[main.get_admin] = lambda: True
        main.app.dependency_overrides[main.get_super_admin] = lambda: True
        
        # Ensure backups directory is clean or we track created files
        self.created_backups = []
        # Save original backups folder path and redirect it to a test folder
        self.orig_backups_dir = backup_service.BACKUPS_DIR
        self.test_backups_dir = os.path.abspath("./test_backups_folder")
        backup_service.BACKUPS_DIR = self.test_backups_dir
        if os.path.exists(self.test_backups_dir):
            shutil.rmtree(self.test_backups_dir)
        os.makedirs(self.test_backups_dir)

    def tearDown(self):
        main.app.dependency_overrides.clear()
        # Clean up test backups
        backup_service.BACKUPS_DIR = self.orig_backups_dir
        if os.path.exists(self.test_backups_dir):
            shutil.rmtree(self.test_backups_dir)

    @classmethod
    def setUpClass(cls):
        engine.dispose()
        if os.path.exists("./test_backup_db.db"):
            try:
                os.remove("./test_backup_db.db")
            except:
                pass

        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()
        # Create initial owner to backup
        owner = models.Owner(line_user_id="TEST_OWNER_BACKUP", display_name="Original Owner Name")
        db.add(owner)
        db.commit()
        db.close()

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        if os.path.exists("./test_backup_db.db"):
            try:
                os.remove("./test_backup_db.db")
            except:
                pass

    def test_backup_and_restore_cycle(self):
        client = TestClient(main.app)

        # 1. Create a backup
        response = client.post("/admin/backup/create")
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data["status"], "Success")
        filename = res_data["filename"]
        self.assertTrue(filename.startswith("backup_"))

        # 2. List backups
        list_response = client.get("/admin/backup/list")
        self.assertEqual(list_response.status_code, 200)
        backups = list_response.json()
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0]["filename"], filename)

        # 3. Download backup
        download_response = client.get(f"/admin/backup/download/{filename}")
        self.assertEqual(download_response.status_code, 200)
        self.assertTrue(len(download_response.content) > 0)

        # 4. Modify db data to simulate data change
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        owner.display_name = "Modified Owner Name"
        db.commit()
        db.close()

        # Check modified value
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        self.assertEqual(owner.display_name, "Modified Owner Name")
        db.close()

        # 5. Restore from backup
        restore_response = client.post(f"/admin/backup/restore/{filename}")
        self.assertEqual(restore_response.status_code, 200)
        self.assertEqual(restore_response.json()["status"], "Success")

        # 6. Verify db was restored back to original
        db = TestingSessionLocal()
        owner = db.query(models.Owner).first()
        self.assertEqual(owner.display_name, "Original Owner Name")
        db.close()

        # 7. Get and save backup schedule config
        sched_get_resp = client.get("/admin/backup/schedule")
        self.assertEqual(sched_get_resp.status_code, 200)
        sched_config = sched_get_resp.json()
        self.assertEqual(sched_config["frequency"], "disabled")

        # Save schedule
        sched_save_resp = client.post(
            "/admin/backup/schedule/save",
            data={
                "frequency": "daily",
                "time_str": "03:30",
                "max_backups": 5
            }
        )
        self.assertEqual(sched_save_resp.status_code, 200)
        self.assertEqual(sched_save_resp.json()["status"], "Success")

        # Verify saved schedule
        sched_get_resp = client.get("/admin/backup/schedule")
        sched_config = sched_get_resp.json()
        self.assertEqual(sched_config["frequency"], "daily")
        self.assertEqual(sched_config["time"], "03:30")
        self.assertEqual(sched_config["max_backups"], 5)

        # 8. Delete backup file
        delete_response = client.post(f"/admin/backup/delete/{filename}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["status"], "Success")

        # Verify backup list is empty
        list_response = client.get("/admin/backup/list")
        self.assertEqual(len(list_response.json()), 0)

if __name__ == "__main__":
    unittest.main()
