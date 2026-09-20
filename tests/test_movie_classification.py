"""S-05 / ধারা ৪.২ · ৪.৬ - the classification cache, and the flags it feeds.

Three things are being protected here.

**Provider-agnosticism** (v৩.৪ correction 4). An earlier draft keyed this store
on `tmdb_tv_id`, which contradicts a plan that routes every provider by
capability, quota and health. The record holds an `external_ids` map and names
its `identity_source`; `tmdb_tv_id` survives only as a compatibility field.

**Version invalidation** (ধারা ৪.৭). ধাপ ৩ changes the classifier. A record
written by the old one is stale whatever its age, or the whole benefit of
changing the rules sits behind a TTL. The real risk is not a "not found"
getting stuck - this codebase retries those - it is a wrong match from a dirty
title cached as applied for 90 days.

**"Adds only"** (ধাপ ৩খ, first half). The annotation marks what is known and
what is pending. It moves nothing, hides nothing and drops nothing, and above
all it never writes an episode number the title did not state.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import movie_classification as mc  # noqa: E402
from scanner import series_signal as ss  # noqa: E402


def _now() -> datetime:
    return datetime(2026, 9, 20, tzinfo=timezone.utc)


class RecordShapeTests(unittest.TestCase):
    """ধারা ৪.২ names the fields this store carries."""

    def setUp(self) -> None:
        self.store = mc.load(Path(tempfile.mkdtemp()) / "missing.json")
        self.record = mc.remember(
            self.store,
            show_key="show:bachelor-point",
            canonical_show_title="Bachelor Point",
            identity_source=mc.SOURCE_TITLE_PATTERN,
            now=_now(),
        )

    def test_every_field_the_plan_names_is_present(self) -> None:
        for field in ("normalized_show_key", "canonical_show_title",
                      "external_ids", "identity_source",
                      "disambiguation_context", "tmdb_tv_id", "type",
                      "confidence", "classifier_version", "verified_at"):
            self.assertIn(field, self.record)

    def test_external_ids_is_a_map_not_a_single_provider(self) -> None:
        mc.remember(
            self.store, show_key="show:bachelor-point",
            identity_source=mc.SOURCE_PROVIDER,
            external_ids={"tvmaze": "123", "imdb": "tt99", "tmdb": "456"},
            now=_now(),
        )
        record = mc.get(self.store, "show:bachelor-point", now=_now())
        self.assertEqual(
            record["external_ids"],
            {"tvmaze": "123", "imdb": "tt99", "tmdb": "456"},
        )

    def test_tmdb_tv_id_is_only_a_compatibility_mirror(self) -> None:
        """A record identified by TVmaze alone must still be a valid record."""
        mc.remember(
            self.store, show_key="show:only-tvmaze",
            identity_source=mc.SOURCE_PROVIDER,
            external_ids={"tvmaze": "777"}, now=_now(),
        )
        record = mc.get(self.store, "show:only-tvmaze", now=_now())
        self.assertEqual(record["external_ids"], {"tvmaze": "777"})
        self.assertEqual(record["tmdb_tv_id"], "")
        self.assertEqual(record["identity_source"], mc.SOURCE_PROVIDER)

    def test_a_missing_store_is_empty_not_an_error(self) -> None:
        store = mc.load(Path(tempfile.mkdtemp()) / "nope.json")
        self.assertEqual(store["shows"], {})

    def test_a_damaged_store_is_empty_not_a_scan_failure(self) -> None:
        path = Path(tempfile.mkdtemp()) / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(mc.load(path)["shows"], {})


class FillOnlyTests(unittest.TestCase):
    """A later, weaker answer must not undo an earlier, better one."""

    def setUp(self) -> None:
        self.store = {"shows": {}}

    def test_confidence_only_rises(self) -> None:
        mc.remember(self.store, show_key="show:x",
                    identity_source=mc.SOURCE_PROVIDER, now=_now())
        high = mc.get(self.store, "show:x", now=_now())["confidence"]
        mc.remember(self.store, show_key="show:x",
                    identity_source=mc.SOURCE_PACK_SIGNAL, now=_now())
        after = mc.get(self.store, "show:x", now=_now())
        self.assertEqual(after["confidence"], high)
        self.assertEqual(after["identity_source"], mc.SOURCE_PROVIDER)

    def test_a_known_title_is_not_replaced_by_an_empty_one(self) -> None:
        mc.remember(self.store, show_key="show:x",
                    canonical_show_title="Bachelor Point", now=_now())
        mc.remember(self.store, show_key="show:x",
                    canonical_show_title="", now=_now())
        self.assertEqual(
            mc.get(self.store, "show:x", now=_now())["canonical_show_title"],
            "Bachelor Point",
        )

    def test_external_ids_accumulate_across_providers(self) -> None:
        mc.remember(self.store, show_key="show:x",
                    external_ids={"tvmaze": "1"}, now=_now())
        mc.remember(self.store, show_key="show:x",
                    external_ids={"imdb": "tt2"}, now=_now())
        self.assertEqual(
            mc.get(self.store, "show:x", now=_now())["external_ids"],
            {"tvmaze": "1", "imdb": "tt2"},
        )

    def test_a_show_key_is_required(self) -> None:
        with self.assertRaises(ValueError):
            mc.remember(self.store, show_key="  ")


class StalenessTests(unittest.TestCase):
    """ধারা ৪.৭ - a cached answer is only as good as the rules behind it."""

    def setUp(self) -> None:
        self.store = {"shows": {}}
        mc.remember(self.store, show_key="show:x", now=_now())

    def test_a_record_from_an_older_classifier_is_stale_whatever_its_age(self) -> None:
        self.store["shows"]["show:x"]["classifier_version"] = 0
        self.assertTrue(mc.is_stale(self.store["shows"]["show:x"], _now()))
        self.assertIsNone(mc.get(self.store, "show:x", now=_now()))

    def test_a_current_record_is_returned(self) -> None:
        self.assertIsNotNone(mc.get(self.store, "show:x", now=_now()))

    def test_a_record_past_its_ttl_is_stale(self) -> None:
        later = _now() + timedelta(days=mc.REFRESH_TTL_DAYS + 1)
        self.assertTrue(mc.is_stale(self.store["shows"]["show:x"], later))

    def test_a_record_with_no_timestamp_is_stale(self) -> None:
        self.store["shows"]["show:x"]["verified_at"] = ""
        self.assertTrue(mc.is_stale(self.store["shows"]["show:x"], _now()))


class ZeroCostEvidenceTests(unittest.TestCase):
    """ধারা ৪.৬ - the catalogue proves most shows without any provider."""

    def _signals(self, *titles):
        return ss.classify_rows([ss.detect(title) for title in titles])

    def test_an_explicit_episode_records_a_show(self) -> None:
        store = {"shows": {}}
        summary = mc.remember_from_signals(
            store, self._signals("Bachelor Point S04E12"), now=_now())
        self.assertEqual(summary["recorded"], 1)
        record = mc.get(store, "show:bachelor-point", now=_now())
        self.assertEqual(record["identity_source"], mc.SOURCE_TITLE_PATTERN)
        self.assertEqual(record["type"], mc.TYPE_SERIES)

    def test_a_sibling_proves_a_show_and_is_recorded_as_such(self) -> None:
        store = {"shows": {}}
        mc.remember_from_signals(
            store,
            self._signals("Peaky Blinders S01 English",
                          "Peaky Blinders S01E05 English"),
            now=_now(),
        )
        record = mc.get(store, "show:peaky-blinders", now=_now())
        self.assertIn(
            record["identity_source"],
            {mc.SOURCE_TITLE_PATTERN, mc.SOURCE_CATALOGUE_SIBLING},
        )

    def test_an_unproven_row_records_nothing_at_all(self) -> None:
        """A record here would be exactly the invented certainty ধারা ৪.৬
        forbids. It stays unresolved instead."""
        store = {"shows": {}}
        summary = mc.remember_from_signals(
            store, self._signals("Lonely Show S01 Dual Audio"), now=_now())
        self.assertEqual(summary["recorded"], 0)
        self.assertEqual(summary["skipped_unproven"], 1)
        self.assertEqual(store["shows"], {})

    def test_a_marker_with_no_show_name_records_nothing(self) -> None:
        store = {"shows": {}}
        summary = mc.remember_from_signals(
            store, self._signals("S01E01"), now=_now())
        self.assertEqual(summary["skipped_no_key"], 1)

    def test_unresolved_shows_are_deduplicated(self) -> None:
        """ধারা ৪.৬ plans the provider quota on the deduplicated number, not on
        the row count."""
        store = {"shows": {}}
        signals = self._signals(
            "Lonely Show S01 Dual", "Lonely Show S02 Dual",
            "Other Show S01 Hindi",
        )
        self.assertEqual(
            mc.unresolved_show_keys(store, signals),
            ["show:lonely-show", "show:other-show"],
        )

    def test_a_show_already_in_the_store_is_not_asked_about_again(self) -> None:
        store = {"shows": {}}
        mc.remember(store, show_key="show:lonely-show", now=_now())
        signals = self._signals("Lonely Show S01 Dual")
        self.assertEqual(mc.unresolved_show_keys(store, signals), [])

    def test_the_store_round_trips_through_disk(self) -> None:
        path = Path(tempfile.mkdtemp()) / "cache.json"
        store = {"shows": {}}
        mc.remember(store, show_key="show:x", canonical_show_title="X",
                    now=_now())
        self.assertTrue(mc.save(store, path))
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["classifier_version"], ss.CLASSIFIER_VERSION)
        self.assertIsNotNone(mc.get(mc.load(path), "show:x", now=_now()))


class AnnotationIsAdditiveTests(unittest.TestCase):
    """ধাপ ৩খ first half - mark it, do not move it."""

    def setUp(self) -> None:
        from scanner import movies

        self.movies = movies
        self.directory = Path(tempfile.mkdtemp(prefix="cls-annotate-"))
        self._original = mc.DEFAULT_PATH
        mc.DEFAULT_PATH = self.directory / "movie-classification-cache.json"
        self.addCleanup(setattr, mc, "DEFAULT_PATH", self._original)

    def _cards(self):
        return [
            {"id": "d2", "name": "Drishyam 2",
             "url": "https://cdn.test/d2.mkv", "category": "Hindi"},
            {"id": "bp", "name": "Bachelor Point S04E12",
             "url": "https://cdn.test/bp.mkv", "category": "Bangla"},
            {"id": "pb", "name": "Peaky Blinders S01 English",
             "url": "https://cdn.test/pb.mkv", "category": "English"},
            {"id": "pbe", "name": "Peaky Blinders S01E05 English",
             "url": "https://cdn.test/pbe.mkv", "category": "English"},
            {"id": "lonely", "name": "Lonely Show S01 Dual",
             "url": "https://cdn.test/l.mkv", "category": "Mix"},
        ]

    def test_no_card_is_added_removed_or_repointed(self) -> None:
        cards = self._cards()
        before = [(card["id"], card["url"]) for card in cards]
        self.movies._annotate_classification(cards)
        self.assertEqual([(card["id"], card["url"]) for card in cards], before)

    def test_a_plain_film_is_left_completely_alone(self) -> None:
        cards = self._cards()
        self.movies._annotate_classification(cards)
        film = next(card for card in cards if card["id"] == "d2")
        self.assertEqual(set(film) - {"id", "name", "url", "category"}, set())

    def test_an_explicit_episode_is_recorded(self) -> None:
        cards = self._cards()
        self.movies._annotate_classification(cards)
        card = next(card for card in cards if card["id"] == "bp")
        self.assertEqual(card["series_season_number"], 4)
        self.assertEqual(card["series_episode_number"], 12)
        self.assertEqual(card["series_evidence_tier"], "explicit_episode")
        self.assertNotIn("classification_pending", card)

    def test_a_season_only_row_never_gains_an_episode_number(self) -> None:
        cards = self._cards()
        self.movies._annotate_classification(cards)
        for identity in ("pb", "lonely"):
            card = next(card for card in cards if card["id"] == identity)
            self.assertEqual(card["series_season_number"], 1)
            self.assertNotIn(
                "series_episode_number", card,
                msg="a season marker must never produce an episode number",
            )

    def test_only_the_unproven_row_is_marked_pending(self) -> None:
        """Peaky Blinders S01 is proved by its own sibling; Lonely Show is not."""
        cards = self._cards()
        self.movies._annotate_classification(cards)
        pending = {
            card["id"] for card in cards if card.get("classification_pending")}
        self.assertEqual(pending, {"lonely"})

    def test_the_summary_counts_what_a_scan_report_needs(self) -> None:
        summary = self.movies._annotate_classification(self._cards())
        # Four series signals: two with an explicit episode (Bachelor Point
        # S04E12, Peaky Blinders S01E05) and two season-only (Peaky Blinders
        # S01, Lonely Show S01). Only the last is unproven - the other
        # season-only row has a sibling episode in the same fixture.
        self.assertEqual(summary["series_signal_candidates"], 4)
        self.assertEqual(summary["confirmed_series"], 2)
        self.assertEqual(summary["season_only_unknown_episode"], 2)
        self.assertEqual(summary["classification_pending"], 1)
        self.assertEqual(
            summary["confirmed_series"]
            + summary["season_only_unknown_episode"],
            summary["series_signal_candidates"],
        )

    def test_a_broken_classifier_leaves_the_catalogue_untouched(self) -> None:
        """Classification failing must never cost a scan."""
        cards = self._cards()
        original = ss.classify_rows
        try:
            ss.classify_rows = lambda signals: (_ for _ in ()).throw(
                RuntimeError("boom"))
            summary = self.movies._annotate_classification(cards)
        finally:
            ss.classify_rows = original
        self.assertEqual(summary, {})
        self.assertNotIn("series_show_key", cards[1])


class CategoryPendingTests(unittest.TestCase):
    """ধারা ৪.০, v৩.৪ correction 2 - an unknown category is marked, not dropped.

    The behaviour was already right: `_canonical_movie_category` has always
    sent an unknown category to Mix, so nothing was being lost. What was
    missing is the record of *why* a film is in Mix, which is what ধাপ ১১ needs
    to move the 937 Mix entries out again.
    """

    def setUp(self) -> None:
        from scanner import movies

        self.movies = movies

    def test_an_unknown_category_becomes_mix_and_is_flagged(self) -> None:
        for raw in ("Punjabi", "", None, "totally-unknown", "  "):
            with self.subTest(raw=raw):
                self.assertEqual(self.movies._canonical_movie_category(raw), "Mix")
                self.assertFalse(self.movies._has_known_movie_category(raw))

    def test_a_known_category_is_never_flagged(self) -> None:
        for raw in ("Bangla", "Mix", "south indian", "South Indian",
                    "Dubbed", "Premium"):
            with self.subTest(raw=raw):
                self.assertTrue(self.movies._has_known_movie_category(raw))

    def test_the_flag_is_taken_before_the_category_is_canonicalised(self) -> None:
        """Reading it after canonicalisation would flag nothing at all: by then
        every unknown category is already the string "Mix", which is known."""
        source = (Path(__file__).resolve().parents[1]
                  / "scanner" / "movies.py").read_text(encoding="utf-8")
        raw_read = source.index('raw_category = movie.get("category")')
        canonical = source.index("category = _canonical_movie_category(raw_category)")
        flag = source.index('movie_copy["category_pending"] = True')
        self.assertLess(raw_read, canonical)
        self.assertLess(canonical, flag)
        self.assertIn(
            "if not _has_known_movie_category(raw_category):", source)

    def test_category_pending_is_a_flag_the_no_loss_gate_understands(self) -> None:
        """A pending card is visible-pending to INVARIANT ২, never a loss."""
        from scanner import movie_coverage

        self.assertIn("category_pending", movie_coverage.PENDING_FLAGS)
        self.assertIn("classification_pending", movie_coverage.PENDING_FLAGS)


class RealCatalogueAnnotationTests(unittest.TestCase):
    """The rule that matters most, checked on all 1,667 published cards."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_no_published_card_would_gain_an_episode_it_never_stated(self) -> None:
        from scanner import movie_baseline, movies

        cards = [dict(card) for _, card in movie_baseline.published_movies(self.ROOT)]
        if not cards:
            self.skipTest("no published movie catalogue")
        original = mc.DEFAULT_PATH
        mc.DEFAULT_PATH = Path(tempfile.mkdtemp()) / "cache.json"
        try:
            movies._annotate_classification(cards)
        finally:
            mc.DEFAULT_PATH = original

        invented = [
            card["name"] for card in cards
            if card.get("series_episode_number") is not None
            and card.get("series_evidence_tier") != "explicit_episode"
        ]
        self.assertEqual(invented, [])


if __name__ == "__main__":
    unittest.main()
