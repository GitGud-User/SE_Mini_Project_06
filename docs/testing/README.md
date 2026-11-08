# Test documentation (STP v1.1 section 8)

| Report | What it shows | Made by |
|---|---|---|
| [Test_Execution_Log.md](Test_Execution_Log.md) | When, where and by whom the cycle ran; totals per test file; entry/exit criteria | `tools/test_reports.py` |
| [Unit_Test_Report.md](Unit_Test_Report.md) | Every unit and API-integration test with its test-case ID and result | `tools/test_reports.py` |
| [System_Validation_Report.md](System_Validation_Report.md) | The six end-to-end journeys SYS-01 … SYS-06 and the requirements they validate | `tools/test_reports.py` |
| [Code_Coverage_Report.md](Code_Coverage_Report.md) | Line coverage per file (NF-006: at least 70 %) | `tools/test_reports.py` |
| [Branch_Coverage_Report.md](Branch_Coverage_Report.md) | Branch coverage per file | `tools/test_reports.py` |
| [RTM.md](RTM.md) | Requirement → test case → automated tests → result | `tools/test_reports.py` |
| [Security_Test_Report.md](Security_Test_Report.md) | STP 5.1 security validation | Raghav |
| [Defect_Report.md](Defect_Report.md) | Defects found, fixes and regression tests | Rahul |
| [Test_Summary_Report.md](Test_Summary_Report.md) | IEEE 829 summary and sign-off | Prithvish |
| `raw/` | junit.xml and coverage.json the generated reports are built from | `tools/test_reports.py` |

Regenerate after any change: `python tools/test_reports.py --tester "Your Name"`.
Test cases are specified in the Software Test Plan v1.1, section 13.1 (`docs/Team06_STP_Smart_Parking_Management_System.pdf`).
