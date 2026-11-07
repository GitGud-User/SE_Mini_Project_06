# SE Mini Project 06 – Smart Parking Management System (SPMS)

Team 06: Raghav G Uddihal · Prithvish Adhikari (repository owner) · Rahul Prasanna · Rufus Thomas

SPMS tracks parking availability, vehicle entry and exit, reservations and payments. It allocates a
suitable slot to every vehicle and gives administrators occupancy and revenue reports.

## Features (all SRS functional requirements SPMS-F-001 … F-073)

| Who | What they can do |
|---|---|
| Driver | Register and log in · see live free slots · add vehicles · reserve a slot ahead with an advance payment and a QR code · change or cancel a booking (refund 100 % ≥ 1 h before, else 50 %) · view history and download receipts |
| Gate attendant | Log entry (walk-in or reservation QR) · log exit with automatic fee (15-min grace, hourly rate, daily cap) · take cash or online payment · move a car to another slot (audited) |
| Admin | Manage lots, levels, slots and tariffs · put slots into maintenance · waive a fee (audited) · dashboard with occupancy, revenue, average stay, peak hours · CSV export · audit log · backups · email outbox |

## Run it (Windows)

```
git clone https://github.com/GitGud-User/SE_Mini_Project_06.git
cd SE_Mini_Project_06
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m backend.seed                 # demo lot "City Centre Parking", 34 slots, demo accounts
uvicorn backend.main:app --reload
```

Open http://127.0.0.1:8000 (web app) and http://127.0.0.1:8000/docs (interactive API documentation).
macOS/Linux: `python3 -m venv .venv` and `source .venv/bin/activate`.

| Demo account | Email | Password |
|---|---|---|
| Admin | admin@spms.local | admin123 |
| Gate attendant | attendant@spms.local | attend123 |
| Driver | register one on the login page | – |

Optional settings (environment variables): `SPMS_DATABASE_URL` (default `sqlite:///./spms.db`; the code is
PostgreSQL-ready through SQLAlchemy but was only tested on SQLite), `SPMS_SECRET_KEY` (JWT key – set it in any real deployment), `SPMS_SMTP_HOST` / `SPMS_SMTP_PORT` /
`SPMS_SMTP_USER` / `SPMS_SMTP_PASSWORD` / `SPMS_MAIL_FROM` (send emails), `SPMS_BACKUP_DIR`, `SPMS_SWEEP_SECONDS`.

## Tests

```
python -m pytest                                   # whole suite
python -m pytest --cov=backend --cov-branch        # with line + branch coverage
python tools/test_reports.py --tester "Your Name"  # run everything and regenerate docs/testing/*
```

The suite has unit, API-integration, system (end-to-end journeys), security, performance, usability and
accessibility tests. Every test carries the STP test-case ID it verifies (`# TC-...`), so
`docs/testing/RTM.md` traces each SRS requirement to its tests. GitHub Actions runs the suite on every pull
request and blocks the merge if a test fails or coverage drops below 70 % (NF-006).

## Project structure

```
backend/            FastAPI application (one module per feature: auth, lots, availability, reservations,
                    gate, payments, reports, notifications, audit, backup, ...)
backend/static/     the web pages (plain HTML, CSS and JavaScript, served at /ui)
tests/              pytest suite
tools/              test_reports.py - runs the suite and writes the test reports
perf/               Locust load test (NF-001, 200 users)
docs/               SRS, SAD and Test Plan (v1.1), Deviations.md, testing/ reports
videos/             sprint demo videos
```

## Documents

- [Software Requirements Specification](docs/Team06_SRS_Smart_Parking_Management_System.pdf)
- [Software Architecture & Design](docs/Team06_SAD_Smart_Parking_Management_System.pdf)
- [Software Test Plan](docs/Team06_STP_Smart_Parking_Management_System.pdf)
- [Deviations from the design documents](docs/Deviations.md)
- [Test reports](docs/testing/)

## Workflow

Backlog: GitHub Project "SPMS Backlog" (story points, Sprint 1 / Sprint 2). Every change goes through a
pull request from a `feature/`, `fix/`, `test/` or `docs/` branch; CI must be green and a teammate
approves before the author merges.
