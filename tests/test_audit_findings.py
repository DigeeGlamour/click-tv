"""The four defects an external audit found on 2026-08-30, each verified first.

The audit read the code rather than playing anything, and said so, and the
repository moved underneath it while it ran. So every claim in it was checked
against the tree before anything was changed. Four were real, one was not
reproducible, and the rest were product opinions rather than defects.

Kept as tests because all four are the kind that come back: a hand-written
version string, a signature list, a hash of an empty payload, an off-by-one
that only shows on rows nobody looks at.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import merger  # noqa: E402

APP = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")


class ReturningViewersGetTheCodeThatWasShippedTests(unittest.TestCase):
    """The audit's CRITICAL-1, and the most serious thing in it.

    site/index.html asked for `assets/js/app.js?v=20260819-today-match-crimson-v4`
    - a string written by hand and unchanged since 2026-08-19, across every fix
    made since - and site/sw.js caches script requests cache-first, keyed on the
    full URL. So the service worker kept serving the app.js a viewer had cached
    weeks earlier. The code was right in the repository and wrong in the browser.
    """

    def test_the_build_stamps_a_content_hash(self):
        build = (ROOT / "scripts" / "build-pages.sh").read_text(encoding="utf-8")
        self.assertIn("stamp-asset-versions.py", build)

    def test_the_stamp_runs_before_the_validator(self):
        """Otherwise the build validates a tree it is about to rewrite."""
        build = (ROOT / "scripts" / "build-pages.sh").read_text(encoding="utf-8")
        # The VALIDATOR variable is defined at the top of the file; what matters
        # is where it is run.
        self.assertLess(build.index("stamp-asset-versions.py"),
                        build.index('"${VALIDATOR}"'))

    def test_a_built_page_carries_hashes_not_hand_written_labels(self):
        index = ROOT / "dist" / "index.html"
        if not index.is_file():
            self.skipTest("no dist build present")
        html = index.read_text(encoding="utf-8")
        # Only what the browser actually fetches. The page also mentions an
        # asset URL in a sentence, and prose is not a reference.
        versions = {
            match.group(1)
            for match in re.finditer(
                r"""(?:src|href)=["'][^"']*assets/(?:js|css)/[^"'?]+\?v=([^"']+)["']""",
                html,
            )
        }
        self.assertTrue(versions, "no stamped asset references in the build")
        for version in versions:
            self.assertRegex(
                version, r"^[0-9a-f]{12}$",
                f"{version!r} is not a content hash - a hand-written version "
                "is exactly what pinned viewers to a stale app.js",
            )

    def test_the_service_worker_cache_is_stamped_too(self):
        worker = ROOT / "dist" / "sw.js"
        if not worker.is_file():
            self.skipTest("no dist build present")
        match = re.search(r'const CACHE_VERSION = "([^"]+)"',
                          worker.read_text(encoding="utf-8"))
        self.assertIsNotNone(match)
        self.assertRegex(match.group(1), r"^click-tv-[0-9a-f]{12}$")


class ARouteChangeReachesTheOpenPageTests(unittest.TestCase):
    """The audit's CRITICAL-2.

    The background refresh compared _uid, url, status, start_time and end_time.
    An event's playable identity is in none of them: most event cards carry an
    empty top-level url and play through playback_id and channels[]. So a scan
    that swapped a dead route for a working one produced a byte-identical
    signature and the refresh returned without touching the UI - which is this
    project's own self-repair arriving in the file and stopping at the browser.
    """

    def _signature_block(self):
        start = APP.index("const signature = (items) =>")
        return APP[start:APP.index("if (signature(nextItems)", start)]

    def test_the_signature_includes_playable_identity(self):
        block = self._signature_block()
        for field in ("playback_id", "metadata_only", "verification_status",
                      "available_link_count", "default_channel_id"):
            self.assertIn(field, block, f"{field} is not compared")

    def test_it_notices_a_channel_or_backup_change(self):
        block = self._signature_block()
        self.assertIn("item.channels", block)
        self.assertIn("item.backups", block)

    def test_it_still_compares_the_original_fields(self):
        block = self._signature_block()
        for field in ("_uid", "url", "status", "start_time", "end_time"):
            self.assertIn(field, block)


class AnIdentityKeyIdentifiesSomethingTests(unittest.TestCase):
    """The audit's HIGH-3, and worse than it reported.

    `_stream_identity_key` hashed url, headers, drm, profile and proxy mode. A
    metadata-only fixture has none of them, so every one hashed the same empty
    payload - one key was published as the `primary_stream_key` of 183
    unrelated fixtures and another of 33. `load_previous_primary_keys` maps
    event name to that key so a healthy primary keeps its place across scans,
    and 183 events all claiming the same previous primary is a comparison that
    can only give wrong answers.
    """

    def test_a_routeless_stream_claims_no_identity(self):
        self.assertEqual("", merger._stream_identity_key({"url": "", "headers": {}}))
        self.assertEqual("", merger._stream_identity_key({}))

    def test_a_real_route_still_gets_one(self):
        key = merger._stream_identity_key({"url": "https://a.example/x.m3u8"})
        self.assertRegex(key, r"^[0-9a-f]{64}$")

    def test_two_different_routes_differ(self):
        self.assertNotEqual(
            merger._stream_identity_key({"url": "https://a.example/x.m3u8"}),
            merger._stream_identity_key({"url": "https://b.example/x.m3u8"}),
        )

    def test_an_empty_identity_never_matches_an_incumbent(self):
        """Otherwise every routeless fixture reads as the previous primary."""
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        held = source[source.index("held = next("):]
        held = held[:held.index("-1,")]
        self.assertIn("previous_primary_identity", held)
        self.assertIn("and", held)

    def test_the_published_files_share_no_placeholder_key(self):
        """Two fixtures may legitimately share a key when they are the same
        broadcast - GT World Challenge and GT3 Revival Series ran off one URL.
        A key shared by dozens is a placeholder, not a broadcast."""
        counts = {}
        for name in ("today-match.json", "upcoming.json"):
            path = ROOT / "data" / name
            if not path.is_file():
                continue
            for item in json.loads(path.read_text(encoding="utf-8")).get("items") or []:
                key = str(item.get("primary_stream_key") or "")
                if key:
                    counts[key] = counts.get(key, 0) + 1
        crowded = {k: v for k, v in counts.items() if v > 4}
        self.assertEqual({}, crowded)


class ALinkCountCountsLinksTests(unittest.TestCase):
    """The audit's HIGH-2. Every one of the 216 Upcoming cards published
    `available_link_count: 1` beside `metadata_only: true` and an empty
    channels[] - a field whose name promises a playable route, asserting one
    that does not exist."""

    def test_the_published_files_do_not_claim_a_link_they_lack(self):
        offenders = []
        for name in ("today-match.json", "upcoming.json"):
            path = ROOT / "data" / name
            if not path.is_file():
                continue
            for item in json.loads(path.read_text(encoding="utf-8")).get("items") or []:
                if item.get("metadata_only") is True and int(
                    item.get("available_link_count") or 0
                ) > 0:
                    offenders.append(item.get("name"))
        self.assertEqual([], offenders[:10])

    def test_a_real_card_still_counts_its_primary(self):
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        self.assertIn('(0 if is_metadata_only else 1) + len(backups)', source)


if __name__ == "__main__":
    unittest.main()
