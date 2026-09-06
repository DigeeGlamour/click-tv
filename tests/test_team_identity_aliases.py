"""P50 IDENTITY CORRECTION V2 - one canonical name per club, by exact table.

FINAL_2's identity rule is "normalized teams + competition + kickoff bucket".
The competition and the kickoff were handled; the teams were normalized only
for punctuation, accents and corporate abbreviations. So on 2026-09-05 one
Premier League fixture existed twice:

    provider:brighton-vs-leeds|premier league|2026-09-05                (bingstream)
    provider:brighton-hove-albion-vs-leeds-united|premier league|...    (sm-sports-data)

archived as ended at 14:52:04 and simultaneously live on Today. Both sides were
spelled differently at once, and `fixture_dedupe.same_fixture` requires one
side to match exactly - the anchor that keeps `Manchester United vs Arsenal`
and `Manchester City vs Arsenal` apart.

The missing step is deliberately the dullest one available: an exact lookup in
config/team-aliases.json, read by scanner/team_identity.py, asked by
`fixture_dedupe.sides()` and `merger.participant_fold_key()`. No score, no
substring, no token overlap, no "drop the last word". A club with no entry is
untouched, and Manchester United is not Manchester City in any of them.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import event_archive as ea  # noqa: E402
from scanner import fixture_dedupe  # noqa: E402
from scanner import team_identity  # noqa: E402
from scanner.merger import participant_fold_key, same_real_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALIASES = ROOT / "config" / "team-aliases.json"
KICKOFF = "2026-09-05T14:00:00+00:00"
NOW = __import__("datetime").datetime(
    2026, 9, 5, 16, 0, tzinfo=__import__("datetime").timezone.utc)


def _card(name, *, competition="English Premier League", start=KICKOFF,
          sport="football", fixture_id=None):
    slug = name.casefold().replace(" ", "-").replace("&", "and")
    return {
        "id": slug,
        "fixture_id": fixture_id or "provider:%s|%s|%s" % (
            slug, competition.casefold(), start[:10]),
        "name": name,
        "competition": competition,
        "sport_type": sport,
        "start_time": start,
        "lifecycle_state": "ENDED",
    }


def _archive_with(*cards):
    archive = {"fixtures": {}}
    ea.archive_retired(list(cards), now=NOW, archive=archive)
    return archive


def _verdicts(left, right):
    """Every identity consumer, asked the same question."""
    return {
        "dedupe": fixture_dedupe.same_fixture(left, right),
        "merge": same_real_fixture(left, right),
        "archive": ea.is_archived(right, _archive_with(left)),
    }


class ACaseTheRealBugCameFrom(unittest.TestCase):
    def test_brighton_and_leeds_are_one_fixture(self):
        left = _card("Brighton vs Leeds", competition="Premier League")
        right = _card("Brighton & Hove Albion vs Leeds United")
        self.assertEqual({"dedupe": True, "merge": True, "archive": True},
                         _verdicts(left, right))

    def test_every_consumer_gives_the_same_verdict(self):
        """The correction is only correct if nothing disagrees - an archive
        that blocks what the merge layer would publish is a new fault."""
        left = _card("Brighton vs Leeds", competition="Premier League")
        for spelling in ("Brighton Hove Albion vs Leeds United",
                         "Brighton & Hove Albion vs Leeds",
                         "Brighton vs Leeds United"):
            verdicts = _verdicts(left, _card(spelling))
            with self.subTest(name=spelling):
                self.assertEqual({True}, set(verdicts.values()), verdicts)

    def test_the_sides_really_are_canonical(self):
        self.assertEqual(
            fixture_dedupe.sides(_card("Brighton vs Leeds")),
            fixture_dedupe.sides(_card("Brighton & Hove Albion vs Leeds United")),
        )
        self.assertEqual(
            participant_fold_key(_card("Brighton vs Leeds")),
            participant_fold_key(_card("Brighton & Hove Albion vs Leeds United")),
        )


class BManchesterMustNeverFold(unittest.TestCase):
    def test_united_and_city_stay_two_clubs(self):
        left = _card("Manchester United vs Arsenal")
        right = _card("Manchester City vs Arsenal")
        self.assertEqual({"dedupe": False, "merge": False, "archive": False},
                         _verdicts(left, right))

    def test_their_canonical_names_differ(self):
        self.assertNotEqual(team_identity.canonical_team("Manchester United"),
                            team_identity.canonical_team("Manchester City"))
        self.assertEqual("manchester united",
                         team_identity.canonical_team("Manchester United"))

    def test_other_near_neighbours_stay_apart(self):
        for left_name, right_name in (
            ("Sheffield United vs Arsenal", "Sheffield Wednesday vs Arsenal"),
            ("Nottingham Forest vs Chelsea", "Nottingham County vs Chelsea"),
            ("West Ham United vs Fulham", "West Bromwich Albion vs Fulham"),
        ):
            verdicts = _verdicts(_card(left_name), _card(right_name))
            with self.subTest(pair=(left_name, right_name)):
                self.assertEqual({False}, set(verdicts.values()), verdicts)


class CNoGenericSuffixRuleExists(unittest.TestCase):
    def test_united_is_not_stripped_from_an_unlisted_club(self):
        for name in ("Manchester United", "West Ham United", "Newcastle United",
                     "Sheffield United"):
            self.assertEqual(team_identity.normalize_team(name),
                             team_identity.canonical_team(name), name)

    def test_city_and_town_are_not_stripped(self):
        for name in ("Manchester City", "Norwich City", "Ipswich Town"):
            self.assertEqual(team_identity.normalize_team(name),
                             team_identity.canonical_team(name), name)

    def test_the_table_holds_exact_spellings_and_nothing_else(self):
        table = team_identity.load_aliases()
        self.assertTrue(table)
        for spelling, canonical in table.items():
            self.assertEqual(team_identity.normalize_team(spelling), spelling)
            self.assertEqual(team_identity.normalize_team(canonical), canonical)
            self.assertNotEqual(spelling, canonical)

    def test_no_similarity_machinery_anywhere_in_the_module(self):
        source = (ROOT / "scanner" / "team_identity.py").read_text(
            encoding="utf-8").casefold()
        for banned in ("difflib", "sequencematcher", "levenshtein", "fuzz",
                       "startswith", "endswith", "in name", "ratio("):
            self.assertNotIn(banned, source, banned)

    def test_no_fixture_is_special_cased_in_code(self):
        for module in ("event_archive.py", "fixture_dedupe.py", "merger.py",
                       "team_identity.py"):
            source = (ROOT / "scanner" / module).read_text(
                encoding="utf-8").casefold()
            self.assertNotIn('"brighton', source, module)
            self.assertNotIn("'brighton", source, module)

    def test_every_alias_carries_its_evidence(self):
        payload = json.loads(ALIASES.read_text(encoding="utf-8"))
        for spelling, entry in payload["aliases"].items():
            self.assertIn("evidence", entry, spelling)
            self.assertGreater(len(entry["evidence"]), 40, spelling)


class DCompetitionStillSeparates(unittest.TestCase):
    def test_same_canonical_teams_in_a_different_competition_are_two_fixtures(self):
        left = _card("Brighton vs Leeds", competition="English Premier League")
        right = _card("Brighton & Hove Albion vs Leeds United",
                      competition="FA Cup", start="2026-09-05T14:05:00+00:00")
        self.assertFalse(same_real_fixture(left, right))
        self.assertFalse(ea.is_archived(right, _archive_with(left)))


class EKickoffStillSeparates(unittest.TestCase):
    def test_a_different_kickoff_is_a_different_fixture(self):
        left = _card("Brighton vs Leeds", competition="Premier League")
        right = _card("Brighton & Hove Albion vs Leeds United",
                      start="2026-09-05T19:00:00+00:00")
        self.assertEqual({"dedupe": False, "merge": False, "archive": False},
                         _verdicts(left, right))

    def test_a_kickoff_inside_the_existing_tolerance_still_folds(self):
        """The tolerance is the merge layer's, unchanged - so the competition
        has to be spelled the same way here. Two different descriptions of one
        league only reconcile on an identical kickoff, which is the merge
        layer's own rule and not something this correction touches."""
        left = _card("Brighton vs Leeds")
        right = _card("Brighton & Hove Albion vs Leeds United",
                      start="2026-09-05T14:04:00+00:00")
        self.assertTrue(same_real_fixture(left, right))

    def test_two_spellings_of_one_league_reconcile_on_an_exact_kickoff(self):
        """Which is what the real Brighton pair had: the same 14:00 to the
        second, and one of the two competition strings blank."""
        left = _card("Brighton vs Leeds", competition="Premier League")
        right = _card("Brighton & Hove Albion vs Leeds United")
        self.assertTrue(same_real_fixture(left, right))


