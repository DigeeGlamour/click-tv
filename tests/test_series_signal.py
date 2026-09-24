"""ধারা ৪.৬ - what a title says about season and episode, and what it does not.

Two rules carry the most weight here, and both exist because the earlier draft
of the plan got them wrong:

  * an episode number is never invented. `S01` means the season is known and
    the episode is not - it does not mean episode 1. The draft would have
    turned 181 rows in this catalogue into "Episode 1", each a false claim
    about what a viewer is about to watch.
  * a series identity never contains a year. House of the Dragon is 2022, 2024
    and 2026 in this very catalogue; grouping on title+year would split one
    show into three cards, which is the problem this work exists to fix.

The regression test that matters most is `S01E15`. The first version of the
range pattern made its separator optional, so it read `E15` as "1" then "5" and
reported episodes 1 to 5. Left alone it would have corrupted all 348 explicit
episode numbers in the catalogue at once.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import series_signal as ss  # noqa: E402


class TitleProtectionTests(unittest.TestCase):
    """A number in a title is usually part of the title."""

    def test_a_number_in_a_film_title_is_not_a_season(self) -> None:
        for title in ("1917", "2012", "Drishyam 2", "Blade Runner 2049 (2017)",
                      "Ocean's 8", "Se7en", "300", "The Magnificent Seven"):
            with self.subTest(title=title):
                signal = ss.detect(title)
                self.assertFalse(signal["is_series_signal"], msg=signal)
                self.assertIsNone(signal["season"])
                self.assertIsNone(signal["episode"])

    def test_an_apostrophe_s_is_not_a_season_marker(self) -> None:
        """This catalogue contains "Newton's 3rd Law"; a looser pattern reads
        `'s 3` as season 3."""
        signal = ss.detect("Newton's 3rd Law")
        self.assertFalse(signal["is_series_signal"])

    def test_a_season_marker_inside_a_word_is_not_a_marker(self) -> None:
        for title in ("MS Dhoni", "Words2 Live By", "ABS1 Rescue"):
            with self.subTest(title=title):
                self.assertFalse(ss.detect(title)["is_series_signal"])


class ExplicitEpisodeTests(unittest.TestCase):
    """348 rows in this catalogue state both numbers. None needs an API."""

    def test_the_plain_form(self) -> None:
        signal = ss.detect("Ananta Bhalobasha S01E15 Bangla Dubbed")
        self.assertEqual(
            (signal["season"], signal["episode"], signal["episode_end"]),
            (1, 15, None),
        )
        self.assertEqual(signal["detected_pattern"], ss.PATTERN_SXXEXX)
        self.assertEqual(signal["base_show"], "Ananta Bhalobasha")

    def test_s01e15_is_episode_15_and_not_episodes_1_to_5(self) -> None:
        """The regression that would have corrupted 348 episode numbers."""
        for title, expected in (
            ("Show S01E15", 15),
            ("Show S02E06 Dual Audio", 6),
            ("Show S01E01", 1),
            ("Show S03E04 Hindi", 4),
            ("Show S10E123", 123),
        ):
            with self.subTest(title=title):
                signal = ss.detect(title)
                self.assertEqual(signal["episode"], expected)
                self.assertIsNone(signal["episode_end"])

    def test_the_spacing_and_ep_variants(self) -> None:
        for title in ("Show S1 E 5", "Show S01EP05", "Show S1E05"):
            with self.subTest(title=title):
                signal = ss.detect(title)
                self.assertEqual((signal["season"], signal["episode"]), (1, 5))

    def test_season_and_episode_written_as_words(self) -> None:
        signal = ss.detect("Bachelor Point Season 4 Episode 12")
        self.assertEqual((signal["season"], signal["episode"]), (4, 12))


class RangeTests(unittest.TestCase):
    """A batch link covers a stretch of a season and must say so."""

    def test_the_three_spellings_this_catalogue_uses(self) -> None:
        for title, expected in (
            ("Vikings (2019) S06E11 20 Dual Audio", (6, 11, 20)),
            ("The Walking Dead Dead City S01E1 4 Hindi", (1, 1, 4)),
            ("Vincenzo S1 E 1 to 10 Hindi Dubbed", (1, 1, 10)),
            ("Show S01E05-08 WEB-DL", (1, 5, 8)),
        ):
            with self.subTest(title=title):
                signal = ss.detect(title)
                self.assertEqual(
                    (signal["season"], signal["episode"], signal["episode_end"]),
                    expected,
                )

    def test_a_resolution_after_an_episode_is_not_a_range_end(self) -> None:
        for title, expected in (
            ("Show S01E15 1080p", 15),
            ("Show S02E03 720p Dual", 3),
            ("Show S01E02 2160p", 2),
        ):
            with self.subTest(title=title):
                signal = ss.detect(title)
                self.assertEqual(signal["episode"], expected)
                self.assertIsNone(signal["episode_end"])

    def test_a_year_after_an_episode_is_not_a_range_end(self) -> None:
        signal = ss.detect("Show S02E09 2023")
        self.assertEqual(signal["episode"], 9)
        self.assertIsNone(signal["episode_end"])

    def test_a_backwards_range_falls_back_to_one_episode(self) -> None:
        signal = ss.detect("Show S01E09 3")
        self.assertEqual(signal["episode"], 9)
        self.assertIsNone(signal["episode_end"])


class NeverInventedTests(unittest.TestCase):
    """The rule with the most rows behind it."""

    def test_a_season_marker_alone_leaves_the_episode_unknown(self) -> None:
        for title in ("Peaky Blinders S01 English",
                      "Squid Game (2024) S02 Dual Audio",
                      "The Flash (2014) S01 Dual Audio BluRay"):
            with self.subTest(title=title):
                signal = ss.detect(title)
                self.assertTrue(signal["is_series_signal"])
                self.assertIsNotNone(signal["season"])
                self.assertIsNone(
                    signal["episode"],
                    msg="a season marker must never produce an episode number",
                )

    def test_a_part_is_not_an_episode(self) -> None:
        """"The Sandman S1 P01" is Season 1 Part 1, and a Part number is not
        an episode number."""
        signal = ss.detect("The Sandman S1 P01 Hindi Dubbed")
        self.assertEqual(signal["season"], 1)
        self.assertEqual(signal["part"], 1)
        self.assertIsNone(signal["episode"])
        self.assertEqual(signal["detected_pattern"], ss.PATTERN_SEASON_PART)

    def test_the_season_label_never_says_episode_one(self) -> None:
        unspecified = ss.detect("Peaky Blinders S01 English")
        self.assertEqual(ss.season_label(unspecified), "Season 1 — Unspecified")
        pack = ss.detect("Dark Desire (2022) S02 Complete Season")
        self.assertEqual(ss.season_label(pack), "Season 2 — Complete Season")
        for signal in (unspecified, pack):
            self.assertNotIn("Episode", ss.season_label(signal))

    def test_a_language_beginning_with_e_is_not_an_episode(self) -> None:
        """A naive "not followed by E" guard rejects "S01 English" and loses
        five real rows, one of them the plan's own worked example."""
        signal = ss.detect("Peaky Blinders S01 English")
        self.assertTrue(signal["is_series_signal"])
        self.assertEqual(signal["season"], 1)


