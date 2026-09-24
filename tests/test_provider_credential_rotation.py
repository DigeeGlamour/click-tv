"""ধারা ৪.৭ - a replaced secret must not inherit the refused one's cooldown.

**The measured failure.** On 2026-09-24 OMDb answered 401 at 06:35. The module
printed its own policy - "Provider disabled **for this run**; replace the
secret" - and then persisted a six-hour block keyed on the string `omdb`. The
secret was replaced. The 08:04 run sent OMDb **zero** requests and finished
with the record still reading:

    status        unhealthy_auth
    requests      288  (unchanged)
    next_retry_at 2026-09-24T12:35:41Z

So the new key was never tested, and could not have been until the clock ran
out. A cooldown belongs to the credential that earned it, not to the provider's
name.

**What must not be broken while fixing it.** The cooldown exists for a real
reason: a retry storm against a suspended key is how it stays suspended. So the
two relaxations are narrow and each is pinned by its own test - a demonstrably
different secret, or a different run - and nothing else opens the block.

**And the fingerprint may not become a way to read the key.**
`state/provider-health.json` is committed to a public repository and an OMDb
key is eight hex characters; a plain SHA-256 of one is a lookup table away from
the key itself. The digest is therefore keyed, and with no key configured the
honest answer is None rather than something weak that looks strong.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import provider_health as ph  # noqa: E402
from scanner import route_evidence  # noqa: E402

#: Long enough for `route_evidence.configured_hmac_key` to accept it.
HMAC_KEY = "0123456789abcdef0123456789abcdef"


class _Isolated(unittest.TestCase):
    """Every test writes into its own store and its own environment."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "provider-health.json")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "providers": {}}, handle)
        ph.reset(self.path)
        self.addCleanup(ph.reset, None)
        self._env = {
            name: os.environ.get(name)
            for name in ("OMDB_API_KEY", route_evidence.HMAC_KEY_ENV)
        }
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, value in self._env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _configure(self, key="omdb-key-one", hmac=HMAC_KEY):
        if key is None:
            os.environ.pop("OMDB_API_KEY", None)
        else:
            os.environ["OMDB_API_KEY"] = key
        if hmac is None:
            os.environ.pop(route_evidence.HMAC_KEY_ENV, None)
        else:
            os.environ[route_evidence.HMAC_KEY_ENV] = hmac

    def _refuse(self, status=401):
        """Make one request that the provider answers with a refusal."""
        original = ph._perform_request
        ph._perform_request = lambda *a, **k: (status, {}, None)
        try:
            return ph.request_json("omdb", "https://omdb.test/?t=x",
                                   path=self.path)
        finally:
            ph._perform_request = original

    def _record(self):
        return ph._peek_record("omdb", self.path) or {}


class TheFingerprintCannotBecomeTheKey(_Isolated):
    def test_it_never_contains_the_credential(self):
        self._configure(key="1a2b3c4d")
        fingerprint = ph.credential_fingerprint("omdb")
        self.assertTrue(fingerprint)
        self.assertNotIn("1a2b3c4d", fingerprint)

    def test_it_is_keyed_so_a_short_key_is_not_brute_forceable(self):
        """The same eight-character secret fingerprints differently under two
        HMAC keys, which is what a rainbow table cannot survive."""
        self._configure(key="1a2b3c4d", hmac=HMAC_KEY)
        first = ph.credential_fingerprint("omdb")
        self._configure(key="1a2b3c4d", hmac="f" * 40)
        self.assertNotEqual(first, ph.credential_fingerprint("omdb"))

    def test_with_no_hmac_key_the_answer_is_none_not_a_weak_digest(self):
        self._configure(key="1a2b3c4d", hmac=None)
        self.assertIsNone(ph.credential_fingerprint("omdb"))

    def test_with_no_credential_the_answer_is_none(self):
        self._configure(key=None)
        self.assertIsNone(ph.credential_fingerprint("omdb"))

    def test_a_short_hmac_key_is_treated_as_absent(self):
        self._configure(key="1a2b3c4d", hmac="tooshort")
        self.assertIsNone(ph.credential_fingerprint("omdb"))

    def test_two_providers_do_not_share_a_fingerprint_for_one_secret(self):
        self._configure(key="same-secret-value")
        os.environ["TMDB_API_KEY"] = "same-secret-value"
        self.addCleanup(os.environ.pop, "TMDB_API_KEY", None)
        self.assertNotEqual(ph.credential_fingerprint("omdb"),
                            ph.credential_fingerprint("tmdb"))

    def test_every_provider_with_a_secret_is_mapped(self):
        """A provider missing from the map can never be seen to rotate."""
        for provider in ("tmdb", "omdb", "moviesdatabase", "fanart"):
            self.assertIn(provider, ph.PROVIDER_CREDENTIAL_ENV)


