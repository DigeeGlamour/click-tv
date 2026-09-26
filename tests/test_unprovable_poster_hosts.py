"""ধারা ৮ - prefer a poster that can be proven to load over one that cannot.

`403` is vantage-shaped and this does not change that: the same URL answers 403
from Bangladesh and 200 with real JPEG bytes from a GitHub runner, so an image
is never retired on one network's word. But the host breaker only ever counted
`ok` and `dead`, so a host that answered 403 for every single URL never counted
as anything at all - and the poster cache, which ranks first, re-selected the
same unprovable URL every run for ever.

Measured on 2026-09-26:

    srhady-live-stream.hf.space              1,005 published posters
    image.sm-iptv-monirul-islam.workers.dev    135 published posters

Every probe refused - from two continents, and through the site's own
Cloudflare worker, whose own error read "Failed to fetch image. Status: 403".
1,140 of 1,970 cards showed the placeholder to every viewer.

So refusals are counted per host, kept well away from the dead ledger, and used
for one thing only: a candidate from a host that answers nothing ranks below
one from a host that answers. It is still used when it is all there is.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import poster_validity as pv  # noqa: E402

REFUSING = "https://srhady-live-stream.hf.space/poster/%d.jpg"
ANSWERING = "https://image.tmdb.org/t/p/w500/%d.jpg"


class _FakeResponse:
    def __init__(self, payload=b"\xff\xd8\xff\xe0jpeg", content_type="image/jpeg"):
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self._payload = payload

    def read(self, _n=None):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _validator(behaviour):
    """A validator whose network is a dict of host -> what that host does."""
    import urllib.error

    def opener(request, timeout=None):  # noqa: ARG001
        host = pv._host_of(request.full_url)
        what = behaviour.get(host, "ok")
        if what == "ok":
            return _FakeResponse()
        raise urllib.error.HTTPError(request.full_url, what, "refused", {}, None)

    cache = Path(tempfile.mkdtemp()) / "poster-validity.json"
    validator = pv.PosterValidator(cache, enabled=True, opener=opener)
    return validator


class AHostThatAnswersNothingIsRecognised(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = _validator({"srhady-live-stream.hf.space": 403})

    def test_one_refusal_is_not_enough(self):
        self.validator.verdict(REFUSING % 1)
        self.assertFalse(self.validator.refuses_everything(REFUSING % 1))

    def test_enough_refusals_with_no_answer_is(self):
        for index in range(pv.HOST_BREAKER_TRIPS):
            self.validator.verdict(REFUSING % index)
        self.assertTrue(self.validator.refuses_everything(REFUSING % 99))

    def test_the_image_is_still_never_called_dead(self):
        """The rule this must not break."""
        for index in range(pv.HOST_BREAKER_TRIPS + 2):
            self.assertEqual(self.validator.verdict(REFUSING % index), pv.UNKNOWN)
        self.assertFalse(self.validator.is_dead(REFUSING % 1))

    def test_a_host_that_answers_once_is_not_refusing_everything(self):
        """One success is enough, exactly as the dead-host breaker has it."""
        validator = _validator({"mixed.example": 403})
        for index in range(pv.HOST_BREAKER_TRIPS + 1):
            validator.verdict(f"https://mixed.example/{index}.jpg")
        self.assertTrue(validator.refuses_everything("https://mixed.example/x.jpg"))
        validator._opener = _validator({})._opener
        validator.verdict("https://mixed.example/later.jpg")
        self.assertFalse(validator.refuses_everything("https://mixed.example/x.jpg"))

    def test_a_healthy_host_is_never_marked(self):
        validator = _validator({})
        for index in range(pv.HOST_BREAKER_TRIPS + 2):
            validator.verdict(ANSWERING % index)
        self.assertFalse(validator.refuses_everything(ANSWERING % 1))

    def test_a_404_still_goes_to_the_dead_ledger_not_this_one(self):
        validator = _validator({"gone.example": 404})
        for index in range(pv.HOST_BREAKER_TRIPS + 1):
            validator.verdict(f"https://gone.example/{index}.jpg")
        self.assertTrue(validator.is_dead("https://gone.example/1.jpg"))
        self.assertFalse(validator.refuses_everything("https://gone.example/1.jpg"))

    def test_it_is_counted_for_the_log(self):
        for index in range(pv.HOST_BREAKER_TRIPS):
            self.validator.verdict(REFUSING % index)
        self.assertEqual(self.validator.stats["hosts_refusing_all"], 1)

    def test_an_empty_url_is_not_a_refusing_host(self):
        self.assertFalse(self.validator.refuses_everything(""))
        self.assertFalse(self.validator.refuses_everything(None))


class ThePosterPassPrefersWhatItCanProve(unittest.TestCase):
    SOURCE = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")

    def _block(self):
        start = self.SOURCE.index("    for sweep in (False, True):")
        return self.SOURCE[start:start + 1200]

    def test_the_unprovable_sweep_comes_second(self):
        block = self._block()
        self.assertIn("for sweep in (False, True):", block)
        self.assertIn("if _refusing(url) is not sweep:", block)

    def test_an_unprovable_url_is_still_used_when_it_is_all_there_is(self):
        """Never a blank card where a URL exists: the viewer's own browser is
        what falls back to the placeholder, per viewer."""
        block = self._block()
        self.assertIn("counters[label]", block)
        self.assertIn("return url", block)

    def test_a_missing_method_on_an_older_validator_is_survived(self):
        block = self.SOURCE[self.SOURCE.index("    def _refusing(url: str) -> bool:"):]
        block = block[:block.index("for sweep in")]
        self.assertIn("except Exception", block)

    def test_the_count_is_published_so_the_metric_is_measurable(self):
        self.assertIn('"unprovable_host"', self.SOURCE)
        self.assertIn("on a host that answers nothing", self.SOURCE)


if __name__ == "__main__":
    unittest.main()
