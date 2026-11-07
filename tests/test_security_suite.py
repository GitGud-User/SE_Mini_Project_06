"""Security validation suite (STP section 5.1): SPMS-SR-002 .. SR-006 and SPMS-F-003.

Covers what the module tests do not: forged and tampered JWTs, a full role/endpoint access matrix,
no user enumeration at login, no card data anywhere in the database, and rate-limit behaviour.
SR-001 (TLS 1.2+) is a deployment setting (reverse proxy) and is checked manually - see
docs/testing/Security_Test_Report.md.
"""
import datetime as dt

import jwt
import pytest
from sqlalchemy import inspect

from backend import auth
from backend.db import engine

API = "/api/v1"


def _token(payload, key=auth.SECRET_KEY, algorithm=auth.ALGORITHM):
    return jwt.encode(payload, key, algorithm=algorithm)


def _claims(sub="1", role="ADMIN", minutes=30):
    return {"sub": sub, "role": role, "jti": "t" * 32,
            "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)}


# ---------- TC-SEC-03: JWT expiry and tampering ----------

def test_token_signed_with_another_key_is_rejected(client):  # TC-SEC-03
    forged = _token(_claims(), key="not-the-server-secret-" + "x" * 16)
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_unsigned_alg_none_token_is_rejected(client):  # TC-SEC-03
    unsigned = jwt.encode(_claims(), key=None, algorithm="none")
    r = client.get(f"{API}/audit", headers={"Authorization": f"Bearer {unsigned}"})
    assert r.status_code == 401


def test_expired_token_is_rejected(client):  # TC-SEC-03
    expired = _token(_claims(minutes=-1))
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and "expired" in r.json()["detail"].lower()


def test_editing_the_role_inside_a_token_breaks_it(client, driver_headers):  # TC-SEC-03, TC-AUTH-03
    header, payload, signature = driver_headers["Authorization"].split()[1].split(".")
    claims = jwt.decode(driver_headers["Authorization"].split()[1], options={"verify_signature": False})
    claims["role"] = "ADMIN"
    tampered_payload = _token(claims, key="y" * 32).split(".")[1]
    tampered = ".".join([header, tampered_payload, signature])
    assert client.get(f"{API}/audit", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401


def test_role_comes_from_database_not_token(client, driver_headers):  # TC-SEC-03, TC-AUTH-03
    """Even a validly signed token that claims ADMIN does not make a driver an admin."""
    me = client.get(f"{API}/auth/me", headers=driver_headers).json()
    claims_admin = _token(_claims(sub=str(me["id"]), role="ADMIN"))
    r = client.get(f"{API}/audit", headers={"Authorization": f"Bearer {claims_admin}"})
    assert r.status_code == 403


# ---------- TC-AUTH-03: role / endpoint access matrix (F-003) ----------

ADMIN, ATTENDANT, DRIVER = "ADMIN", "ATTENDANT", "DRIVER"
MATRIX = [  # method, path, json body, roles that are allowed in
    ("GET", "/audit", None, {ADMIN}),
    ("GET", "/admin/outbox", None, {ADMIN}),
    ("GET", "/admin/backups", None, {ADMIN}),
    ("GET", "/reports/summary", None, {ADMIN}),
    ("GET", "/reports/export.csv", None, {ADMIN}),
    ("POST", "/lots", {"name": "Matrix Lot"}, {ADMIN}),
    ("POST", "/payments/waive", {"ticket": "T-999999", "reason": "test"}, {ADMIN}),
    ("GET", "/gate/sessions", None, {ATTENDANT, ADMIN}),
    ("POST", "/gate/exit", {"plate": "KA01AB1234"}, {ATTENDANT, ADMIN}),
    ("GET", "/me/history", None, {DRIVER}),
    ("GET", "/reservations/mine", None, {DRIVER}),
    ("POST", "/vehicles", {"plate": "KA01AB1234", "vehicle_type": "FOUR_WHEELER"}, {DRIVER}),
]


@pytest.mark.parametrize("method,path,body,allowed", MATRIX, ids=[f"{m} {p}" for m, p, _, _ in MATRIX])
def test_access_matrix(client, admin_headers, attendant_headers, driver_headers, method, path, body, allowed):  # TC-AUTH-03, TC-SEC-03
    callers = {None: {}, ADMIN: admin_headers, ATTENDANT: attendant_headers, DRIVER: driver_headers}
    for role, headers in callers.items():
        r = client.request(method, API + path, json=body, headers=headers)
        if role in allowed:
            assert r.status_code not in (401, 403), f"{role} should reach {method} {path}, got {r.status_code}"
        else:
            expected = 401 if role is None else 403
            assert r.status_code == expected, f"{role} must be refused on {method} {path}, got {r.status_code}"


# ---------- TC-SEC-02: passwords and accounts ----------

def test_login_does_not_reveal_which_emails_exist(client, driver_headers):  # TC-SEC-02, TC-AUTH-02
    unknown = client.post(f"{API}/auth/login", json={"email": "nobody@test.com", "password": "secret123"})
    wrong = client.post(f"{API}/auth/login", json={"email": "driver@test.com", "password": "wrong-pass"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_short_password_rejected_at_registration(client):  # TC-SEC-02, TC-AUTH-01
    r = client.post(f"{API}/auth/register", json={"name": "Short", "email": "s@test.com", "phone": "9876500000",
                                                  "password": "123"})
    assert r.status_code == 422


# ---------- TC-SEC-05: no card data stored ----------

def test_no_card_data_columns_anywhere(client):  # TC-SEC-05
    forbidden = ("card_number", "card_no", "pan", "cvv", "cvc", "expiry", "card_holder")
    insp = inspect(engine)
    for table in insp.get_table_names():
        for column in insp.get_columns(table):
            assert column["name"].lower() not in forbidden, f"{table}.{column['name']} looks like card data"


def test_gateway_page_collects_no_card_fields(client, admin_headers, attendant_headers, monkeypatch):  # TC-SEC-05
    start = dt.datetime(2026, 11, 9, 9, 0)
    now = {"t": start}
    monkeypatch.setattr(auth, "utcnow", lambda: now["t"])
    lot = client.post(f"{API}/lots", json={"name": "Card Lot"}, headers=admin_headers).json()
    level = client.post(f"{API}/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"}, headers=admin_headers).json()
    client.post(f"{API}/lots/{lot['id']}/slots", headers=admin_headers,
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 1, "prefix": "A"})
    ticket = client.post(f"{API}/gate/entry", json={"lot_id": lot["id"], "plate": "KA01AB1234"},
                         headers=attendant_headers).json()["ticket"]
    now["t"] = start + dt.timedelta(minutes=45)
    order = client.post(f"{API}/payments/online/checkout", json={"ticket": ticket}, headers=attendant_headers).json()
    page = client.get(order["checkout_url"]).text.lower()
    assert "<input" not in page, "the mock gateway must not ask for card numbers"


# ---------- TC-SEC-04: input handling ----------

@pytest.mark.parametrize("plate", ["<script>", "KA01'; DROP TABLE slots;--", "../../etc/passwd", "A" * 40])
def test_hostile_plates_rejected_at_gate(client, attendant_headers, plate):  # TC-SEC-04
    r = client.post(f"{API}/gate/entry", json={"lot_id": 1, "plate": plate}, headers=attendant_headers)
    assert r.status_code == 422


def test_receipt_number_cannot_walk_paths(client, attendant_headers):  # TC-SEC-04
    r = client.get(f"{API}/receipts/..%2F..%2Fspms.db", headers=attendant_headers)
    assert r.status_code in (404, 422)


# ---------- TC-SEC-06: rate limiting ----------

def test_rate_limit_tells_client_when_to_retry(client):  # TC-SEC-06
    for _ in range(10):
        client.post(f"{API}/auth/login", json={"email": "x@test.com", "password": "wrong-pass"})
    r = client.post(f"{API}/auth/login", json={"email": "x@test.com", "password": "wrong-pass"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
