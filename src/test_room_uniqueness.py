import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from main import app, get_db
import models
import security
from database import Base

# Setup test database
SQLALCHEMY_DATABASE_URL = "sqlite://"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(name="db_session")
def fixture_db_session():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    try:
        # Add Owner for auth
        hashed_pw = security.hash_password("admin1234")
        owner = models.Owner(line_user_id="UADMIN", password_hash=hashed_pw)
        db.add(owner)
        
        # Add two buildings for tests
        b1 = models.Building(name="Building A")
        b2 = models.Building(name="Building B")
        db.add(b1)
        db.add(b2)
        db.commit()
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(name="client")
def fixture_client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def get_admin_cookie(db_session):
    owner = db_session.query(models.Owner).first()
    return {"admin_session": owner.password_hash}

def test_add_duplicate_room_different_buildings(client, db_session):
    cookies = get_admin_cookie(db_session)
    # 1. Add Room 101 in Building 1
    response = client.post("/admin/rooms/add", data={
        "room_number": "101",
        "floor": 1,
        "base_rent": 3000,
        "electricity_rate": 8,
        "water_rate": 18,
        "building_id": "1"
    }, cookies=cookies)
    assert response.status_code == 200
    
    # 2. Add Room 101 in Building 2 (Should work now!)
    response = client.post("/admin/rooms/add", data={
        "room_number": "101",
        "floor": 1,
        "base_rent": 3000,
        "electricity_rate": 8,
        "water_rate": 18,
        "building_id": "2"
    }, cookies=cookies)
    assert response.status_code == 200

def test_add_duplicate_room_same_building(client, db_session):
    cookies = get_admin_cookie(db_session)
    # 1. Add Room 101 in Building 1
    client.post("/admin/rooms/add", data={
        "room_number": "101",
        "floor": 1,
        "base_rent": 3000,
        "electricity_rate": 8,
        "water_rate": 18,
        "building_id": "1"
    }, cookies=cookies)
    
    # 2. Add Room 101 in Building 1 (Should fail)
    response = client.post("/admin/rooms/add", data={
        "room_number": "101",
        "floor": 1,
        "base_rent": 3000,
        "electricity_rate": 8,
        "water_rate": 18,
        "building_id": "1"
    }, cookies=cookies)
    assert response.status_code == 400
    assert "already exists in this building" in response.json()["detail"]

def test_edit_room_to_duplicate_same_building(client, db_session):
    cookies = get_admin_cookie(db_session)
    # 1. Add Room 101 and 102 in Building 1
    client.post("/admin/rooms/add", data={"room_number": "101", "floor": 1, "base_rent": 3000, "electricity_rate": 8, "water_rate": 18, "building_id": "1"}, cookies=cookies)
    client.post("/admin/rooms/add", data={"room_number": "102", "floor": 1, "base_rent": 3000, "electricity_rate": 8, "water_rate": 18, "building_id": "1"}, cookies=cookies)
    
    # 2. Edit Room 102 (ID 2) to 101 in Building 1 (Should fail)
    # Note: SQLite IDs are predictable in this setup
    response = client.post("/admin/rooms/2/edit", data={
        "room_number": "101",
        "floor": 1,
        "base_rent": 3000,
        "electricity_rate": 8,
        "water_rate": 18,
        "building_id": "1"
    }, cookies=cookies)
    assert response.status_code == 400
    assert "already exists in this building" in response.json()["detail"]

def test_edit_room_to_duplicate_different_building(client, db_session):
    cookies = get_admin_cookie(db_session)
    # 1. Add Room 101 in Building 1
    client.post("/admin/rooms/add", data={"room_number": "101", "floor": 1, "base_rent": 3000, "electricity_rate": 8, "water_rate": 18, "building_id": "1"}, cookies=cookies)
    # 2. Add Room 201 in Building 2
    client.post("/admin/rooms/add", data={"room_number": "201", "floor": 2, "base_rent": 3000, "electricity_rate": 8, "water_rate": 18, "building_id": "2"}, cookies=cookies)
    
    # 3. Edit Room 201 (ID 2) to 101 but keep it in Building 2 (Should work!)
    response = client.post("/admin/rooms/2/edit", data={
        "room_number": "101",
        "floor": 2,
        "base_rent": 3000,
        "electricity_rate": 8,
        "water_rate": 18,
        "building_id": "2"
    }, cookies=cookies)
    assert response.status_code == 200

