"""A route the delivery path cannot fetch must never be published as playable.

The scanner reads a stream with Python's own socket from a GitHub runner. The
audience reaches it through a Cloudflare Worker, from a browser, on an HTTPS
page. Those are different fetchers with different rules, and this project keeps
meeting bugs where the first says yes and the second says no.

Measured on 2026-08-30 against the live workers, with the site Origin, on three
unrelated addresses that had already passed the Worker's own host allowlist:

    http://181.119.215.61:8000/...   Disney Channel  -> 403 error code: 1003
    http://23.237.104.106:8080/...   Dazn 2, 4, 5    -> 403 error code: 1003
    http://66.102.126.10:8000/...    Star Gold       -> 403 error code: 1003

A name-based host through the same worker in the same run returned 200 and a
parseable manifest, so it is the IP literal and nothing else. Six published
channel routes and fifty-nine event backups were in that state, all carrying a
Verified badge.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import deliverability  # noqa: E402
from scanner import verifier  # noqa: E402


class BareIpTests(unittest.TestCase):
    def test_the_three_measured_hosts_are_refused(self):
        for url in (
            "http://181.119.215.61:8000/play/x/index.m3u8",
            "http://23.237.104.106:8080/iptv/X/1/index.m3u8",
            "http://66.102.126.10:8000/play/a00f/index.m3u8",
        ):
            self.assertFalse(deliverability.is_deliverable(url), url)

    def test_a_named_host_is_fine(self):
        for url in (
            "https://cdn.example.com/live/index.m3u8",
            "http://livestream.biznetvideo.net/x/index.m3u8",
            "https://1tv.example.org/a.m3u8",
        ):
            self.assertTrue(deliverability.is_deliverable(url), url)

    def test_a_hostname_that_merely_looks_numeric_is_fine(self):
        """`1.2.3.4.example.com` is a name, not an address, and `9tv.com` is a
        name that starts with a digit. Rejecting either would delete working
        channels."""
        for url in ("https://1.2.3.4.example.com/a.m3u8",
                    "https://9tv.com/a.m3u8",
                    "https://185tv.net/a.m3u8"):
            self.assertTrue(deliverability.is_deliverable(url), url)

    def test_ipv6_counts_too(self):
        self.assertFalse(
            deliverability.is_deliverable("http://[2001:db8::1]:8000/a.m3u8")
        )

    def test_a_zone_id_does_not_smuggle_an_address_through(self):
        self.assertTrue(deliverability.is_bare_ip_host("fe80::1%eth0"))

    def test_the_project_header_tail_is_stripped_before_parsing(self):
        """This catalogue stores headers as `url|user-agent=...`. Handing that
        whole string to a URL parser yields a host of `66.102.126.10:8000/x|user`
        and the check silently passes."""
        self.assertFalse(deliverability.is_deliverable(
            "http://66.102.126.10:8000/x.m3u8|user-agent=Mozilla/5.0"
        ))

    def test_an_empty_url_is_not_reported_as_undeliverable(self):
        """A missing URL is a different failure with a different message; this
        check must not claim it."""
        for value in ("", None, "   "):
            self.assertTrue(deliverability.is_deliverable(value))

    def test_the_reason_names_the_host_and_the_edge_error(self):
        reason = deliverability.undeliverable_reason("http://23.237.104.106/a.m3u8")
        self.assertIn("23.237.104.106", reason)
        self.assertIn("1003", reason)

    def test_garbage_does_not_raise(self):
        for value in ("://", "http://", "h ttp://x", 12345, {"url": "x"}):
            deliverability.is_deliverable(value)


class TheVerifierRefusesThemTests(unittest.TestCase):
    def test_a_bare_ip_candidate_never_becomes_verified(self):
        item = verifier.verify_single_stream(
            {"url": "http://23.237.104.106:8080/a.m3u8", "source_pipeline": "tv"},
            {}, {},
        )
        self.assertIs(False, item["verified"])
        self.assertIs(False, item["publish_allowed"])
        self.assertEqual("failed", item["verification_status"])
        self.assertEqual("Playback Unproven", item["verification_badge"])

    def test_it_is_decided_without_a_network_call(self):
        """The answer does not depend on one, and a scan that has to wait for a
        timeout to learn this would spend its whole budget on dead routes."""
        item = verifier.verify_single_stream(
            {"url": "http://10.255.255.1/a.m3u8", "source_pipeline": "tv"}, {}, {},
        )
        self.assertEqual(0, item["response_time_ms"])
        self.assertEqual(deliverability.UNDELIVERABLE_BARE_IP,
                         item["verification_mode"])

    def test_a_named_host_still_reaches_the_network_path(self):
        """Guard against the check being too broad and failing everything."""
        item = verifier.verify_single_stream(
            {"url": "https://nonexistent.invalid/a.m3u8", "source_pipeline": "tv"},
            {}, {},
        )
        self.assertNotEqual(deliverability.UNDELIVERABLE_BARE_IP,
                            item.get("verification_mode"))


class NothingPublishedIsUndeliverableTests(unittest.TestCase):
    """The catalogue itself, so a regression shows up as a failing test rather
    than as a card that spins forever."""

    def _routes(self, stream):
        for key in ("url", "stream_url", "link"):
            value = stream.get(key)
            if isinstance(value, str) and value.strip():
                yield value.strip()
                return

    def _files(self):
        yield from sorted((ROOT / "data" / "channels").glob("*.json"))
        for name in ("today-match.json", "upcoming.json"):
            path = ROOT / "data" / name
            if path.is_file():
                yield path
            yield from sorted((ROOT / "data" / "snapshots").glob(f"*/{name}"))

    def test_no_published_route_sits_on_a_bare_ip(self):
        offenders = []
        for path in self._files():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            key = next((k for k in ("channels", "events", "items")
                        if isinstance(payload.get(k), list)), None)
            if not key:
                continue
            for card in payload[key]:
                if not isinstance(card, dict):
                    continue
                streams = [card, *[b for b in (card.get("backups") or [])
                                   if isinstance(b, dict)]]
                for stream in streams:
                    for url in self._routes(stream):
                        if not deliverability.is_deliverable(url):
                            offenders.append(
                                f"{path.name}: {card.get('name')} -> "
                                f"{deliverability.host_of(url)}"
                            )
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
