"""The thirteen event feeds, one adapter per real JSON layout.

Today Match and Upcoming are now built from exactly thirteen sources, and no
two of them share a shape. The last two are the sportlive18 FanCode and
SonyLIV backups; their own layouts, counts and folding rules are held in
tests/test_sportlive_sources.py, and the registry counts here include them. sonyliv nests the stream under
`live_matches[].playback_info`; axsports and bingstream carry a `link_live[]`
array where each entry holds both a tokenless `stream_link` and a signed
`videoURL` for the same server; tapmad has a flat `stream_url`; primevideo and
willow-event use a `{"Amazon Server": url}` dictionary; trysports has
`streams[]` with a per-record `headers` block; sm-sportsdata nests
`eventInfo` beside `streams[]`; fancode is flat with `stream_link`; footy-live
spells its keys `"match name"` and `"Tour/Group name"`.

So there is no common-field parser here. Each layout has its own adapter, and
these tests hold each one to the shape its feed actually served on 2026-08-20 -
442 records, 0 skipped, 819 servers.

Three rules from the request get their own classes below, because they are the
ones a "reasonable" parser gets wrong:

  - Every server is collected, not just the first. A record with five links to
    the same channel yields five candidates, and channel_groups turns them into
    one button with four backups.
  - An upcoming fixture keeps its match data even with no stream at all, and
    also when it ships links that are not serving yet.
  - LIVE/Today/Upcoming is decided after parsing, from the status the record
    itself carries - not from which config file the source is listed in. All
    thirteen live in today-match.json, and axsports alone mixes 4 live rows with
    83 not-started ones inside a single array.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.merger import _is_publishable_stream
from scanner.parsers.event_adapters import (
    ADAPTER_BY_SOURCE,
    adapter_name_for,
    adapter_report,
    flatten_records,
    parse_event_source,
    parse_event_source_flat,
    record_pipeline,
    reset_adapter_stats,
)

CONFIG = ROOT / "config" / "sources" / "today-match.json"


def source_info(source_id, **extra):
    info = {"id": source_id, "name": source_id, "url": f"https://x.test/{source_id}.json"}
    info.update(extra)
    return info


def parse(source_id, payload, **extra):
    return parse_event_source(json.dumps(payload), source_info(source_id, **extra))


def flat(source_id, payload, **extra):
    info = source_info(source_id, **extra)
    return parse_event_source_flat(json.dumps(payload), info)


# --------------------------------------------------------------------------
# one minimal record per real layout
# --------------------------------------------------------------------------

SONYLIV = {
    "playlist_info": {"name": "SonyLiv Matches Live Data"},
    "live_matches": [{
        "match_info": {
            "title": "Pakistan Tour of England 2026",
            "episodeTitle": "Day 2 - 1st Test - 20 Aug 2026",
            "genres": ["Cricket"],
            "isLive": True,
            "isOnAir": True,
            "contractStartDate": 1787218200000,
            "contractEndDate": 1787261400000,
        },
        "playback_info": {
            "resultCode": "OK",
            "resultObj": {
                "videoURL": "https://sony.test/hls/live/ENG/master.m3u8?hdnea=exp=1787241033",
                "isLive": True,
                "Maximum_Resolution": "FULL_HD",
                "contentProvider": "ENGLAND AND WALES CRICKET",
            },
        },
    }],
}

AXSPORTS = {
    "playlist_info": {"statistics": {"total_live": 1, "total_upcoming": 1}},
    "matches": [
        {
            "id": 43093, "status": "LIVE", "name": "No Limits FC W vs Ayeyawady W",
            "is_playing": True, "has_ended": False, "start_at": 1787218200,
            "league_name": "AFC Women's Champions League",
            "league_logo": "https://logo.test/league.png",
            "localteam_name": "No Limits FC W", "visitorteam_name": "Ayeyawady W",
            "link_live": [{
                "stream_link": "https://cdn.test/sla/1-abc/index.m3u8",
                "display_name": "FHD", "line": "other",
                "videoURL": "https://cdn.test/sla/1-abc/chunks.m3u8?token=1787308347-x",
            }],
        },
        {
            "id": 43094, "status": "NS", "name": "Alpha vs Beta",
            "is_playing": False, "has_ended": False, "start_at": 1787318200,
            "localteam_name": "Alpha", "visitorteam_name": "Beta",
            "link_live": [{
                "stream_link": "https://cdn.test/sla/2-def/index.m3u8",
                "display_name": "HD", "line": "akamai",
            }],
        },
    ],
}

TAPMAD = {
    "HeaderInfo": {"PlaylistName": "Tapmad Matches Metadata"},
    "Stats": {"LiveCount": 1, "UpcomingCount": 1},
    "Matches": [
        {
            "EntityId": 15933, "VideoName": "England vs Pakistan",
            "CategoryName": "Pakistan Tour of England 2026", "StageName": "1st Test",
            "EventStartDate": "2026-08-19 16:00:00", "Status": "Live",
            "ThumbnailStandard": "https://thumb.test/a.jpg",
            "stream_url": "https://tapmad.test/hls/live/master.m3u8",
        },
        {
            "EntityId": 15934, "VideoName": "Gamma vs Delta",
            "EventStartDate": "2026-08-25 16:00:00", "Status": "Upcoming",
            "ThumbnailStandard": "https://thumb.test/b.jpg",
        },
    ],
}

PRIMEVIDEO = {
    "HeaderInfo": {"PlaylistName": "Prime Video Free Sports"},
    "Stats": {"LiveCount": 1, "UpcomingCount": 0},
    "Matches": [{
        "match_id": "B0HFBSVT69",
        "title": "2026 Predator EuroTour Longoni Open",
        "status": "LIVE", "time": "LIVE NOW",
        "cover_image": "https://img.test/cover.jpg",
        "stream_url": {
            "Fastly Server": "https://fastly.test/out/v1/a/cenc.mpd",
            "Amazon Server": "https://amazon.test/out/v1/a/cenc.mpd",
            "Cloudfront Server 1": "https://cf1.test/out/v1/a/cenc.mpd",
        },
        "drm_key": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4:f6e5d4c3b2a1f6e5d4c3b2a1f6e5d4c3",
    }],
}

TRYSPORTS = {
    "category_name": "Cricket Live", "total_items": 2,
    "matches": [{
        "id": "ppv-england-vs-pakistan-1-st-test",
        "title": "England vs. Pakistan - 1st Test",
        "category": "cricket", "status": "LIVE_NOW",
        "start_time_bd": "20 Aug 2026, 10:00 AM (BD Time)",
        "poster": "https://poster.test/a.webp",
        "headers": {
            "User-Agent": "Mozilla/5.0", "Referer": "https://embed.st/",
            "Origin": "https://embed.st",
        },
        "streams": [
            {"channel_name": "Sky Sport 1 NZ (HD)", "hd": True,
             "channel_poster": "https://poster.test/sky.webp",
             "direct_stream_url": "https://lb14.strmd.st/secure/tok/rtmp/stream/x/1/playlist.m3u8"},
            {"channel_name": "Willow", "hd": True,
             "direct_stream_url": "https://lb7.strmd.st/secure/tok/delta/stream/y/1/playlist.m3u8"},
        ],
    }],
}

SM_SPORTSDATA = {
    "name": "Live and Upcoming Sports Data", "total_matches": 3, "live_match": 1,
    "matches": [
        {
            "status": "LIVE", "Category": "Cricket", "event_name": "England Vs Pakistan",
            "eventInfo": {
                "teamA": "England", "teamB": "Pakistan",
                "teamAFlag": "https://flag.test/eng.jpg",
                "teamBFlag": "https://flag.test/pak.jpg",
                "eventName": "Pakistan Tour Of England 2026",
                "startTime": "2026-08-19 16:00:00",
            },
            "streams": [{"stream_url": "https://sm.test/hls/live/master.m3u8"}],
        },
        {
            "status": "UPCOMING", "Category": "Football", "event_name": "Alpha Vs Beta",
            "eventInfo": {"teamA": "Alpha", "teamB": "Beta",
                          "startTime": "2026-08-28 16:00:00"},
            "streams": [],
        },
        {
            "status": "FINISHED", "Category": "Cricket", "event_name": "Gamma Vs Delta",
            "eventInfo": {"teamA": "Gamma", "teamB": "Delta",
                          "startTime": "2026-08-19 06:00:00"},
            "streams": [{"stream_url": "https://sm.test/hls/done/master.m3u8"}],
        },
    ],
}

FANCODE = {
    "name": "Fancode Auto Update Playlist ", "total_matches": 2, "live_match": 1,
    "matches": [
        {
            "status": "LIVE", "event_category": "Cricket",
            "title": "Gaur Gorakhpur Lions vs Noida Kings (Uttar Pradesh T20 League, 2026)",
            "src": "https://fancode.test/poster.png",
            "team_1": "Gaur Gorakhpur Lions", "team_2": "Noida Kings",
            "event_name": "Uttar Pradesh T20 League, 2026",
            "match_name": "Gaur Gorakhpur Lions vs Noida Kings",
            "match_id": 4248331, "startTime": "03:00:00 PM 20-08-2026",
            "stream_link": "https://fancode.test/4248331/index.m3u8",
        },
        {
            "status": "UPCOMING", "event_category": "Tennis",
            "title": "Alpha vs Beta", "team_1": "Alpha", "team_2": "Beta",
            "match_name": "Alpha vs Beta", "match_id": 4248332,
            "startTime": "09:00:00 PM 28-08-2026",
        },
    ],
}

FOOTY_LIVE = {
    "playlist_name": "Live Sports Events", "total_links": 0,
    "matches": [{
        "Category": "Football",
        "Tour/Group name": "FIH Hockey World Cup",
        "match name": "Hockey World Cup 2026",
        "Team 1 Name": "Hockey World Cup 2026",
        "Team 1 Logo": "https://logo.test/fih.webp",
        "Team 2 Name": "FIH Hockey World Cup",
        "Team 2 Logo": "https://logo.test/fih.webp",
        "Start time": "2026-08-18T08:00:00.000Z",
        "End time": "2026-08-30T18:00:00.000Z",
        "Status": "LIVE",
        "referer": "https://bhalocast.pro/",
        "User agent": "Mozilla/5.0",
        "Channels": [],
    }],
}

WILLOW_EVENT = {
    "HeaderInfo": {"PlaylistName": "Willow Cricket Event Info"},
    "Stats": {"LiveCount": 1, "UpcomingCount": 1},
    "Matches": [
        {
            "match_id": "B0HF1RTTDR",
            "title": "Pakistan tour of England 2026 - 1st Test - England vs Pakistan",
            "status": "LIVE", "time": "3:15 PM BDT",
            "cover_image": "https://img.test/willow.jpeg",
            "stream_url_alpha": {
                "Fistly Server": "https://fistly.test/out/v1/a/cenc.mpd",
                "Amazon Server": "https://amazon.test/out/v1/a/cenc.mpd",
                "Akamai Server": "https://akamai.test/out/v1/a/cenc.mpd",
            },
            "drm_key": "aaaabbbbccccddddeeeeffff00001111:1111000fffeeeeddddccccbbbbaaaa22",
        },
        {
            "match_id": "B0HF1RTTDS", "title": "Alpha vs Beta",
            "status": "UPCOMING", "time": "Tomorrow 9:00 PM BDT",
            "cover_image": "https://img.test/next.jpeg",
        },
    ],
}

PAYLOAD_BY_SOURCE = {
    "srhady-sonyliv-live": SONYLIV,
    "srhady-axsports-live": AXSPORTS,
    "srhady-tapmad-bd": TAPMAD,
    "srhady-primevideo-sports": PRIMEVIDEO,
    "0matbank-trysports-cricket-live": TRYSPORTS,
    "0matbank-trysports-football-live": TRYSPORTS,
    "srhady-bingstream": AXSPORTS,
    "sm-sports-data": SM_SPORTSDATA,
    "sm-fancode": FANCODE,
    "srhady-crichd-footy-live": FOOTY_LIVE,
    "srhady-willow-event": WILLOW_EVENT,
}


class DispatchTests(unittest.TestCase):
    def test_every_configured_source_has_an_adapter(self):
        configured = {
            s["id"] for s in json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]
        }
        self.assertEqual(configured, set(ADAPTER_BY_SOURCE))
        self.assertEqual(len(configured), 13)

    def test_an_unregistered_source_is_left_to_the_normal_parsers(self):
        self.assertEqual(adapter_name_for(source_info("some-m3u-source")), "")
        self.assertIsNone(parse_event_source_flat("#EXTM3U\n", source_info("x")))

    def test_an_explicit_adapter_name_in_config_is_honoured(self):
        info = source_info("brand-new-mirror", adapter="adapt_fancode")
        self.assertEqual(adapter_name_for(info), "adapt_fancode")

    def test_non_json_content_is_declined_rather_than_crashed(self):
        info = source_info("srhady-bingstream")
        self.assertIsNone(parse_event_source("#EXTM3U\n#EXTINF:-1,X\nhttp://a/b", info))
        self.assertIsNone(parse_event_source("", info))


class EachRealLayoutParsesTests(unittest.TestCase):
    """One record of each feed's real shape, name and stream both recovered."""

    def test_every_layout_yields_a_named_record(self):
        for source_id, payload in PAYLOAD_BY_SOURCE.items():
            with self.subTest(source=source_id):
                records = parse(source_id, payload)
                self.assertTrue(records, "the adapter returned nothing")
                for record in records:
                    self.assertTrue(record["name"].strip(), "a record lost its name")

    def test_sonyliv_reads_the_stream_out_of_playback_info_resultobj(self):
        """Two levels below the record, and nowhere else."""
        record = parse("srhady-sonyliv-live", SONYLIV)[0]
        self.assertEqual(len(record["channels"]), 1)
        self.assertIn("master.m3u8", record["channels"][0]["servers"][0]["url"])
        self.assertEqual(record["sport"], "cricket")
        self.assertEqual(record["status_raw"], "LIVE")
        self.assertTrue(record["start_time"], "contractStartDate must become a clock")
        self.assertTrue(record["end_time"], "contractEndDate must become a clock")

    def test_axsports_keeps_the_tokenless_link_and_the_signed_one(self):
        record = parse("srhady-axsports-live", AXSPORTS)[0]
        urls = [s["url"] for s in record["channels"][0]["servers"]]
        self.assertEqual(len(urls), 2, "stream_link and videoURL are two forms")
        self.assertIn("index.m3u8", urls[0], "the tokenless form leads")
        self.assertIn("token=", urls[1])

    def test_tapmad_reads_a_flat_stream_url(self):
        live = parse("srhady-tapmad-bd", TAPMAD)[0]
        self.assertEqual(
            live["channels"][0]["servers"][0]["url"],
            "https://tapmad.test/hls/live/master.m3u8",
        )

    def test_primevideo_reads_a_server_dictionary_and_its_clearkey(self):
        record = parse("srhady-primevideo-sports", PRIMEVIDEO)[0]
        servers = record["channels"][0]["servers"]
        self.assertEqual(len(servers), 3)
        self.assertTrue(servers[0]["drm"], "drm_key must become a ClearKey block")

    def test_trysports_attaches_the_records_headers_to_every_stream(self):
        record = parse("0matbank-trysports-cricket-live", TRYSPORTS)[0]
        names = [c["channel_name"] for c in record["channels"]]
        self.assertEqual(names, ["Sky Sport 1 NZ", "Willow"])
        for channel in record["channels"]:
            headers = channel["servers"][0]["headers"]
            self.assertEqual(headers.get("Referer"), "https://embed.st/")
            self.assertEqual(headers.get("Origin"), "https://embed.st")
            self.assertTrue(headers.get("User-Agent"))

    def test_sm_sportsdata_reads_the_nested_eventinfo(self):
        record = parse("sm-sports-data", SM_SPORTSDATA)[0]
        self.assertEqual(record["competition"], "Pakistan Tour Of England 2026")
        self.assertEqual(record["start_time"][:10], "2026-08-19")
        self.assertEqual(record["sport"], "cricket")

    def test_fancode_prefers_match_name_over_the_decorated_title(self):
        record = parse("sm-fancode", FANCODE)[0]
        self.assertEqual(record["name"], "Gaur Gorakhpur Lions vs Noida Kings")
        self.assertEqual(record["competition"], "Uttar Pradesh T20 League, 2026")

    def test_footy_live_reads_keys_that_contain_spaces_and_a_slash(self):
        record = parse("srhady-crichd-footy-live", FOOTY_LIVE)[0]
        self.assertEqual(record["name"], "Hockey World Cup 2026")
        self.assertEqual(record["competition"], "FIH Hockey World Cup")
        self.assertEqual(record["start_time"][:10], "2026-08-18")
        self.assertEqual(record["end_time"][:10], "2026-08-30")

    def test_willow_reads_the_alpha_server_dictionary(self):
        record = parse("srhady-willow-event", WILLOW_EVENT)[0]
        self.assertEqual(len(record["channels"][0]["servers"]), 3)


