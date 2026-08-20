"""Each content category owns its own source file, and the scanner CLI must
survive a legacy Windows console.

Two production failures are locked down here:

1. Every category's sources used to live in one config/sources.json, so a Today
   Match edit sat in the same file as the TV and Movie lists. They are now one
   file per category under config/sources/, and collect_candidates() has to
   assemble them back into the same shape the rest of the pipeline expects.

2. scan.py prints progress with emoji. A Windows console defaults to cp1252, so
   the first print raised UnicodeEncodeError and the local PC scan died before
   reading a single source, while the Linux CI runner was unaffected.
"""

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scan  # noqa: E402
from scanner.source_loader import (  # noqa: E402
    SOURCE_PIPELINE_FILES,
    load_sources_config,
)


class PerCategorySourceFileTests(unittest.TestCase):
    def test_every_category_has_its_own_file(self):
        for filename in SOURCE_PIPELINE_FILES.values():
            path = ROOT / "config" / "sources" / filename
            self.assertTrue(path.is_file(), f"missing {path}")

    def test_live_config_loads_every_pipeline(self):
        config = load_sources_config(ROOT / "config")
        for pipeline in ("tv", "movies", "today_match", "upcoming"):
            self.assertIsInstance(config[pipeline], list)
        for pipeline in ("tv", "movies", "today_match"):
            self.assertTrue(config[pipeline], f"{pipeline} is empty")
        # upcoming.json is intentionally an empty list: every event feed is
        # registered once under today_match, and a record reaches Today or
        # Upcoming by the status it carries. Listing the same URL in both files
        # only fetched and parsed it twice.
        self.assertEqual(config["upcoming"], [])
        self.assertIsInstance(config["manual"], dict)

    def test_source_ids_are_unique_across_every_category(self):
        config = load_sources_config(ROOT / "config")
        ids = [
            source["id"]
            for pipeline in ("tv", "movies", "today_match", "upcoming")
            for source in config[pipeline]
        ]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_two_requested_live_event_sources_are_registered_for_today(self):
        """Both are read as JSON now. The m3u mirror of the same playlist
        carries a name and a URL per line and nothing else; the JSON carries the
        status, the kickoff, the league and team logos, and every server rather
        than one."""
        config = load_sources_config(ROOT / "config")
        urls = {source["url"] for source in config["today_match"]}
        self.assertIn(
            "https://raw.githubusercontent.com/srhady/bingstream/refs/heads/main/playlist.json",
            urls,
        )
        self.assertIn(
            "https://raw.githubusercontent.com/srhady/axsports/refs/heads/main/live_sports.json",
            urls,
        )

    def test_editing_one_category_cannot_disturb_another(self):
        """The point of the split: a Today Match file with a syntax error must
        not take the TV list down with it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            (config_dir / "sources").mkdir(parents=True)
            (config_dir / "sources" / "tv.json").write_text(
                json.dumps({"sources": [{"id": "tv-one"}]}), encoding="utf-8"
            )
            (config_dir / "sources" / "today-match.json").write_text(
                "{ this is not valid json", encoding="utf-8"
            )

            config = load_sources_config(config_dir)

        self.assertEqual(config["tv"], [{"id": "tv-one"}])
        self.assertNotIn("today_match", config)

    def test_a_bare_list_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            (config_dir / "sources").mkdir(parents=True)
            (config_dir / "sources" / "upcoming.json").write_text(
                json.dumps([{"id": "up-one"}]), encoding="utf-8"
            )
            config = load_sources_config(config_dir)
        self.assertEqual(config["upcoming"], [{"id": "up-one"}])

    def test_a_category_without_a_file_falls_back_to_the_legacy_monolith(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            (config_dir / "sources").mkdir(parents=True)
            (config_dir / "sources" / "tv.json").write_text(
                json.dumps({"sources": [{"id": "tv-new"}]}), encoding="utf-8"
            )
            (config_dir / "sources.json").write_text(
                json.dumps({"tv": [{"id": "tv-old"}], "movies": [{"id": "movie-old"}]}),
                encoding="utf-8",
            )
            config = load_sources_config(config_dir)

        self.assertEqual(config["tv"], [{"id": "tv-new"}])
        self.assertEqual(config["movies"], [{"id": "movie-old"}])


class ConsoleEncodingTests(unittest.TestCase):
    def test_emoji_progress_survives_a_cp1252_console(self):
        original_stdout, original_stderr = sys.stdout, sys.stderr
        # Exactly what Python hands scan.py on a default Windows console.
        cp1252_console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        try:
            sys.stdout = cp1252_console
            sys.stderr = cp1252_console
            scan._force_utf8_console()
            print("\U0001f680 LIVE SIGNAL SCANNER")
            sys.stdout.flush()
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr

        self.assertEqual(cp1252_console.encoding, "utf-8")

    def test_a_stream_that_cannot_be_reconfigured_is_left_alone(self):
        """unittest and pytest both swap stdout for a plain buffer."""
        original_stdout, original_stderr = sys.stdout, sys.stderr
        try:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            scan._force_utf8_console()  # must not raise
        finally:
            sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == "__main__":
    unittest.main()
