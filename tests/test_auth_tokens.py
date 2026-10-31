"""Tests for SPMS-SR-002 (bcrypt, never returned), SR-003 (30-min JWT, refresh rotation, logout
revocation) and SR-006 (rate limiting)."""
import datetime as dt

import jwt

from backend import auth

DRIVER = {"name": "Ravi", "email": "ravi@example.com", "phone": "9333333333", "password": "pass1234"}


def login(client, email="ravi@example.com", password="pass1234"):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_password_never_returned_and_cost_12(client):  # SR-002
    body = client.post("/api/v1/auth/register", json=DRIVER).json()
    tokens = login(client).json()
    for payload in (body, tokens, client.get("/api/v1/auth/me", headers=bearer(tokens["access_token"])).json()):
        assert not any("password" in key or "hash" in key for key in payload)
    assert auth.DEFAULT_BCRYPT_ROUNDS >= 12


def test_access_token_expires_in_30_minutes(client):  # SR-003
    client.post("/api/v1/auth/register", json=DRIVER)
    token = login(client).json()["access_token"]
    claims = jwt.decode(token, auth.SECRET_KEY, algorithms=[auth.ALGORITHM])
    lifetime = claims["exp"] - dt.datetime.now(dt.timezone.utc).timestamp()
    assert 29 * 60 < lifetime <= 30 * 60
    expired = jwt.encode(dict(claims, exp=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)),
                         auth.SECRET_KEY, algorithm=auth.ALGORITHM)
    assert client.get("/api/v1/auth/me", headers=bearer(expired)).status_code == 401
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get("/api/v1/auth/me", headers=bearer(tampered)).status_code == 401
    forged = jwt.encode(dict(claims, role="ADMIN"), "a-different-secret-key-that-is-32-bytes-long", algorithm="HS256")
    assert client.get("/api/v1/auth/admin-check", headers=bearer(forged)).status_code == 401


def test_refresh_token_rotation(client):  # SR-003
    client.post("/api/v1/auth/register", json=DRIVER)
    first = login(client).json()["refresh_token"]
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert second.status_code == 200 and second.json()["refresh_token"] != first
    new_refresh = second.json()["refresh_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": first}).status_code == 401  # reused
    # re-use looks like theft: every refresh token of that user is revoked
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh}).status_code == 401


def test_logout_revokes_tokens(client):  # SR-003
    client.post("/api/v1/auth/register", json=DRIVER)
    tokens = login(client).json()
    headers = bearer(tokens["access_token"])
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]},
                       headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_login_rate_limited(client):  # SR-006
    codes = [login(client, "nobody@x.com", "wrong").status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_register_rate_limited(client):  # SR-006
    codes = [client.post("/api/v1/auth/register", json={"name": "X" * 3, "email": f"u{i}@x.com",
                                                         "phone": f"90000001{i:02d}", "password": "secret1"}).status_code
             for i in range(11)]
    assert codes[:10] == [201] * 10 and codes[10] == 429