class EveryServerIsCollectedTests(unittest.TestCase):
    """A record with five links to one channel keeps all five."""

    def test_five_servers_become_five_candidates_under_one_channel(self):
        payload = json.loads(json.dumps(PRIMEVIDEO))
        payload["Matches"][0]["stream_url"] = {
            f"Server {i}": f"https://s{i}.test/out/v1/a/cenc.mpd" for i in range(1, 6)
        }
        record = parse("srhady-primevideo-sports", payload)[0]
        self.assertEqual(len(record["channels"]), 1)
        self.assertEqual(len(record["channels"][0]["servers"]), 5)

        candidates = flat("srhady-primevideo-sports", payload)
        playable = [c for c in candidates if c["url"]]
        self.assertEqual(len(playable), 5)
        self.assertEqual(len({c["channel_name"] for c in playable}), 1)
        self.assertEqual([c["stream_index"] for c in playable], [0, 1, 2, 3, 4])

    def test_two_different_channels_stay_two_channels(self):
        record = parse("0matbank-trysports-cricket-live", TRYSPORTS)[0]
        self.assertEqual(len(record["channels"]), 2)

    def test_a_stream_nested_two_levels_deep_is_still_found(self):
        payload = json.loads(json.dumps(TRYSPORTS))
        payload["matches"][0]["streams"] = [{
            "channel_name": "Deep",
            "sources": [{"quality": "hd", "link": {
                "url": "https://deep.test/a/index.m3u8"}}],
        }]
        record = parse("0matbank-trysports-cricket-live", payload)[0]
        urls = [s["url"] for c in record["channels"] for s in c["servers"]]
        self.assertIn("https://deep.test/a/index.m3u8", urls)


