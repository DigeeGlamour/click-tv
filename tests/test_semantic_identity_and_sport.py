"""One real fixture is one card, and only cricket and football are published.

WHAT THIS PINS, AND WHY

On 2026-09-06 the live site showed four things a viewer could see and the
pipeline could not:

    Hamburger SV Vs 1 FSV Mainz 05  /  Hamburger SV vs FSV Mainz 05
    England W vs Ireland W          /  England Women Vs Ireland Women
    Real Madrid Vs Eibar (Liga F)   /  Real Madrid W vs Eibar W
    FIM MotoJunior World Championship-Valencia, on Today Match, as FOOTBALL

The first three are one match published twice, because the two source
families spell clubs differently and nothing related the spellings. The
fourth is a motorcycle race: scanner/merger.py builds every event card with
`sport_type = event_sport(...)`, event_sport's football pattern contains the
bare word "championship" for the EFL, and sport_filter then read that derived
value back as "source sport field" - the merge layer's guess returning as the
classifier's evidence.

The rules these tests hold the pipeline to:

  * a club-form word cannot carry identity, so removing one relates two
    spellings of one club and nothing else;
  * a gender marker is removed from the participant and KEPT as the key's
    category, so a men's fixture and a women's fixture never fold together;
  * a women-only competition states the category even when the title does not;
  * anything the rules cannot derive needs a verified alias, never a guess;
  * a governing body or race series in the fixture's own words outranks any
    sport field, derived or not - and the rest of the sport gazetteer does
    NOT, because a club is allowed to be named after another sport.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scanner import fixture_dedupe, sport_filter, team_identity  # noqa: E402
from scanner.merger import participant_fold_key  # noqa: E402

KICKOFF = "2026-09-06T16:30:00+00:00"


def card(name, competition="", start=KICKOFF, **extra):
    item = {
        "name": name,
        "match_name": name,
        "competition": competition,
        "start_time": start,
        "sport_type": "football",
        "sport_type_derived": True,
        "event_type": "today_match",
        "source_pipeline": "today_match",
    }
    item.update(extra)
    return item


class OneRealFixtureIsOneCard(unittest.TestCase):
    """The four pairs seen live, and the shape of each."""

    def assert_folds(self, left, right, why):
        self.assertEqual(participant_fold_key(left), participant_fold_key(right), why)
        self.assertTrue(fixture_dedupe.same_fixture(left, right), why)

    def assert_apart(self, left, right, why):
        self.assertNotEqual(participant_fold_key(left), participant_fold_key(right), why)
        self.assertFalse(fixture_dedupe.same_fixture(left, right), why)

    def test_a_legal_prefix_does_not_make_a_second_fixture(self):
        """1. FSV Mainz 05 is FSV Mainz 05. The ordinal belongs to the club."""
        self.assert_folds(
            card("Hamburger SV Vs 1 FSV Mainz 05", "German Bundesliga"),
            card("Hamburger SV vs FSV Mainz 05", "German Bundesliga"),
            "the German 1. FC / 1. FSV convention",
        )

    def test_a_club_form_abbreviation_does_not_either(self):
        self.assert_folds(
            card("CA Lanús Vs Defensa y Justicia", "Liga Profesional de Fútbol"),
            card("Lanus vs Defensa Y Justicia", "Liga Profesional Argentina"),
            "CA is Club Atlético",
        )
        self.assert_folds(
            card("Deportivo Alavés Vs Osasuna", "LaLiga"),
            card("Alaves vs Osasuna", "La Liga"),
            "Deportivo Alavés is Alavés",
        )

    def test_W_and_Women_are_the_same_word(self):
        self.assert_folds(
            card("England W vs Ireland W", "3rd ODI"),
            card("England Women Vs Ireland Women", "ICC Women's Championship"),
            "the same women's ODI, spelled two ways",
        )

    def test_a_women_only_league_states_the_category(self):
        """Liga F and NWSL have no men's edition, so a neutral title in them
        is a women's fixture - which is what left these as two cards."""
        self.assert_folds(
            card("Real Madrid Vs Eibar", "Liga F Moeve"),
            card("Real Madrid W vs Eibar W", "Primera División Femenina"),
            "Liga F is the Spanish women's league",
        )
        self.assert_folds(
            card("Seattle Reign FC Vs San Diego Wave FC", "NWSL"),
            card("Seattle Reign FC W vs San Diego Wave W", "NWSL Women"),
            "NWSL is a women's league",
        )

    def test_an_initialism_needs_a_verified_alias(self):
        """OH Leuven is Oud-Heverlee Leuven, and no rule can know that: no
        club-form word separates the two spellings. It is in the table, with
        the evidence beside it."""
        self.assert_folds(
            card("SK Beveren vs OH Leuven", "Jupiler Pro League"),
            card("SK Beveren Vs Oud Heverlee Leuven", "Pro League"),
            "verified alias",
        )
        aliases = json.loads(
            (PROJECT_ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8"))
        entry = aliases["aliases"]["oh leuven"]
        self.assertEqual(entry["canonical"], "oud heverlee leuven")
        self.assertIn("2026-09-06", entry["evidence"])


class ThingsThatMustNeverFold(unittest.TestCase):
    """The guards. Every one of these is a fixture the site would lose."""

    def test_men_and_women_are_two_fixtures(self):
        for left_name, left_comp, right_name, right_comp in (
            ("Arsenal W vs Chelsea W", "Women's Super League",
             "Arsenal vs Chelsea", "Premier League"),
            ("Arsenal Women vs Chelsea Women", "WSL",
             "Arsenal Men vs Chelsea Men", "Premier League"),
            ("Barcelona W vs Real Madrid W", "Liga F",
             "Barcelona vs Real Madrid", "La Liga"),
            ("Trent Rockets Women vs Oval Women", "The Hundred Women",
             "Trent Rockets vs Oval", "The Hundred"),
        ):
            with self.subTest(left=left_name):
                self.assertFalse(fixture_dedupe.same_fixture(
                    card(left_name, left_comp), card(right_name, right_comp)))

    def test_clubs_that_merely_look_alike_stay_apart(self):
        for left, right in (
            ("Manchester United vs Arsenal", "Manchester City vs Arsenal"),
            ("Real Madrid vs Eibar", "Real Sociedad vs Eibar"),
            ("Racing Santander vs Eibar", "Racing Louisville vs Eibar"),
            ("Juventus vs Milan", "Juventus U23 vs Milan"),
            ("Bayern Munich vs Koln", "1860 Munich vs Koln"),
        ):
            with self.subTest(left=left):
                self.assertFalse(fixture_dedupe.same_fixture(card(left), card(right)))

    def test_a_leading_number_is_only_dropped_before_a_club_form_word(self):
        self.assertEqual(team_identity.structural_form("1860 Munich"), "1860 munich")
        self.assertEqual(team_identity.structural_form("09 Wolfsburg"), "09 wolfsburg")
        self.assertEqual(team_identity.structural_form("1 FSV Mainz 05"), "mainz 05")
        self.assertEqual(team_identity.structural_form("1 FC Koln"), "koln")

    def test_no_generic_gender_strip_leaks_into_the_key(self):
        """The word is removed from the participant and kept as the category.
        Dropping it outright is what the working agreement forbids."""
        womens = participant_fold_key(card("Arsenal W vs Chelsea W", "WSL"))
        neutral = participant_fold_key(card("Arsenal vs Chelsea", "Premier League"))
        self.assertTrue(womens.endswith("#women"), womens)
        self.assertTrue(neutral.endswith("#"), neutral)
        self.assertNotEqual(womens, neutral)

    def test_structural_form_never_empties_a_name(self):
        for name in ("Women", "FC", "W", "CA", "1"):
            self.assertTrue(team_identity.structural_form(name),
                            f"{name} was reduced to nothing")


class AGenericTitleIsNotEvidence(unittest.TestCase):
    """`3rd ODI England vs Ireland` carries no gender at all.

    It is the same shape as the women's fixture and gives nothing to tell it
    apart from a men's one, so it must stay its own card until something
    states the category. Merging it on the round alone is the mistake the
    working agreement calls worse than the duplicate.
    """

    def test_a_round_prefix_alone_does_not_merge_into_a_womens_fixture(self):
        generic = card("3rd ODI England vs Ireland", "")
        womens = card("England W vs Ireland W", "3rd ODI")
        self.assertEqual(team_identity.fixture_gender(generic), "")
        self.assertEqual(team_identity.fixture_gender(womens), "women")
        self.assertNotEqual(participant_fold_key(generic), participant_fold_key(womens))

    def test_it_merges_once_the_competition_states_the_category(self):
        stated = card("3rd ODI England vs Ireland", "ICC Women's Championship")
        womens = card("England W vs Ireland W", "3rd ODI")
        self.assertEqual(team_identity.fixture_gender(stated), "women")
        self.assertEqual(participant_fold_key(stated), participant_fold_key(womens))


class OnlyCricketAndFootballArePublished(unittest.TestCase):

    def _classify(self, name, competition=""):
        return sport_filter.classify(card(name, competition))

    def test_a_race_series_is_never_football(self):
        for name in ("FIM MotoJunior World Championship-Valencia",
                     "MotoGP Valencia Grand Prix",
                     "Formula 1 Italian Grand Prix",
                     "NASCAR Cup Series Championship"):
            with self.subTest(name=name):
                verdict = self._classify(name)
                self.assertEqual(verdict["state"], sport_filter.CONFIRMED_OTHER, verdict)
                self.assertFalse(sport_filter.is_publishable(card(name)))

    def test_the_derived_football_no_longer_wins(self):
        """The exact record that shipped: sport_type football, put there by
        the merge layer, with FIM in the title."""
        item = card("FIM MotoJunior World Championship-Valencia", "",
                    sport_type="football", sport_type_derived=True)
        self.assertEqual(sport_filter.classify(item)["reason"],
                         "governing body or race series")

    def test_pickleball_too(self):
        self.assertFalse(sport_filter.is_publishable(
            card("PPA Tour: Cary-Championship Sunday")))

    def test_a_club_named_after_another_sport_is_still_published(self):
        """The other half of the rule, and the reason the full gazetteer is
        NOT consulted before the sport field. Running it there cost five false
        positives on the 193 cards published that afternoon."""
        for name, competition, sport in (
            ("Lucknow Falcons vs Meerut Mavericks", "Emirates T20", "cricket"),
            ("Dublin Guardians vs Belfast Wolves", "ETPL", "cricket"),
            ("Dublin Guardians vs Edinburgh Castle Rockers", "ETPL", "cricket"),
            ("Bromley vs AFC Wimbledon", "National League", "football"),
            ("Seoul W vs Incheon Red Angels W", "WK League", "football"),
        ):
            with self.subTest(name=name):
                item = card(name, competition, sport_type=sport,
                            sport_type_derived=True)
                self.assertTrue(sport_filter.is_publishable(item),
                                sport_filter.classify(item))

    def test_a_real_source_sport_field_still_counts(self):
        for sport in ("cricket", "football"):
            item = {"name": "Some Local Derby", "sport_type": sport}
            self.assertEqual(sport_filter.classify(item)["reason"],
                             "source sport field")


class AMergeKeepsWhatBothCardsCarried(unittest.TestCase):
    """Folding two cards must not lose a source or a channel - the whole
    point is that the broadcasters become buttons on one card."""

    def test_the_fold_key_ignores_sources_and_channels(self):
        left = card("Hamburger SV Vs 1 FSV Mainz 05", "German Bundesliga",
                    source_ids=["sm-sports-data"],
                    channels=[{"name": "Server-1", "url": "https://a.example/x.m3u8"}])
        right = card("Hamburger SV vs FSV Mainz 05", "German Bundesliga",
                     source_ids=["srhady-bingstream", "srhady-axsports-live"],
                     channels=[{"name": "Server-1", "url": "https://b.example/y.m3u8"},
                               {"name": "Server-2", "url": "https://c.example/z.m3u8"}])
        self.assertEqual(participant_fold_key(left), participant_fold_key(right))
        # Nothing here may read the streams: the identity of a fixture is who
        # is playing, not who is showing it.
        self.assertTrue(fixture_dedupe.same_fixture(left, right))


def timeless(name, competition="", start="2026-09-06T13:47:26+00:00", **extra):
    """A card the feed gave no kickoff for - the scan stamped start_time."""
    item = card(name, competition, start, sport_type="cricket", **extra)
    item.pop("source_start_time", None)
    return item


def timed(name, competition="", start="2026-09-06T09:30:00+00:00", **extra):
    item = card(name, competition, start, sport_type="cricket", **extra)
    item["source_start_time"] = start
    return item


class ACardWithNoKickoffIsIdentifiedByItsRound(unittest.TestCase):
    """The general rule for a feed that publishes "LIVE NOW" and no time.

    The pipeline stamps the moment of the scan into start_time so the card can
    be sorted, and that stamp is not a kickoff. Compared as one it put
    "3rd ODI England vs Ireland" four hours away from the same match published
    as "England W vs Ireland W", and the site showed both.

    So such a card is matched on participants + round + day instead - and only
    when EXACTLY ONE fixture answers. One candidate is an identification; two
    is a question, and a question stays two cards.
    """

    def test_one_candidate_identifies_it(self):
        kept, report = fixture_dedupe.fold([
            timed("England W vs Ireland W", "3rd ODI"),
            timeless("3rd ODI England vs Ireland"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertIn("one candidate", report[0]["rule"])

    def test_two_candidates_are_a_question_and_stay_apart(self):
        """A men's and a women's 3rd ODI on one day. Nothing in the generic
        title says which, so nothing merges - the case the working agreement
        calls worse than the duplicate."""
        kept, _ = fixture_dedupe.fold([
            timed("England W vs Ireland W", "3rd ODI"),
            timed("England Men vs Ireland Men", "3rd ODI"),
            timeless("3rd ODI England vs Ireland"),
        ])
        self.assertEqual(len(kept), 3)

    def test_a_different_round_is_a_different_match(self):
        kept, _ = fixture_dedupe.fold([
            timed("England W vs Ireland W", "3rd ODI"),
            timeless("2nd ODI England vs Ireland"),
        ])
        self.assertEqual(len(kept), 2)

    def test_no_round_evidence_means_no_match(self):
        kept, _ = fixture_dedupe.fold([
            timed("England W vs Ireland W", "ODI Series"),
            timeless("England vs Ireland"),
        ])
        self.assertEqual(len(kept), 2)

    def test_a_different_day_is_a_different_match(self):
        kept, _ = fixture_dedupe.fold([
            timed("England W vs Ireland W", "3rd ODI",
                  start="2026-09-05T09:30:00+00:00"),
            timeless("3rd ODI England vs Ireland"),
        ])
        self.assertEqual(len(kept), 2)

    def test_the_round_prefix_is_read_off_the_participant(self):
        for title, expected in (
            ("3rd ODI England", "england"),
            ("1st Test Sri Lanka", "sri lanka"),
            ("2nd T20I India", "india"),
            ("11th Match Dublin Guardians", "dublin guardians"),
        ):
            with self.subTest(title=title):
                self.assertEqual(team_identity.structural_form(title), expected)

    def test_a_card_that_HAS_a_feed_kickoff_is_never_matched_this_way(self):
        """The rule exists for cards with no time. One that has a real
        kickoff goes through the ordinary comparison, which would keep these
        apart - four hours is four hours."""
        kept, _ = fixture_dedupe.fold([
            timed("England W vs Ireland W", "3rd ODI"),
            timed("3rd ODI England vs Ireland", start="2026-09-06T13:47:26+00:00"),
        ])
        self.assertEqual(len(kept), 2)


class FoldingNeverCostsAStream(unittest.TestCase):
    """Requirement 4: one card, every source, every broadcaster."""

    def test_all_sources_and_channels_survive_a_three_way_fold(self):
        kept, _ = fixture_dedupe.fold([
            timed("England Women Vs Ireland Women", "ICC Women's Championship",
                  source_ids=["sm-sports-data"],
                  channels=[{"name": "SKY CRICKET", "url": "https://a.example/1.m3u8"}]),
            timed("England W vs Ireland W", "3rd ODI",
                  source_ids=["srhady-bingstream", "srhady-axsports-live"],
                  channels=[{"name": "WILLOW", "url": "https://b.example/2.m3u8"},
                            {"name": "Willow 2", "url": "https://c.example/3.m3u8"}]),
            timeless("3rd ODI England vs Ireland",
                     source_ids=["srhady-primevideo-sports"],
                     channels=[{"name": "Prime Video", "url": "https://d.example/4.m3u8"}]),
        ])
        self.assertEqual(len(kept), 1)
        keeper = kept[0]
        self.assertEqual(
            set(keeper.get("source_ids") or []),
            {"sm-sports-data", "srhady-bingstream", "srhady-axsports-live",
             "srhady-primevideo-sports"})
        urls = {ch.get("url") for ch in (keeper.get("channels") or [])}
        for expected in ("https://a.example/1.m3u8", "https://b.example/2.m3u8",
                         "https://c.example/3.m3u8", "https://d.example/4.m3u8"):
            self.assertIn(expected, urls, "a broadcaster was lost in the fold")


class TheAliasTableStaysEvidenceLed(unittest.TestCase):

    def test_every_alias_carries_its_evidence(self):
        aliases = json.loads(
            (PROJECT_ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8"))
        for name, entry in aliases["aliases"].items():
            with self.subTest(alias=name):
                self.assertTrue(str(entry.get("canonical") or "").strip())
                self.assertGreater(len(str(entry.get("evidence") or "")), 40,
                                   f"{name} has no usable evidence")

    def test_no_generic_rule_is_expressible_as_an_alias(self):
        """The forbidden note is part of the file's contract."""
        raw = (PROJECT_ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8")
        self.assertIn("_forbidden", raw)


class AnApostropheJoinsAWordItDoesNotSeparateOne(unittest.TestCase):
    """`Newell's Old Boys` and `Newells Old Boys` are one club.

    Found 2026-09-06 by sweeping every published pair at one kickoff that the
    rules refuse and asking which token refuses them. The answer here was not a
    word at all: the punctuation rule replaced the apostrophe with a space, so
    one feed gave "newell s old boys" and the other "newells old boys", and one
    Argentinian fixture published twice.
    """

    def fixture(self, name, kickoff="2026-09-06T22:00:00+00:00"):
        return {"name": name, "start_time": kickoff, "competition": "Liga"}

    def test_the_two_spellings_are_one_fixture(self):
        self.assertTrue(fixture_dedupe.same_fixture(
            self.fixture("Rosario Central vs Newell's Old Boys"),
            self.fixture("Rosario Central Vs Newells Old Boys")))

    def test_both_identity_modules_normalise_it_the_same_way(self):
        for module in (fixture_dedupe, team_identity):
            with self.subTest(module=module.__name__):
                clean = getattr(module, "_clean", None) or module.normalize_team
                self.assertEqual(clean("Newell's"), clean("Newells"))

    def test_every_apostrophe_a_feed_might_use(self):
        for mark in ("'", "’", "ʼ", "`", "´"):
            with self.subTest(mark=mark):
                self.assertEqual(fixture_dedupe._clean("Newell%sd" % mark),
                                 "newelld")

    def test_other_punctuation_still_separates(self):
        self.assertEqual(fixture_dedupe._clean("Bayer 04-Leverkusen"),
                         "bayer 04 leverkusen")


class TheClubFormListGrewOnEvidence(unittest.TestCase):
    """Three abbreviations, each found by the same dataset-wide sweep.

    Across 39 publishes and 6419 published cards, six pairs sat at one kickoff
    with one side identical and the other differing by a single token. Three of
    those tokens are club-form abbreviations and are added here; the other
    three are not, and are not.
    """

    def fixture(self, name, kickoff="2026-09-06T19:00:00+00:00"):
        return {"name": name, "start_time": kickoff, "competition": "Liga"}

    def test_rcd_is_real_club_deportivo(self):
        """RCD Espanyol and Espanyol were both on Today Match at 19:00."""
        self.assertTrue(fixture_dedupe.same_fixture(
            self.fixture("Espanyol vs Sevilla"),
            self.fixture("RCD Espanyol vs Sevilla FC")))

    def test_ks_is_klub_sportowy(self):
        self.assertTrue(fixture_dedupe.same_fixture(
            self.fixture("Cracovia Krakow vs Gornik Zabrze"),
            self.fixture("KS Cracovia Vs Gornik Zabrze")))

    def test_estac_is_the_troyes_club_form(self):
        self.assertTrue(fixture_dedupe.same_fixture(
            self.fixture("Estac Troyes vs Strasbourg"),
            self.fixture("Troyes Vs RC Strasbourg")))

    def test_none_of_them_can_empty_a_name(self):
        for word in ("rcd", "ks", "estac"):
            with self.subTest(word=word):
                self.assertEqual(team_identity.structural_form(word), word)

    def test_the_anchor_rule_still_refuses_two_spellings_at_once(self):
        """Both sides merely similar is still not a fixture. This is what keeps
        `Manchester United vs Arsenal` and `Manchester City vs Arsenal` apart,
        and it is why `Baltika Kaliningrad vs Lokomotiv Moscow` is still two
        cards beside `Baltika vs Lokomotiv` - recorded, not quietly folded."""
        self.assertFalse(fixture_dedupe.same_fixture(
            self.fixture("Manchester United vs Arsenal"),
            self.fixture("Manchester City vs Arsenal")))
        self.assertFalse(fixture_dedupe.same_fixture(
            self.fixture("Baltika vs Lokomotiv"),
            self.fixture("Baltika Kaliningrad Vs Lokomotiv Moscow")))

    def test_a_rename_is_not_a_form_word(self):
        """Chicago Red Stars became Chicago Stars FC. That is a fact about one
        club, so it belongs in the alias table with its evidence or nowhere -
        never in a list of words that carry no identity."""
        self.assertFalse(fixture_dedupe.same_fixture(
            self.fixture("Chicago Stars FC Vs North Carolina Courage"),
            self.fixture("Chicago Red Stars W vs North Carolina Courage W")))


if __name__ == "__main__":
    unittest.main()
