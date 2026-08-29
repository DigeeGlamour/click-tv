"""The four sportlive18 sources: adapters, per-source rules, safety gates.

Every fixture here is the shape the real feed returned on 2026-08-28, not an
invented one:

  fancode.json   40 rows, 5 LIVE and 35 UPCOMING. adfree_url and dai_url were
                 byte-identical on every LIVE row, and the 35 UPCOMING rows
                 carried no URL at all - so the metadata is the only thing they
                 have, which is why the JSON is read instead of an M3U.
  sonyliv.json   20 rows, 8 with isLive true, and NO start-time field at all.
                 dai_url, pub_url and video_url were byte-identical. The same
                 fixture appears once per audio language.
  playlist.m3u   113 entries, every one carrying the author's credit in the
                 channel name AND in the group title.
  jtvplus7.m3u   1,409 entries, 44 in the Bengali group, 1,108 ClearKey keys,
                 and the Akamai token expiry in a Cookie rather than the URL.
"""
import importlib.util
import re
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import source_filters as sf  # noqa: E402
from scanner.parsers import event_adapters as ea  # noqa: E402
from scanner.source_loader import parse_source_content  # noqa: E402

TV = json.loads((ROOT / "config" / "sources" / "tv.json").read_text(encoding="utf-8"))
TODAY = json.loads(
    (ROOT / "config" / "sources" / "today-match.json").read_text(encoding="utf-8")
)


def _source(config, source_id):
    return next(
        (s for s in config["sources"] if s.get("id") == source_id), None
    )


FANCODE_FIXTURE = {
    "matches": [
        {
            "match_name": "Central Delhi Kings vs Purani Dilli-6 [English]",
            "title": "Delhi Premier League, 2026 [English]",
            "status": "LIVE",
            "startTime": "07:00:00 PM 28-08-2026",
            "event_name": "Delhi Premier League, 2026",
            "event_category": "Cricket",
            "team_1": "Central Delhi Kings",
            "team_2": "Purani Dilli-6",
            "match_id": "4248006_ENG",
            "adfree_url": "https://in-mc-flive.fancode.com/mumbai/adfree.m3u8",
            "dai_url": "https://in-mc-flive.fancode.com/mumbai/dai.m3u8",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64)",
        },
        {
            "match_name": "A vs B",
            "status": "UPCOMING",
            "startTime": "04:00:00 PM 29-08-2026",
            "event_name": "Some Cup",
            "event_category": "Football",
            "match_id": "999_ENG",
            "adfree_url": None,
            "dai_url": None,
        },
        {
            "match_name": "C vs D",
            "status": "COMPLETED",
            "startTime": "01:00:00 PM 27-08-2026",
            "match_id": "888_ENG",
        },
        {
            "match_name": "Only DAI vs Team",
            "status": "LIVE",
            "startTime": "08:00:00 PM 28-08-2026",
            "match_id": "777_ENG",
            "dai_url": "https://in-mc-flive.fancode.com/mumbai/only-dai.m3u8",
        },
    ]
}

SONYLIV_FIXTURE = {
    "matches": [
        {
            "match_name": "Thailand Women vs Hong Kong, China Women - 28 Aug 2026 [ENG]",
            "event_name": "DP World Women's Asia Cup 2026",
            "event_category": "Cricket",
            "isLive": True,
            "contentId": "1090540693_ENG",
            "broadcast_channel": "Sony Sports Ten 5",
            "dai_url": "https://sonydaimenew.akamaized.net/hls/live/ENG/master.m3u8",
            "pub_url": "https://sonydaimenew.akamaized.net/hls/live/ENG/master.m3u8",
            "video_url": "https://sonydaimenew.akamaized.net/hls/live/ENG/master.m3u8",
        },
        {
            "match_name": "Thailand Women vs Hong Kong, China Women - 28 Aug 2026 [HIN]",
            "event_name": "DP World Women's Asia Cup 2026",
            "event_category": "Cricket",
            "isLive": True,
            "contentId": "1090540693_HIN",
            "broadcast_channel": "Sony Sports Ten 5",
            "dai_url": "https://sonydaimenew.akamaized.net/hls/live/HIN/master.m3u8",
            "pub_url": "https://sonydaimenew.akamaized.net/hls/live/HIN/master.m3u8",
            "video_url": "https://sonydaimenew.akamaized.net/hls/live/HIN/master.m3u8",
        },
        {
            "match_name": "Upcoming - ACC Women's Asia Cup 2026",
            "event_name": "ACC Women's Asia Cup 2026",
            "event_category": "Cricket",
            "isLive": False,
            "contentId": "555",
            "broadcast_channel": "Sony Sports Ten 2",
        },
    ]
}