class UpcomingKeepsItsMatchDataTests(unittest.TestCase):
    def test_a_record_with_no_stream_still_produces_a_candidate(self):
        candidates = flat("srhady-crichd-footy-live", FOOTY_LIVE)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["metadata_only"])
        self.assertEqual(candidates[0]["name"], "Hockey World Cup 2026")
        self.assertTrue(candidates[0]["logo"], "the poster must survive too")

    def test_a_metadata_only_upcoming_candidate_is_publishable(self):
        candidate = flat("srhady-crichd-footy-live", FOOTY_LIVE,
                         allow_without_stream=True)[0]
        candidate["source_pipeline"] = "upcoming"
        self.assertTrue(_is_publishable_stream(candidate))

    def test_an_upcoming_record_with_links_also_gets_a_metadata_companion(self):
        """Its links are usually not serving yet; the fixture must not vanish."""
        candidates = flat("srhady-axsports-live", AXSPORTS)
        upcoming = [c for c in candidates if c["name"] == "Alpha vs Beta"]
        self.assertTrue(any(c["url"] for c in upcoming), "the links are kept")
        companions = [c for c in upcoming if c.get("metadata_companion")]
        self.assertEqual(len(companions), 1)
        self.assertEqual(companions[0]["url"], "")

    def test_a_live_record_gets_no_companion(self):
        live = [
            c for c in flat("srhady-axsports-live", AXSPORTS)
            if c["name"] == "No Limits FC W vs Ayeyawady W"
        ]
        self.assertTrue(live)
        self.assertFalse(any(c.get("metadata_companion") for c in live))


