"""Shared test setup: a fresh temporary database for every test, plus login helpers."""
import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="spms-test-")
os.environ["SPMS_DATABASE_URL"] = f"sqlite:///{_TMP_DIR}/test.db"
os.environ["SPMS_BCRYPT_ROUNDS"] = "4"  # fast hashing in tests only

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import auth, ratelimit  # noqa: E402
from backend.db import Base, SessionLocal, engine  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    ratelimit.reset()  # SR-006 counters start fresh for every test
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        auth.seed_default_users(db)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def login_headers(client, email, password):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    return login_headers(client, "admin@spms.local", "admin123")


@pytest.fixture
def attendant_headers(client):
    return login_headers(client, "attendant@spms.local", "attend123")


@pytest.fixture
def driver_headers(client):
    client.post("/api/v1/auth/register", json={
        "name": "Test Driver", "email": "driver@test.com", "phone": "9876543210", "password": "secret123"})
    return login_headers(client, "driver@test.com", "secret123")
