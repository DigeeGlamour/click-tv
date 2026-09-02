"""A scan that cannot serialise its own payload publishes nothing, silently.

The 18:26 run on 2026-09-02 did all of its work - it tidied a title, folded
eight duplicates, and corrected four cards' home and away sides, including the
`Real Madrid vs Real Betis` the report named - and then said:

    Snapshot validation failed (requirement 15): today-match.json is not
    serializable: Object of type ZoneInfo is not JSON serializable
    Carried forward unchanged: today-match.json, upcoming.json

The run went green, the manifest took a new timestamp, and both event files
kept the previous scan's contents. Nothing on the page said so. That is why
four published corrections could be read in the log and none of them in the
data, and why the same two cards looked unfixed across several runs.

The value was `_source_timezone`, a ZoneInfo the merge puts on a card. The
payload builder stripped it at the top level, but `_absorb` copies a folded
card into the keeper's `backups`, so a folded fixture carried one nested one
level down where the strip never looked. Folding more often - which the
kickoff tolerance does deliberately - is what made it happen.

Two things are fixed here, because either alone would leave the trap set: a
backup is a route and no longer carries the card's own bookkeeping, and the
payload refuses to keep any value json.dumps would reject, wherever it sits,
naming it rather than dropping it quietly.
"""
import json
import unittest
from datetime import timedelta, timezone

from scanner import events, fixture_dedupe

#: What `_source_timezone` holds. Not JSON, and the default in this codebase.
SOURCE_ZONE = timezone(timedelta(hours=6))


def card(name, kickoff, url, **extra):
    row = {"name": name, "start_time": kickoff, "url": url,
           "_source_timezone": SOURCE_ZONE}
    row.update(extra)
    return row


def payload(items, event_type="today_match"):
    return events._payload(items, event_type, filtered_stale=0,
                           filtered_unplayable=0)


class TheRunThatPublishedNothing(unittest.TestCase):
    """The two Willow/bingstream cards, folded, as the scan folded them."""

    def setUp(self):
        self.willow = card(
            "Belfast Wolves vs Edinburgh Castle Rockers",
            "2026-09-02T12:55:00+00:00", "https://willow.example/a.m3u8",
            channels=[{"name": "Willow"}, {"name": "Willow 2"}])
        self.bingstream = card(
            "Belfast Wolves vs Edinburgh Castle Rockers",
            "2026-09-02T13:15:00+00:00", "https://bingstream.example/b.m3u8",
            channels=[{"name": "Server-1"}])

    def test_the_folded_payload_serialises(self):
        kept, folded = fixture_dedupe.fold([self.willow, self.bingstream])
        self.assertEqual(len(folded), 1, "the two cards must fold")
        json.dumps(payload(kept))  # would raise TypeError before

    def test_a_backup_does_not_carry_the_cards_bookkeeping(self):
        kept, _ = fixture_dedupe.fold([self.willow, self.bingstream])
        for row in kept[0].get("backups") or []:
            with self.subTest(url=row.get("url")):
                self.assertEqual(
                    [key for key in row if str(key).startswith("_")], [])

    def test_the_folded_route_is_still_there(self):
        # The strip must not throw the backup away with the bookkeeping.
        kept, _ = fixture_dedupe.fold([self.willow, self.bingstream])
        urls = [row.get("url") for row in kept[0].get("backups") or []]
        self.assertIn("https://bingstream.example/b.m3u8", urls)

    def test_the_folded_channels_survive(self):
        kept, _ = fixture_dedupe.fold([self.willow, self.bingstream])
        names = [c.get("name") for c in kept[0].get("channels") or []]
        self.assertEqual(names, ["Willow", "Willow 2", "Server-1"])


