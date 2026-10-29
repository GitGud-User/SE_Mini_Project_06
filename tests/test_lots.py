"""Tests for SPMS-F-010 .. F-012 (TC-LOT-01 .. TC-LOT-03)."""
from backend import lots
from backend.db import SessionLocal


def make_lot(client, admin_headers, name="Test Lot"):
    lot = client.post("/api/v1/lots", json={"name": name, "address": "Somewhere"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "Ground"},
                        headers=admin_headers).json()
    return lot, level


def test_admin_creates_lot_and_level(client, admin_headers):  # TC-LOT-01
    lot, level = make_lot(client, admin_headers)
    assert lot["is_active"] is True and level["floor_no"] == 0
    assert any(item["name"] == "Test Lot" for item in client.get("/api/v1/lots").json())


def test_duplicate_lot_name_rejected(client, admin_headers):
    make_lot(client, admin_headers)
    response = client.post("/api/v1/lots", json={"name": "Test Lot"}, headers=admin_headers)
    assert response.status_code == 409


def test_driver_cannot_create_lot(client, driver_headers):  # F-003 applied to lots
    response = client.post("/api/v1/lots", json={"name": "Nope"}, headers=driver_headers)
    assert response.status_code == 403


def test_update_lot(client, admin_headers):
    lot, _ = make_lot(client, admin_headers)
    response = client.patch(f"/api/v1/lots/{lot['id']}", json={"is_active": False}, headers=admin_headers)
    assert response.json()["is_active"] is False


def test_bulk_create_slots_with_types(client, admin_headers):  # TC-LOT-02
    lot, level = make_lot(client, admin_headers)
    body = {"level_id": level["id"], "slot_type": "ev", "count": 3, "prefix": "E"}
    created = client.post(f"/api/v1/lots/{lot['id']}/slots", json=body, headers=admin_headers)
    assert created.status_code == 201
    assert [s["code"] for s in created.json()] == ["E-1", "E-2", "E-3"]
    assert all(s["slot_type"] == "EV" and s["status"] == "FREE" for s in created.json())
    more = client.post(f"/api/v1/lots/{lot['id']}/slots", json=dict(body, count=1), headers=admin_headers)
    assert more.json()[0]["code"] == "E-4"


def test_invalid_slot_type_rejected(client, admin_headers):
    lot, level = make_lot(client, admin_headers)
    body = {"level_id": level["id"], "slot_type": "TRUCK", "count": 1}
    assert client.post(f"/api/v1/lots/{lot['id']}/slots", json=body, headers=admin_headers).status_code == 422


def test_maintenance_slot_not_allocated(client, admin_headers, attendant_headers):  # TC-LOT-03
    lot, level = make_lot(client, admin_headers)
    body = {"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 2}
    slots = client.post(f"/api/v1/lots/{lot['id']}/slots", json=body, headers=admin_headers).json()
    nearest = slots[0]
    response = client.patch(f"/api/v1/slots/{nearest['id']}/maintenance", json={"under_maintenance": True},
                            headers=attendant_headers)
    assert response.json()["status"] == "MAINTENANCE"
    with SessionLocal() as db:
        best = lots.find_best_free_slot(db, lot["id"], "FOUR_WHEELER")
    assert best.id == slots[1]["id"]
    back = client.patch(f"/api/v1/slots/{nearest['id']}/maintenance", json={"under_maintenance": False},
                        headers=attendant_headers)
    assert back.json()["status"] == "FREE"


def test_tariff_upsert_and_default(client, admin_headers):
    lot, _ = make_lot(client, admin_headers)
    body = {"vehicle_type": "FOUR_WHEELER", "hourly_rate": 50, "daily_cap": 300}
    assert client.put(f"/api/v1/lots/{lot['id']}/tariffs", json=body, headers=admin_headers).status_code == 200
    tariffs = {t["vehicle_type"]: t for t in client.get(f"/api/v1/lots/{lot['id']}/tariffs").json()}
    assert tariffs["FOUR_WHEELER"]["hourly_rate"] == 50
    assert tariffs["EV"]["hourly_rate"] == lots.DEFAULT_HOURLY_RATE


def test_unknown_lot_is_404(client):
    assert client.get("/api/v1/lots/999/slots").status_code == 404


def test_seed_script_is_idempotent():
    from backend import seed
    first = seed.seed()
    second = seed.seed()
    assert first.id == second.id
    with SessionLocal() as db:
        assert db.query(lots.Slot).filter_by(lot_id=first.id).count() == 34
