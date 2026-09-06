"""PROMPT 30 - a scan that attached no stream does not finish quietly.

FINAL_3, অংশ ৪ক: `streams_attached: 0`, `fixtures_with_stream: 0`, and a scan
that reported `completed_with_warnings` for unrelated route errors while saying
nothing at all about having produced no playable link. One line of accounting
would have caught the 114-hour targeted-scan stall on its first day.

The warning had to wait for the counter, though. `streams_attached` counts one
stage - a stream-only playlist entry cross-matched onto a fixture that arrived
from somewhere else - and most feeds carry the fixture and its links in the
same record, so on a perfectly healthy scan that stage does nothing and the
number is 0. Measured on real output the same day: `streams_attached: 0`,
`fixtures_with_stream: 0`, and 33 of the 35 published Today cards holding a
verified route. A warning built on that counter would have fired every scan and
been ignored inside a week.

So `fixtures_with_stream` now means what its name says, and the two kinds of
route are kept apart: one carried from an earlier scan by live protection, one
found by this scan. A tab full of carried links is exactly what the stall
looked like.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.events import _stream_health  # noqa: E402


def _card(name, *, url="", links=0, carried=False, **extra):
    card = {"id": name.lower().replace(" ", "-"), "name": name}
    if url:
        card["url"] = url
        card["verification_status"] = "verified_global"
        card["verified"] = True
    if links:
        card["available_link_count"] = links
    if carried:
        card["carried_forward_reason"] = "not seen in this scan"
        card["carried_forward_misses"] = 2
    card.update(extra)
    return card


class TheCounterTellsTheTruthFirst(unittest.TestCase):
    def test_a_playable_card_is_counted_even_though_nothing_was_cross_matched(self):
        """The measured case: 33 playable cards, `streams_attached` 0."""
        today = [_card("Fixture %d" % index, url="https://a.example/%d.m3u8" % index,
                       links=6) for index in range(33)]
        health = _stream_health(today, [], {"streams_attached": 0})
        self.assertEqual(33, health["fixtures_with_stream"])
        self.assertEqual(0, health["streams_attached"])
        self.assertEqual("ok", health["state"])
        self.assertEqual([], health["warnings"])

    def test_the_cross_match_stage_keeps_its_own_number(self):
        health = _stream_health([_card("A vs B", url="https://a.example/1.m3u8")],
                                [], {"streams_attached": 4})
        self.assertEqual(4, health["streams_attached"])
        self.assertEqual(1, health["fixtures_with_stream"])

    def test_a_metadata_only_card_is_not_counted_as_playable(self):
        cards = [_card("No Link Yet", metadata_only=True),
                 _card("Playable", url="https://a.example/1.m3u8")]
        health = _stream_health(cards, [], {})
        self.assertEqual(1, health["fixtures_with_stream"])

    def test_the_two_tabs_are_reported_separately(self):
        health = _stream_health(
            [_card("Today One", url="https://a.example/1.m3u8")],
            [_card("Upcoming One"), _card("Upcoming Two")],
            {},
        )
        self.assertEqual(1, health["today_with_stream"])
        self.assertEqual(0, health["upcoming_with_stream"])
        self.assertEqual(3, health["published_fixtures"])


class ACarriedRouteIsNotThisScansWork(unittest.TestCase):
    def test_carried_and_fresh_routes_are_counted_apart(self):
        cards = [
            _card("Fresh One", url="https://a.example/1.m3u8"),
            _card("Fresh Two", url="https://a.example/2.m3u8"),
            _card("Held Over", url="https://a.example/3.m3u8", carried=True),
        ]
        health = _stream_health(cards, [], {})
        self.assertEqual(3, health["fixtures_with_stream"])
        self.assertEqual(2, health["fixtures_with_fresh_stream"])
        self.assertEqual(1, health["fixtures_with_carried_stream"])
        self.assertEqual("ok", health["state"])

    def test_a_tab_of_nothing_but_carried_links_is_degraded(self):
        """What the 114-hour stall looked like from the outside: every card
        still playable, and not one of them found by the scan running now."""
        cards = [_card("Held %d" % index, url="https://a.example/%d.m3u8" % index,
                       carried=True) for index in range(5)]
        health = _stream_health(cards, [], {})
        self.assertEqual("degraded", health["state"])
        self.assertIn("carried from an earlier scan", health["warnings"][0])
        self.assertEqual(5, health["fixtures_with_stream"])


class NoPlayableRouteIsNeverASilentSuccess(unittest.TestCase):
    def test_published_cards_with_no_route_at_all_raise_a_warning(self):
        cards = [_card("Upcoming %d" % index) for index in range(124)]
        health = _stream_health([], cards, {"streams_attached": 0})
        self.assertEqual("degraded", health["state"])
        self.assertEqual(1, len(health["warnings"]))
        self.assertIn("no published fixture has a playable route",
                      health["warnings"][0])
        self.assertIn("124", health["warnings"][0])

    def test_a_scan_that_published_nothing_at_all_is_not_blamed_for_streams(self):
        """No cards is a different fault, reported elsewhere. Calling it a
        stream failure would put the warning on the wrong thing."""
        health = _stream_health([], [], {})
        self.assertEqual("ok", health["state"])
        self.assertEqual([], health["warnings"])

    def test_one_real_route_is_enough_to_clear_the_warning(self):
        cards = [_card("Playable", url="https://a.example/1.m3u8")]
        cards += [_card("Waiting %d" % index) for index in range(50)]
        health = _stream_health(cards, [], {})
        self.assertEqual("ok", health["state"])
        self.assertEqual([], health["warnings"])


class TheWarningReachesTheScanSummary(unittest.TestCase):
    def test_the_summary_reads_the_stream_health_and_raises_the_status(self):
        source = (Path(__file__).resolve().parents[1] / "scanner"
                  / "output.py").read_text(encoding="utf-8")
        self.assertIn('reports_root / "event-schedule.json").get("stream_health")',
                      source.replace("\r\n", "\n"))
        self.assertIn("if source_errors or output_safety_items or stream_warnings",
                      source.replace("\r\n", "\n"))
        self.assertIn('"stream_warnings": stream_warnings,',
                      source.replace("\r\n", "\n"))

    def test_the_publish_path_reports_the_published_count_not_the_stage_count(self):
        source = (Path(__file__).resolve().parents[1] / "scanner"
                  / "events.py").read_text(encoding="utf-8").replace("\r\n", "\n")
        self.assertIn('schedule_stats["stream_health"] = _stream_health(', source)
        self.assertIn('schedule_stats["fixtures_with_attached_stream"]', source)

    def test_nothing_here_changes_which_streams_are_accepted(self):
        """A report may not become a gate. `_stream_health` reads the published
        cards and returns numbers; it returns no list anything is filtered by."""
        cards = [_card("A vs B", url="https://a.example/1.m3u8"),
                 _card("C vs D")]
        before = [dict(card) for card in cards]
        _stream_health(cards, [], {})
        self.assertEqual(before, cards)


if __name__ == "__main__":
    unittest.main()
