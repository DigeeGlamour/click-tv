"""ধাপ ৬ / D-03 - the poster policy, applied rather than only classified.

`poster_validity.py` has classified artwork as ok / dead / unknown for a while.
ধারা ৩ says the gap was never the classification - it was "চূড়ান্ত poster
নির্বাচনে প্রয়োগ", applying it when the final poster is chosen. Measured on the
real catalogue while writing this:

    state/movie-poster-validity.json    208 verdicts, every one `ok`
    srhady-live-stream.hf.space among them        0
    published posters on that host            1,197 of 1,667

Those 1,197 were never verified. They held first place because the resolver
returned the feed's `logo` the moment its verdict was "not DEAD", and "not
DEAD" includes "never probed" - so nothing could ever displace them. The fix is
to rank rather than take the first thing that is not disqualified.

What is deliberately NOT done
-----------------------------
They are not retired. `poster_validity` records a measured decision that a 403
is vantage-shaped - the same URL answers 403 from Bangladesh and 200 with real
JPEG bytes from a GitHub runner - and the page already swaps in the designed
placeholder per viewer on the img error event. Blanking artwork on one
network's word removes a poster from every viewer who *can* see it. ধারা ৮ asks
for verified artwork coverage, which is raised by finding a verified
alternative, not by deleting an unverified one.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movies as M  # noqa: E402
from scanner import poster_validity as PV  # noqa: E402

JPEG = b"\xff\xd8\xff" + b"\x00" * 64

SOURCE_URL = "https://srhady-live-stream.hf.space/poster/gargi.jpg"
VERIFIED_URL = "https://image.tmdb.org/t/p/w780/gargi.jpg"
DEAD_URL = "https://dead.test/gone.jpg"


class _Response:
    def __init__(self, status, payload, content_type):
        self.status = status
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def read(self, _size=None):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _opener(script):
    """A probe that answers only for URLs the test scripted.

    Anything unscripted raises, which is how "never probed" is modelled - the
    validator turns that into UNKNOWN, exactly as an unreachable host does.
    """
    def opener(request, timeout=None):  # noqa: ARG001
        url = getattr(request, "full_url", str(request))
        if url not in script:
            # Transport failure, which the validator reads as UNKNOWN - the
            # state 1,197 of this catalogue's posters are actually in.
            raise OSError("not scripted")
        status, payload, content_type = script[url]
        if status >= 400:
            # An HTTP status, not a transport error: 404 is about the resource
            # and is the only kind of refusal allowed to retire artwork.
            raise urllib.error.HTTPError(url, status, "no", {}, None)
        return _Response(status, payload, content_type)
    return opener


class PosterRankingTests(unittest.TestCase):
    """Rank the candidates; do not take the first one that is not dead."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _resolve(self, movie, script, *, cache=None, tmdb="", supplementary=""):
        validator = PV.PosterValidator(
            cache_path=self.root / "validity.json", opener=_opener(script))
        original_tmdb = M._tmdb_poster_lookup
        original_supplementary = M.supplementary_poster_lookup
        M._tmdb_poster_lookup = lambda *_a, **_k: tmdb
        M.supplementary_poster_lookup = lambda *_a, **_k: supplementary
        try:
            counters: dict = {}
            resolved = M._resolve_published_poster(
                movie, validator=validator,
                cache=cache if cache is not None else {},
                generated_posters={}, clean_name=movie.get("name", ""),
                year=int(movie.get("year") or 0), counters=counters,
            )
            return resolved, counters
        finally:
            M._tmdb_poster_lookup = original_tmdb
            M.supplementary_poster_lookup = original_supplementary

    def test_a_verified_alternative_beats_an_unverified_source_logo(self) -> None:
        """The D-03 fix in one test.

        The source logo is not dead - it is simply unprobed, which is the state
        1,197 of this catalogue's posters are in. Before this change it won on
        arrival; now a poster that is proven to load wins.
        """
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": SOURCE_URL},
            {VERIFIED_URL: (200, JPEG, "image/jpeg")},
            tmdb=VERIFIED_URL,
        )
        self.assertEqual(resolved, VERIFIED_URL)
        self.assertEqual(counters["verified"], 1)
        self.assertEqual(counters["recovered_tmdb"], 1)
        self.assertEqual(counters.get("kept", 0), 0)

    def test_an_unverified_source_logo_is_still_published_when_nothing_better_exists(self) -> None:
        """No blanking. A 403 is vantage-shaped and the page already draws the
        placeholder per viewer; removing the URL takes it from everybody who
        can load it."""
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": SOURCE_URL}, {})
        self.assertEqual(resolved, SOURCE_URL)
        self.assertEqual(counters["kept"], 1)
        self.assertEqual(counters["unverified"], 1)
        self.assertEqual(counters.get("verified", 0), 0)

    def test_a_verified_source_logo_keeps_its_place(self) -> None:
        """A source whose artwork does load is not displaced for being a
        source - the ranking is about verification, not provenance."""
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": SOURCE_URL},
            {SOURCE_URL: (200, JPEG, "image/jpeg"),
             VERIFIED_URL: (200, JPEG, "image/jpeg")},
            tmdb=VERIFIED_URL,
        )
        # The cache tier outranks the source logo, so TMDB's verified answer
        # is taken - but the source was verified too, and neither is a loss.
        self.assertIn(resolved, {SOURCE_URL, VERIFIED_URL})
        self.assertEqual(counters["verified"], 1)

    def test_a_dead_source_logo_is_never_published(self) -> None:
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (404, b"", ""), VERIFIED_URL: (200, JPEG, "image/jpeg")},
            tmdb=VERIFIED_URL,
        )
        self.assertEqual(resolved, VERIFIED_URL)
        self.assertEqual(counters["dropped_dead"], 1)

    def test_a_dead_source_logo_is_counted_even_when_replaced(self) -> None:
        """How many were retired, not how many were retired and left blank."""
        _, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (404, b"", ""), VERIFIED_URL: (200, JPEG, "image/jpeg")},
            tmdb=VERIFIED_URL,
        )
        self.assertEqual(counters["dropped_dead"], 1)
        self.assertEqual(counters.get("blank", 0), 0)

    def test_a_dead_logo_with_no_replacement_publishes_nothing(self) -> None:
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (404, b"", "")})
        self.assertEqual(resolved, "")
        self.assertEqual(counters["blank"], 1)
        self.assertEqual(counters["dropped_dead"], 1)

    def test_the_priority_order_is_the_one_the_plan_states(self) -> None:
        """cached verified artwork before the Artwork Router, router before the
        source's own logo."""
        cached = "https://image.tmdb.org/t/p/w780/cached.jpg"
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": SOURCE_URL},
            {cached: (200, JPEG, "image/jpeg"),
             VERIFIED_URL: (200, JPEG, "image/jpeg")},
            cache={M._poster_identity("Gargi", 2024): cached},
            tmdb=VERIFIED_URL,
        )
        self.assertEqual(resolved, cached)
        self.assertEqual(counters["recovered_cache"], 1)

    def test_poster_lookup_false_still_falls_back_to_the_source_logo(self) -> None:
        """An owner who switched lookups off for an item did not ask for its
        artwork to be removed."""
        resolved, _ = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": SOURCE_URL,
             "poster_lookup": False},
            {}, tmdb=VERIFIED_URL)
        self.assertEqual(resolved, SOURCE_URL)


