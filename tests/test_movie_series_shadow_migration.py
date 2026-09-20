"""ধাপ ৩ক - the dry run that has to balance before any card is rebuilt.

563 cards are about to be reorganised. The plan makes one rehearsal mandatory
first, and gives it a single pass/fail condition:

    before_stream_count = after_movie_streams
                        + after_series_streams
                        + merged_backups
                        + pending_visible_streams

The equation is only worth something if the four terms are a *partition* -
every link in exactly one of them. A term that double-counts makes the sum
balance while a stream goes missing, which is the failure this whole step
exists to prevent, so the partition is what most of these tests check.

The other thing tested here is that the rehearsal is a rehearsal: it must not
write to the catalogue it is describing.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_baseline  # noqa: E402


def _load_script():
    """The script is not importable by name (it has hyphens), so load it."""
    path = ROOT / "scripts" / "movie-series-shadow-migration.py"
    spec = importlib.util.spec_from_file_location("shadow_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shadow = _load_script()


def _card(identity, name, url, backups=()):
    return {
        "id": identity,
        "name": name,
        "url": url,
        "backups": [{"url": backup} for backup in backups],
        "verification_status": "verified_global",
    }


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class PartitionTests(unittest.TestCase):
    """Every link lands in exactly one term."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="shadow-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _publish(self, cards, slug="mix"):
        _write(self.root / "data" / "movies" / slug / "index.json",
               {"slug": slug, "count": len(cards), "total_pages": 1})
        _write(self.root / "data" / "movies" / slug / "page-001.json",
               {"count": len(cards), "items": cards})

    def test_a_plain_film_counts_only_as_a_movie_stream(self) -> None:
        self._publish([_card("drishyam-2", "Drishyam 2",
                             "https://cdn.test/d2.mkv", ["https://b.test/d2.mkv"])])
        report = shadow.build(self.root)
        coverage = report["stream_coverage"]
        self.assertEqual(coverage["before_stream_count"], 2)
        self.assertEqual(coverage["after_movie_streams"], 2)
        self.assertEqual(coverage["after_series_streams"], 0)
        self.assertEqual(coverage["merged_backups"], 0)
        self.assertEqual(coverage["pending_visible_streams"], 0)
        self.assertTrue(coverage["balanced"])

    def test_an_episode_splits_into_a_primary_and_its_backups(self) -> None:
        self._publish([
            _card("bp-1", "Bachelor Point S04E12",
                  "https://cdn.test/bp12.mkv",
                  ["https://b1.test/bp12.mkv", "https://b2.test/bp12.mkv"]),
        ])
        coverage = shadow.build(self.root)["stream_coverage"]
        self.assertEqual(coverage["before_stream_count"], 3)
        self.assertEqual(coverage["after_series_streams"], 1)
        self.assertEqual(coverage["merged_backups"], 2)
        self.assertEqual(coverage["after_movie_streams"], 0)
        self.assertTrue(coverage["balanced"])

    def test_a_pending_card_is_its_own_term_not_part_of_the_movie_total(self) -> None:
        """ধারা ৪.৬ tier 3 stays a visible movie card. Folding it into
        `after_movie_streams` would make it impossible to see how many links
        are waiting on a classification."""
        self._publish([
            _card("lonely", "Lonely Show S01 Dual Audio",
                  "https://cdn.test/l.mkv", ["https://b.test/l.mkv"]),
        ])
        coverage = shadow.build(self.root)["stream_coverage"]
        self.assertEqual(coverage["pending_visible_streams"], 2)
        self.assertEqual(coverage["after_movie_streams"], 0)
        self.assertTrue(coverage["balanced"])

    def test_the_four_terms_balance_on_a_mixed_catalogue(self) -> None:
        self._publish([
            _card("d2", "Drishyam 2", "https://cdn.test/d2.mkv"),
            _card("bp1", "Bachelor Point S04E12", "https://cdn.test/bp12.mkv",
                  ["https://b.test/bp12.mkv"]),
            _card("bp2", "Bachelor Point S04E13", "https://cdn.test/bp13.mkv"),
            _card("pack", "Dark Desire S02 Complete Season",
                  "https://cdn.test/dd.mkv"),
            _card("lonely", "Lonely Show S01", "https://cdn.test/l.mkv"),
            _card("nameless", "S01E01", "https://cdn.test/n.mkv"),
        ])
        report = shadow.build(self.root)
        coverage = report["stream_coverage"]
        self.assertEqual(coverage["before_stream_count"], 7)
        self.assertEqual(
            coverage["sum_of_terms"], coverage["before_stream_count"])
        self.assertTrue(coverage["balanced"])
        self.assertEqual(coverage["difference"], 0)

    def test_a_marker_with_no_show_name_stays_a_movie_card(self) -> None:
        """Grouping it would be the over-merge this step exists to catch."""
        self._publish([_card("nameless", "S01E01", "https://cdn.test/n.mkv")])
        report = shadow.build(self.root)
        self.assertEqual(len(report["unkeyed_series_signals"]), 1)
        self.assertEqual(report["stream_coverage"]["after_movie_streams"], 1)
        self.assertEqual(report["catalogue_after_proposed"]["series_shows_created"], 0)

    def test_one_show_across_several_years_becomes_one_proposed_show(self) -> None:
        self._publish([
            _card("hotd1", "House of the Dragon (2022) S01E01",
                  "https://cdn.test/h1.mkv"),
            _card("hotd2", "House Of The Dragon (2024) S02E07 Dual",
                  "https://cdn.test/h2.mkv"),
            _card("hotd3", "House of the Dragon (2026) S03E04",
                  "https://cdn.test/h3.mkv"),
        ])
        report = shadow.build(self.root)
        self.assertEqual(len(report["proposed_shows"]), 1)
        self.assertEqual(report["proposed_shows"][0]["cards_merged"], 3)
        self.assertEqual(
            report["proposed_shows"][0]["years_seen"], [2022, 2024, 2026])

    def test_several_years_raise_an_over_merge_warning_for_a_person(self) -> None:
        """A year is not evidence of a different show, so this warns and does
        not act."""
        self._publish([
            _card("hotd1", "House of the Dragon (2022) S01E01",
                  "https://cdn.test/h1.mkv"),
            _card("hotd2", "House of the Dragon (2026) S03E04",
                  "https://cdn.test/h2.mkv"),
        ])
        report = shadow.build(self.root)
        self.assertEqual(len(report["over_merge_warnings"]), 1)
        self.assertEqual(len(report["proposed_shows"]), 1)


