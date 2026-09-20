"""ধাপ ১ - the gate is wired to the publish, not only written to a file.

ধারা ৪.০ says `unexplained_live_loss = 0` or the publish is BLOCKed. A report
that states the number and a publisher that never reads it would satisfy the
letter of that and none of its purpose, so these tests drive
`scanner.output.publish_scan_outputs` itself and check what ends up on disk.

The behaviour being protected, in one line each:

  * a blocked gate leaves the previous catalogue exactly where it was;
  * an unevaluable gate blocks - a gate that fails open is not a gate;
  * no gate at all changes nothing, so channels and events runs are untouched;
  * a missing poster, a missing category and a missing metadata field are not
    losses and never block.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import movie_baseline, movie_coverage  # noqa: E402
from scanner.output import publish_scan_outputs  # noqa: E402


def _movie(index: int, category: str = "Bangla") -> dict:
    return {
        "id": f"film-{index}",
        "name": f"Film {index} (2026)",
        "url": f"https://cdn.test/movies/film-{index}.mkv",
        "backups": [],
        "category": category,
        "year": 2026,
        "source_id": "sm-movie-combined",
        "verified": True,
        "publish_allowed": True,
    }


def _paginated(cards: list, slug: str = "bangla", category: str = "Bangla") -> dict:
    return {
        category: {
            "index": {
                "slug": slug,
                "count": len(cards),
                "total_pages": 1,
                "page_size": 100,
            },
            "page_contents": {
                "page-001.json": {"count": len(cards), "items": cards},
            },
        }
    }


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class PublishGateTests(unittest.TestCase):
    """What reaches `data/movies` when the gate speaks."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.state = self.root / "state"
        self.reports = self.root / "reports"
        self.settings = self.root / "settings.json"
        _write(self.settings, {
            "movie_failure_protection": {
                "enabled": True,
                "maximum_drop_percentage": 40,
                "minimum_previous_count": 100,
            },
            "notifications": {"telegram_enabled": False},
        })

        # A catalogue already live: ten films, one page.
        self.previous_cards = [_movie(index) for index in range(10)]
        _write(self.data / "movies" / "bangla" / "index.json", {
            "slug": "bangla", "count": 10, "total_pages": 1,
        })
        _write(self.data / "movies" / "bangla" / "page-001.json", {
            "count": 10, "items": self.previous_cards,
        })
        _write(self.data / "manifest.json", {
            "schema_version": 1,
            "movies": {"Bangla": {
                "count": 10, "visible": True, "total_pages": 1,
                "index": "data/movies/bangla/index.json",
            }},
        })

    def _publish(self, cards, coverage):
        return publish_scan_outputs(
            movies_data=_paginated(cards),
            settings_path=str(self.settings),
            data_dir=str(self.data),
            state_dir=str(self.state),
            reports_dir=str(self.reports),
            scan_mode="movies",
            movie_coverage=coverage,
        )

    def _published_names(self):
        page = self.data / "movies" / "bangla" / "page-001.json"
        payload = json.loads(page.read_text(encoding="utf-8"))
        return [item["name"] for item in payload.get("items") or ()]

    def _coverage(self, published_cards, previous_cards=None):
        previous = movie_baseline.inventory_lines(
            [("bangla", card) for card in (
                self.previous_cards if previous_cards is None else previous_cards)]
        )
        current = movie_baseline.inventory_lines(
            [("bangla", card) for card in published_cards])
        return movie_coverage.build_movie_coverage(
            configured_sources=[
                {"id": "sm-movie-combined", "name": "SM", "enabled": True}],
            raw_entries=[dict(card, source_pipeline="movies")
                         for card in published_cards],
            published_movies=[("bangla", card) for card in published_cards],
            previous_inventory=previous,
            current_inventory=current,
        )

    def test_a_clean_gate_publishes_normally(self) -> None:
        cards = self.previous_cards + [_movie(99)]
        report = self._coverage(cards)
        self.assertFalse(report["invariants"]["block"])

        summary = self._publish(cards, report)
        self.assertFalse(summary["movie_output_preserved"])
        self.assertEqual(len(self._published_names()), 11)

    def test_an_unexplained_loss_preserves_the_previous_catalogue(self) -> None:
        """The whole point of ধারা ৪.০: a stream gone with no explanation
        stops the publish instead of quietly shrinking the site."""
        cards = self.previous_cards[:6]
        report = self._coverage(cards)
        self.assertEqual(report["invariant_2"]["unexplained_live_loss"], 4)
        self.assertTrue(report["invariants"]["block"])

        summary = self._publish(cards, report)

        self.assertTrue(summary["movie_output_preserved"])
        self.assertEqual(
            len(self._published_names()), 10,
            msg="the previous catalogue must be exactly where it was",
        )
        self.assertEqual(
            summary["manifest_summary"]["movies"]["Bangla"]["count"], 10)

    def test_the_block_is_reported_where_the_numbers_are_read(self) -> None:
        cards = self.previous_cards[:6]
        summary = self._publish(cards, self._coverage(cards))

        warnings = json.loads(
            (self.reports / "output-safety.json").read_text(encoding="utf-8"))
        kinds = [item.get("type") for item in warnings.get("warnings") or ()]
        self.assertIn("movie_no_loss_gate_block", kinds)

        blocked = next(item for item in warnings["warnings"]
                       if item["type"] == "movie_no_loss_gate_block")
        self.assertEqual(blocked["unexplained_live_loss"], 4)
        self.assertEqual(blocked["status"], "previous_output_preserved")
        self.assertTrue(blocked["block_reasons"])
        self.assertEqual(summary["status"], "completed_with_warnings")

    def test_a_failing_invariant_reaches_the_scan_summary(self) -> None:
        """A check that fails quietly is the same as no check."""
        cards = self.previous_cards[:6]
        summary = self._publish(cards, self._coverage(cards))
        self.assertIn(
            "movie: INVARIANT_2 unexplained_live_loss == 0",
            summary["coverage_invariant_failures"],
        )

    def test_a_gate_that_cannot_be_evaluated_blocks(self) -> None:
        """Fail closed. A gate that fails open is not a gate."""
        summary = self._publish(
            self.previous_cards[:1],
            {"kind": "movie_source_coverage", "invariants": "not-a-mapping"},
        )
        self.assertTrue(summary["movie_output_preserved"])
        self.assertEqual(len(self._published_names()), 10)

    def test_a_gate_build_failure_report_blocks(self) -> None:
        """What `scan.py` writes when the gate itself could not be built."""
        summary = self._publish(self.previous_cards[:1], {
            "kind": "movie_source_coverage",
            "invariants": {
                "block": True,
                "failures": ["gate_build_failed"],
                "block_reasons": ["the no-loss gate could not be built: boom"],
            },
        })
        self.assertTrue(summary["movie_output_preserved"])
        self.assertIn(
            "movie: gate_build_failed",
            summary["coverage_invariant_failures"],
        )

    def test_no_gate_at_all_leaves_the_old_behaviour_untouched(self) -> None:
        """A channels or events run passes nothing, and must be unaffected."""
        cards = self.previous_cards + [_movie(99)]
        summary = self._publish(cards, None)
        self.assertFalse(summary["movie_output_preserved"])
        self.assertEqual(len(self._published_names()), 11)

    def test_the_percentage_guard_still_works_on_its_own(self) -> None:
        """A-04 was replaced by the gate, not deleted. Both run."""
        # Enough previous cards for the guard's minimum, and a collapse past
        # its threshold, with a gate that sees no unexplained loss because the
        # films are still reachable under their own ids.
        many = [_movie(index) for index in range(200)]
        _write(self.data / "movies" / "bangla" / "index.json", {
            "slug": "bangla", "count": 200, "total_pages": 1,
        })
        _write(self.data / "movies" / "bangla" / "page-001.json", {
            "count": 200, "items": many,
        })
        _write(self.data / "manifest.json", {
            "schema_version": 1,
            "movies": {"Bangla": {
                "count": 200, "visible": True, "total_pages": 1,
                "index": "data/movies/bangla/index.json",
            }},
        })
        kept = many[:50]
        # A gate with no "before" makes no claim about preservation, so only
        # the percentage guard can object here.
        report = movie_coverage.build_movie_coverage(
            configured_sources=[{"id": "sm-movie-combined", "enabled": True}],
            raw_entries=[dict(card, source_pipeline="movies") for card in kept],
            published_movies=[("bangla", card) for card in kept],
        )
        self.assertFalse(report["invariants"]["block"])

        summary = self._publish(kept, report)
        self.assertTrue(summary["movie_output_preserved"])
        warnings = json.loads(
            (self.reports / "output-safety.json").read_text(encoding="utf-8"))
        kinds = [item.get("type") for item in warnings.get("warnings") or ()]
        self.assertIn("movie_output_safety_warning", kinds)
        self.assertNotIn("movie_no_loss_gate_block", kinds)

    def test_missing_metadata_poster_or_category_never_blocks(self) -> None:
        """ধারা ৪.০'s five forbidden reasons, at the publish boundary."""
        stripped = []
        for card in self.previous_cards:
            copy = dict(card)
            copy.pop("year", None)
            copy["logo"] = ""
            copy["category"] = "Mix"
            copy["metadata_pending"] = True
            copy["artwork_pending"] = True
            copy["category_pending"] = True
            stripped.append(copy)

        report = self._coverage(stripped)
        self.assertEqual(report["invariant_2"]["unexplained_live_loss"], 0)
        self.assertFalse(report["invariants"]["block"])

        summary = self._publish(stripped, report)
        self.assertFalse(summary["movie_output_preserved"])
        self.assertEqual(len(self._published_names()), 10)


