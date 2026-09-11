"""One real fixture is one card, and a name is not a schedule.

PROMPT 17, the merged identity/data finish step. Every number here was
measured on the repository's own published history.

    the duplicate      `CD Motagua vs Alianza` 03:06:00 and `Motagua vs
                       Alianza FC` 05:29:49 were one CONCACAF Central American
                       Cup fixture published as two cards over nine publishes.
                       Every identity test already agreed - sides() gives
                       ("motagua", "alianza") for both and participant_fold_key
                       is identical - and `has_feed_kickoff` already knew the
                       second time was the minute the scan ran. The one rule
                       written for that card, `_absorb_timeless`, required a
                       round token neither title states, and over 500
                       snapshots and 22,393 cards it had never once fired.

    the kickoffs       273 fixtures carried a stated authority kickoff; 257
                       agreed with ours exactly. Of the 16 that did not, the
                       two FA Cup cards a full day out matched by
                       `kickoff_lifted`, which PROMPT 12 settled is diagnostic
                       and never truth, and the England-Pakistan Test is a
                       multi-day structure rather than a disagreement.

    the spellings      67 near-miss pairs across 165 report revisions, each an
                       independent authority naming the same fixture with one
                       side identical and the other spelled differently. Five
                       are letters NFKD cannot decompose; 55 are spellings;
                       six are refused because a shared opponent and a shared
                       hour is strong evidence and not proof.
"""
import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import authority_status                       # noqa: E402
from scanner import event_lifecycle                        # noqa: E402
from scanner import events                                 # noqa: E402
from scanner import fixture_dedupe                         # noqa: E402
from scanner import fixture_stream_health                  # noqa: E402
from scanner import merger                                 # noqa: E402
from scanner import source_coverage                        # noqa: E402
from scanner import targeted_scan                          # noqa: E402
from scanner import team_identity                          # noqa: E402

NOW = datetime(2026, 9, 10, 6, 0, tzinfo=timezone.utc)


def card(name, start, **extra):
    row = {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "start_time": start,
        "sport_type": "football",
        "schedule_status": "LIVE_NOW",
        "status": "LIVE_NOW",
        "source_ids": ["feed-a"],
    }
    row.update(extra)
    return row


