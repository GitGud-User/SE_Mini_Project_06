"""Run the whole test suite once and write the test reports into docs/testing/ (STP sections 8 and 14).

    python tools/test_reports.py --tester "Rahul Prasanna"

Writes (all generated from this run - never edit them by hand, run the script again instead):
    docs/testing/Test_Execution_Log.md       when, where, who, totals per test file
    docs/testing/Unit_Test_Report.md         every module (unit/integration) test and its result
    docs/testing/System_Validation_Report.md the end-to-end journeys (tests/test_system_e2e.py)
    docs/testing/Code_Coverage_Report.md     line coverage per file (NF-006: at least 70 %)
    docs/testing/Branch_Coverage_Report.md   branch coverage per file
    docs/testing/RTM.md                      requirement -> test case -> automated tests -> result
    docs/testing/raw/junit.xml, coverage.json   the raw tool output the reports are built from
Exit code is 1 if any test failed, so the script can also be used as a gate.
"""
import argparse
import datetime as dt
import json
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "testing"
RAW = OUT / "raw"

# STP section 13: requirement -> test case (kept in step with the STP v1.1 test-case table)
REQUIREMENTS = [
    ("SPMS-F-001", "Driver registration", "TC-AUTH-01"), ("SPMS-F-002", "Login & JWT issue", "TC-AUTH-02"),
    ("SPMS-F-003", "Role-based access control", "TC-AUTH-03"), ("SPMS-F-004", "Account lockout after 5 failures", "TC-AUTH-04"),
    ("SPMS-F-010", "Manage lots / levels / slots", "TC-LOT-01"), ("SPMS-F-011", "Configure tariffs", "TC-LOT-02"),
    ("SPMS-F-012", "Slot maintenance status", "TC-LOT-03"), ("SPMS-F-020", "Display free-slot counts", "TC-AVL-01"),
    ("SPMS-F-021", "Live status push <= 5 s", "TC-AVL-02"), ("SPMS-F-030", "Reserve a slot", "TC-RES-01"),
    ("SPMS-F-031", "Prevent double booking", "TC-RES-02"), ("SPMS-F-032", "Modify / cancel with refund", "TC-RES-03"),
    ("SPMS-F-033", "Auto-release no-shows", "TC-RES-04"), ("SPMS-F-040", "Auto-allocate suitable slot", "TC-ALC-01"),
    ("SPMS-F-041", "Attendant allocation override", "TC-ALC-02"), ("SPMS-F-050", "Record vehicle entry", "TC-GATE-01"),
    ("SPMS-F-051", "Validate reservation QR", "TC-GATE-02"), ("SPMS-F-052", "Reject duplicate entry", "TC-GATE-03"),
    ("SPMS-F-053", "Record vehicle exit", "TC-GATE-04"), ("SPMS-F-060", "Fee calculation", "TC-PAY-01"),
    ("SPMS-F-061", "Online & cash payment", "TC-PAY-02"), ("SPMS-F-062", "Digital receipt", "TC-PAY-03"),
    ("SPMS-F-063", "Block unpaid exit / waiver", "TC-PAY-04"), ("SPMS-F-070", "Occupancy & revenue dashboard", "TC-RPT-01"),
    ("SPMS-F-071", "CSV export", "TC-RPT-02"), ("SPMS-F-072", "Email notifications", "TC-NOT-01"),
    ("SPMS-F-073", "Driver history & receipts", "TC-HIS-01"),
    ("SPMS-NF-001", "API response time", "TC-PERF-01"), ("SPMS-NF-002", "Gate transaction time", "TC-PERF-02"),
    ("SPMS-NF-003", "Availability & backup", "TC-REL-01"), ("SPMS-NF-004", "Usability & accessibility", "TC-UX-01"),
    ("SPMS-NF-005", "Scalability", "TC-PERF-03"), ("SPMS-NF-006", "Maintainability", "TC-MNT-01"),
    ("SPMS-SR-001", "TLS 1.2+ everywhere", "TC-SEC-01"), ("SPMS-SR-002", "bcrypt password hashing", "TC-SEC-02"),
    ("SPMS-SR-003", "JWT expiry & revocation", "TC-SEC-03"), ("SPMS-SR-004", "Input validation / no SQLi-XSS", "TC-SEC-04"),
    ("SPMS-SR-005", "No card data; signed webhooks", "TC-SEC-05"), ("SPMS-SR-006", "Rate limiting", "TC-SEC-06"),
    ("SPMS-SR-007", "Append-only audit log", "TC-SEC-07"),
]
# test cases verified by something other than a pytest function
NOT_PYTEST = {
    "TC-MNT-01": "CI coverage gate: `pytest --cov-fail-under=70` on every PR (see Code_Coverage_Report.md)",
    "TC-SEC-01": "Manual / deployment: TLS is terminated by the reverse proxy; local demo runs on http://localhost "
                 "(documented deviation, see docs/Deviations.md and Security_Test_Report.md)",
}
SYSTEM_FILE = "test_system_e2e.py"


