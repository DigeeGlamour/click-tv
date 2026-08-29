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

from scanner import route_preference  # noqa: E402
from scanner import verifier as V  # noqa: E402

SETTINGS = json.loads(
    (ROOT / "config" / "settings.json").read_text(encoding="utf-8")
)

#: The route the registry currently prefers for Zee Bangla, read rather than
#: written down.
#:
#: It used to be a literal - the 1024x576 balajibroadband route this exception
#: was created for. On 2026-08-29 that route answered HTTP 500 to two
#: consecutive probes from Bangladesh and produced zero seconds of media in two
#: 120 s browser sessions, while stream.ottplus.bd - a 1280x720 HLS master from
#: the same configured source - passed both sessions at 119.99 s and 119.87 s
#: with zero stall. The preference moved, and a test that pins a URL rather
#: than the mechanism then fails for the one reason that should never fail a
#: test: the thing it guards got better.
#:
#: What these tests guard is the mechanism, so they follow the registry. The
#: named channel, the proof requirement and every "must NOT open" case below
#: are unchanged.
PROVEN_URL = (
    route_preference.load()
    .get("preferred", {})
    .get("channel|zee bangla", {})
    .get("route_id")
    or "http://live.balajibroadband.com:3500/live/625.m3u8"
)


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

    def test_the_preferred_route_is_the_one_the_registry_holds(self):
        """The exception is bound to a route, not to a channel name."""
        registry = route_preference.load()["preferred"]["channel|zee bangla"]
        self.assertEqual(registry["route_id"], PROVEN_URL)
        self.assertGreaterEqual(int(registry["pass_count"]), 2)

    def test_the_route_it_replaced_is_retained_not_deleted(self):
        """A superseded proof was still a real measurement, and a route can
        come back. `record` used to overwrite the entry, which silently deleted
        two earlier Zee Bangla proofs the first time it was called."""
        registry = route_preference.load()["preferred"]["channel|zee bangla"]
        superseded = [
            row.get("route_id") for row in (registry.get("superseded") or ())
        ]
        self.assertIn(
            "live.balajibroadband.com:3500/live/625.m3u8", superseded,
            "the route the exception was created for was dropped from history",
        )


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


