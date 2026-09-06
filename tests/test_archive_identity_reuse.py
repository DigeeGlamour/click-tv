"""P50 CORRECTION - the archive recognises a fixture the way the tabs do.

Measured on real data, 2026-09-05. `provider:brighton-vs-leeds|premier
league|2026-09-05` was archived as ended at 14:52:04 with `ended_seen_at
14:25:20`, and at the same time `Brighton Hove Albion Vs Leeds United` from a
second feed was live on Today. Same match, same kickoff, two spellings, two
provider ids - and `is_archived()` was a dict lookup on one string, so it could
only ever recognise the identity it had stored.

FINAL_2's identity rule is two-tier: the provider fixture id, and failing that
normalized teams + competition + kickoff bucket. The second tier already exists
in this repository, twice - `fixture_dedupe.same_fixture` decides whether two
published cards are one, and `merger.same_real_fixture` is the merge layer's
own verdict - so the archive asks them and forms no opinion of its own. No new
matcher and no loosened rule: whatever the pipeline calls one fixture, the
archive now calls one retirement, and whatever it calls two, the archive lets
through.

Both helpers require a compatible kickoff, which is what keeps a rematch
between the same two teams admissible.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import event_archive as ea  # noqa: E402
from scanner import fixture_dedupe  # noqa: E402
from scanner.merger import same_real_fixture  # noqa: E402

NOW = datetime(2026, 9, 5, 16, 0, tzinfo=timezone.utc)
KICKOFF = "2026-09-05T14:00:00+00:00"


def _card(name, *, fixture_id=None, competition="English Premier League",
          start=KICKOFF, sport="football", **extra):
    slug = name.casefold().replace(" ", "-")
    card = {
        "id": slug,
        "fixture_id": fixture_id or "provider:%s|%s|%s" % (
            slug, competition.casefold(), start[:10]),
        "name": name,
        "competition": competition,
        "sport_type": sport,
        "start_time": start,
        "lifecycle_state": "ENDED",
    }
    card.update(extra)
    return card


def _archive_with(*cards):
    archive = {"fixtures": {}}
    ea.archive_retired(list(cards), now=NOW, archive=archive)
    return archive


class TheStoredIdentityStillWorks(unittest.TestCase):
    """Tier one, unchanged: the provider fixture id, one lookup."""

    def test_the_same_provider_id_is_blocked(self):
        ended = _card("Brighton vs Leeds")
        archive = _archive_with(ended)
        self.assertTrue(ea.is_archived(dict(ended), archive))

    def test_both_tabs_refuse_it(self):
        ended = _card("Brighton vs Leeds")
        archive = _archive_with(ended)
        for tab in ("today", "upcoming"):
            kept, dropped = ea.drop_resurrected([dict(ended)], archive)
            self.assertEqual([], kept, tab)
            self.assertEqual(["Brighton vs Leeds"], dropped, tab)

    def test_an_empty_archive_blocks_nothing(self):
        self.assertFalse(ea.is_archived(_card("Brighton vs Leeds"),
                                        {"fixtures": {}}))


class TheArchiveAgreesWithTheDedupeLayer(unittest.TestCase):
    """Tier two: whatever the tabs would fold, the archive treats as retired."""

    def _pair(self, left_name, right_name, **right):
        ended = _card(left_name, competition="Premier League")
        returning = _card(right_name, **right)
        return ended, returning

    def test_a_second_source_spelling_is_recognised(self):
        """`fixture_dedupe.same_fixture`'s own rule: one side identical, the
        other merely spelled longer - "Leeds" and "Leeds United"."""
        ended, returning = self._pair("Brighton vs Leeds",
                                      "Brighton vs Leeds United")
        self.assertTrue(fixture_dedupe.same_fixture(ended, returning))
        archive = _archive_with(ended)
        self.assertTrue(ea.is_archived(returning, archive))

    def test_the_sides_the_other_way_round_are_recognised(self):
        ended, returning = self._pair("Real Sociedad vs RC Celta",
                                      "RC Celta vs Real Sociedad")
        self.assertTrue(fixture_dedupe.same_fixture(ended, returning))
        archive = _archive_with(ended)
        self.assertTrue(ea.is_archived(returning, archive))

    def test_the_archive_and_the_dedupe_layer_never_disagree(self):
        """The point of the correction. Every verdict below is the pipeline's,
        not the archive's - including the ones that say "different"."""
        ended = _card("Brighton vs Leeds", competition="Premier League")
        archive = _archive_with(ended)
        candidates = [
            _card("Brighton vs Leeds United"),
            _card("Brighton Hove Albion Vs Leeds United"),
            _card("Manchester United vs Leeds"),
            _card("Brighton vs Leeds", start="2026-11-20T15:00:00+00:00"),
            _card("Brighton vs Leeds", competition="FA Cup"),
        ]
        for candidate in candidates:
            pipeline = (fixture_dedupe.same_fixture(ended, candidate)
                        or same_real_fixture(ended, candidate))
            with self.subTest(name=candidate["name"],
                              competition=candidate["competition"],
                              start=candidate["start_time"]):
                self.assertEqual(pipeline, ea.is_archived(candidate, archive))

    def test_no_new_matcher_was_written(self):
        """The archive asks; it does not decide."""
        source = (Path(__file__).resolve().parents[1] / "scanner"
                  / "event_archive.py").read_text(encoding="utf-8")
        self.assertIn("same_fixture", source)
        self.assertIn("same_real_fixture", source)
        for invented in ("difflib", "SequenceMatcher", "ratio(", "levenshtein",
                         "fuzz"):
            self.assertNotIn(invented, source.casefold())


