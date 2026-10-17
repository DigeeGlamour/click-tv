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


def _routes(item):
    """How many playable routes a card actually carries.

    One definition, read by the published-file check and by the unit cases
    below, so the two cannot drift apart the way they did on 2026-10-17.
    A route is a url or a playback_id - the player accepts either.
    """
    routes = 0 if item.get("metadata_only") is True else int(bool(
        str(item.get("url") or "").strip()
        or str(item.get("playback_id") or "").strip()))
    for backup in item.get("backups") or []:
        if not isinstance(backup, dict):
            continue
        if (str(backup.get("url") or "").strip()
                or str(backup.get("playback_id") or "").strip()):
            routes += 1
    return routes


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
        """Counted against the routes that exist, not against `metadata_only`.

        The fault this was written for is still caught, and over-counting is
        caught now too - but the test used to read `metadata_only is True`
        alone as the offence, and that is not what the flag means.
        `metadata_only` says the card has no PRIMARY route; `merger.py`
        writes the count as `(0 if is_metadata_only else 1) + len(backups)`,
        so a card with real backups and no primary is both metadata_only and
        honestly countable.

        A route is a url OR a playback_id, for the primary exactly as for a
        backup. An earlier version counted only `url` on the primary while
        counting either on a backup, and that inconsistency failed the suite
        - which gates every scan - on a card that was telling the truth:

            Powerball Cup, Round 3
              metadata_only         False
              url                   null
              playback_id           ctv_697b0a44...   verified
              backups               3   each with a playback_id, verified
              available_link_count  4

        Four real routes, counted as four. The url is withheld on purpose:
        `collectPlaybackSources` in app.js admits a source on
        `sourceUrl || playbackId` and sends an id-only source through the
        id-aware proxy, so a playback_id is a route a viewer can watch.

        It fired on the published catalogue of 2026-10-17 12:53 and failed
        the suite, which gates every catalogue scan:

            RCD Espanyol vs Atletico de Madrid
              metadata_only         True
              url                   ''
              backups               4   each with a playback_id, verified
              channels              4
              available_link_count  4

        Four real routes, counted as four. Refusing that publishes nothing
        and stops the scanner, which is the cost this project has already
        paid once for a test that was stricter than the thing it described.
        """
        offenders = []
        for name in ("today-match.json", "upcoming.json"):
            path = ROOT / "data" / name
            if not path.is_file():
                continue
            for item in json.loads(path.read_text(encoding="utf-8")).get("items") or []:
                claimed = int(item.get("available_link_count") or 0)
                routes = _routes(item)
                if claimed > routes:
                    offenders.append(
                        f"{item.get('name')} claims {claimed} of {routes}")
        self.assertEqual([], offenders[:10])

    def test_a_card_with_nothing_at_all_still_fails_it(self):
        """The original HIGH-2 shape: `available_link_count: 1` beside
        `metadata_only: true` and nothing to play."""
        item = {"name": "Nothing", "metadata_only": True,
                "available_link_count": 1, "url": "", "playback_id": "",
                "backups": []}
        self.assertEqual(0, _routes(item))
        self.assertGreater(int(item["available_link_count"]), _routes(item))

    def test_a_hidden_url_primary_counts_as_the_route_it_is(self):
        """The Powerball shape: no url, a verified playback_id, and three
        backups of the same kind. Four routes - and refusing it stops every
        scan, which is the cost this project has already paid twice for a
        test stricter than the thing it describes."""
        item = {"name": "Powerball Cup, Round 3", "metadata_only": False,
                "available_link_count": 4, "url": None,
                "playback_id": "ctv_697b0a444f2a5e45587837413248c3da",
                "backups": [{"playback_id": "ctv_5072a733"},
                            {"playback_id": "ctv_fb7f3477"},
                            {"playback_id": "ctv_e5a70720"}]}
        self.assertEqual(4, _routes(item))

    def test_a_backup_carrying_neither_is_not_a_route(self):
        """Over-counting is still the offence it was."""
        item = {"name": "Half empty", "metadata_only": False,
                "available_link_count": 2, "url": "https://a.example/x.m3u8",
                "backups": [{"name": "Backup-1"}]}
        self.assertEqual(1, _routes(item))
        self.assertGreater(int(item["available_link_count"]), _routes(item))

    def test_a_real_card_still_counts_its_primary(self):
        source = (ROOT / "scanner" / "merger.py").read_text(encoding="utf-8")
        self.assertIn('(0 if is_metadata_only else 1) + len(backups)', source)


if __name__ == "__main__":
    unittest.main()