class RoutingHappensAfterParsingTests(unittest.TestCase):
    """The measured vocabulary of all 442 records, plus its neighbours."""

    def test_the_live_words_route_to_today_match(self):
        for token in ("LIVE", "Live", "LIVE_NOW", "1H", "HT", "2H"):
            with self.subTest(status=token):
                self.assertEqual(
                    record_pipeline({"status_raw": token, "channels": [{}]}),
                    "today_match",
                )

    def test_the_not_started_words_route_to_upcoming(self):
        for token in ("NS", "UPCOMING", "Upcoming", "Scheduled", "TBA"):
            with self.subTest(status=token):
                self.assertEqual(
                    record_pipeline({"status_raw": token, "channels": [{}]}),
                    "upcoming",
                )

    def test_an_ended_record_stays_a_today_match_candidate(self):
        """It never publishes, but it is the verdict that retires yesterday's card."""
        self.assertEqual(
            record_pipeline({"status_raw": "FINISHED", "source_says_ended": True,
                             "channels": [{}]}),
            "today_match",
        )

    def test_one_source_file_routes_its_rows_both_ways(self):
        candidates = flat("srhady-axsports-live", AXSPORTS)
        by_name = {}
        for candidate in candidates:
            by_name.setdefault(candidate["name"], set()).add(
                candidate["source_pipeline"]
            )
        self.assertEqual(by_name["No Limits FC W vs Ayeyawady W"], {"today_match"})
        self.assertEqual(by_name["Alpha vs Beta"], {"upcoming"})

    def test_the_configured_pipeline_is_remembered_alongside_the_routed_one(self):
        candidate = flat("srhady-axsports-live", AXSPORTS)[0]
        self.assertEqual(candidate["configured_source_pipeline"], "today_match")
        self.assertTrue(candidate["routed_by_record_status"])

    def test_an_unknown_status_falls_back_to_the_clock(self):
        self.assertEqual(
            record_pipeline({"status_raw": "??", "channels": [{}],
                             "start_time": "2099-01-01T00:00:00+00:00"}),
            "upcoming",
        )
        self.assertEqual(
            record_pipeline({"status_raw": "??", "channels": [{}],
                             "start_time": "2000-01-01T00:00:00+00:00",
                             "end_time": "2000-01-01T03:00:00+00:00"}),
            "today_match",
        )

    def test_a_record_with_no_status_no_clock_and_no_stream_goes_to_upcoming(self):
        """Today Match drops a stream-less card, so that choice would lose it."""
        self.assertEqual(record_pipeline({"status_raw": "", "channels": []}), "upcoming")