class ARealFutureFixtureIsNeverBlocked(unittest.TestCase):
    def test_the_same_two_teams_on_another_date_are_admitted(self):
        ended = _card("Brighton vs Leeds")
        archive = _archive_with(ended)
        rematch = _card("Brighton vs Leeds", start="2026-11-20T15:00:00+00:00")
        self.assertFalse(ea.is_archived(rematch, archive))
        kept, dropped = ea.drop_resurrected([rematch], archive)
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_the_same_two_teams_in_another_competition_are_admitted(self):
        """Reliable, different competition evidence - a cup tie is not the
        league fixture, even at a kickoff the tolerance would accept."""
        ended = _card("Brighton vs Leeds", competition="English Premier League")
        archive = _archive_with(ended)
        cup = _card("Brighton vs Leeds", competition="FA Cup",
                    start="2026-09-05T14:05:00+00:00")
        self.assertFalse(ea.is_archived(cup, archive))

    def test_a_different_opponent_is_admitted(self):
        ended = _card("Brighton vs Leeds")
        archive = _archive_with(ended)
        self.assertFalse(ea.is_archived(_card("Brighton vs Arsenal"), archive))

    def test_a_card_with_no_kickoff_is_never_blocked_by_tier_two(self):
        """Every helper the second tier uses needs a kickoff. Without one the
        archive stops at the stored identity rather than guessing."""
        ended = _card("Brighton vs Leeds")
        archive = _archive_with(ended)
        homeless = {"id": "brighton-vs-leeds-united",
                    "name": "Brighton vs Leeds United"}
        self.assertFalse(ea.is_archived(homeless, archive))