class FancodeAdapterTests(unittest.TestCase):
    def setUp(self):
        ea.ADAPTER_STATS.clear()
        self.records = ea.adapt_sportlive_fancode(FANCODE_FIXTURE, "fixture")

    def _by_name(self, fragment):
        return next(
            r for r in self.records if fragment.lower() in str(r.get("name")).lower()
        )

    def test_every_row_becomes_a_record(self):
        self.assertEqual(len(self.records), 4)

    def test_status_is_passed_through_for_the_lifecycle_router(self):
        """Routing is the router's job; the adapter must not pre-empt it."""
        statuses = {str(r.get("status_raw") or "").upper() for r in self.records}
        self.assertEqual(statuses, {"LIVE", "UPCOMING", "COMPLETED"})

    def test_adfree_is_offered_before_dai(self):
        record = self._by_name("Central Delhi Kings")
        servers = record["channels"][0]["servers"]
        self.assertGreaterEqual(len(servers), 2)
        self.assertIn("ad-free", servers[0]["server_label"].lower())
        self.assertIn("adfree", servers[0]["url"])

    def test_dai_is_the_fallback_when_adfree_is_missing(self):
        record = self._by_name("Only DAI")
        servers = record["channels"][0]["servers"]
        self.assertEqual(len(servers), 1)
        self.assertIn("only-dai", servers[0]["url"])

    def test_an_upcoming_row_keeps_its_metadata_without_a_stream(self):
        """The 35 real UPCOMING rows carry no URL. The metadata is the point.

        _record drops a channel that has no server and marks the record
        metadata_only, which is the behaviour the upcoming pipeline already
        expects - the row is kept for its name, competition and start time
        rather than discarded for having no stream yet.
        """
        record = self._by_name("A vs B")
        self.assertEqual(record["channels"], [])
        self.assertTrue(record["metadata_only"])
        self.assertEqual(record["start_time"], "2026-08-29T10:00:00+00:00")
        self.assertEqual(record["competition"], "Some Cup")
        self.assertEqual(record["sport"], "football")

    def test_a_live_row_is_not_metadata_only(self):
        record = self._by_name("Central Delhi Kings")
        self.assertFalse(record.get("metadata_only"))
        self.assertTrue(record["channels"][0]["servers"])

    def test_start_time_is_parsed_to_timezone_aware_utc(self):
        record = self._by_name("Central Delhi Kings")
        self.assertEqual(record["start_time"], "2026-08-28T13:00:00+00:00")
        self.assertTrue(record["start_time"].endswith("+00:00"))

    def test_the_feeds_user_agent_reaches_the_server(self):
        """The stream is served only to that agent; losing it makes a 403."""
        record = self._by_name("Central Delhi Kings")
        for server in record["channels"][0]["servers"]:
            self.assertEqual(
                server["headers"].get("User-Agent"),
                "Mozilla/5.0 (X11; Linux x86_64)",
            )

    def test_a_row_with_no_name_is_skipped_and_counted(self):
        ea.ADAPTER_STATS.clear()
        ea.adapt_sportlive_fancode({"matches": [{"status": "LIVE"}]}, "fixture")
        self.assertEqual(ea.ADAPTER_STATS["fixture"]["skipped"], 1)

    def test_it_does_not_disturb_the_existing_fancode_adapter(self):
        """sm-fancode reads a different shape and must keep working."""
        self.assertIn("fancode", ea.ADAPTERS)
        self.assertIsNot(ea.ADAPTERS["fancode"], ea.ADAPTERS["sportlive_fancode"])


