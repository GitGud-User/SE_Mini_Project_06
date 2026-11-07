"""System validation tests (STP section 5, level "System tests").

Each test drives one complete user journey through the public HTTP API only, exactly as the three web
pages do (driver, gate attendant, admin), and checks the result at every step. Unlike the module tests,
nothing here calls backend functions directly, so these tests prove the parts work together.

Journeys:
  SYS-01  driver reserves -> QR check-in -> exit -> pays the rest -> receipt -> history -> admin report
  SYS-02  walk-in inside the grace period leaves free; duplicate entry is refused
  SYS-03  the last slot: second driver refused, first driver cancels (refund), second driver books it
  SYS-04  unpaid exit is blocked, admin waives the fee, the waiver is in the audit log
  SYS-05  a slot under maintenance is never allocated; the lot reports full
  SYS-06  online (UPI) payment through the signed mock gateway; forged confirmation rejected
"""
import csv
import datetime as dt
import io

import pytest

from backend import auth

T0 = dt.datetime(2026, 11, 9, 9, 0, 0)  # a Monday morning, UTC


@pytest.fixture
def clock(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])
    return state


def build_lot(client, admin_headers, cars=3, grace=15):
    """Admin sets up a lot the way the Admin page does: lot -> level -> slots -> tariff."""
    lot = client.post("/api/v1/lots", json={"name": "System Test Lot", "address": "MG Road"}, headers=admin_headers)
    assert lot.status_code == 201, lot.text
    lot = lot.json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "Ground"},
                        headers=admin_headers).json()
    slots = client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                        json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": cars, "prefix": "A"})
    assert slots.status_code == 201, slots.text
    tariff = client.put(f"/api/v1/lots/{lot['id']}/tariffs", headers=admin_headers,
                        json={"vehicle_type": "FOUR_WHEELER", "hourly_rate": 30, "grace_minutes": grace,
                              "daily_cap": 250})
    assert tariff.status_code == 200, tariff.text
    return lot