class AnEndedMatchNeverPublishesTests(unittest.TestCase):
    def test_a_verified_stream_is_refused_when_its_feed_says_finished(self):
        stream = {
            "source_pipeline": "today_match",
            "url": "https://sm.test/hls/done/master.m3u8",
            "verified": True,
            "verification_status": "verified_global",
            "source_says_ended": True,
        }
        self.assertFalse(_is_publishable_stream(stream))

    def test_the_same_stream_publishes_while_the_feed_says_nothing(self):
        stream = {
            "source_pipeline": "today_match",
            "url": "https://sm.test/hls/live/master.m3u8",
            "verified": True,
            "verification_status": "verified_global",
        }
        self.assertTrue(_is_publishable_stream(stream))

    def test_the_finished_record_is_still_parsed_and_still_a_candidate(self):
        records = parse("sm-sports-data", SM_SPORTSDATA)
        ended = [r for r in records if r["name"] == "Gamma Vs Delta"]
        self.assertEqual(len(ended), 1, "it must not be skipped")
        self.assertTrue(ended[0]["source_says_ended"])


class HeadersAndDrmSurviveTests(unittest.TestCase):
    def test_inline_pipe_headers_are_split_off_the_url(self):
        payload = json.loads(json.dumps(FANCODE))
        payload["matches"][0]["stream_link"] = (
            "https://fancode.test/a/index.m3u8?|Referer=https://ref.test/"
            "&Origin=https://ref.test&User-Agent=Mozilla/5.0"
        )
        server = parse("sm-fancode", payload)[0]["channels"][0]["servers"][0]
        self.assertEqual(server["url"], "https://fancode.test/a/index.m3u8")
        self.assertEqual(server["headers"]["Referer"], "https://ref.test/")
        self.assertEqual(server["headers"]["Origin"], "https://ref.test")
        self.assertEqual(server["headers"]["User-Agent"], "Mozilla/5.0")

    def test_a_kid_key_pair_becomes_a_clearkey_block(self):
        server = parse("srhady-willow-event", WILLOW_EVENT)[0]["channels"][0]["servers"][0]
        drm = server["drm"]
        self.assertEqual(drm.get("type"), "clearkey")
        self.assertTrue(drm.get("protected"))
        self.assertEqual(
            drm.get("keys"),
            [{"kid": "aaaabbbbccccddddeeeeffff00001111",
              "k": "1111000fffeeeeddddccccbbbbaaaa22"}],
        )

    def test_footy_live_referer_and_user_agent_reach_the_candidate(self):
        payload = json.loads(json.dumps(FOOTY_LIVE))
        payload["matches"][0]["Channels"] = [
            {"Channel Name": "Sky Sports", "Stream link": "https://sky.test/a/index.m3u8"}
        ]
        candidate = [c for c in flat("srhady-crichd-footy-live", payload) if c["url"]][0]
        self.assertEqual(candidate["headers"].get("Referer"), "https://bhalocast.pro/")
        self.assertTrue(candidate["headers"].get("User-Agent"))

    def test_the_flattened_candidate_carries_its_drm_forward(self):
        candidate = [c for c in flat("srhady-willow-event", WILLOW_EVENT) if c["url"]][0]
        self.assertEqual(candidate["drm"].get("type"), "clearkey")


