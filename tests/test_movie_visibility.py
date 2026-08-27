"""Why the movie catalogue looked frozen, and what now stops it.

The complaint was that no new films ever appeared - the same ones every time,
and only a few of them. Every part of that turned out to be measurable, and
only one part was what it looked like.

  21,411 candidates were collected and 942 published. The other 20,105 failed,
  20,063 of them HTTP 404 from one CDN: the upstream playlist lists tens of
  thousands of files that no longer exist. That is an upstream problem and
  nothing here can fix it.

  New films WERE being published - 510 ids appeared between the 2026-08-22 and
  2026-08-27 scans, 285 of the published set were 2026 releases. They were
  invisible, because `year` was written only by the manual card builder:

      category      total   year set
      mix             448          0
      bangla           30         30   (all 30 manual)

  With no year, all 731 discovered movies tied in the sort and fell through to
  title order. "72 HOURS (2026)" sat behind "100 percent Love (2012)".

  383 films also VANISHED across those same two scans, out of 817. The
  category-total guard stayed silent because the total went up.

So three fixes, tested here: read the year off the title, order by when a film
was first seen, and carry a film through one failed scan.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_recency as mr  # noqa: E402
from scanner import movie_retention as mrt  # noqa: E402
from scanner import movies as M  # noqa: E402


class YearFromTitleTests(unittest.TestCase):
    """Every case here is a real title from this project's own sources."""

    def test_a_bracketed_year_is_read(self):
        self.assertEqual(mr.year_from_text("100 percent Love (2012)"), 2012)
        self.assertEqual(mr.year_from_text("72 HOURS (2026) Dual"), 2026)
        self.assertEqual(mr.year_from_text("36 Ghanta (2024) S01"), 2024)

    def test_a_bare_year_is_read(self):
        self.assertEqual(mr.year_from_text("28 Years Later 2025 Dual"), 2025)
        self.assertEqual(mr.year_from_text("9 Songs 2004 BluRay"), 2004)

    def test_a_resolution_is_not_mistaken_for_a_year(self):
        """1080p and 2160p are four-digit numbers in the right range."""
        self.assertEqual(mr.year_from_text("Some Movie 2160p HEVC 10Bit"), 0)
        self.assertEqual(mr.year_from_text("Turbozaurs S1 E14-26 720P"), 0)
        self.assertEqual(
            mr.year_from_text("Kung Fu Panda 4 2024 1080p x264 AAC2.0"), 2024
        )

    def test_an_episode_range_is_not_a_year(self):
        self.assertEqual(mr.year_from_text("Little Things S01 E01 05 Hindi"), 0)

    def test_a_year_in_the_programme_name_loses_to_the_bracketed_one(self):
        """"Reply 1988" is the show; (2015) is the release."""
        self.assertEqual(
            mr.year_from_text("Reply 1988 (2015) S01E11-15 480p"), 2015
        )

    def test_a_year_beyond_next_year_is_refused(self):
        now = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
        self.assertEqual(mr.year_from_text("Something 2099", now=now), 0)
        self.assertEqual(mr.year_from_text("Something 2027", now=now), 2027)

    def test_the_id_slug_is_a_fallback(self):
        self.assertEqual(
            mr.year_for_movie({"id": "100-percent-love-2012"}), 2012
        )

    def test_an_existing_year_is_never_overwritten(self):
        self.assertEqual(
            mr.year_for_movie({"year": 1999, "name": "Something 2024"}), 1999
        )

    def test_it_recovers_the_year_for_most_of_the_real_catalogue(self):
        """A guard on the fix's actual reach, not just its logic.

        Measured at 481 of the 731 year-less movies. Pinned loosely so an
        upstream naming change shows up as a failure here rather than as a
        silently emptier catalogue ordering.
        """
        yearless = []
        for path in sorted((ROOT / "data" / "movies").glob("*/page-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            items = payload.get("items") or []
            yearless += [
                m for m in items
                if isinstance(m, dict) and not str(m.get("year") or "").strip()
            ]
        if not yearless:
            self.skipTest("every published movie already carries a year")
        recovered = sum(1 for m in yearless if mr.year_for_movie(m))
        self.assertGreater(
            recovered / len(yearless), 0.5,
            f"only {recovered} of {len(yearless)} years could be read",
        )


class FirstSeenTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self._tmp.name) / "seen.json")
        self.addCleanup(self._tmp.cleanup)

    def test_a_first_seen_date_survives_the_next_scan(self):
        """The whole point: a film published in June is not new in August."""
        june = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        august = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
        movie = {"id": "some-film-2024", "name": "Some Film (2024)"}

        mr.stamp_first_seen([movie], path=self.path, now=june)
        self.assertTrue(movie["first_seen_at"].startswith("2026-06-01"))

        again = {"id": "some-film-2024", "name": "Some Film (2024)"}
        fresh, known = mr.stamp_first_seen([again], path=self.path, now=august)
        self.assertEqual((fresh, known), (0, 1))
        self.assertTrue(again["first_seen_at"].startswith("2026-06-01"))
        self.assertFalse(mr.is_new(again, now=august))

    def test_a_genuinely_new_movie_is_stamped_and_badged(self):
        now = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
        movie = {"id": "brand-new-2026", "name": "Brand New (2026)"}
        fresh, known = mr.stamp_first_seen([movie], path=self.path, now=now)
        self.assertEqual((fresh, known), (1, 0))
        self.assertTrue(mr.is_new(movie, now=now))

    def test_the_badge_expires(self):
        now = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
        movie = {"first_seen_at": "2026-08-01T00:00:00+00:00"}
        self.assertFalse(mr.is_new(movie, now=now))
        movie = {"first_seen_at": "2026-08-25T00:00:00+00:00"}
        self.assertTrue(mr.is_new(movie, now=now))

    def test_a_movie_with_no_identity_is_skipped_not_crashed_on(self):
        fresh, known = mr.stamp_first_seen(
            [{}, None, "nonsense"], path=self.path
        )
        self.assertEqual((fresh, known), (0, 0))

    def test_the_committed_store_was_seeded_from_history_not_from_today(self):
        """Guards against the badge being meaningless on the first run.

        An empty store would stamp all 944 movies with today and badge every
        one of them new. The committed store is seeded from the commit that
        first published each id, so the earliest entries predate this work.
        """
        store = ROOT / "state" / "movie-first-seen.json"
        if not store.exists():
            self.skipTest("no first-seen store committed")
        payload = json.loads(store.read_text(encoding="utf-8"))
        seen = payload.get("seen") or {}
        self.assertGreater(len(seen), 1000)
        months = {
            str(record.get("first_seen_at"))[:7]
            for record in seen.values()
            if isinstance(record, dict)
        }
        self.assertGreater(
            len(months), 1,
            "every entry shares one month, so the store was stamped in one go",
        )

    def test_the_store_holds_no_url(self):
        store = ROOT / "state" / "movie-first-seen.json"
        if not store.exists():
            self.skipTest("no first-seen store committed")
        blob = store.read_text(encoding="utf-8")
        self.assertNotIn("http://", blob)
        self.assertNotIn("https://", blob)


