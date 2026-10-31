"""SPMS-NF-001 / NF-002 performance checks that run in CI (in-process, small scale).
The full 200-concurrent-user load test is perf/locustfile.py (run by hand against a live server)."""
import statistics
import time


def _lot(client, admin_headers, cars=60):
    lot = client.post("/api/v1/lots", json={"name": "Perf Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "G"},
                        headers=admin_headers).json()
    client.post(f"/api/v1/lots/{lot['id']}/slots", headers=admin_headers,
                json={"level_id": level["id"], "slot_type": "FOUR_WHEELER", "count": cars, "prefix": "A"})
    return lot


def test_gate_operations_within_3_seconds(client, admin_headers, attendant_headers):  # NF-002
    lot = _lot(client, admin_headers)
    timings = []
    for i in range(50):  # 50 entries + 50 exits = 100 gate operations
        t = time.perf_counter()
        r = client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": f"KA01AB{i:04d}"},
                        headers=attendant_headers)
        timings.append(time.perf_counter() - t)
        assert r.status_code == 201
    for i in range(50):
        t = time.perf_counter()
        r = client.post("/api/v1/gate/exit", json={"plate": f"KA01AB{i:04d}"}, headers=attendant_headers)
        timings.append(time.perf_counter() - t)
        assert r.status_code == 200
    assert len(timings) == 100 and max(timings) <= 3.0


def test_api_p95_within_2_seconds(client, admin_headers, attendant_headers):  # NF-001
    lot = _lot(client, admin_headers)
    urls = ["/api/v1/lots", f"/api/v1/lots/{lot['id']}/availability", f"/api/v1/lots/{lot['id']}/slots",
            "/api/v1/gate/sessions"]
    timings = []
    for i in range(200):
        t = time.perf_counter()
        assert client.get(urls[i % len(urls)], headers=attendant_headers).status_code == 200
        timings.append(time.perf_counter() - t)
    p95 = statistics.quantiles(timings, n=20)[-1]
    assert p95 <= 2.0
