"""Tests for SPMS-F-061 (payments), F-062 (receipts) and F-063 (unpaid exit blocked / admin waiver).
TC-PAY-02, TC-PAY-03, TC-PAY-04."""
import datetime as dt

import pytest

from backend import auth, payments

T0 = dt.datetime(2026, 11, 3, 10, 0, 0)
PLATE = "KA01AB1234"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])
    return state


@pytest.fixture
def lot(client, admin_headers):
    lot = client.post("/api/v1/lots", json={"name": "Pay Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 2, "prefix": "A"})
    client.put(f"/api/v1/lots/{lot['id']}/tariffs", headers=admin_headers,
               json={"vehicle_type": "FOUR_WHEELER", "hourly_rate": 30, "grace_minutes": 15, "daily_cap": 250})
    return lot


def enter(client, headers, lot, plate=PLATE, **extra):
    response = client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": plate, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def leave(client, headers, plate=PLATE):
    return client.post("/api/v1/gate/exit", json={"plate": plate}, headers=headers)


def test_unpaid_exit_blocked_then_cash(client, attendant_headers, lot, clock):  # TC-PAY-02 / TC-PAY-04
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=70)  # 2 started hours x 30
    assert leave(client, attendant_headers).status_code == 402
    due = client.get(f"/api/v1/payments/due?plate={PLATE}", headers=attendant_headers).json()
    assert due["amount_due"] == 60.0 and due["ticket"] == ticket
    short = client.post("/api/v1/payments/cash", json={"ticket": ticket, "amount": 50}, headers=attendant_headers)
    assert short.status_code == 422
    paid = client.post("/api/v1/payments/cash", json={"ticket": ticket, "amount": 60}, headers=attendant_headers)
    assert paid.status_code == 201 and paid.json()["status"] == "PAID"
    done = leave(client, attendant_headers)
    assert done.status_code == 200 and done.json()["fee"] == 60.0


def test_online_payment_through_mock_gateway(client, attendant_headers, lot, clock):  # TC-PAY-02
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=40)
    order = client.post("/api/v1/payments/online/checkout", json={"ticket": ticket}, headers=attendant_headers).json()
    assert order["amount"] == 30.0
    page = client.get(order["checkout_url"])
    assert page.status_code == 200 and "TEST MODE" in page.text
    assert client.post(order["checkout_url"] + "/pay?method=CARD").status_code == 200
    receipt_no = leave(client, attendant_headers).json()["receipt_no"]
    receipt = client.get(f"/api/v1/receipts/{receipt_no}", headers=attendant_headers).json()
    assert receipt["method"] == "CARD" and receipt["amount_paid"] == 30.0


def test_gateway_signature_is_checked(client, attendant_headers, lot, clock):
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=40)
    order_id = client.post("/api/v1/payments/online/checkout", json={"ticket": ticket},
                           headers=attendant_headers).json()["order_id"]
    forged = {"order_id": order_id, "payment_id": "pay_x", "signature": "0" * 64}
    assert client.post("/api/v1/payments/online/verify", json=forged).status_code == 400
    assert leave(client, attendant_headers).status_code == 402
    good = dict(forged, signature=payments._sign(order_id, "pay_x"))
    assert client.post("/api/v1/payments/online/verify", json=good).json()["status"] == "PAID"
    assert leave(client, attendant_headers).status_code == 200


def test_15_minutes_to_leave_after_paying(client, attendant_headers, lot, clock):
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=55)
    client.post("/api/v1/payments/cash", json={"ticket": ticket, "amount": 30}, headers=attendant_headers)
    clock["now"] = T0 + dt.timedelta(minutes=68)  # into the 2nd hour, but within 15 min of paying
    assert leave(client, attendant_headers).status_code == 200


def test_staying_longer_after_paying_costs_extra(client, attendant_headers, lot, clock):
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=55)
    client.post("/api/v1/payments/cash", json={"ticket": ticket, "amount": 30}, headers=attendant_headers)
    clock["now"] = T0 + dt.timedelta(minutes=95)
    assert leave(client, attendant_headers).status_code == 402
    assert client.get(f"/api/v1/payments/due?plate={PLATE}", headers=attendant_headers).json()["amount_due"] == 30.0