class DeclaredResolutionTests(unittest.TestCase):
    """A playlist's declared resolution is read, and the reading was checked.

    A raw transport stream has no manifest, so the scanner has nothing to
    measure a resolution from - and the TV floor rejects an unknown resolution
    outright. The Zee Bangla fallback was therefore dropped for saying nothing
    about itself rather than for being too small, which cost the card its
    backup.

    Reading `tvg-resolution` fixes that, but only if the declaration is true.
    It was verified against the stream itself rather than trusted.
    """

    def test_the_declaration_is_read_only_as_a_last_resort(self):
        source = (ROOT / "scanner" / "verifier.py").read_text(encoding="utf-8")
        self.assertIn("resolution_hint", source)
        # The declared hint must come after the measured fields, never before.
        # Compared on the whole file rather than a slice: the first mention of
        # the measured field has to precede the first mention of the hint.
        height_at = source.index('item.get("resolution_height")')
        hint_at = source.index('item.get("resolution_hint")')
        self.assertLess(
            height_at, hint_at,
            "the declared hint is being read before the measured resolution",
        )

    def test_the_parser_only_reads_the_attribute_and_never_guesses(self):
        source = (ROOT / "scanner" / "parsers" / "m3u_parser.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('_extract_attribute(line, "tvg-resolution")', source)
        self.assertIn('"resolution_hint"', source)
        # Nothing may infer a resolution from the URL or the channel name.
        self.assertNotIn("resolution_hint = _guess", source)

    def test_the_declared_value_was_verified_against_the_stream(self):
        probe = ROOT / "reports" / "declared-resolution-verification.json"
        if not probe.exists():
            self.skipTest("no declared-resolution verification recorded")
        payload = json.loads(probe.read_text(encoding="utf-8"))
        sps = payload["sps"]
        self.assertEqual(sps["height"], 1080)
        self.assertEqual(
            sps["frame_mbs_only_flag"], 0,
            "this route is interlaced; that is why it decodes nowhere",
        )
        self.assertEqual(sps["scan_type"], "interlaced")
        self.assertNotIn("http://", json.dumps(payload))

    @staticmethod
    def _verification_failure_for(fragment: str) -> str:
        """The reason on file for a route, or "" if nothing was recorded.

        Two places, because there are two kinds of evidence and the second is
        the stronger one:

          reports/source-errors-channels.json   what the last scan's network
                                                check saw - an HTTP status.
          state/measured-playback-failures.json what a real browser measured
                                                over two 120 s sessions.

        The ledger was added on 2026-08-29 for Zee Bangla's rgkkw route, which
        answers HTTP 200 and produces zero seconds of media. A guard that read
        only the scan report would have called that "dropped with no reason on
        file" while a decoded-frame measurement of it sat in the repository.
        """
        ledger = ROOT / "state" / "measured-playback-failures.json"
        if ledger.exists():
            routes = json.loads(ledger.read_text(encoding="utf-8")).get("routes") or {}
            for route_id, row in routes.items():
                if not isinstance(row, dict) or row.get("superseded_by"):
                    continue
                if fragment in str(route_id) or fragment in str(
                    row.get("url_public_template") or ""
                ):
                    return str(row.get("reason") or "measured unplayable")

        report = ROOT / "reports" / "source-errors-channels.json"
        if not report.exists():
            return ""
        payload = json.loads(report.read_text(encoding="utf-8"))
        records = (
            payload.get("records")
            or payload.get("errors")
            or payload.get("items")
            or (payload if isinstance(payload, list) else [])
        )
        for record in records:
            if not isinstance(record, dict):
                continue
            if fragment in str(record.get("url") or ""):
                return str(
                    record.get("verification_error")
                    or record.get("verification_status")
                    or "recorded as failed"
                )
        return ""

    def test_the_fallback_is_never_the_primary(self):
        payload = json.loads(
            (ROOT / "data" / "channels" / "indian.json").read_text(encoding="utf-8")
        )
        rows = payload if isinstance(payload, list) else payload.get("channels") or []
        card = next((c for c in rows if c.get("name") == "Zee Bangla"), None)
        if card is None:
            self.skipTest("Zee Bangla is not published in this snapshot")
        # Unconditional: the route that decodes nowhere must never lead.
        self.assertNotIn("rgkkw", str(card.get("url") or ""))

    def test_the_fallback_is_kept_as_a_backup_or_says_why_it_is_not(self):
        """A working second route may not vanish silently.

        It can still be absent for one honest reason: the scan that wrote this
        snapshot could not verify it. Measured on 2026-08-29, that is exactly
        what separates a local scan from the CI runner - the same URL answers
        302 -> 200 in half a second from one and times out from the other, and
        the scan records `"verification_error": "Request timed out"` for it.
        So the route must be in the backups, or the reason must be on file.
        """
        payload = json.loads(
            (ROOT / "data" / "channels" / "indian.json").read_text(encoding="utf-8")
        )
        rows = payload if isinstance(payload, list) else payload.get("channels") or []
        card = next((c for c in rows if c.get("name") == "Zee Bangla"), None)
        if card is None:
            self.skipTest("Zee Bangla is not published in this snapshot")
        backups = " ".join(str(b.get("url") or "") for b in (card.get("backups") or []))
        if "rgkkw" in backups:
            return
        reason = self._verification_failure_for("rgkkw.live")
        self.assertTrue(
            reason,
            "the previous route is neither a backup nor recorded as failing - "
            "a working route was dropped with no reason on file",
        )

    def test_a_verified_second_route_is_kept_as_a_backup(self):
        """The guarantee itself, independent of any vantage.

        The data test above can only speak for the environment that ran the
        scan. This one holds the merger to the rule directly: two verified
        routes for one channel are one card with one backup, and the lower
        priority is the backup, never a discarded route.
        """
        from scanner.merger import merge_candidates

        def entry(url, priority, height):
            return {
                "name": "Zee Bangla",
                "channel_name": "Zee Bangla",
                "url": url,
                "source_id": f"src-{priority}",
                "source_pipeline": "tv",
                "category": "Indian",
                "resolution_height": height,
                "verification_status": "verified_global",
                "verified": True,
                "publish_allowed": True,
                "stream_type": "hls",
                "headers": {},
                "source_priority": priority,
            }

        cards = merge_candidates(
            [
                entry("https://lead.example/zee.m3u8", 150, 1080),
                entry("https://second.example/zee.ts", 90, 1080),
            ],
            settings_path=str(ROOT / "config" / "settings.json"),
        )
        card = next(c for c in cards if c.get("name") == "Zee Bangla")
        self.assertIn("lead.example", str(card.get("url")))
        self.assertIn(
            "second.example",
            " ".join(str(b.get("url") or "") for b in (card.get("backups") or [])),
        )
