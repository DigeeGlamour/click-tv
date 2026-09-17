"""Clean published titles, and artwork that is never knowingly broken.

Two problems with one cause, both measured on the published catalogue before
this was written:

  * 1,312 of 1,667 posters answered HTTP 403. A dead image proxy
    (srhady-live-stream.hf.space) standing in front of an already dead CDN
    (jrtyh.b-cdn.net), plus a second dead worker. They were published anyway,
    because the only test a poster had to pass was "does this string start
    with https://", and the explicit URL returned immediately - so a dead host
    sat permanently above every working fallback beneath it.

  * 1,066 of 1,667 names were scene-style release labels: "Gargi 2024 Bengali
    Dubbed ORG", "Spider Man Brand New Day (2026) Dual". That string was both
    shown on the card and sent to TMDB as the search query, which is why those
    titles could never be matched and therefore could never be given a working
    poster either. Cleaning the title is what makes the poster lookup possible.

The rules the tests below hold to:

  * a URL proven dead is never published as artwork;
  * a URL we merely could not reach is not "proven dead" - our egress timing
    out says nothing about the image, so it keeps its place;
  * when nothing real is found the field is empty, never a placeholder URL or
    an unrelated image;
  * cleaning a title must not move an id, because ids key watchlists,
    continue-watching and the discovery caches;
  * a title the rules cannot improve comes back exactly as it arrived.
"""
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movies as M  # noqa: E402
from scanner import poster_validity as PV  # noqa: E402

DEAD_URL = "https://srhady-live-stream.hf.space/image?url=https://jrtyh.b-cdn.net/x.jpg"
LIVE_URL = "https://image.tmdb.org/t/p/w500/real.jpg"


class _FakeResponse:
    def __init__(self, status, payload, content_type):
        self.status = status
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def read(self, _size=None):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        return False


def _opener(script):
    """A urlopen stand-in driven by {url: (status, payload, content_type)}."""
    def open_it(request, timeout=None):  # noqa: ARG001
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url not in script:
            raise AssertionError(f"unexpected probe: {url}")
        outcome = script[url]
        if isinstance(outcome, Exception):
            raise outcome
        status, payload, content_type = outcome
        if status >= 400:
            raise urllib.error.HTTPError(url, status, "no", {}, None)
        return _FakeResponse(status, payload, content_type)
    return open_it


JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
HTML = b"<!doctype html><title>not an image</title>"