class OrderingTests(unittest.TestCase):
    """Recency leads, and it leads before pagination."""

    @staticmethod
    def _movie(name, first_seen, year="", **extra):
        movie = {
            "id": name.lower().replace(" ", "-"),
            "name": name,
            "url": f"https://x.example.net/{name.replace(' ', '')}.mp4",
            "year": year,
            "first_seen_at": first_seen,
            "verification_status": "verified_global",
            "verified": True,
            "source_pipeline": "movies",
        }
        movie.update(extra)
        return movie

    def test_a_newer_arrival_outranks_an_alphabetically_earlier_one(self):
        old = self._movie("A Very Old Film", "2026-06-01T00:00:00+00:00", 2010)
        new = self._movie("Zzz New Film", "2026-08-27T00:00:00+00:00", 2026)
        ordered = sorted([old, new], key=M._movie_sort_key)
        self.assertEqual(ordered[0]["name"], "Zzz New Film")

    def test_within_one_day_the_newest_year_still_leads(self):
        same_day = "2026-08-27T00:00:00+00:00"
        older = self._movie("Older Film", same_day, 2010)
        newer = self._movie("Newer Film", same_day, 2026)
        ordered = sorted([older, newer], key=M._movie_sort_key)
        self.assertEqual(ordered[0]["name"], "Newer Film")

    def test_a_missing_first_seen_does_not_crash_the_sort(self):
        movies = [
            self._movie("No Stamp", "", 2020),
            self._movie("Stamped", "2026-08-27T00:00:00+00:00", 2020),
        ]
        ordered = sorted(movies, key=M._movie_sort_key)
        self.assertEqual(ordered[0]["name"], "Stamped")

    def test_a_broken_first_seen_value_is_treated_as_unknown(self):
        self.assertEqual(M._first_seen_day({"first_seen_at": "not a date"}), 0)
        self.assertEqual(M._first_seen_day({}), 0)
        self.assertEqual(M._first_seen_day({"first_seen_at": None}), 0)

    def test_the_order_is_decided_before_the_pages_are_cut(self):
        """Sorting after pagination would shuffle within pages only.

        Built so the newest film is alphabetically last: if pagination ran
        first it would land on the final page, which is the bug being fixed.
        """
        movies = [
            self._movie(f"Film {chr(ord('A') + i)}", "2026-06-01T00:00:00+00:00", 2010)
            for i in range(12)
        ]
        movies.append(
            self._movie("Zzz Newest", "2026-08-27T00:00:00+00:00", 2026)
        )
        with tempfile.TemporaryDirectory() as tmp:
            original_seen = mr.DEFAULT_PATH
            original_ret = mrt.DEFAULT_PATH
            original_root = mrt.MOVIES_ROOT
            mr.DEFAULT_PATH = str(Path(tmp) / "seen.json")
            mrt.DEFAULT_PATH = str(Path(tmp) / "ret.json")
            mrt.MOVIES_ROOT = str(Path(tmp) / "movies")
            try:
                result = M.paginate_movie_list(movies, "Mix", page_size=5)
            finally:
                mr.DEFAULT_PATH = original_seen
                mrt.DEFAULT_PATH = original_ret
                mrt.MOVIES_ROOT = original_root
        first_page = result["page_contents"]["page-001.json"]["items"]
        self.assertEqual(first_page[0]["name"], "Zzz Newest")


