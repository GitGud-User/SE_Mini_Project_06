# Deviations from the design documents (SRS / SAD / STP v1.0)

Team 06 · SPMS · recorded at the end of Sprint 2, after the development freeze (08-11-2026).
The SRS, SAD and STP were updated to v1.1 to match; this page is the single list to explain in the demo.
Every requirement in the SRS is still met - the deviations change *how*, not *what*.

## Technology (SAD 3.6)

| # | Planned in v1.0 | As built | Why | Effect on requirements |
|---|---|---|---|---|
| D-01 | React 18 single-page frontend | 8 plain HTML/CSS/JavaScript pages served by FastAPI at `/ui` (`backend/static/`) | No build step, every member can read and change the pages; the pages call exactly the same REST API | None - same API, same features. Usability/accessibility checked against WCAG 2.2 AA (NF-004) |
| D-02 | PostgreSQL 15 with an exclusion constraint against double booking | SQLite through SQLAlchemy (`SPMS_DATABASE_URL`, PostgreSQL-ready) | Zero setup on lab and home laptops | F-031 is enforced by a transaction plus an allocation lock (`ALLOC_LOCK`); proven by a 50-parallel-request test (TC-RES-02) |
| D-03 | Redis 7 + Celery workers (cache, locks, pub/sub, jobs) | In-process: rate limiter in memory, background sweep task in `main.py` (no-shows, reminders), WebSocket push from the API process | Single-server deployment for a lot; fewer moving parts | Meets F-021 (push every 2 s, limit 5 s), F-033, F-072. Scaling out to several servers would need Redis again |
| D-04 | Alembic migrations | `create_all` at start-up; Sprint 2 only added new tables | No schema change to existing tables | Existing `spms.db` files keep working |
| D-05 | python-jose and passlib | PyJWT and bcrypt (cost 12) directly | Maintained libraries, fewer dependencies | SR-002 and SR-003 unchanged |
| D-06 | Razorpay sandbox (test mode) | Mock gateway in `payments.py` with the same flow: checkout -> pay -> HMAC-signed confirmation | No merchant account needed; the STP risk table already planned this fallback | SR-005 met: signature verified, no card data stored (TC-SEC-05) |
| D-07 | SMTP email | Every email is stored in an `email_outbox` table (admin can read it); also sent by SMTP when `SPMS_SMTP_HOST` is set | Demo works without a mail server | F-072 met |
| D-08 | Nginx with TLS 1.2+, Docker Compose deployment | Local demo runs `uvicorn` on http://localhost; no Docker files | Course demo is local; TLS belongs to the hosting set-up | **SR-001 not demonstrated locally** - it is a deployment requirement (TLS at the reverse proxy). Marked MANUAL in the RTM |
| D-09 | Python 3.11 | Python 3.12 in CI; team laptops run 3.12-3.13 | Current GitHub Actions image | None |

## Testing (STP 5 and 6)

| # | Planned | As done | Why |
|---|---|---|---|
| D-10 | Playwright UI automation | Pages tested through the server (HTML, labels, contrast, focus, ARIA) in pytest, plus manual walk-throughs and the demo videos | No browser needed in CI; keeps the suite at about 30 s |
| D-11 | Postman API tests | pytest + FastAPI TestClient (same HTTP requests, run in CI on every PR) | One tool, automatic |
| D-12 | JMeter/Locust load test in CI | Locust script `perf/locustfile.py` for a manual 200-user run; in-process response-time tests in CI (TC-PERF-01/02) | A load test needs a running server and minutes of run time |
| D-13 | OWASP ZAP scan | Automated security suite (`tests/test_security_suite.py` and others); ZAP baseline scan optional and manual | See docs/testing/Security_Test_Report.md |

## Process

| # | Planned | As happened | Note |
|---|---|---|---|
| D-14 | PRs reviewed and merged by the repository owner | Each member raises PRs for their own backlog items; a teammate reviews and approves (ruleset: 1 approval + green CI) and the author merges | So that every member has their own commits, reviews and merges in the history; the owner (Prithvish) sets up the ruleset, CI and board |
| D-15 | One PR per member per sprint | Rufus's PR #46 was merged empty (commits were on his local `main`) and redone as #47 | Lesson recorded: commit only on feature branches |
| D-16 | Sprint 2 frozen at PR #53 | One post-sprint fix PR #55 (issue #54: login redirect loop + accessibility) | Defect found in acceptance use, see docs/testing/Defect_Report.md |

## Added beyond the SRS

- Append-only audit log API (`/api/v1/audit`) for SR-007, backup/restore API and command for NF-003.
- Accessibility work beyond WCAG 2.1 AA: visible focus, pause for live updates, reduced-motion support.
