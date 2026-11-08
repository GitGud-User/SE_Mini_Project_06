# Defect Report - SPMS

Maintained by Rahul Prasanna (QA Lead) · Team 06 · tracked as GitHub issues (label `bug`) where noted.
Severity: Critical (blocks a core use case, no workaround) · Major (core use case affected, workaround exists)
· Minor (cosmetic or rare). Every fixed defect has an automated regression test.

| ID | Found | Where / how | Description | Severity | Status | Fix | Regression test |
|---|---|---|---|---|---|---|---|
| DEF-01 | 06-11-2026 | System test - recording the Sprint 2 demo (driver arrived after the booking's start time) | My history hides **Show QR code** as soon as the start time passes, but the gate accepts the QR until start + 15 min (F-051). A driver arriving a few minutes late cannot show the QR. | Major | Fixed | PR by Raghav (branch `fix/qr-after-start`): QR button follows the gate's 15-minute window; Cancel still stops at the start | `tests/test_history_qr_window.py` (fails on the old page, passes on the fix) |
| DEF-02 | 01-11-2026 | Acceptance use - new driver registered and logged in | Login loop: after login the page returned to `?next=` pages the role may not open, which sent the user back to login ("login doesn't verify"). Issue #54. | Critical | Fixed | PR #55 (Rufus): only follow `next` if the role may open that page | `tests/test_login_redirect.py` |
| DEF-03 | 01-11-2026 | Accessibility review (WCAG 2.2 AA, computed styles) | Field borders 1.31:1 and focus ring 2.15:1 contrast (need 3:1); 8 result messages not announced to screen readers; login fields without autocomplete | Major | Fixed | PR #55 (Rufus) | `tests/test_ui_accessibility.py` (12 tests) |
| DEF-04 | 31-10-2026 | Integration test on Windows before merging PR #50 | Backup restore drill failed with `PermissionError` - the SQLite connection stayed open (`with sqlite3.connect()` does not close it) | Major | Fixed | `contextlib.closing()` in `backend/backup.py`, PR #50 | `tests/test_backup.py::test_backup_and_restore_drill` |
| DEF-05 | 07-11-2026 | Test review (STP traceability) | Several test functions carried test-case IDs that did not match the STP (e.g. exit tagged TC-GATE-03 instead of TC-GATE-04, a non-existent TC-BILL-01) so the RTM could not be trusted | Minor (documentation) | Fixed | Test-case tags aligned with STP v1.1 (Rufus, branch `test/system-tests`) | `tools/test_reports.py` builds the RTM from the tags |
| DEF-06 | 06-11-2026 | System test - gate console | Gate "Vehicles inside" shows entry time in UTC while the driver pages show local time | Minor | Open - deferred | Label says "(UTC)", so it is not misleading; to be unified after the course demo | - |

## Summary
6 defects logged · 5 fixed and covered by regression tests · 1 minor open (deferred, cosmetic) · **0 critical or major open**.
