from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_health_returns_ok():  # TC-REL-01
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
