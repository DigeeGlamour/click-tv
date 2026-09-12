"""A confident wrong answer is worse than no answer.

Two films share a title; a provider returns the other one; the catalogue
acquires its poster, its year and its rating. Everything looks complete
afterwards, which is what makes it dangerous - nothing on the page tells a
viewer that the whole record belongs to a different film.

So these tests are about what a provider is ALLOWED to do, on what evidence,
and what an admin's assertion outranks.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scanner import movie_metadata_confidence as mc


class NormalisingATitle(unittest.TestCase):
    def test_case_and_punctuation_fall_away(self):
        self.assertEqual(mc.normalize_title("The Gray Man!"), mc.normalize_title("gray man"))

    def test_accents_fall_away(self):
        self.assertEqual(mc.normalize_title("Amélie"), "amelie")

    def test_a_leading_article_falls_away(self):
        self.assertEqual(mc.normalize_title("A Quiet Place"), mc.normalize_title("Quiet Place"))

    def test_a_sequel_number_does_not(self):
        self.assertNotEqual(mc.normalize_title("Gray Man 2"), mc.normalize_title("Gray Man"))

    def test_nothing_normalises_to_nothing(self):
        self.assertEqual(mc.normalize_title(None), "")


class ReadingAYear(unittest.TestCase):
    def test_from_a_year(self):
        self.assertEqual(mc.year_of(2019), 2019)

    def test_from_a_date(self):
        self.assertEqual(mc.year_of("2019-04-02"), 2019)

    def test_from_a_title(self):
        self.assertEqual(mc.year_of("Fukrey 3 (2023) Hindi"), 2023)

    def test_from_nothing(self):
        self.assertIsNone(mc.year_of(""))

    def test_a_resolution_is_not_a_year(self):
        self.assertIsNone(mc.year_of("1080p"))


class HowMuchToBelieve(unittest.TestCase):
    def local(self, **extra):
        row = {"name": "The Gray Man", "year": 2022}
        row.update(extra)
        return row

    def test_a_matching_external_id_is_the_strongest_signal(self):
        level, reason = mc.match_confidence(
            self.local(imdb_id="tt1649418"),
            {"name": "Something Else Entirely", "year": 1999, "imdb_id": "tt1649418"},
        )
        self.assertEqual(level, mc.CONFIDENCE_HIGH)
        self.assertIn("external id", reason)

    def test_a_disagreeing_external_id_rejects_outright(self):
        level, reason = mc.match_confidence(
            self.local(imdb_id="tt1649418"),
            {"name": "The Gray Man", "year": 2022, "imdb_id": "tt9999999"},
        )
        self.assertIsNone(level)
        self.assertIn("disagrees", reason)

    def test_exact_title_and_year_is_high(self):
        level, _reason = mc.match_confidence(
            self.local(), {"name": "the gray man", "year": 2022})
        self.assertEqual(level, mc.CONFIDENCE_HIGH)

    def test_a_year_off_by_one_is_medium(self):
        # Release years legitimately differ by a year between territories.
        level, _reason = mc.match_confidence(
            self.local(), {"name": "The Gray Man", "year": 2023})
        self.assertEqual(level, mc.CONFIDENCE_MEDIUM)

    def test_no_year_to_confirm_it_is_medium(self):
        level, reason = mc.match_confidence(
            {"name": "The Gray Man"}, {"name": "The Gray Man"})
        self.assertEqual(level, mc.CONFIDENCE_MEDIUM)
        self.assertIn("no year", reason)

    def test_a_conflicting_year_is_low(self):
        # The remake problem: same title, decades apart.
        level, _reason = mc.match_confidence(
            self.local(), {"name": "The Gray Man", "year": 1974})
        self.assertEqual(level, mc.CONFIDENCE_LOW)

    def test_a_different_title_is_low(self):
        level, _reason = mc.match_confidence(
            self.local(), {"name": "The Grey", "year": 2022})
        self.assertEqual(level, mc.CONFIDENCE_LOW)

    def test_a_sequel_is_not_its_predecessor(self):
        level, _reason = mc.match_confidence(
            {"name": "Fukrey 3", "year": 2023}, {"name": "Fukrey", "year": 2013})
        self.assertEqual(level, mc.CONFIDENCE_LOW)

    def test_a_series_result_never_matches_a_movie(self):
        level, reason = mc.match_confidence(
            self.local(), {"name": "The Gray Man", "year": 2022, "media_type": "tv"})
        self.assertIsNone(level)
        self.assertIn("content type", reason)

    def test_a_movie_result_never_matches_a_series(self):
        level, _reason = mc.match_confidence(
            {"name": "Undekhi", "year": 2022, "content_kind": "series"},
            {"name": "Undekhi", "year": 2022, "media_type": "movie"})
        self.assertIsNone(level)

    def test_a_person_result_is_rejected(self):
        level, reason = mc.match_confidence(
            self.local(), {"name": "The Gray Man", "media_type": "person"})
        self.assertIsNone(level)
        self.assertIn("person", reason)

    def test_an_alias_counts_as_an_exact_title(self):
        level, _reason = mc.match_confidence(
            {"name": "Chokro 2", "year": 2026, "search_aliases": ["Chokro Two"]},
            {"name": "Chokro Two", "year": 2026})
        self.assertEqual(level, mc.CONFIDENCE_HIGH)


class WhatEachLevelMayWrite(unittest.TestCase):
    incoming = {
        "tmdb_id": 123, "imdb_id": "tt1", "poster": "p.jpg",
        "release_date": "2022-07-22", "genres": ["Action"], "category": "English",
    }

    def test_high_writes_the_identity_fields(self):
        plan = mc.plan_application({}, self.incoming, confidence=mc.CONFIDENCE_HIGH)
        self.assertEqual(plan["state"], "applied")
        self.assertIn("tmdb_id", plan["apply"])
        self.assertIn("poster", plan["apply"])

    def test_medium_writes_the_descriptive_fields_only(self):
        plan = mc.plan_application({}, self.incoming, confidence=mc.CONFIDENCE_MEDIUM)
        self.assertIn("poster", plan["apply"])
        self.assertNotIn("tmdb_id", plan["apply"])
        self.assertNotIn("imdb_id", plan["apply"])
        self.assertIn("high-confidence", plan["refused"]["tmdb_id"])

    def test_low_writes_nothing_at_all(self):
        plan = mc.plan_application({}, self.incoming, confidence=mc.CONFIDENCE_LOW)
        self.assertEqual(plan["apply"], {})
        self.assertEqual(plan["state"], "unresolved")

    def test_a_rejected_candidate_writes_nothing(self):
        plan = mc.plan_application({}, self.incoming, confidence=None)
        self.assertEqual(plan["apply"], {})
        self.assertEqual(plan["state"], "rejected")

    def test_no_level_overwrites_a_resolved_field(self):
        plan = mc.plan_application(
            {"poster": "already.jpg"}, self.incoming, confidence=mc.CONFIDENCE_HIGH)
        self.assertNotIn("poster", plan["apply"])
        self.assertIn("fill-only", plan["refused"]["poster"])

    def test_no_level_sets_the_category(self):
        plan = mc.plan_application({}, self.incoming, confidence=mc.CONFIDENCE_HIGH)
        self.assertNotIn("category", plan["apply"])


class ManualOverrides(unittest.TestCase):
    entry = {
        "poster": "https://admin.example/poster.jpg",
        "category": "Bangla",
        "lock_fields": ["tmdb_id"],
    }

    def test_an_asserted_field_is_locked_without_being_listed(self):
        self.assertIn("poster", mc.locked_fields(self.entry))

    def test_a_listed_field_is_locked_without_being_asserted(self):
        self.assertIn("tmdb_id", mc.locked_fields(self.entry))

    def test_a_provider_cannot_overwrite_a_locked_field(self):
        plan = mc.plan_application(
            {}, {"poster": "provider.jpg", "tmdb_id": 5},
            confidence=mc.CONFIDENCE_HIGH, manual=self.entry)
        self.assertEqual(plan["apply"], {})
        self.assertIn("manual", plan["refused"]["poster"])
        self.assertIn("locked", plan["refused"]["tmdb_id"])

    def test_a_manual_override_cannot_invent_a_rating(self):
        values = mc.manual_values({"rating": 9.9, "rating_source": "IMDb", "poster": "p.jpg"})
        self.assertNotIn("rating", values)
        self.assertNotIn("rating_source", values)
        self.assertIn("poster", values)

    def test_a_missing_file_is_simply_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            manual = mc.load_manual(str(Path(tmp) / "nope.json"))
        self.assertEqual(manual["movies"], {})

    def test_a_corrupt_file_is_simply_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.json"
            path.write_text("{not json", encoding="utf-8")
            manual = mc.load_manual(str(path))
        self.assertEqual(manual["movies"], {})

    def test_a_real_file_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.json"
            path.write_text(json.dumps(
                {"version": 1, "movies": {"film-1": {"poster": "p.jpg"}}}), encoding="utf-8")
            manual = mc.load_manual(str(path))
        self.assertEqual(mc.manual_entry(manual, "film-1")["poster"], "p.jpg")
        self.assertEqual(mc.manual_entry(manual, "missing"), {})


class CategoryAuthority(unittest.TestCase):
    def test_a_manual_category_wins_over_everything(self):
        category, reason = mc.resolve_category(
            "Mix", "English", confidence=mc.CONFIDENCE_HIGH,
            manual={"category": "Bangla"})
        self.assertEqual(category, "Bangla")
        self.assertIn("manual", reason)

    def test_a_source_stated_category_is_not_overridden(self):
        category, reason = mc.resolve_category(
            "Hindi", "English", confidence=mc.CONFIDENCE_HIGH, source_asserted=True)
        self.assertEqual(category, "Hindi")
        self.assertIn("source", reason)

    def test_mix_moves_out_on_a_high_confidence_match(self):
        category, _reason = mc.resolve_category(
            "Mix", "English", confidence=mc.CONFIDENCE_HIGH)
        self.assertEqual(category, "English")

    def test_mix_stays_put_on_a_medium_match(self):
        category, reason = mc.resolve_category(
            "Mix", "English", confidence=mc.CONFIDENCE_MEDIUM)
        self.assertEqual(category, "Mix")
        self.assertIn("high-confidence", reason)

    def test_mix_stays_put_on_a_low_match(self):
        category, _reason = mc.resolve_category(
            "Mix", "English", confidence=mc.CONFIDENCE_LOW)
        self.assertEqual(category, "Mix")

    def test_an_established_category_is_never_reclassified(self):
        category, reason = mc.resolve_category(
            "Bangla", "English", confidence=mc.CONFIDENCE_HIGH)
        self.assertEqual(category, "Bangla")
        self.assertIn("only the Mix fallback", reason)

    def test_premium_is_not_mix(self):
        # Business categories, not facts about a film.
        category, _reason = mc.resolve_category(
            "Premium", "Mix", confidence=mc.CONFIDENCE_HIGH)
        self.assertEqual(category, "Premium")
        category, _reason = mc.resolve_category(
            "Mix", "Premium", confidence=mc.CONFIDENCE_HIGH)
        self.assertEqual(category, "Premium", "only a confident match may move it")


class DuplicateExternalIds(unittest.TestCase):
    def test_two_films_claiming_one_id_are_flagged(self):
        conflicts = mc.external_id_conflicts({
            "film-a": {"tmdb_id": 500},
            "film-b": {"tmdb_id": 500},
            "film-c": {"tmdb_id": 501},
        })
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["items"], ["film-a", "film-b"])
        self.assertIn("nothing merged", conflicts[0]["action"])

    def test_a_clean_catalogue_reports_nothing(self):
        self.assertEqual(mc.external_id_conflicts({
            "film-a": {"tmdb_id": 500, "imdb_id": "tt1"},
            "film-b": {"tmdb_id": 501, "imdb_id": "tt2"},
        }), [])

    def test_both_identity_fields_are_checked(self):
        conflicts = mc.external_id_conflicts({
            "film-a": {"imdb_id": "tt1"},
            "film-b": {"imdb_id": "tt1"},
        })
        self.assertEqual(conflicts[0]["field"], "imdb_id")

    def test_items_without_an_external_id_are_not_conflicts(self):
        self.assertEqual(mc.external_id_conflicts({
            "film-a": {}, "film-b": {}, "film-c": {"tmdb_id": ""},
        }), [])


class LogsCarryNoSecrets(unittest.TestCase):
    def test_the_line_says_what_was_decided(self):
        line = mc.log_line(
            "gray-man-2022", "tmdb",
            {"name": "The Gray Man", "year": 2022, "tmdb_id": 725201},
            mc.CONFIDENCE_HIGH, "exact title and exact year")
        self.assertIn("gray-man-2022", line)
        self.assertIn("tmdb", line)
        self.assertIn("high", line)
        self.assertIn("725201", line)

    def test_a_rejection_says_so(self):
        line = mc.log_line("x", "omdb", {"name": "Other"}, None, "content type mismatch")
        self.assertIn("rejected", line)

    def test_no_credential_reaches_a_log(self):
        cleaned = mc.scrub({
            "name": "The Gray Man",
            "api_key": "secret-value",
            "headers": {"Authorization": "Bearer x"},
            "nested": [{"token": "t", "title": "keep me"}],
        })
        serialised = json.dumps(cleaned)
        self.assertIn("The Gray Man", serialised)
        self.assertIn("keep me", serialised)
        for leaked in ("secret-value", "Bearer", "api_key", "token", "headers"):
            self.assertNotIn(leaked, serialised)


if __name__ == "__main__":
    unittest.main()


class ThroughTheRealCache(unittest.TestCase):
    """The policy is enforced where metadata actually enters the catalogue."""

    def setUp(self):
        from scanner import movie_metadata_cache as cache

        self.cache = cache
        self.store = {"version": 1, "movies": {}}

    def upsert(self, fields, *, local=None, manual=None):
        return self.cache.upsert(
            self.store, "gray-man-2022", fields,
            local=local if local is not None else {"name": "The Gray Man", "year": 2022},
            manual=manual,
        )

    def test_a_good_match_is_applied(self):
        record = self.upsert({
            "match_title": "The Gray Man", "match_year": 2022,
            "tmdb_id": 725201, "genres": ["Action"],
        })
        self.assertEqual(record["tmdb_id"], 725201)
        self.assertEqual(record["metadata_confidence"], mc.CONFIDENCE_HIGH)
        self.assertEqual(record["metadata_match_state"], "applied")

    def test_a_wrong_year_match_writes_nothing(self):
        record = self.upsert({
            "match_title": "The Gray Man", "match_year": 1974,
            "tmdb_id": 999999, "genres": ["Drama"], "poster": "wrong.jpg",
        })
        self.assertNotIn("tmdb_id", record)
        self.assertNotIn("genres", record)
        self.assertEqual(record["metadata_match_state"], "unresolved")
        self.assertEqual(record["metadata_confidence"], mc.CONFIDENCE_LOW)

    def test_a_series_result_is_rejected_for_a_film(self):
        record = self.upsert({
            "match_title": "The Gray Man", "match_year": 2022,
            "content_kind": "series", "tmdb_id": 555, "genres": ["Action"],
        })
        self.assertNotIn("tmdb_id", record)
        self.assertEqual(record["metadata_match_state"], "rejected")
        self.assertIn("content type", record["metadata_match_reason"])

    def test_a_medium_match_takes_the_artwork_but_not_the_id(self):
        # backdrop is a cached metadata field; the identity fields are not
        # this match's to write.
        record = self.upsert({
            "match_title": "The Gray Man", "match_year": 2023,
            "tmdb_id": 725201, "backdrop": "b.jpg",
        })
        self.assertEqual(record["backdrop"], "b.jpg")
        self.assertNotIn("tmdb_id", record)
        self.assertEqual(record["metadata_confidence"], mc.CONFIDENCE_MEDIUM)

    def test_a_manual_lock_survives_a_high_confidence_match(self):
        record = self.upsert(
            {"match_title": "The Gray Man", "match_year": 2022,
             "backdrop": "provider.jpg", "genres": ["Action"]},
            manual={"backdrop": "https://admin.example/chosen.jpg"},
        )
        self.assertEqual(record["backdrop"], "https://admin.example/chosen.jpg")
        self.assertEqual(record["genres"], ["Action"])
        self.assertIn("backdrop", record["metadata_refused"])

    def test_a_failed_lookup_still_erases_nothing(self):
        self.upsert({"match_title": "The Gray Man", "match_year": 2022,
                     "genres": ["Action"], "rating": 6.5})
        record = self.cache.upsert(self.store, "gray-man-2022", None)
        self.assertEqual(record["genres"], ["Action"])
        self.assertEqual(record["rating"], 6.5)

    def test_the_legacy_call_still_behaves_exactly_as_before(self):
        # No local record and no overrides: the pre-PART-19 path, untouched.
        record = self.cache.upsert(
            self.store, "other-film", {"tmdb_id": 7, "metadata_confidence": "high"})
        self.assertEqual(record["tmdb_id"], 7)
        self.assertEqual(record["metadata_confidence"], "high")
        self.assertNotIn("metadata_match_state", record)
