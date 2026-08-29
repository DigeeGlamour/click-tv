"""One bad minute on one egress must not delete a working channel.

Measured on 2026-08-29. Cards published in 150d3487c that are not published at
HEAD, re-probed from a Bangladeshi residential connection within minutes of
reading the CI verdict out of reports/source-errors-channels.json:

    BTV News     CI: HTTP 429 Too Many Requests  ->  here: HTTP 200, 1080p
    My TV        CI: HTTP 429 Too Many Requests  ->  here: HTTP 200
    Anand TV     CI: request timed out           ->  here: HTTP 200, 1080p
    Praise TV    CI: request timed out           ->  here: HTTP 200,  720p

Four working channels, two of them Bangla, deleted on evidence that says
nothing about the stream: 429 is a statement about the asker and so is a socket
timeout. The verifier already refuses to call that "failed" for movies; the TV
path had no equivalent because it had no way to tell a route the catalogue was
already carrying from a route nobody had ever verified.

The tests below pin the narrowness as hard as the behaviour. The objection that
kept the movie rule movies-only - that a pending status is publishable for TV -
is answered by only ever rescuing a route the previous published catalogue
carried, and only twice in a row.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import last_published  # noqa: E402
from scanner import verifier  # noqa: E402

PUBLISHED = "https://app24.jagobd.com.bd/live/btvnews/index.m3u8"
NEVER_SEEN = "https://example.invalid/live/brand-new.m3u8"


def _snapshot(directory, url=PUBLISHED, rescues=0):
    Path(directory, "bangla.json").write_text(
        json.dumps({
            "category": "Bangla",
            "count": 1,
            "channels": [{
                "name": "BTV News",
                "category": "Bangla",
                "url": url,
                "transient_rescue_count": rescues,
                "backups": [],
            }],
        }),
        encoding="utf-8",
    )
    return directory


class RescueTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(last_published.reset_cache)
        last_published.reset_cache()
        real = last_published.DEFAULT_DIR
        last_published.DEFAULT_DIR = _snapshot(self.dir.name)
        self.addCleanup(setattr, last_published, "DEFAULT_DIR", real)

    def status(self, code=429, kind="", url=PUBLISHED, pipeline="tv"):
        return verifier._transient_rescue_status(
            {"url": url, "source_pipeline": pipeline, "name": "BTV News"}, code, kind
        )

    def test_a_rate_limit_on_a_published_route_is_held_not_failed(self):
        self.assertEqual("retryable_pending", self.status(429))

    def test_so_is_a_timeout(self):
        self.assertEqual("retryable_pending", self.status(0, "timeout"))

    def test_and_a_gateway_error(self):
        for code in (500, 502, 503, 504):
            self.assertEqual("retryable_pending", self.status(code), code)

    def test_a_missing_file_always_fails(self):
        """404 is about the file, not about the asker."""
        self.assertEqual("", self.status(404))
        self.assertEqual("", self.status(410))

    def test_a_geo_block_is_not_rescued(self):
        """403/451 are vantage-shaped too, and holding them would ship a
        geo-blocked route on a card. They stay out deliberately."""
        self.assertEqual("", self.status(403))
        self.assertEqual("", self.status(451))

    def test_a_route_the_catalogue_never_carried_gets_nothing(self):
        """The whole objection to widening this: it must never publish an
        unverified route."""
        self.assertEqual("", self.status(429, url=NEVER_SEEN))

    def test_movies_keep_their_own_rule(self):
        self.assertEqual("", self.status(429, pipeline="movies"))

    def test_the_rescue_runs_out(self):
        last_published.reset_cache()
        last_published.DEFAULT_DIR = _snapshot(
            self.dir.name, rescues=verifier.MAXIMUM_TRANSIENT_RESCUES
        )
        self.assertEqual("", self.status(429))

    def test_the_counter_advances(self):
        item = {"url": PUBLISHED}
        self.assertEqual(1, verifier._transient_rescue_next(item))
        last_published.reset_cache()
        last_published.DEFAULT_DIR = _snapshot(self.dir.name, rescues=1)
        self.assertEqual(2, verifier._transient_rescue_next(item))

    def test_a_backup_route_counts_as_published_too(self):
        last_published.reset_cache()
        Path(self.dir.name, "bangla.json").write_text(
            json.dumps({
                "category": "Bangla",
                "channels": [{
                    "name": "BTV News",
                    "url": "https://other.example/primary.m3u8",
                    "backups": [{"url": PUBLISHED}],
                }],
            }),
            encoding="utf-8",
        )
        self.assertTrue(last_published.was_published(PUBLISHED))

    def test_a_missing_snapshot_directory_rescues_nothing(self):
        last_published.reset_cache()
        last_published.DEFAULT_DIR = str(Path(self.dir.name) / "does-not-exist")
        self.assertEqual("", self.status(429))

    def test_the_reason_names_what_happened(self):
        self.assertEqual("HTTP 429", verifier._transient_reason(429, ""))
        self.assertEqual("a timeout error", verifier._transient_reason(0, "timeout"))
        self.assertEqual("a network error", verifier._transient_reason(0, ""))


class PublishGateTests(unittest.TestCase):
    """A rescue the publish gate then drops is not a rescue.

    Measured before this clause existed: the 2026-08-29 channels scan produced
    thirteen retryable_pending items and published none, because
    `item_is_proven_live` did not recognise the status. The rescue ran, wrote
    its counter, and changed nothing a viewer could see.
    """

    def gate(self, **item):
        from scanner import browser_reachability as br
        return br.item_is_proven_live(item)

    def test_a_rescued_route_passes_the_gate(self):
        self.assertTrue(self.gate(
            verification_status="retryable_pending", transient_rescue_count=1))

    def test_an_unrescued_retryable_item_still_does_not(self):
        """The status alone must not be a way in: a movie or a first-time
        candidate can carry it without ever having been published."""
        self.assertFalse(self.gate(verification_status="retryable_pending"))
        self.assertFalse(self.gate(
            verification_status="retryable_pending", transient_rescue_count=0))

    def test_a_junk_counter_is_not_a_way_in(self):
        self.assertFalse(self.gate(
            verification_status="retryable_pending", transient_rescue_count="yes"))

    def test_turning_geo_pending_off_turns_this_off_too(self):
        from scanner import browser_reachability as br
        self.assertFalse(br.item_is_proven_live(
            {"verification_status": "retryable_pending", "transient_rescue_count": 1},
            allow_geo_pending=False,
        ))

    def test_a_plain_failure_never_passes(self):
        self.assertFalse(self.gate(
            verification_status="failed", transient_rescue_count=1))


class TheRescuedItemTests(unittest.TestCase):
    """What the rescue writes on the item, read from the source it writes it in.

    The first version set only the status. Thirteen routes were rescued in the
    2026-08-29 channels scan and none reached a card, because the merger reads
    `publish_allowed` and falls back to `verified` - and `verified` is False
    here by definition.
    """

    def setUp(self):
        self.block = self._rescue_block()

    @staticmethod
    def _rescue_block():
        source = (ROOT / "scanner" / "verifier.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("rescued = _transient_rescue_status(")
        return tail[: tail.index("item[\"verification_status\"] = \"failed\"")]

    def test_it_is_publishable(self):
        self.assertIn('item["publish_allowed"] = True', self.block)

    def test_but_never_claims_verification(self):
        """A rescued card must not wear a Verified badge for a scan that did
        not reach the stream."""
        self.assertIn('item["verified"] = False', self.block)

    def test_the_badge_for_the_status_says_temporary(self):
        from scanner import merger
        self.assertEqual(
            "Temporary",
            merger._verification_badge({"verification_status": "retryable_pending"}),
        )

    def test_the_reason_travels_on_the_item(self):
        self.assertIn('item["vantage_note"]', self.block)


class CardCarriesTheCounterTests(unittest.TestCase):
    """The counter is useless if the published card drops it."""

    def test_the_merger_carries_it_onto_the_card(self):
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        self.assertIn('"transient_rescue_count",', source)

    def test_and_onto_a_backup(self):
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        _, _, tail = source.partition("for index, b_stream in enumerate(")
        block = tail[: tail.index("backups.append(")]
        self.assertIn("transient_rescue_count", block)


if __name__ == "__main__":
    unittest.main()
