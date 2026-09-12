"""PART 07: real Just Added, on the production first-seen system.

"Just Added" answers one question - when did this film arrive on Click TV -
and the only honest source for it is our own observation, which is why this
row needs no external API and keeps working with every provider down.

The failure this PART exists to prevent is a film being announced as new
when it is not. There are three ways that happens, and each has a test:

  - the whole existing catalogue being treated as arriving on rollout day
    (15,000+ real historical timestamps already exist; nothing here rebuilds
    or re-keys them),
  - a film's date being reset because a new server or backup was added to it,
  - a film's date being reset because its generated id changed while the
    film did not - a rename, a manual entry replaced by a discovered one.

The opposite direction matters too: a 2010 film genuinely added today IS
Just Added, even though it is nowhere near Latest.
"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_discovery as md  # noqa: E402
from scanner import movie_identity_alias as mia  # noqa: E402
from scanner import movie_recency as mr  # noqa: E402
from scanner import movies as M  # noqa: E402

NOW = dt.datetime(2026, 9, 11, 12, tzinfo=dt.timezone.utc)


def _ago(days):
    return (NOW - dt.timedelta(days=days)).isoformat()


def _catalog(*movies):
    return {"Mix": {"index": {}, "page_contents": {"page-001.json": {"items": list(movies)}}}}


class JustAddedWindowTests(unittest.TestCase):
    def test_only_films_inside_the_rolling_window_are_listed(self):
        document = md.build_just_added(
            _catalog(
                {"id": "yesterday", "name": "Yesterday", "first_seen_at": _ago(1)},
                {"id": "thirteen", "name": "Thirteen Days", "first_seen_at": _ago(13)},
                {"id": "fifteen", "name": "Fifteen Days", "first_seen_at": _ago(15)},
                {"id": "ancient", "name": "Long Ago", "first_seen_at": _ago(300)},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["yesterday", "thirteen"])
        self.assertEqual(document["window_days"], 14)

    def test_the_default_window_is_fourteen_days(self):
        self.assertEqual(md.JUST_ADDED_WINDOW_DAYS, 14)

    def test_newest_arrival_leads(self):
        document = md.build_just_added(
            _catalog(
                {"id": "older", "first_seen_at": _ago(9)},
                {"id": "newest", "first_seen_at": _ago(1)},
                {"id": "middle", "first_seen_at": _ago(5)},
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["newest", "middle", "older"])

    def test_an_existing_catalogue_is_not_announced_as_newly_added(self):
        """The 15k historical timestamps already in production are old, and
        an old timestamp must simply not qualify."""
        legacy = [{"id": f"film-{n}", "first_seen_at": _ago(45)} for n in range(500)]
        document = md.build_just_added(_catalog(*legacy), now=NOW)
        self.assertEqual(document["count"], 0)
        self.assertEqual(document["items"], [])

    def test_a_film_with_no_first_seen_at_is_not_guessed_into_the_row(self):
        document = md.build_just_added(_catalog({"id": "unknown", "name": "No Date"}), now=NOW)
        self.assertEqual(document["items"], [])

    def test_a_future_timestamp_cannot_pin_itself_to_the_top(self):
        document = md.build_just_added(
            _catalog({"id": "clock-skew", "first_seen_at": (NOW + dt.timedelta(days=3)).isoformat()}),
            now=NOW,
        )
        self.assertEqual(document["items"], [])

    def test_an_old_release_added_today_is_just_added(self):
        """A 2010 film genuinely arriving today belongs here - and nowhere
        near Latest, which PART 08 keeps separate."""
        document = md.build_just_added(
            _catalog(
                {
                    "id": "old-film-2010",
                    "name": "Old Film",
                    "year": 2010,
                    "release_date": "2010-04-02",
                    "first_seen_at": _ago(0),
                }
            ),
            now=NOW,
        )
        self.assertEqual([item["id"] for item in document["items"]], ["old-film-2010"])
        self.assertEqual(document["items"][0]["year"], 2010)
        self.assertEqual(document["items"][0]["release_date"], "2010-04-02")

    def test_one_film_cannot_appear_twice(self):
        payload = {
            "Mix": {"page_contents": {"page-001.json": {"items": [{"id": "a", "first_seen_at": _ago(1)}]}}},
            "Bangla": {"page_contents": {"page-001.json": {"items": [{"id": "a", "first_seen_at": _ago(1)}]}}},
        }
        self.assertEqual(len(md.build_just_added(payload, now=NOW)["items"]), 1)

    def test_no_external_api_is_needed_for_just_added(self):
        with patch("scanner.provider_health.request_json") as request:
            md.build_just_added(_catalog({"id": "a", "first_seen_at": _ago(1)}), now=NOW)
            request.assert_not_called()


class JustAddedPayloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output = str(Path(self._tmp.name) / "discovery" / "just-added.json")

    def test_the_card_carries_metadata_and_never_playback_data(self):
        movie = {
            "id": "film-a",
            "name": "Some Film",
            "category": "Bangla",
            "year": 2026,
            "release_date": "2026-01-02",
            "logo": "https://x.test/poster.jpg",
            "backdrop": "https://x.test/back.jpg",
            "rating": 7.5,
            "rating_source": "IMDb",
            "genres": ["Action"],
            "first_seen_at": _ago(1),
            # None of the following may survive into a discovery file.
            "url": "https://secret.test/stream.mkv",
            "backups": [{"url": "https://secret.test/backup.mkv"}],
            "headers": {"Referer": "https://secret.test/"},
            "playback_id": "ctv_deadbeef",
            "proxy_mode": "direct_first",
        }
        document = md.build_just_added(_catalog(movie), now=NOW)
        card = document["items"][0]

        self.assertEqual(card["poster"], "https://x.test/poster.jpg")
        self.assertEqual(card["rating_source"], "IMDb")
        self.assertEqual(card["genres"], ["Action"])
        self.assertEqual(card["first_seen_at"], movie["first_seen_at"])

        serialized = json.dumps(document)
        for forbidden in md.FORBIDDEN_CARD_FIELDS:
            self.assertNotIn(f'"{forbidden}"', serialized, f"{forbidden} leaked into discovery")
        self.assertNotIn("secret.test", serialized)

    def test_absent_metadata_is_omitted_rather_than_serialised_as_null(self):
        document = md.build_just_added(
            _catalog({"id": "film-a", "name": "Bare", "first_seen_at": _ago(1)}), now=NOW
        )
        card = document["items"][0]
        self.assertNotIn("rating", card)
        self.assertNotIn("genres", card)
        self.assertEqual(card["id"], "film-a")

    def test_the_row_is_capped_so_it_stays_a_shelf_not_a_catalogue(self):
        many = [{"id": f"film-{n}", "first_seen_at": _ago(1)} for n in range(200)]
        document = md.build_just_added(_catalog(*many), now=NOW)
        self.assertEqual(len(document["items"]), md.JUST_ADDED_LIMIT)

    def test_generate_writes_atomically_with_no_temp_files_left(self):
        summary = md.generate_just_added(
            _catalog({"id": "film-a", "first_seen_at": _ago(1)}), output_path=self.output, now=NOW
        )
        self.assertEqual(summary["count"], 1)
        on_disk = json.loads(Path(self.output).read_text(encoding="utf-8"))
        self.assertEqual(on_disk["signal"], "first_seen_at")
        self.assertEqual(list(Path(self.output).parent.glob(".*.tmp")), [])


class FirstSeenStabilityTests(unittest.TestCase):
    """The production first-seen system, exercised through movies.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.seen_path = str(Path(self._tmp.name) / "seen.json")

    def test_adding_a_backup_does_not_reset_the_arrival_date(self):
        june = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        movie = {"id": "some-film-2024", "name": "Some Film (2024)", "backups": []}
        mr.stamp_first_seen([movie], path=self.seen_path, now=june)
        original = movie["first_seen_at"]

        later = {
            "id": "some-film-2024",
            "name": "Some Film (2024)",
            "backups": [{"url": "https://x.test/b1.mkv"}, {"url": "https://x.test/b2.mkv"}],
            "available_link_count": 3,
        }
        mr.stamp_first_seen([later], path=self.seen_path, now=NOW)
        self.assertEqual(later["first_seen_at"], original)

    def test_a_film_that_disappears_and_returns_keeps_its_original_date(self):
        june = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
        movie = {"id": "some-film-2024", "name": "Some Film"}
        mr.stamp_first_seen([movie], path=self.seen_path, now=june)

        # Two scans where it is absent entirely, then it returns.
        mr.stamp_first_seen([], path=self.seen_path, now=june + dt.timedelta(days=1))
        mr.stamp_first_seen([], path=self.seen_path, now=june + dt.timedelta(days=2))
        returned = {"id": "some-film-2024", "name": "Some Film"}
        mr.stamp_first_seen([returned], path=self.seen_path, now=NOW)
        self.assertTrue(returned["first_seen_at"].startswith("2026-06-01"))


class IdentityReconciliationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.alias_path = str(Path(self._tmp.name) / "movie-identity-aliases.json")

    def test_a_renamed_film_keeps_the_date_it_actually_arrived(self):
        """The id changed; the film did not. Its arrival date must not."""
        original = {"id": "some-film-2024", "tmdb_id": 555, "name": "Some Film", "first_seen_at": _ago(90)}
        mia.reconcile([original], path=self.alias_path, now=NOW)

        renamed = {"id": "some-film-2024-web-dl", "tmdb_id": 555, "name": "Some Film", "first_seen_at": _ago(0)}
        summary = mia.reconcile([renamed], path=self.alias_path, now=NOW)

        self.assertEqual(summary["corrected"], 1)
        self.assertEqual(renamed["first_seen_at"], original["first_seen_at"])

    def test_such_a_film_is_therefore_kept_out_of_just_added(self):
        original = {"id": "old-id", "tmdb_id": 555, "first_seen_at": _ago(90)}
        mia.reconcile([original], path=self.alias_path, now=NOW)
        renamed = {"id": "new-id", "tmdb_id": 555, "first_seen_at": _ago(0)}
        mia.reconcile([renamed], path=self.alias_path, now=NOW)
        self.assertEqual(md.build_just_added(_catalog(renamed), now=NOW)["items"], [])

    def test_a_genuinely_new_film_is_not_dragged_backwards(self):
        summary = mia.reconcile(
            [{"id": "brand-new", "tmdb_id": 999, "first_seen_at": _ago(0)}],
            path=self.alias_path,
            now=NOW,
        )
        self.assertEqual(summary["corrected"], 0)

    def test_a_date_is_only_ever_moved_backwards_never_forwards(self):
        mia.reconcile([{"id": "a", "tmdb_id": 1, "first_seen_at": _ago(0)}], path=self.alias_path, now=NOW)
        older = {"id": "a", "tmdb_id": 1, "first_seen_at": _ago(100)}
        mia.reconcile([older], path=self.alias_path, now=NOW)
        self.assertEqual(older["first_seen_at"], _ago(100), "the earlier date wins, not the recorded one")

    def test_a_title_without_a_year_is_too_weak_an_identity_to_inherit_on(self):
        """Two unrelated films share a title far more often than one film
        changes its id."""
        self.assertEqual(mia.alias_identity({"name": "Some Film"}), "")
        self.assertTrue(mia.alias_identity({"name": "Some Film", "year": 2024}).startswith("title:"))
        self.assertTrue(mia.alias_identity({"tmdb_id": 5}).startswith("tmdb_id:"))

    def test_the_production_first_seen_ledger_is_left_byte_for_byte_untouched(self):
        """The 15,000+ historical timestamps are the source of truth. This
        asserts the behaviour, not the wording: a real first-seen ledger is
        put where the module would find it, reconciliation runs, and the
        file must come back identical."""
        ledger_path = Path(self._tmp.name) / "movie-first-seen.json"
        ledger = {
            "version": 1,
            "seen": {
                "some-film-2024": {"first_seen_at": _ago(200), "last_seen_at": _ago(1)},
                "another-film-2019": {"first_seen_at": _ago(365)},
            },
        }
        ledger_path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        before = ledger_path.read_bytes()

        original_default = mr.DEFAULT_PATH
        mr.DEFAULT_PATH = str(ledger_path)
        try:
            mia.reconcile(
                [
                    {"id": "renamed-id", "tmdb_id": 555, "first_seen_at": _ago(0)},
                    {"id": "some-film-2024", "tmdb_id": 1, "first_seen_at": _ago(200)},
                ],
                path=self.alias_path,
                now=NOW,
            )
        finally:
            mr.DEFAULT_PATH = original_default

        self.assertEqual(ledger_path.read_bytes(), before, "history must not be rewritten")

    def test_this_module_only_ever_owns_its_own_state_file(self):
        self.assertTrue(mia.DEFAULT_PATH.endswith("movie-identity-aliases.json"))

    def test_a_corrupt_alias_ledger_does_not_crash_a_scan(self):
        Path(self.alias_path).write_text("{not json", encoding="utf-8")
        summary = mia.reconcile(
            [{"id": "a", "tmdb_id": 1, "first_seen_at": _ago(1)}], path=self.alias_path, now=NOW
        )
        self.assertEqual(summary["corrected"], 0)

    def test_stale_identities_are_pruned_so_the_ledger_stays_bounded(self):
        store = {
            "version": 1,
            "identities": {
                "tmdb_id:1": {"first_seen_at": _ago(400), "last_seen_at": _ago(400)},
                "tmdb_id:2": {"first_seen_at": _ago(10), "last_seen_at": _ago(10)},
            },
        }
        mia.save(store, self.alias_path)
        mia.reconcile([], path=self.alias_path, now=NOW)
        remaining = mia.load(self.alias_path)["identities"]
        self.assertNotIn("tmdb_id:1", remaining)
        self.assertIn("tmdb_id:2", remaining)


