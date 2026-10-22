"""A route whose file name is an opaque token is registered without it.

itcnbd names its files with a base64 upstream address. Star Jalsha's proven
720p route (two 120 s passes, 2026-10-22) was refused by the registry's own
guard because that segment looks like a credential. It is stored as a short
digest instead, and published as a placeholder.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402
from scanner import route_preference as rp  # noqa: E402

OPAQUE = ("https://s3.itcnbd.live/server-4/stream/"
          "aHR0cDovLzE3Mi4xOS4xNy4yMzA6ODA5MC9obHMvU3RhckphbHNoYUhELm0zdTg.m3u8")


class TheRegistryId(unittest.TestCase):
    def test_the_opaque_segment_becomes_a_digest(self):
        route_id = rp.registry_route_id(OPAQUE)
        self.assertNotIn("aHR0cDovLz", route_id)
        self.assertRegex(route_id, r"\{h:[0-9a-f]{12}\}\.m3u8$")
        self.assertFalse(rev.evidence_contains_forbidden_material({"route_id": route_id}))

    def test_it_is_stable_and_discriminating(self):
        other = OPAQUE.replace("U3RhckphbHNoYUhE", "WmVlQmFuZ2xhSEQ")
        self.assertEqual(rp.registry_route_id(OPAQUE), rp.registry_route_id(OPAQUE))
        self.assertNotEqual(rp.registry_route_id(OPAQUE), rp.registry_route_id(other))

    def test_an_ordinary_route_keeps_its_old_identity(self):
        url = "https://stream.ottplus.bd/live/zee_bangla_abr/index.m3u8"
        self.assertEqual(rp.registry_route_id(url), rev.normalize_source_identity(url))

    def test_the_public_template_hides_it(self):
        self.assertTrue(rev.redact_public_template(OPAQUE).endswith("/stream/{seg}.m3u8"))

    def test_the_registered_route_is_promoted(self):
        streams = [{"url": "https://cdn.smartplaytv.bond/server7/starjalsha.m3u8"},
                   {"url": OPAQUE}]
        registry = {"preferred": {"channel|star jalsha": {
            "route_id": rp.registry_route_id(OPAQUE),
            "recorded_at": "2099-01-01T00:00:00+00:00"}}}
        self.assertEqual(rp.preferred_route_id("channel", "STAR JALSHA", registry),
                         rp.registry_route_id(OPAQUE))


if __name__ == "__main__":
    unittest.main()
