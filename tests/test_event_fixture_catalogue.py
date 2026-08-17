"""config/event-fixtures.json is fixture authority. Guard its data.

Fixture authority decides what may become a public event card, so a wrong entry
in this file does not produce a wrong card - it produces no card, silently, and
takes every broadcaster for that match with it. There is no runtime error to
notice. These tests are the noticing.

The specific shape that motivated them: a multi-day Test whose window is one
day. `duration_minutes` is a single block, so a five-day Test modelled by
duration alone appears to end eight hours after the first ball, and from day two
onward enrich_event_candidates()' historical guard refuses every stream that
mentions it. The catalogue avoids this by stating an explicit `end` per Test
fixture - which is correct today, and is what test_a_multi_day_format_spans_more
_than_one_day keeps correct.
"""

import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.schedule_resolver import (  # noqa: E402
    _competition_matches,
    load_fixtures,
    team_pair_key,
)

CATALOGUE = ROOT / "config" / "event-fixtures.json"
RAW = json.loads(CATALOGUE.read_text(encoding="utf-8"))
COMPETITIONS = RAW.get("competitions") or []
FIXTURES = load_fixtures(CATALOGUE)

# Formats played over more than one calendar day. A fixture in one of these
# competitions must say so, because duration_minutes cannot.
MULTI_DAY_MARKERS = re.compile(r"(?i)\b(?:test|tests)\b")


class TheCatalogueIsWellFormed(unittest.TestCase):
    def test_it_parses_and_is_not_empty(self):
        self.assertIsInstance(RAW, dict)
        self.assertTrue(COMPETITIONS, "no competitions in the catalogue")
        self.assertTrue(FIXTURES, "no fixtures loaded from the catalogue")

    def test_every_competition_has_the_fields_the_loader_reads(self):
        for competition in COMPETITIONS:
            with self.subTest(competition.get("id")):
                self.assertTrue(str(competition.get("id") or "").strip())
                self.assertTrue(str(competition.get("name") or "").strip())
                self.assertTrue(competition.get("fixtures")
                                or competition.get("double_headers"),
                                "a competition with no fixtures cannot match anything")

    def test_competition_ids_are_unique(self):
        ids = [str(c.get("id") or "") for c in COMPETITIONS]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_every_timezone_resolves(self):
        for competition in COMPETITIONS:
            name = str(competition.get("timezone") or "")
            if not name:
                continue
            with self.subTest(competition.get("id"), timezone=name):
                try:
                    ZoneInfo(name)
                except ZoneInfoNotFoundError:
                    # A tzdata-less runner is allowed, but only if the entry also
                    # carries the literal offset the loader falls back to.
                    self.assertTrue(
                        str(competition.get("utc_offset") or "").strip(),
                        f"{name} is unknown here and no utc_offset is given",
                    )

    def test_fixture_ids_are_unique(self):
        ids = [fixture["fixture_id"] for fixture in FIXTURES]
        duplicates = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(duplicates, set(), duplicates)


class EveryFixtureWindowIsUsable(unittest.TestCase):
    """A window that is backwards, empty or absurd silently drops the fixture."""

    def test_every_fixture_ends_after_it_starts(self):
        for fixture in FIXTURES:
            with self.subTest(fixture["name"]):
                self.assertGreater(fixture["end"], fixture["start"])

    def test_no_window_is_shorter_than_a_sporting_event(self):
        for fixture in FIXTURES:
            with self.subTest(fixture["name"]):
                self.assertGreaterEqual(
                    fixture["end"] - fixture["start"], timedelta(minutes=30),
                    "too short to cover any real fixture",
                )

    def test_no_window_runs_away(self):
        """A stray end turns one fixture into a permanent live event."""
        for fixture in FIXTURES:
            with self.subTest(fixture["name"]):
                self.assertLessEqual(
                    fixture["end"] - fixture["start"], timedelta(days=7),
                    "no single fixture lasts a week",
                )

    def test_every_window_is_timezone_aware(self):
        for fixture in FIXTURES:
            with self.subTest(fixture["name"]):
                self.assertIsNotNone(fixture["start"].tzinfo)
                self.assertIsNotNone(fixture["end"].tzinfo)
                self.assertEqual(fixture["start"].utcoffset(), timedelta(0),
                                 "load_fixtures must normalise to UTC")

    def test_a_multi_day_format_spans_more_than_one_day(self):
        """The defect this module exists for.

        duration_minutes is one block of play. A Test needs its own end, or the
        catalogue believes the match finished on day one and every broadcaster
        for it is refused from day two onwards.
        """
        checked = 0
        for competition in COMPETITIONS:
            for entry in competition.get("fixtures") or []:
                name = str(entry.get("name") or "")
                if not MULTI_DAY_MARKERS.search(name):
                    continue
                checked += 1
                with self.subTest(name):
                    self.assertTrue(
                        str(entry.get("end") or "").strip(),
                        "a Test fixture must state its own end; "
                        "duration_minutes cannot express five days",
                    )
                    record = next(f for f in FIXTURES if f["name"] == name)
                    self.assertGreaterEqual(
                        record["end"] - record["start"], timedelta(days=2),
                        "a Test window must span more than a single day's play",
                    )
        self.assertGreater(checked, 0, "no Test fixtures found to check")

    def test_a_single_day_format_stays_inside_one_day(self):
        for competition in COMPETITIONS:
            for entry in competition.get("fixtures") or []:
                name = str(entry.get("name") or "")
                if MULTI_DAY_MARKERS.search(name):
                    continue
                record = next((f for f in FIXTURES if f["name"] == name), None)
                if record is None:
                    continue
                with self.subTest(name):
                    self.assertLess(
                        record["end"] - record["start"], timedelta(days=1),
                        "a one-day fixture should not hold a multi-day window",
                    )