class CoverageMetricTests(unittest.TestCase):
    """ধারা ৮'s replacement metric, computed rather than asserted."""

    def test_the_share_is_verified_over_published(self) -> None:
        coverage = M._verified_artwork_coverage(
            {"verified": 90, "unverified": 10, "blank": 5})
        self.assertEqual(coverage["published_with_artwork"], 100)
        self.assertEqual(coverage["verified_share"], 0.9)
        self.assertEqual(coverage["no_artwork"], 5)
        self.assertEqual(coverage["target_share"], 0.95)

    def test_an_empty_catalogue_is_zero_not_a_division_error(self) -> None:
        self.assertEqual(
            M._verified_artwork_coverage({})["verified_share"], 0.0)

    def test_the_metric_names_no_provider(self) -> None:
        """ধারা ৮ struck out "TMDB পোস্টার > ৭৫%" because measuring one
        provider's share contradicts a provider-agnostic router - a good poster
        from Fanart would have counted as a failure."""
        coverage = M._verified_artwork_coverage({"verified": 1, "unverified": 0})
        for key in coverage:
            for provider in ("tmdb", "fanart", "omdb", "cinemeta"):
                self.assertNotIn(provider, key.casefold())


class ArtworkRouterTests(unittest.TestCase):
    """ধাপ ৬ - "Artwork Router (কোটা/হেলথ/capability অনুযায়ী ... স্থির ক্রম নয়)"."""

    def setUp(self):
        from scanner import provider_health

        self.ph = provider_health
        provider_health.reset(str(Path(tempfile.mkdtemp()) / "ph.json"))
        self.addCleanup(provider_health.reset, None)
        self.calls: list = []

    def _spy(self, name, result=""):
        def lookup(_ctx):
            self.calls.append(name)
            return result
        return lookup

    def _run(self, results, **kwargs):
        from scanner import poster_providers

        original = dict(poster_providers._POSTER_LOOKUPS)
        poster_providers._POSTER_LOOKUPS.clear()
        poster_providers._POSTER_LOOKUPS.update(
            {name: self._spy(name, results.get(name, "")) for name in original}
        )
        try:
            return poster_providers.supplementary_poster_lookup(
                "Inception", 2010, **kwargs)
        finally:
            poster_providers._POSTER_LOOKUPS.clear()
            poster_providers._POSTER_LOOKUPS.update(original)

    def test_an_id_keyed_provider_is_asked_before_a_title_keyed_one(self) -> None:
        """Not a preference - a different kind of answer. Fanart asked for
        tmdb_id 27205 returns art for that exact film; OMDb asked for
        "Inception" returns art for whatever it thinks that title means."""
        self._run({"fanart": "https://art.test/f.jpg"}, tmdb_id=27205)
        self.assertEqual(self.calls[0], "fanart")

    def test_an_unavailable_provider_is_skipped(self) -> None:
        self.ph._mark_unavailable(
            self.ph._provider_record("fanart"),
            self.ph.STATUS_COOLING_DOWN, 3600)
        self._run({"omdb": "https://art.test/o.jpg"}, tmdb_id=27205)
        self.assertNotIn("fanart", self.calls)
        self.assertIn("omdb", self.calls)

    def test_nothing_the_old_chain_tried_stops_being_tried(self) -> None:
        """The router's `kinds` filter is about where a provider is likely to
        help. A long shot that used to be taken and now is not is a regression
        dressed up as routing."""
        self._run({}, tmdb_id=0, imdb_id="")
        for provider in ("fanart", "cinemeta", "omdb", "tvmaze", "anilist"):
            self.assertIn(provider, self.calls)

    def test_the_first_non_empty_answer_wins(self) -> None:
        found = self._run(
            {"omdb": "https://art.test/o.jpg", "tvmaze": "https://art.test/t.jpg"})
        self.assertEqual(found, "https://art.test/o.jpg")

    def test_no_provider_is_asked_twice(self) -> None:
        self._run({}, tmdb_id=27205, imdb_id="tt1375666")
        self.assertEqual(len(self.calls), len(set(self.calls)))

    def test_omdb_is_registered_as_an_artwork_provider(self) -> None:
        """It returns a Poster field and `omdb_poster_lookup` has always used
        it; leaving artwork off its capabilities made the router unable to
        reach a provider the fixed chain was calling all along."""
        from scanner import provider_router

        self.assertIn(
            provider_router.CAPABILITY_ARTWORK,
            provider_router.DEFAULT_PROVIDERS["omdb"]["capabilities"],
        )


if __name__ == "__main__":
    unittest.main()