class FAFutureRematchIsAdmitted(unittest.TestCase):
    def test_the_same_two_clubs_in_november_are_not_archived(self):
        archive = _archive_with(_card("Brighton vs Leeds",
                                      competition="Premier League"))
        rematch = _card("Brighton & Hove Albion vs Leeds United",
                        start="2026-11-20T15:00:00+00:00")
        self.assertFalse(ea.is_archived(rematch, archive))
        kept, dropped = ea.drop_resurrected([rematch], archive)
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)


class GExactProviderIdIsUnchanged(unittest.TestCase):
    def test_the_stored_identity_still_blocks_on_its_own(self):
        ended = _card("Some Unlisted Club vs Another Unlisted Club",
                      competition="Some League")
        archive = _archive_with(ended)
        self.assertTrue(ea.is_archived(dict(ended), archive))

    def test_an_unlisted_club_is_matched_only_by_the_existing_rules(self):
        left = _card("Grotta vs Volsungur", competition="1. Deild")
        right = _card("Grotta FC vs Volsungur", competition="1. Deild")
        self.assertTrue(fixture_dedupe.same_fixture(left, right),
                        "the existing corporate-suffix rule, not an alias")


class HLegacyArchiveEntriesStillWork(unittest.TestCase):
    def test_a_seven_field_row_written_before_this_change_still_matches(self):
        old = {"fixtures": {"provider:brighton-vs-leeds|premier league|2026-09-05": {
            "id": "brighton-vs-leeds",
            "fixture_id": "provider:brighton-vs-leeds|premier league|2026-09-05",
            "name": "Brighton vs Leeds",
            "start_time": KICKOFF,
            "ended_seen_at": "2026-09-05T14:25:20+00:00",
            "lifecycle_state": "END_PENDING",
            "archived_at": "2026-09-05T14:52:04+00:00",
        }}}
        self.assertTrue(ea.is_archived(
            _card("Brighton & Hove Albion vs Leeds United"), old))
        self.assertFalse(ea.is_archived(
            _card("Brighton & Hove Albion vs Leeds United",
                  start="2026-11-20T15:00:00+00:00"), old))