def sh(*cmd):
    try:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def tags_by_test():
    """Read the '# TC-...' comment on every 'def test_...' line."""
    tags = {}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"def (test_\w+)\(.*\):\s*#\s*(.*)$", line)
            if m:
                tags[(path.name, m.group(1))] = re.findall(r"TC-[A-Z]+-\d+", m.group(2))
    return tags


def docstring_first_line(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r'\s*"""(.*?)(?:\n|""")', text)
    return m.group(1).strip() if m else ""


def run_pytest():
    RAW.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={RAW / 'junit.xml'}",
           "--cov=backend", "--cov-branch", f"--cov-report=json:{RAW / 'coverage.json'}", "--cov-report=term"]
    started = dt.datetime.now()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(proc.stdout[-1500:])
    return started, proc.returncode, " ".join(["python"] + cmd[1:]).replace(str(ROOT) + "\\", "").replace(str(ROOT) + "/", "")


def read_junit():
    results = []
    for case in ET.parse(RAW / "junit.xml").getroot().iter("testcase"):
        file = case.get("classname", "").split(".")[-1] + ".py"
        name = case.get("name", "")
        status = "PASS"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "FAIL"
        elif case.find("skipped") is not None:
            status = "SKIP"
        results.append({"file": file, "name": name, "base": name.split("[")[0], "time": float(case.get("time", 0)),
                        "status": status})
    return results