def test_admin_waiver_is_audited(client, attendant_headers, admin_headers, lot, clock):  # TC-PAY-04
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=90)
    body = {"ticket": ticket, "reason": "Barrier fault delayed exit"}
    assert client.post("/api/v1/payments/waive", json=body, headers=attendant_headers).status_code == 403
    assert client.post("/api/v1/payments/waive", json=body, headers=admin_headers).status_code == 201
    done = leave(client, attendant_headers).json()
    assert done["status"] == "CLOSED"
    entry = client.get("/api/v1/audit?action=FEE_WAIVER", headers=admin_headers).json()[0]
    assert entry["entity_id"] == ticket and entry["reason"] == "Barrier fault delayed exit"
    assert client.get(f"/api/v1/receipts/{done['receipt_no']}", headers=admin_headers).json()["method"] == "WAIVER"


def test_free_stay_needs_no_payment(client, attendant_headers, lot, clock):
    enter(client, attendant_headers, lot)
    clock["now"] = T0 + dt.timedelta(minutes=10)
    done = leave(client, attendant_headers)
    assert done.status_code == 200 and done.json()["fee"] == 0.0


def test_receipt_fields_and_pdf(client, attendant_headers, driver_headers, lot, clock):  # TC-PAY-03
    client.post("/api/v1/vehicles", json={"plate": PLATE}, headers=driver_headers)
    ticket = enter(client, attendant_headers, lot)["ticket"]
    clock["now"] = T0 + dt.timedelta(minutes=125)
    client.post("/api/v1/payments/cash", json={"ticket": ticket, "amount": 90}, headers=attendant_headers)
    receipt_no = leave(client, attendant_headers).json()["receipt_no"]
    r = client.get(f"/api/v1/receipts/{receipt_no}", headers=driver_headers).json()  # owner can see it
    assert (r["plate"], r["ticket"], r["minutes"], r["total_fee"], r["method"]) == (PLATE, ticket, 125, 90.0, "CASH")
    assert r["entry_time"].startswith("2026-11-03T10:00") and r["exit_time"].startswith("2026-11-03T12:05")
    pdf = client.get(f"/api/v1/receipts/{receipt_no}/pdf", headers=driver_headers)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert receipt_no.encode() in pdf.content and b"Rs 90.00" in pdf.content


def test_other_driver_cannot_see_receipt(client, attendant_headers, driver_headers, lot, clock):
    enter(client, attendant_headers, lot)
    clock["now"] = T0 + dt.timedelta(minutes=5)
    receipt_no = leave(client, attendant_headers).json()["receipt_no"]
    assert client.get(f"/api/v1/receipts/{receipt_no}", headers=driver_headers).status_code == 404


def test_reservation_advance_is_deducted(client, attendant_headers, driver_headers, lot, clock):
    car = client.post("/api/v1/vehicles", json={"plate": PLATE}, headers=driver_headers).json()
    start = T0 + dt.timedelta(minutes=10)
    r = client.post("/api/v1/reservations", headers=driver_headers, json={
        "vehicle_id": car["id"], "lot_id": lot["id"], "start": start.isoformat() + "Z",
        "end": (start + dt.timedelta(hours=2)).isoformat() + "Z"}).json()
    assert r["advance_amount"] == 60.0
    clock["now"] = start
    enter(client, attendant_headers, lot, reservation_qr=r["qr_token"])
    clock["now"] = start + dt.timedelta(minutes=100)  # fee 60, fully covered by the advance
    done = leave(client, attendant_headers)
    assert done.status_code == 200
    receipt = client.get(f"/api/v1/receipts/{done.json()['receipt_no']}", headers=attendant_headers).json()
    assert receipt["advance_deducted"] == 60.0 and receipt["amount_paid"] == 0.0 and receipt["method"] == "ADVANCE"


def test_gate_page_has_payment_panel(client):
    page = client.get("/ui/gate.html").text
    assert 'id="payBox"' in page and "/api/v1/payments/cash" in page and "receipts/" in page
