"""PROMPT 35 - name the failure, from evidence the verifier already wrote.

Every code here is derived from something the scanner already records:
`verification_error_kind` (fast_pipeline writes `path_404_quarantine` and
`host_circuit_open`), `verification_status`, `verification_mode`,
`http_status`, and the same message words `bd_verifier._error_kind` and
`fast_pipeline._error_kind` have always read. Nothing is invented - a failure
this scanner cannot describe is reported as `verification_failed` rather than
given a name that sounds like a diagnosis.

A code is a code, never a message. The real errors carry host addresses ("the
playback proxy cannot fetch a bare IP host (193.47.62.41)") and a route URL can
carry a play token, so no raw text reaches the report.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.fixture_stream_health import (  # noqa: E402
    UNCLASSIFIED_FAILURE,
    build_fixture_stream_health,
    failure_code,
)


def _route(name, **extra):
    route = {"name": name, "source_id": "srhady-bingstream",
             "url": "https://a.example/x.m3u8",
             "verification_status": "failed",
             "_verification_group": "today_match:%s" % name.lower().replace(" ", "-")}
    route.update(extra)
    return route


class TheCodeComesFromTheEvidence(unittest.TestCase):
    def test_an_explicit_kind_is_used_as_written(self):
        """fast_pipeline writes these two itself."""
        self.assertEqual("host_circuit_open", failure_code(
            {"verification_error_kind": "host_circuit_open"}))
        self.assertEqual("path_404_quarantine", failure_code(
            {"verification_error_kind": "path_404_quarantine"}))

    def test_a_status_that_already_names_its_failure_is_honoured(self):
        self.assertEqual("browser_unreachable", failure_code(
            {"verification_status": "unreachable_from_browser"}))
        self.assertEqual("http_404", failure_code(
            {"verification_status": "404_quarantined"}))

    def test_the_http_status_the_host_answered_with(self):
        for status, code in ((403, "http_403"), (404, "http_404"),
                             (407, "http_407"), (500, "http_500"),
                             (521, "http_521"), (567, "http_567")):
            self.assertEqual(code, failure_code(
                {"http_status": status,
                 "verification_error": "HTTP %d: something" % status}))

    def test_a_success_status_is_never_read_as_a_failure_code(self):
        self.assertEqual(UNCLASSIFIED_FAILURE, failure_code({"http_status": 200}))

    def test_the_message_words_the_existing_classifiers_read(self):
        for message, code in (
            ("connection timed out", "timeout"),
            ("connection refused", "connection"),
            ("name or service not known", "dns"),
            ("certificate has expired", "ssl"),
            ("network is unreachable", "network"),
            ("Stream URL is empty", "empty_stream_url"),
            ("manifest was html", "invalid_content"),
        ):
            self.assertEqual(code, failure_code({"verification_error": message}),
                             message)

    def test_a_timeout_is_a_timeout_even_when_the_word_connection_is_present(self):
        """bd_verifier._error_kind has always read it that way."""
        self.assertEqual("timeout", failure_code(
            {"verification_error": "connection timed out after 8s"}))

    def test_the_bare_ip_refusal_is_named_without_the_address(self):
        code = failure_code({
            "verification_mode": "undeliverable_bare_ip",
            "verification_error": (
                "the playback proxy cannot fetch a bare IP host "
                "(193.47.62.41): Cloudflare"),
        })
        self.assertEqual("undeliverable_bare_ip", code)
        self.assertNotIn("193.47", code)

    def test_the_strict_player_gate_is_named(self):
        self.assertEqual("player_gate_failed", failure_code(
            {"verification_mode": "same_run_player_gate",
             "verification_status": "failed"}))

    def test_an_undescribable_failure_is_not_given_a_diagnosis(self):
        self.assertEqual(UNCLASSIFIED_FAILURE,
                         failure_code({"verification_status": "failed"}))


class TheCountsStaySeparate(unittest.TestCase):
    def test_forty_routes_with_one_problem_are_one_code_and_forty_failures(self):
        routes = [_route("Ajax vs PSV Eindhoven", http_status=403,
                         verification_error="HTTP 403: Forbidden")
                  for _ in range(40)]
        report = build_fixture_stream_health(
            routes, [], [],
            fixtures=[{"name": "Ajax vs PSV Eindhoven",
                       "_verification_group": "today_match:ajax-vs-psv-eindhoven"}])
        row = report["fixtures"][0]
        self.assertEqual(["http_403"], row["failure_codes"])
        self.assertEqual(40, row["failed_stream_count"])
        self.assertEqual(1, report["totals"]["distinct_failure_codes"])
        self.assertEqual({"http_403": 40}, report["failure_codes"])
        self.assertEqual({"http_403": 1}, report["failure_code_fixtures"])

    def test_different_failures_on_one_fixture_are_listed_once_each(self):
        routes = [
            _route("Genoa Vs Como", http_status=403),
            _route("Genoa Vs Como", http_status=403),
            _route("Genoa Vs Como", http_status=404),
            _route("Genoa Vs Como", verification_status="unreachable_from_browser"),
        ]
        report = build_fixture_stream_health(
            routes, [], [],
            fixtures=[{"name": "Genoa Vs Como",
                       "_verification_group": "today_match:genoa-vs-como"}])
        row = report["fixtures"][0]
        self.assertEqual({"http_403", "http_404", "browser_unreachable"},
                         set(row["failure_codes"]))
        self.assertEqual(4, row["failed_stream_count"])
        self.assertEqual({"http_403": 2, "http_404": 1, "browser_unreachable": 1},
                         report["failure_codes"])

    def test_a_verified_route_contributes_no_failure_code(self):
        routes = [
            _route("Genoa Vs Como", verification_status="verified_global",
                   verified=True, http_status=200),
            _route("Genoa Vs Como", http_status=403),
        ]
        report = build_fixture_stream_health(
            routes, [], [],
            fixtures=[{"name": "Genoa Vs Como",
                       "_verification_group": "today_match:genoa-vs-como"}])
        row = report["fixtures"][0]
        self.assertEqual(1, row["verified_stream_count"])
        self.assertEqual(1, row["failed_stream_count"])
        self.assertEqual(["http_403"], row["failure_codes"])

    def test_route_failures_are_never_read_as_lost_fixtures(self):
        """Four fixtures, twelve dead routes, three of them still playable."""
        routes = []
        for index in range(4):
            name = "Fixture %d" % index
            routes += [_route(name, http_status=403) for _ in range(3)]
        cards = [{"id": "fixture-%d" % index, "name": "Fixture %d" % index,
                  "source_id": "x", "url": "https://a.example/%d.m3u8" % index}
                 for index in range(3)]
        recognised = [{"name": "Fixture %d" % index,
                       "_verification_group": "today_match:fixture-%d" % index}
                      for index in range(4)]
        report = build_fixture_stream_health(routes, cards, [],
                                             fixtures=recognised)
        totals = report["totals"]
        self.assertEqual(12, totals["failed_routes"])
        self.assertEqual(4, totals["fixtures_touched_by_a_route_failure"])
        self.assertEqual(1, totals["fixtures_left_with_nothing"])

    def test_the_classifier_can_be_switched_off_without_losing_the_counts(self):
        routes = [_route("Genoa Vs Como", http_status=403)]
        report = build_fixture_stream_health(
            routes, [], [], classify=False,
            fixtures=[{"name": "Genoa Vs Como",
                       "_verification_group": "today_match:genoa-vs-como"}])
        row = report["fixtures"][0]
        self.assertEqual(1, row["failed_stream_count"])
        self.assertEqual([], row["failure_codes"])


class NothingSensitiveIsWritten(unittest.TestCase):
    def test_no_url_token_host_or_raw_message_reaches_the_report(self):
        routes = [_route(
            "Genoa Vs Como",
            url="https://cdn98.example/live.m3u8?play_token=SECRET",
            headers={"Cookie": "session=SECRET"},
            http_status=0,
            verification_mode="undeliverable_bare_ip",
            verification_error=(
                "the playback proxy cannot fetch a bare IP host "
                "(193.47.62.41): Cloudflare rejected it"),
        )]
        report = build_fixture_stream_health(
            routes, [], [],
            fixtures=[{"name": "Genoa Vs Como",
                       "_verification_group": "today_match:genoa-vs-como"}])
        text = str(report)
        for secret in ("SECRET", "play_token", "193.47.62.41", "Cloudflare",
                       "m3u8", "Cookie"):
            self.assertNotIn(secret, text, secret)
        self.assertEqual(["undeliverable_bare_ip"],
                         report["fixtures"][0]["failure_codes"])

    def test_every_code_is_a_short_stable_token(self):
        codes = set()
        for item in ({"http_status": 403}, {"verification_status": "quarantine"},
                     {"verification_error": "timed out"},
                     {"verification_error_kind": "host_circuit_open"},
                     {"verification_status": "failed"}):
            codes.add(failure_code(item))
        for code in codes:
            self.assertLessEqual(len(code), 40, code)
            self.assertRegex(code, r"^[a-z0-9_]+$")


if __name__ == "__main__":
    unittest.main()