class TheCatalogueCannotOverReach(unittest.TestCase):
    """An alias or a placeholder name that matches too much is worse than a gap."""

    def test_no_alias_is_a_bare_sport_or_category(self):
        forbidden = {"cricket", "football", "soccer", "tennis", "sports", "sport",
                     "live", "live tv", "match", "matches", "event", "events", "tv"}
        for competition in COMPETITIONS:
            for alias in competition.get("aliases") or []:
                with self.subTest(competition.get("id"), alias=alias):
                    self.assertNotIn(str(alias).strip().casefold(), forbidden)

    def test_no_alias_is_too_short_to_mean_one_competition(self):
        for competition in COMPETITIONS:
            for alias in competition.get("aliases") or []:
                with self.subTest(competition.get("id"), alias=alias):
                    self.assertGreaterEqual(len(str(alias).strip()), 4, alias)

    def test_no_fixture_names_the_same_side_twice(self):
        """"Cpl T20 Vs Cpl T20" is a placeholder, not a fixture. If one ever
        reached the catalogue it would claim every match in the competition.

        Compared on the raw title rather than on team_pair_key: the key strips
        competition words, and for a title made only of competition words it
        strips both sides down to nothing and returns "" - so keying on it would
        skip the very entry this test is looking for.
        """
        separator = re.compile(r"(?i)\s+(?:versus|vs\.?|v\.?)\s+")
        for fixture in FIXTURES:
            parts = separator.split(str(fixture["name"]), maxsplit=1)
            if len(parts) != 2:
                continue
            left = " ".join(parts[0].split()).casefold()
            right = " ".join(parts[1].split()).casefold()
            with self.subTest(fixture["name"]):
                self.assertNotEqual(left, right,
                                    "both sides are the same - placeholder name")
                self.assertTrue(team_pair_key(fixture["name"]),
                                "the participants cannot be read out of this name, "
                                "so no stream can ever be attached to it")

    def test_a_competition_alias_does_not_match_another_competition(self):
        for competition in COMPETITIONS:
            others = [c for c in COMPETITIONS if c is not competition]
            for other in others:
                for entry in (other.get("fixtures") or [])[:4]:
                    name = str(entry.get("name") or "")
                    if not name:
                        continue
                    record = next((f for f in FIXTURES
                                   if f["competition_id"] == str(competition.get("id"))),
                                  None)
                    if record is None:
                        continue
                    with self.subTest(alias_of=competition.get("id"), name=name[:40]):
                        # A fixture of one competition must not be claimed by
                        # another competition's aliases.
                        if _competition_matches(name, record):
                            self.assertEqual(
                                str(other.get("id")), str(competition.get("id")),
                                f"{competition.get('id')} aliases also match "
                                f"{other.get('id')}'s fixture {name!r}",
                            )


class TheCatalogueStillDescribesReality(unittest.TestCase):
    """Cheap staleness signals. These are warnings made into assertions only
    where a wrong answer would actively mislead."""

    def test_no_fixture_sits_absurdly_far_in_the_past(self):
        """A catalogue that only holds last season cannot verify anything."""
        newest = max(fixture["end"] for fixture in FIXTURES)
        self.assertGreater(
            newest, datetime(2026, 1, 1, tzinfo=timezone.utc),
            "every fixture in the catalogue predates 2026",
        )

    def test_the_catalogue_covers_a_window_rather_than_a_single_day(self):
        span = (max(f["end"] for f in FIXTURES) - min(f["start"] for f in FIXTURES))
        self.assertGreaterEqual(span, timedelta(days=3), span)

    def test_every_competition_records_where_its_schedule_came_from(self):
        for competition in COMPETITIONS:
            with self.subTest(competition.get("id")):
                self.assertTrue(
                    str(competition.get("source_url") or "").strip(),
                    "a fixture-authority entry must say what it was taken from",
                )


if __name__ == "__main__":
    unittest.main()