class TheEntryStaysThinAndCompatible(unittest.TestCase):
    def test_the_entry_carries_identity_evidence_and_nothing_else(self):
        fat = _card("Brighton vs Leeds",
                    channels=[{"id": "c1", "name": "Sky"}],
                    backups=["https://a.example/1.m3u8"],
                    logo="https://a.example/logo.png",
                    source_provenance=[{"source_id": "x"}])
        archive = _archive_with(fat)
        entry = next(iter(archive["fixtures"].values()))
        self.assertEqual(
            {"id", "fixture_id", "name", "competition", "sport_type",
             "start_time", "ended_seen_at", "lifecycle_state", "archived_at"},
            set(entry))

    def test_an_entry_written_before_this_change_still_blocks(self):
        """Backward compatible: the old seven-field row has no competition, and
        a blank competition is a wildcard to the merge layer rather than a
        contradiction. Nothing is rewritten or migrated."""
        old = {"fixtures": {"provider:brighton-vs-leeds|premier league|2026-09-05": {
            "id": "brighton-vs-leeds",
            "fixture_id": "provider:brighton-vs-leeds|premier league|2026-09-05",
            "name": "Brighton vs Leeds",
            "start_time": KICKOFF,
            "ended_seen_at": "2026-09-05T14:25:20+00:00",
            "lifecycle_state": "END_PENDING",
            "archived_at": "2026-09-05T14:52:04+00:00",
        }}}
        exact = _card("Brighton vs Leeds",
                      fixture_id="provider:brighton-vs-leeds|premier league|2026-09-05")
        self.assertTrue(ea.is_archived(exact, old))
        self.assertTrue(ea.is_archived(_card("Brighton vs Leeds United"), old))
        self.assertFalse(ea.is_archived(
            _card("Brighton vs Leeds", start="2026-11-20T15:00:00+00:00"), old))

    def test_a_malformed_entry_is_stepped_over(self):
        archive = {"fixtures": {"broken": "not a dict",
                                "empty": {"name": "", "start_time": ""}}}
        self.assertFalse(ea.is_archived(_card("Brighton vs Leeds"), archive))

    def test_repeated_scans_do_not_grow_the_archive(self):
        ended = _card("Brighton vs Leeds")
        archive = {"fixtures": {}}
        for _ in range(7):
            ea.archive_retired([dict(ended)], now=NOW, archive=archive)
        self.assertEqual(1, len(archive["fixtures"]))

    def test_a_recognised_second_spelling_does_not_add_a_second_row(self):
        """It is one fixture, so retiring it twice is one retirement."""
        archive = _archive_with(_card("Brighton vs Leeds"))
        ea.archive_retired([_card("Brighton vs Leeds United")], now=NOW,
                           archive=archive)
        self.assertEqual(2, len(archive["fixtures"]),
                         "archive_retired keys by identity; is_archived is what "
                         "keeps the fixture off the tabs")
        self.assertTrue(ea.is_archived(_card("Brighton vs Leeds United"), archive))

    def test_it_round_trips_through_the_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "event-archive.json"
            ea.archive_retired([_card("Brighton vs Leeds")], now=NOW, path=path)
            reloaded = ea.load_archive(path)
            self.assertTrue(ea.is_archived(_card("Brighton vs Leeds United"),
                                           reloaded))
            self.assertEqual(1, json.loads(path.read_text(encoding="utf-8"))["count"])


class NothingAboutGroupingChanged(unittest.TestCase):
    def test_the_archive_still_copies_no_channel_or_stream_data(self):
        fat = _card("Brighton vs Leeds",
                    channels=[{"id": "c1", "name": "Sky Sports"}],
                    backups=[{"url": "https://a.example/1.m3u8",
                              "source_id": "x"}])
        archive = _archive_with(fat)
        blob = json.dumps(archive)
        for leaked in ("Sky Sports", "m3u8", "channels", "backups"):
            self.assertNotIn(leaked, blob)

    def test_the_dedupe_helpers_are_read_and_not_modified(self):
        """The archive imports the verdicts; it must not reach into them."""
        source = (Path(__file__).resolve().parents[1] / "scanner"
                  / "event_archive.py").read_text(encoding="utf-8")
        for mutation in ("KICKOFF_TOLERANCE_MINUTES =", "same_side =",
                         "_bare =", "MIN_TRUNCATION"):
            self.assertNotIn(mutation, source)

    def test_drop_resurrected_still_reports_what_it_dropped(self):
        archive = _archive_with(_card("Brighton vs Leeds"))
        kept, dropped = ea.drop_resurrected(
            [_card("Brighton vs Leeds United"), _card("Arsenal vs Chelsea")],
            archive)
        self.assertEqual(["Arsenal vs Chelsea"], [c["name"] for c in kept])
        self.assertEqual(["Brighton vs Leeds United"], dropped)


if __name__ == "__main__":
    unittest.main()