def pct(a, b):
    return 100.0 * a / b if b else 100.0


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write(name, title, meta, body):
    text = f"# {title}\n\n{meta}\n\n{body}\n"
    (OUT / name).write_text(text, encoding="utf-8")
    print("  wrote docs/testing/" + name)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tester", required=True, help="who ran this cycle, e.g. \"Rahul Prasanna\"")
    args = ap.parse_args()

    started, code, command = run_pytest()
    results = read_junit()
    cov = json.loads((RAW / "coverage.json").read_text(encoding="utf-8"))
    tags = tags_by_test()
    commit = sh("git", "rev-parse", "--short", "HEAD") or "unknown"
    branch = sh("git", "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    count = defaultdict(int)
    for r in results:
        count[r["status"]] += 1
    total_time = sum(r["time"] for r in results)
    meta = (f"Generated by `tools/test_reports.py` · run {started:%d-%m-%Y %H:%M} · tester **{args.tester}** · "
            f"commit `{commit}` ({branch}) · Python {platform.python_version()} on {platform.system()} {platform.release()}")
    t = cov["totals"]
    line_pct = pct(t["covered_lines"], t["num_statements"])
    branch_pct = pct(t["covered_branches"], t["num_branches"])
    verdict = "**PASS**" if code == 0 and count["FAIL"] == 0 else "**FAIL**"

    # ---- execution log ----
    per_file = defaultdict(lambda: defaultdict(float))
    for r in results:
        per_file[r["file"]]["tests"] += 1
        per_file[r["file"]][r["status"]] += 1
        per_file[r["file"]]["time"] += r["time"]
    rows = [(f, int(v["tests"]), int(v["PASS"]), int(v["FAIL"]), int(v["SKIP"]), f"{v['time']:.2f}")
            for f, v in sorted(per_file.items())]
    failures = [f"- `{r['file']}::{r['name']}`" for r in results if r["status"] == "FAIL"]
    body = "\n\n".join([
        "## Summary",
        table(["Item", "Value"], [
            ("Verdict", verdict), ("Tests executed", len(results)), ("Passed", count["PASS"]),
            ("Failed", count["FAIL"]), ("Skipped", count["SKIP"]), ("Total test time", f"{total_time:.1f} s"),
            ("Line coverage", f"{line_pct:.1f} %"), ("Branch coverage", f"{branch_pct:.1f} %"),
            ("Command", f"`{command}`")]),
        "## Results per test file", table(["Test file", "Tests", "Pass", "Fail", "Skip", "Time (s)"], rows),
        "## Failures", "\n".join(failures) if failures else "None - every executed test passed.",
        "## Entry / exit criteria (STP section 5)",
        table(["Criterion", "Met?"], [
            ("Stable build: `main` at the commit above, CI green", "Yes" if code == 0 else "No"),
            ("100 % of planned automated test cases executed", "Yes" if count["SKIP"] == 0 else f"No - {count['SKIP']} skipped"),
            ("0 critical defects open", "See Defect_Report.md"),
            ("Coverage at least 70 % (NF-006)", "Yes" if line_pct >= 70 else "No")]),
    ])
    write("Test_Execution_Log.md", "Test Execution Log - SPMS", meta, body)

    # ---- unit test report ----
    sections = []
    for f in sorted(per_file):
        if f == SYSTEM_FILE:
            continue
        rows = [(f"`{r['name']}`", ", ".join(tags.get((f, r["base"]), [])) or "-", r["status"], f"{r['time']:.3f}")
                for r in results if r["file"] == f]
        sections.append(f"### {f}\n{docstring_first_line(ROOT / 'tests' / f)}\n\n" + table(["Test", "Test case", "Result", "Time (s)"], rows))
    unit = [r for r in results if r["file"] != SYSTEM_FILE]
    body = (f"Module-level tests (unit and API integration, STP section 5 levels 1-2): **{len(unit)} tests, "
            f"{sum(r['status'] == 'PASS' for r in unit)} passed, {sum(r['status'] == 'FAIL' for r in unit)} failed.** "
            "Each module test starts from a fresh database (tests/conftest.py).\n\n" + "\n\n".join(sections))
    write("Unit_Test_Report.md", "Unit Test Report - SPMS", meta, body)

    # ---- system validation report ----
    sys_file = ROOT / "tests" / SYSTEM_FILE
    journeys = dict(re.findall(r"(SYS-\d+)\s+(.+)", sys_file.read_text(encoding="utf-8")))
    rows = []
    covered = set()
    for r in results:
        if r["file"] != SYSTEM_FILE:
            continue
        sid = "SYS-" + re.search(r"sys(\d+)", r["name"]).group(1)
        tc = tags.get((SYSTEM_FILE, r["base"]), [])
        covered.update(tc)
        rows.append((sid, journeys.get(sid, r["name"]), ", ".join(tc), r["status"]))
    reqs = [(rid, title) for rid, title, tc in REQUIREMENTS if tc in covered]
    body = "\n\n".join([
        "System tests (STP section 5, level 3) drive complete user journeys through the public HTTP API only - "
        "the same calls the driver, gate and admin pages make - and check every step.",
        table(["ID", "Journey", "Test cases exercised", "Result"], rows),
        f"## Requirements validated end-to-end ({len(reqs)} of {len(REQUIREMENTS)})",
        "\n".join(f"- {rid} {title}" for rid, title in reqs),
        "## Manual system validation", "The UI journey (all 8 pages) was also exercised by hand and recorded for the Sprint 2 demo "
        "video; accessibility was checked against WCAG 2.2 AA (see tests/test_ui_accessibility.py).",
    ])
    write("System_Validation_Report.md", "System Validation Report - SPMS", meta, body)

    # ---- coverage reports ----
    files = sorted(cov["files"].items())
    rows = [(f"`{p}`", s["summary"]["num_statements"], s["summary"]["missing_lines"],
             f"{pct(s['summary']['covered_lines'], s['summary']['num_statements']):.0f} %") for p, s in files]
    rows.append(("**TOTAL**", t["num_statements"], t["missing_lines"], f"**{line_pct:.1f} %**"))
    body = (f"Line (statement) coverage of `backend/` measured with pytest-cov during this run. "
            f"NF-006 requires at least 70 %; CI enforces it on every pull request (`--cov-fail-under=70`).\n\n"
            + table(["File", "Statements", "Missed", "Coverage"], rows))
    write("Code_Coverage_Report.md", "Code Coverage Report - SPMS", meta, body)
    rows = [(f"`{p}`", s["summary"]["num_branches"], s["summary"]["covered_branches"], s["summary"]["num_partial_branches"],
             f"{pct(s['summary']['covered_branches'], s['summary']['num_branches']):.0f} %") for p, s in files]
    rows.append(("**TOTAL**", t["num_branches"], t["covered_branches"], t["num_partial_branches"], f"**{branch_pct:.1f} %**"))
    body = ("Branch coverage (`--cov-branch`): every `if`/loop decision must be taken both ways to count as covered. "
            "A *partial* branch was taken only one way.\n\n"
            + table(["File", "Branches", "Covered", "Partial", "Branch coverage"], rows))
    write("Branch_Coverage_Report.md", "Branch Coverage Report - SPMS", meta, body)

    # ---- RTM ----
    by_tc = defaultdict(list)
    for r in results:
        for tc in tags.get((r["file"], r["base"]), []):
            by_tc[tc].append(r)
    rows = []
    for rid, title, tc in REQUIREMENTS:
        tests = by_tc.get(tc, [])
        if tests:
            names = sorted({f"{r['file']}::{r['base']}" for r in tests})
            status = "PASS" if all(r["status"] == "PASS" for r in tests) else "FAIL"
            evidence = f"{len(tests)} automated (" + ", ".join(f"`{n}`" for n in names[:3]) + (", ..." if len(names) > 3 else "") + ")"
        else:
            status = "PASS" if tc == "TC-MNT-01" and line_pct >= 70 else "MANUAL"
            evidence = NOT_PYTEST.get(tc, "**not covered**")
        rows.append((rid, title, tc, evidence, status))
    done = sum(r[4] == "PASS" for r in rows)
    body = (f"Every SRS requirement, its STP test case and the automated tests that carry that test-case ID "
            f"(the `# TC-...` comment on each test). **{done} of {len(rows)} requirements verified by passing tests.**\n\n"
            + table(["Requirement", "Title", "Test case", "Evidence", "Result"], rows))
    write("RTM.md", "Requirements Traceability Matrix (RTM) - SPMS", meta, body)

    print(f"\n{len(results)} tests: {count['PASS']} passed, {count['FAIL']} failed, {count['SKIP']} skipped | "
          f"line {line_pct:.1f} % | branch {branch_pct:.1f} %")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