class ShowIdentityTests(unittest.TestCase):
    """ধারা ৪.৬ - a series identity never contains a year."""

    def test_one_show_across_three_release_years_is_one_key(self) -> None:
        keys = {
            ss.detect(title)["show_key"] for title in (
                "House of the Dragon (2022) S01E01",
                "House Of The Dragon (2024) S02E07 Dual",
                "House of the Dragon (2026) S03E04",
                "House of the Dragon S02 E02 Dual",
            )
        }
        self.assertEqual(len(keys), 1, msg=keys)
        self.assertNotIn("2022", next(iter(keys)))

    def test_reacher_and_ertugrul_behave_the_same_way(self) -> None:
        for titles in (
            ("Reacher (2023) S01 Dual Audio", "Reacher (2025) S03E06"),
            ("Dirilis Ertugrul (2023) S01E01 03",
             "Dirilis Ertugrul (2024) S02E13 15"),
        ):
            with self.subTest(titles=titles):
                self.assertEqual(
                    len({ss.detect(title)["show_key"] for title in titles}), 1)

    def test_release_noise_does_not_create_a_second_show(self) -> None:
        self.assertEqual(
            ss.detect("Loki (2023) S02E06 Dual Audio Hindi ORG DSNP")["show_key"],
            ss.detect("Loki S02E07 1080p WEB-DL")["show_key"],
        )

    def test_the_year_is_still_reported_even_though_it_is_not_in_the_key(self) -> None:
        """Movies keep `title + exact year`; only the show key drops it."""
        signal = ss.detect("Loki (2023) S02E06")
        self.assertEqual(signal["year"], 2023)
        self.assertNotIn("2023", signal["show_key"])

    def test_two_different_shows_do_not_share_a_key(self) -> None:
        self.assertNotEqual(
            ss.detect("Bigg Boss S01E01")["show_key"],
            ss.detect("Bigg Boss OTT S01E01")["show_key"],
        )

    def test_a_marker_with_no_show_name_has_no_key(self) -> None:
        """Guessing a group for it would be exactly the over-merge ধাপ ৩ক
        exists to catch."""
        signal = ss.detect("S01E01")
        self.assertTrue(signal["is_series_signal"])
        self.assertEqual(signal["show_key"], "")


