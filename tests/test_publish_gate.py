"""Nothing that a viewer cannot actually play may reach the public JSON."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.browser_reachability import (  # noqa: E402
    item_is_browser_reachable,
    item_is_proven_live,
    mark_browser_unreachable,
    mark_unproven_items,
    requires_same_run_proof,
    route_is_browser_reachable,
)


class BrowserReachabilityTests(unittest.TestCase):
    def test_http_on_a_bare_ip_has_no_viewer_route(self):
        # Browser blocks it as mixed content; the Cloudflare Worker answers
        # 403 "error code: 1003" for any raw IP host. Both routes are dead.
        self.assertFalse(route_is_browser_reachable("http://115.187.41.216:8080/hls/x/index.m3u8"))
        self.assertFalse(route_is_browser_reachable("http://103.190.133.68:1935/live/playlist.m3u8"))

    def test_https_is_always_reachable_even_on_an_ip_or_odd_port(self):
        self.assertTrue(route_is_browser_reachable("https://115.187.41.216:8080/x.m3u8"))
        self.assertTrue(route_is_browser_reachable("https://host.example:7000/x.m3u8"))

    def test_http_on_a_hostname_is_reachable_through_the_proxy(self):
        # Measured against the live proxy: non-standard ports stream fine.
        self.assertTrue(route_is_browser_reachable("http://iptv.prosto.tv:7000/ch78/video.m3u8"))
        self.assertTrue(route_is_browser_reachable("http://live.balajibroadband.com:3500/live/471.m3u8"))

    def test_a_reachable_backup_keeps_the_card_alive(self):
        item = {
            "name": "Discovery",
            "url": "http://66.102.126.10:8000/play/a076/index.m3u8",
            "backups": [{"url": "https://cdn.example.com/discovery/index.m3u8"}],
        }
        self.assertTrue(item_is_browser_reachable(item))
        hidden, _ = mark_browser_unreachable([item])
        self.assertEqual(hidden, 0)
        # The dead primary is replaced by the working backup.
        self.assertEqual(item["url"], "https://cdn.example.com/discovery/index.m3u8")

    def test_an_item_with_no_reachable_route_is_hidden(self):
        item = {"name": "Probashi TV", "url": "http://158.69.24.53:8080/probashi_tv/index.m3u8"}
        hidden, records = mark_browser_unreachable([item])
        self.assertEqual(hidden, 1)
        self.assertIs(item["publish_allowed"], False)
        self.assertEqual(records[0]["reason"], "http_bare_ip_host")

    def test_an_upcoming_announcement_without_a_link_survives(self):
        # Upcoming matches are published before a stream exists; the card says
        # "stream link will be added before the match starts". Treating an empty
        # URL as unreachable emptied the whole Upcoming tab.
        announcement = {
            "name": "The Hundred Men's Final",
            "url": "",
            "source_pipeline": "upcoming",
            "verification_status": "metadata_only",
            "metadata_only": True,
        }
        self.assertTrue(item_is_browser_reachable(announcement))
        hidden, _ = mark_browser_unreachable([announcement])
        self.assertEqual(hidden, 0)
        self.assertIsNone(announcement.get("publish_allowed"))


class SameRunProofTests(unittest.TestCase):
    def test_only_same_run_proof_or_manual_trust_may_publish(self):
        for status in ("verified", "verified_global", "verified_bd", "verified_proxy", "manual_trusted"):
            self.assertTrue(item_is_proven_live({"verification_status": status}), status)

    def test_verified_flag_alone_is_proof(self):
        self.assertTrue(item_is_proven_live({"verified": True}))

    def test_stale_last_good_is_never_published(self):
        # This status means "this run's check failed, reusing the old link".
        # Every expired CDN link on the live site carried exactly this status.
        self.assertFalse(item_is_proven_live({"verification_status": "stale_last_good"}))
        # The preserved-last-good path sets publish_allowed but leaves verified
        # False on purpose, so publish_allowed must never count as proof.
        self.assertFalse(item_is_proven_live({
            "verification_status": "stale_last_good",
            "verified": False,
            "publish_allowed": True,
        }))

    def test_unproven_pending_states_are_not_published(self):
        for status in ("retryable_pending", "host_deferred", "failed_bd", "pending_player_proof"):
            self.assertFalse(item_is_proven_live({"verification_status": status}), status)

    def test_bangladesh_geo_locked_channels_stay_by_default(self):
        # These cannot be verified from a GitHub runner but do work for the
        # Bangladeshi audience, so removing them would delete working channels.
        for status in ("geo_pending", "bd_protected_pending"):
            self.assertTrue(item_is_proven_live({"verification_status": status}), status)
            self.assertFalse(item_is_proven_live({"verification_status": status}, allow_geo_pending=False), status)

    def test_manual_catalogue_entries_need_no_network_status(self):
        self.assertTrue(item_is_proven_live({"manual_source": True}))
        self.assertFalse(item_is_proven_live({}))

    def test_mark_unproven_items_records_why(self):
        items = [
            {"name": "Live", "verification_status": "verified_global"},
            {"name": "Expired", "verification_status": "stale_last_good"},
        ]
        hidden, records = mark_unproven_items(items, "channel")
        self.assertEqual(hidden, 1)
        self.assertEqual(records[0]["name"], "Expired")
        self.assertEqual(records[0]["reason"], "stale_last_good")
        self.assertIs(items[0].get("publish_allowed"), None)
        self.assertIs(items[1]["publish_allowed"], False)


class GateScopeTests(unittest.TestCase):
    def test_same_run_proof_covers_live_tv(self):
        self.assertTrue(requires_same_run_proof({"content_kind": "live_tv"}))
        self.assertTrue(requires_same_run_proof({"source_pipeline": "tv"}))

    def test_movies_are_excluded_by_default(self):
        # Measured, not assumed: through the real player path "verified_global"
        # movies scored the same as every pending status, so the status carries
        # no playability signal for movies and must not delete 38% of them.
        movie = {"content_kind": "movie", "source_pipeline": "movies"}
        self.assertFalse(requires_same_run_proof(movie))
        self.assertTrue(requires_same_run_proof(movie, apply_to_movies=True))


class PublishedCatalogueTests(unittest.TestCase):
    """The data currently in the repository must already satisfy both gates."""

    def test_no_published_channel_is_unplayable(self):
        offenders = []
        for path in sorted((ROOT / "data" / "channels").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for channel in payload.get("channels", []):
                if not item_is_browser_reachable(channel):
                    offenders.append(f"{path.name}: {channel.get('name')} (no viewer route)")
                elif not item_is_proven_live(channel):
                    offenders.append(
                        f"{path.name}: {channel.get('name')} ({channel.get('verification_status')})"
                    )
        self.assertEqual(offenders, [], "Unplayable entries are published:\n" + "\n".join(offenders))

    def test_no_published_movie_lost_its_viewer_route(self):
        offenders = []
        for path in sorted((ROOT / "data" / "movies").glob("*/page-*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for movie in payload.get("items", []):
                if not item_is_browser_reachable(movie):
                    offenders.append(f"{path.parent.name}: {movie.get('name')}")
        self.assertEqual(offenders, [], "Unreachable movies published:\n" + "\n".join(offenders))

    def test_publish_gate_is_configured(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        gate = settings.get("publish_gate")
        self.assertIsInstance(gate, dict)
        self.assertIs(gate.get("require_same_run_proof"), True)
        self.assertIs(gate.get("apply_to_movies"), False)


if __name__ == "__main__":
    unittest.main()