class ServerOrderTests(unittest.TestCase):
    def test_the_tokenless_form_of_a_server_is_ranked_first(self):
        record = parse("srhady-axsports-live", AXSPORTS)[0]
        servers = record["channels"][0]["servers"]
        self.assertFalse(servers[0]["has_token"])
        self.assertTrue(servers[1]["has_token"])

    def test_higher_quality_leads_within_one_channel(self):
        payload = json.loads(json.dumps(AXSPORTS))
        payload["matches"][0]["link_live"] = [
            {"stream_link": "https://cdn.test/sd/index.m3u8", "display_name": "SD"},
            {"stream_link": "https://cdn.test/fhd/index.m3u8", "display_name": "FHD"},
            {"stream_link": "https://cdn.test/hd/index.m3u8", "display_name": "HD"},
        ]
        urls = [s["url"] for s in parse("srhady-axsports-live", payload)[0]["channels"][0]["servers"]]
        self.assertEqual(urls[0], "https://cdn.test/fhd/index.m3u8")
        self.assertEqual(urls[-1], "https://cdn.test/sd/index.m3u8")


class NothingIsSkippedSilentlyTests(unittest.TestCase):
    def test_a_named_stream_with_an_empty_url_is_counted_with_a_reason(self):
        reset_adapter_stats()
        payload = json.loads(json.dumps(SM_SPORTSDATA))
        payload["matches"][0]["streams"] = [
            {"stream_url": ""}, {"stream_url": "https://sm.test/ok/master.m3u8"},
        ]
        parse("sm-sports-data", payload)
        report = adapter_report()["sm-sports-data"]
        self.assertTrue(report["skip_reasons"], "an empty url must be reported")
        self.assertEqual(sum(report["skip_reasons"].values()), 1)

    def test_the_record_counts_add_up_for_every_layout(self):
        reset_adapter_stats()
        for source_id, payload in PAYLOAD_BY_SOURCE.items():
            parse(source_id, payload)
        report = adapter_report()
        self.assertEqual(len(report), len(PAYLOAD_BY_SOURCE))
        for source_id, entry in report.items():
            with self.subTest(source=source_id):
                self.assertEqual(entry["skipped"], 0)
                self.assertEqual(entry["parsed"], entry["total_records"])
                self.assertEqual(entry["unknown_fields"], {})

    def test_the_routing_columns_are_recorded_per_source(self):
        reset_adapter_stats()
        info = source_info("srhady-axsports-live")
        flatten_records(parse("srhady-axsports-live", AXSPORTS), info)
        entry = adapter_report()["srhady-axsports-live"]
        self.assertEqual(entry["routed_today"], 1)
        self.assertEqual(entry["routed_upcoming"], 1)
        self.assertEqual(entry["routed_ended"], 0)
        self.assertEqual(entry["candidates"], 4)

    def test_a_record_that_is_not_a_dict_is_reported_not_dropped_in_silence(self):
        reset_adapter_stats()
        payload = json.loads(json.dumps(FANCODE))
        payload["matches"].append("this is not a match object")
        parse("sm-fancode", payload)
        entry = adapter_report()["sm-fancode"]
        self.assertEqual(entry["total_records"], 3)
        self.assertEqual(entry["skipped"], 1)
        self.assertTrue(entry["skip_reasons"])