class UnderMergeTests(unittest.TestCase):
    """One show left in two cards."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="shadow-under-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _publish_movies(self, cards):
        _write(self.root / "data" / "movies" / "mix" / "index.json",
               {"slug": "mix", "count": len(cards), "total_pages": 1})
        _write(self.root / "data" / "movies" / "mix" / "page-001.json",
               {"count": len(cards), "items": cards})

    def test_a_show_the_site_already_publishes_is_flagged(self) -> None:
        """Measured on the real catalogue: 15 of 114 proposed shows already
        exist as series, so ধাপ ৩খ has to merge into them rather than create a
        second card for each."""
        self._publish_movies([
            _card("r1", "Resort S01E05", "https://cdn.test/r5.mkv"),
        ])
        _write(self.root / "data" / "series" / "premium" / "index.json", {
            "count": 1, "items": [{"id": "resort", "name": "Resort"}],
        })
        report = shadow.build(self.root)
        kinds = [w["kind"] for w in report["under_merge_warnings"]]
        self.assertIn("already_published_as_a_series", kinds)

    def test_a_key_that_is_a_prefix_of_another_is_flagged(self) -> None:
        self._publish_movies([
            _card("b1", "Bigg Boss S01E01", "https://cdn.test/b1.mkv"),
            _card("b2", "Bigg Boss OTT S01E01", "https://cdn.test/b2.mkv"),
        ])
        report = shadow.build(self.root)
        kinds = [w["kind"] for w in report["under_merge_warnings"]]
        self.assertIn("prefix_of_another_proposed_show", kinds)
        # Reported, never merged: these really are two different shows.
        self.assertEqual(len(report["proposed_shows"]), 2)


class RehearsalIsARehearsalTests(unittest.TestCase):
    """"production-এ কিছুই লেখা হবে না — শুধু রিপোর্ট"."""

    ROOT = ROOT

    def test_building_the_report_does_not_touch_the_catalogue(self) -> None:
        baseline_path = self.ROOT / movie_baseline.DEFAULT_BASELINE_PATH
        if not baseline_path.is_file():
            self.skipTest("no baseline to compare against")
        baseline = movie_baseline.load_baseline(root=self.ROOT)
        self.assertEqual(
            movie_baseline.verify_worktree(baseline, self.ROOT), [],
            msg="the catalogue was already modified before this test ran",
        )
        shadow.build(self.ROOT)
        self.assertEqual(
            movie_baseline.verify_worktree(baseline, self.ROOT), [],
            msg="the shadow migration wrote to the catalogue it is describing",
        )


class RealReportTests(unittest.TestCase):
    """The committed dry run is the gate ধাপ ৩খ reads."""

    PATH = ROOT / "reports" / "movie-series-migration-dryrun.json"

    def setUp(self) -> None:
        if not self.PATH.is_file():
            self.skipTest("no dry-run report yet")
        self.report = json.loads(self.PATH.read_text(encoding="utf-8"))

    def test_the_stream_coverage_identity_balances(self) -> None:
        coverage = self.report["stream_coverage"]
        self.assertEqual(
            coverage["before_stream_count"],
            coverage["after_movie_streams"] + coverage["after_series_streams"]
            + coverage["merged_backups"] + coverage["pending_visible_streams"],
        )
        self.assertTrue(coverage["balanced"])

    def test_it_accounts_for_every_link_the_site_serves(self) -> None:
        counts = movie_baseline.catalogue_counts(ROOT)
        self.assertEqual(
            self.report["stream_coverage"]["before_stream_count"],
            counts["movie_links_total"],
        )

    def test_no_proposed_show_carries_a_year_in_its_key(self) -> None:
        import re

        for row in self.report["proposed_shows"]:
            self.assertIsNone(
                re.search(r"\b(?:19|20)\d{2}\b", row["show_key"]),
                msg=row["show_key"],
            )

    def test_every_pending_card_has_a_reason(self) -> None:
        for card in self.report["pending_as_movie_cards"]:
            self.assertTrue(card.get("reason"))

    def test_nothing_was_published_by_the_dry_run(self) -> None:
        self.assertIn("Nothing was published", self.report["note"])


if __name__ == "__main__":
    unittest.main()