class SonylivAdapterTests(unittest.TestCase):
    def setUp(self):
        ea.ADAPTER_STATS.clear()
        self.records = ea.adapt_sportlive_sonyliv(SONYLIV_FIXTURE, "fixture")

    def test_islive_routes_the_status(self):
        statuses = [str(r.get("status_raw")) for r in self.records]
        self.assertEqual(statuses.count("LIVE"), 2)
        self.assertEqual(statuses.count("UPCOMING"), 1)

    def test_the_language_suffix_does_not_split_one_fixture_into_two(self):
        """ENG and HIN are one match with two servers, not two cards."""
        live = [r for r in self.records if r["status_raw"] == "LIVE"]
        self.assertEqual(len({r["name"] for r in live}), 1)

    def test_the_language_is_carried_on_the_server_label(self):
        live = [r for r in self.records if r["status_raw"] == "LIVE"]
        labels = {
            s["server_label"]
            for r in live for s in r["channels"][0]["servers"]
        }
        self.assertTrue(any("ENG" in l for l in labels))
        self.assertTrue(any("HIN" in l for l in labels))

    def test_identical_urls_are_offered_once(self):
        """dai, pub and video were byte-identical on every real live row."""
        live = [r for r in self.records if r["status_raw"] == "LIVE"][0]
        self.assertEqual(len(live["channels"][0]["servers"]), 1)

    def test_no_start_time_is_invented(self):
        """The feed has no start-time field at all."""
        for record in self.records:
            self.assertEqual(record["start_time"], "")

    def test_the_broadcast_channel_names_the_channel(self):
        live = [r for r in self.records if r["status_raw"] == "LIVE"][0]
        self.assertEqual(live["channels"][0]["channel_name"], "Sony Sports Ten 5")

    def test_content_id_is_the_identity(self):
        live = [r for r in self.records if r["status_raw"] == "LIVE"][0]
        self.assertTrue(str(live.get("identity")).startswith("1090540693"))

    def test_it_does_not_disturb_the_existing_sonyliv_adapter(self):
        self.assertIsNot(ea.ADAPTERS["sonyliv"], ea.ADAPTERS["sportlive_sonyliv"])


class HotstarNameCleanupTests(unittest.TestCase):
    def setUp(self):
        self.source = _source(TV, "sportlive-hotstar-backup")
        self.assertIsNotNone(self.source, "the Hotstar backup source is missing")
        self.patterns = self.source["source_rules"]["strip_patterns"]

    def test_the_examples_the_owner_gave(self):
        for dirty, clean in (
            ("Star Jalsha @rtxcric", "Star Jalsha"),
            ("Jalsha Movies @rtxcric", "Jalsha Movies"),
            ("Colors Bangla HD @rtxcric", "Colors Bangla HD"),
        ):
            self.assertEqual(sf.clean_name(dirty, self.patterns), clean)

    def test_a_group_title_credit_is_cleaned_too(self):
        self.assertEqual(sf.clean_name("Sports By @rtxcric", self.patterns), "Sports")
        self.assertEqual(
            sf.clean_name("Entertainment By @rtxcric", self.patterns), "Entertainment"
        )

    def test_a_name_without_the_credit_is_untouched(self):
        for name in ("Zee Bangla", "Star Sports 1 HD", "Colors Bangla"):
            self.assertEqual(sf.clean_name(name, self.patterns), name)

    def test_the_rules_belong_to_this_source_only(self):
        """A global replacement would damage an unrelated source."""
        for source in TV["sources"]:
            if source.get("id") == "sportlive-hotstar-backup":
                continue
            rules = source.get("source_rules") or {}
            self.assertNotIn(
                "strip_patterns", rules,
                f"{source.get('id')} also strips names; this must stay local",
            )

    def test_an_uncompilable_pattern_is_recorded_not_swallowed(self):
        """A JSON-escaping mistake looked exactly like a rule with no matches.

        Measured: all 113 names went through unchanged because every stored
        pattern had a doubled backslash, and nothing said so.
        """
        sf.INVALID_PATTERNS.clear()
        sf.clean_name("anything", ["*not a regex("])
        self.assertTrue(sf.INVALID_PATTERNS)
        sf.INVALID_PATTERNS.clear()

    def test_the_configured_patterns_all_compile(self):
        sf.INVALID_PATTERNS.clear()
        sf.clean_name("Star Jalsha @rtxcric", self.patterns)
        self.assertEqual(sf.INVALID_PATTERNS, {})


