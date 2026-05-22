import sys
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock

# Ensure the 'src' directory is in python path
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

# Set default mock environment variables for testing before importing main
os.environ.setdefault("LINE_ADMIN_CHANNEL_SECRET", "dummy_secret")
os.environ.setdefault("LINE_TENANT_CHANNEL_SECRET", "dummy_secret")
os.environ.setdefault("LINE_ADMIN_CHANNEL_ACCESS_TOKEN", "dummy_token")
os.environ.setdefault("LINE_TENANT_CHANNEL_ACCESS_TOKEN", "dummy_token")
os.environ.setdefault("LINE_NOTIFY_TOKEN", "dummy_token")

from database import Base, get_db
from main import app
import models
import security

# SQLite test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_suk_anan.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function")
def setup_db():
    # Setup test schema
    Base.metadata.create_all(bind=engine)
    
    # Create default owner for tests
    db = TestingSessionLocal()
    hashed_pw = security.hash_password("admin1234")
    owner = models.Owner(
        line_user_id="UADMIN", 
        password_hash=hashed_pw, 
        session_token="test_session_token",
        move_in_fees_config='[{"name": "ค่าเช่าล่วงหน้า 1 เดือน", "value": 1, "is_multiplier": true}, {"name": "ค่าประกันทรัพย์สิน", "value": 5000, "is_multiplier": false}]'
    )
    db.add(owner)
    db.commit()
    db.close()
    
    yield
    
    # Teardown test schema
    Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def db_session(setup_db):
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

@pytest.fixture(scope="function")
def client(setup_db):
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.fixture(scope="function", autouse=True)
def mock_line_apis(monkeypatch):
    """Automatically mock all LINE API instances globally for all tests."""
    mock_tenant_api = MagicMock()
    mock_admin_api = MagicMock()
    monkeypatch.setattr("main.tenant_bot_api", mock_tenant_api)
    monkeypatch.setattr("main.admin_bot_api", mock_admin_api)
    return mock_tenant_api, mock_admin_api
