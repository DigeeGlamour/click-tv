#!/usr/bin/env python3
"""The test gate in front of every scan - code faults block, data drift warns.

    python scripts/run-test-gate.py
    python scripts/run-test-gate.py --report /tmp/gate.json --failures-file /tmp/gate.txt

Why this exists
---------------
The whole suite used to gate every scan, and part of it does not test code at
all: it reads the published catalogue in ``data/`` and asserts something
about it. ``data/`` is exactly what a scan rewrites, so when the world moved
underneath one of those assertions the suite failed, the scan never started,
and the only thing that could have moved the data back was the scan. Four
times in three weeks:

    2026-10-04  test_promote_proven_channels            13 days with no scan
    2026-10-17  available_link_count vs playback_id     a card telling the truth
    2026-10-17  Upcoming badge 15 seconds past kickoff  a timing race
    2026-10-20  largest season <= 57 episodes           a show reached 61

Each one was fixed, and the next one was always going to arrive, because the
shape was never the individual test. One episode more on one show should not
be able to stop the Today Match scan.

What it does
------------
It runs the same suite, unchanged, and watches which tests *read anything
under data/* while they run - including in ``setUpClass`` and at module
import, where the panel-scale test does its reading. Then it splits the
result:

    gate failure    a test that never touched data/ failed. That is a fault
                    in the code, and the scan must not run on it. Exit 1.
    audit finding   a test that read the published data failed. That is a
                    fact about today's catalogue. It is printed as a GitHub
                    warning, written to the report and sent on, and the scan
                    goes ahead - the scan is the only thing that can change it.

Nothing is skipped and no assertion is loosened. Every test still runs and
every failure is still reported; what changes is which of them is allowed to
stop the whole system.

The classification is measured rather than declared, so a test written next
month is classified correctly without anyone remembering to mark it.
"""
from __future__ import annotations

import argparse
import builtins
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(os.getcwd()).resolve()
DATA_ROOT = (ROOT / "data").resolve()

#: The test running right now, and whether it has read data/ yet.
_ACTIVE: Dict[str, Any] = {"id": "", "touched": False}
#: Classes and modules that read data/ outside a test method - setUpClass,
#: setUpModule, or module scope. Every test in them is an audit.
_DATA_CLASSES: Set[str] = set()
_DATA_MODULES: Set[str] = set()


def _under_data(target: Any) -> bool:
    try:
        if isinstance(target, int):
            return False
        path = Path(os.fsdecode(target))
        if not path.is_absolute():
            path = Path(os.getcwd()) / path
        resolved = Path(os.path.abspath(path))
    except Exception:  # noqa: BLE001 - never let the tracker break a test
        return False
    try:
        resolved.relative_to(DATA_ROOT)
        return True
    except ValueError:
        return False


def _attribute_to_owner() -> None:
    """A data read with no test running: find the class or module doing it."""
    frame = sys._getframe(2)
    while frame is not None:
        local = frame.f_locals
        owner = local.get("cls")
        if isinstance(owner, type) and issubclass(owner, unittest.TestCase):
            _DATA_CLASSES.add(f"{owner.__module__}.{owner.__qualname__}")
            return
        instance = local.get("self")
        if isinstance(instance, unittest.TestCase):
            kind = type(instance)
            _DATA_CLASSES.add(f"{kind.__module__}.{kind.__qualname__}")
            return
        module = frame.f_globals.get("__name__", "")
        if module.split(".")[-1].startswith("test_"):
            _DATA_MODULES.add(module)
            return
        frame = frame.f_back


_REAL_OPEN = builtins.open


def _tracking_open(file: Any, *args: Any, **kwargs: Any):
    if _under_data(file):
        if _ACTIVE["id"]:
            _ACTIVE["touched"] = True
        else:
            _attribute_to_owner()
    return _REAL_OPEN(file, *args, **kwargs)


def _install() -> None:
    builtins.open = _tracking_open
    io.open = _tracking_open


def _uninstall() -> None:
    builtins.open = _REAL_OPEN
    io.open = _REAL_OPEN