def new_driver(client, email, phone):
    r = client.post("/api/v1/auth/register", json={"name": "Asha", "email": email, "phone": phone,
                                                   "password": "secret123"})
    assert r.status_code == 201, r.text
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "secret123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def add_vehicle(client, headers, plate):
    r = client.post("/api/v1/vehicles", json={"plate": plate, "vehicle_type": "FOUR_WHEELER"}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def reserve(client, headers, lot, vehicle_id, start, hours=1):
    return client.post("/api/v1/reservations", headers=headers, json={
        "vehicle_id": vehicle_id, "lot_id": lot["id"],
        "start": start.isoformat() + "Z", "end": (start + dt.timedelta(hours=hours)).isoformat() + "Z"})


def enter(client, headers, lot, plate, **extra):
    return client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": plate, **extra}, headers=headers)


def leave(client, headers, plate):
    return client.post("/api/v1/gate/exit", json={"plate": plate}, headers=headers)


def test_sys01_reservation_to_report(client, admin_headers, attendant_headers, clock):  # TC-RES-01, TC-ALC-01, TC-GATE-02, TC-GATE-04, TC-PAY-01, TC-PAY-02, TC-PAY-03, TC-HIS-01, TC-RPT-01, TC-NOT-01
    lot = build_lot(client, admin_headers)
    driver = new_driver(client, "asha@example.com", "9123456780")
    vehicle = add_vehicle(client, driver, "KA01AB1234")

    booking = reserve(client, driver, lot, vehicle, T0 + dt.timedelta(hours=1))      # 10:00-11:00
    assert booking.status_code == 201, booking.text
    booking = booking.json()
    assert booking["status"] == "CONFIRMED" and booking["advance_amount"] == 30.0 and booking["slot_code"]
    mine = client.get("/api/v1/reservations/mine", headers=driver).json()
    qr = mine[0]["qr_token"]
    assert qr, "a confirmed booking must carry a QR token"

    clock["now"] = T0 + dt.timedelta(minutes=55)                                     # arrives 09:55
    entry = enter(client, attendant_headers, lot, "KA01AB1234", reservation_qr=qr)
    assert entry.status_code == 201, entry.text
    assert entry.json()["slot_code"] == booking["slot_code"], "car must go to its reserved slot"

    clock["now"] = T0 + dt.timedelta(minutes=55 + 100)                               # 100 min = 2 started hours
    blocked = leave(client, attendant_headers, "KA01AB1234")
    assert blocked.status_code == 402, "advance covers 1 hour only, the rest must be paid"
    due = client.get("/api/v1/payments/due?plate=KA01AB1234", headers=attendant_headers).json()
    assert due["total_fee"] == 60.0 and due["advance"] == 30.0 and due["amount_due"] == 30.0
    paid = client.post("/api/v1/payments/cash", json={"ticket": due["ticket"], "amount": 30},
                       headers=attendant_headers)
    assert paid.status_code == 201
    done = leave(client, attendant_headers, "KA01AB1234")
    assert done.status_code == 200 and done.json()["fee"] == 60.0
    receipt_no = done.json()["receipt_no"]
    pdf = client.get(f"/api/v1/receipts/{receipt_no}/pdf", headers=attendant_headers)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    history = client.get("/api/v1/me/history", headers=driver).json()
    assert [s["plate"] for s in history["sessions"]] == ["KA01AB1234"]
    assert history["sessions"][0]["receipt_no"] == receipt_no
    assert history["receipts"][0]["receipt_no"] == receipt_no

    day = T0.date().isoformat()
    report = client.get(f"/api/v1/reports/summary?date_from={day}&date_to={day}", headers=admin_headers).json()
    assert report["totals"]["entries"] == 1
    assert report["totals"]["advances"] == 30.0 and report["totals"]["exit_payments"] == 30.0
    assert report["totals"]["revenue"] == 60.0

    outbox = client.get("/api/v1/admin/outbox", headers=admin_headers).json()
    assert any(m["kind"] == "CONFIRMED" and m["to"] == "asha@example.com" for m in outbox)


def test_sys02_walk_in_grace_and_duplicate(client, admin_headers, attendant_headers, clock):  # TC-GATE-01, TC-GATE-03, TC-GATE-04, TC-PAY-01, TC-AVL-01
    lot = build_lot(client, admin_headers)
    first = enter(client, attendant_headers, lot, "KA05MN4321")
    assert first.status_code == 201
    assert enter(client, attendant_headers, lot, "KA05MN4321").status_code == 409, "same car cannot enter twice"
    free = client.get(f"/api/v1/lots/{lot['id']}/availability").json()
    assert free["free_by_type"]["FOUR_WHEELER"] == 2

    clock["now"] = T0 + dt.timedelta(minutes=15)                                     # exactly the grace period
    out = leave(client, attendant_headers, "KA05MN4321")
    assert out.status_code == 200 and out.json()["fee"] == 0.0
    assert client.get(f"/api/v1/lots/{lot['id']}/availability").json()["free_by_type"]["FOUR_WHEELER"] == 3


def test_sys03_last_slot_cancel_and_rebook(client, admin_headers, clock):  # TC-RES-02, TC-RES-03, TC-NOT-01
    lot = build_lot(client, admin_headers, cars=1)
    asha = new_driver(client, "asha@example.com", "9123456780")
    ravi = new_driver(client, "ravi@example.com", "9123456781")
    start = T0 + dt.timedelta(hours=5)
    first = reserve(client, asha, lot, add_vehicle(client, asha, "KA01AB1234"), start)
    assert first.status_code == 201
    ravi_car = add_vehicle(client, ravi, "KA02CD5678")
    assert reserve(client, ravi, lot, ravi_car, start).status_code == 409, "no double booking of the last slot"

    cancel = client.post(f"/api/v1/reservations/{first.json()['code']}/cancel", headers=asha)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED" and cancel.json()["refund_amount"] == 30.0  # >= 1 h before: full

    again = reserve(client, ravi, lot, ravi_car, start)
    assert again.status_code == 201 and again.json()["slot_code"] == first.json()["slot_code"]


def test_sys04_unpaid_exit_waiver_is_audited(client, admin_headers, attendant_headers, clock):  # TC-PAY-04, TC-SEC-07
    lot = build_lot(client, admin_headers)
    ticket = enter(client, attendant_headers, lot, "KA09XY1234").json()["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=40)
    assert leave(client, attendant_headers, "KA09XY1234").status_code == 402
    refused = client.post("/api/v1/payments/waive", json={"ticket": ticket, "reason": "Ambulance"},
                          headers=attendant_headers)
    assert refused.status_code == 403, "only an admin may waive a fee"
    waived = client.post("/api/v1/payments/waive", json={"ticket": ticket, "reason": "Ambulance on duty"},
                         headers=admin_headers)
    assert waived.status_code == 201
    assert leave(client, attendant_headers, "KA09XY1234").status_code == 200
    audit = client.get("/api/v1/audit", headers=admin_headers).json()
    assert any("WAIVE" in row["action"] and "Ambulance on duty" in (row.get("reason") or "") for row in audit)


def test_sys05_maintenance_slot_never_allocated(client, admin_headers, attendant_headers, clock):  # TC-LOT-01, TC-LOT-03, TC-ALC-01, TC-AVL-01
    lot = build_lot(client, admin_headers, cars=2)
    slots = client.get(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers).json()
    nearest = slots[0]
    r = client.patch(f"/api/v1/slots/{nearest['id']}/maintenance", json={"under_maintenance": True},
                     headers=admin_headers)
    assert r.status_code == 200
    first = enter(client, attendant_headers, lot, "KA01AA0001")
    assert first.status_code == 201 and first.json()["slot_code"] != nearest["code"]
    assert enter(client, attendant_headers, lot, "KA01AA0002").status_code == 409, "lot is full"
    assert client.get(f"/api/v1/lots/{lot['id']}/availability").json()["free_by_type"]["FOUR_WHEELER"] == 0


def test_sys06_online_payment_and_csv(client, admin_headers, attendant_headers, clock):  # TC-PAY-02, TC-PAY-03, TC-RPT-02, TC-SEC-05
    lot = build_lot(client, admin_headers)
    ticket = enter(client, attendant_headers, lot, "KA03EF7788").json()["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=50)
    assert leave(client, attendant_headers, "KA03EF7788").status_code == 402
    order = client.post("/api/v1/payments/online/checkout", json={"ticket": ticket}, headers=attendant_headers).json()
    forged = client.post("/api/v1/payments/online/verify",
                         json={"order_id": order["order_id"], "payment_id": "pay_fake", "signature": "0" * 64})
    assert forged.status_code == 400, "a confirmation without a valid signature must be rejected"
    assert client.post(order["checkout_url"] + "/pay?method=UPI").status_code == 200
    done = leave(client, attendant_headers, "KA03EF7788")
    assert done.status_code == 200
    receipt = client.get(f"/api/v1/receipts/{done.json()['receipt_no']}", headers=attendant_headers).json()
    assert receipt["method"] == "UPI" and receipt["amount_paid"] == 30.0

    day = T0.date().isoformat()
    report = client.get(f"/api/v1/reports/summary?date_from={day}&date_to={day}", headers=admin_headers).json()
    rows = list(csv.reader(io.StringIO(
        client.get(f"/api/v1/reports/export.csv?date_from={day}&date_to={day}", headers=admin_headers).text)))
    total = rows[-1]
    assert total[1] == "TOTAL" and float(total[8]) == report["totals"]["revenue"] == 30.0
