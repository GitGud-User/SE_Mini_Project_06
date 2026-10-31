"""Tests for SPMS-F-070 (dashboard), F-071 (CSV export) and F-073 (driver history).
TC-RPT-01, TC-RPT-02, TC-HIS-01."""
import csv
import datetime as dt
import io

import pytest
from sqlalchemy import text

from backend import auth
from backend.db import engine

DAY = dt.datetime(2026, 11, 5)
CAR1, CAR2 = "KA01AB1234", "KA02CD5678"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": DAY.replace(hour=8)}
    monkeypatch.setattr(auth, "utcnow", lambda: state["now"])
    return state


@pytest.fixture
def day_of_data(client, admin_headers, attendant_headers, driver_headers, clock):
    """08:00 car1 in; 08:00 two bookings (advances 60 + 30); 08:30 one cancelled (refund 60);
    09:00-09:10 car2 (free); 10:05 car1 pays 90 cash and leaves. Report time: 11:00."""
    lot = client.post("/api/v1/lots", json={"name": "Report Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 2, "prefix": "A"})
    client.put(f"/api/v1/lots/{lot['id']}/tariffs", headers=admin_headers,
               json={"vehicle_type": "FOUR_WHEELER", "hourly_rate": 30, "grace_minutes": 15})
    car = client.post("/api/v1/vehicles", json={"plate": CAR1}, headers=driver_headers).json()
    gate = lambda path, body: client.post(f"/api/v1/gate/{path}", json=body, headers=attendant_headers)  # noqa: E731
    ticket1 = gate("entry", {"lot_id": lot["id"], "plate": CAR1}).json()["ticket"]
    codes = []
    for start_h, hours in ((12, 2), (15, 1)):
        start = DAY.replace(hour=start_h)
        codes.append(client.post("/api/v1/reservations", headers=driver_headers, json={
            "vehicle_id": car["id"], "lot_id": lot["id"], "start": start.isoformat() + "Z",
            "end": (start + dt.timedelta(hours=hours)).isoformat() + "Z"}).json()["code"])
    clock["now"] = DAY.replace(hour=8, minute=30)
    client.post(f"/api/v1/reservations/{codes[0]}/cancel", headers=driver_headers)
    clock["now"] = DAY.replace(hour=9)
    gate("entry", {"lot_id": lot["id"], "plate": CAR2})
    clock["now"] = DAY.replace(hour=9, minute=10)
    gate("exit", {"plate": CAR2})
    clock["now"] = DAY.replace(hour=10, minute=5)
    client.post("/api/v1/payments/cash", json={"ticket": ticket1, "amount": 90}, headers=attendant_headers)
    gate("exit", {"plate": CAR1})
    clock["now"] = DAY.replace(hour=11)
    return lot


def summary(client, headers, **params):
    query = "&".join(f"{k}={v}" for k, v in {"date_from": "2026-11-05", "date_to": "2026-11-05", **params}.items())
    return client.get(f"/api/v1/reports/summary?{query}", headers=headers)


def test_dashboard_figures(client, admin_headers, day_of_data):  # TC-RPT-01
    s = summary(client, admin_headers).json()
    r = s["lots"][0]
    assert (r["entries"], r["exit_payments"], r["advances"], r["refunds"], r["revenue"]) == (2, 90.0, 90.0, 60.0, 120.0)
    assert r["average_stay_minutes"] == 67.5            # (125 + 10) / 2
    assert r["occupancy_percent"] == 10.2               # 135 parked minutes / (2 slots x 660 minutes so far)
    assert r["peak_hours"] == [8, 9] and sum(r["entries_by_hour"]) == 2
    assert s["totals"]["revenue"] == 120.0


def test_dashboard_matches_plain_sql(client, admin_headers, day_of_data):  # TC-RPT-01
    r = summary(client, admin_headers).json()["lots"][0]
    with engine.connect() as c:
        paid = c.execute(text("SELECT SUM(amount) FROM payments WHERE status='PAID'")).scalar()
        adv = c.execute(text("SELECT SUM(advance_amount) FROM reservations")).scalar()
        ref = c.execute(text("SELECT COALESCE(SUM(refund_amount), 0) FROM reservations")).scalar()
        stay = c.execute(text("SELECT AVG(minutes) FROM receipts")).scalar()
    assert r["revenue"] == paid + adv - ref and r["average_stay_minutes"] == stay


def test_other_days_are_empty(client, admin_headers, day_of_data):
    r = summary(client, admin_headers, date_from="2026-11-06", date_to="2026-11-06").json()["lots"][0]
    assert r["entries"] == 0 and r["revenue"] == 0


def test_reports_admin_only_and_validated(client, attendant_headers, admin_headers, day_of_data):
    assert summary(client, attendant_headers).status_code == 403
    bad = client.get("/api/v1/reports/summary?date_from=2026-11-06&date_to=2026-11-01", headers=admin_headers)
    assert bad.status_code == 422


def test_csv_totals_match_dashboard(client, admin_headers, day_of_data):  # TC-RPT-02
    response = client.get("/api/v1/reports/export.csv?date_from=2026-11-05&date_to=2026-11-05", headers=admin_headers)
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows[0]["lot"] == "Report Lot" and rows[0]["peak_hours"] == "08:00 09:00"
    total = rows[-1]
    assert total["lot"] == "TOTAL" and float(total["revenue"]) == summary(client, admin_headers).json()["totals"]["revenue"]


def test_driver_history_only_own_records(client, driver_headers, day_of_data):  # TC-HIS-01
    h = client.get("/api/v1/me/history", headers=driver_headers).json()
    assert [v["plate"] for v in h["vehicles"]] == [CAR1]
    assert sorted(r["status"] for r in h["reservations"]) == ["CANCELLED", "CONFIRMED"]
    assert [s["plate"] for s in h["sessions"]] == [CAR1]  # car2 is not this driver's
    assert h["sessions"][0]["receipt_no"] == h["receipts"][0]["receipt_no"] and h["receipts"][0]["total_fee"] == 90.0
    client.post("/api/v1/auth/register", json={"name": "Neha", "email": "neha@x.com", "phone": "9444444444",
                                                "password": "secret1"})
    other = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={
        "email": "neha@x.com", "password": "secret1"}).json()["access_token"]}
    empty = client.get("/api/v1/me/history", headers=other).json()
    assert empty == {"vehicles": [], "reservations": [], "sessions": [], "receipts": []}


def test_reservation_qr_image(client, driver_headers, day_of_data):
    pytest.importorskip("segno")
    h = client.get("/api/v1/me/history", headers=driver_headers).json()
    active = next(r for r in h["reservations"] if r["status"] == "CONFIRMED")
    cancelled = next(r for r in h["reservations"] if r["status"] == "CANCELLED")
    svg = client.get(f"/api/v1/reservations/{active['code']}/qr.svg", headers=driver_headers)
    assert svg.headers["content-type"] == "image/svg+xml" and b"<svg" in svg.content
    assert client.get(f"/api/v1/reservations/{cancelled['code']}/qr.svg", headers=driver_headers).status_code == 409
