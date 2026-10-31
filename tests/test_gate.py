"""Tests for SPMS-F-050 .. F-053 and F-060 (TC-GATE-01 .. TC-GATE-04, TC-BILL-01)."""
import datetime as dt

import pytest

from backend import gate

T0 = dt.datetime(2026, 1, 10, 9, 0, 0)


@pytest.fixture
def clock(monkeypatch):
    """Controllable clock: set clock['now'] to move time."""
    state = {"now": T0}
    monkeypatch.setattr(gate, "_now", lambda: state["now"])
    return state


@pytest.fixture
def lot(client, admin_headers):
    """A lot with 2 four-wheeler slots (A-1 nearest) and a 30/hr tariff."""
    lot = client.post("/api/v1/lots", json={"name": "Gate Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots",
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 2, "prefix": "A"},
                headers=admin_headers)
    client.put(f"/api/v1/lots/{lot['id']}/tariffs",
               json={"vehicle_type": "FOUR_WHEELER", "hourly_rate": 30, "grace_minutes": 15, "daily_cap": 250},
               headers=admin_headers)
    return lot


def enter(client, headers, lot, plate="KA01AB1234", **extra):
    return client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": plate, **extra}, headers=headers)


def test_entry_allocates_nearest_slot(client, attendant_headers, lot, clock):  # TC-GATE-01/02
    response = enter(client, attendant_headers, lot, plate="ka 01 ab 1234")
    assert response.status_code == 201
    body = response.json()
    assert body["plate"] == "KA01AB1234" and body["slot_code"] == "A-1" and body["ticket"] == "T-000001"
    slots = client.get(f"/api/v1/lots/{lot['id']}/slots").json()
    assert slots[0]["status"] == "OCCUPIED"


def test_invalid_plate_rejected(client, attendant_headers, lot):
    assert enter(client, attendant_headers, lot, plate="HELLO").status_code == 422


def test_same_vehicle_cannot_enter_twice(client, attendant_headers, lot, clock):
    enter(client, attendant_headers, lot)
    assert enter(client, attendant_headers, lot).status_code == 409


def test_lot_full(client, attendant_headers, lot, clock):  # TC-GATE-02 (no slot)
    enter(client, attendant_headers, lot, plate="KA01AB0001")
    enter(client, attendant_headers, lot, plate="KA01AB0002")
    response = enter(client, attendant_headers, lot, plate="KA01AB0003")
    assert response.status_code == 409 and "full" in response.json()["detail"]


def test_driver_cannot_use_gate(client, driver_headers, lot):
    assert enter(client, driver_headers, lot).status_code == 403


def test_exit_computes_fee_and_frees_slot(client, attendant_headers, lot, clock):  # TC-GATE-03 + TC-BILL-01
    enter(client, attendant_headers, lot)
    clock["now"] = T0 + dt.timedelta(minutes=125)  # 2h05m -> 3 started hours x 30
    blocked = client.post("/api/v1/gate/exit", json={"plate": "KA01AB1234"}, headers=attendant_headers)
    assert blocked.status_code == 402  # F-063: unpaid fee blocks the exit
    due = client.get("/api/v1/payments/due?plate=KA01AB1234", headers=attendant_headers).json()
    assert due["amount_due"] == 90.0
    client.post("/api/v1/payments/cash", json={"ticket": due["ticket"], "amount": 90}, headers=attendant_headers)
    response = client.post("/api/v1/gate/exit", json={"plate": "KA01AB1234"}, headers=attendant_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["minutes"] == 125 and body["fee"] == 90.0 and body["status"] == "CLOSED"
    assert body["receipt_no"] == "RCPT-000001"
    assert client.get(f"/api/v1/lots/{lot['id']}/slots").json()[0]["status"] == "FREE"


def test_exit_within_grace_is_free(client, attendant_headers, lot, clock):
    enter(client, attendant_headers, lot)
    clock["now"] = T0 + dt.timedelta(minutes=10)
    assert client.post("/api/v1/gate/exit", json={"plate": "KA01AB1234"},
                       headers=attendant_headers).json()["fee"] == 0.0


def test_exit_without_entry_is_404(client, attendant_headers, lot):
    assert client.post("/api/v1/gate/exit", json={"plate": "KA01AB9999"},
                       headers=attendant_headers).status_code == 404


def test_valid_reservation_qr_accepted(client, attendant_headers, lot, clock):  # TC-GATE-04
    qr = gate.make_reservation_qr(lot["id"], "KA01AB1234", start=T0 + dt.timedelta(minutes=5))
    assert enter(client, attendant_headers, lot, reservation_qr=qr).status_code == 201


@pytest.mark.parametrize("case", ["tampered", "wrong_plate", "too_late"])
def test_invalid_reservation_qr_rejected(client, attendant_headers, lot, clock, case):  # TC-GATE-04
    plate, start = "KA01AB1234", T0
    if case == "wrong_plate":
        plate = "KA02CD5678"
    if case == "too_late":
        start = T0 - dt.timedelta(minutes=40)
    qr = gate.make_reservation_qr(lot["id"], plate, start=start)
    if case == "tampered":
        qr = qr[:-3] + ("aaa" if not qr.endswith("aaa") else "bbb")
    assert enter(client, attendant_headers, lot, reservation_qr=qr).status_code == 400


def test_sessions_list(client, attendant_headers, lot, clock):
    enter(client, attendant_headers, lot)
    sessions = client.get("/api/v1/gate/sessions?only_open=true", headers=attendant_headers).json()
    assert len(sessions) == 1 and sessions[0]["status"] == "OPEN"
