"""A pipeline that crashes takes only itself down.

`all` mode scans channels, movies and events in one process; a catch-up run
scans a second catalogue beside the scheduled one; the series tree is written
after the movie catalogue. In each case one failure used to discard the work
of the others.
"""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scan  # noqa: E402

SOURCE = (ROOT / "scan.py").read_text(encoding="utf-8")


class ThePolicy(unittest.TestCase):
    def setUp(self):
        scan.PIPELINE_FAILURES.clear()

    def test_a_single_pipeline_run_still_fails_loudly(self):
        with self.assertRaises(RuntimeError):
            scan._pipeline_failed("movies", RuntimeError("boom"), isolate=False)

    def test_an_isolated_failure_is_recorded_and_the_run_continues(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(scan, "PROJECT_ROOT", Path(root)):
            scan._pipeline_failed("channels", RuntimeError("bad feed"), isolate=True)
            report = (Path(root) / "reports" / "pipeline-failures.json").read_text(
                encoding="utf-8")
        self.assertEqual(scan.PIPELINE_FAILURES[0]["pipeline"], "channels")
        self.assertIn("bad feed", report)


class EveryPipelineIsWrapped(unittest.TestCase):
    def _guarded_calls(self):
        """Names of functions called inside a try whose handler calls
        _pipeline_failed."""
        tree = ast.parse(SOURCE)
        guarded = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            handler_calls = {
                getattr(call.func, "id", "")
                for handler in node.handlers for call in ast.walk(handler)
                if isinstance(call, ast.Call)}
            if "_pipeline_failed" not in handler_calls:
                continue
            for inner in node.body:
                for call in ast.walk(inner):
                    if isinstance(call, ast.Call):
                        guarded.add(getattr(call.func, "id", "")
                                    or getattr(call.func, "attr", ""))
        return guarded

    def test_channels_movies_events_and_series(self):
        guarded = self._guarded_calls()
        for function in ("process_tv_channels", "process_movies",
                         "_process_events_for_mode", "publish_prepared_series"):
            self.assertIn(function, guarded, f"{function} is not isolated")

    def test_the_series_tree_always_isolates(self):
        self.assertIn('_pipeline_failed("series", error, isolate=True)', SOURCE)


class TheCatchUpCannotAbortTheScheduledScan(unittest.TestCase):
    def test_the_catch_up_failure_is_caught(self):
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        self.assertIn('if ! python -u scan.py "$CATCHUP"', workflow)


if __name__ == "__main__":
    unittest.main()
