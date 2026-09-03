"""A route identity must not carry a live credential into a committed file.

Run #1215 failed on:

    test_the_cache_holds_no_credential
    AssertionError: 2 != 0 : 2 query values are not redacted; first: token=...

That was the guard working, on real data, and the credential was live. The
route was `cdn98.com/play/live.php?...&play_token=<10 chars>&stream=225805`,
and it reached `state/route-evidence-cache.json` - a file every scan commits -
as a dict key, in the clear.

Why it got through: the locked removable list in config/phase0b-locks.json
matches parameter names EXACTLY. `play_token` is not `token`, so
normalize_source_identity preserved it verbatim, exactly as its docstring
promised. The leak check matches on shape instead, so it saw a `token=` the
normaliser never looked for.

Why the fix masks instead of removing: a route id is also the key every
evidence ledger is stored under, and two accounts on one host can differ only
by their token. Removing the parameter could fuse them, which is the one
failure the closed allowlist exists to prevent - its own derivation is 22
channels that differ only by `?id=NNN`. A digest keeps distinct values
distinct while the value is never written down.

Measured before the change, on the committed cache: 18,296 route ids, of which
exactly one carried a credential-shaped parameter, and removing that parameter
would have fused zero routes.
"""
import json
import re
import sys
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import route_evidence as rev  # noqa: E402

CACHE = ROOT / "state" / "route-evidence-cache.json"
LOCKS = ROOT / "config" / "phase0b-locks.json"

#: The shape the published-data check calls a leak, copied from
#: tests/test_enforcement_behaviour.py so a drift between the two is a failure
#: here rather than a surprise in production.
LEAK_SHAPE = re.compile(
    r"(?:token|auth|hdnea|key|sig|signature|password|pwd)=([^&\"\s]+)",
    re.IGNORECASE,
)


class TheCredentialIsMaskedNotPublished(unittest.TestCase):
    def test_the_route_that_leaked_no_longer_does(self):
        identity = rev.normalize_source_identity(
            "https://cdn98.com/play/live.php?extension=ts"
            "&mac=00%3A1A%3A79%3A99%3A54%3A11"
            # A stand-in, not the value that leaked. Writing the real one
            # here would put it back into a source file, which - unlike a
            # regenerated state file - never gets rewritten.
            "&play_token=STANDINVAL&stream=225805"
        )
        self.assertNotIn("STANDINVAL", identity)
        self.assertIn("play_token={cred:", identity)
        leaked = [
            m.group(0) for m in LEAK_SHAPE.finditer(identity)
            if not m.group(1).startswith("{")
        ]
        self.assertEqual(leaked, [])

    def test_the_parameter_survives_so_nothing_fuses(self):
        """Removal was the rejected option: it could fold two accounts on one
        host into one identity, and the locked allowlist exists to stop that."""
        one = rev.normalize_source_identity(
            "https://c.example/p?play_token=AAAAAAAAAA&stream=1")
        two = rev.normalize_source_identity(
            "https://c.example/p?play_token=BBBBBBBBBB&stream=1")
        self.assertNotEqual(one, two)
        self.assertIn("play_token=", one)
        self.assertIn("stream=1", one)

    def test_it_is_stable_across_calls_and_idempotent(self):
        """An identity is a ledger key. One that changed between two scans
        would silently start every route's evidence again."""
        url = "https://c.example/p?auth=SECRETVALUE1&id=9"
        first = rev.normalize_source_identity(url)
        self.assertEqual(first, rev.normalize_source_identity(url))
        # Fed its own output back, an identity must come out unchanged - the
        # cache repair does exactly that.
        self.assertEqual(
            first, rev.normalize_source_identity("https://" + first)
        )

    def test_every_credential_name_shape_is_covered(self):
        for name in ("play_token", "access_token", "accessToken", "api_key",
                     "auth", "authorization", "x_auth_token", "jwt",
                     "signature", "session_id", "hdnea"):
            url = f"https://c.example/p?{name}=SECRETVALUE1&id=4"
            identity = rev.normalize_source_identity(url)
            with self.subTest(parameter=name):
                self.assertNotIn("SECRETVALUE1", identity)

    def test_an_identity_parameter_is_never_touched(self):
        """The lock's own derivation: 22 channels differ only by ?id=NNN."""
        for name in sorted(rev.NEVER_REMOVABLE_QUERY_PARAMS):
            url = f"https://c.example/p?{name}=225805"
            identity = rev.normalize_source_identity(url)
            with self.subTest(parameter=name):
                self.assertIn(f"{name}=225805", identity)

    def test_a_route_with_no_credential_is_byte_identical_to_before(self):
        """The old rule, reimplemented here, must still agree on every route
        that carries nothing secret - otherwise this change re-keys ledgers it
        had no business touching."""
        for url in (
            "https://h.example.com/live?id=225805",
            "https://h.example.com/x?stream=225805&mac=00%3A1A%3A79",
            "https://h.example.com/a/b/c.m3u8",
            "https://h.example.com/p?formet=ts&is_vip=1&u=abc",
            "https://h.example.com:8080/p?e=1&md5=deadbeef",
        ):
            split = urllib.parse.urlsplit(url)
            host = (split.hostname or "").lower()
            port = (
                f":{split.port}"
                if split.port and split.port not in (80, 443) else ""
            )
            kept = [
                (key, value)
                for key, value in urllib.parse.parse_qsl(
                    split.query, keep_blank_values=True)
                if key.lower() not in rev.REMOVABLE_QUERY_PARAMS
            ]
            was = f"{host}{port}{split.path}"
            query = urllib.parse.urlencode(sorted(kept))
            if query:
                was += f"?{query}"
            with self.subTest(url=url):
                self.assertEqual(rev.normalize_source_identity(url), was)

    def test_the_exact_removable_list_still_drops_its_names(self):
        identity = rev.normalize_source_identity(
            "https://h.example.com/a.m3u8?token=DROPPED&id=7")
        self.assertEqual(identity, "h.example.com/a.m3u8?id=7")