class JioSourceFilterTests(unittest.TestCase):
    def setUp(self):
        self.source = _source(TV, "sportlive-jiotv-targeted")
        self.assertIsNotNone(self.source, "the targeted Jio source is missing")

    def _run(self, items):
        return sf.apply_source_rules(items, self.source)

    def test_a_source_with_no_rules_is_returned_unchanged(self):
        items = [{"name": "Anything", "group_title": "Whatever"}]
        kept, telemetry = sf.apply_source_rules(items, {"id": "plain"})
        self.assertEqual(len(kept), 1)
        self.assertFalse(telemetry["rules_declared"])

    def test_the_bengali_group_is_included(self):
        kept, _t = self._run([{"name": "Zee 24 Ghanta", "group_title": "Bengali"}])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source_include_reason"], "group allowed")

    def test_a_named_channel_outside_that_group_is_included(self):
        kept, _t = self._run([{"name": "Zee Bangla", "group_title": "Zee5"}])
        self.assertEqual(len(kept), 1)

    def test_the_sports_families_are_included_by_prefix(self):
        kept, _t = self._run([
            {"name": "Star Sports 1 HD", "group_title": "STAR"},
            {"name": "Star Sports Select 1", "group_title": "STAR"},
            {"name": "Sony Ten 5", "group_title": "SONY"},
        ])
        self.assertEqual(len(kept), 3)

    def test_an_unrelated_channel_is_dropped_with_a_reason(self):
        kept, telemetry = self._run([
            {"name": "Star Gold HD", "group_title": "Hindi"},
            {"name": "Some Telugu Channel", "group_title": "Telugu"},
        ])
        self.assertEqual(kept, [])
        self.assertEqual(telemetry["dropped"], 2)
        self.assertTrue(telemetry["reasons"], "a drop with no reason is a silent skip")

    def test_every_drop_is_counted(self):
        kept, telemetry = self._run([
            {"name": "Zee Bangla", "group_title": "Zee5"},
            {"name": "Star Gold HD", "group_title": "Hindi"},
        ])
        self.assertEqual(telemetry["parsed"], 2)
        self.assertEqual(telemetry["kept"], 1)
        self.assertEqual(telemetry["dropped"], 1)
        self.assertEqual(sum(telemetry["reasons"].values()), telemetry["dropped"])


class BengaliCartoonDetectionTests(unittest.TestCase):
    """Not three hardcoded names - the owner asked for future ones too."""

    def test_the_three_named_channels_are_detected(self):
        for name, group in (
            ("Nick Bangla", "Bengali"),
            ("Sonic Bangla", "Kids"),
            ("SONY YAY Bengali", "SONY"),
        ):
            self.assertTrue(
                sf.is_bangla_kids({"name": name, "group_title": group}), name
            )

    def test_a_channel_the_source_adds_later_is_detected(self):
        for name, group in (
            ("Chutti TV Bangla", "Cartoon"),
            ("Pogo Bengali", "Kids"),
            ("Disney Junior Bangla", "Bengali"),
            ("Cartoon Network Bangla", "Bengali"),
        ):
            self.assertTrue(
                sf.is_bangla_kids({"name": name, "group_title": group}), name
            )

    def test_a_kids_channel_in_another_language_is_not(self):
        for name, group in (("Nick Hindi", "Kids"), ("Sonic Tamil", "Kids")):
            self.assertFalse(
                sf.is_bangla_kids({"name": name, "group_title": group}), name
            )

    def test_a_bengali_channel_that_is_not_kids_is_not(self):
        for name in ("Zee Bangla", "Star Jalsha HD", "News18 Bangla"):
            self.assertFalse(
                sf.is_bangla_kids({"name": name, "group_title": "Bengali"}), name
            )

    def test_a_detected_channel_is_routed_to_cartoon_with_bangla_metadata(self):
        source = _source(TV, "sportlive-jiotv-targeted")
        kept, _t = sf.apply_source_rules(
            [{"name": "Nick Bangla", "group_title": "Bengali"}], source
        )
        self.assertEqual(kept[0]["force_category"], "Cartoon")
        self.assertEqual(kept[0]["language_hint"], "Bangla")