class IRepeatedRetirementDoesNotGrow(unittest.TestCase):
    def test_seven_retirements_are_one_entry(self):
        archive = {"fixtures": {}}
        for _ in range(7):
            ea.archive_retired([_card("Brighton vs Leeds")], now=NOW,
                               archive=archive)
        self.assertEqual(1, len(archive["fixtures"]))


class JBothTabsRefuseIt(unittest.TestCase):
    def test_today_and_upcoming_both_drop_the_canonical_equivalent(self):
        archive = _archive_with(_card("Brighton vs Leeds",
                                      competition="Premier League"))
        returning = _card("Brighton & Hove Albion vs Leeds United")
        for tab in ("today", "upcoming"):
            kept, dropped = ea.drop_resurrected([dict(returning)], archive)
            self.assertEqual([], kept, tab)
            self.assertEqual(["Brighton & Hove Albion vs Leeds United"],
                             dropped, tab)


class TheAliasTableIsDataAndDegradesQuietly(unittest.TestCase):
    def setUp(self):
        team_identity.clear_cache()

    def tearDown(self):
        team_identity.clear_cache()

    def test_a_missing_file_leaves_every_name_untouched(self):
        # POST-P51: `canonical_team` gained the fixture's category as its
        # second argument, so the alias path is now named. The guarantee this
        # test exists for - a missing table changes no name - is unchanged.
        missing = Path(tempfile.mkdtemp()) / "team-aliases.json"
        self.assertEqual({}, team_identity.load_aliases(missing))
        self.assertEqual("brighton hove albion",
                         team_identity.canonical_team("Brighton & Hove Albion",
                                                      path=missing))
        self.assertEqual("brighton w",
                         team_identity.canonical_team("Brighton W", "women",
                                                      path=missing))

    def test_a_broken_file_never_raises(self):
        broken = Path(tempfile.mkdtemp()) / "team-aliases.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual({}, team_identity.load_aliases(broken))

    def test_a_self_referential_or_blank_entry_is_ignored(self):
        path = Path(tempfile.mkdtemp()) / "team-aliases.json"
        path.write_text(json.dumps({"aliases": {
            "leeds": {"canonical": "leeds"},
            "": {"canonical": "x"},
            "arsenal": {"canonical": ""},
        }}), encoding="utf-8")
        self.assertEqual({}, team_identity.load_aliases(path))

    def test_the_ampersand_and_the_word_reach_the_same_entry(self):
        self.assertEqual(team_identity.canonical_team("Brighton & Hove Albion"),
                         team_identity.canonical_team("Brighton and Hove Albion"))


class NothingAboutTheCardsChanged(unittest.TestCase):
    def test_a_canonical_name_is_never_what_a_viewer_reads(self):
        """`correct_home_away` rewrites a title from `_teams_of`, the feed's own
        spelling. The alias table is comparison-only."""
        card = _card("Brighton & Hove Albion vs Leeds United")
        before = dict(card)
        fixture_dedupe.sides(card)
        participant_fold_key(card)
        self.assertEqual(before, card)
        self.assertEqual(("Brighton & Hove Albion", "Leeds United"),
                         fixture_dedupe._teams_of(card))

    def test_folding_two_spellings_keeps_one_card_and_both_sources(self):
        left = dict(_card("Brighton vs Leeds", competition="Premier League"),
                    source_ids=["srhady-bingstream"], available_link_count=1,
                    url="https://a.example/1.m3u8")
        right = dict(_card("Brighton & Hove Albion vs Leeds United"),
                     source_ids=["sm-sports-data"], available_link_count=2,
                     url="https://b.example/2.m3u8")
        kept, folded = fixture_dedupe.fold([left, right], lambda item: None)
        self.assertEqual(1, len(kept))
        self.assertEqual(1, len(folded))
        self.assertEqual({"srhady-bingstream", "sm-sports-data"},
                         set(kept[0].get("source_ids") or []))


if __name__ == "__main__":
    unittest.main()
