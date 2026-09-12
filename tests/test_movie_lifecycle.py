"""A movie that goes missing for a day is not a movie that has been withdrawn.

Two failures this guards against, and they pull in opposite directions.

A source hiccups, a CDN times out, a playlist is briefly malformed - and the
film vanishes from the site. That is the one the retention grace was written
for, after 383 of 817 films disappeared between two scans.

The other is the opposite: a film really is gone, and the catalogue keeps
offering it for ever because nothing ever decides it is over.

Between them sits the case that is easy to get wrong - a scan that half-ran.
Its silence is not evidence. Nothing it failed to reach may be counted absent,
because a broken run reports a short list, not an empty one, and the existing
empty-list check does not catch that.
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scanner import movie_retention as mrt


def at(day):
    return dt.datetime(2026, 9, day, 4, 37, tzinfo=dt.timezone.utc)


class ScanCompleteness(unittest.TestCase):
    def test_a_full_scan_is_complete(self):
        self.assertTrue(mrt.scan_looks_complete(800, 800))

    def test_a_scan_that_found_nothing_is_not_evidence(self):
        self.assertFalse(mrt.scan_looks_complete(0, 800))

    def test_a_scan_that_died_early_is_not_evidence(self):
        self.assertFalse(mrt.scan_looks_complete(12, 800))

    def test_a_real_shrink_is_still_believed(self):
        # Films do get withdrawn. Half the catalogue going is believed; a
        # twentieth of it remaining is not.
        self.assertTrue(mrt.scan_looks_complete(420, 800))

    def test_a_brand_new_category_is_complete(self):
        self.assertTrue(mrt.scan_looks_complete(5, 0))


class TheLifecycleMovesOneScanAtATime(unittest.TestCase):
    def setUp(self):
        self.store = {"version": 1, "absent": {}, "lifecycle": {}}

    def advance(self, present, previous, *, complete=True, day=1):
        return mrt.update_lifecycle(
            set(present), set(previous),
            store=self.store, stamp=at(day).isoformat(),
            scan_complete=complete, now=at(day),
        )

    def record(self, key):
        return self.store["lifecycle"].get(key, {})

    def test_a_film_that_is_there_is_active(self):
        self.advance(["a"], ["a"], day=1)
        self.assertTrue(self.record("a")["is_active"])
        self.assertEqual(self.record("a")["consecutive_missing_scans"], 0)
        self.assertEqual(self.record("a")["last_seen_at"], at(1).isoformat())

    def test_one_missing_scan_does_not_deactivate(self):
        self.advance(["a"], ["a"], day=1)
        self.advance([], ["a"], day=2)
        self.assertNotEqual(self.record("a").get("is_active"), False)
        self.assertEqual(self.record("a")["consecutive_missing_scans"], 1)

    def test_two_missing_scans_do_not_deactivate(self):
        self.advance(["a"], ["a"], day=1)
        self.advance([], ["a"], day=2)
        self.advance([], ["a"], day=3)
        self.assertNotEqual(self.record("a").get("is_active"), False)
        self.assertEqual(self.record("a")["consecutive_missing_scans"], 2)

    def test_the_third_missing_scan_deactivates(self):
        self.advance(["a"], ["a"], day=1)
        for day in (2, 3, 4):
            self.advance([], ["a"], day=day)
        self.assertIs(self.record("a")["is_active"], False)
        self.assertEqual(self.record("a")["inactive_since"], at(4).isoformat())
        self.assertEqual(
            self.record("a")["consecutive_missing_scans"],
            mrt.INACTIVE_AFTER_MISSING_SCANS,
        )

    def test_a_gap_resets_the_count(self):
        self.advance(["a"], ["a"], day=1)
        self.advance([], ["a"], day=2)
        self.advance(["a"], ["a"], day=3)
        self.advance([], ["a"], day=4)
        self.assertEqual(self.record("a")["consecutive_missing_scans"], 1)
        self.assertNotEqual(self.record("a").get("is_active"), False)


class AnIncompleteScanProvesNothing(unittest.TestCase):
    def setUp(self):
        self.store = {"version": 1, "absent": {}, "lifecycle": {}}

    def advance(self, present, previous, *, complete, day):
        return mrt.update_lifecycle(
            set(present), set(previous),
            store=self.store, stamp=at(day).isoformat(),
            scan_complete=complete, now=at(day),
        )

    def test_nothing_is_counted_absent(self):
        self.advance(["a", "b"], ["a", "b"], complete=True, day=1)
        summary = self.advance(["a"], ["a", "b"], complete=False, day=2)
        self.assertEqual(summary["missing"], 0)
        self.assertEqual(summary["absence_ignored_incomplete_scan"], 1)
        self.assertEqual(
            self.store["lifecycle"]["b"]["consecutive_missing_scans"], 0)

    def test_three_broken_scans_still_deactivate_nothing(self):
        self.advance(["a", "b"], ["a", "b"], complete=True, day=1)
        for day in (2, 3, 4, 5):
            self.advance(["a"], ["a", "b"], complete=False, day=day)
        self.assertNotEqual(
            self.store["lifecycle"]["b"].get("is_active"), False)

    def test_what_it_did_see_is_still_recorded_as_seen(self):
        self.advance(["a"], ["a", "b"], complete=False, day=2)
        self.assertTrue(self.store["lifecycle"]["a"]["is_active"])


class ComingBack(unittest.TestCase):
    def setUp(self):
        self.store = {"version": 1, "absent": {}, "lifecycle": {}}

    def advance(self, present, previous, *, day):
        return mrt.update_lifecycle(
            set(present), set(previous),
            store=self.store, stamp=at(day).isoformat(),
            scan_complete=True, now=at(day),
        )

    def deactivate(self):
        self.advance(["a"], ["a"], day=1)
        for day in (2, 3, 4):
            self.advance([], ["a"], day=day)

    def test_the_same_identity_is_reactivated(self):
        self.deactivate()
        summary = self.advance(["a"], ["a"], day=20)
        self.assertTrue(self.store["lifecycle"]["a"]["is_active"])
        self.assertIsNone(self.store["lifecycle"]["a"]["inactive_since"])
        self.assertEqual(summary["reactivated"], 1)

    def test_the_missing_count_is_cleared(self):
        self.deactivate()
        self.advance(["a"], ["a"], day=20)
        self.assertEqual(
            self.store["lifecycle"]["a"]["consecutive_missing_scans"], 0)

    def test_the_return_is_recorded_without_touching_first_seen(self):
        # first_seen_at belongs to scanner/movie_recency and is never written
        # here - a film that comes back is not a new arrival.
        self.deactivate()
        self.advance(["a"], ["a"], day=20)
        record = self.store["lifecycle"]["a"]
        self.assertIn("reactivated_at", record)
        self.assertNotIn("first_seen_at", record)

    def test_a_film_that_never_left_is_not_reported_as_returning(self):
        self.advance(["a"], ["a"], day=1)
        summary = self.advance(["a"], ["a"], day=2)
        self.assertEqual(summary["reactivated"], 0)


class GarbageCollection(unittest.TestCase):
    def setUp(self):
        self.store = {"version": 1, "absent": {}, "lifecycle": {}}

    def test_a_recently_inactive_record_is_kept(self):
        self.store["lifecycle"]["a"] = {
            "is_active": False, "inactive_since": at(1).isoformat()}
        mrt.update_lifecycle(set(), set(), store=self.store,
                             stamp=at(20).isoformat(), scan_complete=True,
                             now=at(20))
        self.assertIn("a", self.store["lifecycle"])

    def test_a_long_inactive_record_is_collected(self):
        long_ago = at(1) - dt.timedelta(days=mrt.GC_AFTER_INACTIVE_DAYS + 5)
        self.store["lifecycle"]["a"] = {
            "is_active": False, "inactive_since": long_ago.isoformat()}
        summary = mrt.update_lifecycle(set(), set(), store=self.store,
                                       stamp=at(1).isoformat(),
                                       scan_complete=True, now=at(1))
        self.assertNotIn("a", self.store["lifecycle"])
        self.assertEqual(summary["collected"], 1)

    def test_an_active_record_is_never_collected(self):
        self.store["lifecycle"]["a"] = {
            "is_active": True, "last_seen_at": at(1).isoformat()}
        mrt.update_lifecycle(set(), set(), store=self.store,
                             stamp=at(1).isoformat(), scan_complete=True,
                             now=at(1))
        self.assertIn("a", self.store["lifecycle"])


class ThroughTheRealRetainCall(unittest.TestCase):
    """The lifecycle rides the scan that is already happening."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "movies"
        (self.root / "mix").mkdir(parents=True)
        self.store = str(Path(self._tmp.name) / "ret.json")
        self.previous = [
            {"id": f"film-{i}", "name": f"Film {i}",
             "url": f"https://x.example.net/f{i}.mp4",
             "verification_status": "verified_global"}
            for i in range(10)
        ]
        (self.root / "mix" / "page-001.json").write_text(
            json.dumps({"items": self.previous}), encoding="utf-8")

    def retain(self, incoming):
        return mrt.retain(incoming, "mix", root=str(self.root), path=self.store)

    def read(self):
        with open(self.store, encoding="utf-8") as handle:
            return json.load(handle)

    def test_everything_found_is_recorded_active(self):
        self.retain([dict(m) for m in self.previous])
        lifecycle = self.read()["lifecycle"]
        self.assertEqual(len(lifecycle), 10)
        self.assertTrue(all(row["is_active"] for row in lifecycle.values()))
        self.assertTrue(all(row["last_seen_at"] for row in lifecycle.values()))

    def test_a_complete_scan_counts_what_is_missing(self):
        _kept, summary = self.retain([dict(m) for m in self.previous[:8]])
        self.assertTrue(summary["scan_complete"])
        self.assertEqual(summary["lifecycle"]["missing"], 2)

    def test_a_half_failed_scan_counts_nothing_missing(self):
        _kept, summary = self.retain([dict(m) for m in self.previous[:2]])
        self.assertFalse(summary["scan_complete"])
        self.assertEqual(summary["lifecycle"]["missing"], 0)
        self.assertEqual(summary["lifecycle"]["absence_ignored_incomplete_scan"], 8)
        self.assertIn("lifecycle_note", summary)

    def test_the_publish_grace_is_unaffected_by_the_lifecycle(self):
        # The grace owns whether a card is still shown, and a short scan must
        # not make it drop things either.
        kept, summary = self.retain([dict(m) for m in self.previous[:2]])
        self.assertEqual(summary["retained"], 8)
        self.assertEqual(len(kept), 10)

    def test_is_active_answers_for_an_unknown_title(self):
        # Never recorded is not the same as withdrawn.
        self.assertTrue(mrt.is_active("never-heard-of-it", self.store))

    def test_is_active_answers_for_a_withdrawn_title(self):
        for _ in range(mrt.INACTIVE_AFTER_MISSING_SCANS + 1):
            self.retain([dict(m) for m in self.previous[:8]])
        lifecycle = self.read()["lifecycle"]
        withdrawn = [key for key, row in lifecycle.items() if row.get("is_active") is False]
        self.assertTrue(withdrawn, "a title missing past the threshold should be inactive")
        self.assertFalse(mrt.is_active(withdrawn[0], self.store))

    def test_a_new_backup_url_is_not_a_new_film(self):
        # Same identity, different server: the lifecycle record is the same one
        # and nothing about it resets.
        self.retain([dict(m) for m in self.previous])
        moved = [dict(m) for m in self.previous]
        for movie in moved:
            movie["url"] = movie["url"].replace("x.example.net", "y.example.net")
        self.retain(moved)
        lifecycle = self.read()["lifecycle"]
        self.assertEqual(len(lifecycle), 10)
        self.assertTrue(all(row["is_active"] for row in lifecycle.values()))
        self.assertTrue(all(row["consecutive_missing_scans"] == 0
                            for row in lifecycle.values()))