class AnEventWithNoTwoSidesTests(unittest.TestCase):
    """sm-sportsdata writes every name as "teamA Vs teamB", sides or not."""

    def test_an_event_named_against_itself_is_named_once(self):
        payload = json.loads(json.dumps(SM_SPORTSDATA))
        payload["matches"][1].update({
            "event_name": "Horse Racing Vs Horse Racing",
            "eventInfo": {"teamA": "Horse Racing", "teamB": "Horse Racing",
                          "startTime": "2026-08-28 16:00:00"},
        })
        names = [r["name"] for r in parse("sm-sports-data", payload)]
        self.assertIn("Horse Racing", names)
        self.assertNotIn("Horse Racing Vs Horse Racing", names)

    def test_two_genuinely_different_sides_are_left_alone(self):
        payload = json.loads(json.dumps(SM_SPORTSDATA))
        payload["matches"][1].update({
            "event_name": "Nepal Vs Nepal A",
            "eventInfo": {"teamA": "Nepal", "teamB": "Nepal A",
                          "startTime": "2026-08-28 16:00:00"},
        })
        names = [r["name"] for r in parse("sm-sports-data", payload)]
        self.assertIn("Nepal Vs Nepal A", names)

    def test_the_long_and_short_spelling_are_one_fixture(self):
        """A card published under the long name before this fix folds in."""
        from scanner.merger import normalize_event_key

        for long, short in (
            ("Cycling Vs Cycling", "Cycling"),
            ("Horse Racing Vs Horse Racing", "Horse Racing"),
            ("Golf Eventos Vs Golf Eventos", "Golf Eventos"),
        ):
            with self.subTest(name=long):
                self.assertEqual(
                    normalize_event_key(long), normalize_event_key(short)
                )

    def test_two_real_sides_stay_two_sides_in_the_merge_key(self):
        from scanner.merger import normalize_event_key

        self.assertNotEqual(
            normalize_event_key("Nepal vs Nepal A"), normalize_event_key("Nepal")
        )

    def test_the_round_prefix_is_still_trimmed(self):
        payload = json.loads(json.dumps(WILLOW_EVENT))
        record = parse("srhady-willow-event", payload)[0]
        self.assertEqual(record["name"], "England vs Pakistan")