class TheLockRecordsThePolicyChange(unittest.TestCase):
    def setUp(self):
        if not LOCKS.is_file():
            self.skipTest("no locks file")
        self.doc = json.loads(LOCKS.read_text(encoding="utf-8"))

    def test_the_version_was_bumped_and_the_reason_written_down(self):
        """The file's own rule: changing a locked value is a policy change,
        so bump lock_version and say why."""
        self.assertGreaterEqual(self.doc.get("lock_version", 0), 2)
        history = self.doc.get("lock_version_history") or []
        versions = {entry.get("version") for entry in history}
        self.assertIn(2, versions)
        entry = next(e for e in history if e.get("version") == 2)
        self.assertTrue(str(entry.get("why") or "").strip())

    def test_the_mask_is_documented_next_to_the_allowlist(self):
        mask = (self.doc.get("normalization_allowlist") or {}).get(
            "credential_value_mask")
        self.assertIsInstance(mask, dict)
        for key in ("policy", "applies_to", "replacement", "derivation"):
            self.assertTrue(str(mask.get(key) or "").strip(), key)

    def test_nothing_new_was_made_removable(self):
        """Masking must not have quietly widened what gets dropped - that is
        the change that could fuse two channels."""
        removable = set(
            (self.doc.get("normalization_allowlist") or {}).get(
                "removable_query_params") or ())
        self.assertEqual(
            removable,
            {"token", "signature", "sig", "expires", "hdnts", "session",
             "sid", "nonce", "_t", "cb"},
        )


class TheCommittedCacheIsClean(unittest.TestCase):
    """The same assertion tests/test_enforcement_behaviour.py makes, kept here
    so this file fails for its own reason rather than by side effect."""

    def test_no_route_id_carries_a_credential_in_the_clear(self):
        if not CACHE.is_file():
            self.skipTest("no evidence cache committed")
        try:
            payload = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            self.skipTest(f"evidence cache unreadable right now: {error}")
        routes = payload.get("routes") or {}
        if not routes:
            self.skipTest("evidence cache is empty")

        offenders = []
        for route_id in routes:
            query = route_id.split("?", 1)[1] if "?" in route_id else ""
            for name, value in urllib.parse.parse_qsl(
                query, keep_blank_values=True
            ):
                if not value or value.startswith("{"):
                    continue
                if name.lower() in rev.NEVER_REMOVABLE_QUERY_PARAMS:
                    continue
                if rev.CREDENTIAL_QUERY_NAME_PATTERN.search(name):
                    offenders.append(name)
        self.assertEqual(
            offenders[:5], [],
            f"{len(offenders)} credential-named parameter(s) sit in a route id "
            "in the clear",
        )


if __name__ == "__main__":
    unittest.main()