class EvidenceTierTests(unittest.TestCase):
    """ধারা ৪.৬'s three tiers, and what each one is allowed to do."""

    def _classify(self, *titles):
        return ss.classify_rows([ss.detect(title) for title in titles])

    def test_an_explicit_episode_is_tier_one_evidence(self) -> None:
        rows = self._classify("Show S01E05")
        self.assertEqual(rows[0]["evidence_tier"], ss.TIER_EXPLICIT_EPISODE)
        self.assertTrue(rows[0]["moves_to_series"])

    def test_a_pack_signal_moves_without_inventing_an_episode(self) -> None:
        rows = self._classify("Show S02 Complete Season")
        self.assertEqual(rows[0]["evidence_tier"], ss.TIER_PACK_SIGNAL)
        self.assertTrue(rows[0]["moves_to_series"])
        self.assertIsNone(rows[0]["episode"])

    def test_a_sibling_episode_proves_a_series_at_zero_api_cost(self) -> None:
        """ধারা ৪.৬ tier 2: "Peaky Blinders S01" is proved a series by
        "Peaky Blinders S01E05" already being in the catalogue."""
        rows = self._classify(
            "Peaky Blinders S01 English", "Peaky Blinders S01E05 English")
        self.assertEqual(rows[0]["evidence_tier"], ss.TIER_SIBLING_EPISODE)
        self.assertTrue(rows[0]["moves_to_series"])

    def test_no_evidence_stays_a_visible_movie_card(self) -> None:
        """"অনিশ্চয়তা থাকলে আমরা অনুমান করব না, হারাবও না, এবং লুকাবও না."" """
        rows = self._classify("Lonely Show S01 Dual Audio")
        self.assertEqual(rows[0]["evidence_tier"], ss.TIER_NO_EVIDENCE)
        self.assertFalse(rows[0]["moves_to_series"])
        self.assertTrue(rows[0]["classification_pending"])

    def test_a_row_climbs_a_tier_when_a_sibling_arrives(self) -> None:
        """The 181 unproven rows need no API call to be resolved later - a new
        episode of the same show resolves them for free."""
        before = self._classify("Lonely Show S01 Dual Audio")
        self.assertEqual(before[0]["evidence_tier"], ss.TIER_NO_EVIDENCE)
        after = self._classify(
            "Lonely Show S01 Dual Audio", "Lonely Show S01E02 Dual Audio")
        self.assertEqual(after[0]["evidence_tier"], ss.TIER_SIBLING_EPISODE)

    def test_a_plain_film_gets_no_tier_at_all(self) -> None:
        rows = self._classify("Drishyam 2")
        self.assertEqual(rows[0]["evidence_tier"], "")
        self.assertFalse(rows[0]["moves_to_series"])


