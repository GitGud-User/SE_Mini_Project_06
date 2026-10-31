"""Tests for SPMS-SR-004 (input validation / output encoding) and SPMS-SR-007 (append-only audit log)."""
import pytest

from backend import audit
from backend.db import SessionLocal


@pytest.fixture
def lot(client, admin_headers):
    lot = client.post("/api/v1/lots", json={"name": "Audit Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots",
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 2, "prefix": "A"},
                headers=admin_headers)
    return lot


def test_tariff_change_is_audited(client, admin_headers, lot):  # SR-007
    client.put(f"/api/v1/lots/{lot['id']}/tariffs", json={"vehicle_type": "EV", "hourly_rate": 55},
               headers=admin_headers)
    entries = client.get("/api/v1/audit?action=TARIFF_CHANGE", headers=admin_headers).json()
    assert len(entries) == 1
    e = entries[0]
    assert e["user_email"] == "admin@spms.local" and e["role"] == "ADMIN"
    assert e["before"]["hourly_rate"] == 20.0 and e["after"]["hourly_rate"] == 55.0


def test_maintenance_and_lot_update_are_audited(client, admin_headers, attendant_headers, lot):
    slot_id = client.get(f"/api/v1/lots/{lot['id']}/slots").json()[0]["id"]
    client.patch(f"/api/v1/slots/{slot_id}/maintenance", json={"under_maintenance": True}, headers=attendant_headers)
    client.patch(f"/api/v1/lots/{lot['id']}", json={"is_active": False}, headers=admin_headers)
    actions = [e["action"] for e in client.get("/api/v1/audit", headers=admin_headers).json()]
    assert actions == ["LOT_UPDATE", "SLOT_MAINTENANCE"]


def test_audit_is_admin_only(client, attendant_headers, driver_headers):
    assert client.get("/api/v1/audit", headers=attendant_headers).status_code == 403
    assert client.get("/api/v1/audit", headers=driver_headers).status_code == 403


def test_audit_rows_cannot_be_changed_via_api(client, admin_headers, lot):  # SR-007
    client.put(f"/api/v1/lots/{lot['id']}/tariffs", json={"vehicle_type": "EV", "hourly_rate": 55},
               headers=admin_headers)
    entry_id = client.get("/api/v1/audit", headers=admin_headers).json()[0]["id"]
    for method in ("put", "patch", "delete"):
        response = getattr(client, method)(f"/api/v1/audit/{entry_id}", headers=admin_headers)
        assert response.status_code == 405


def test_audit_rows_cannot_be_changed_in_code(client, admin_headers, lot):  # SR-007 (ORM guard)
    client.put(f"/api/v1/lots/{lot['id']}/tariffs", json={"vehicle_type": "EV", "hourly_rate": 55},
               headers=admin_headers)
    with SessionLocal() as db:
        row = db.query(audit.AuditLog).first()
        row.reason = "tampered"
        with pytest.raises(PermissionError):
            db.commit()
        db.rollback()
        db.delete(db.query(audit.AuditLog).first())
        with pytest.raises(PermissionError):
            db.commit()


@pytest.mark.parametrize("name", ["<script>alert(1)</script>", "Lot <b>"])
def test_markup_rejected_in_free_text(client, admin_headers, name):  # SR-004
    assert client.post("/api/v1/lots", json={"name": name}, headers=admin_headers).status_code == 422
    bad_driver = {"name": name, "email": "x@y.com", "phone": "9000000099", "password": "secret1"}
    assert client.post("/api/v1/auth/register", json=bad_driver).status_code == 422


@pytest.mark.parametrize("payload", ["' OR '1'='1", "admin@spms.local' --", "\"; DROP TABLE users; --"])
def test_sql_injection_strings_are_harmless(client, payload):  # SR-004: parameterised ORM queries
    response = client.post("/api/v1/auth/login", json={"email": payload, "password": payload})
    assert response.status_code == 401
    assert client.post("/api/v1/auth/login",
                       json={"email": "admin@spms.local", "password": "admin123"}).status_code == 200


def test_slot_prefix_must_be_alphanumeric(client, admin_headers, lot):
    level_id = client.get(f"/api/v1/lots/{lot['id']}/levels").json()[0]["id"]
    body = {"level_id": level_id, "slot_type": "EV", "count": 1, "prefix": "<i>"}
    assert client.post(f"/api/v1/lots/{lot['id']}/slots", json=body, headers=admin_headers).status_code == 422


def test_security_headers_present(client):
    headers = client.get("/health").headers
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"


def test_ui_escapes_server_data(client):  # SR-004 output encoding
    js = client.get("/ui/app.js").text
    assert "esc(value)" in js and "SPMS.esc(u.name)" in js
    for page in ("index.html", "gate.html", "admin.html"):
        assert "SPMS.esc(" in client.get(f"/ui/{page}").text