class _GateResult(unittest.TextTestResult):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.touched_tests: Set[str] = set()
        self.problems: List[Dict[str, str]] = []

    def startTest(self, test: unittest.TestCase) -> None:
        _ACTIVE["id"] = test.id()
        _ACTIVE["touched"] = False
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
        if _ACTIVE["touched"]:
            self.touched_tests.add(test.id())
        _ACTIVE["id"] = ""
        _ACTIVE["touched"] = False
        super().stopTest(test)

    def _record(self, test: Any, kind: str, err: Any) -> None:
        self.problems.append({
            "id": test.id() if hasattr(test, "id") else str(test),
            "kind": kind,
            "detail": self._exc_info_to_string(err, test)
            if err is not None else "",
            "touched_now": "yes" if _ACTIVE["touched"] else "",
        })

    def addFailure(self, test: Any, err: Any) -> None:
        super().addFailure(test, err)
        self._record(test, "failure", err)

    def addError(self, test: Any, err: Any) -> None:
        super().addError(test, err)
        self._record(test, "error", err)

    def addSubTest(self, test: Any, subtest: Any, err: Any) -> None:
        super().addSubTest(test, subtest, err)
        if err is not None:
            failed = issubclass(err[0], test.failureException)
            self._record(subtest, "failure" if failed else "error", err)

    def addUnexpectedSuccess(self, test: Any) -> None:
        super().addUnexpectedSuccess(test)
        self.problems.append({"id": test.id(), "kind": "unexpected_success",
                              "detail": "", "touched_now": ""})


def _owner_ids(test_id: str) -> List[str]:
    """`module.Class.method` -> its module and class; also unwraps the
    `setUpClass (module.Class)` ids unittest gives a class-level error."""
    text = test_id
    if "(" in text and text.endswith(")"):
        text = text[text.index("(") + 1:-1]
    parts = text.split(" ")[0].split(".")
    owners = []
    for cut in range(1, len(parts) + 1):
        owners.append(".".join(parts[:cut]))
    return owners


def classify(problem: Dict[str, str], touched_tests: Set[str]) -> str:
    test_id = problem["id"].split(" ")[0]
    if problem.get("touched_now") or test_id in touched_tests:
        return "audit"
    for owner in _owner_ids(problem["id"]):
        if owner in _DATA_CLASSES or owner in _DATA_MODULES:
            return "audit"
    return "gate"


def _short(detail: str) -> str:
    lines = [line for line in detail.strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--report", default="")
    parser.add_argument("--failures-file", default="")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    _install()
    started = time.monotonic()
    try:
        suite = unittest.defaultTestLoader.discover(
            arguments.start, pattern=arguments.pattern, top_level_dir=None)
        runner = unittest.TextTestRunner(
            verbosity=1 if arguments.quiet else 2, resultclass=_GateResult,
            stream=sys.stdout)
        result = runner.run(suite)
    finally:
        _uninstall()

    gate: List[Dict[str, str]] = []
    audit: List[Dict[str, str]] = []
    for problem in result.problems:
        (audit if classify(problem, result.touched_tests) == "audit"
         else gate).append(problem)

    print("\n" + "=" * 72)
    print("TEST GATE  ran %d  gate failures %d  data-audit findings %d  (%.0fs)"
          % (result.testsRun, len(gate), len(audit), time.monotonic() - started))
    for problem in audit:
        print("::warning title=Data audit (does not block the scan)::%s - %s"
              % (problem["id"], _short(problem["detail"])))
    for problem in gate:
        print("::error title=Code test failed (blocks the scan)::%s - %s"
              % (problem["id"], _short(problem["detail"])))
    print("=" * 72)

    report = {
        "kind": "test_gate",
        "tests_run": result.testsRun,
        "skipped": len(result.skipped),
        "gate_failures": [{"id": p["id"], "kind": p["kind"],
                           "summary": _short(p["detail"])} for p in gate],
        "audit_findings": [{"id": p["id"], "kind": p["kind"],
                            "summary": _short(p["detail"])} for p in audit],
        "tests_reading_data": len(result.touched_tests),
        "classes_reading_data": sorted(_DATA_CLASSES),
        "modules_reading_data": sorted(_DATA_MODULES),
        "blocked": bool(gate),
    }
    if arguments.report:
        Path(arguments.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if arguments.failures_file:
        lines = ["BLOCK " + p["id"] + " - " + _short(p["detail"]) for p in gate]
        lines += ["AUDIT " + p["id"] + " - " + _short(p["detail"]) for p in audit]
        Path(arguments.failures_file).write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return 1 if gate else 0


if __name__ == "__main__":
    raise SystemExit(main())
