"""Only an end a provider stated is an authoritative end.

FINAL_1 রায় ১০ proved the fault: this system writes `end_time = kickoff +
provider_event_hours`, stamps `schedule_verified = True` on it, and then two
different rules read that stamp back as though a fixture had announced its
own finish. Measured on 2026-09-05, 344 of 344 published cards took that
branch and 343 of them were exactly 240 minutes past kickoff.

Two consumers are corrected here, and they are the only two that treat an
end time as authority:

  event_lifecycle.verified_end_passed   retires a card outright
  events._is_today_fresh                exempts a card from every rule below
                                        it - which is PROMPT 13's Finding A

Everything else about end times is unchanged. `estimate_passed` still reads
any end time it likes, with its own 90-minute grace, because it is
explicitly a supporting signal and not an authority.
"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import events as ev                          # noqa: E402
from scanner import schedule_resolver as sr               # noqa: E402
from scanner.event_lifecycle import (                     # noqa: E402
    END_TIME_SOURCES, estimate_passed, end_time_provenance,
    end_time_is_provider_stated, verified_end_passed,
)
from scanner.lifecycle_config import lifecycle_settings   # noqa: E402

UTC = timezone.utc
KICKOFF = datetime(2026, 9, 5, 9, 0, tzinfo=UTC)
LATER = KICKOFF + timedelta(hours=11)
GRACE = lifecycle_settings(
    settings_path=ROOT / "config" / "settings.json")["no_link_today_grace_minutes"]


def ended_card(source=None, **extra):
    card = {
        "schedule_verified": True,
        "start_time": KICKOFF.isoformat(),
        "end_time": (KICKOFF + timedelta(hours=4)).isoformat(),
    }
    if source is not None:
        card["end_time_source"] = source
    card.update(extra)
    return card


class OnlyProviderIsAuthority(unittest.TestCase):
    def test_a_stated_end_that_has_passed_retires_the_card(self):
        self.assertTrue(
            verified_end_passed(ended_card("provider"), LATER, grace_minutes=90)
        )

    def test_a_sport_estimate_never_does(self):
        self.assertFalse(
            verified_end_passed(ended_card("sport"), LATER, grace_minutes=90)
        )

    def test_a_generic_estimate_never_does(self):
        self.assertFalse(
            verified_end_passed(ended_card("assumed"), LATER, grace_minutes=90)
        )

    def test_a_legacy_card_with_no_provenance_never_does(self):
        """An end time and `schedule_verified: true` and nothing else - which
        is every card published before PROMPT 14."""
        legacy = ended_card()
        self.assertNotIn("end_time_source", legacy)
        self.assertIs(True, legacy["schedule_verified"])
        self.assertFalse(verified_end_passed(legacy, LATER, grace_minutes=90))

    def test_the_stamp_alone_proves_nothing(self):
        for source in ("sport", "assumed"):
            with self.subTest(source=source):
                card = ended_card(source)
                self.assertIs(True, card["schedule_verified"])
                self.assertFalse(
                    verified_end_passed(card, LATER, grace_minutes=90))

    def test_a_provider_end_still_needs_the_schedule_to_be_verified(self):
        card = ended_card("provider", schedule_verified=False)
        self.assertFalse(verified_end_passed(card, LATER, grace_minutes=90))

    def test_junk_provenance_is_not_authority(self):
        for value in ("PROVIDER-ish", "feed", "true", 1, [], None):
            with self.subTest(value=value):
                self.assertFalse(verified_end_passed(
                    ended_card(value), LATER, grace_minutes=90))

    def test_a_provider_end_still_in_the_future_is_not_passed(self):
        soon = KICKOFF + timedelta(hours=1)
        self.assertFalse(
            verified_end_passed(ended_card("provider"), soon, grace_minutes=90)
        )


class ATestDayIsNotAFinish(unittest.TestCase):
    """The case with the worst consequence: four more days may remain."""

    def test_a_test_days_sport_estimate_ends_nothing(self):
        card = {
            "schedule_verified": True,
            "start_time": KICKOFF.isoformat(),
            "end_time": (KICKOFF + timedelta(minutes=480)).isoformat(),
            "end_time_source": "sport",
            "sport_type": "cricket",
        }
        way_past = KICKOFF + timedelta(minutes=480 + 90 + 1)
        self.assertFalse(verified_end_passed(card, way_past, grace_minutes=90))

    def test_but_a_catalogue_tests_real_end_does(self):
        fixtures = sr.load_fixtures(ROOT / "config" / "event-fixtures.json")
        fixture = next(
            f for f in fixtures
            if f["end_source"] == "provider"
            and (f["end"] - f["start"]).total_seconds() > 24 * 3600
        )
        card = {
            "schedule_verified": True,
            "start_time": fixture["start"].isoformat(),
            "end_time": fixture["end"].isoformat(),
            "end_time_source": "provider",
        }
        after = fixture["end"] + timedelta(hours=3)
        self.assertTrue(verified_end_passed(card, after, grace_minutes=90))


class TheSupportingSignalIsUntouched(unittest.TestCase):
    def test_estimate_passed_still_reads_any_end_time(self):
        for source in END_TIME_SOURCES:
            with self.subTest(source=source):
                self.assertTrue(estimate_passed(
                    ended_card(source),
                    KICKOFF + timedelta(hours=4, minutes=91),
                    90,
                ))

    def test_and_still_keeps_its_own_grace(self):
        for source in END_TIME_SOURCES:
            with self.subTest(source=source):
                self.assertFalse(estimate_passed(
                    ended_card(source),
                    KICKOFF + timedelta(hours=4, minutes=89),
                    90,
                ))


class FindingAIsClosed(unittest.TestCase):
    """PROMPT 13, Finding A. The 25-minute no-link grace was unreachable:
    every real card carried `schedule_verified` and an end time, which
    exempted it from every rule below - so a card that never found a link
    sat on Today Match for four hours instead of twenty-five minutes."""

    def _waiting(self, source=None):
        card = {
            "id": "rishikesh-dragons-vs-doiwala-kings",
            "name": "Rishikesh Dragons vs Doiwala Kings",
            "start_time": KICKOFF.isoformat(),
            "end_time": (KICKOFF + timedelta(hours=4)).isoformat(),
            "schedule_verified": True,
            "status": "LINK_UPDATING",
            "schedule_status": "LINK_UPDATING",
            "metadata_only": True,
            "verification_status": "metadata_only",
            "available_link_count": 0,
        }
        if source is not None:
            card["end_time_source"] = source
        return card

    def test_the_grace_is_now_reachable_at_all(self):
        for source in (None, "assumed", "sport"):
            with self.subTest(source=source):
                kept = ev._is_today_fresh(
                    self._waiting(source),
                    KICKOFF + timedelta(minutes=GRACE + 1), 12, GRACE)
                self.assertFalse(kept)

    def test_the_card_is_kept_right_up_to_the_boundary(self):
        for source in (None, "assumed", "sport"):
            for offset in (0, 10, GRACE):
                with self.subTest(source=source, offset=offset):
                    self.assertTrue(ev._is_today_fresh(
                        self._waiting(source),
                        KICKOFF + timedelta(minutes=offset), 12, GRACE))

    def test_a_provider_stated_end_still_earns_the_exemption(self):
        """Which is what the exemption was written for: an official
        multi-day fixture that really does run past the age guard."""
        card = self._waiting("provider")
        self.assertTrue(ev._is_today_fresh(
            card, KICKOFF + timedelta(minutes=GRACE + 1), 12, GRACE))
        self.assertTrue(ev._is_today_fresh(
            card, KICKOFF + timedelta(minutes=239), 12, GRACE))
        # and it goes when its own stated end arrives
        self.assertFalse(ev._is_today_fresh(
            card, KICKOFF + timedelta(minutes=240), 12, GRACE))

    def test_the_four_hour_estimate_no_longer_buys_anything(self):
        card = self._waiting("assumed")
        self.assertFalse(ev._is_today_fresh(
            card, KICKOFF + timedelta(minutes=60), 12, GRACE))


class OneDefinitionOnly(unittest.TestCase):
    def test_the_resolver_re_exports_rather_than_repeating(self):
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8")
        self.assertNotIn('END_SOURCE_PROVIDER = "provider"', source)
        self.assertIn("    end_time_provenance,", source)

    def test_both_names_still_answer_from_the_resolver(self):
        self.assertEqual("provider", sr.end_time_provenance(
            {"end_time_source": "provider"}))
        self.assertTrue(sr.end_time_is_provider_stated(
            {"end_time_source": "provider"}))
        self.assertEqual("assumed", sr.end_time_provenance({}))

    def test_and_from_the_lifecycle_module(self):
        self.assertEqual("assumed", end_time_provenance({}))
        self.assertFalse(end_time_is_provider_stated({}))


class NothingBeforeThisMoved(unittest.TestCase):
    def test_the_lifecycle_timings_are_unchanged(self):
        from scanner.lifecycle_config import targeted_timings
        settings_path = ROOT / "config" / "settings.json"
        live = lifecycle_settings(settings_path=settings_path)
        timings = targeted_timings(settings_path=settings_path)
        self.assertEqual(25, live["move_to_today_minutes"])
        self.assertEqual(25, live["no_link_today_grace_minutes"])
        self.assertEqual((25, 10, 5), (
            timings["window_minutes"], timings["after_kickoff_minutes"],
            timings["retry_interval_minutes"]))

    def test_the_durations_are_unchanged(self):
        from scanner.event_lifecycle import CRICKET_FORMAT_MINUTES
        self.assertEqual(
            {"T10": 150, "T20": 240, "ODI": 480, "Test": 480,
             "Hundred": 210, "unknown": 300},
            CRICKET_FORMAT_MINUTES,
        )

    def test_a_card_with_a_link_is_still_never_dropped_by_the_no_link_rule(self):
        playable = {
            "start_time": KICKOFF.isoformat(),
            "end_time": (KICKOFF + timedelta(hours=9)).isoformat(),
            "end_time_source": "sport",
            "schedule_verified": True,
            "playback_id": "ctv_abc",
            "url": "https://a.example/x.m3u8",
            "available_link_count": 1,
        }
        self.assertTrue(ev._is_today_fresh(
            playable, KICKOFF + timedelta(minutes=200), 12, GRACE))


if __name__ == "__main__":
    unittest.main()
