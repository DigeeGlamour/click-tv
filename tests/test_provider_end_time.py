"""An end time the feed itself stated is kept, and only that is `provider`.

Audited on 2026-09-05 against all 21 configured event feeds. One states an
end: `srhady-crichd-footy-live`, 24 rows carrying "End time", e.g.
`Milan vs Juventus  18:45 -> 21:00`. Twenty state none.

The representation was already reaching us - `event_adapters._record` has
taken an `end_time` all along and `flatten_records` copies it onto every
candidate - so nothing new was written to extract it. What was missing was
the last step: `_provider_fixture_item` overwrote the field with
`start + provider_event_hours` before anything could read it.

One trap this closes on the way. SonyLiv sends `contractEndDate`, its
licence expiry, which this code has always used to work out LIVE vs
FINISHED. It is not a finishing time: `Fazilka Falcons vs Bathinda Royals`
carried one 915 minutes past its own start, for a T20. Whether an end means
the match is only knowable in the adapter that read it, so that is where it
is decided.
"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import schedule_resolver as sr           # noqa: E402
from scanner.parsers import event_adapters as ea      # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 5, 14, 0, tzinfo=UTC)
UTC_ZONE = sr._zone("UTC", "UTC", "+00:00")


def stated(value, field="end_time"):
    return {field: value, "end_time_stated": True}


class OnlyAStatedEndCounts(unittest.TestCase):
    def test_a_real_stated_end_is_accepted(self):
        end = KICKOFF + timedelta(minutes=135)
        got = sr._provider_end_time(
            stated(end.isoformat()), KICKOFF, UTC_ZONE, NOW
        )
        self.assertEqual(end, got)

    def test_all_three_field_names_are_read(self):
        end = KICKOFF + timedelta(minutes=135)
        for field in sr.PROVIDER_END_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(
                    end,
                    sr._provider_end_time(
                        stated(end.isoformat(), field), KICKOFF, UTC_ZONE, NOW
                    ),
                )

    def test_an_end_the_adapter_did_not_vouch_for_is_refused(self):
        """SonyLiv's contractEndDate travels in `end_time` and must not be
        mistaken for the match finishing."""
        end = KICKOFF + timedelta(minutes=915)
        item = {"end_time": end.isoformat()}          # no end_time_stated
        self.assertIsNone(
            sr._provider_end_time(item, KICKOFF, UTC_ZONE, NOW)
        )
        item_false = dict(item, end_time_stated=False)
        self.assertIsNone(
            sr._provider_end_time(item_false, KICKOFF, UTC_ZONE, NOW)
        )

    def test_a_truthy_non_true_flag_is_not_enough(self):
        end = KICKOFF + timedelta(minutes=135)
        for flag in (1, "yes", "true", [1]):
            with self.subTest(flag=flag):
                self.assertIsNone(sr._provider_end_time(
                    {"end_time": end.isoformat(), "end_time_stated": flag},
                    KICKOFF, UTC_ZONE, NOW,
                ))


class MalformedIsRefused(unittest.TestCase):
    def test_nothing_at_all(self):
        self.assertIsNone(
            sr._provider_end_time({"end_time_stated": True}, KICKOFF, UTC_ZONE, NOW)
        )

    def test_blank_and_unparseable(self):
        for value in ("", "   ", "later today", "soon", "-", "null"):
            with self.subTest(value=value):
                self.assertIsNone(sr._provider_end_time(
                    stated(value), KICKOFF, UTC_ZONE, NOW))

    def test_an_end_before_kickoff(self):
        self.assertIsNone(sr._provider_end_time(
            stated((KICKOFF - timedelta(hours=2)).isoformat()),
            KICKOFF, UTC_ZONE, NOW,
        ))

    def test_an_end_exactly_at_kickoff(self):
        self.assertIsNone(sr._provider_end_time(
            stated(KICKOFF.isoformat()), KICKOFF, UTC_ZONE, NOW))

    def test_a_zero_epoch(self):
        self.assertIsNone(sr._provider_end_time(
            stated("1970-01-01T00:00:00Z"), KICKOFF, UTC_ZONE, NOW))

    def test_the_next_field_is_tried_after_a_bad_one(self):
        end = KICKOFF + timedelta(minutes=90)
        item = {
            "end_time": "not a time",
            "end_at": end.isoformat(),
            "end_time_stated": True,
        }
        self.assertEqual(end, sr._provider_end_time(item, KICKOFF, UTC_ZONE, NOW))


class TimeZonesLandInUtc(unittest.TestCase):
    def test_a_z_suffix(self):
        """The shape the one real feed uses: 2026-09-06T21:00:00.000Z."""
        got = sr._provider_end_time(
            stated("2026-09-05T21:00:00.000Z"), KICKOFF, UTC_ZONE, NOW)
        self.assertEqual(datetime(2026, 9, 5, 21, 0, tzinfo=UTC), got)

    def test_an_offset_is_converted(self):
        got = sr._provider_end_time(
            stated("2026-09-05T21:00:00+05:30"), KICKOFF, UTC_ZONE, NOW)
        self.assertEqual(datetime(2026, 9, 5, 15, 30, tzinfo=UTC), got)

    def test_a_naive_clock_uses_the_feeds_own_zone(self):
        india = sr.source_clock_zone("sm-fancode", UTC_ZONE)
        got = sr._provider_end_time(
            stated("2026-09-05T21:00:00"), KICKOFF, india, NOW)
        self.assertEqual(datetime(2026, 9, 5, 15, 30, tzinfo=UTC), got)
        self.assertEqual(UTC, got.tzinfo)


class AStatedEndBeatsTheArithmetic(unittest.TestCase):
    def _resolve(self, extra=None, status="UPCOMING", now=NOW):
        item = dict({
            "name": "Alpha vs Beta",
            "source_id": "some-feed",
            "status": status,
            "url": "https://a.example/x.m3u8",
        }, **(extra or {}))
        return sr._provider_fixture_item(
            item, KICKOFF, now, 4, source_timezone=UTC_ZONE
        )

    def test_without_one_it_is_still_the_generic_four_hours(self):
        card = self._resolve()
        self.assertEqual("assumed", card["end_time_source"])
        self.assertEqual(
            (KICKOFF + timedelta(hours=4)).isoformat(), card["end_time"]
        )

    def test_with_one_the_stated_end_wins(self):
        end = KICKOFF + timedelta(minutes=135)
        card = self._resolve(stated(end.isoformat()))
        self.assertEqual("provider", card["end_time_source"])
        self.assertEqual(end.isoformat(), card["end_time"])
        self.assertEqual(
            135,
            round((datetime.fromisoformat(card["end_time"])
                   - datetime.fromisoformat(card["start_time"])).total_seconds() / 60),
        )

    def test_a_refused_end_falls_back_and_says_assumed(self):
        for value in ("", "rubbish", (KICKOFF - timedelta(hours=1)).isoformat()):
            with self.subTest(value=value):
                card = self._resolve(stated(value))
                self.assertEqual("assumed", card["end_time_source"])
                self.assertEqual(
                    (KICKOFF + timedelta(hours=4)).isoformat(), card["end_time"]
                )

    def test_a_long_stated_end_is_kept_not_capped(self):
        """A Test day, or a session that really does run nine hours."""
        end = KICKOFF + timedelta(hours=9)
        card = self._resolve(stated(end.isoformat()))
        self.assertEqual("provider", card["end_time_source"])
        self.assertEqual(end.isoformat(), card["end_time"])


class TheLiveExtensionCannotBorrowTheLabel(unittest.TestCase):
    """The existing guard still runs; what it touches stops being provider."""

    def _resolve(self, extra, now):
        item = dict({
            "name": "Alpha vs Beta",
            "source_id": "some-feed",
            "status": "LIVE",
            "url": "https://a.example/x.m3u8",
        }, **extra)
        return sr._provider_fixture_item(
            item, KICKOFF, now, 4, source_timezone=UTC_ZONE
        )

    def test_a_stated_end_still_ahead_is_left_alone(self):
        end = KICKOFF + timedelta(hours=6)
        card = self._resolve(stated(end.isoformat()), NOW)
        self.assertEqual("provider", card["end_time_source"])
        self.assertEqual(end.isoformat(), card["end_time"])

    def test_a_stated_end_already_passed_is_extended_and_downgraded(self):
        """The feed says LIVE and its own end has gone by. The card must not
        vanish - the guard has always prevented that - but the time it ends
        up with is this system's, so it is no longer `provider`."""
        late = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
        end = KICKOFF + timedelta(minutes=30)
        card = self._resolve(stated(end.isoformat()), late)
        self.assertEqual("assumed", card["end_time_source"])
        self.assertEqual((late + timedelta(hours=1)).isoformat(), card["end_time"])

    def test_the_guess_path_is_unchanged_by_all_of_this(self):
        late = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
        card = self._resolve({}, late)
        self.assertEqual("assumed", card["end_time_source"])
        self.assertEqual((late + timedelta(hours=1)).isoformat(), card["end_time"])


