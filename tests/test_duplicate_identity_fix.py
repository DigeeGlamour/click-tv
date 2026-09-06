"""POST-P51 DUPLICATE FIX - two real one-match-two-cards cases, closed exactly.

PROMPT 51's audit found real duplicates on the live tabs. Two were investigated
and fixed here; both were pre-existing, and both are the same failure family as
the Brighton/Leeds blocker: a club spelled differently by different feeds.

CASE 1, measured 2026-09-05 16:30Z - ONE LaLiga fixture, THREE cards:

    Rayo Vallecano Vs Real Racing Club          sm-sports-data       4 links
    Rayo Vallecano vs Racing Club de Santander  six fancode feeds    0 links
    Rayo Vallecano vs Racing Santander          srhady-tapmad-bd     1 link

    Independent check: ESPN esp.1 event 401882889, "Racing Santander at Rayo
    Vallecano", 2026-09-05T16:30Z. ESPN team id 87, slug esp.racing_santander.

CASE 2, measured 2026-09-06 11:00Z - ONE Women's Super League fixture, twice:

    Brighton W vs Arsenal W   bingstream + axsports   competition "FA WSL"
    Brighton Vs Arsenal       ten feeds               competition "Women's Super League"

    Independent check: ESPN eng.w.1 ("English Women's Super League"), "Arsenal
    at Brighton & Hove Albion", 2026-09-06T11:00Z. ESPN's men's eng.1 that day
    has no Brighton fixture and nothing at 11:00.

CASE 2 is NOT a rule about the letter W. No suffix is stripped. Two exact
aliases carry `"gender": "women"`, and a scoped alias is reachable only by a
fixture whose own evidence - its title or its competition - says women. On top
of that `fixture_dedupe.same_fixture` now refuses two fixtures whose categories
differ, which is the rule `participant_fold_key` has always enforced through
its `#women` tag; saying it once means the tabs and the merge layer cannot
answer differently.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import fixture_dedupe  # noqa: E402
from scanner import team_identity  # noqa: E402
from scanner.merger import participant_fold_key, same_real_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALIASES = ROOT / "config" / "team-aliases.json"

LALIGA_KICKOFF = "2026-09-05T16:30:00+00:00"
WSL_KICKOFF = "2026-09-06T11:00:00+00:00"


def _card(name, *, competition, start, sport="football", source_ids=None,
          channels=None, fixture_id=None):
    slug = name.casefold().replace(" ", "-").replace("&", "and")
    return {
        "id": slug,
        "name": name,
        "fixture_id": fixture_id or f"provider:{slug}|{competition.casefold()}|{start[:10]}",
        "competition": competition,
        "sport_type": sport,
        "start_time": start,
        "start_at": start,
        "source_ids": list(source_ids or ["feed-a"]),
        "channels": list(channels or []),
    }


# The three real CASE 1 cards, as the scan published them.
REAL_RACING = _card("Rayo Vallecano Vs Real Racing Club",
                    competition="LaLiga", start=LALIGA_KICKOFF,
                    source_ids=["sm-sports-data"])
RACING_DE_SANTANDER = _card("Rayo Vallecano vs Racing Club de Santander",
                            competition="LALIGA 2026-27", start=LALIGA_KICKOFF,
                            source_ids=["sm-fancode", "sportlive-fancode-backup"])
RACING_SANTANDER = _card("Rayo Vallecano vs Racing Santander",
                         competition="LaLiga 2026/27", start=LALIGA_KICKOFF,
                         source_ids=["srhady-tapmad-bd"])

# The two real CASE 2 cards.
BRIGHTON_W = _card("Brighton W vs Arsenal W", competition="FA WSL",
                   start=WSL_KICKOFF,
                   source_ids=["srhady-bingstream", "srhady-axsports-live"])
BRIGHTON_NEUTRAL = _card("Brighton Vs Arsenal",
                         competition="Women's Super League", start=WSL_KICKOFF,
                         source_ids=["sm-sports-data", "sm-fancode"])
# The men's fixture that must never be folded into either of them.
BRIGHTON_MEN = _card("Brighton Vs Arsenal", competition="English Premier League",
                     start=WSL_KICKOFF, source_ids=["srhady-bingstream"])


def _both_layers_say_same(left, right):
    return (fixture_dedupe.same_fixture(left, right),
            same_real_fixture(left, right))


class Case1Racing(unittest.TestCase):
    """SAFETY CASE 1 - the three spellings are one club, on both layers."""

    def test_every_pair_of_the_three_is_one_fixture(self):
        for left, right in ((REAL_RACING, RACING_SANTANDER),
                            (RACING_DE_SANTANDER, RACING_SANTANDER),
                            (REAL_RACING, RACING_DE_SANTANDER)):
            dedupe, merger = _both_layers_say_same(left, right)
            self.assertTrue(dedupe, f"dedupe: {left['name']} vs {right['name']}")
            self.assertTrue(merger, f"merger: {left['name']} vs {right['name']}")

    def test_the_two_layers_agree(self):
        for left, right in ((REAL_RACING, RACING_SANTANDER),
                            (RACING_DE_SANTANDER, RACING_SANTANDER)):
            dedupe, merger = _both_layers_say_same(left, right)
            self.assertEqual(dedupe, merger)

    def test_all_three_reduce_to_one_canonical_pair(self):
        pairs = {fixture_dedupe.sides(card)
                 for card in (REAL_RACING, RACING_DE_SANTANDER, RACING_SANTANDER)}
        self.assertEqual(pairs, {("rayo vallecano", "racing santander")})

    def test_fold_keeps_one_card_and_every_source(self):
        kept, report = fixture_dedupe.fold(
            [dict(REAL_RACING), dict(RACING_DE_SANTANDER), dict(RACING_SANTANDER)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(report), 2)
        self.assertEqual(
            set(kept[0]["source_ids"]),
            {"sm-sports-data", "sm-fancode", "sportlive-fancode-backup",
             "srhady-tapmad-bd"})

    def test_the_canonical_name_is_the_espn_spelling(self):
        self.assertEqual(team_identity.canonical_team("Real Racing Club"),
                         "racing santander")
        self.assertEqual(team_identity.canonical_team("Racing Club de Santander"),
                         "racing santander")

    def test_a_different_racing_is_untouched(self):
        # SAFETY: four other clubs in these feeds carry the word.
        for name in ("Racing Club", "Racing Louisville", "Racing Cordoba",
                     "Racing Genk", "Racing"):
            self.assertEqual(team_identity.canonical_team(name),
                             team_identity.normalize_team(name),
                             f"{name} must not be canonicalised")

    def test_racing_club_avellaneda_keeps_its_own_canonical_name(self):
        # The alias must not draw Racing Club (Avellaneda) towards Santander.
        self.assertEqual(team_identity.canonical_team("Racing Club"), "racing club")
        self.assertNotEqual(team_identity.canonical_team("Racing Club"),
                            team_identity.canonical_team("Real Racing Club"))
        left = _card("Racing Club vs Boca Juniors",
                     competition="Liga Profesional", start=LALIGA_KICKOFF)
        right = _card("Racing Santander vs Boca Juniors",
                      competition="Liga Profesional", start=LALIGA_KICKOFF)
        self.assertFalse(same_real_fixture(left, right))

    def test_the_truncation_rule_is_unchanged_by_this_fix(self):
        # PRE-EXISTING, recorded rather than quietly inherited: `same_side`
        # treats a >=5 character prefix as a truncation, so "racing" reads as
        # a short form of "racing santander" once `_bare` has taken the word
        # "club" off. Measured on the tree BEFORE this fix and after it, the
        # answer is the same - this correction neither caused it nor widened
        # it, and narrowing `same_side` is a separate change with its own
        # regression risk. Logged as an open finding.
        self.assertTrue(fixture_dedupe.same_side("racing club", "racing santander"))
        self.assertFalse(fixture_dedupe.same_side("racing louisville",
                                                  "racing santander"))
        self.assertFalse(fixture_dedupe.same_side("racing cordoba",
                                                  "racing santander"))


class Case2GenderSafe(unittest.TestCase):
    """SAFETY CASE 8 - a women's fixture folds; a men's one never joins it."""

    def test_the_two_womens_cards_are_one_fixture(self):
        dedupe, merger = _both_layers_say_same(BRIGHTON_W, BRIGHTON_NEUTRAL)
        self.assertTrue(dedupe)
        self.assertTrue(merger)

    def test_the_mens_fixture_folds_with_neither(self):
        for other in (BRIGHTON_W, BRIGHTON_NEUTRAL):
            dedupe, merger = _both_layers_say_same(BRIGHTON_MEN, other)
            self.assertFalse(dedupe, f"dedupe folded men into {other['name']}")
            self.assertFalse(merger, f"merger folded men into {other['name']}")

    def test_the_womens_evidence_is_not_lost(self):
        self.assertEqual(team_identity.fixture_gender(BRIGHTON_W), "women")
        self.assertEqual(team_identity.fixture_gender(BRIGHTON_NEUTRAL), "women")
        self.assertEqual(team_identity.fixture_gender(BRIGHTON_MEN), "")
        self.assertTrue(participant_fold_key(BRIGHTON_W).endswith("#women"))
        self.assertTrue(participant_fold_key(BRIGHTON_NEUTRAL).endswith("#women"))
        self.assertFalse(participant_fold_key(BRIGHTON_MEN).endswith("#women"))

    def test_the_scoped_alias_is_unreachable_without_womens_evidence(self):
        self.assertEqual(team_identity.canonical_team("Brighton W"), "brighton w")
        self.assertEqual(team_identity.canonical_team("Brighton W", "men"),
                         "brighton w")
        self.assertEqual(team_identity.canonical_team("Brighton W", "women"),
                         "brighton")

    def test_no_generic_w_rule_exists(self):
        # SAFETY: only the two verified spellings move. Every other W name in
        # today's data is left exactly as the feed wrote it.
        for name in ("Tottenham Hotspur W", "West Ham W", "Charlton Athletic W",
                     "Liverpool W", "Crystal Palace W", "Everton W",
                     "Racing Louisville W", "Angel City W", "England W",
                     "Chicago Red Stars W", "W Connection"):
            self.assertEqual(team_identity.canonical_team(name, "women"),
                             team_identity.normalize_team(name),
                             f"{name} must not be canonicalised")

    def test_a_womens_card_never_folds_into_a_neutral_one_by_default(self):
        # Racing Louisville W / Racing Louisville is the same shape as CASE 2
        # and has NO alias, so it must still be two fixtures.
        left = _card("Racing Louisville W vs Angel City W",
                     competition="NWSL Women", start=LALIGA_KICKOFF)
        right = _card("Racing Louisville Vs Angel City FC",
                      competition="NWSL", start=LALIGA_KICKOFF)
        self.assertFalse(fixture_dedupe.same_fixture(left, right))
        self.assertFalse(same_real_fixture(left, right))

    def test_gender_is_read_from_the_competition_as_well_as_the_title(self):
        self.assertEqual(
            team_identity.fixture_gender(
                {"name": "Trent Rockets vs Oval", "competition": "The Hundred Women"}),
            "women")
        self.assertEqual(
            team_identity.fixture_gender(
                {"name": "Trent Rockets vs Oval", "competition": "The Hundred"}), "")

    def test_gender_reads_no_field_but_title_and_competition(self):
        # A logo path or a URL can carry a stray "w" that means nothing.
        noisy = {"name": "Brighton Vs Arsenal", "competition": "Premier League",
                 "logo": "https://cdn/x/w/brighton.png",
                 "url": "https://host/w/stream.m3u8", "category": "women"}
        self.assertEqual(team_identity.fixture_gender(noisy), "")


class ClubsThatMustStayApart(unittest.TestCase):
    """SAFETY CASES 5, 6, 7 - and the ones the alias file forbids by name."""

    PAIRS = (
        ("Manchester United", "Manchester City"),
        ("West Ham United", "West Bromwich Albion"),
        ("West Ham United", "West Brom"),
        ("Sheffield United", "Sheffield Wednesday"),
        ("Nottingham Forest", "Nottingham County"),
        ("Racing Santander", "Racing Louisville"),
        ("Brighton", "Brighton W"),
    )

    def test_each_pair_has_two_canonical_names(self):
        for left, right in self.PAIRS:
            self.assertNotEqual(team_identity.canonical_team(left),
                                team_identity.canonical_team(right),
                                f"{left} and {right} share a canonical name")

    def test_each_pair_is_two_fixtures_on_both_layers(self):
        for left, right in self.PAIRS:
            a = _card(f"{left} vs Chelsea", competition="English Premier League",
                      start=LALIGA_KICKOFF)
            b = _card(f"{right} vs Chelsea", competition="English Premier League",
                      start=LALIGA_KICKOFF)
            self.assertFalse(fixture_dedupe.same_fixture(a, b),
                             f"dedupe folded {left} into {right}")
            self.assertFalse(same_real_fixture(a, b),
                             f"merger folded {left} into {right}")


class KickoffAndCompetitionStillDecide(unittest.TestCase):
    """SAFETY CASES 2, 3, 4 - the alias widens the name and nothing else."""

    def test_same_teams_different_kickoff_are_two_fixtures(self):
        later = _card("Rayo Vallecano vs Racing Santander",
                      competition="LaLiga", start="2026-09-05T19:00:00+00:00")
        self.assertFalse(fixture_dedupe.same_fixture(REAL_RACING, later))
        self.assertFalse(same_real_fixture(REAL_RACING, later))

    def test_same_teams_reliably_different_competition_are_two_fixtures(self):
        cup = _card("Rayo Vallecano vs Racing Santander",
                    competition="Copa del Rey",
                    start="2026-09-05T16:34:00+00:00")
        self.assertFalse(same_real_fixture(REAL_RACING, cup))

    def test_a_future_rematch_is_a_different_fixture(self):
        rematch = _card("Rayo Vallecano vs Racing Santander",
                        competition="LaLiga", start="2026-11-21T15:00:00+00:00")
        self.assertFalse(fixture_dedupe.same_fixture(REAL_RACING, rematch))
        self.assertFalse(same_real_fixture(REAL_RACING, rematch))

    def test_a_womens_rematch_is_a_different_fixture(self):
        rematch = _card("Brighton W vs Arsenal W", competition="FA WSL",
                        start="2026-12-13T11:00:00+00:00")
        self.assertFalse(fixture_dedupe.same_fixture(BRIGHTON_NEUTRAL, rematch))
        self.assertFalse(same_real_fixture(BRIGHTON_NEUTRAL, rematch))


class GroupingIsUntouched(unittest.TestCase):
    """SAFETY CASES 9-12 - what a fold does with sources, channels and URLs."""

    A = _card(
        "Rayo Vallecano Vs Real Racing Club", competition="LaLiga",
        start=LALIGA_KICKOFF, source_ids=["sm-sports-data"],
        channels=[{"name": "beIN ARABIC", "normalized_name": "bein-arabic",
                   "streams": [{"id": "a1", "role": "primary",
                                "playback_id": "ctv_aaa"}]},
                  {"name": "D SPORTS +", "normalized_name": "d-sports",
                   "streams": [{"id": "a2", "role": "primary",
                                "playback_id": "ctv_bbb"},
                               {"id": "a3", "role": "backup",
                                "playback_id": "ctv_ccc"}]}])
    B = _card(
        "Rayo Vallecano vs Racing Santander", competition="LaLiga 2026/27",
        start=LALIGA_KICKOFF, source_ids=["srhady-tapmad-bd"],
        channels=[{"name": "Tapmad", "normalized_name": "tapmad",
                   "streams": [{"id": "b1", "role": "primary",
                                "playback_id": "ctv_ddd"}]}])

    def test_source_ids_survive_the_fold(self):
        kept, _report = fixture_dedupe.fold([dict(self.A), dict(self.B)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(set(kept[0]["source_ids"]),
                         {"sm-sports-data", "srhady-tapmad-bd"})

    def test_every_channel_button_survives_the_fold(self):
        # SAFETY 10/11: three broadcasters in, three buttons out, and the
        # channel that had a backup still has it.
        kept, _report = fixture_dedupe.fold([dict(self.A), dict(self.B)])
        names = [channel["name"] for channel in kept[0]["channels"]]
        self.assertEqual(sorted(names), ["D SPORTS +", "Tapmad", "beIN ARABIC"])
        d_sports = next(c for c in kept[0]["channels"] if c["name"] == "D SPORTS +")
        roles = [stream["role"] for stream in d_sports["streams"]]
        self.assertEqual(roles.count("primary"), 1)
        self.assertEqual(roles.count("backup"), 1)

    def test_channel_groups_module_is_not_consulted_here(self):
        # The fix touches which two strings are compared, never how channels
        # are built, ordered, labelled or merged.
        source = (ROOT / "scanner" / "fixture_dedupe.py").read_text(encoding="utf-8")
        source += (ROOT / "scanner" / "team_identity.py").read_text(encoding="utf-8")
        self.assertNotIn("channel_groups", source)
        self.assertNotIn("build_event_channels", source)

    def test_no_stream_or_playback_id_is_invented_or_duplicated(self):
        # SAFETY 12: the fold must not create a second copy of one URL.
        before = [stream["playback_id"]
                  for card in (self.A, self.B)
                  for channel in card["channels"]
                  for stream in channel["streams"]]
        kept, _report = fixture_dedupe.fold([dict(self.A), dict(self.B)])
        after = [stream["playback_id"]
                 for channel in (kept[0].get("channels") or [])
                 for stream in channel["streams"]]
        self.assertEqual(sorted(after), sorted(before))
        self.assertEqual(len(after), len(set(after)))

    def test_the_card_a_viewer_reads_keeps_the_feeds_spelling(self):
        # sides() is comparison only. correct_home_away rewrites a title from
        # _teams_of, which is deliberately the feed's own words.
        self.assertEqual(fixture_dedupe._teams_of(REAL_RACING),
                         ("Rayo Vallecano", "Real Racing Club"))
        self.assertEqual(fixture_dedupe._teams_of(BRIGHTON_W),
                         ("Brighton W", "Arsenal W"))

    def test_sides_does_not_mutate_the_card(self):
        for card in (REAL_RACING, BRIGHTON_W):
            before = json.dumps(card, sort_keys=True)
            fixture_dedupe.sides(card)
            participant_fold_key(card)
            self.assertEqual(json.dumps(card, sort_keys=True), before)


class TheAliasFileIsData(unittest.TestCase):
    """The table stays a table: no rule, no code, no unexplained entry."""

    def setUp(self):
        self.payload = json.loads(ALIASES.read_text(encoding="utf-8"))

    def test_every_alias_carries_its_evidence(self):
        for spelling, entry in self.payload["aliases"].items():
            self.assertTrue(str(entry.get("evidence") or "").strip(),
                            f"{spelling} has no evidence")

    def test_only_the_two_verified_w_spellings_are_scoped(self):
        scoped = {spelling for spelling, entry in self.payload["aliases"].items()
                  if entry.get("gender")}
        self.assertEqual(scoped, {"brighton w", "arsenal w"})
        for spelling in scoped:
            self.assertEqual(self.payload["aliases"][spelling]["gender"], "women")

    def test_no_entry_maps_a_bare_or_generic_word(self):
        for spelling, entry in self.payload["aliases"].items():
            self.assertGreaterEqual(len(spelling.split()), 2,
                                    f"{spelling} is a single bare word")
            self.assertNotEqual(spelling, entry["canonical"])

    def test_the_modules_contain_no_similarity_matching(self):
        for name in ("team_identity.py", "fixture_dedupe.py"):
            source = (ROOT / "scanner" / name).read_text(encoding="utf-8")
            for forbidden in ("difflib", "SequenceMatcher", "levenshtein",
                              "fuzz", "ratio("):
                self.assertNotIn(forbidden, source, f"{forbidden} in {name}")

    def test_no_case_is_hard_coded_in_a_scanner_module(self):
        for name in ("team_identity.py", "fixture_dedupe.py", "merger.py"):
            source = (ROOT / "scanner" / name).read_text(encoding="utf-8").casefold()
            for needle in ("racing santander", "real racing club", "brighton w",
                           "arsenal w"):
                self.assertNotIn(needle, source,
                                 f"{needle!r} is hard-coded in {name}")

    def test_a_missing_alias_file_changes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "not-here.json"
            self.assertEqual(team_identity.canonical_team("Real Racing Club", "",
                                                          missing),
                             "real racing club")
            self.assertEqual(team_identity.canonical_team("Brighton W", "women",
                                                          missing),
                             "brighton w")

    def test_a_malformed_entry_is_stepped_over(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aliases.json"
            path.write_text(json.dumps({"aliases": {
                "": {"canonical": "x"},
                "same": {"canonical": "same"},
                "good spelling": {"canonical": "good", "gender": "women"},
            }}), encoding="utf-8")
            team_identity.clear_cache()
            self.assertEqual(team_identity.load_aliases(path), {})
            self.assertEqual(team_identity.load_scoped_aliases(path),
                             {("women", "good spelling"): "good"})
        team_identity.clear_cache()


if __name__ == "__main__":
    unittest.main()
