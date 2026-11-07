# Security Test Report - SPMS (STP section 5.1)

Prepared by Raghav G Uddihal (Developer / security tests) · Team 06

## Scope
Security objectives SO-1 … SO-4 and requirements SPMS-SR-001 … SR-007 from the SRS, validated as planned in
STP section 5.1. Automated tests run on every pull request in CI; the pass/fail result of each one is in
[RTM.md](RTM.md) and [Test_Execution_Log.md](Test_Execution_Log.md), generated from the same run.

## Automated checks

| STP 5.1 item | Requirement / test case | What the tests prove | Tests |
|---|---|---|---|
| Password handling | SR-002 / TC-SEC-02 | bcrypt cost 12, hash never returned or stored in plain text; login gives the same answer for an unknown email and a wrong password (no account enumeration); short passwords refused | `test_auth.py::test_password_is_hashed_not_plain`, `test_auth_tokens.py::test_password_never_returned_and_cost_12`, `test_security_suite.py::test_login_does_not_reveal_which_emails_exist`, `::test_short_password_rejected_at_registration` |
| Account lockout | F-004 / TC-AUTH-04 | 5 wrong passwords lock the account for 15 minutes, then it unlocks | `test_auth.py::test_account_locks_after_5_failed_logins`, `::test_lock_expires` |
| JWT expiry / tampering | SR-003 / TC-SEC-03 | 30-minute expiry; expired, unsigned (`alg: none`), wrongly signed and edited tokens are refused; the role is read from the database, so a token claiming ADMIN does not make a driver an admin; logout revokes; refresh tokens rotate | `test_security_suite.py` (5 token tests), `test_auth_tokens.py` (4 tests) |
| Role-based access | F-003 / TC-AUTH-03 | Access matrix: 12 protected endpoints x 4 callers (anonymous, driver, attendant, admin) - 401 without login, 403 for the wrong role, allowed otherwise | `test_security_suite.py::test_access_matrix` (12 cases) |
| Input fuzzing - SQL injection / XSS | SR-004 / TC-SEC-04 | SQL-injection strings in login and search are harmless (parameterised ORM queries); `<script>` and markup refused in names, addresses and plates; every page escapes data before showing it; path tricks in receipt numbers refused; security headers on every response | `test_audit_security.py` (5 tests), `test_security_suite.py::test_hostile_plates_rejected_at_gate` (4 cases), `::test_receipt_number_cannot_walk_paths` |
| Payment webhook signature, no card data | SR-005 / TC-SEC-05 | A payment confirmation with a forged HMAC signature is rejected; no table has a card-number/CVV/expiry column; the gateway page has no input fields | `test_payments.py::test_gateway_signature_is_checked`, `test_security_suite.py::test_no_card_data_columns_anywhere`, `::test_gateway_page_collects_no_card_fields`, `test_system_e2e.py::test_sys06_online_payment_and_csv` |
| Rate limiting | SR-006 / TC-SEC-06 | More than 10 logins or registrations per minute from one address get HTTP 429 with a `Retry-After` header | `test_auth_tokens.py::test_login_rate_limited`, `::test_register_rate_limited`, `test_security_suite.py::test_rate_limit_tells_client_when_to_retry` |
| Audit trail | SR-007 / TC-SEC-07 | Tariff, lot, maintenance, override and waiver actions are logged with who/when/before/after/reason; rows cannot be changed through the API or the ORM | `test_audit_security.py` (5 tests), `test_system_e2e.py::test_sys04_unpaid_exit_waiver_is_audited` |

## Manual checks

| Check | Requirement | Result | Notes |
|---|---|---|---|
| TLS 1.2+ on every connection | SR-001 / TC-SEC-01 | Not applicable to the local demo | The app runs on http://localhost; TLS is configured at the reverse proxy when deployed (deviation D-08 in [../Deviations.md](../Deviations.md)) |
| OWASP ZAP baseline scan | SR-004 | Not run (optional) | To run: start the app, then `docker run -t ghcr.io/zaproxy/zaproxy:stable zap-baseline.py -t http://host.docker.internal:8000/ui/` and attach the report here |
| Penetration test of login flow | SR-002/003/006 | Covered by the automated tests above | Lockout, rate limiting, token forgery and enumeration all exercised |

## Findings
No security defect was found by these tests. Known limitation: the rate limiter and token revocation list
live in one server process (deviation D-03); with several servers they would need a shared store such as Redis.