if __name__ == "__main__":
    unittest.main()


class AWithdrawnTitleIsNotOffered(unittest.TestCase):
    """Inactive means gone from every discovery surface, not just from browse.

    The publish grace and the lifecycle threshold are currently the same
    number, so in practice a withdrawn title has already stopped being
    published. This is the guard for when they disagree - a stale page file, a
    hand-edited catalogue, a future change to either constant - because
    discovery reads what is on disk.
    """

    def paginated(self):
        def movie(name, active=True):
            row = {
                "id": name, "name": name.replace("-", " ").title(),
                "url": f"https://x.example.net/{name}.mp4",
                "category": "Mix", "year": 2026,
                "genres": ["Action"], "release_date": "2026-05-02",
                "first_seen_at": at(1).isoformat(),
            }
            if not active:
                row["is_active"] = False
            return row

        return {
            "Mix": {
                "page_contents": {
                    "page-001.json": {
                        "items": [movie("still-here"), movie("withdrawn", active=False)]
                    }
                }
            }
        }

    def names(self, rows):
        return {row.get("id") for row in rows}

    def test_it_is_gone_from_the_published_iterator(self):
        from scanner import movie_discovery

        seen = self.names(movie_discovery.iter_published_movies(self.paginated()))
        self.assertIn("still-here", seen)
        self.assertNotIn("withdrawn", seen)

    def test_it_is_gone_from_just_added(self):
        from scanner import movie_discovery

        document = movie_discovery.build_just_added(self.paginated(), now=at(2))
        self.assertNotIn("withdrawn", self.names(document["items"]))
        self.assertIn("still-here", self.names(document["items"]))

    def test_it_is_gone_from_latest(self):
        from scanner import movie_discovery

        document = movie_discovery.build_latest(self.paginated(), now=at(2))
        self.assertNotIn("withdrawn", self.names(document["items"]))

    def test_it_is_gone_from_the_browse_and_search_index(self):
        from scanner import movie_discovery

        with tempfile.TemporaryDirectory() as empty:
            document = movie_discovery.build_search_index(
                self.paginated(), now=at(2), series_root=empty)
        self.assertNotIn("withdrawn", self.names(document["items"]))
        self.assertIn("still-here", self.names(document["items"]))

    def test_it_is_gone_from_the_genre_indexes(self):
        from scanner import movie_genre_index

        seen = self.names(movie_genre_index.iter_published_movies(self.paginated()))
        self.assertNotIn("withdrawn", seen)

    def test_it_is_gone_from_trending_matching(self):
        from scanner import movie_trending

        seen = self.names(movie_trending.iter_published_movies(self.paginated()))
        self.assertNotIn("withdrawn", seen)

    def test_a_title_with_no_lifecycle_flag_is_still_offered(self):
        from scanner import movie_discovery

        self.assertFalse(movie_discovery.is_withdrawn({"id": "unknown"}))
        self.assertFalse(movie_discovery.is_withdrawn({"id": "x", "is_active": True}))
        self.assertTrue(movie_discovery.is_withdrawn({"id": "x", "is_active": False}))