class ConfigurationTests(unittest.TestCase):
    def test_today_match_holds_all_thirteen_and_upcoming_holds_none(self):
        today = json.loads(CONFIG.read_text(encoding="utf-8"))
        upcoming = json.loads(
            (ROOT / "config" / "sources" / "upcoming.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(today["sources"]), 13)
        self.assertEqual(upcoming["sources"], [],
                         "listing a source twice would fetch and parse it twice")

    def test_every_source_opts_into_metadata_only_cards(self):
        for source in json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]:
            with self.subTest(source=source["id"]):
                self.assertTrue(source["allow_without_stream"])
                self.assertTrue(source["preserve_source_headers"])
                self.assertTrue(source["preserve_drm"])
                self.assertIn(source["adapter"], set(ADAPTER_BY_SOURCE.values()))

    def test_all_configured_sources_are_fixture_authorities(self):
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        configured = [
            s["id"] for s in json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]
        ]
        self.assertEqual(
            sorted(settings["events"]["fixture_authority_sources"]), sorted(configured)
        )

    def test_the_private_repo_reads_its_token_from_the_environment(self):
        private = [
            s for s in json.loads(CONFIG.read_text(encoding="utf-8"))["sources"]
            if s["id"].startswith("0matbank-")
        ]
        self.assertEqual(len(private), 2)
        for source in private:
            with self.subTest(source=source["id"]):
                header = source["fetch_headers"]["Authorization"]
                self.assertIn("${PRIVATE_SPORTS_SOURCE_TOKEN}", header)
                self.assertNotIn("github_pat", header)


if __name__ == "__main__":
    unittest.main()
