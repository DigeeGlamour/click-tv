"""Two rules deadlocked over Star Jalsha, and the build lost.

Three scheduled runs failed on 2026-09-02 - 15:14, 16:28 and 16:37 - each with
one error:

    [ERROR] Indian channel #1 সমমান বা বেশি বিশ্বাসযোগ্য HTTPS backup থাকা
            সত্ত্বেও HTTP primary: Star Jalsha

The route-evidence ledger says what the card actually is:

    https://s3.itcnbd.live/...      403 through the playback proxy
    https://cache.devm3u.top/...    15.56s of media in a 120s window
    http://premiumtvs.space/...     no measurement against it
    http://rgkkw.live:80/...        29.86s of media, under the 115s floor

So the scan promotes the HTTP route, correctly - it is the only one anybody can
watch - and this check then refused the whole build over it. The only escape it
offered was hiding a channel that works.

`stream_confidence` ranks verification status. It knows nothing about whether a
browser could sustain the stream, so it called a route that plays for 15 of 120
seconds "equally trustworthy". That premise is what was wrong, and it is what
this fixes: an HTTPS backup with a measured playback failure is not an
alternative to anything.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate-pages.py"

#: The real card, as `data/channels/indian.json` publishes it.
PRIMARY_HTTP = "http://premiumtvs.space/live/YqXTywueEV/damp2purchase/1"
HTTPS_DEAD = "https://s3.itcnbd.live/server-4/stream/aHR0cDovLzE3Mi4xOS4xN"
HTTPS_STALLS = "https://cache.devm3u.top/hls/starjalsha.m3u8"
HTTP_STALLS = "http://rgkkw.live:80/live/1Aoen7elp5/IgMJ60tmAa/198.ts"


def load_validator(measured):
    """The validator's module namespace, with the ledger stubbed.

    Loading the script rather than reimplementing it: the rule under test is
    the one the build actually runs.
    """
    source = VALIDATOR.read_text(encoding="utf-8")
    namespace = {"__name__": "validate_pages_under_test",
                 "__file__": str(VALIDATOR)}
    exec(compile(source, "validate-pages", "exec"), namespace)  # noqa: S102
    namespace["measured_failure"] = lambda url: measured.get(url, "")
    namespace["ERRORS"].clear()
    namespace["WARNINGS"].clear()
    return namespace


def run_check(namespace, primary, backups, media_kind="hls"):
    namespace["ERRORS"].clear()
    namespace["WARNINGS"].clear()
    namespace["validate_https_priority"](
        {"url": primary, "verified": True, "resolution_height": 720},
        primary,
        [dict(row) for row in backups],
        "Indian channel #1",
        "Star Jalsha",
        media_kind,
    )
    return list(namespace["ERRORS"]), list(namespace["WARNINGS"])


def backup(url):
    return {"url": url, "verified": True, "resolution_height": 720}


class TheCardThatFailedThreeScheduledRuns(unittest.TestCase):
    LEDGER = {
        HTTPS_DEAD: "the playback proxy gets HTTP 403 for this route",
        HTTPS_STALLS: "media progress [15.56, 15.56] s in a 120.0 s window,"
                      " under the 115 s sustained floor",
        HTTP_STALLS: "media progress 29.86s < 115s",
    }

    def setUp(self):
        self.validator = load_validator(self.LEDGER)

    def test_the_build_is_no_longer_refused(self):
        errors, _ = run_check(
            self.validator, PRIMARY_HTTP,
            [backup(HTTPS_STALLS), backup(HTTPS_DEAD), backup(HTTP_STALLS)])
        self.assertEqual(errors, [])

    def test_it_still_says_something_rather_than_going_quiet(self):
        # The card really is on HTTP, and that is worth a line in the log.
        _, warnings = run_check(
            self.validator, PRIMARY_HTTP,
            [backup(HTTPS_STALLS), backup(HTTPS_DEAD), backup(HTTP_STALLS)])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Star Jalsha", warnings[0])


class AnHttpsRouteThatWorksStillWins(unittest.TestCase):
    """The rule this check exists for has to keep working."""

    def setUp(self):
        self.validator = load_validator({HTTPS_STALLS: "media progress 15.56s"})

    def test_an_unmeasured_https_backup_still_refuses_an_http_primary(self):
        errors, _ = run_check(self.validator, PRIMARY_HTTP,
                              [backup("https://good.example/live.m3u8")])
        self.assertEqual(len(errors), 1)
        self.assertIn("Star Jalsha", errors[0])

    def test_one_working_https_backup_among_broken_ones_is_enough(self):
        errors, _ = run_check(
            self.validator, PRIMARY_HTTP,
            [backup(HTTPS_STALLS), backup("https://good.example/live.m3u8")])
        self.assertEqual(len(errors), 1)

    def test_an_https_primary_is_never_questioned(self):
        errors, warnings = run_check(
            self.validator, "https://good.example/live.m3u8",
            [backup("https://other.example/live.m3u8")])
        self.assertEqual((errors, warnings), ([], []))

    def test_no_https_backup_at_all_is_not_an_error(self):
        errors, warnings = run_check(self.validator, PRIMARY_HTTP,
                                     [backup(HTTP_STALLS)])
        self.assertEqual((errors, warnings), ([], []))


class ItReadsTheSameLedgerTheScanReads(unittest.TestCase):
    def test_the_validator_asks_playback_evidence(self):
        source = VALIDATOR.read_text(encoding="utf-8")
        self.assertIn("from scanner import playback_evidence", source)
        self.assertIn("playback_evidence.unproven_reason", source)

    def test_the_filter_is_applied_where_the_backups_are_collected(self):
        source = VALIDATOR.read_text(encoding="utf-8")
        block = source[source.index("    https_backups = ["):]
        block = block[:block.index("]")]
        self.assertIn("measured_failure(get_primary_url(backup))", block)

    def test_a_ledger_read_that_throws_does_not_fail_the_build(self):
        namespace = load_validator({})
        source = VALIDATOR.read_text(encoding="utf-8")
        self.assertIn("except Exception:", source[source.index(
            "def measured_failure("):source.index("def validate_https_priority(")])

    def test_the_scan_and_the_validator_use_one_predicate(self):
        # scanner/unplayable_primary.py decides what to promote from the same
        # function; two implementations would drift and deadlock again.
        promoter = (ROOT / "scanner" / "unplayable_primary.py").read_text(
            encoding="utf-8")
        self.assertIn("playback_evidence.unproven_reason", promoter)


if __name__ == "__main__":
    unittest.main()
