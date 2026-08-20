"""No embed player is ever published, by direct request.

A card plays through the native pipeline or it does not play. Turning the
`events.attach_embed_streams` flag off was not enough on its own: once an embed
reaches data/today-match.json it survives every later scan through live
protection, which carries a previously published card forward verbatim.
Measured on production at 2026-08-20T09:02 with the flag already false, "Sri
Lanka vs India 1st Test" was still publishing two embed buttons (Server-8 and
Server-9) plus an embed_backups list.

So the attach path is gone, and `_strip_embed_streams` runs over every published
event card - freshly merged and carried alike - on the way out.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner.events import _payload, _strip_embed_streams


def native(stream_id="s1"):
    return {
        "id": stream_id,
        "role": "primary",
        "playback_type": "native",
        "playback_id": f"ctv_{stream_id}",
        "resolution": "HD",
    }


def embed(stream_id="e1", url="https://embed.st/embed/delta/x/1"):
    return {
        "id": stream_id,
        "role": "backup",
        "playback_type": "embed",
        "embed_url": url,
    }


class StripRemovesEveryEmbedShape(unittest.TestCase):
    def test_an_embed_stream_is_removed_from_a_mixed_channel(self):
        card = {
            "channels": [{
                "id": "c1", "name": "Willow", "renderer": "native",
                "playback_types": ["native", "embed"],
                "streams": [native("a"), embed("b")],
                "stream_count": 2, "backup_count": 1,
            }]
        }
        removed = _strip_embed_streams(card)
        channel = card["channels"][0]
        self.assertEqual(removed, 1)
        self.assertEqual([s["id"] for s in channel["streams"]], ["a"])
        self.assertEqual(channel["playback_types"], ["native"])
        self.assertEqual(channel["stream_count"], 1)
        self.assertEqual(channel["backup_count"], 0)

    def test_an_embed_only_channel_is_removed_entirely(self):
        card = {
            "channels": [
                {"id": "c1", "name": "Willow", "streams": [native("a")]},
                {"id": "c2", "name": "Server-8", "renderer": "embed",
                 "playback_types": ["embed"], "streams": [embed("b"), embed("c")]},
            ]
        }
        _strip_embed_streams(card)
        self.assertEqual([c["id"] for c in card["channels"]], ["c1"])

    def test_embed_backups_and_their_count_are_dropped(self):
        card = {
            "embed_backups": [{"name": "Streamed", "embed_url": "https://embed.st/x"}],
            "embed_backup_count": 1,
            "channels": [{"id": "c1", "name": "Willow", "streams": [native("a")]}],
        }
        _strip_embed_streams(card)
        self.assertNotIn("embed_backups", card)
        self.assertNotIn("embed_backup_count", card)

    def test_a_stream_carrying_only_an_embed_url_is_still_an_embed(self):
        card = {
            "channels": [{
                "id": "c1", "name": "Willow",
                "streams": [native("a"), {"id": "b", "embed_url": "https://embed.st/y"}],
            }]
        }
        _strip_embed_streams(card)
        self.assertEqual([s["id"] for s in card["channels"][0]["streams"]], ["a"])

    def test_a_default_channel_that_was_embed_only_is_repointed(self):
        card = {
            "default_channel_id": "c2",
            "channels": [
                {"id": "c1", "name": "Willow", "streams": [native("a")]},
                {"id": "c2", "name": "Server-8", "streams": [embed("b")]},
            ],
        }
        _strip_embed_streams(card)
        self.assertEqual(card["default_channel_id"], "c1")

    def test_a_native_only_card_is_untouched(self):
        card = {
            "channels": [{
                "id": "c1", "name": "Willow", "renderer": "native",
                "playback_types": ["native"], "streams": [native("a"), native("b")],
                "stream_count": 2, "backup_count": 1,
            }]
        }
        self.assertEqual(_strip_embed_streams(card), 0)
        self.assertEqual(len(card["channels"][0]["streams"]), 2)

    def test_a_channel_with_no_streams_list_is_left_alone(self):
        """Some callers build channels[] before the streams are attached."""
        card = {"channels": [{"id": "c1", "name": "Willow", "name_confidence": "explicit"}]}
        _strip_embed_streams(card)
        self.assertEqual([c["id"] for c in card["channels"]], ["c1"])

    def test_a_card_with_no_channels_is_safe(self):
        for card in ({}, {"channels": None}, {"channels": []}):
            with self.subTest(card=card):
                self.assertEqual(_strip_embed_streams(dict(card)), 0)


class PublishAlwaysStrips(unittest.TestCase):
    def test_payload_removes_embeds_from_a_carried_card(self):
        """The production shape: an embed that arrived on a previous scan."""
        item = {
            "id": "sri-lanka-vs-india-1st-test",
            "name": "Sri Lanka vs India 1st Test",
            "sport_type": "cricket",
            "carried_forward_misses": 109,
            "embed_backups": [{"name": "Streamed", "embed_url": "https://embed.st/a"}],
            "embed_backup_count": 1,
            "channels": [
                {"id": "c1", "name": "Willow", "name_confidence": "explicit",
                 "streams": [native("a")]},
                {"id": "c8", "name": "Server-8", "name_confidence": "generic",
                 "renderer": "embed", "playback_types": ["embed"],
                 "streams": [embed("h")]},
                {"id": "c9", "name": "Server-9", "name_confidence": "generic",
                 "renderer": "embed", "playback_types": ["embed"],
                 "streams": [embed("i")]},
            ],
        }
        payload = _payload([item], "today_match", 0, 0)
        published = payload["items"][0]
        self.assertNotIn("embed_backups", published)
        self.assertEqual([c["name"] for c in published["channels"]], ["Willow"])

    def test_the_server_numbering_has_no_gap_after_a_strip(self):
        item = {
            "id": "evt", "name": "Alpha vs Beta", "sport_type": "football",
            "channels": [
                {"id": "c1", "name": "Server-1", "name_confidence": "generic",
                 "streams": [native("a")]},
                {"id": "c2", "name": "Server-2", "name_confidence": "generic",
                 "streams": [embed("b")]},
                {"id": "c3", "name": "Server-3", "name_confidence": "generic",
                 "streams": [native("c")]},
            ],
        }
        payload = _payload([item], "today_match", 0, 0)
        self.assertEqual(
            [c["name"] for c in payload["items"][0]["channels"]],
            ["Server-1", "Server-2"],
        )

    def test_no_published_card_can_carry_an_embed_url(self):
        item = {
            "id": "evt", "name": "Alpha vs Beta", "sport_type": "football",
            "embed_backups": [{"embed_url": "https://embed.st/a"}],
            "channels": [{"id": "c1", "name": "Willow", "name_confidence": "explicit",
                          "streams": [native("a"), embed("b")]}],
        }
        payload = _payload([item], "today_match", 0, 0)
        blob = json.dumps(payload)
        self.assertNotIn("embed.st", blob)
        self.assertNotIn("embed_url", blob)
        self.assertNotIn('"embed"', blob)


class TheAttachPathIsGone(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")

    def test_nothing_writes_embed_backups_any_more(self):
        self.assertNotIn('card["embed_backups"] = [', self.source)

    def test_the_strip_is_wired_into_publish(self):
        self.assertIn("_strip_embed_streams(item)", self.source)

    def test_the_flag_stays_off_in_settings(self):
        settings = json.loads(
            (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
        )
        self.assertFalse(settings["events"]["attach_embed_streams"])


if __name__ == "__main__":
    unittest.main()