class SafetyGateTests(unittest.TestCase):
    def test_an_expired_token_is_rejected_before_any_probe(self):
        now = time.time()
        item = {
            "url": "https://jiotvmblive.cdn.jio.com/bpk-tv/X/index.mpd",
            "headers": {
                "Cookie": "__hdnea__=st=1~exp=%d~acl=/*~hmac=ab" % int(now - 60)
            },
        }
        expired, why = sf.token_is_expired(item, now=now)
        self.assertTrue(expired)
        self.assertIn("expired", why)

    def test_the_expiry_is_read_from_the_cookie_not_only_the_url(self):
        """The measured source puts it in a Cookie, not the URL."""
        now = time.time()
        item = {
            "url": "https://h/x.mpd",
            "headers": {"Cookie": "__hdnea__=st=1~exp=%d~acl=/*" % int(now + 3600)},
        }
        self.assertIsNotNone(sf.token_expiry_seconds_left(item, now=now))

    def test_a_live_token_is_not_rejected(self):
        now = time.time()
        item = {
            "url": "https://h/x.mpd",
            "headers": {"Cookie": "__hdnea__=st=1~exp=%d~acl=/*" % int(now + 3600)},
        }
        self.assertFalse(sf.token_is_expired(item, now=now)[0])

    def test_an_entry_with_no_token_is_never_called_expired(self):
        self.assertFalse(sf.token_is_expired({"url": "https://h/x.m3u8"})[0])

    def test_a_remote_url_declared_as_clearkey_is_refused(self):
        usable, why = sf.clearkey_is_usable(
            {"license_type": "clearkey", "license_key": "https://licence.example/get"}
        )
        self.assertFalse(usable)
        self.assertIn("remote URL", why)

    def test_a_static_hex_key_is_accepted(self):
        usable, _why = sf.clearkey_is_usable({
            "license_type": "clearkey",
            "license_key": (
                "4b35e987730e55528cf07a0ef12b10e1:"
                "3fcc6d95fc399ff2814024d337cfa116"
            ),
        })
        self.assertTrue(usable)

    def test_a_clearkey_with_no_key_is_refused(self):
        self.assertFalse(
            sf.clearkey_is_usable({"license_type": "clearkey", "license_key": ""})[0]
        )

    def test_a_non_clearkey_drm_is_left_alone(self):
        self.assertTrue(
            sf.clearkey_is_usable(
                {"license_type": "widevine", "license_url": "https://x/l"}
            )[0]
        )

    def test_no_drm_at_all_is_left_alone(self):
        self.assertTrue(sf.clearkey_is_usable({})[0])
        self.assertTrue(sf.clearkey_is_usable(None)[0])

    def test_the_two_new_tv_sources_switch_both_gates_on(self):
        for source_id in ("sportlive-hotstar-backup", "sportlive-jiotv-targeted"):
            rules = _source(TV, source_id)["source_rules"]
            self.assertTrue(rules.get("reject_expired_tokens"), source_id)
            self.assertTrue(rules.get("require_usable_clearkey"), source_id)