class ARejectedCredentialStillStopsTheProvider(_Isolated):
    """The protection this is not allowed to weaken."""

    def test_a_401_disables_the_provider(self):
        self._configure()
        self._refuse()
        record = self._record()
        self.assertEqual(record["status"], ph.STATUS_UNHEALTHY_AUTH)
        self.assertFalse(ph.is_available("omdb", path=self.path))

    def test_the_same_key_is_not_retried_inside_the_run(self):
        self._configure()
        self._refuse()
        for _ in range(5):
            self.assertFalse(ph.is_available("omdb", path=self.path))
        self.assertEqual(self._record()["requests"], 1)

    def test_a_refusal_is_never_retried_by_the_backoff_ladder(self):
        """A rejected credential is not a transient fault."""
        self._configure()
        self._refuse()
        self.assertEqual(self._record()["requests"], 1)
        self.assertEqual(self._record()["auth_failures"], 1)

    def test_the_cooldown_stamp_is_still_written(self):
        self._configure()
        self._refuse()
        self.assertTrue(self._record().get("next_retry_at"))


class AReplacedCredentialWaitsForNothing(_Isolated):
    def test_a_changed_secret_reopens_the_provider_at_once(self):
        self._configure(key="omdb-key-one")
        self._refuse()
        self.assertFalse(ph.is_available("omdb", path=self.path))
        self._configure(key="omdb-key-two")
        self.assertTrue(ph.is_available("omdb", path=self.path))
        self.assertEqual(self._record()["auth_retest_reason"],
                         "credential_rotated")

    def test_the_cooldown_stamp_is_cleared_with_it(self):
        self._configure(key="omdb-key-one")
        self._refuse()
        self._configure(key="omdb-key-two")
        ph.is_available("omdb", path=self.path)
        self.assertNotIn("next_retry_at", self._record())

    def test_an_unchanged_secret_reopens_nothing(self):
        self._configure(key="omdb-key-one")
        self._refuse()
        self._configure(key="omdb-key-one")
        self.assertFalse(ph.is_available("omdb", path=self.path))

    def test_cannot_tell_is_not_rotated(self):
        """With no HMAC key there is no fingerprint on either side. That must
        read as "unknown" and leave the run rule to decide, never as "the key
        changed" - which would retry a suspended key on every call."""
        self._configure(key="omdb-key-one", hmac=None)
        self._refuse()
        record = self._record()
        self.assertNotIn("credential_fingerprint", record)
        self._configure(key="omdb-key-two", hmac=None)
        self.assertFalse(ph.credential_changed("omdb", record))


class ANewRunTestsTheSecretOnce(_Isolated):
    """The module's own sentence - "disabled for this run" - made true."""

    def test_a_later_run_is_allowed_one_request(self):
        self._configure()
        self._refuse()
        self.assertFalse(ph.is_available("omdb", path=self.path))
        self._record()["auth_run"] = "a-run-that-has-ended"
        self.assertTrue(ph.is_available("omdb", path=self.path))
        self.assertEqual(self._record()["auth_retest_reason"],
                         "new_run_retest")

    def test_the_retest_happens_once_and_then_the_block_returns(self):
        self._configure()
        self._refuse()
        self._record()["auth_run"] = "a-run-that-has-ended"
        ph.is_available("omdb", path=self.path)   # the one retest
        self._refuse()                            # which is refused again
        self.assertFalse(ph.is_available("omdb", path=self.path))
        self.assertEqual(self._record()["requests"], 2)

    def test_the_run_that_refused_it_is_recorded(self):
        self._configure()
        self._refuse()
        self.assertEqual(self._record()["auth_run"], ph._RUN_ID)