class ScanWiringTests(unittest.TestCase):
    """The gate has to be built where both sides of the question exist."""

    def test_scan_captures_the_raw_entries_the_gate_counts(self) -> None:
        """INVARIANT ১ counts what the sources produced, and that only exists
        before the planner replaces `items` with the candidate pool."""
        source = (Path(__file__).resolve().parents[1] / "scan.py").read_text(
            encoding="utf-8")
        self.assertIn('normalized["raw_collected_items"]', source)
        self.assertIn("_build_movie_coverage(", source)
        self.assertIn("movie_coverage=movie_coverage_report", source)

    def test_the_previous_inventory_is_read_before_processing(self) -> None:
        """Nothing this run does may change what it claims was live before."""
        source = (Path(__file__).resolve().parents[1] / "scan.py").read_text(
            encoding="utf-8")
        before = source.index("movie_previous_inventory = _movie_previous_inventory()")
        after = source.index("movies_data = process_movies()")
        self.assertLess(before, after)

    def test_the_gate_is_built_after_series_are_prepared(self) -> None:
        """Or an episode this run produced reads as a lost movie stream."""
        source = (Path(__file__).resolve().parents[1] / "scan.py").read_text(
            encoding="utf-8")
        prepared = source.index("prepared_series = prepare_manual_series(")
        gate = source.index("movie_coverage_report = _build_movie_coverage(")
        self.assertLess(prepared, gate)


if __name__ == "__main__":
    unittest.main()