class HeaderAndDrmRoundTripTests(unittest.TestCase):
    def test_headers_and_drm_survive_the_source_rules(self):
        source = _source(TV, "sportlive-jiotv-targeted")
        item = {
            "name": "Star Jalsha HD",
            "group_title": "Bengali",
            "url": "https://jiotvmblive.cdn.jio.com/bpk-tv/Star_Jalsha_HD_MOB/x.mpd",
            "headers": {
                "User-Agent": "Sayan1o",
                "Cookie": "__hdnea__=st=1~exp=%d~acl=/*" % int(time.time() + 3600),
                "Origin": "https://www.jiotv.com/",
                "Referer": "https://www.jiotv.com/",
            },
            "drm": {
                "license_type": "clearkey",
                "license_key": (
                    "4b35e987730e55528cf07a0ef12b10e1:"
                    "3fcc6d95fc399ff2814024d337cfa116"
                ),
            },
        }
        kept, _t = sf.apply_source_rules([item], source)
        self.assertEqual(len(kept), 1)
        survivor = kept[0]
        for header in ("User-Agent", "Cookie", "Origin", "Referer"):
            self.assertIn(header, survivor["headers"], header)
        self.assertEqual(survivor["drm"]["license_type"], "clearkey")


class ConfigPlacementTests(unittest.TestCase):
    def test_the_event_sources_are_backups_not_primaries(self):
        for source_id, beaten in (
            ("sportlive-fancode-backup", "sm-fancode"),
            ("sportlive-sonyliv-backup", "srhady-sonyliv-live"),
        ):
            new = _source(TODAY, source_id)
            existing = _source(TODAY, beaten)
            self.assertIsNotNone(new, source_id)
            self.assertIsNotNone(existing, beaten)
            self.assertLess(
                int(new["priority"]), int(existing["priority"]),
                f"{source_id} must sit below {beaten}",
            )

    def test_the_tv_sources_sit_below_the_existing_jio_sources(self):
        for source_id, beaten in (
            ("sportlive-hotstar-backup", "sm-iptv-jiohotstar"),
            ("sportlive-jiotv-targeted", "sm-iptv-jiotv"),
        ):
            new = _source(TV, source_id)
            existing = _source(TV, beaten)
            self.assertIsNotNone(new, source_id)
            self.assertLess(int(new["priority"]), int(existing["priority"]))

    def test_the_event_sources_are_not_added_to_upcoming_as_well(self):
        """Adding one feed twice fetches it twice and splits one fixture."""
        upcoming = json.loads(
            (ROOT / "config" / "sources" / "upcoming.json").read_text(encoding="utf-8")
        )
        ids = {s.get("id") for s in (upcoming.get("sources") or [])}
        self.assertNotIn("sportlive-fancode-backup", ids)
        self.assertNotIn("sportlive-sonyliv-backup", ids)

    def test_the_canonical_willow_sources_have_no_added_mirror(self):
        """Both are already configured; a mirror would duplicate every card."""
        blob = json.dumps(TODAY)
        self.assertIn("srhady/willow-event", blob)
        self.assertNotIn("sportlive18/willow", blob)
        self.assertNotIn("sportlive18/Willow", blob)

    def test_the_refused_sources_are_in_no_config(self):
        refused = (
            "All-Playlist", "above18", "sky-sport", "jtvplus2", "jtv2.m3u",
            "jtv3", "jtv4", "jtvplus4", "jtvplus5", "jtvplus6", "mixiptv",
            "jtvplus8", "star.json",
        )
        for path in sorted((ROOT / "config" / "sources").glob("*.json")):
            text = path.read_text(encoding="utf-8")
            for name in refused:
                self.assertNotIn(name, text, f"{name} in {path.name}")

    def test_both_new_tv_sources_preserve_headers_and_drm(self):
        for source_id in ("sportlive-hotstar-backup", "sportlive-jiotv-targeted"):
            source = _source(TV, source_id)
            self.assertTrue(source.get("preserve_source_headers"), source_id)
            self.assertTrue(source.get("preserve_drm"), source_id)

    def test_every_new_source_declares_its_adapter_or_rules(self):
        for config, source_id in (
            (TODAY, "sportlive-fancode-backup"),
            (TODAY, "sportlive-sonyliv-backup"),
        ):
            self.assertIn("adapter", _source(config, source_id))
        for source_id in ("sportlive-hotstar-backup", "sportlive-jiotv-targeted"):
            self.assertIn("source_rules", _source(TV, source_id))


