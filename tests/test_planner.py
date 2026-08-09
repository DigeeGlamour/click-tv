import json
import os
import tempfile
import unittest
from pathlib import Path

from scanner.normalizer import Normalizer
from scanner.planner import plan_candidates


class PlannerTests(unittest.TestCase):
    def test_movie_mode_rejects_tv_source_content_under_strict_separation(self):
        normalizer = Normalizer()
        movie = normalizer.normalize_candidate(
            {
                "name": "100 Love (2012)",
                "url": "http://example.test/Movies/indianbangla/100-love.mkv",
                "source_pipeline": "tv",
                "headers": {},
            }
        )
        live_tv = normalizer.normalize_candidate(
            {
                "name": "T Sports",
                "url": "http://example.test/live.m3u8",
                "source_pipeline": "tv",
                "group_title": "Sports",
                "headers": {},
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "config").mkdir()
            (root / "reports").mkdir()
            (root / "data").mkdir()
            settings = {
                "planning": {
                    "drop_unknown_tv_before_verification": True,
                    "initial_candidates_per_group": {
                        "tv": 1,
                        "movies": 1,
                        "today_match": 1,
                        "upcoming": 1,
                    },
                    "maximum_candidates_per_group": {
                        "tv": 6,
                        "movies": 2,
                        "today_match": 3,
                        "upcoming": 3,
                    },
                    "target_publishable_per_group": {
                        "tv": 2,
                        "movies": 1,
                        "today_match": 1,
                        "upcoming": 1,
                    },
                    "maximum_total_candidate_pool": {"movies": 100},
                }
            }
            (root / "config" / "settings.json").write_text(
                json.dumps(settings), encoding="utf-8"
            )
            old_cwd = os.getcwd()
            os.chdir(root)
            try:
                planned, summary = plan_candidates([movie, live_tv], "movies")
            finally:
                os.chdir(old_cwd)

        self.assertEqual(len(planned), 0)
        self.assertNotIn("tv->movies", summary["rerouted_counts"])


if __name__ == "__main__":
    unittest.main()
