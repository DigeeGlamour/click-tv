"""ধাপ ১১ / A-03 - emptying the Mix bin without mislabelling anything.

    ৯৩৭টি মুভি (৫৬%) "Mix"-এ — এটি ক্যাটাগরি নয়, আবর্জনার ঝুড়ি।

Measured on the live catalogue, and the plan's number is exact: 937 of 1,667,
against 29 in Bangla.

The risk here is not failing to move films. It is moving the wrong ones: a
category is what a viewer trusts to mean something, and a router that files a
Hindi-dubbed Hollywood film under English has made the Dubbed row lie. So most
of this file is about what the router REFUSES to do.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_category_router as router  # noqa: E402


def mix(name="A Film", **extra):
    card = {"name": name, "category": "Mix"}
    card.update(extra)
    return card


class TheLanguageDecidesWhenThereIsOne(unittest.TestCase):
    def test_bengali_goes_to_bangla(self):
        self.assertEqual(router.decide("bn")[0], "Bangla")

    def test_hindi_goes_to_hindi(self):
        self.assertEqual(router.decide("hi")[0], "Hindi")

    def test_the_four_south_indian_languages_go_together(self):
        for code in ("ta", "te", "ml", "kn"):
            with self.subTest(code=code):
                self.assertEqual(router.decide(code)[0], "South Indian")

    def test_english_goes_to_english(self):
        self.assertEqual(router.decide("en")[0], "English")

    def test_the_reason_is_recorded(self):
        self.assertEqual(router.decide("bn")[1], router.BY_LANGUAGE)

    def test_a_language_with_no_row_on_this_site_stays_in_mix(self):
        """Japanese, Korean, Spanish. Mix is the right answer for them, and
        the reason distinguishes "we decided" from "we never looked"."""
        category, reason = router.decide("ja")
        self.assertIsNone(category)
        self.assertEqual(reason, router.UNMAPPED)


class TheShapesProvidersActuallySend(unittest.TestCase):
    """TMDB answers "bn" and `[{"iso_3166_1": "BD"}]`; OMDb answers "Bengali"
    and "Bangladesh". Both have to arrive at the same row."""

    def test_a_full_language_name_is_understood(self):
        self.assertEqual(router.decide("Bengali")[0], "Bangla")
        self.assertEqual(router.decide("Tamil")[0], "South Indian")

    def test_a_regional_tag_is_reduced_to_its_language(self):
        self.assertEqual(router.decide("bn-BD")[0], "Bangla")
        self.assertEqual(router.decide("pt_BR")[1], router.UNMAPPED)

    def test_case_and_whitespace_do_not_matter(self):
        self.assertEqual(router.decide("  HINDI ")[0], "Hindi")

    def test_tmdb_country_objects_are_read(self):
        self.assertEqual(
            router.normalise_countries([{"iso_3166_1": "BD", "name": "Bangladesh"}]),
            ["BD"],
        )

    def test_omdb_country_names_are_read(self):
        self.assertEqual(
            router.normalise_countries("Bangladesh, India"), ["BD", "IN"])

    def test_a_country_listed_twice_is_listed_once(self):
        self.assertEqual(
            router.normalise_countries(["BD", "Bangladesh"]), ["BD"])

    def test_nothing_is_an_empty_list_not_an_error(self):
        self.assertEqual(router.normalise_countries(None), [])
        self.assertEqual(router.normalise_countries(""), [])
        self.assertEqual(router.normalise_language(None), "")


class WhatTheRouterRefusesToDo(unittest.TestCase):
    def test_a_stated_category_is_never_overruled(self):
        """Rule 1. A film a source put in Hindi is in Hindi because somebody
        said so; metadata may fill a blank, not contradict a statement."""
        category, reason = router.decide("en", current_category="Hindi")
        self.assertIsNone(category)
        self.assertEqual(reason, router.NOT_MIX)

    def test_nothing_is_ever_routed_to_dubbed(self):
        """Rule 2, and the reason it exists: "Dubbed" describes the audio of
        THIS file, not the film. A Hindi dub of a Hollywood action film has
        original_language "en" - routing it to English empties the row the
        viewer opened to hear Hindi."""
        for language in ("en", "hi", "bn", "ta", "ja"):
            with self.subTest(language=language):
                self.assertNotIn(
                    router.decide(language)[0], router.NEVER_INFERRED)

    def test_nothing_is_ever_routed_to_premium(self):
        self.assertNotIn("Premium", router.LANGUAGE_CATEGORY.values())
        self.assertNotIn("Premium", router.COUNTRY_CATEGORY.values())

    def test_india_alone_proves_nothing(self):
        """Rule 3. India makes Hindi films and South Indian films alike, so a
        country with no language is not evidence - this is how a Tamil film
        would be filed under Hindi."""
        category, reason = router.decide(None, ["IN"])
        self.assertIsNone(category)
        self.assertEqual(reason, router.NO_EVIDENCE)

    def test_bangladesh_alone_is_enough(self):
        """The one country that names one industry."""
        category, reason = router.decide(None, ["BD"])
        self.assertEqual(category, "Bangla")
        self.assertEqual(reason, router.BY_COUNTRY)

    def test_no_evidence_at_all_stays_in_mix(self):
        self.assertEqual(router.decide(None, None), (None, router.NO_EVIDENCE))

    def test_a_language_beats_a_country_that_disagrees(self):
        """A Bengali-language film shot in India is a Bangla film."""
        self.assertEqual(router.decide("bn", ["IN"])[0], "Bangla")

    def test_every_routable_category_is_a_real_one(self):
        from scanner import movies

        for category in router.ROUTABLE:
            self.assertIn(category, movies.VALID_MOVIE_CATEGORIES)
        for category in set(router.LANGUAGE_CATEGORY.values()) | set(
                router.COUNTRY_CATEGORY.values()):
            self.assertIn(category, router.ROUTABLE)


class ReadingACardAndItsRecord(unittest.TestCase):
    def test_the_card_is_believed_over_the_provider(self):
        """A source that stated a language knows more about this file than a
        provider that matched it by title."""
        card = mix(original_language="bn")
        record = {"original_language": "en"}
        self.assertEqual(router.decide_for(card, record)[0], "Bangla")

    def test_the_record_fills_what_the_card_does_not_have(self):
        self.assertEqual(
            router.decide_for(mix(), {"original_language": "hi"})[0], "Hindi")

    def test_a_missing_record_is_simply_no_evidence(self):
        self.assertEqual(router.decide_for(mix(), None)[1], router.NO_EVIDENCE)

    def test_a_country_on_the_record_is_used_when_no_language_exists(self):
        self.assertEqual(
            router.decide_for(mix(), {"production_countries": [
                {"iso_3166_1": "BD"}]})[0],
            "Bangla",
        )


class RedistributionMovesAndNeverLoses(unittest.TestCase):
    def _records(self, pairs):
        return {key: {"original_language": value} for key, value in pairs}

    def test_a_film_is_moved_and_the_move_is_recorded_on_the_card(self):
        card = mix("Rongin Shurma")
        summary = router.redistribute(
            [card], {"x": {"original_language": "bn"}},
            identity=lambda item: "x")
        self.assertEqual(card["category"], "Bangla")
        self.assertEqual(card["category_routed_from"], "Mix")
        self.assertEqual(card["category_routed_by"], router.BY_LANGUAGE)
        self.assertEqual(summary["moved"], 1)

    def test_every_card_survives_whether_or_not_it_moved(self):
        """A-03 is about where a film appears, never about whether it does."""
        cards = [mix("A"), mix("B"), {"name": "C", "category": "Hindi"}]
        before = len(cards)
        router.redistribute(cards, {}, identity=lambda item: item["name"])
        self.assertEqual(len(cards), before)
        self.assertEqual([card["name"] for card in cards], ["A", "B", "C"])

    def test_a_film_that_cannot_be_placed_keeps_its_category_untouched(self):
        card = mix("Unknown")
        router.redistribute([card], {}, identity=lambda item: "x")
        self.assertEqual(card["category"], "Mix")
        self.assertNotIn("category_routed_by", card)

    def test_a_card_outside_mix_is_not_even_considered(self):
        card = {"name": "Stated", "category": "Hindi"}
        summary = router.redistribute(
            [card], {"x": {"original_language": "en"}},
            identity=lambda item: "x")
        self.assertEqual(summary["considered"], 0)
        self.assertEqual(card["category"], "Hindi")

    def test_the_summary_accounts_for_every_card_it_considered(self):
        cards = [mix("A"), mix("B"), mix("C")]
        records = {"A": {"original_language": "bn"},
                   "B": {"original_language": "ja"}}
        summary = router.redistribute(
            cards, records, identity=lambda item: item["name"])
        self.assertEqual(summary["considered"], 3)
        self.assertEqual(summary["moved"] + summary["remaining_in_mix"], 3)
        self.assertEqual(summary["reasons"],
                         {router.BY_LANGUAGE: 1, router.UNMAPPED: 1,
                          router.NO_EVIDENCE: 1})

    def test_a_broken_identity_function_costs_one_card_not_the_run(self):
        def explode(item):
            raise RuntimeError("no identity")

        cards = [mix("A")]
        summary = router.redistribute(cards, {"x": {}}, identity=explode)
        self.assertEqual(cards[0]["category"], "Mix")
        self.assertEqual(summary["considered"], 1)

    def test_an_empty_catalogue_is_not_an_error(self):
        self.assertEqual(router.redistribute([], {})["considered"], 0)

    def test_a_malformed_card_is_skipped(self):
        summary = router.redistribute(
            ["not a dict", None, mix("A")], {}, identity=lambda item: "x")
        self.assertEqual(summary["considered"], 1)


class TheLogLineIsHonest(unittest.TestCase):
    def test_it_says_when_nothing_could_be_placed_yet(self):
        line = router.describe(
            {"considered": 937, "moved": 0, "remaining_in_mix": 937})
        self.assertIn("937 in Mix", line)
        self.assertIn("none placeable yet", line)

    def test_it_names_where_the_films_went(self):
        line = router.describe({
            "considered": 100, "moved": 30, "remaining_in_mix": 70,
            "moved_by_category": {"Bangla": 20, "Hindi": 10}})
        self.assertIn("Bangla 20", line)
        self.assertIn("70 stay in Mix", line)

    def test_nothing_to_say_says_nothing(self):
        self.assertEqual(router.describe({}), "")


class TheWiring(unittest.TestCase):
    MOVIES = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
    # The order only means anything inside the function that does the work;
    # these names appear in other functions too.
    PROCESS = MOVIES[MOVIES.index("def process_movies("):]

    def test_it_runs_before_the_catalogue_is_split_by_category(self):
        """After grouping, a film is already on the Mix page."""
        redistribute = self.PROCESS.index("_redistribute_mix(merged_movies)")
        grouped = self.PROCESS.index("grouped_movies: Dict[str, List[Dict[str, Any]]]")
        self.assertLess(redistribute, grouped)

    def test_it_runs_after_classification(self):
        """Episode cards leave the movie catalogue on classification; routing
        one into Bangla first would only have to be undone."""
        classify = self.PROCESS.index("_annotate_classification(merged_movies)")
        redistribute = self.PROCESS.index("_redistribute_mix(merged_movies)")
        self.assertLess(classify, redistribute)

    def test_it_never_makes_a_provider_call(self):
        """A second, unbudgeted round of requests in the middle of the publish
        path is the thing this must not become. Reads the calls rather than
        the prose - the docstring says "never a lookup" quite deliberately."""
        import ast

        tree = ast.parse(self.MOVIES)
        function = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_redistribute_mix"
        )
        names = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.ImportFrom):
                names.update(alias.name for alias in node.names)
        for forbidden in ("resolve_metadata", "metadata_providers",
                          "enrich", "lookup", "requests", "urlopen"):
            self.assertNotIn(forbidden, names,
                             msg=f"{forbidden} reached from the publish path")
        self.assertIn("movie_metadata_cache", names)

    def test_it_cannot_fail_a_scan(self):
        self.assertIn("Mix redistribution skipped", self.MOVIES)


class MovingAFilmIsNotLosingIt(unittest.TestCase):
    """The risk this phase introduces, and the reason it is safe.

    The no-loss inventory line is `movie|{slug}|{identity}|{url}` - it carries
    the CATEGORY. Moving 937 films out of Mix rewrites the slug on every one
    of them, so if INVARIANT ২ compared those lines as text, ধাপ ১১ would
    report the largest loss in the catalogue's history and block its own
    publish.

    It does not: `_inventory_families` keys on the stream URL and keeps the
    line only as the human-readable value. This test is what stops that
    becoming a text comparison later.
    """

    def test_a_category_change_keeps_the_same_stream_family(self):
        from scanner import movie_coverage

        url = "https://cdn.example.com/films/rongin-shurma.mkv"
        before = movie_coverage._inventory_families(
            [f"movie|mix|remote-manual-rongin-shurma-2026|{url}"])
        after = movie_coverage._inventory_families(
            [f"movie|bangla|remote-manual-rongin-shurma-2026|{url}"])
        self.assertEqual(set(before), set(after))
        self.assertTrue(set(before), "no family was derived at all")

    def test_the_whole_mix_row_could_move_with_no_loss(self):
        from scanner import movie_coverage

        urls = [f"https://cdn.example.com/f/{index}.mkv" for index in range(50)]
        previous = [f"movie|mix|id-{index}|{url}"
                    for index, url in enumerate(urls)]
        current = [f"movie|bangla|id-{index}|{url}"
                   for index, url in enumerate(urls)]
        lost = set(movie_coverage._inventory_families(previous)) - set(
            movie_coverage._inventory_families(current))
        self.assertEqual(lost, set())


class TheMetadataSchemaKnowsTheFieldsAreNew(unittest.TestCase):
    def test_both_fields_are_cached(self):
        from scanner import movie_metadata_cache

        self.assertIn("original_language", movie_metadata_cache.METADATA_FIELDS)
        self.assertIn("production_countries",
                      movie_metadata_cache.METADATA_FIELDS)

    def test_the_schema_was_bumped_so_old_records_are_re_read(self):
        """Every record resolved before this phase has neither field. Without
        the bump they would never be filled and A-03 would never start."""
        from scanner import movie_metadata_cache

        self.assertGreaterEqual(movie_metadata_cache.METADATA_SCHEMA, 3)

    def test_a_record_from_the_old_schema_is_due_for_refresh(self):
        from scanner import movie_metadata_cache as cache

        record = {"tmdb_id": 1, "metadata_schema": 2,
                  "metadata_updated_at": "2026-09-24T00:00:00+00:00"}
        self.assertEqual(cache.lookup_state(record), cache.LOOKUP_DUE)


if __name__ == "__main__":
    unittest.main()