class RetentionTests(unittest.TestCase):
    """One scan of grace, and no more."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "movies"
        (self.root / "mix").mkdir(parents=True)
        self.store = str(Path(self._tmp.name) / "ret.json")
        self.previous = [
            {
                "id": f"film-{i}",
                "name": f"Film {i}",
                "url": f"https://x.example.net/f{i}.mp4",
                "verification_status": "verified_global",
            }
            for i in range(10)
        ]
        (self.root / "mix" / "page-001.json").write_text(
            json.dumps({"items": self.previous}), encoding="utf-8"
        )
        self.addCleanup(self._tmp.cleanup)

    def _retain(self, incoming):
        return mrt.retain(
            incoming, "mix", root=str(self.root), path=self.store
        )

    def test_a_movie_missing_once_is_kept(self):
        kept, summary = self._retain([dict(m) for m in self.previous[:4]])
        self.assertEqual(summary["retained"], 6)
        self.assertEqual(len(kept), 10)

    def test_a_kept_movie_is_marked_stale_not_passed_off_as_verified(self):
        kept, _summary = self._retain([dict(m) for m in self.previous[:4]])
        carried = [m for m in kept if m.get("retained_after_failed_scan")]
        self.assertEqual(len(carried), 6)
        for movie in carried:
            self.assertEqual(movie["verification_status"], "stale_last_good")
            self.assertIn("transient", movie["retention_note"])

    def test_a_movie_missing_twice_is_dropped(self):
        incoming = [dict(m) for m in self.previous[:4]]
        self._retain(incoming)
        kept, summary = self._retain([dict(m) for m in self.previous[:4]])
        self.assertEqual(summary["retained"], 0)
        self.assertEqual(summary["dropped_after_grace"], 6)
        self.assertEqual(len(kept), 4)

    def test_a_movie_that_comes_back_has_its_counter_cleared(self):
        self._retain([dict(m) for m in self.previous[:4]])
        self._retain([dict(m) for m in self.previous])
        kept, summary = self._retain([dict(m) for m in self.previous[:4]])
        self.assertEqual(
            summary["retained"], 6,
            "a recovered movie must get its full grace again",
        )

    def test_an_empty_scan_result_is_left_entirely_alone(self):
        """A category that found nothing has failed, not emptied.

        Re-publishing everything as stale_last_good would present a failed scan
        as a result. The category-total guard in scanner/output.py owns this.
        """
        kept, summary = self._retain([])
        self.assertEqual(kept, [])
        self.assertEqual(summary["retained"], 0)
        self.assertIn("empty", summary["skipped"])

    def test_retention_only_adds_and_never_reorders_the_incoming_list(self):
        incoming = [dict(m) for m in self.previous[:4]]
        kept, _summary = self._retain(incoming)
        self.assertEqual(
            [m["id"] for m in kept[:4]], [m["id"] for m in incoming]
        )

    def test_an_unreadable_previous_page_is_not_fatal(self):
        (self.root / "mix" / "page-002.json").write_text(
            "{broken", encoding="utf-8"
        )
        kept, summary = self._retain([dict(m) for m in self.previous[:4]])
        self.assertEqual(summary["retained"], 6)


class RetentionIsOptInTests(unittest.TestCase):
    """Retention reads disk and adds items, so it cannot be always-on.

    With it switched on inside paginate_movie_list, a caller paginating four
    films got the seven already published in that category back as well - which
    broke an existing ordering test and would have broken any tool that
    paginates a subset. It belongs to the publish path only.
    """

    def test_paginating_a_subset_does_not_pull_in_the_published_set(self):
        movies = [{
            "id": "just-one-2026",
            "name": "Just One (2026)",
            "url": "https://x.example.net/one.mp4",
            "year": 2026,
            "verification_status": "verified_global",
            "verified": True,
            "publish_allowed": True,
            "source_pipeline": "movies",
        }]
        with tempfile.TemporaryDirectory() as tmp:
            original = mr.DEFAULT_PATH
            mr.DEFAULT_PATH = str(Path(tmp) / "seen.json")
            try:
                result = M.paginate_movie_list(movies, "Premium", page_size=50)
            finally:
                mr.DEFAULT_PATH = original
        items = result["page_contents"]["page-001.json"]["items"]
        self.assertEqual([m["name"] for m in items], ["Just One (2026)"])

    def test_the_publish_path_does_ask_for_retention(self):
        source = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
        self.assertIn("retain_recent_dropouts=True", source)


class DailyScanTests(unittest.TestCase):
    def test_the_movie_scan_runs_daily_and_the_dispatcher_agrees(self):
        """Both halves, because changing one alone silently disables movies.

        The workflow picks its mode by matching the schedule string, so a cron
        change without the matching dispatcher change means the movie mode is
        never selected at all.
        """
        workflow = (ROOT / ".github" / "workflows" / "scan.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('- cron: "37 4 * * *"', workflow)
        self.assertNotIn('"37 4 */2 * *"', workflow)
        self.assertIn(
            'github.event.schedule }}" == "37 4 * * *"', workflow
        )


if __name__ == "__main__":
    unittest.main()
