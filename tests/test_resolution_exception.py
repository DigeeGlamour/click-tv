"""The one channel allowed below the 720p floor, and why it cannot spread.

Zee Bangla's published route is nominally 720p and decodes nowhere: interlaced
H.264 with zero IDR frames, MEDIA_ERR_DECODE in six 120 s sessions across three
browser profiles. The only route that plays is the 1024x576 mobile profile of
the same upstream, proven by two independent 120 s sessions with zero stall on
each of desktop Chromium, mobile Chromium and Android TV Chromium.

A floor that prefers a nominally-HD stream nobody can watch over an SD stream
that plays is not protecting quality. But lowering the floor would have been
the wrong fix: all 530 published cards sit at or above 720p, and the floor is
what keeps them there.

So the hole is named and evidence-bound, and these tests are mostly about the
ways it must NOT open.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import verifier as V  # noqa: E402

SETTINGS = json.loads(
    (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
)
PROVEN_URL = "http://live.balajibroadband.com:3500/live/625.m3u8"


def _item(name="Zee Bangla", url=PROVEN_URL, height=576):
    return {
        "name": name,
        "url": url,
        "resolution_height": height,
        "source_pipeline": "tv",
    }


class TheFloorItselfTests(unittest.TestCase):
    def test_the_global_floor_is_untouched(self):
        resolution = SETTINGS["resolution"]
        self.assertEqual(resolution["tv_minimum_height"], 720)
        self.assertEqual(resolution["movie_minimum_height"], 720)
        self.assertFalse(resolution["allow_unknown_tv_resolution"])

    def test_exactly_one_channel_is_listed(self):
        listed = [
            entry["channel"]
            for entry in SETTINGS["resolution"]["below_floor_exceptions"]
        ]
        self.assertEqual(listed, ["Zee Bangla"])

    def test_the_entry_says_why_and_cites_its_evidence(self):
        entry = SETTINGS["resolution"]["below_floor_exceptions"][0]
        self.assertIn("MEDIA_ERR_DECODE", entry["reason"])
        self.assertIn("zee-balaji-candidate.json", entry["reason"])
        self.assertTrue(entry["requires_sustained_proof"])

    def test_no_published_card_is_below_the_floor_without_the_exception(self):
        below = []
        for path in sorted((ROOT / "data" / "channels").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = (
                payload
                if isinstance(payload, list)
                else payload.get("channels") or []
            )
            for card in rows:
                if not isinstance(card, dict):
                    continue
                try:
                    height = int(card.get("resolution_height") or 0)
                except (TypeError, ValueError):
                    continue
                if 0 < height < 720 and not card.get("resolution_exception"):
                    below.append(card.get("name"))
        self.assertEqual(
            below[:5], [],
            f"{len(below)} cards are below the floor with no exception flag",
        )


class TheExceptionTests(unittest.TestCase):
    def test_the_named_channel_on_its_proven_route_is_allowed(self):
        allowed, why = V._below_floor_exception(_item(), SETTINGS)
        self.assertTrue(allowed, why)
        verdict = V._apply_resolution_policy(_item(), SETTINGS, 576)
        self.assertEqual(verdict[0], True)
        self.assertEqual(verdict[1], "verified_global")

    def test_config_alone_cannot_publish_an_unproven_route(self):
        """The condition that stops this becoming a general escape hatch."""
        item = _item(url="http://someone-else.example.net/live/625.m3u8")
        allowed, why = V._below_floor_exception(item, SETTINGS)
        self.assertFalse(allowed)
        self.assertIn("no sustained-playback proof", why)
        self.assertEqual(
            V._apply_resolution_policy(item, SETTINGS, 576)[1],
            "rejected_low_quality",
        )

    def test_the_exception_has_its_own_floor(self):
        item = _item(height=480)
        allowed, why = V._below_floor_exception(item, SETTINGS)
        self.assertFalse(allowed)
        self.assertIn("576p", why)
        self.assertEqual(
            V._apply_resolution_policy(item, SETTINGS, 480)[1],
            "rejected_low_quality",
        )

    def test_another_channel_gets_nothing_from_it(self):
        item = _item(name="Star Jalsha")
        self.assertFalse(V._below_floor_exception(item, SETTINGS)[0])
        self.assertEqual(
            V._apply_resolution_policy(item, SETTINGS, 576)[1],
            "rejected_low_quality",
        )

    def test_an_empty_exception_list_changes_nothing(self):
        settings = json.loads(json.dumps(SETTINGS))
        settings["resolution"]["below_floor_exceptions"] = []
        self.assertFalse(V._below_floor_exception(_item(), settings)[0])
        self.assertEqual(
            V._apply_resolution_policy(_item(), settings, 576)[1],
            "rejected_low_quality",
        )

    def test_a_channel_above_the_floor_is_unaffected_either_way(self):
        verdict = V._apply_resolution_policy(_item(height=1080), SETTINGS, 1080)
        self.assertEqual(verdict[0], True)

    def test_the_allowed_item_keeps_its_sd_resolution(self):
        """Nothing here dresses 576p up as HD."""
        item = _item()
        V._apply_resolution_policy(item, SETTINGS, 576)
        self.assertEqual(item["resolution_height"], 576)
        self.assertTrue(item.get("resolution_exception"))
        self.assertIn("576p", item["quality_policy_note"])


class StarJalshaIsNotClaimedFixedTests(unittest.TestCase):
    """Both of its routes were measured and both failed. Recorded, not hidden."""

    def test_both_measurements_exist(self):
        for name in ("starjalsha-primary.json", "starjalsha-fallback.json"):
            path = ROOT / "reports" / name
            if not path.exists():
                self.skipTest(f"{name} not recorded")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload.get("proven_routes"), 0,
                f"{name} now reports a proven route - re-read the report's "
                "claim that Star Jalsha is unresolved",
            )

    def test_star_jalsha_is_not_listed_as_a_resolution_exception(self):
        listed = {
            str(entry.get("channel") or "").casefold()
            for entry in SETTINGS["resolution"]["below_floor_exceptions"]
        }
        self.assertNotIn("star jalsha", listed)


if __name__ == "__main__":
    unittest.main()
