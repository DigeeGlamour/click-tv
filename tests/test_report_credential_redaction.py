"""A committed report must never carry a live credential.

Every report in reports/ is committed by every scan, to a public repository. A
credential that reaches one is published, and there is no way to unpublish it.

Two gaps were open, both found on 2026-08-30.

reports/bd-verification.json was written straight to disk without going through
the project's redactor - the only protection was `_safe_url_for_report`, which
strips the query string. That covers `?hdnts=...`, which is where most CDNs put
a signed token. It does not cover the two this project actually deals with:

    https://host/live/hdntl=exp=...~hmac=<64 hex>/index.m3u8     (Akamai, path)
    __hdnea__=st=...~exp=...~hmac=<64 hex>                        (JioTV, cookie)

Nor does it cover `verification_error` and `verification_note`, which are free
text copied out of an upstream response - a CDN that echoes the request back
would put the token in one of them.

And the redactor itself only looked at query-parameter NAMES, so it would have
passed both of the lines above through untouched.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.playback_profiles import redact_public_report  # noqa: E402

AKAMAI_PATH = (
    "https://jcevents.hotstar.com/bpk-tv/x/Fallback/x.m3u8"
    "|cookie=hdntl=exp=1788074432~acl=%2f*~id=c362582a8169198d984bcedc535831f7"
    "~data=hdntl~hmac=ab6edd11f58b1b8a95a6b151a73602166101a0be0f8d2c6af6a0b2d7a4108fd7"
)
JIO_COOKIE = (
    "__hdnea__=st=1787994005~exp=1788015605~acl=/*"
    "~hmac=deb60f17e8840cb6a66c4eb87ab94c135a2895d951cc4a3d64636fe13b7659b4"
)
CLEARKEY = "license_key=39b5c756c18355d9978fe6c6311b4891:3aaa7efc64f9b81b91512d1af2c56e3c"


class RedactionTests(unittest.TestCase):
    def test_a_token_in_the_path_is_redacted(self):
        out = redact_public_report(AKAMAI_PATH)
        self.assertNotIn("ab6edd11f58b1b8a95a6b151a7360216", out)
        self.assertIn("<protected>", out)

    def test_a_token_in_free_text_is_redacted(self):
        out = redact_public_report({"verification_error": "HTTP 403; " + JIO_COOKIE})
        self.assertNotIn("deb60f17e8840cb6a66c4eb87ab94c13", out["verification_error"])

    def test_a_drm_key_in_free_text_is_redacted(self):
        out = redact_public_report({"verification_note": CLEARKEY})
        self.assertNotIn("3aaa7efc64f9b81b91512d1af2c56e3c", out["verification_note"])

    def test_a_query_token_is_still_redacted(self):
        out = redact_public_report("https://h.example/a?token=abc123secretvalue999")
        self.assertNotIn("abc123secretvalue999", out)

    def test_ordinary_prose_is_untouched(self):
        """The words key, expires, policy and signature appear in real report
        text. Redacting on the name alone would destroy legitimate reports -
        which this project has already had happen once."""
        for text in (
            "The key point is that expires soon and the policy is fine",
            "Request timed out",
            "HLS response does not contain #EXTM3U",
            "media progress 29.86s < 115s",
        ):
            self.assertEqual(text, redact_public_report(text))

    def test_a_short_value_is_not_treated_as_a_credential(self):
        self.assertEqual("sig=abc", redact_public_report("sig=abc"))

    def test_nested_structures_are_walked(self):
        out = redact_public_report(
            {"groups": {"failed": [{"url": AKAMAI_PATH, "name": "Star Jalsha"}]}}
        )
        row = out["groups"]["failed"][0]
        self.assertEqual("Star Jalsha", row["name"])
        self.assertNotIn("ab6edd11f58b1b8a95a6b151a7360216", row["url"])


class TheBdReportGoesThroughItTests(unittest.TestCase):
    def test_the_writer_redacts(self):
        source = (ROOT / "scanner" / "bd_verifier.py").read_text(encoding="utf-8")
        self.assertIn("_atomic_write_json(report_path, redact_public_report(report_data))",
                      source)

    def test_the_committed_report_carries_no_token(self):
        path = ROOT / "reports" / "bd-verification.json"
        if not path.is_file():
            self.skipTest("no bd report committed")
        text = path.read_text(encoding="utf-8")
        for pattern in (r"hmac=[0-9a-f]{32,}", r"hdntl=[^\"\\s]{40,}",
                        r"__hdnea__=[^\"\\s]{40,}"):
            self.assertIsNone(
                re.search(pattern, text),
                f"a live credential matching {pattern} is committed",
            )

    def test_no_committed_report_carries_one(self):
        """The whole directory, not just the one file that prompted this."""
        offenders = []
        for path in sorted((ROOT / "reports").glob("*.json")):
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"hmac=[0-9a-f]{40,}", text):
                offenders.append(path.name)
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