class FunnelIntegrationTests(unittest.TestCase):
    """The rules must apply through parse_source_content, not only directly."""

    def test_an_m3u_source_is_filtered_by_its_own_rules(self):
        source = _source(TV, "sportlive-jiotv-targeted")
        content = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-name="Zee Bangla" group-title="Zee5",Zee Bangla\n'
            "https://h/zee.m3u8\n"
            '#EXTINF:-1 tvg-name="Star Gold" group-title="Hindi",Star Gold HD\n'
            "https://h/gold.m3u8\n"
        )
        items, detected = parse_source_content(content, source)
        self.assertEqual(detected, "m3u")
        self.assertEqual([i["name"] for i in items], ["Zee Bangla"])

    def test_a_source_without_rules_passes_everything_through(self):
        content = (
            "#EXTM3U\n"
            '#EXTINF:-1 group-title="Hindi",Star Gold HD\n'
            "https://h/gold.m3u8\n"
        )
        items, _detected = parse_source_content(
            content, {"id": "no-rules", "format": "m3u"}
        )
        self.assertEqual(len(items), 1)


class HarnessSecretRedactionTests(unittest.TestCase):
    """The browser hands its own error text back to the harness, and both
    players quote the full failing URL in it. For an Akamai-signed route that
    text carries a live `hmac=` value, so a report written straight from the
    page published a working credential - the redacted URL templates beside it
    were safe, the quoted error string was not.
    """

    @staticmethod
    def _harness():
        spec = importlib.util.spec_from_file_location(
            "sustained_playback_check",
            ROOT / "scripts" / "sustained-playback-check.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_signed_url_in_browser_error_text_is_redacted(self):
        harness = self._harness()
        quoted = (
            "error: Access to XMLHttpRequest at 'https://cdn.example.net/x.m3u8"
            "?hdnea=exp=1788028635~acl=/*~id=8793402~hmac=df6b493ec1f328fde877'"
        )
        cleaned = harness.redact_secrets(quoted)
        self.assertNotIn("df6b493ec1f328fde877", cleaned)
        self.assertIn("hmac={redacted}", cleaned)
        self.assertIn("hdnea={redacted}", cleaned)
        # On this Akamai form the whole `exp=...~acl=...~hmac=...` blob is the
        # value of hdnea, so redacting hdnea takes the expiry with it. Where an
        # expiry stands on its own it is kept: it says when, not how to
        # authenticate, and it is what makes an expiry finding readable.
        self.assertIn(
            "exp=1788028635",
            harness.redact_secrets("?exp=1788028635&token=abc123def456"),
        )

    def test_redaction_reaches_nested_lists_and_dicts(self):
        harness = self._harness()
        cleaned = harness.redact_secrets(
            {"fatal_errors": ["token=abc123def456"], "notes": [{"u": "sig=zzz9"}]}
        )
        self.assertEqual(cleaned["fatal_errors"], ["token={redacted}"])
        self.assertEqual(cleaned["notes"][0]["u"], "sig={redacted}")

    def test_no_committed_playback_report_carries_a_live_token(self):
        pattern = re.compile(
            r"(?i)(?:hmac|hdnea|hdntl|edge-cache-token|sig|signature)="
            r"(?!\{redacted\})[^&\s\"'~]{8,}"
        )
        # Scoped to the reports this harness writes. Other scanner artifacts
        # are regenerated by a full scan and are checked by their own owners;
        # see the security note in the completion report for one that was
        # measured writing a live `hdntl=` value.
        checked = 0
        for path in sorted((ROOT / "reports").glob("sportlive-playback-*.json")):
            text = path.read_text(encoding="utf-8", errors="replace")
            checked += 1
            self.assertIsNone(
                pattern.search(text),
                f"{path.name} carries an unredacted signed token",
            )
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