class WiringTests(unittest.TestCase):
    """The reconciliation runs inside the scan, and cannot break it."""

    def test_reconciliation_recomputes_is_new_from_the_corrected_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            alias_path = str(Path(tmp) / "aliases.json")
            original_default = mia.DEFAULT_PATH
            mia.DEFAULT_PATH = alias_path
            try:
                old = {"id": "old-id", "tmdb_id": 7, "first_seen_at": _ago(200)}
                mia.reconcile([old], path=alias_path)

                renamed = {"id": "new-id", "tmdb_id": 7, "first_seen_at": dt.datetime.now(dt.timezone.utc).isoformat()}
                renamed["is_new"] = True
                M._reconcile_first_seen([renamed])
                self.assertFalse(renamed["is_new"], "corrected date must drive the badge too")
            finally:
                mia.DEFAULT_PATH = original_default

    def test_a_reconciliation_failure_never_takes_a_scan_down(self):
        with patch("scanner.movie_identity_alias.reconcile", side_effect=RuntimeError("boom")):
            self.assertEqual(M._reconcile_first_seen([{"id": "a"}]), {})

    def test_one_discovery_row_failing_never_takes_a_scan_or_the_other_rows_down(self):
        """Every generator is patched, so this test can never write into the
        repository's real data/movies/discovery/ directory."""
        with patch(
            "scanner.movie_discovery.generate_just_added", side_effect=RuntimeError("boom")
        ), patch(
            "scanner.movie_discovery.generate_latest", return_value={"count": 0, "eligible": 0, "excluded": {}}
        ), patch(
            "scanner.movie_discovery.generate_search_index", return_value={"count": 0}
        ), patch(
            "scanner.movie_discovery.generate_home", return_value={"counts": {}, "featured_status": "x"}
        ), patch(
            "scanner.movie_featured.generate",
            return_value={"written": True, "preserved": False, "count": 0, "reason": "",
                          "document": {"count": 0, "slots": 5, "manual_count": 0, "auto_count": 0}},
        ):
            summary = M._generate_discovery(_catalog({"id": "a"}))

        self.assertNotIn("just_added", summary, "the failing row is simply absent")
        self.assertIn("latest", summary, "the other rows still ran")
        self.assertIn("home", summary)
        self.assertIn("featured", summary)

    def test_every_discovery_row_failing_still_leaves_the_scan_standing(self):
        with patch(
            "scanner.movie_discovery.generate_just_added", side_effect=RuntimeError("boom")
        ), patch(
            "scanner.movie_discovery.generate_latest", side_effect=RuntimeError("boom")
        ), patch(
            "scanner.movie_discovery.generate_search_index", side_effect=RuntimeError("boom")
        ), patch(
            "scanner.movie_discovery.generate_home", side_effect=RuntimeError("boom")
        ), patch(
            # Featured is a discovery row like the others: it may not take a
            # scan down, and it may not write into the repository's real
            # data/movies/discovery/ while a test is running either.
            "scanner.movie_featured.generate", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(M._generate_discovery(_catalog({"id": "a"})), {})


if __name__ == "__main__":
    unittest.main()