class TheAdapterDecidesWhatItRead(unittest.TestCase):
    def test_the_flag_defaults_to_false(self):
        record = ea._record(
            "src", name="A vs B", status_raw="UPCOMING", channels=[],
            end_time="2026-09-05T21:00:00Z",
        )
        self.assertIs(False, record["end_time_stated"])

    def test_an_empty_end_cannot_be_stated(self):
        record = ea._record(
            "src", name="A vs B", status_raw="UPCOMING", channels=[],
            end_time="", end_time_stated=True,
        )
        self.assertIs(False, record["end_time_stated"])

    def test_a_vouched_end_is_marked(self):
        record = ea._record(
            "src", name="A vs B", status_raw="UPCOMING", channels=[],
            end_time="2026-09-05T21:00:00Z", end_time_stated=True,
        )
        self.assertIs(True, record["end_time_stated"])

    def test_the_sonyliv_adapter_does_not_vouch_for_its_contract_date(self):
        source = (ROOT / "scanner" / "parsers" / "event_adapters.py").read_text(
            encoding="utf-8")
        block = source[source.index("end_iso = _epoch(info.get(\"contractEndDate\")"):]
        block = block[:block.index("_record(") + 2000]
        self.assertNotIn("end_time_stated=True", block)

    def test_the_tabular_adapter_does_vouch(self):
        """The feed with "Start time" and "End time" side by side."""
        source = (ROOT / "scanner" / "parsers" / "event_adapters.py").read_text(
            encoding="utf-8")
        self.assertIn('end_time = _parse_clock(row.get("End time"))', source)
        self.assertEqual(
            1,
            source.count("end_time_stated=True,"),
            "exactly one adapter should vouch for its end time today",
        )

    def test_the_flag_reaches_the_candidate(self):
        source = (ROOT / "scanner" / "parsers" / "event_adapters.py").read_text(
            encoding="utf-8")
        self.assertIn(
            '"end_time_stated": bool(record.get("end_time_stated")),', source
        )

    def test_the_json_parser_vouches_for_its_own_end_keys(self):
        source = (ROOT / "scanner" / "parsers" / "json_parser.py").read_text(
            encoding="utf-8")
        self.assertIn('"end_time_stated": bool(str(end_time or "").strip()),', source)


class ProvenanceStillTravels(unittest.TestCase):
    def test_a_provider_card_reads_back_as_provider(self):
        end = KICKOFF + timedelta(minutes=135)
        card = sr._provider_fixture_item(
            {"name": "Alpha vs Beta", "source_id": "f", "status": "UPCOMING",
             "url": "https://a.example/x.m3u8", **stated(end.isoformat())},
            KICKOFF, NOW, 4, source_timezone=UTC_ZONE,
        )
        self.assertTrue(sr.end_time_is_provider_stated(card))
        self.assertEqual("provider", sr.end_time_provenance(card))


if __name__ == "__main__":
    unittest.main()
