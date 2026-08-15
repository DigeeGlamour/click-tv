import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import scan


class ZeroCandidatePreservationTests(unittest.TestCase):
    def test_all_mode_continues_through_every_child_pipeline(self):
        original_run_pipeline = scan.run_pipeline
        visited = []

        def child_result(mode):
            visited.append(mode)
            status = "completed_preserved" if mode == "today" else "completed"
            return {"mode": mode, "status": status}

        with (
            patch.object(
                scan,
                "_load_required_json",
                return_value={"pipeline": {"sequential_full_scan": True}},
            ),
            patch.object(scan, "_ensure_project_root"),
            patch.object(scan, "_write_scan_progress"),
            patch.object(scan, "run_pipeline", side_effect=child_result),
        ):
            result = original_run_pipeline("all")

        self.assertEqual(visited, ["upcoming", "today", "channels", "movies"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["pipelines"]["today"]["status"], "completed_preserved")

    def test_today_zero_plan_preserves_existing_output(self):
        payload = {
            "mode": "today",
            "items": [{"name": "Old fixture", "url": "https://example.com/live.m3u8"}],
        }

        with tempfile.TemporaryDirectory() as directory:
            candidates_path = Path(directory) / "candidates.json"
            with (
                patch.object(scan, "CANDIDATES_PATH", candidates_path),
                patch.object(scan, "normalize_all_candidates", return_value=payload["items"]),
                patch.object(scan, "plan_candidates", return_value=([], {"dropped": {}})),
            ):
                result = scan._normalize_candidate_payload(payload)

        self.assertTrue(result["preserve_existing_output"])
        self.assertEqual(result["planned_candidate_count"], 0)

    def test_static_zero_plan_remains_fatal(self):
        payload = {
            "mode": "channels",
            "items": [{"name": "Channel", "url": "https://example.com/live.m3u8"}],
        }

        with (
            patch.object(scan, "normalize_all_candidates", return_value=payload["items"]),
            patch.object(scan, "plan_candidates", return_value=([], {"dropped": {}})),
        ):
            with self.assertRaisesRegex(RuntimeError, "zero candidates"):
                scan._normalize_candidate_payload(payload)

    def test_preserved_run_writes_reports_without_replacing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "reports").mkdir()
            scan._atomic_write_json(
                root / "data" / "manifest.json",
                {
                    "channels": {"bangla": {"count": 2}},
                    "movies": {"bangla": {"count": 3}},
                    "today_match": {"count": 4},
                    "upcoming": {"count": 5},
                },
            )
            with (
                patch.object(scan, "PROJECT_ROOT", root),
                patch.object(scan, "SCAN_PROGRESS_PATH", root / "working" / "scan-progress.json"),
            ):
                result = scan._complete_preserved_event_run(
                    "today",
                    {
                        "preservation_reason": "test",
                        "raw_candidate_count": 1,
                        "normalized_candidate_count": 1,
                    },
                    datetime.now(timezone.utc),
                )

            self.assertEqual(result["status"], "completed_preserved")
            self.assertEqual(result["totals"]["today_match"], 4)
            self.assertFalse((root / "data" / "today-match.json").exists())
            self.assertTrue((root / "reports" / "scan-summary-today.json").exists())


if __name__ == "__main__":
    unittest.main()
