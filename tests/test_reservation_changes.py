"""Tests for SPMS-F-032 (modify/cancel + refund), F-033 (no-show release) and F-072 (emails).
TC-RES-03, TC-RES-04, TC-NOT-01."""
import datetime as dt

import pytest

from backend import auth, notifications, reservation_changes
from backend.db import SessionLocal

T0 = dt.datetime(2026, 11, 4, 8, 0, 0)


@pytest.fixture
def clock(monkeypatch):
    state = {"now": T0}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])
    return state


@pytest.fixture
def lot(client, admin_headers):
    lot = client.post("/api/v1/lots", json={"name": "Change Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 2, "prefix": "A"})
    return lot


@pytest.fixture
def booking(client, driver_headers, lot, clock):
    """A 2-hour reservation starting 3 hours from now (advance 40 at the default 20/hr)."""
    car = client.post("/api/v1/vehicles", json={"plate": "KA01AB1234"}, headers=driver_headers).json()
    start = T0 + dt.timedelta(hours=3)
    r = client.post("/api/v1/reservations", headers=driver_headers, json={
        "vehicle_id": car["id"], "lot_id": lot["id"], "start": start.isoformat() + "Z",
        "end": (start + dt.timedelta(hours=2)).isoformat() + "Z"})
    assert r.status_code == 201, r.text
    return r.json()


def outbox(kind=None):
    with SessionLocal() as db:
        rows = db.query(notifications.EmailOutbox).order_by(notifications.EmailOutbox.id).all()
    return [e for e in rows if kind is None or e.kind == kind]


def test_cancel_early_refunds_everything(client, driver_headers, booking, clock):  # TC-RES-03
    r = client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=driver_headers).json()
    assert r["status"] == "CANCELLED" and r["refund_amount"] == 40.0


def test_cancel_late_refunds_half_and_frees_slot(client, driver_headers, booking, lot, clock):  # TC-RES-03
    clock["now"] = T0 + dt.timedelta(hours=2, minutes=30)  # 30 min before start
    r = client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=driver_headers).json()
    assert r["refund_amount"] == 20.0
    car2 = client.post("/api/v1/vehicles", json={"plate": "KA02CD5678"}, headers=driver_headers).json()
    start = T0 + dt.timedelta(hours=3)
    again = client.post("/api/v1/reservations", headers=driver_headers, json={
        "vehicle_id": car2["id"], "lot_id": lot["id"], "start": start.isoformat() + "Z",
        "end": (start + dt.timedelta(hours=1)).isoformat() + "Z"}).json()
    assert again["slot_code"] == booking["slot_code"]  # released immediately


def test_cannot_cancel_after_start_or_twice(client, driver_headers, booking, clock):
    clock["now"] = T0 + dt.timedelta(hours=3, minutes=5)
    assert client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=driver_headers).status_code == 409
    clock["now"] = T0
    client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=driver_headers)
    assert client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=driver_headers).status_code == 409


def test_modify_time_recalculates_advance(client, driver_headers, booking, clock):  # TC-RES-03
    new_start = T0 + dt.timedelta(hours=5)
    r = client.patch(f"/api/v1/reservations/{booking['code']}", headers=driver_headers, json={
        "start": new_start.isoformat() + "Z", "end": (new_start + dt.timedelta(hours=3)).isoformat() + "Z"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["start"].startswith("2026-11-04T13:00") and body["advance_amount"] == 60.0
    assert body["slot_code"] == booking["slot_code"]
    bad = client.patch(f"/api/v1/reservations/{booking['code']}", headers=driver_headers, json={
        "start": new_start.isoformat() + "Z", "end": (new_start + dt.timedelta(minutes=10)).isoformat() + "Z"})
    assert bad.status_code == 422


def test_other_driver_cannot_change_booking(client, booking, clock):
    client.post("/api/v1/auth/register", json={"name": "Eve", "email": "eve@x.com", "phone": "9222222222",
                                                "password": "secret1"})
    eve = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={
        "email": "eve@x.com", "password": "secret1"}).json()["access_token"]}
    assert client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=eve).status_code == 404


def test_no_show_released_after_15_minutes(client, driver_headers, attendant_headers, booking, clock):  # TC-RES-04
    with SessionLocal() as db:
        clock["now"] = T0 + dt.timedelta(hours=3, minutes=10)
        assert reservation_changes.release_no_shows(db) == 0  # still inside the 15-minute window
        clock["now"] = T0 + dt.timedelta(hours=3, minutes=16)
        assert reservation_changes.release_no_shows(db) == 1
    r = client.get(f"/api/v1/reservations/{booking['code']}", headers=driver_headers).json()
    assert r["status"] == "NO_SHOW" and r["qr_token"] is None
    assert client.post("/api/v1/reservations/release-no-shows", headers=attendant_headers).json() == {"released": 0}


def test_emails_for_confirm_cancel_and_reminder(client, driver_headers, admin_headers, booking, clock):  # TC-NOT-01
    confirm = outbox("CONFIRMED")
    assert len(confirm) == 1 and confirm[0].to == "driver@test.com" and booking["code"] in confirm[0].subject
    with SessionLocal() as db:
        clock["now"] = T0 + dt.timedelta(hours=2, minutes=40)  # 20 min before: too early
        assert notifications.send_due_reminders(db) == 0
        clock["now"] = T0 + dt.timedelta(hours=2, minutes=46)  # 14 min before start
        assert notifications.send_due_reminders(db) == 1
        assert notifications.send_due_reminders(db) == 0  # only once
    assert "starts at 11:00" in outbox("REMINDER")[0].body
    client.post(f"/api/v1/reservations/{booking['code']}/cancel", headers=driver_headers)
    assert "Refund: Rs 20.00" in outbox("CANCELLED")[0].body
    inbox = client.get("/api/v1/admin/outbox", headers=admin_headers).json()
    assert [e["kind"] for e in inbox] == ["CANCELLED", "REMINDER", "CONFIRMED"]


def test_email_goes_through_smtp_when_configured(client, monkeypatch, clock):
    sent = []

    class FakeSMTP:  # stands in for a test SMTP inbox (e.g. MailHog)
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setenv("SPMS_SMTP_HOST", "localhost")
    monkeypatch.setattr(notifications.smtplib, "SMTP", FakeSMTP)
    with SessionLocal() as db:
        row = notifications.send(db, "a@b.com", "Hello", "Body", "TEST")
        db.commit()
        assert row.sent is True
    assert sent[0]["To"] == "a@b.com" and sent[0]["Subject"] == "Hello"
