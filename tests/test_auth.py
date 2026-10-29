"""Tests for SPMS-F-001 .. F-004 (TC-AUTH-01 .. TC-AUTH-04)."""
import datetime as dt

from backend import auth
from backend.db import SessionLocal

NEW_DRIVER = {"name": "Asha", "email": "asha@example.com", "phone": "9123456780", "password": "pass1234"}


def test_register_creates_driver(client):  # TC-AUTH-01
    response = client.post("/api/v1/auth/register", json=NEW_DRIVER)
    assert response.status_code == 201
    assert response.json()["role"] == "DRIVER"


def test_register_rejects_duplicate_email(client):  # TC-AUTH-01
    client.post("/api/v1/auth/register", json=NEW_DRIVER)
    again = dict(NEW_DRIVER, phone="9000011111")
    response = client.post("/api/v1/auth/register", json=again)
    assert response.status_code == 409


def test_register_validates_input(client):
    bad = dict(NEW_DRIVER, email="not-an-email", phone="123")
    assert client.post("/api/v1/auth/register", json=bad).status_code == 422


def test_login_returns_token(client):  # TC-AUTH-02
    client.post("/api/v1/auth/register", json=NEW_DRIVER)
    response = client.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "pass1234"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "asha@example.com"


def test_login_wrong_password_is_401(client):  # TC-AUTH-02
    client.post("/api/v1/auth/register", json=NEW_DRIVER)
    response = client.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "wrong"})
    assert response.status_code == 401


def test_password_is_hashed_not_plain(client):  # SR-002
    client.post("/api/v1/auth/register", json=NEW_DRIVER)
    with SessionLocal() as db:
        user = db.query(auth.User).filter_by(email="asha@example.com").one()
    assert user.password_hash != "pass1234" and user.password_hash.startswith("$2")


def test_role_based_access(client, driver_headers, admin_headers):  # TC-AUTH-03
    assert client.get("/api/v1/auth/admin-check", headers=driver_headers).status_code == 403
    assert client.get("/api/v1/auth/admin-check", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/auth/admin-check").status_code == 401


def test_invalid_token_rejected(client):  # SR-003
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401


def test_account_locks_after_5_failed_logins(client):  # TC-AUTH-04
    client.post("/api/v1/auth/register", json=NEW_DRIVER)
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "wrong"})
    response = client.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "pass1234"})
    assert response.status_code == 423


def test_lock_expires(client):  # TC-AUTH-04
    client.post("/api/v1/auth/register", json=NEW_DRIVER)
    with SessionLocal() as db:
        user = db.query(auth.User).filter_by(email="asha@example.com").one()
        user.locked_until = auth.utcnow() - dt.timedelta(minutes=1)
        db.commit()
    response = client.post("/api/v1/auth/login", json={"email": "asha@example.com", "password": "pass1234"})
    assert response.status_code == 200