class ASuccessfulCallClearsTheBookkeeping(_Isolated):
    def _succeed(self):
        original = ph._perform_request
        ph._perform_request = lambda *a, **k: (200, {"Title": "ok"}, None)
        try:
            return ph.request_json("omdb", "https://omdb.test/?t=x",
                                   path=self.path)
        finally:
            ph._perform_request = original

    def test_the_working_credential_is_fingerprinted(self):
        """A record that only ever learns the rejected key cannot recognise
        the replacement."""
        self._configure(key="omdb-key-one")
        self._succeed()
        self.assertEqual(self._record()["credential_fingerprint"],
                         ph.credential_fingerprint("omdb"))

    def test_a_recovery_reopens_the_provider(self):
        self._configure()
        self._refuse()
        self._record()["auth_run"] = "a-run-that-has-ended"
        ph.is_available("omdb", path=self.path)
        self._succeed()
        record = self._record()
        self.assertEqual(record["status"], ph.STATUS_HEALTHY)
        self.assertNotIn("auth_run", record)
        self.assertNotIn("auth_retest_reason", record)
        self.assertTrue(ph.is_available("omdb", path=self.path))


class OtherCooldownsAreUntouched(_Isolated):
    """Only the auth block learned about credentials. A 429 or a 5xx knows
    nothing about them and must keep waiting out its clock."""

    def test_a_rate_limit_cooldown_still_holds_across_runs(self):
        self._configure()
        record = ph._provider_record("omdb", self.path)
        record["status"] = ph.STATUS_COOLING_DOWN
        record["ok"] = False
        record["next_retry_at"] = "2099-01-01T00:00:00+00:00"
        record["auth_run"] = "a-run-that-has-ended"
        self.assertFalse(ph.is_available("omdb", path=self.path))

    def test_rotating_a_key_does_not_end_a_rate_limit_cooldown(self):
        self._configure(key="omdb-key-one")
        record = ph._provider_record("omdb", self.path)
        record["status"] = ph.STATUS_COOLING_DOWN
        record["ok"] = False
        record["next_retry_at"] = "2099-01-01T00:00:00+00:00"
        record["credential_fingerprint"] = ph.credential_fingerprint("omdb")
        self._configure(key="omdb-key-two")
        self.assertFalse(ph.is_available("omdb", path=self.path))

    def test_an_expired_clock_still_reopens_anything(self):
        self._configure()
        record = ph._provider_record("omdb", self.path)
        record["status"] = ph.STATUS_COOLING_DOWN
        record["ok"] = False
        record["next_retry_at"] = "2000-01-01T00:00:00+00:00"
        self.assertTrue(ph.is_available("omdb", path=self.path))


class TheCredentialNeverTravels(unittest.TestCase):
    def test_nothing_prints_or_stores_the_secret_itself(self):
        source = (ROOT / "scanner" / "provider_health.py").read_text(
            encoding="utf-8")
        body = source[source.index("def configured_credential("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertNotIn("print(", body)
        # The only caller inside this module is the fingerprint.
        callers = [
            line for line in source.splitlines()
            if "configured_credential(" in line and "def " not in line
        ]
        self.assertEqual(len(callers), 1)

    def test_the_record_carries_a_digest_and_never_a_key_name(self):
        source = (ROOT / "scanner" / "provider_health.py").read_text(
            encoding="utf-8")
        marked = source[source.index("if status in _AUTH_STATUSES:"):]
        marked = marked[:marked.index("return {}")]
        self.assertIn("credential_fingerprint", marked)
        self.assertNotIn("OMDB_API_KEY", marked)


if __name__ == "__main__":
    unittest.main()