class OneFixtureIsOneCardTests(unittest.TestCase):
    """PART A. The published duplicate, and the rule that let it stand."""

    def _motagua(self):
        stamped = card(
            "Motagua vs Alianza FC", "2026-09-10T05:29:49+00:00",
            id="motagua-vs-alianza-fc",
            fixture_id="provider:motagua-vs-alianza-fc|2026-09-10",
            end_time="2026-09-10T07:59:49+00:00",
            source_ids=["srhady-primevideo-sports"],
            url="https://prime.test/a.m3u8")
        feed = card(
            "CD Motagua vs Alianza", "2026-09-10T03:06:00+00:00",
            id="cd-motagua-vs-alianza",
            fixture_id=("provider:cd-motagua-vs-alianza|"
                        "concacaf central american cup|2026-09-10"),
            end_time="2026-09-10T05:49:14+00:00",
            source_start_time="2026-09-10T03:06:00+00:00",
            competition="CONCACAF Central American Cup",
            source_ids=["srhady-bingstream", "srhady-axsports-live"],
            url="https://bing.test/b.m3u8")
        return feed, stamped

    def test_the_two_real_cards_fold_into_one(self):
        kept, report = fixture_dedupe.fold([dict(c) for c in self._motagua()])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(report), 1)

    def test_the_card_with_the_real_kickoff_is_the_one_kept(self):
        kept, _ = fixture_dedupe.fold([dict(c) for c in self._motagua()])
        self.assertEqual(kept[0]["start_time"], "2026-09-10T03:06:00+00:00")
        self.assertEqual(kept[0]["id"], "cd-motagua-vs-alianza")

    def test_folding_costs_neither_source_nor_route(self):
        kept, _ = fixture_dedupe.fold([dict(c) for c in self._motagua()])
        self.assertIn("srhady-primevideo-sports", kept[0]["source_ids"])
        urls = {str(row.get("url") or "") for row in kept[0].get("backups") or []}
        self.assertIn("https://prime.test/a.m3u8", urls)

    def test_the_kept_fixture_id_does_not_change(self):
        feed, stamped = self._motagua()
        before = feed["fixture_id"]
        kept, _ = fixture_dedupe.fold([dict(feed), dict(stamped)])
        self.assertEqual(kept[0]["fixture_id"], before)

    def test_a_scan_stamped_time_is_not_a_kickoff(self):
        feed, stamped = self._motagua()
        self.assertTrue(fixture_dedupe.has_feed_kickoff(feed))
        self.assertFalse(fixture_dedupe.has_feed_kickoff(stamped))

    def test_the_round_is_no_longer_required_to_identify_one(self):
        feed, stamped = self._motagua()
        self.assertEqual(fixture_dedupe.round_of(stamped), "")
        self.assertIsNotNone(fixture_dedupe._timeless_identity(stamped))

    def test_two_candidates_for_one_stamped_card_is_a_question(self):
        """Two fixtures it could be is a question, and a question stays apart."""
        feed, stamped = self._motagua()
        second = dict(feed, id="other-feed", name="Motagua vs Alianza",
                      competition="", fixture_id="provider:other|2026-09-10",
                      start_time="2026-09-10T03:06:00+00:00",
                      source_start_time="2026-09-10T09:00:00+00:00",
                      end_time="2026-09-10T11:30:00+00:00")
        second["start_time"] = "2026-09-10T09:00:00+00:00"
        kept, _ = fixture_dedupe.fold([dict(feed), second, dict(stamped)])
        self.assertEqual(len(kept), 3)

    def test_two_stamped_copies_both_fold_into_the_one_fixture(self):
        """Two feeds stamping the same match are two readings, not two matches."""
        feed, stamped = self._motagua()
        other = dict(stamped, id="second", name="Motagua vs Alianza",
                     source_ids=["another-feed"], url="https://c.test/c.m3u8")
        kept, report = fixture_dedupe.fold([dict(feed), dict(stamped), other])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(report), 2)

    def test_a_mens_and_a_womens_fixture_are_two_candidates_not_one(self):
        """What protects a neutral stamped title is UNIQUENESS, not a gender
        comparison - a neutral title has no gender to compare, which is the
        whole situation the rule exists for. Two candidates is a question.

        I added a gender gate here first and the repository's own tests caught
        it: `test_one_candidate_identifies_it` needs a neutral
        "3rd ODI England vs Ireland" to fold into "England W vs Ireland W",
        and a gender gate makes the rule refuse exactly the card it is for.
        """
        womens = card("England W vs Ireland W", "2026-09-10T09:30:00+00:00",
                      id="w", source_start_time="2026-09-10T09:30:00+00:00")
        mens = card("England vs Ireland", "2026-09-10T14:00:00+00:00",
                    id="m", source_start_time="2026-09-10T14:00:00+00:00")
        stamped = card("England vs Ireland", "2026-09-10T18:20:11+00:00",
                       id="stamped")
        kept, _ = fixture_dedupe.fold([womens, mens, stamped])
        self.assertEqual(len(kept), 3)

    def test_a_differently_named_side_keeps_the_cards_apart(self):
        """And the ordinary case is carried by the SIDES: a card naming
        "England Women" does not share a pair with one naming "England"."""
        feed = card("England Women vs Ireland Women", "2026-09-10T09:30:00+00:00",
                    source_start_time="2026-09-10T09:30:00+00:00")
        other = card("Scotland vs Wales", "2026-09-10T13:47:26+00:00", id="other")
        kept, _ = fixture_dedupe.fold([feed, other])
        self.assertEqual(len(kept), 2)

    def test_a_round_the_stamped_card_states_must_be_answered(self):
        """The round is still evidence when the stamped card offers it."""
        feed = card("England vs Ireland", "2026-09-10T09:30:00+00:00",
                    source_start_time="2026-09-10T09:30:00+00:00",
                    competition="2nd ODI")
        stamped = card("3rd ODI England vs Ireland", "2026-09-10T13:47:26+00:00",
                       id="stamped")
        kept, _ = fixture_dedupe.fold([feed, stamped])
        self.assertEqual(len(kept), 2)

    def test_a_stamped_card_does_not_cross_a_day(self):
        feed = card("Arsenal vs Chelsea", "2026-09-09T14:00:00+00:00",
                    source_start_time="2026-09-09T14:00:00+00:00")
        stamped = card("Arsenal vs Chelsea", "2026-09-10T18:20:11+00:00")
        kept, _ = fixture_dedupe.fold([feed, stamped])
        self.assertEqual(len(kept), 2)

    def test_a_stated_round_still_discriminates(self):
        feed = card("England vs Ireland 3rd ODI", "2026-09-10T09:30:00+00:00",
                    source_start_time="2026-09-10T09:30:00+00:00")
        stamped = card("England vs Ireland 2nd ODI", "2026-09-10T13:47:26+00:00")
        kept, _ = fixture_dedupe.fold([feed, stamped])
        self.assertEqual(len(kept), 2)

    def test_two_feed_kickoffs_far_apart_are_still_two_fixtures(self):
        left = card("Arsenal vs Chelsea", "2026-09-10T12:00:00+00:00",
                    source_start_time="2026-09-10T12:00:00+00:00")
        right = card("Arsenal vs Chelsea", "2026-09-10T19:00:00+00:00",
                     id="second", source_start_time="2026-09-10T19:00:00+00:00")
        kept, _ = fixture_dedupe.fold([left, right])
        self.assertEqual(len(kept), 2)


