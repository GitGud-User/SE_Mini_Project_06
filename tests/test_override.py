"""Tests for SPMS-F-041: attendant allocation override (TC-ALC-02)."""
import pytest


@pytest.fixture
def lot(client, admin_headers):
    lot = client.post("/api/v1/lots", json={"name": "Override Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    for prefix, slot_type, count in (("A", "FOUR_WHEELER", 3), ("M", "TWO_WHEELER", 1)):
        client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                    json={"level_id": level["id"], "slot_type": slot_type, "count": count, "prefix": prefix})
    return lot


@pytest.fixture
def ticket(client, attendant_headers, lot):
    return client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": "KA01AB1234"},
                       headers=attendant_headers).json()["ticket"]


def move(client, headers, ticket, slot, reason="Slot A-1 blocked by debris"):
    return client.post(f"/api/v1/gate/sessions/{ticket}/reassign", json={"slot_code": slot, "reason": reason},
                       headers=headers)


def test_override_to_free_compatible_slot_is_audited(client, attendant_headers, admin_headers, lot, ticket):  # TC-ALC-02
    response = move(client, attendant_headers, ticket, "A-3")
    assert response.status_code == 200 and response.json()["slot_code"] == "A-3"
    statuses = {s["code"]: s["status"] for s in client.get(f"/api/v1/lots/{lot['id']}/slots").json()}
    assert statuses["A-1"] == "FREE" and statuses["A-3"] == "OCCUPIED"
    entry = client.get("/api/v1/audit?action=ALLOCATION_OVERRIDE", headers=admin_headers).json()[0]
    assert entry["before"] == {"slot": "A-1"} and entry["after"] == {"slot": "A-3"}
    assert entry["reason"] == "Slot A-1 blocked by debris" and entry["role"] == "ATTENDANT"


def test_override_rejects_bad_targets(client, attendant_headers, admin_headers, lot, ticket):
    client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": "KA02CD5678"}, headers=attendant_headers)
    assert move(client, attendant_headers, ticket, "A-2").status_code == 409   # occupied
    assert move(client, attendant_headers, ticket, "M-1").status_code == 409   # two-wheeler slot
    assert move(client, attendant_headers, ticket, "A-1").status_code == 409   # same slot
    assert move(client, attendant_headers, ticket, "Z-9").status_code == 404   # no such slot
    assert move(client, attendant_headers, ticket, "A-3", reason="x").status_code == 422  # reason too short
    assert client.get("/api/v1/audit?action=ALLOCATION_OVERRIDE", headers=admin_headers).json() == []


def test_drivers_cannot_override(client, driver_headers, ticket):
    assert move(client, driver_headers, ticket, "A-3").status_code == 403
