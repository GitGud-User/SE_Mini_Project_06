# Test Summary Report - SPMS v1.0 (IEEE 829 structure)

Identifier: SPMS-TSR-01 · Team 06 · Test cycle 07-11-2026 to 08-11-2026 · Prepared by Prithvish Adhikari (Product Owner)
References: SRS v1.1, SAD v1.1, STP v1.1 (all in `docs/`), [Deviations.md](../Deviations.md)

## 1. Summary
The SPMS build on `main` after the testing-phase pull requests was tested against the Software Test Plan
v1.1. All 40 SRS requirements (27 functional, 6 non-functional, 7 security) map to STP test cases; 39 are
verified by passing automated tests and 1 (SR-001, TLS) is a deployment setting that is documented as a
deviation. No critical or major defect is open. **The product is accepted for the final demo.**

## 2. Variances from the plan
- Test tools differ from STP v1.0 (Playwright, Postman, JMeter, ZAP) - see deviations D-10 … D-13. The STP
  was updated to v1.1 accordingly.
- Test environment: SQLite and a single server instead of PostgreSQL/Redis/Docker (D-02, D-03, D-08).
- Test execution was done in two days at the end of the cycle instead of 02-11 … 13-11, because unit and
  integration tests were written and run with every feature PR during the sprints (CI on every PR).

## 3. Comprehensiveness
Every requirement has at least one test case and every test case has at least one automated test, except
TC-SEC-01 (manual) and TC-MNT-01 (the CI coverage gate). See [RTM.md](RTM.md).

## 4. Summary of results

| Measure | Result | Source |
|---|---|---|
| Automated tests executed | 202 | [Test_Execution_Log.md](Test_Execution_Log.md) |
| Passed / failed / skipped | 202 / 0 / 0 | [Test_Execution_Log.md](Test_Execution_Log.md) |
| Unit and integration tests | 196, all passed | [Unit_Test_Report.md](Unit_Test_Report.md) |
| System validation journeys | 6 (SYS-01 … SYS-06), all passed | [System_Validation_Report.md](System_Validation_Report.md) |
| Line coverage (NF-006 target 70 %) | 94.9 % | [Code_Coverage_Report.md](Code_Coverage_Report.md) |
| Branch coverage | 78.2 % | [Branch_Coverage_Report.md](Branch_Coverage_Report.md) |
| Requirements verified | 39 of 40 (SR-001 manual / deployment) | [RTM.md](RTM.md) |
| Security validation (STP 5.1) | All automated checks pass | [Security_Test_Report.md](Security_Test_Report.md) |
| Defects | 6 logged, 5 fixed with regression tests, 1 minor open | [Defect_Report.md](Defect_Report.md) |

## 5. Evaluation against the exit criteria (STP section 5)

| Exit criterion | Status |
|---|---|
| 100 % of planned test cases executed | Met - all automated test cases ran; TC-SEC-01 assessed manually |
| 0 critical defects open | Met - 0 critical, 0 major open |
| All acceptance criteria satisfied | Met - each SRS acceptance test maps to a passing test case |

## 6. Summary of activities
| Day | Activity | Who |
|---|---|---|
| 07-11-2026 | System validation tests (6 journeys) and STP test-case IDs aligned in all tests | Rufus Thomas |
| 07-11-2026 | Security validation suite (27 tests) and security report | Raghav G Uddihal |
| 07-11-2026 | Documentation finalised: README, SRS v1.1, SAD v1.1, deviations | Prithvish Adhikari |
| 08-11-2026 | DEF-01 fixed with regression test | Raghav G Uddihal |
| 08-11-2026 | STP v1.1 with detailed test cases | Rufus Thomas |
| 08-11-2026 | Full test cycle run, reports generated, defect report, branch coverage added to CI | Rahul Prasanna |
| 08-11-2026 | This summary and sign-off | Prithvish Adhikari |

## 7. Approvals

| Role | Name | Signature / Date |
|---|---|---|
| QA Lead | Rahul Prasanna | |
| Test Engineer | Rufus Thomas | |
| Dev Lead | Raghav G Uddihal | |
| Product Owner | Prithvish Adhikari | |
