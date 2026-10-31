"""Tests for SPMS-F-030 (reserve), F-031 (no double booking) and F-040 (auto-allocation).
TC-RES-01, TC-RES-02, TC-ALC-01 plus gate check-in with a reservation QR."""
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

from backend import auth, reservations
from backend.db import SessionLocal

T0 = dt.datetime(2026, 11, 2, 9, 0, 0)


@pytest.fixture
def clock(monkeypatch):
    """Freeze 'now' for every module (they all ask auth.utcnow)."""
    state = {"now": T0}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])
    return state


def make_lot(client, admin_headers, layout=(("A", "FOUR_WHEELER", 3), ("H", "ACCESSIBLE", 1), ("E", "EV", 1))):
    lot = client.post("/api/v1/lots", json={"name": "Res Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    for prefix, slot_type, count in layout:
        client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                    json={"level_id": level["id"], "slot_type": slot_type, "count": count, "prefix": prefix})
    return lot


@pytest.fixture
def lot(client, admin_headers):
    return make_lot(client, admin_headers)


def add_vehicle(client, headers, plate="KA01AB1234", vehicle_type="FOUR_WHEELER", accessible=False):
    response = client.post("/api/v1/vehicles", headers=headers,
                           json={"plate": plate, "vehicle_type": vehicle_type, "needs_accessible": accessible})
    assert response.status_code == 201, response.text
    return response.json()


def iso(t):
    return t.isoformat() + "Z"


def reserve(client, headers, lot, vehicle, start, minutes=120):
    return client.post("/api/v1/reservations", headers=headers, json={
        "vehicle_id": vehicle["id"], "lot_id": lot["id"], "start": iso(start),
        "end": iso(start + dt.timedelta(minutes=minutes))})


def test_register_vehicle_once(client, driver_headers):
    add_vehicle(client, driver_headers)
    again = client.post("/api/v1/vehicles", headers=driver_headers, json={"plate": "KA01AB1234"})
    assert again.status_code == 409
    assert len(client.get("/api/v1/vehicles", headers=driver_headers).json()) == 1


def test_reserve_returns_slot_advance_and_qr(client, driver_headers, lot, clock):  # TC-RES-01
    car = add_vehicle(client, driver_headers)
    response = reserve(client, driver_headers, lot, car, T0 + dt.timedelta(hours=2))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["code"] == "R-000001" and body["slot_code"] == "A-1" and body["status"] == "CONFIRMED"
    assert body["advance_amount"] == 40.0 and body["advance_ref"] == "ADV-000001"  # 2 h x default 20/hr
    assert body["qr_token"]
    assert client.get("/api/v1/reservations/mine", headers=driver_headers).json()[0]["code"] == "R-000001"


@pytest.mark.parametrize("start_offset, minutes", [
    (dt.timedelta(hours=-1), 60),      # in the past
    (dt.timedelta(days=8), 60),        # more than 7 days ahead
    (dt.timedelta(hours=1), 20),       # shorter than 30 min
    (dt.timedelta(hours=1), 25 * 60),  # longer than 24 h
])
def test_reservation_time_rules(client, driver_headers, lot, clock, start_offset, minutes):
    car = add_vehicle(client, driver_headers)
    assert reserve(client, driver_headers, lot, car, T0 + start_offset, minutes).status_code == 422


def test_only_drivers_reserve_their_own_vehicles(client, driver_headers, attendant_headers, lot, clock):
    car = add_vehicle(client, driver_headers)
    assert reserve(client, attendant_headers, lot, car, T0 + dt.timedelta(hours=1)).status_code == 403
    client.post("/api/v1/auth/register", json={"name": "Other", "email": "o@x.com", "phone": "9111111111",
                                                "password": "secret1"})
    other = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={
        "email": "o@x.com", "password": "secret1"}).json()["access_token"]}
    assert reserve(client, other, lot, car, T0 + dt.timedelta(hours=1)).status_code == 404
    code = reserve(client, driver_headers, lot, car, T0 + dt.timedelta(hours=1)).json()["code"]
    assert client.get(f"/api/v1/reservations/{code}", headers=other).status_code == 404
    assert client.get(f"/api/v1/lots/{lot['id']}/reservations", headers=attendant_headers).json()[0]["code"] == code


def test_overlapping_reservations_get_different_slots(client, driver_headers, lot, clock):  # TC-RES-02
    cars = [add_vehicle(client, driver_headers, plate=f"KA01AB000{i}") for i in range(4)]
    start = T0 + dt.timedelta(hours=1)
    codes = [reserve(client, driver_headers, lot, c, start) for c in cars]
    assert [r.json().get("slot_code") for r in codes[:3]] == ["A-1", "A-2", "A-3"]
    assert codes[3].status_code == 409  # all three car slots taken for that time
    later = reserve(client, driver_headers, lot, cars[3], start + dt.timedelta(hours=3))
    assert later.json()["slot_code"] == "A-1"  # no overlap -> slot can be reused