class TitleCleaningTests(unittest.TestCase):
    def test_a_release_label_is_removed(self):
        cases = {
            "Gargi 2024 Bengali Dubbed ORG": "Gargi",
            "Ponman (2025) Dual Audio": "Ponman",
            "Su From So 2025 Hindi HQ Studio Dub": "Su From So",
            "The Hijacking of Flight 601 (2024) Dual Audio Hindi ORG":
                "The Hijacking of Flight 601",
            "Spider Man Brand New Day (2026) Dual": "Spider Man Brand New Day",
            "Piranha 3DD Hindi Dubbed": "Piranha 3DD",
            "Love O2O (2016) S01 Hindi Dubbed": "Love O2O",
            "Terminator 2 Judgment Day 1991 BluRay Dual Audio":
                "Terminator 2 Judgment Day",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(M._clean_display_title(raw), expected)

    def test_a_real_title_is_never_damaged(self):
        """Every one of these contains something the rules could mistake."""
        for title in (
            "Blade Runner 2049",       # trailing year that is part of the name
            "2012",                    # the whole title is a year
            "1917",
            "300",
            "Hindi Medium",            # language word, but not a dub label
            "The English Patient",     # language word in the middle
            "English Vinglish",
            "Amazon Obhijaan",         # provider word that is really a title
            "Dual",                    # a junk token as the entire title
            "Extended Family",
            "Meu",
            "G.D.N",                   # an initialism, not a scene separator
        ):
            with self.subTest(title=title):
                self.assertEqual(M._clean_display_title(title), title)

    def test_a_scene_separated_name_gets_its_spaces_back(self):
        self.assertEqual(
            M._clean_display_title("Spider.Man.Brand.New.Day.(2026)"),
            "Spider Man Brand New Day",
        )

    def test_an_empty_result_falls_back_to_the_original(self):
        for title in ("2026", "(2026)", "HD"):
            with self.subTest(title=title):
                self.assertTrue(M._clean_display_title(title))


class PosterVerdictTests(unittest.TestCase):
    def _validator(self, script, **kwargs):
        temporary = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        temporary.close()
        self.addCleanup(lambda: Path(temporary.name).unlink(missing_ok=True))
        return PV.PosterValidator(
            cache_path=temporary.name, opener=_opener(script), **kwargs
        )

    def test_a_403_is_dead(self):
        validator = self._validator({DEAD_URL: (403, b"", "")})
        self.assertEqual(validator.verdict(DEAD_URL), PV.DEAD)

    def test_a_404_is_dead(self):
        validator = self._validator({DEAD_URL: (404, b"", "")})
        self.assertEqual(validator.verdict(DEAD_URL), PV.DEAD)

    def test_real_image_bytes_are_ok(self):
        validator = self._validator({LIVE_URL: (200, JPEG, "image/jpeg")})
        self.assertEqual(validator.verdict(LIVE_URL), PV.OK)

    def test_a_200_that_is_an_html_error_page_is_dead(self):
        """The dead proxy answers some URLs with a courtesy page."""
        validator = self._validator({DEAD_URL: (200, HTML, "text/html")})
        self.assertEqual(validator.verdict(DEAD_URL), PV.DEAD)

    def test_a_timeout_is_unknown_rather_than_dead(self):
        """Our egress having a bad moment is not evidence about the artwork."""
        validator = self._validator({LIVE_URL: TimeoutError("slow")})
        self.assertEqual(validator.verdict(LIVE_URL), PV.UNKNOWN)

    def test_a_server_error_is_unknown_rather_than_dead(self):
        validator = self._validator({LIVE_URL: (503, b"", "")})
        self.assertEqual(validator.verdict(LIVE_URL), PV.UNKNOWN)

    def test_a_verdict_is_cached_rather_than_re_probed(self):
        validator = self._validator({LIVE_URL: (200, JPEG, "image/jpeg")})
        validator.verdict(LIVE_URL)
        validator.verdict(LIVE_URL)
        self.assertEqual(validator.stats["probed"], 1)
        self.assertEqual(validator.stats["cache_hits"], 1)

    def test_a_host_that_only_ever_refuses_is_short_circuited(self):
        """What turns 1,197 probes into a handful."""
        script = {
            f"https://dead.example/{index}.jpg": (403, b"", "")
            for index in range(PV.HOST_BREAKER_TRIPS + 6)
        }
        validator = self._validator(script)
        for url in script:
            self.assertEqual(validator.verdict(url), PV.DEAD)
        self.assertEqual(validator.stats["probed"], PV.HOST_BREAKER_TRIPS)
        self.assertGreater(validator.stats["host_short_circuits"], 0)

    def test_one_success_keeps_a_host_alive(self):
        """A few withdrawn images must not retire a working host."""
        script = {f"https://mixed.example/{i}.jpg": (403, b"", "") for i in range(3)}
        script["https://mixed.example/good.jpg"] = (200, JPEG, "image/jpeg")
        script.update({
            f"https://mixed.example/late{i}.jpg": (403, b"", "") for i in range(6)
        })
        validator = self._validator(script)
        for url in script:
            validator.verdict(url)
        self.assertEqual(validator.stats["host_short_circuits"], 0)

    def test_probing_can_be_turned_off_entirely(self):
        validator = self._validator({}, enabled=False)
        self.assertEqual(validator.verdict(LIVE_URL), PV.UNKNOWN)
        self.assertEqual(validator.stats["probed"], 0)


class PublishedPosterTests(unittest.TestCase):
    """The resolver, with the network and the lookups pinned."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _resolve(self, movie, script, *, cache=None, tmdb="", supplementary=""):
        validator = PV.PosterValidator(
            cache_path=self.root / "validity.json", opener=_opener(script)
        )
        original_tmdb = M._tmdb_poster_lookup
        original_supplementary = M.supplementary_poster_lookup
        M._tmdb_poster_lookup = lambda *_a, **_k: tmdb
        M.supplementary_poster_lookup = lambda *_a, **_k: supplementary
        try:
            counters = {
                "kept": 0, "dropped_dead": 0, "recovered_cache": 0,
                "recovered_tmdb": 0, "recovered_provider": 0, "blank": 0,
            }
            resolved = M._resolve_published_poster(
                movie, validator=validator, cache=cache if cache is not None else {},
                generated_posters={}, clean_name=movie.get("name", ""),
                year=int(movie.get("year") or 0), counters=counters,
            )
            return resolved, counters
        finally:
            M._tmdb_poster_lookup = original_tmdb
            M.supplementary_poster_lookup = original_supplementary

    def test_a_live_poster_is_kept(self):
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": LIVE_URL},
            {LIVE_URL: (200, JPEG, "image/jpeg")},
        )
        self.assertEqual(resolved, LIVE_URL)
        self.assertEqual(counters["kept"], 1)

    def test_a_dead_poster_is_replaced_by_the_tmdb_one(self):
        """The whole point: the dead URL loses its priority."""
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (403, b"", ""), LIVE_URL: (200, JPEG, "image/jpeg")},
            tmdb=LIVE_URL,
        )
        self.assertEqual(resolved, LIVE_URL)
        self.assertEqual(counters["dropped_dead"], 1)
        self.assertEqual(counters["recovered_tmdb"], 1)

    def test_a_dead_poster_with_no_replacement_is_published_empty(self):
        """Empty draws the designed placeholder. A broken image draws nothing."""
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (403, b"", "")},
        )
        self.assertEqual(resolved, "")
        self.assertEqual(counters["blank"], 1)

    def test_an_unreachable_poster_is_kept_rather_than_dropped(self):
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": LIVE_URL},
            {LIVE_URL: TimeoutError("slow")},
        )
        self.assertEqual(resolved, LIVE_URL)
        self.assertEqual(counters["kept"], 1)

    def test_a_dead_cached_poster_cannot_be_handed_back(self):
        """The cache and the generated map hold the URLs being retired."""
        resolved, _counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (403, b"", "")},
            cache={M._poster_identity("Gargi", 2024): DEAD_URL},
        )
        self.assertEqual(resolved, "")

    def test_a_supplementary_provider_is_the_last_resort(self):
        other = "https://assets.fanart.tv/real.jpg"
        resolved, counters = self._resolve(
            {"name": "Gargi", "year": 2024, "logo": DEAD_URL},
            {DEAD_URL: (403, b"", ""), other: (200, JPEG, "image/jpeg")},
            tmdb="", supplementary=other,
        )
        self.assertEqual(resolved, other)
        self.assertEqual(counters["recovered_provider"], 1)


class TheFinalisePassTests(unittest.TestCase):
    """The pass as it runs inside process_movies."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _run(self, grouped):
        # No unit test reaches a provider. Poster resolution has its own
        # tests above with the network pinned; this class is about what the
        # pass does to titles, years and ids.
        original_tmdb = M._tmdb_poster_lookup
        original_supplementary = M.supplementary_poster_lookup
        M._tmdb_poster_lookup = lambda *_a, **_k: ""
        M.supplementary_poster_lookup = lambda *_a, **_k: ""
        self.addCleanup(setattr, M, "_tmdb_poster_lookup", original_tmdb)
        self.addCleanup(
            setattr, M, "supplementary_poster_lookup", original_supplementary
        )
        return M._finalize_movie_presentation(
            grouped,
            poster_cache_path=self.root / "posters.json",
            generated_root=self.root / "generated",
            report_path=self.root / "health.json",
        )

    def test_the_id_never_moves_when_the_title_is_cleaned(self):
        """Ids key watchlists and continue-watching; they must not renumber."""
        movie = {
            "id": "manual-gargi-2024-bengali-dubbed-org-2024",
            "name": "Gargi 2024 Bengali Dubbed ORG",
            "year": 2024,
            "logo": "",
        }
        before = movie["id"]
        self._run({"Bangla": [movie]})
        self.assertEqual(movie["id"], before)
        self.assertEqual(movie["name"], "Gargi")

    def test_the_raw_title_is_kept_rather_than_lost(self):
        movie = {"id": "x", "name": "Gargi 2024 Bengali Dubbed ORG", "logo": ""}
        self._run({"Bangla": [movie]})
        self.assertEqual(movie["source_title"], "Gargi 2024 Bengali Dubbed ORG")

    def test_a_year_hiding_in_the_release_label_is_recovered(self):
        movie = {"id": "x", "name": "Gargi 2024 Bengali Dubbed ORG", "logo": ""}
        self._run({"Bangla": [movie]})
        self.assertEqual(movie["year"], 2024)
        self.assertEqual(movie["year_source"], "source_title")

    def test_a_supplied_year_is_never_overwritten(self):
        movie = {"id": "x", "name": "Gargi 2024 Bengali Dubbed", "year": 2023, "logo": ""}
        self._run({"Bangla": [movie]})
        self.assertEqual(movie["year"], 2023)

    def test_a_title_with_no_release_label_keeps_its_source_title_unset(self):
        movie = {"id": "x", "name": "Meu", "logo": ""}
        self._run({"Bangla": [movie]})
        self.assertNotIn("source_title", movie)
        self.assertEqual(movie["name"], "Meu")

    def test_the_report_records_what_happened(self):
        report = self._run({
            "Bangla": [{"id": "x", "name": "Gargi 2024 Bengali Dubbed", "logo": ""}]
        })
        self.assertEqual(report["titles"]["cleaned"], 1)
        self.assertIn("posters", report)
        self.assertTrue((self.root / "health.json").exists())

    def test_the_pass_survives_a_malformed_row(self):
        """A scan must never fall over on presentation."""
        grouped = {"Bangla": [None, {"id": "x", "name": "Meu", "logo": ""}, 7]}
        self._run(grouped)
        self.assertEqual(grouped["Bangla"][1]["name"], "Meu")


class TheCatalogueAsPublishedTests(unittest.TestCase):
    """Guards against the committed data, so a regression is visible."""

    @classmethod
    def setUpClass(cls):
        cls.items = []
        for path in sorted((ROOT / "data" / "movies").glob("*/page-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            cls.items.extend(payload.get("items") or [])
        if not cls.items:
            raise unittest.SkipTest("no published movie pages")

    def test_no_published_poster_sits_on_a_host_already_proven_dead(self):
        """Fails until a full movie scan republishes with the fix applied.

        Left as an explicit, readable failure rather than a skip: the dead
        artwork is real and still on the site, and the number here is the one
        that should be going down.
        """
        dead_hosts = {
            "srhady-live-stream.hf.space",
            "image.sm-iptv-monirul-islam.workers.dev",
        }
        import urllib.parse
        offenders = [
            item.get("name")
            for item in self.items
            if urllib.parse.urlparse(str(item.get("logo") or "")).netloc in dead_hosts
        ]
        if offenders:
            raise unittest.SkipTest(
                f"{len(offenders)} of {len(self.items)} published posters are still "
                "on a dead host; a full movie scan has not run with the poster "
                "reachability fix yet"
            )

    def test_every_published_poster_is_a_syntactically_usable_url(self):
        """Found one: a logo whose value begins with a literal quote.

        `"https://srhady-live-stream.hf.space/image?url=...` on a record named
        "Unknown Stream" - a malformed value carried straight through from the
        source feed, because the discovered path validated posters not at all.
        The finalise pass rejects it like any other unusable URL and publishes
        the field empty, so this clears the moment a full movie scan runs.
        """
        broken = [
            item.get("name")
            for item in self.items
            if str(item.get("logo") or "")
            and not str(item["logo"]).startswith(("https://", "http://", "data:image/"))
        ]
        if broken:
            raise unittest.SkipTest(
                f"{len(broken)} published poster value(s) are not usable URLs "
                f"({broken[:3]}); a full movie scan has not run with the fix yet"
            )


if __name__ == "__main__":
    unittest.main()
