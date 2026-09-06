"""Where a card's `end_time` came from, carried on the card itself.

FINAL_1 rule 10 measured the fault and FINAL_2 ধাপ ৩ names the fix. The
system writes `end_time = kickoff + events.provider_event_hours`, stamps
`schedule_verified = True` on it, and from that point nothing can tell the
guess apart from a fixture that really did state when it finishes.

Measured again on 2026-09-05, on live published data: 344 of 344 cards
carried an end_time, 343 of them exactly 240 minutes past kickoff, every
one `schedule_verified = True`, and none carrying any provenance at all.

This step only names the three cases. It changes no end_time, no duration,
no removal rule - those are PROMPT 15 through 21.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import schedule_resolver as sr  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def catalogue(competition_extra=None, fixture_extra=None):
    """A one-fixture catalogue file, written the way production reads it."""
    competition = {
        "id": "test-series-2026",
        "name": "Test Series 2026",
        "timezone": "UTC",
        "source_url": "https://example.test/schedule",
        "fixtures": [dict({
            "name": "Alpha vs Beta",
            "start": "2026-09-05T12:00:00",
        }, **(fixture_extra or {}))],
    }
    competition.update(competition_extra or {})
    directory = Path(tempfile.mkdtemp())
    path = directory / "event-fixtures.json"
    path.write_text(
        json.dumps({"competitions": [competition]}), encoding="utf-8"
    )
    return path


class TheThreeValues(unittest.TestCase):
    def test_the_vocabulary_is_exactly_three(self):
        self.assertEqual(
            ("provider", "sport", "assumed"), sr.END_TIME_SOURCES
        )


class ASystemGuessSaysSo(unittest.TestCase):
    """Requirement 1: kickoff + provider_event_hours is `assumed`."""

    def _provider_item(self, **extra):
        item = dict({
            "name": "Alpha vs Beta",
            "source_id": "some-feed",
            "status": "UPCOMING",
            "url": "https://a.example/x.m3u8",
        }, **extra)
        return sr._provider_fixture_item(
            item, NOW + timedelta(hours=2), NOW, 4
        )

    def test_a_provider_feed_fixture_is_assumed_not_provider(self):
        resolved = self._provider_item()
        self.assertIsNotNone(resolved)
        self.assertEqual("assumed", resolved["end_time_source"])
        self.assertNotEqual("provider", resolved["end_time_source"])

    def test_the_end_time_itself_is_unchanged(self):
        """This step names the guess. It does not correct it - that is
        PROMPT 16 for football and 17/18 for cricket."""
        resolved = self._provider_item()
        start = datetime.fromisoformat(resolved["start_time"])
        end = datetime.fromisoformat(resolved["end_time"])
        self.assertEqual(240, round((end - start).total_seconds() / 60))

    def test_a_live_fixture_extension_is_still_a_guess(self):
        resolved = self._provider_item(status="LIVE")
        self.assertEqual("assumed", resolved["end_time_source"])

    def test_schedule_verified_is_still_stamped_on_it(self):
        """Deliberately unchanged. What that stamp is allowed to mean is
        `verified_end_passed`, and rewriting that is PROMPT 19."""
        resolved = self._provider_item()
        self.assertIs(True, resolved["schedule_verified"])


class ACatalogueFixtureKnowsWhichItIs(unittest.TestCase):
    """Requirements 2 and 3, through the path that already exists."""

    def test_an_explicit_end_is_provider(self):
        path = catalogue(
            competition_extra={"duration_minutes": 480},
            fixture_extra={"end": "2026-09-09T18:00:00"},
        )
        fixture = sr.load_fixtures(path)[0]
        self.assertEqual("provider", fixture["end_source"])
        # Four days long: no duration could have produced this.
        self.assertEqual(
            "2026-09-09T18:00:00+00:00", fixture["end"].isoformat()
        )

    def test_a_competition_duration_is_sport(self):
        path = catalogue(competition_extra={"duration_minutes": 210})
        fixture = sr.load_fixtures(path)[0]
        self.assertEqual("sport", fixture["end_source"])
        self.assertEqual(
            210,
            round((fixture["end"] - fixture["start"]).total_seconds() / 60),
        )

    def test_neither_is_assumed_and_still_two_hundred_and_forty(self):
        path = catalogue()
        fixture = sr.load_fixtures(path)[0]
        self.assertEqual("assumed", fixture["end_source"])
        self.assertEqual(
            240,
            round((fixture["end"] - fixture["start"]).total_seconds() / 60),
        )

    def test_the_real_catalogue_carries_all_of_this(self):
        """The nine explicit ends in config/event-fixtures.json are real
        multi-day Test finishes, which is why the `provider` path is not
        theoretical - no new parser was written for it."""
        fixtures = sr.load_fixtures(ROOT / "config" / "event-fixtures.json")
        sources = {f["end_source"] for f in fixtures}
        self.assertTrue(sources <= set(sr.END_TIME_SOURCES))
        self.assertIn("provider", sources)
        self.assertIn("sport", sources)
        self.assertTrue(all("end_source" in f for f in fixtures))

    def test_applying_a_fixture_stamps_the_card(self):
        path = catalogue(fixture_extra={"end": "2026-09-09T18:00:00"})
        fixture = sr.load_fixtures(path)[0]
        card = sr._apply_fixture(
            {"name": "whatever", "url": "https://a.example/x.m3u8"},
            fixture,
            None,
        )
        self.assertEqual("provider", card["end_time_source"])
        self.assertEqual("2026-09-09T18:00:00+00:00", card["end_time"])


class ProvenanceTravelsWithTheTime(unittest.TestCase):
    """Requirement 4: it must survive every copy, merge and publish."""

    def test_every_list_that_carries_end_time_carries_the_source(self):
        for module in ("schedule_resolver.py", "merger.py"):
            source = (ROOT / "scanner" / module).read_text(encoding="utf-8")
            blocks = source.count('"end_time"')
            with_source = source.count('"end_time_source"')
            with self.subTest(module=module):
                self.assertGreaterEqual(
                    with_source,
                    1,
                    "%s copies end_time and would drop its provenance" % module,
                )
                self.assertGreater(blocks, 0)

    def test_the_channel_fallback_drops_both_together(self):
        """It throws the fixture clock away, so the provenance of a time it
        no longer has must go with it."""
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '"start_time", "start_at", "end_time", "end_at", "end_time_source",',
            source,
        )

    def test_the_merge_copies_it_next_to_end_time(self):
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        index_end = source.index('            "end_time",\n')
        tail = source[index_end:index_end + 600]
        self.assertIn('"end_time_source",', tail)

    def test_a_stream_attached_to_a_fixture_inherits_it(self):
        source = (ROOT / "scanner" / "schedule_resolver.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            2,
            source.count(
                '"competition", "fixture_id", "start_time", "start_at", '
                '"end_time",\n            "end_time_source",'
            ),
            "both attachment paths must carry it",
        )


class SilenceIsNotEvidence(unittest.TestCase):
    """Requirement 5: a legacy card must never read as `provider`."""

    def test_a_card_with_no_field_reads_as_assumed(self):
        self.assertEqual("assumed", sr.end_time_provenance({}))
        self.assertFalse(sr.end_time_is_provider_stated({}))

    def test_a_legacy_card_with_an_end_time_and_no_source(self):
        legacy = {
            "id": "old-card",
            "start_time": "2026-09-05T12:00:00+00:00",
            "end_time": "2026-09-05T16:00:00+00:00",
            "schedule_verified": True,
        }
        self.assertEqual("assumed", sr.end_time_provenance(legacy))
        self.assertFalse(sr.end_time_is_provider_stated(legacy))

    def test_junk_and_unknown_values_are_not_promoted(self):
        for value in ("", "   ", None, "PROVIDER-ish", "feed", "true", 1, []):
            with self.subTest(value=value):
                self.assertEqual(
                    "assumed",
                    sr.end_time_provenance({"end_time_source": value}),
                )

    def test_the_three_real_values_read_back(self):
        for value in sr.END_TIME_SOURCES:
            with self.subTest(value=value):
                self.assertEqual(
                    value, sr.end_time_provenance({"end_time_source": value})
                )
        self.assertTrue(
            sr.end_time_is_provider_stated({"end_time_source": "provider"})
        )

    def test_case_and_padding_do_not_change_the_meaning(self):
        self.assertEqual(
            "provider", sr.end_time_provenance({"end_time_source": " Provider "})
        )


class NothingElseMoved(unittest.TestCase):
    """Requirement 6: this step is provenance only."""

    def test_the_removal_rule_now_reads_this_field(self):
        """PROMPT 14 recorded provenance and spent none of it. PROMPT 19
        is what spends it: an estimate no longer retires anything."""
        from scanner.event_lifecycle import verified_end_passed
        card = {
            "schedule_verified": True,
            "end_time": "2026-09-05T10:00:00+00:00",
            "end_time_source": "assumed",
        }
        self.assertFalse(verified_end_passed(card, NOW, grace_minutes=90))

    def test_provenance_is_what_separates_the_two(self):
        from scanner.event_lifecycle import verified_end_passed
        assumed = {
            "schedule_verified": True,
            "end_time": "2026-09-05T10:00:00+00:00",
            "end_time_source": "assumed",
        }
        provider = dict(assumed, end_time_source="provider")
        self.assertFalse(verified_end_passed(assumed, NOW, grace_minutes=90))
        self.assertTrue(verified_end_passed(provider, NOW, grace_minutes=90))

    def test_the_lifecycle_timings_are_where_prompt_13_left_them(self):
        from scanner.lifecycle_config import lifecycle_settings, targeted_timings
        settings_path = ROOT / "config" / "settings.json"
        live = lifecycle_settings(settings_path=settings_path)
        timings = targeted_timings(settings_path=settings_path)
        self.assertEqual(25, live["move_to_today_minutes"])
        self.assertEqual(25, live["no_link_today_grace_minutes"])
        self.assertEqual(25, timings["window_minutes"])
        self.assertEqual(10, timings["after_kickoff_minutes"])
        self.assertEqual(5, timings["retry_interval_minutes"])


if __name__ == "__main__":
    unittest.main()
