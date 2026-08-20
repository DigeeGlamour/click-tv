"""Why sports posters went missing, and what stops it recurring.

Measured on production data, 2026-08-20: 37 of 118 published Upcoming cards
had no logo. Every API was configured, wired and reachable - the causes were
elsewhere:

1.  `THESPORTSDB_API_KEY` unset makes every lookup fall back to the published
    open test key. The first calls returned full artwork; after roughly forty
    the host answered HTTP 429 to everything, including queries that had just
    succeeded. `_get_json` swallowed every error into `{}`, so a rate-limited
    scan was indistinguishable from "this fixture has no artwork" - and nothing
    in any report said which had happened.
2.  The card path read only poster/thumbnail/banner for the logo, although the
    module documents the priority as "Poster -> Thumbnail -> Fanart -> Team
    Logos". A fixture whose only artwork was the two team badges filled
    `home_badge_url` and still published with no logo.
3.  Team names went to the provider verbatim. "FC Sion vs Ajax", "FC Lugano vs
    Maccabi Tel Aviv", "Rangers vs FK Jablonec", "FC Midtjylland vs HNK Rijeka"
    and "Motherwell vs SC Freiburg" all missed on the affix alone.
4.  Nothing was cached, so each scan spent the whole quota re-asking for
    fixtures it had already resolved.

Running the same 37 cards through the patched path took 3 logos to 9 while the
key was already rate-limited, and a second pass answered from cache in 0.1s
instead of 10.5s.
"""

from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import sports_poster_providers as providers


class RateLimitCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        providers.reset_lookup_stats()

    def tearDown(self):
        providers.reset_lookup_stats()

    @staticmethod
    def _http_error(code):
        return urllib.error.HTTPError(
            "https://www.thesportsdb.com/x", code, "err", {}, None
        )

    def test_two_rate_limited_replies_stop_the_host_being_asked_again(self):
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            raise self._http_error(429)

        with mock.patch.object(providers.urllib.request, "urlopen", fake_urlopen):
            for _ in range(6):
                providers._get_json("https://www.thesportsdb.com/api/x")

        self.assertEqual(len(calls), 2, "the host must be asked twice, not six times")
        self.assertEqual(providers.LOOKUP_STATS["rate_limited"], 2)
        self.assertEqual(providers.LOOKUP_STATS["skipped_rate_limited"], 4)
        self.assertTrue(
            providers.provider_is_rate_limited("https://www.thesportsdb.com/api/y")
        )

    def test_one_rate_limited_reply_does_not_trip_the_breaker(self):
        state = {"n": 0}

        class Response:
            status = 200
            def read(self, *_): return b'{"event": []}'
            def __enter__(self): return self
            def __exit__(self, *_): return False

        def fake_urlopen(request, timeout=None):
            state["n"] += 1
            if state["n"] == 1:
                raise RateLimitCircuitBreakerTests._http_error(429)
            return Response()

        with mock.patch.object(providers.urllib.request, "urlopen", fake_urlopen):
            providers._get_json("https://www.thesportsdb.com/api/x")
            providers._get_json("https://www.thesportsdb.com/api/x")

        self.assertEqual(state["n"], 2)
        self.assertFalse(
            providers.provider_is_rate_limited("https://www.thesportsdb.com/api/x")
        )

    def test_a_normal_http_error_is_counted_separately_from_a_rate_limit(self):
        with mock.patch.object(
            providers.urllib.request, "urlopen",
            lambda request, timeout=None: (_ for _ in ()).throw(self._http_error(500)),
        ):
            providers._get_json("https://www.thesportsdb.com/api/x")
        self.assertEqual(providers.LOOKUP_STATS["errors"], 1)
        self.assertEqual(providers.LOOKUP_STATS["rate_limited"], 0)

    def test_the_breaker_is_per_host(self):
        with mock.patch.object(
            providers.urllib.request, "urlopen",
            lambda request, timeout=None: (_ for _ in ()).throw(self._http_error(429)),
        ):
            providers._get_json("https://www.thesportsdb.com/a")
            providers._get_json("https://www.thesportsdb.com/b")
        self.assertTrue(providers.provider_is_rate_limited("https://www.thesportsdb.com/c"))
        self.assertFalse(providers.provider_is_rate_limited("https://soccer.highlightly.net/x"))


