"""A route failure has to say what kind of failure it was.

Requirement 7 of the source-integration brief asks for source failures and
route failures to be reported separately. They were - but every route failure
read the same, and they are not the same thing.

Measured on 2026-08-29 in reports/source-errors-today.json: 972 route failures,
of which 661 were HTTP 403 from the GitHub runner's US datacentre egress. One
of those rows was

    "url": "https://sonydaimenew.akamaized.net/hls/live/2022316/.../std_lrh-800300010.m3u8"
    "verification_error": "HTTP 403: Forbidden"

and that exact URL answered HTTP 200 with a real live HLS master from a
Bangladeshi residential connection minutes later. The row was true about the
runner and false about the stream, and nothing in the file said which.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scan  # noqa: E402


class FailureClassTests(unittest.TestCase):
    def cls(self, **item):
        return scan._failure_class(item)

    def test_a_missing_file_is_permanent(self):
        self.assertEqual("permanent", self.cls(http_status=404))
        self.assertEqual("permanent", self.cls(http_status=410))

    def test_a_forbidden_answer_is_about_the_asker(self):
        for code in (401, 403, 407, 451):
            self.assertEqual("vantage_shaped", self.cls(http_status=code), code)

    def test_a_rate_limit_or_gateway_error_is_transient(self):
        for code in (408, 425, 429, 500, 502, 503, 504):
            self.assertEqual("transient", self.cls(http_status=code), code)

    def test_a_cdn_invented_5xx_is_transient_too(self):
        """292 routes on srhady-bingstream answered HTTP 567 in one run.
        Naming only the standard codes left every one of them 'unknown'."""
        self.assertEqual("transient", self.cls(http_status=567))
        self.assertEqual("transient", self.cls(http_status=599))

    def test_a_dns_failure_is_transient(self):
        self.assertEqual(
            "transient",
            self.cls(http_status=0,
                     verification_error="dns: [Errno 11001] getaddrinfo failed"),
        )

    def test_a_200_that_is_not_media_gets_its_own_class(self):
        """A server saying yes and sending something unplayable is not the same
        as a server saying no, and the two need different follow-up."""
        for text in ("HLS response does not contain #EXTM3U",
                     "HLS media playlist has no playable segment"):
            self.assertEqual(
                "unplayable_body", self.cls(http_status=200, verification_error=text), text
            )

    def test_an_empty_url_is_unplayable_body_not_a_network_verdict(self):
        self.assertEqual(
            "unplayable_body",
            self.cls(verification_error="Stream URL is empty"),
        )

    def test_a_timeout_with_no_status_is_transient(self):
        self.assertEqual(
            "transient",
            self.cls(http_status=0, verification_error="Request timed out"),
        )

    def test_so_is_a_certificate_failure(self):
        self.assertEqual(
            "transient",
            self.cls(http_status=0,
                     verification_error="ssl: CERTIFICATE_VERIFY_FAILED"),
        )

    def test_an_unrecognised_error_says_unknown_rather_than_guessing(self):
        self.assertEqual(
            "unknown", self.cls(http_status=0, verification_error="something else")
        )

    def test_no_evidence_at_all_classifies_nothing(self):
        self.assertEqual("", self.cls())
        self.assertEqual("", self.cls(http_status=0))

    def test_a_non_numeric_status_does_not_raise(self):
        self.assertEqual("", self.cls(http_status="n/a"))

    def test_the_class_reaches_the_report_row(self):
        row = scan._safe_report_item({
            "name": "Day 3 2nd Test",
            "source_id": "sportlive-sonyliv-backup",
            "verification_status": "failed",
            "http_status": 403,
            "verification_error": "HTTP 403: Forbidden",
            "url": "https://sonydaimenew.akamaized.net/hls/live/x/std.m3u8",
        })
        self.assertEqual("vantage_shaped", row["failure_class"])

    def test_a_row_with_nothing_to_classify_gains_no_field(self):
        row = scan._safe_report_item({"name": "x", "verification_status": "failed"})
        self.assertNotIn("failure_class", row)


if __name__ == "__main__":
    unittest.main()
