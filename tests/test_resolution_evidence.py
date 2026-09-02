"""Reading a stream's real resolution instead of recording "unknown".

The scanner keeps a verified stream whose resolution it could not read and
marks it quality_unknown, and the Pages validator honours that so working
Bangladeshi channels survive. That protection is right, but "unknown" is the
absence of an answer, and 120 published cards were carrying it.

Asking the streams produced an answer for 110 of them:

    at or above 720p   75   had simply never declared it
    below 720p         35   were being published under a blanket exemption
    undeterminable     10   genuinely unreadable, and 2 of those are Bangla

So the choice was never "publish all unknowns" or "drop all unknowns" - both
would have been wrong for most of them.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import media_probe as mp  # noqa: E402
from scanner import verifier as V  # noqa: E402

AUDIT = ROOT / "reports" / "unknown-resolution-audit.json"


class SpsDecodingTests(unittest.TestCase):
    def test_a_malformed_decode_is_refused_rather_than_published(self):
        """Two real streams decoded to 8 and 16 pixels.

        A television channel is neither, and publishing a measured "8p" would
        be worse than admitting the resolution is unknown.
        """
        self.assertEqual(mp.plausible(8), 0)
        self.assertEqual(mp.plausible(16), 0)
        self.assertEqual(mp.plausible(119), 0)
        self.assertEqual(mp.plausible(720), 720)
        self.assertEqual(mp.plausible(1080), 1080)

    def test_a_master_playlist_resolution_is_read(self):
        text = (
            '#EXTM3U\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=2075000,RESOLUTION=1024x576\n'
            '/render.m3u8\n'
        )
        self.assertEqual(mp.master_playlist_height(text), 576)

    def test_the_tallest_variant_wins(self):
        text = (
            'RESOLUTION=640x360\nRESOLUTION=1920x1080\nRESOLUTION=1280x720\n'
        )
        self.assertEqual(mp.master_playlist_height(text), 1080)

    def test_an_implausible_declared_resolution_is_ignored(self):
        self.assertEqual(mp.master_playlist_height("RESOLUTION=16x16"), 0)

    def test_no_resolution_reads_as_zero(self):
        self.assertEqual(mp.master_playlist_height("#EXTM3U\n#EXTINF:4,\na.ts"), 0)
        self.assertEqual(mp.master_playlist_height(None), 0)

    def test_garbage_bytes_do_not_raise(self):
        self.assertIsNone(mp.sps_from_transport_stream(b"\x00" * 400))
        self.assertIsNone(mp.sps_from_transport_stream(b""))

    def test_the_decoder_reports_interlacing(self):
        """The property that explained Zee Bangla's old route decoding nowhere.

        frame_mbs_only_flag=0 means the picture is coded as fields, and the
        published 1080i route with no IDR frame is why real Chrome played a few
        seconds and then froze.
        """
        import inspect

        source = inspect.getsource(mp.decode_sps)
        self.assertIn("frame_mbs_only", source)
        self.assertIn("interlaced", source)


class VerifierProbeTests(unittest.TestCase):
    def test_a_missing_url_measures_nothing(self):
        self.assertEqual(V._measure_resolution_from_media({}), 0)
        self.assertEqual(V._measure_resolution_from_media({"url": ""}), 0)

    def test_an_unreachable_url_measures_nothing_and_does_not_raise(self):
        self.assertEqual(
            V._measure_resolution_from_media(
                {"url": "http://nonexistent.invalid/x.m3u8"}
            ),
            0,
        )

    def test_it_runs_only_when_nothing_else_gave_a_resolution(self):
        source = (ROOT / "scanner" / "verifier.py").read_text(encoding="utf-8")
        probe_at = source.index("_measure_resolution_from_media(item)")
        hint_at = source.index('item.get("resolution_hint")')
        self.assertLess(
            hint_at, probe_at,
            "the declared hint must be consulted before the media is fetched",
        )


class MovieVantageStatusTests(unittest.TestCase):
    """A movie refused from this egress is not a movie that is gone.

    Measured on the last movie scan: 2,053 candidates answered 403 and 20,081
    answered 404, and every one was recorded as "failed". Both egresses
    available here are datacentre, so a 403 cannot be told apart from a
    geo-restricted library.
    """

    def test_a_vantage_shaped_code_gets_a_pending_status(self):
        for code in (401, 403, 407, 451):
            self.assertEqual(
                V._vantage_shaped_movie_status(code, "network"), "geo_pending", code
            )

    def test_a_missing_file_still_fails(self):
        for code in (404, 410):
            self.assertEqual(V._vantage_shaped_movie_status(code, "network"), "", code)

    def test_a_transient_code_is_retryable_not_geo(self):
        for code in (429, 500, 502, 503, 504):
            self.assertEqual(
                V._vantage_shaped_movie_status(code, "network"),
                "retryable_pending", code,
            )

    def test_an_open_host_circuit_defers(self):
        self.assertEqual(
            V._vantage_shaped_movie_status(0, "host_circuit_open"), "host_deferred"
        )

    def test_it_applies_to_movies_only(self):
        """geo_pending is publishable for TV, so widening this would publish
        unverified TV routes onto cards."""
        source = (ROOT / "scanner" / "verifier.py").read_text(encoding="utf-8")
        block = source.split("_vantage_shaped_movie_status(status_code, error_kind)")[0]
        self.assertIn('== "movies"', block[-600:])

    def test_a_pending_movie_is_not_published(self):
        from scanner import merger as MG

        for status in ("geo_pending", "retryable_pending", "host_deferred"):
            item = {
                "url": "https://x.example.net/f.mp4",
                "source_pipeline": "movies",
                "verification_status": status,
                "publish_allowed": True,
                "resolution_height": 1080,
            }
            self.assertFalse(
                MG._is_publishable_stream(item),
                f"{status} movies must be held back, not shown",
            )


class AuditArtifactTests(unittest.TestCase):
    def test_the_audit_records_what_it_could_and_could_not_read(self):
        if not AUDIT.exists():
            self.skipTest("no audit recorded")
        payload = json.loads(AUDIT.read_text(encoding="utf-8"))
        for key in (
            "cards_without_resolution", "determined", "at_or_above_720",
            "below_720", "undeterminable",
        ):
            self.assertIn(key, payload)
        self.assertEqual(
            payload["determined"],
            payload["at_or_above_720"] + payload["below_720"],
        )

    def test_the_audit_holds_no_raw_url(self):
        if not AUDIT.exists():
            self.skipTest("no audit recorded")
        blob = AUDIT.read_text(encoding="utf-8")
        self.assertNotIn("http://", blob)
        self.assertNotIn("https://", blob)


class BackupDedupeTests(unittest.TestCase):
    """Zee Bangla shipped the same URL twice as two backups."""

    def test_one_entry_survives_per_physical_url(self):
        from scanner import merger as MG

        streams = [
            {"url": "http://h/live/u/p/1.ts", "requires_headers": False},
            {"url": "http://h/live/u/p/1.ts", "requires_headers": True},
            {"url": "http://other/x.ts", "requires_headers": False},
        ]
        out = MG._dedupe_by_physical_url(streams)
        self.assertEqual(len(out), 2)

    def test_the_header_needing_variant_is_the_one_kept(self):
        from scanner import merger as MG

        out = MG._dedupe_by_physical_url([
            {"url": "http://h/a.ts", "requires_headers": False},
            {"url": "http://h/a.ts", "requires_headers": True},
        ])
        self.assertTrue(out[0]["requires_headers"])

    def test_a_verified_variant_beats_an_unverified_one(self):
        from scanner import merger as MG

        out = MG._dedupe_by_physical_url([
            {
                "url": "http://h/a.ts", "requires_headers": True,
                "verification_status": "failed",
            },
            {
                "url": "http://h/a.ts", "requires_headers": False,
                "verification_status": "verified_global", "verified": True,
            },
        ])
        self.assertEqual(out[0]["verification_status"], "verified_global")
        self.assertTrue(
            out[0].get("folded_variant_requires_headers"),
            "the folded variant's header requirement must not vanish silently",
        )

    def test_a_different_cookie_or_drm_is_a_different_route(self):
        """The URL alone is not the route.

        Folding on the URL deleted genuinely different things to play - the
        project derives playback ids from credential values for exactly this
        reason - and an existing contract test caught it.
        """
        from scanner import merger as MG

        streams = [
            {"url": "https://h/live.m3u8", "headers": {"Cookie": "session=a"}},
            {"url": "https://h/live.m3u8", "headers": {"Cookie": "session=b"}},
            {"url": "https://h/live.m3u8", "drm": {"license_key": "kid:key"}},
        ]
        self.assertEqual(len(MG._dedupe_by_physical_url(streams)), 3)

    def test_requires_headers_alone_is_still_folded(self):
        """The duplicate this function exists for."""
        from scanner import merger as MG

        streams = [
            {"url": "https://h/live.m3u8", "headers": {}, "requires_headers": False},
            {"url": "https://h/live.m3u8", "headers": {}, "requires_headers": True},
        ]
        out = MG._dedupe_by_physical_url(streams)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["requires_headers"])

    def test_order_is_preserved(self):
        from scanner import merger as MG

        out = MG._dedupe_by_physical_url([
            {"url": "http://a/1.ts"}, {"url": "http://b/1.ts"},
            {"url": "http://a/1.ts"}, {"url": "http://c/1.ts"},
        ])
        self.assertEqual(
            [s["url"] for s in out],
            ["http://a/1.ts", "http://b/1.ts", "http://c/1.ts"],
        )

    def test_the_published_zee_card_has_no_duplicate_backup_url(self):
        path = ROOT / "data" / "channels" / "indian.json"
        if not path.exists():
            self.skipTest("no catalogue")
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("channels") or []
        for card in rows:
            if not isinstance(card, dict):
                continue
            urls = [
                str(b.get("url") or "") for b in (card.get("backups") or [])
                if isinstance(b, dict) and b.get("url")
            ]
            self.assertEqual(
                len(urls), len(set(urls)),
                f"{card.get('name')} lists the same backup URL twice",
            )


if __name__ == "__main__":
    unittest.main()


class TheDuplicateComesFromTwoSources(unittest.TestCase):
    """A repair without a pipeline rule is undone by the next scan.

    The Zee Bangla duplicate was folded out of the committed data once, by a
    script. The channels scan at 19:00 on 2026-09-02 put it straight back and
    failed the run: `rgkkw.live/.../98881.ts` as Backup-1 from
    manual-playlist-1 needing no headers, and again as Backup-3 from
    smartplaytv-worker-stream needing them.

    Two sources, two header sets, so two playback ids - which is why nothing
    keyed on the id noticed, and why a viewer was offered one dead route twice.
    """

    def test_the_same_url_from_two_sources_is_one_backup(self):
        from scanner.unplayable_primary import dedupe_backup_urls

        card = {
            "name": "Zee Bangla",
            "url": "https://stream.ottplus.bd/live/zee_bangla_abr/live/720/",
            "available_link_count": 4,
            "backups": [
                {"name": "Backup-1", "url": "http://rgkkw.live/x/98881.ts",
                 "requires_headers": False, "source_id": "manual-playlist-1",
                 "playback_id": "ctv_" + "a" * 32},
                {"name": "Backup-2",
                 "url": "https://stream.ottplus.bd/live/zee_bangla_abr/index.m3u8"},
                {"name": "Backup-3", "url": "http://rgkkw.live/x/98881.ts",
                 "requires_headers": True,
                 "source_id": "smartplaytv-worker-stream",
                 "playback_id": "ctv_" + "b" * 32},
            ],
        }
        dropped = dedupe_backup_urls([card])

        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["dropped"], "Backup-3")
        urls = [row["url"] for row in card["backups"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertIn("http://rgkkw.live/x/98881.ts", urls)

    def test_the_first_spelling_of_the_route_is_the_one_kept(self):
        from scanner.unplayable_primary import dedupe_backup_urls

        card = {"name": "A", "url": "", "backups": [
            {"name": "keep", "url": "http://one/x.ts"},
            {"name": "drop", "url": "http://one/x.ts"},
        ]}
        dedupe_backup_urls([card])
        self.assertEqual([row["name"] for row in card["backups"]], ["keep"])

    def test_a_backup_that_repeats_the_primary_goes(self):
        from scanner.unplayable_primary import dedupe_backup_urls

        card = {"name": "A", "url": "http://one/x.ts", "backups": [
            {"name": "same as primary", "url": "http://one/x.ts"},
            {"name": "real backup", "url": "http://two/y.ts"},
        ]}
        dedupe_backup_urls([card])
        self.assertEqual([row["name"] for row in card["backups"]],
                         ["real backup"])

    def test_the_link_count_follows(self):
        from scanner.unplayable_primary import dedupe_backup_urls

        card = {"name": "A", "url": "http://p/x", "available_link_count": 3,
                "backups": [{"url": "http://one/x.ts"},
                            {"url": "http://one/x.ts"}]}
        dedupe_backup_urls([card])
        self.assertEqual(card["available_link_count"], 2)

    def test_distinct_routes_are_all_kept(self):
        from scanner.unplayable_primary import dedupe_backup_urls

        card = {"name": "A", "url": "http://p/x", "backups": [
            {"url": "http://one/x.ts"}, {"url": "http://two/y.ts"},
            {"url": "http://three/z.ts"}]}
        self.assertEqual(dedupe_backup_urls([card]), [])
        self.assertEqual(len(card["backups"]), 3)

    def test_the_scan_applies_it_and_not_only_the_script(self):
        channels = (Path(__file__).resolve().parent.parent / "scanner"
                    / "channels.py").read_text(encoding="utf-8")
        self.assertIn("unplayable_primary.dedupe_backup_urls", channels)

        script = (Path(__file__).resolve().parent.parent / "scripts"
                  / "repair-unplayable-primaries.py").read_text(encoding="utf-8")
        self.assertIn("unplayable_primary.dedupe_backup_urls", script,
                      "the script must share the rule, not copy it")

    def test_no_published_card_repeats_a_route(self):
        import glob

        for path in glob.glob(str(Path(__file__).resolve().parent.parent
                                  / "data" / "channels" / "*.json")):
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            for card in payload.get("channels") or []:
                urls = [str(row.get("url") or "")
                        for row in card.get("backups") or []
                        if isinstance(row, dict) and str(row.get("url") or "")]
                with self.subTest(card=card.get("name")):
                    self.assertEqual(
                        len(urls), len(set(urls)),
                        f"{card.get('name')} repeats a backup route")