def test_same_vehicle_cannot_double_book(client, driver_headers, lot, clock):
    car = add_vehicle(client, driver_headers)
    reserve(client, driver_headers, lot, car, T0 + dt.timedelta(hours=1))
    assert reserve(client, driver_headers, lot, car, T0 + dt.timedelta(hours=2)).status_code == 409


def test_50_concurrent_requests_for_last_slot(client, admin_headers, driver_headers, clock):  # TC-RES-02
    lot = make_lot(client, admin_headers, layout=(("A", "FOUR_WHEELER", 1),))
    for i in range(50):
        add_vehicle(client, driver_headers, plate=f"KA02CD{i:04d}")
    with SessionLocal() as db:
        user = db.query(auth.User).filter_by(email="driver@test.com").one()
        vehicle_ids = [v.id for v in db.query(reservations.Vehicle).all()]
    start = T0 + dt.timedelta(hours=1)

    def attempt(vehicle_id):
        with SessionLocal() as db:
            data = reservations.ReservationIn(vehicle_id=vehicle_id, lot_id=lot["id"], start=start,
                                              end=start + dt.timedelta(hours=1))
            try:
                reservations.create_reservation(db, db.get(auth.User, user.id), data)
                return "ok"
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(attempt, vehicle_ids))
    assert results.count("ok") == 1 and results.count(409) == 49


def test_allocation_matches_type_and_accessibility(client, driver_headers, lot, clock):  # TC-ALC-01
    start = T0 + dt.timedelta(hours=1)
    ev = add_vehicle(client, driver_headers, plate="KA03EV0001", vehicle_type="EV")
    needs = add_vehicle(client, driver_headers, plate="KA03AC0001", accessible=True)
    assert reserve(client, driver_headers, lot, ev, start).json()["slot_code"] == "E-1"
    assert reserve(client, driver_headers, lot, needs, start).json()["slot_code"] == "H-1"


def test_walk_in_avoids_slot_reserved_soon(client, driver_headers, attendant_headers, lot, clock):  # F-031/F-040
    car = add_vehicle(client, driver_headers)
    assert reserve(client, driver_headers, lot, car, T0 + dt.timedelta(minutes=30)).json()["slot_code"] == "A-1"
    walk_in = client.post("/api/v1/gate/entry", headers=attendant_headers,
                          json={"lot_id": lot["id"], "plate": "KA09ZZ9999"})
    assert walk_in.json()["slot_code"] == "A-2"


def test_no_show_stops_blocking_after_15_minutes(client, driver_headers, attendant_headers, lot, clock):
    car = add_vehicle(client, driver_headers)
    reserve(client, driver_headers, lot, car, T0 + dt.timedelta(minutes=30))
    clock["now"] = T0 + dt.timedelta(minutes=50)  # start + 20 min, car never came
    walk_in = client.post("/api/v1/gate/entry", headers=attendant_headers,
                          json={"lot_id": lot["id"], "plate": "KA09ZZ9999"})
    assert walk_in.json()["slot_code"] == "A-1"


def test_gate_check_in_with_reservation_qr(client, driver_headers, attendant_headers, lot, clock):
    other = add_vehicle(client, driver_headers, plate="KA05XY0001")
    reserve(client, driver_headers, lot, other, T0 + dt.timedelta(minutes=20))  # takes A-1
    car = add_vehicle(client, driver_headers)
    r = reserve(client, driver_headers, lot, car, T0 + dt.timedelta(minutes=20)).json()
    assert r["slot_code"] == "A-2"
    clock["now"] = T0 + dt.timedelta(minutes=25)
    entry = client.post("/api/v1/gate/entry", headers=attendant_headers,
                        json={"lot_id": lot["id"], "plate": "KA01AB1234", "reservation_qr": r["qr_token"]})
    assert entry.status_code == 201 and entry.json()["slot_code"] == "A-2"
    status = client.get(f"/api/v1/reservations/{r['code']}", headers=driver_headers).json()["status"]
    assert status == "CHECKED_IN"
    again = client.post("/api/v1/gate/entry", headers=attendant_headers,
                        json={"lot_id": lot["id"], "plate": "KA01AB1234", "reservation_qr": r["qr_token"]})
    assert again.status_code == 409


def test_qr_of_other_plate_rejected(client, driver_headers, attendant_headers, lot, clock):
    car = add_vehicle(client, driver_headers)
    r = reserve(client, driver_headers, lot, car, T0 + dt.timedelta(minutes=10)).json()
    entry = client.post("/api/v1/gate/entry", headers=attendant_headers,
                        json={"lot_id": lot["id"], "plate": "KA07QQ7777", "reservation_qr": r["qr_token"]})
    assert entry.status_code == 400


def test_availability_counts_reserved_slots(client, driver_headers, lot, clock):
    before = client.get(f"/api/v1/lots/{lot['id']}/availability").json()["free_by_type"]["FOUR_WHEELER"]
    car = add_vehicle(client, driver_headers)
    reserve(client, driver_headers, lot, car, T0 + dt.timedelta(minutes=10))
    after = client.get(f"/api/v1/lots/{lot['id']}/availability").json()["free_by_type"]["FOUR_WHEELER"]
    assert (before, after) == (3, 2)