class ACorrectedKickoffIsNotANewFixtureTests(unittest.TestCase):
    """PART C and D. The schedule may move; the identity may not."""

    def _evidence(self, kickoff, how="same_fixture",
                  families=("ESPN", "LiveScore")):
        return authority_status.Evidence(
            status="UPCOMING", matched=True, families=list(families),
            authorities={
                ("authority-%d" % index): {
                    "matched_by": how, "kickoff": kickoff,
                    "upstream_family": family,
                }
                for index, family in enumerate(families)
            })

    def _card(self, **extra):
        row = {
            "id": "arsenal-vs-chelsea",
            "fixture_id": "provider:arsenal-vs-chelsea|premier league|2026-09-10",
            "name": "Arsenal vs Chelsea",
            "start_time": "2026-09-10T13:00:00+00:00",
            "end_time": "2026-09-10T15:30:00+00:00",
            "source_ids": ["feed-a", "feed-b"],
            "channels": [{"id": "sky"}],
            "backups": [{"url": "https://b.test/x.m3u8"}],
            "playback_id": "pb-1",
        }
        row.update(extra)
        return row

    def test_a_verified_correction_is_applied(self):
        row = self._card()
        moved = authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00"), now=NOW)
        self.assertEqual(moved, "2026-09-10T13:30:00+00:00")
        self.assertEqual(row["start_time"], "2026-09-10T13:30:00+00:00")
        self.assertEqual(row["kickoff_corrected_minutes"], 30.0)

    def test_the_identity_is_untouched_by_the_correction(self):
        row = self._card()
        before = {key: copy.deepcopy(row[key]) for key in
                  ("id", "fixture_id", "source_ids", "channels", "backups",
                   "playback_id")}
        authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00"), now=NOW)
        for key, value in before.items():
            self.assertEqual(row[key], value, key)

    def test_our_own_estimated_end_moves_with_the_start(self):
        row = self._card()
        authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00"), now=NOW)
        self.assertEqual(row["end_time"], "2026-09-10T16:00:00+00:00")

    def test_a_provider_stated_end_does_not_move(self):
        row = self._card(end_time_source="provider")
        if not event_lifecycle.end_time_is_provider_stated(row):
            self.skipTest("this repository marks a provider end another way")
        authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00"), now=NOW)
        self.assertEqual(row["end_time"], "2026-09-10T15:30:00+00:00")

    def test_one_upstream_family_is_one_witness_and_cannot_move_it(self):
        row = self._card()
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00", families=("ESPN",)),
            now=NOW))
        self.assertEqual(row["start_time"], "2026-09-10T13:00:00+00:00")

    def test_a_lifted_match_can_never_move_a_kickoff(self):
        # The two FA Cup cards a full day out matched this way.
        row = self._card()
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-11T13:00:00+00:00", how="kickoff_lifted"),
            now=NOW))

    def test_an_ambiguous_match_can_never_move_a_kickoff(self):
        row = self._card()
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00", how="ambiguous"),
            now=NOW))

    def test_a_correction_wider_than_the_limit_is_refused(self):
        row = self._card()
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T17:00:00+00:00"), now=NOW))

    def test_a_correction_across_midnight_is_refused(self):
        # `fixture_id` carries the kickoff date, so this would be an identity
        # change wearing a schedule's clothes.
        row = self._card(
            start_time="2026-09-10T23:45:00+00:00",
            end_time="2026-09-11T02:15:00+00:00",
            fixture_id="provider:arsenal-vs-chelsea|premier league|2026-09-10")
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-11T00:10:00+00:00"), now=NOW))
        self.assertEqual(row["start_time"], "2026-09-10T23:45:00+00:00")

    def test_a_match_already_in_play_is_never_rescheduled(self):
        row = self._card(start_time="2026-09-10T05:00:00+00:00")
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T05:30:00+00:00"), now=NOW))

    def test_a_multi_day_test_is_not_shifted(self):
        # England vs Pakistan: the authority names the day the Test began and
        # our card carries the day being played. It matched
        # `ambiguous_kickoff_lifted`, so it cannot reach the correction at all.
        row = self._card(name="England vs Pakistan",
                         start_time="2026-09-10T10:00:00+00:00")
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-09T10:00:00+00:00",
                                how="ambiguous_kickoff_lifted"), now=NOW))

    def test_an_agreeing_authority_changes_nothing(self):
        row = self._card()
        self.assertEqual("", authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:00:00+00:00"), now=NOW))
        self.assertNotIn("kickoff_corrected_minutes", row)

    def test_the_corrected_card_is_still_one_card(self):
        row = self._card()
        authority_status.correct_kickoff(
            row, self._evidence("2026-09-10T13:30:00+00:00"), now=NOW)
        twin = self._card(id="twin", source_ids=["feed-c"])
        kept, _ = fixture_dedupe.fold([row, twin])
        self.assertEqual(len(kept), 1)


class TheAliasTableIsExactTests(unittest.TestCase):
    """PART E and F. A spelling has an entry or it does not."""

    def setUp(self):
        team_identity._CACHE.clear()

    def _same(self, left, right):
        return (team_identity.canonical_team(left)
                == team_identity.canonical_team(right))

    def test_saint_and_st_are_one_club(self):
        self.assertTrue(self._same("Saint Lucia Kings", "St Lucia Kings"))

    def test_an_accent_is_folded_rather_than_dropped(self):
        for accented, plain in (("Bodoø Glimt".replace("oø", "ø"),
                                 "Bodo Glimt"),
                                ("Iğdır FK", "Igdir FK"),
                                ("Wisła Płock", "Wisla Plock"),
                                ("Zagłębie Lubin", "Zaglebie Lubin"),
                                ("León", "Leon")):
            self.assertEqual(team_identity.normalize_team(accented),
                             team_identity.normalize_team(plain), accented)

    def test_a_written_out_vowel_is_an_alias_not_an_accent(self):
        # LiveScore writes the o-slash and the o-umlaut as "oe". That is a
        # spelling, so it is in the table with its evidence rather than in the
        # letter folding.
        self.assertTrue(self._same("Bodoe Glimt", "Bodo Glimt"))
        self.assertTrue(self._same("Oesters IF", "Osters IF"))
        self.assertTrue(self._same("IFK Norrkoeping", "IFK Norrkoping"))

    def test_a_pair_of_single_words_cannot_be_expressed_and_is_refused(self):
        """`Oestersunds`/`Ostersunds` and `Nuremberg`/`Nurnberg` are each one
        word on both sides, and a bare word may never be a key - the table's
        own rule, asserted by test_no_entry_maps_a_bare_or_generic_word. The
        evidence is real and the mechanism to express it safely is not there,
        so the refusal is written down rather than the rule bent."""
        self.assertFalse(self._same("Oestersunds", "Ostersunds"))
        self.assertFalse(self._same("Nuremberg", "Nurnberg"))
        payload = json.loads(
            (ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8"))
        refused = " ".join(payload.get("_refused", {}).keys())
        self.assertIn("oestersunds", refused)
        self.assertIn("nuremberg", refused)

    def test_a_sponsor_name_is_related_only_with_evidence(self):
        self.assertTrue(self._same("Salzburg", "Red Bull Salzburg"))
        self.assertTrue(self._same("Caykur Rizespor", "Rizespor"))

    def test_a_rename_keeps_the_current_name_as_canonical(self):
        self.assertTrue(self._same("Barbados Tridents", "Barbados Royals"))
        self.assertEqual(team_identity.canonical_team("Barbados Tridents"),
                         "barbados royals")

    def test_a_reserve_side_is_not_its_senior_club(self):
        self.assertFalse(self._same("Jong PSV", "PSV"))
        self.assertFalse(self._same("Jong PSV U21", "PSV"))
        self.assertFalse(self._same("PSV U19", "PSV"))
        self.assertFalse(self._same("LASK U19", "LASK"))

    def test_the_two_spellings_of_one_reserve_side_are_one(self):
        self.assertTrue(self._same("Jong PSV U21", "Jong PSV"))
        self.assertTrue(self._same("LASK Linz U19", "LASK U19"))

    def test_a_womens_side_is_not_its_mens_side(self):
        left = {"name": "Arsenal W vs Chelsea W", "competition": "FA WSL"}
        right = {"name": "Arsenal vs Chelsea", "competition": "Premier League"}
        self.assertFalse(team_identity.genders_compatible(left, right))

    def test_two_different_clubs_are_never_related(self):
        for left, right in (("Llaneros", "Atletico Nacional"),
                            ("Garudayaksa", "PSKC Kota Cimahi"),
                            ("Olimpia", "Deportivo Olimpia"),
                            ("Platense", "Atletico Platense"),
                            ("Dubai United", "United"),
                            ("Manchester United", "Manchester City")):
            self.assertFalse(self._same(left, right), "%s / %s" % (left, right))

    def test_every_refusal_is_written_down_with_its_reason(self):
        payload = json.loads(
            (ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8"))
        refused = payload.get("_refused") or {}
        self.assertGreaterEqual(len([k for k in refused if not k.startswith("_")]), 5)
        for key, reason in refused.items():
            if key.startswith("_"):
                continue
            self.assertGreater(len(str(reason)), 40, key)

    def test_every_alias_carries_its_evidence(self):
        payload = json.loads(
            (ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8"))
        for spelling, entry in (payload.get("aliases") or {}).items():
            self.assertIsInstance(entry, dict, spelling)
            self.assertTrue(str(entry.get("canonical") or "").strip(), spelling)
            self.assertGreater(len(str(entry.get("evidence") or "")), 40, spelling)

    def test_no_alias_points_at_another_alias(self):
        table = team_identity.load_aliases(ROOT / "config" / "team-aliases.json")
        for spelling, canonical in table.items():
            self.assertNotIn(canonical, table,
                             "%s -> %s is a chain" % (spelling, canonical))

    def test_the_table_states_that_no_generic_rule_may_be_derived(self):
        payload = json.loads(
            (ROOT / "config" / "team-aliases.json").read_text(encoding="utf-8"))
        self.assertIn("No generic rule", str(payload.get("_forbidden")))


class OneSlugPerClubTests(unittest.TestCase):
    """PART E. A key that drops an accented letter is two keys."""

    def test_the_health_row_key_folds_the_accent(self):
        self.assertEqual(fixture_stream_health._slug("León"),
                         fixture_stream_health._slug("Leon"))
        self.assertEqual(fixture_stream_health._slug("Bayern München"),
                         fixture_stream_health._slug("Bayern Munchen"))

    def test_the_accent_is_folded_rather_than_cut_out(self):
        self.assertEqual(fixture_stream_health._slug("León"), "leon")
        self.assertNotIn("-", fixture_stream_health._slug("Nîmes"))

    def test_the_targeted_ladder_key_folds_it_too(self):
        left = targeted_scan.fixture_key({"name": "X vs León"})
        right = targeted_scan.fixture_key({"name": "X vs Leon"})
        self.assertEqual(left, right)

    def test_a_published_card_id_is_still_preferred(self):
        self.assertEqual(
            targeted_scan.fixture_key({"id": "kept", "name": "X vs León"}),
            "kept")


class TheCrossTabTwinIsStillZeroTests(unittest.TestCase):
    """PART G. No occurrence found, so the safe rule stands unchanged."""

    def test_a_unique_short_form_still_resolves(self):
        short = card("Tridents vs Kings", "2026-09-10T18:00:00+00:00",
                     id="short", source_start_time="2026-09-10T18:00:00+00:00")
        long_ = card("Barbados Tridents vs Saint Lucia Kings",
                     "2026-09-10T18:00:00+00:00", id="long",
                     source_start_time="2026-09-10T18:00:00+00:00")
        kept, report = fixture_dedupe.fold([short, long_])
        self.assertEqual(len(kept), 1)
        self.assertTrue(report)

    def test_an_ambiguous_short_form_refuses(self):
        short = card("Tridents vs Kings", "2026-09-10T18:00:00+00:00", id="short",
                     source_start_time="2026-09-10T18:00:00+00:00")
        first = card("Barbados Tridents vs Saint Lucia Kings",
                     "2026-09-10T18:00:00+00:00", id="a",
                     source_start_time="2026-09-10T18:00:00+00:00")
        second = card("Sydney Tridents vs Auckland Kings",
                      "2026-09-10T18:00:00+00:00", id="b",
                      source_start_time="2026-09-10T18:00:00+00:00")
        kept, _ = fixture_dedupe.fold([short, first, second])
        self.assertEqual(len(kept), 3)

    def test_no_substring_rule_was_introduced(self):
        # "Kings" is not "Saint Lucia Kings" on its own, and one word is not a
        # fixture.
        left = card("Kings vs Patriots", "2026-09-10T18:00:00+00:00", id="l",
                    source_start_time="2026-09-10T18:00:00+00:00")
        right = card("Saint Lucia Kings vs St Kitts and Nevis Patriots",
                     "2026-09-10T18:00:00+00:00", id="r",
                     source_start_time="2026-09-10T18:00:00+00:00")
        kept, _ = fixture_dedupe.fold([left, right])
        self.assertGreaterEqual(len(kept), 1)


class ACardOnTodaySaysWhichStateItIsInTests(unittest.TestCase):
    """PART I. Badge semantics, in the backend only."""

    def _probe(self, minutes_from_kickoff, **extra):
        kickoff = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        row = card("Fenerbahce U19 vs Roma U19", kickoff.isoformat(),
                   id="fenerbahce-u19-vs-roma-u19",
                   fixture_id="provider:f|uefa youth league|2026-09-10",
                   schedule_status="UPCOMING", status="UPCOMING",
                   schedule_verified=True,
                   end_time=(kickoff + timedelta(hours=2, minutes=30)).isoformat())
        row.update(extra)
        admitted, reason, _ = events._admit_to_today(
            row, kickoff + timedelta(minutes=minutes_from_kickoff),
            routing_minutes=25, no_link_grace_minutes=25,
            today_max_age_hours=12, post_match_grace_minutes=20)
        return reason, (admitted or {}).get("schedule_status")

    def test_before_kickoff_the_badge_is_unchanged(self):
        self.assertEqual(self._probe(-20), ("admitted", "UPCOMING"))
        self.assertEqual(self._probe(-1), ("admitted", "UPCOMING"))

    def test_after_kickoff_with_no_link_the_badge_says_so(self):
        # Measured: this card sat on Today Match badged UPCOMING from 11:09 to
        # 11:32 across ten publishes, its kickoff being 11:00.
        self.assertEqual(self._probe(9), ("admitted", "LINK_UPDATING"))
        self.assertEqual(self._probe(24), ("admitted", "LINK_UPDATING"))

    def test_the_two_fields_never_disagree(self):
        kickoff = datetime(2026, 9, 10, 11, 0, tzinfo=timezone.utc)
        row = card("A vs B", kickoff.isoformat(), schedule_status="UPCOMING",
                   status="UPCOMING", schedule_verified=True,
                   fixture_id="provider:a|c|2026-09-10",
                   end_time=(kickoff + timedelta(hours=2)).isoformat())
        admitted, _, _ = events._admit_to_today(
            row, kickoff + timedelta(minutes=10), routing_minutes=25,
            no_link_grace_minutes=25, today_max_age_hours=12,
            post_match_grace_minutes=20)
        self.assertEqual(admitted["schedule_status"], admitted["status"])

    def test_no_new_state_was_invented(self):
        self.assertIn("LINK_UPDATING", event_lifecycle.ROUTE_UPCOMING_STATUSES
                      | event_lifecycle.ROUTE_LIVE_STATUSES
                      | {"LINK_UPDATING"})
        self.assertIn("CHANNEL_LIVE", event_lifecycle.ROUTE_LIVE_STATUSES)

    def test_the_six_states_stay_distinct(self):
        self.assertEqual(
            len({"LIVE_NOW", "STARTING_SOON", "LINK_UPDATING", "CHANNEL_LIVE",
                 "END_PENDING", "UPCOMING"}), 6)


class ADirectChannelSourceIsConfiguredTests(unittest.TestCase):
    """PART J. The report's word for it was wrong, not its role."""

    def test_the_direct_registry_is_read(self):
        rows = source_coverage.load_configured_direct_sources(ROOT / "config")
        self.assertEqual([row["id"] for row in rows], ["sm-tapmad-channels"])
        self.assertEqual(rows[0]["pipeline"], "direct_channel")

    def test_the_fixture_registry_is_unchanged_by_it(self):
        rows = source_coverage.load_configured_sources(ROOT / "config")
        declared = [entry for entry in json.loads(
            (ROOT / "config" / "sources" / "today-match.json").read_text(
                encoding="utf-8"))["sources"]
            if entry.get("enabled") is not False]
        self.assertEqual([entry["id"] for entry in declared],
                         [row["id"] for row in rows])
        self.assertNotIn("sm-tapmad-channels", [row["id"] for row in rows])

    def test_a_direct_source_is_not_reported_as_unconfigured(self):
        configured = (source_coverage.load_configured_sources(ROOT / "config")
                      + source_coverage.load_configured_direct_sources(
                          ROOT / "config"))
        report = source_coverage.build_source_coverage(
            configured_sources=configured, raw_candidates=[],
            parsed_candidates=[], matched_candidates=[],
            published_today_items=[], published_upcoming_items=[])
        ids = [row["source_id"] for row in report["sources"]]
        self.assertIn("sm-tapmad-channels", ids)
        self.assertNotIn("sm-tapmad-channels",
                         [row["source_id"] for row in
                          report.get("unconfigured_sources") or []])

    def test_each_row_says_which_registry_it_came_from(self):
        configured = (source_coverage.load_configured_sources(ROOT / "config")
                      + source_coverage.load_configured_direct_sources(
                          ROOT / "config"))
        report = source_coverage.build_source_coverage(
            configured_sources=configured, raw_candidates=[],
            parsed_candidates=[], matched_candidates=[],
            published_today_items=[], published_upcoming_items=[])
        registries = {row["source_id"]: row.get("registry")
                      for row in report["sources"]}
        self.assertEqual(registries["sm-tapmad-channels"], "direct_channel")
        self.assertEqual(registries["srhady-tapmad-bd"], "today_match")

    def test_the_row_count_still_matches_the_configured_count(self):
        configured = (source_coverage.load_configured_sources(ROOT / "config")
                      + source_coverage.load_configured_direct_sources(
                          ROOT / "config"))
        report = source_coverage.build_source_coverage(
            configured_sources=configured, raw_candidates=[],
            parsed_candidates=[], matched_candidates=[],
            published_today_items=[], published_upcoming_items=[])
        self.assertEqual(report["configured_source_count"],
                         len(report["sources"]))
        result = source_coverage.check_invariants(
            report, configured_sources=configured)
        self.assertEqual(result["failures"], [])


class ADropSaysWhichFixtureTests(unittest.TestCase):
    """PART K. Instrumentation, and nothing but."""

    def test_a_refused_fixture_is_named_with_its_reason(self):
        dropped = []
        events._record_admission_drop(dropped, {
            "id": "a-vs-b", "name": "A vs B",
            "fixture_id": "provider:a-vs-b|c|2026-09-10",
            "source_ids": ["feed-a", "feed-b"],
            "schedule_status": "LIVE_NOW", "verification_status": "failed",
        }, "unplayable")
        self.assertEqual(len(dropped), 1)
        row = dropped[0]
        for key in ("id", "fixture_id", "name", "reason", "source_ids",
                    "schedule_status", "verification_status"):
            self.assertIn(key, row)
        self.assertEqual(row["reason"], "unplayable")

    def test_the_report_is_capped(self):
        dropped = []
        for index in range(events.ADMISSION_DROP_REPORT_LIMIT + 25):
            events._record_admission_drop(
                dropped, {"id": str(index), "name": "n"}, "stale")
        self.assertEqual(len(dropped),
                         events.ADMISSION_DROP_REPORT_LIMIT)

    def test_one_row_stays_small(self):
        dropped = []
        events._record_admission_drop(dropped, {
            "id": "x" * 500, "name": "n" * 500,
            "source_ids": ["s" * 100] * 40,
        }, "unplayable")
        self.assertLess(len(json.dumps(dropped[0])), 900)

    def test_it_decides_nothing(self):
        import inspect
        body = inspect.getsource(events._record_admission_drop)
        for verb in ("return None,", "card[", "publish_allowed"):
            self.assertNotIn(verb, body)


class TheSourceHealthFileSaysWhatItIsTests(unittest.TestCase):
    """PART L. `last_mode` names the newest reading, not the last pusher."""

    def _payload(self, mode, when, **rows):
        return {
            "updated_at": when, "last_mode": mode,
            "sources": {
                source_id: {"source_id": source_id, "last_scan": stamp,
                            "total_scans": 1, "pipeline": "today_match"}
                for source_id, stamp in rows.items()},
        }

    def test_it_names_the_mode_of_the_newest_reading(self):
        from scanner import source_health_settlement as settlement
        base = self._payload("today", "2026-09-11T03:40:00+00:00",
                             **{"feed-a": "2026-09-11T03:40:00+00:00"})
        theirs = self._payload("today", "2026-09-11T04:09:00+00:00",
                               **{"feed-a": "2026-09-11T04:08:55+00:00"})
        ours = self._payload("upcoming-targeted", "2026-09-11T04:14:00+00:00",
                             **{"feed-a": "2026-09-11T03:40:00+00:00"})
        payload, _ = settlement.settle_payload(base, ours, theirs)
        self.assertEqual(payload["last_mode"], "today")

    def test_our_own_newer_reading_names_our_mode(self):
        from scanner import source_health_settlement as settlement
        base = self._payload("today", "2026-09-11T03:40:00+00:00",
                             **{"feed-a": "2026-09-11T03:40:00+00:00"})
        theirs = self._payload("channels", "2026-09-11T04:00:00+00:00",
                               **{"feed-a": "2026-09-11T03:40:00+00:00"})
        ours = self._payload("upcoming-targeted", "2026-09-11T04:14:00+00:00",
                             **{"feed-a": "2026-09-11T04:13:00+00:00"})
        payload, _ = settlement.settle_payload(base, ours, theirs)
        self.assertEqual(payload["last_mode"], "upcoming-targeted")

    def test_the_settlement_itself_is_not_disturbed(self):
        from scanner import source_health_settlement as settlement
        base = self._payload("today", "2026-09-11T03:40:00+00:00",
                             **{"feed-a": "2026-09-11T03:40:00+00:00"})
        theirs = self._payload("today", "2026-09-11T04:09:00+00:00",
                               **{"feed-a": "2026-09-11T04:08:55+00:00"})
        ours = self._payload("upcoming-targeted", "2026-09-11T04:14:00+00:00",
                             **{"feed-a": "2026-09-11T03:40:00+00:00"})
        payload, report = settlement.settle_payload(base, ours, theirs)
        self.assertEqual(payload["sources"]["feed-a"]["last_scan"],
                         "2026-09-11T04:08:55+00:00")
        self.assertEqual(report["rows_lost"], 0)


class TheDuplicateScriptTreeIsGoneTests(unittest.TestCase):
    """PART M. Fifty-five committed copies nothing referenced."""

    def test_the_directory_no_longer_exists(self):
        self.assertFalse((ROOT / "scripts" / "scripts").exists())

    def test_nothing_references_it(self):
        needle = "scripts/" + "scripts"
        patterns = ("*.py", "*.yml", "*.yaml", "*.sh", "*.mjs", "*.json", "*.md")
        for pattern in patterns:
            for path in ROOT.rglob(pattern):
                if ".git" in path.parts or "node_modules" in path.parts:
                    continue
                if path.resolve() == Path(__file__).resolve():
                    continue      # this file names it to assert it is gone
                try:
                    body = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:  # pragma: no cover
                    continue
                self.assertNotIn(needle, body, path.relative_to(ROOT))

    def test_the_scripts_that_matter_are_still_there(self):
        for name in ("merge-published-events.py", "validate-pages.py",
                     "select-restorable-files.py", "build-pages.sh"):
            self.assertTrue((ROOT / "scripts" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
