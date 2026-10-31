"""SPMS-NF-001 load test: 200 concurrent users, 95% of requests must finish within 2 s.

Run against a live server (not part of CI):
    pip install locust
    python -m backend.seed
    python -m uvicorn backend.main:app
    locust -f perf/locustfile.py --host http://127.0.0.1:8000 --users 200 --spawn-rate 20 --run-time 2m --headless
Then read the "95%" column of the summary: it must be <= 2000 ms.
"""
import random

from locust import HttpUser, between, task


class Driver(HttpUser):
    wait_time = between(1, 3)

    @task(5)
    def availability(self):
        self.client.get("/api/v1/lots/1/availability", name="/lots/{id}/availability")

    @task(2)
    def lots(self):
        self.client.get("/api/v1/lots")

    @task(1)
    def slots(self):
        self.client.get("/api/v1/lots/1/slots", name="/lots/{id}/slots")


class Attendant(HttpUser):
    wait_time = between(2, 5)

    def on_start(self):
        r = self.client.post("/api/v1/auth/login", json={"email": "attendant@spms.local", "password": "attend123"})
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    @task
    def gate_cycle(self):
        plate = f"KA{random.randint(10, 99)}LT{random.randint(1000, 9999)}"
        self.client.post("/api/v1/gate/entry", json={"lot_id": 1, "plate": plate}, headers=self.headers,
                         name="/gate/entry")
        self.client.post("/api/v1/gate/exit", json={"plate": plate}, headers=self.headers, name="/gate/exit")
