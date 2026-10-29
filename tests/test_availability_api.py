"""Tests for SPMS-F-020 / F-021 (TC-AVL-01, TC-AVL-02)."""
import pytest
from starlette.websockets import WebSocketDisconnect

from backend import availability_api


@pytest.fixture
def lot(client, admin_headers):
    lot = client.post("/api/v1/lots", json={"name": "Avail Lot"}, headers=admin_headers).json()
    level = client.post(f"/api/v1/lots/{lot['id']}/levels", json={"floor_no": 0, "name": "Ground"},
                        headers=admin_headers).json()
    for slot_type, count, prefix in [("FOUR_WHEELER", 3, "A"), ("EV", 1, "E")]:
        client.post(f"/api/v1/lots/{lot['id']}/slots",
                    json={"level_id": level["id"], "slot_type": slot_type, "count": count, "prefix": prefix},
                    headers=admin_headers)
    return lot


def test_availability_counts_by_type(client, lot):  # TC-AVL-01
    data = client.get(f"/api/v1/lots/{lot['id']}/availability").json()
    assert data["free_by_type"]["FOUR_WHEELER"] == 3 and data["free_by_type"]["EV"] == 1
    assert data["free_by_type"]["TWO_WHEELER"] == 0
    assert data["total_free"] == 4 and data["occupancy_percent"] == 0.0
    assert data["levels"] == [{"level": "Ground", "free": 4, "total": 4}]


def test_availability_updates_after_entry(client, lot, attendant_headers):  # TC-AVL-02
    client.post("/api/v1/gate/entry", json={"lot_id": lot["id"], "plate": "KA01AB1234"}, headers=attendant_headers)
    data = client.get(f"/api/v1/lots/{lot['id']}/availability").json()
    assert data["free_by_type"]["FOUR_WHEELER"] == 2 and data["occupancy_percent"] == 25.0


def test_maintenance_not_counted(client, lot, admin_headers):
    slot_id = client.get(f"/api/v1/lots/{lot['id']}/slots").json()[0]["id"]
    client.patch(f"/api/v1/slots/{slot_id}/maintenance", json={"under_maintenance": True}, headers=admin_headers)
    assert client.get(f"/api/v1/lots/{lot['id']}/availability").json()["free_by_type"]["FOUR_WHEELER"] == 2


def test_unknown_lot_404(client):
    assert client.get("/api/v1/lots/999/availability").status_code == 404


def test_websocket_pushes_snapshots(client, lot, monkeypatch):  # TC-AVL-02 (live push)
    monkeypatch.setattr(availability_api, "PUSH_INTERVAL_SECONDS", 0.05)
    with client.websocket_connect(f"/ws/lots/{lot['id']}/availability") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
    assert first["total_free"] == 4 and second["lot_id"] == lot["id"]


def test_websocket_unknown_lot(client):
    with client.websocket_connect("/ws/lots/999/availability") as ws:
        assert ws.receive_json() == {"error": "Parking lot not found"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