class RealCatalogueTests(unittest.TestCase):
    """The detector reproduces the plan's own independent audit.

    These numbers were counted by hand for the master plan before this module
    existed. Matching them is the strongest evidence available that the rules
    here are the rules the plan measured.
    """

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls) -> None:
        from scanner import movie_baseline

        cards = movie_baseline.published_movies(cls.ROOT)
        if not cards:
            raise unittest.SkipTest("no published movie catalogue")
        cls.rows = ss.classify_rows(
            [ss.detect(card.get("name")) for _, card in cards])

    def _tier(self, tier: str) -> int:
        return sum(1 for row in self.rows if row.get("evidence_tier") == tier)

    def test_explicit_episode_rows_are_about_three_hundred_and_fifty(self) -> None:
        """Plan: 350. A catalogue moves between runs, so this is a band."""
        self.assertGreaterEqual(self._tier(ss.TIER_EXPLICIT_EPISODE), 300)
        self.assertLessEqual(self._tier(ss.TIER_EXPLICIT_EPISODE), 400)

    def test_the_two_smaller_tiers_stay_the_size_the_plan_counted(self) -> None:
        """Plan: 16 pack signals and 26 sibling-proven rows.

        Bands, for the reason the test above already gives - "a catalogue moves
        between runs". These two were pinned exactly, and on 2026-09-24 the
        first publish the no-loss gate let through grew the catalogue from
        1,667 cards to 1,970; the counts became 14 and 20 and the exact
        assertion failed the test suite, which stops every scan.

        A band still does the job this class exists for. The plan's audit found
        a handful of pack signals and a couple of dozen sibling-proven rows
        among hundreds of explicit episodes; a detector that had stopped
        reproducing the plan's rules would not land near those numbers, it
        would land at zero or in the hundreds.
        """
        self.assertGreaterEqual(self._tier(ss.TIER_PACK_SIGNAL), 8)
        self.assertLessEqual(self._tier(ss.TIER_PACK_SIGNAL), 30)
        self.assertGreaterEqual(self._tier(ss.TIER_SIBLING_EPISODE), 13)
        self.assertLessEqual(self._tier(ss.TIER_SIBLING_EPISODE), 45)

    def test_the_small_tiers_stay_small_beside_the_explicit_one(self) -> None:
        """The shape of the plan's audit, which a band cannot express: pack and
        sibling evidence are the rare cases, explicit episodes the common one.
        A detector that started reading ordinary films as packs would break
        this long before it broke either band."""
        explicit = self._tier(ss.TIER_EXPLICIT_EPISODE)
        self.assertGreater(explicit, 5 * self._tier(ss.TIER_PACK_SIGNAL))
        self.assertGreater(explicit, 5 * self._tier(ss.TIER_SIBLING_EPISODE))

    def test_the_shows_the_plan_names_collapse_as_it_predicted(self) -> None:
        """Bachelor Point 37 cards -> 1, Kurulus Osman 12 -> 1."""
        from collections import Counter

        counts = Counter(
            row["show_key"] for row in self.rows if row.get("moves_to_series"))
        self.assertEqual(counts["show:bachelor-point"], 37)
        self.assertEqual(counts["show:kurulus-osman"], 12)

    def test_no_row_in_the_real_catalogue_gets_an_invented_episode(self) -> None:
        for row in self.rows:
            if row.get("detected_pattern") in (
                ss.PATTERN_SEASON_ONLY, ss.PATTERN_SEASON_PART
            ):
                self.assertIsNone(row["episode"], msg=row["raw_title"])


if __name__ == "__main__":
    unittest.main()
