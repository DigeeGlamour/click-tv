"""Items parsed by other code are not reused on a 304.

2026-10-22: a new parser rule shipped, sm-sports-data answered 304, and the
scan published what the previous parser had produced.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scanner import source_loader as sl


class ParserFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(sl, "SOURCE_CACHE_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def save(self):
        sl._save_source_cache("src", "https://feed.example/a.json",
                              {"etag": '"abc"'}, "json", [{"name": "A vs B"}])

    def test_same_parser_reuses_validators_and_items(self):
        self.save()
        meta, items = sl._load_source_cache("src", "https://feed.example/a.json")
        self.assertEqual(meta["etag"], '"abc"')
        self.assertEqual(items, [{"name": "A vs B"}])

    def test_changed_parser_drops_validators_but_keeps_the_outage_fallback(self):
        self.save()
        with mock.patch.object(sl, "_parser_fingerprint", return_value="different"):
            meta, items = sl._load_source_cache("src", "https://feed.example/a.json")
        # No If-None-Match goes out, so the feed is parsed again by this code.
        self.assertEqual(meta, {})
        # A feed that is down still has something to fall back on.
        self.assertEqual(items, [{"name": "A vs B"}])

    def test_fingerprint_follows_the_parser_code(self):
        self.assertRegex(sl._parser_fingerprint(), r"^[0-9a-f]{16}$")


if __name__ == "__main__":
    unittest.main()
