"""Regression tests for defect DEF-01 (found 06-11-2026 while recording the Sprint 2 demo).

The gate accepts a reservation QR from 15 minutes before to 15 minutes after the start (SPMS-F-051),
but My history hid the "Show QR code" button as soon as the start time passed, so a driver arriving a
few minutes late could not show the QR. The button must follow the gate's window; "Cancel booking"
must still disappear at the start time (SPMS-F-032).
"""
import datetime as dt
import pathlib
import re

import pytest

from backend import auth, gate

HISTORY = pathlib.Path(__file__).resolve().parents[1] / "backend" / "static" / "history.html"
T0 = dt.datetime(2026, 11, 9, 9, 0, 0)


def test_page_uses_the_gate_qr_window():  # TC-HIS-01, TC-GATE-02
    page = HISTORY.read_text(encoding="utf-8")
    window = re.search(r"QR_WINDOW_MS\s*=\s*(\d+)\s*\*\s*60000", page)
    assert window and int(window.group(1)) == gate.QR_WINDOW_MINUTES
    assert "now < start + QR_WINDOW_MS" in page, "Show QR must stay visible until start + 15 min"
    assert re.search(r"canCancel\s*=.*start > now", page), "Cancel must still stop at the start time"
    assert "${canShowQr ?" in page and "${canCancel ?" in page, "the two buttons must not share one condition"


def test_qr_still_served_ten_minutes_after_start(client, admin_headers, monkeypatch):  # TC-HIS-01, TC-GATE-02
    pytest.importorskip("segno")
    now = {"t": T0}
    monkeypatch.setattr(auth, "utcnow", lambda: now["t"])
    lot = client.post("/api/v1/lots", json={"name": "QR Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": 1, "prefix": "A"})
    client.post("/api/v1/auth/register", json={"name": "Asha", "email": "asha@example.com", "phone": "9123456780",
                                                "password": "secret123"})
    driver = {"Authorization": "Bearer " + client.post("/api/v1/auth/login", json={
        "email": "asha@example.com", "password": "secret123"}).json()["access_token"]}
    vid = client.post("/api/v1/vehicles", json={"plate": "KA01AB1234", "vehicle_type": "FOUR_WHEELER"},
                      headers=driver).json()["id"]
    start = T0 + dt.timedelta(minutes=30)
    code = client.post("/api/v1/reservations", headers=driver, json={
        "vehicle_id": vid, "lot_id": lot["id"], "start": start.isoformat() + "Z",
        "end": (start + dt.timedelta(hours=1)).isoformat() + "Z"}).json()["code"]

    now["t"] = start + dt.timedelta(minutes=10)                    # driver is 10 minutes late
    svg = client.get(f"/api/v1/reservations/{code}/qr.svg", headers=driver)
    assert svg.status_code == 200 and b"<svg" in svg.content
    assert client.post(f"/api/v1/reservations/{code}/cancel", headers=driver).status_code == 409
