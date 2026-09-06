"""Football falls back on how long football lasts, not on four hours.

`kickoff + events.provider_event_hours` gives a T20, a Test day and a
Champions League tie the same 240 minutes. FINAL_2 ধাপ ৩ costs that out: a
football match really finishing around T+105 was being retired at T+330.

Nothing new is invented here. `event_lifecycle.SPORT_DURATION_MINUTES`
already reads `"football": 150` - it was simply unreachable, because
`estimated_end` only consults it for a card with no end_time and every card
has one. `sport_filter.classify` already decides what sport a card is.
This step joins the two.

Football only. The same table carries `"cricket": 8 * 60`, written for a
Test day, and nearly every cricket fixture on this site is a T20 or T10 -
so switching cricket on here would swap one wrong number for another.
Reading its format is PROMPT 17 and applying a duration is PROMPT 18.
"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import events as ev                        # noqa: E402
from scanner import schedule_resolver as sr             # noqa: E402
from scanner import sport_filter                        # noqa: E402
from scanner.event_lifecycle import (                   # noqa: E402
    SPORT_DURATION_MINUTES, verified_end_passed,
)
from scanner.live_protection import protect_live_events  # noqa: E402

UTC = timezone.utc
KICKOFF = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
UTC_ZONE = sr._zone("UTC", "UTC", "+00:00")


def candidate(**extra):
    return dict({
        "name": "Milan vs Juventus",
        "competition": "Italian Serie A",
        "group_title": "football",
        "source_id": "some-feed",
        "status": "UPCOMING",
        "url": "https://a.example/x.m3u8",
    }, **extra)


def resolve(item, now=KICKOFF):
    return sr._provider_fixture_item(
        item, KICKOFF, now, 4, source_timezone=UTC_ZONE
    )


def span(card):
    return round((datetime.fromisoformat(card["end_time"])
                  - datetime.fromisoformat(card["start_time"])).total_seconds() / 60)


class TheNumberComesFromTheExistingTable(unittest.TestCase):
    def test_no_second_duration_table_was_created(self):
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8")
        self.assertIn("from scanner.event_lifecycle import (", source)
        self.assertIn("    SPORT_DURATION_MINUTES,", source)
        # No table of its own, and no number of its own: the value has to be
        # looked up, so moving it in one place moves it everywhere.
        self.assertNotIn("SPORT_DURATION_MINUTES = {", source)
        self.assertNotIn("football_duration", source)
        self.assertIn('SPORT_DURATION_MINUTES.get("football")', source)

    def test_it_is_the_hundred_and_fifty_that_was_already_there(self):
        self.assertEqual(150, SPORT_DURATION_MINUTES["football"])
        self.assertEqual(150, sr._sport_end_minutes(candidate()))

    def test_the_classification_comes_from_sport_filter(self):
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8")
        self.assertIn("sport_filter.classify(item)", source)
        self.assertIn("sport_filter.FOOTBALL_STATES", source)


class FootballGetsAFootballLength(unittest.TestCase):
    def test_a_football_fixture_ends_at_start_plus_one_fifty(self):
        card = resolve(candidate())
        self.assertEqual("sport", card["end_time_source"])
        self.assertEqual(150, span(card))
        self.assertEqual(
            (KICKOFF + timedelta(minutes=150)).isoformat(), card["end_time"]
        )

    def test_the_generic_two_forty_is_gone_for_it(self):
        card = resolve(candidate())
        self.assertNotEqual(240, span(card))

    def test_both_football_verdicts_count(self):
        for state in sport_filter.FOOTBALL_STATES:
            self.assertIn("football", state)

    def test_a_likely_football_card_also_gets_it(self):
        card = resolve(candidate(competition="Premier League"))
        self.assertEqual("sport", card["end_time_source"])
        self.assertEqual(150, span(card))


class AStatedEndStillWins(unittest.TestCase):
    def test_provider_beats_the_sport_estimate(self):
        end = KICKOFF + timedelta(minutes=135)
        card = resolve(candidate(end_time=end.isoformat(), end_time_stated=True))
        self.assertEqual("provider", card["end_time_source"])
        self.assertEqual(135, span(card))

    def test_a_refused_stated_end_falls_to_the_sport_estimate(self):
        card = resolve(candidate(end_time="rubbish", end_time_stated=True))
        self.assertEqual("sport", card["end_time_source"])
        self.assertEqual(150, span(card))


class NothingElseChangedLength(unittest.TestCase):
    """Requirement: non-football behaviour must not move without cause."""

    def test_cricket_now_has_its_own_lengths_too(self):
        """PROMPT 16 left cricket on the generic estimate on purpose;
        PROMPT 18 gave it a length per format. The 240 here is T20's own
        number now, not the four hours everything used to get."""
        card = resolve(candidate(
            name="Barbados Tridents Vs Trinbago Knight Riders",
            competition="Caribbean Premier League",
            group_title="cricket",
        ))
        self.assertEqual("sport", card["end_time_source"])
        self.assertEqual(240, span(card))

    def test_an_unclassifiable_card_is_not_guessed_at(self):
        """No sport evidence means no sport estimate. 56 of the cards in the
        live scan are exactly this, and guessing football for them would be
        the invention this whole step is against."""
        item = candidate(name="SV Lafnitz vs Austria Lustenau",
                         competition="Cup", group_title="")
        self.assertIsNone(sr._sport_end_minutes(item))
        card = resolve(item)
        self.assertEqual("assumed", card["end_time_source"])
        self.assertEqual(240, span(card))

    def test_another_sport_is_untouched(self):
        card = resolve(candidate(name="Cycling", competition="Cycling",
                                 group_title="other"))
        self.assertEqual("assumed", card["end_time_source"])
        self.assertEqual(240, span(card))

    def test_the_ball_sports_are_the_ones_switched_on(self):
        """Football from PROMPT 16, cricket from PROMPT 18. Nothing else -
        every other sport still takes the generic estimate."""
        self.assertEqual(("football", "cricket"), sr.SPORT_DERIVED_ENDS)


class TheEstimateIsNotProof(unittest.TestCase):
    """Requirement: an estimate must not retire a match that is still on."""

    def _card(self, end_minutes, source):
        end = KICKOFF + timedelta(minutes=end_minutes)
        return {
            "id": "milan-vs-juventus", "name": "Milan vs Juventus",
            "sport_type": "football", "competition": "Italian Serie A",
            "start_time": KICKOFF.isoformat(), "end_time": end.isoformat(),
            "end_time_source": source, "schedule_verified": True,
            "status": "LIVE_NOW", "schedule_status": "LIVE_NOW",
            "verification_status": "verified_global", "verified": True,
            "available_link_count": 1, "playback_id": "ctv_abc",
            "url": "https://a.example/x.m3u8",
        }

    def _carried(self, card, offset):
        import tempfile
        kept, _ = protect_live_events(
            [], [card], probe=lambda _c: True,
            now=KICKOFF + timedelta(minutes=offset),
            state_path=Path(tempfile.mkdtemp()) / "state.json",
            grace_minutes=90, authority_states={},
        )
        return bool(kept)

    def test_a_match_still_being_played_is_carried_past_the_estimate(self):
        """Extra time and penalties run to about T+150. The freshness gate
        drops the card there, and live protection puts it straight back -
        which is the difference between an estimate and a proof."""
        card = self._card(150, "sport")
        for offset in (151, 180, 239):
            with self.subTest(offset=offset):
                self.assertTrue(self._carried(card, offset))

    def test_an_estimate_no_longer_retires_it_on_its_own(self):
        """PROMPT 16 measured this releasing at T+241, through
        `verified_end_passed`. PROMPT 19 took that path away from
        estimates - it is for a stated end only - so a card whose link
        still answers is now held past its estimate.

        That is the correct half of the trade: the system must not read
        its own arithmetic back as a verdict. The other half - what DOES
        retire a card with no stated end - is the END_PENDING grace in
        PROMPT 20/21, which is what FINAL_2 calls a separate, less
        aggressive grace for the rest.
        """
        card = self._card(150, "sport")
        self.assertTrue(self._carried(card, 241))
        for source in ("sport", "assumed"):
            with self.subTest(source=source):
                self.assertTrue(self._carried(self._card(150, source), 331))

    def test_a_stated_end_still_retires_it_on_time(self):
        """The path is not gone - it now requires what it always claimed
        to require."""
        stated = self._card(150, "provider")
        self.assertTrue(self._carried(stated, 239))
        self.assertFalse(self._carried(stated, 241))

    def test_a_normal_match_is_never_near_the_boundary(self):
        """90 + stoppage + half time is about T+120, and the card is still
        published for another 120 minutes after that."""
        card = self._card(150, "sport")
        for offset in (105, 120, 135):
            with self.subTest(offset=offset):
                self.assertTrue(ev._is_today_fresh(
                    card, KICKOFF + timedelta(minutes=offset), 12, 25))
                self.assertFalse(verified_end_passed(
                    card, KICKOFF + timedelta(minutes=offset), grace_minutes=90))


class TheAuthorityRuleCameLater(unittest.TestCase):
    def test_verified_end_passed_now_requires_a_stated_end(self):
        card = {
            "schedule_verified": True,
            "end_time": (KICKOFF - timedelta(hours=3)).isoformat(),
        }
        now = KICKOFF + timedelta(hours=1)
        self.assertFalse(verified_end_passed(
            dict(card, end_time_source="sport"), now, grace_minutes=0))
        self.assertTrue(verified_end_passed(
            dict(card, end_time_source="provider"), now, grace_minutes=0))

    def test_no_cricket_format_detection_exists_yet(self):
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8")
        for token in ("T20", "ODI", "Hundred", "cricket_format"):
            self.assertNotIn('"%s"' % token, source)


if __name__ == "__main__":
    unittest.main()
