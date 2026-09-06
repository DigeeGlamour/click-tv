"""PROMPT 33 - route failures counted against the fixtures they belong to.

FINAL_1 and FINAL_3 both stop at the same sentence: the scan reports hundreds
of source errors - 326 when this was planned, 891 on the day it was built - and
the number counts routes. One fixture carried by three feeds with backups is a
dozen routes, and eleven of them can fail while the match plays. Read as
matches the number destroys the tab; read as routes it may mean nothing
happened. Nobody could tell which, so nobody could act on it.

`reports/fixture-stream-health.json` makes it a sentence: this many fixtures
were touched by a route failure, this many were left with nothing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.fixture_stream_health import (  # noqa: E402
    build_fixture_stream_health,
    fixture_identity,
)


def _route(name, source_id, status, **extra):
    route = {
        "name": name,
        "source_id": source_id,
        "url": "https://%s.example/%s.m3u8" % (source_id, name.lower()[:6]),
        "verification_status": status,
        "verified": status.startswith("verified"),
        "_verification_group": "today_match:%s" % name.lower().replace(" ", "-"),
    }
    route.update(extra)
    return route


def _card(name, source_id, **extra):
    card = {
        "id": name.lower().replace(" ", "-"),
        "fixture_id": "provider:%s|league|2026-09-05" % name.lower().replace(" ", "-"),
        "name": name,
        "source_id": source_id,
    }
    card.update(extra)
    return card


def _rows(report):
    return {row["fixture_key"]: row for row in report["fixtures"]}


class ARouteIsNotAMatch(unittest.TestCase):
    def _report(self):
        """One fixture, eight routes, seven of them dead, and it plays."""
        routes = [_route("Brentford Vs Sunderland", "srhady-bingstream", "failed")
                  for _ in range(5)]
        routes += [_route("Brentford Vs Sunderland", "sm-sports-data", "failed")
                   for _ in range(2)]
        routes.append(_route("Brentford Vs Sunderland", "sm-fancode",
                             "verified_global"))
        card = _card("Brentford Vs Sunderland", "sm-fancode",
                     available_link_count=3,
                     url="https://sm-fancode.example/brentf.m3u8",
                     backups=[{"url": "https://a.example/b.m3u8",
                               "source_id": "srhady-bingstream"}])
        return build_fixture_stream_health(routes, [card], [])

    def test_seven_dead_routes_are_one_fixture_not_seven_lost_matches(self):
        report = self._report()
        self.assertEqual(1, report["totals"]["fixtures"])
        self.assertEqual(7, report["totals"]["failed_routes"])
        self.assertEqual(1, report["totals"]["fixtures_touched_by_a_route_failure"])
        self.assertEqual(0, report["totals"]["fixtures_left_with_nothing"])

    def test_the_row_reconciles_its_own_routes(self):
        row = self._report()["fixtures"][0]
        self.assertEqual(8, row["candidate_stream_count"])
        self.assertEqual(1, row["verified_stream_count"])
        self.assertEqual(7, row["failed_stream_count"])
        self.assertEqual(
            row["candidate_stream_count"],
            row["verified_stream_count"] + row["failed_stream_count"]
            + row["unchecked_stream_count"],
        )

    def test_a_fixture_whose_every_route_died_is_the_one_worth_finding(self):
        routes = [_route("Ajax vs PSV Eindhoven", "srhady-bingstream", "failed")
                  for _ in range(8)]
        report = build_fixture_stream_health(
            routes, [], [],
            fixtures=[{"name": "Ajax vs PSV Eindhoven",
                       "_verification_group": "today_match:ajax-vs-psv-eindhoven"}],
        )
        self.assertEqual(1, report["totals"]["fixtures_left_with_nothing"])
        self.assertEqual(0, report["totals"]["fixtures_with_verified_stream"])


class TheSourceAttributionStaysTrue(unittest.TestCase):
    def test_a_fixture_three_feeds_carried_names_all_three(self):
        routes = [
            _route("Genoa Vs Como", "srhady-bingstream", "failed"),
            _route("Genoa Vs Como", "sm-sports-data", "verified_global"),
            _route("Genoa Vs Como", "sm-fancode", "failed"),
        ]
        card = _card("Genoa Vs Como", "sm-sports-data",
                     url="https://a.example/1.m3u8",
                     backups=[{"url": "https://b.example/2.m3u8",
                               "source_id": "sm-fancode"}])
        row = build_fixture_stream_health(routes, [card], [])["fixtures"][0]
        self.assertEqual("sm-sports-data", row["source_id"])
        self.assertEqual(
            {"srhady-bingstream", "sm-sports-data", "sm-fancode"},
            set(row["source_ids"]),
        )

    def test_the_lead_source_is_the_one_the_card_publishes_under(self):
        routes = [_route("Genoa Vs Como", "srhady-bingstream", "failed")]
        card = _card("Genoa Vs Como", "sm-fancode", url="https://a.example/1.m3u8")
        row = build_fixture_stream_health(routes, [card], [])["fixtures"][0]
        self.assertEqual("sm-fancode", row["source_id"])


class TheJoinFindsTheCard(unittest.TestCase):
    def test_a_candidate_and_the_card_it_became_share_one_row(self):
        """Measured on a real scan: `fixture_id` joined 0 of 214 published
        cards to their routes, the merge slug joined 203."""
        route = _route("Chelsea Vs Arsenal", "srhady-bingstream", "failed")
        card = _card("Chelsea Vs Arsenal", "srhady-bingstream",
                     url="https://a.example/1.m3u8")
        self.assertEqual(fixture_identity(route), fixture_identity(card))
        report = build_fixture_stream_health([route], [card], [])
        self.assertEqual(1, len(report["fixtures"]))
        self.assertTrue(report["fixtures"][0]["published"])

    def test_the_card_id_is_read_when_a_route_has_no_group(self):
        route = {"name": "Chelsea Vs Arsenal", "source_id": "x",
                 "url": "https://a.example/1.m3u8", "verification_status": "failed"}
        card = _card("Chelsea Vs Arsenal", "x", url="https://a.example/1.m3u8")
        self.assertEqual(fixture_identity(route), fixture_identity(card))


class OtherSportsAreNotLostMatches(unittest.TestCase):
    def test_a_discarded_tennis_broadcast_is_kept_out_of_the_totals(self):
        """The sport filter drops tennis on purpose. Its dead routes are real
        and must never be added to a number that reads as lost fixtures."""
        routes = [_route("TENNIS EVENTO", "srhady-bingstream", "failed")
                  for _ in range(18)]
        routes.append(_route("Genoa Vs Como", "sm-fancode", "failed"))
        card = _card("Genoa Vs Como", "sm-fancode", metadata_only=True)
        report = build_fixture_stream_health(routes, [], [card])
        totals = report["totals"]
        self.assertEqual(1, totals["fixtures"])
        self.assertEqual(1, totals["failed_routes"])
        self.assertEqual(1, totals["unrecognised_candidate_groups"])
        self.assertEqual(18, totals["unrecognised_failed_routes"])

    def test_a_recognised_fixture_that_never_published_still_counts(self):
        routes = [_route("Ipswich Vs Liverpool", "sm-sports-data", "failed")]
        recognised = [{"name": "Ipswich Vs Liverpool",
                       "_verification_group": "today_match:ipswich-vs-liverpool"}]
        report = build_fixture_stream_health(routes, [], [], fixtures=recognised)
        self.assertEqual(1, report["totals"]["fixtures"])
        self.assertEqual(1, report["totals"]["fixtures_left_with_nothing"])


class TheCardIsTheAuthorityOnWhatPublished(unittest.TestCase):
    def test_published_without_stream_follows_the_card(self):
        card = _card("Waiting Fixture", "sm-fancode", metadata_only=True)
        row = build_fixture_stream_health([], [], [card])["fixtures"][0]
        self.assertTrue(row["published"])
        self.assertTrue(row["published_without_stream"])
        self.assertEqual("upcoming", row["published_tab"])

    def test_fallback_available_follows_the_published_backups(self):
        with_backup = _card("A Vs B", "x", url="https://a.example/1.m3u8",
                            backups=[{"url": "https://b.example/2.m3u8"}])
        alone = _card("C Vs D", "x", url="https://a.example/3.m3u8", backups=[])
        rows = _rows(build_fixture_stream_health([], [with_backup, alone], []))
        self.assertTrue(rows["a-vs-b"]["fallback_available"])
        self.assertFalse(rows["c-vs-d"]["fallback_available"])

    def test_a_carried_card_with_no_candidates_this_scan_is_still_a_row(self):
        card = _card("Held Over", "x", url="https://a.example/1.m3u8",
                     available_link_count=6,
                     carried_forward_reason="not seen in this scan")
        row = build_fixture_stream_health([], [card], [])["fixtures"][0]
        self.assertEqual(0, row["candidate_stream_count"])
        self.assertFalse(row["published_without_stream"])

    def test_a_metadata_only_candidate_is_not_a_route(self):
        route = _route("A Vs B", "x", "metadata_only", metadata_only=True)
        report = build_fixture_stream_health([route], [], [])
        self.assertEqual(0, report["totals"]["candidate_routes"])


class NothingSensitiveAndNothingChanged(unittest.TestCase):
    def test_no_url_token_or_header_reaches_the_report(self):
        route = _route("A Vs B", "x", "failed",
                       url="https://cdn.example/live.m3u8?play_token=SECRET",
                       headers={"Cookie": "session=SECRET"})
        card = _card("A Vs B", "x",
                     url="https://cdn.example/live.m3u8?play_token=SECRET")
        report = build_fixture_stream_health([route], [card], [])
        self.assertNotIn("SECRET", str(report))
        self.assertNotIn("m3u8", str(report))

    def test_the_row_carries_the_fields_final_asks_for(self):
        card = _card("A Vs B", "x", url="https://a.example/1.m3u8")
        row = build_fixture_stream_health([], [card], [])["fixtures"][0]
        for field in ("fixture_id", "source_id", "candidate_stream_count",
                      "verified_stream_count", "failed_stream_count",
                      "failure_codes", "fallback_available",
                      "published_without_stream"):
            self.assertIn(field, row)

    def test_the_builder_does_not_touch_the_cards_it_reads(self):
        card = _card("A Vs B", "x", url="https://a.example/1.m3u8")
        before = dict(card)
        build_fixture_stream_health([], [card], [])
        self.assertEqual(before, card)


if __name__ == "__main__":
    unittest.main()