class ClubAffixRetryTests(unittest.TestCase):
    def test_the_affixes_that_actually_failed_are_stripped(self):
        cases = {
            "FC Sion": "Sion",
            "FC Lugano": "Lugano",
            "FK Jablonec": "Jablonec",
            "FC Midtjylland": "Midtjylland",
            "HNK Rijeka": "Rijeka",
            "SC Freiburg": "Freiburg",
            "Al Hilal Saudi FC": "Al Hilal Saudi",
            "Lincoln Red Imps FC": "Lincoln Red Imps",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(providers._strip_club_affixes(raw), expected)

    def test_a_name_without_an_affix_is_untouched(self):
        for name in ("Motherwell", "Ajax", "Monaco", "Red Bull Salzburg"):
            with self.subTest(name=name):
                self.assertEqual(providers._strip_club_affixes(name), name)

    def test_a_bare_affix_is_never_stripped_to_nothing(self):
        self.assertEqual(providers._strip_club_affixes("FC"), "FC")
        self.assertEqual(providers._strip_club_affixes(""), "")

    def test_the_retry_only_fires_when_the_verbatim_pair_missed(self):
        seen = []

        def fake(home, away):
            seen.append((home, away))
            return {"poster": "p"} if home == "Sion" else {}

        with mock.patch.object(providers, "thesportsdb_event_artwork", fake):
            got = providers.thesportsdb_event_artwork_with_retry("FC Sion", "Ajax")
        self.assertEqual(got, {"poster": "p"})
        self.assertEqual(seen, [("FC Sion", "Ajax"), ("Sion", "Ajax")])

    def test_no_second_request_when_the_first_one_answered(self):
        seen = []

        def fake(home, away):
            seen.append((home, away))
            return {"poster": "p"}

        with mock.patch.object(providers, "thesportsdb_event_artwork", fake):
            providers.thesportsdb_event_artwork_with_retry("FC Sion", "Ajax")
        self.assertEqual(len(seen), 1)

    def test_no_second_request_when_stripping_changes_nothing(self):
        seen = []

        def fake(home, away):
            seen.append((home, away))
            return {}

        with mock.patch.object(providers, "thesportsdb_event_artwork", fake):
            providers.thesportsdb_event_artwork_with_retry("Motherwell", "Ajax")
        self.assertEqual(len(seen), 1)


class BadgeAsLogoTests(unittest.TestCase):
    """A fixture whose only artwork is a team badge must still get a logo."""

    def _run(self, artwork, tmpdir):
        from scanner import events

        card = {
            "name": "St Kitts and Nevis Patriots vs Jamaica Kingsmen",
            "logo": "",
            "sport_type": "cricket",
            "start_time": "2026-08-21T00:30:00+00:00",
        }
        with mock.patch(
            "scanner.sports_poster_providers.thesportsdb_event_artwork_with_retry",
            lambda home, away: artwork,
        ), mock.patch(
            "scanner.sports_poster_providers.highlightly_match_artwork",
            lambda *a, **k: {},
        ):
            stats = events._apply_supplementary_sports_artwork(
                [card],
                cache_path=Path(tmpdir) / "sports-artwork-cache.json",
            )
        return card, stats

    def test_a_badge_only_fixture_now_gets_a_logo(self):
        import tempfile

        card, stats = self._run(
            {"home_badge": "https://x/home.png", "away_badge": "https://x/away.png"},
            tempfile.mkdtemp(),
        )
        self.assertEqual(card["logo"], "https://x/home.png")
        self.assertEqual(stats["badge_used_as_logo"], 1)
        self.assertEqual(card["home_badge_url"], "https://x/home.png")
        self.assertEqual(card["away_badge_url"], "https://x/away.png")

    def test_a_real_poster_still_wins_over_a_badge(self):
        import tempfile

        card, stats = self._run(
            {
                "poster": "https://x/poster.png",
                "home_badge": "https://x/home.png",
            },
            tempfile.mkdtemp(),
        )
        self.assertEqual(card["logo"], "https://x/poster.png")
        self.assertEqual(stats["badge_used_as_logo"], 0)

    def test_thumbnail_and_banner_stay_ahead_of_a_badge(self):
        import tempfile

        card, _ = self._run(
            {"thumbnail": "https://x/thumb.png", "home_badge": "https://x/home.png"},
            tempfile.mkdtemp(),
        )
        self.assertEqual(card["logo"], "https://x/thumb.png")
        card, _ = self._run(
            {"banner": "https://x/banner.png", "home_badge": "https://x/home.png"},
            tempfile.mkdtemp(),
        )
        self.assertEqual(card["logo"], "https://x/banner.png")


class ArtworkStatsAreReportedTests(unittest.TestCase):
    def test_the_provider_counters_exist_and_reset(self):
        providers.LOOKUP_STATS["requests"] = 7
        providers.reset_lookup_stats()
        self.assertEqual(
            set(providers.LOOKUP_STATS),
            {"requests", "hits", "empty", "rate_limited", "errors", "skipped_rate_limited"},
        )
        self.assertEqual(sum(providers.LOOKUP_STATS.values()), 0)

    def test_events_writes_the_schedule_report(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn('"reports") / "event-schedule.json"', source)
        self.assertIn("upcoming_missing_logo", source)

    def test_events_records_the_provider_block_in_its_stats(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn('stats["provider"] = dict(LOOKUP_STATS)', source)
        self.assertIn("reset_lookup_stats()", source)

    def test_the_artwork_cache_path_is_under_state_and_injectable(self):
        from scanner import events

        self.assertEqual(events.ARTWORK_CACHE_PATH.name, "sports-artwork-cache.json")
        self.assertEqual(events.ARTWORK_CACHE_PATH.parent.name, "state")
        self.assertGreater(events.ARTWORK_CACHE_MAX_ENTRIES, 0)
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("cache_path: Optional[Path] = None", source)


if __name__ == "__main__":
    unittest.main()