class NothingUnpublishableSurvivesThePayload(unittest.TestCase):
    def test_the_top_level_zone_is_still_stripped(self):
        rows = [card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x")]
        built = payload(rows)
        json.dumps(built)
        self.assertNotIn("_source_timezone", built["items"][0])

    def test_a_value_nested_in_a_backup_is_dropped(self):
        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     backups=[{"url": "https://b/y", "zone": SOURCE_ZONE}])]
        json.dumps(payload(rows))

    def test_a_value_nested_in_a_channel_is_dropped(self):
        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     channels=[{"name": "Willow", "zone": SOURCE_ZONE}])]
        built = payload(rows)
        json.dumps(built)
        self.assertEqual(built["items"][0]["channels"][0]["name"], "Willow")

    def test_a_value_nested_three_deep_is_dropped(self):
        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     drm={"licence": {"headers": {"zone": SOURCE_ZONE}}})]
        json.dumps(payload(rows))

    def test_an_unpublishable_list_entry_is_removed(self):
        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     tags=["ok", SOURCE_ZONE, "also ok"])]
        built = payload(rows)
        json.dumps(built)
        self.assertEqual(built["items"][0]["tags"], ["ok", "also ok"])

    def test_it_says_what_it_dropped(self):
        # Silence is what let this run green for hours.
        import contextlib
        import io

        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     backups=[{"url": "https://b/y", "zone": SOURCE_ZONE}])]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            payload(rows)
        printed = out.getvalue()
        self.assertIn("backups[0].zone", printed)
        self.assertIn("A vs B", printed)

    def test_real_values_are_left_exactly_alone(self):
        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     competition="LaLiga", available_link_count=3,
                     metadata_only=False, resolution_height=720,
                     channels=[{"name": "Willow", "quality": "HD"}],
                     backups=[{"url": "https://b/y", "verified": True}],
                     nothing=None)]
        built = payload(rows)
        json.dumps(built)
        item = built["items"][0]
        self.assertEqual(item["competition"], "LaLiga")
        self.assertEqual(item["available_link_count"], 3)
        self.assertIs(item["metadata_only"], False)
        self.assertIsNone(item["nothing"])
        self.assertEqual(item["channels"], [{"name": "Willow", "quality": "HD"}])
        self.assertEqual(item["backups"], [{"url": "https://b/y",
                                            "verified": True}])

    def test_the_uid_the_page_reads_is_not_treated_as_private(self):
        # `_uid` starts with an underscore and the front end needs it, so the
        # scrub keys on what json.dumps accepts, not on the name.
        rows = [dict(card("A vs B", "2026-09-02T12:00:00+00:00", "https://a/x"),
                     _uid="event-1")]
        built = payload(rows)
        self.assertEqual(built["items"][0]["_uid"], "event-1")


class TheScrubItself(unittest.TestCase):
    def test_it_reports_every_path_it_removed(self):
        row = {"a": SOURCE_ZONE, "b": {"c": SOURCE_ZONE},
               "d": [1, SOURCE_ZONE], "e": "kept"}
        dropped = events._drop_unserialisable(row)
        self.assertEqual(sorted(dropped), ["a", "b.c", "d[1]"])
        self.assertEqual(row, {"b": {}, "d": [1], "e": "kept"})

    def test_a_clean_structure_reports_nothing(self):
        row = {"a": 1, "b": [1, "two", {"c": None}], "d": True}
        before = json.dumps(row, sort_keys=True)
        self.assertEqual(events._drop_unserialisable(row), [])
        self.assertEqual(json.dumps(row, sort_keys=True), before)

    def test_the_json_types_are_all_accepted(self):
        for value in (None, True, 1, 1.5, "text", [], {}):
            with self.subTest(value=value):
                self.assertTrue(events._publishable(value))

    def test_a_datetime_is_not_a_json_type(self):
        from datetime import datetime

        self.assertFalse(events._publishable(datetime.now()))
        self.assertFalse(events._publishable(SOURCE_ZONE))
        self.assertFalse(events._publishable({1, 2}))


if __name__ == "__main__":
    unittest.main()
