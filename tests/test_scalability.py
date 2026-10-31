"""SPMS-NF-005: 10 lots, 5,000 slots and 1,000 reservations a day with the normal schema, and the
API stays fast (NF-001: <= 2 s) at that volume. The API is stateless: every request carries its JWT."""
import datetime as dt
import time

from backend.db import SessionLocal
from backend.volume_seed import seed_volume


def test_volume_without_schema_changes(client, driver_headers, attendant_headers):
    with SessionLocal() as db:
        info = seed_volume(db, lots=10, slots_per_lot=500, reservations=1000)
    assert info["slots"] == 5000 and info["reservations"] == 1000
    assert len(client.get("/api/v1/lots").json()) >= 10

    for lot_id in info["lot_ids"][:3]:
        t = time.perf_counter()
        snap = client.get(f"/api/v1/lots/{lot_id}/availability").json()
        assert time.perf_counter() - t <= 2.0 and snap["total_slots"] == 500

    car = client.post("/api/v1/vehicles", json={"plate": "KA01AB1234"}, headers=driver_headers).json()
    start = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=1)
    t = time.perf_counter()
    r = client.post("/api/v1/reservations", headers=driver_headers, json={
        "vehicle_id": car["id"], "lot_id": info["lot_ids"][0], "start": start.isoformat() + "Z",
        "end": (start + dt.timedelta(hours=1)).isoformat() + "Z"})
    assert time.perf_counter() - t <= 2.0 and r.status_code == 201
    assert r.json()["slot_code"] == "V-101"  # first 100 slots are reserved for that hour

    t = time.perf_counter()
    entry = client.post("/api/v1/gate/entry", json={"lot_id": info["lot_ids"][1], "plate": "KA09ZZ0001"},
                        headers=attendant_headers)
    assert time.perf_counter() - t <= 3.0 and entry.status_code == 201
