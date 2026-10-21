"""The test gate: a code fault stops the scan, a fact about today's data does not.

Run against a throwaway project, so the cases are real unittest runs with
real failures rather than a description of them. The four shapes that
stopped the scanner in October are covered by the first three cases: a test
reading data/ in its body, in setUpClass (where the 57-episode ceiling did
its reading), and at module import.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "run-test-gate.py"


def _run(tests):
    project = Path(tempfile.mkdtemp())
    (project / "data").mkdir()
    (project / "data" / "catalogue.json").write_text(
        json.dumps({"largest_season": 61}), encoding="utf-8")
    (project / "tests").mkdir()
    for name, body in tests.items():
        (project / "tests" / name).write_text(textwrap.dedent(body), encoding="utf-8")
    report = project / "gate.json"
    completed = subprocess.run(
        [sys.executable, str(GATE), "--quiet", "--report", str(report)],
        cwd=str(project), capture_output=True, text=True, timeout=120)
    return completed.returncode, json.loads(report.read_text(encoding="utf-8")), completed.stdout


READS_IN_BODY = """
    import json, unittest
    class Panel(unittest.TestCase):
        def test_ceiling(self):
            with open("data/catalogue.json") as handle:
                size = json.load(handle)["largest_season"]
            self.assertLessEqual(size, 57)
"""

READS_IN_SETUPCLASS = """
    import json, unittest
    from pathlib import Path
    class Panel(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.size = json.loads(Path("data/catalogue.json").read_text())["largest_season"]
        def test_ceiling(self):
            self.assertLessEqual(self.size, 57)
"""

READS_AT_IMPORT = """
    import json, unittest
    SIZE = json.load(open("data/catalogue.json"))["largest_season"]
    class Panel(unittest.TestCase):
        def test_ceiling(self):
            self.assertLessEqual(SIZE, 57)
"""

CODE_FAULT = """
    import unittest
    class Code(unittest.TestCase):
        def test_arithmetic(self):
            self.assertEqual(1 + 1, 3)
"""

ALL_GOOD = """
    import unittest
    class Code(unittest.TestCase):
        def test_arithmetic(self):
            self.assertEqual(1 + 1, 2)
"""


class AFactAboutTheDataDoesNotStopTheScan(unittest.TestCase):
    def test_a_test_reading_data_in_its_body(self):
        code, report, _out = _run({"test_a.py": READS_IN_BODY})
        self.assertEqual(code, 0)
        self.assertEqual(len(report["audit_findings"]), 1)
        self.assertEqual(report["gate_failures"], [])

    def test_a_test_reading_data_in_setupclass(self):
        """The 2026-10-20 shape: the read is not inside the test method."""
        code, report, _out = _run({"test_a.py": READS_IN_SETUPCLASS})
        self.assertEqual(code, 0)
        self.assertEqual(len(report["audit_findings"]), 1)

    def test_a_test_reading_data_at_import(self):
        code, report, _out = _run({"test_a.py": READS_AT_IMPORT})
        self.assertEqual(code, 0)
        self.assertEqual(len(report["audit_findings"]), 1)

    def test_the_finding_is_still_reported_not_hidden(self):
        _code, report, out = _run({"test_a.py": READS_IN_BODY})
        self.assertIn("::warning", out)
        self.assertIn("57", report["audit_findings"][0]["summary"])


class ACodeFaultStillStopsIt(unittest.TestCase):
    def test_a_failing_code_test_blocks(self):
        code, report, out = _run({"test_a.py": CODE_FAULT})
        self.assertEqual(code, 1)
        self.assertEqual(len(report["gate_failures"]), 1)
        self.assertTrue(report["blocked"])
        self.assertIn("::error", out)

    def test_a_data_finding_does_not_mask_a_code_fault_beside_it(self):
        code, report, _out = _run({"test_a.py": READS_IN_BODY,
                                   "test_b.py": CODE_FAULT})
        self.assertEqual(code, 1)
        self.assertEqual(len(report["gate_failures"]), 1)
        self.assertEqual(len(report["audit_findings"]), 1)

    def test_a_clean_suite_passes(self):
        code, report, _out = _run({"test_a.py": ALL_GOOD})
        self.assertEqual(code, 0)
        self.assertFalse(report["blocked"])


class TheWorkflowUsesIt(unittest.TestCase):
    WORKFLOW = (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")

    def test_the_scan_gate_runs_through_the_classifier(self):
        self.assertIn("scripts/run-test-gate.py", self.WORKFLOW)

    def test_the_bare_suite_no_longer_gates_the_scan(self):
        self.assertFalse(
            'python -m unittest discover -s tests -p "test_*.py" -v' in self.WORKFLOW,
            "the scan is still gated on the bare suite")


if __name__ == "__main__":
    unittest.main()
