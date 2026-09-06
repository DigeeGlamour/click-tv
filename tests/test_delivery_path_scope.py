"""The five-minute scan sweeps what it publishes, and nothing else.

WHY THIS EXISTS

Measured over seven real `upcoming-targeted` runs on 2026-09-06, "Check the
delivery path a viewer actually uses" was 138.4s of a 267s job - 51.9% - and
535 of its 865 routes were Live TV channels. A targeted run does not read,
write or change a channel route. It was spending more than half its life on
them, and a five-minute cadence cancelled it at the finish line twice in eight
runs (#1298 at 305s, #1300 at 293s).

So `upcoming-targeted` passes `--scope events`. Every other mode keeps `all`.

What these tests have to prove is that this is a narrowing of WORK and not of
VERIFICATION:

  * a targeted run still checks 100% of the routes it is responsible for;
  * no route reachable from Today Match or Upcoming is dropped;
  * today, channels, movies, upcoming and all are byte-for-byte unaffected;
  * the step is still advisory - `continue-on-error` - exactly as before.

The route counts come from the repository's own published data, not fixtures,
so if the shape of a published file changes these tests notice.
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "verify-delivery-path.py"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "scan.yml"
DATA = PROJECT_ROOT / "data"


def _load_verifier():
    """Import the script under a module name - its filename has a dash."""
    spec = importlib.util.spec_from_file_location("verify_delivery_path", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()


def _urls_in(path: Path) -> set[str]:
    """Every playable URL a published file carries, primary and backup.

    Deliberately independent of the script's own walker: if both had the same
    bug, neither test would see it.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    key = next(
        (k for k in ("channels", "events", "items")
         if isinstance(payload.get(k), list)),
        None,
    )
    if not key:
        return set()
    found: set[str] = set()
    for card in payload[key]:
        if not isinstance(card, dict):
            continue
        for stream in [card, *(card.get("backups") or [])]:
            if not isinstance(stream, dict):
                continue
            url = str(stream.get("url") or "").strip()
            if url:
                found.add(url)
    return found


def _event_urls() -> set[str]:
    out: set[str] = set()
    for name in ("today-match.json", "upcoming.json"):
        out |= _urls_in(DATA / name)
    return out


def _channel_urls() -> set[str]:
    out: set[str] = set()
    for path in sorted((DATA / "channels").glob("*.json")):
        if path.name == "index.json":
            continue
        out |= _urls_in(path)
    return out


class TargetedSweepsOnlyWhatItPublishes(unittest.TestCase):
    """1, 2 and 5: what the events scope includes and excludes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.all_rows = verifier.published_routes("all")
        cls.event_rows = verifier.published_routes("events")
        cls.all_urls = {row["url"] for row in cls.all_rows}
        cls.event_urls = {row["url"] for row in cls.event_rows}

    def test_1_no_channel_only_route_is_swept_in_targeted_mode(self):
        """A route reachable ONLY from a channel card must not be swept.

        Not "no URL that also appears on a channel": one stream can be both a
        Live TV channel and the feed for a match - measured 2026-09-06, exactly
        one URL was on both a channel card and an event card. Under
        scope=events that route is swept because an EVENT card carries it, and
        excluding it would drop a route the targeted run published.
        """
        channel_only = _channel_urls() - _event_urls()
        if not channel_only:
            self.skipTest("no published channel data in this checkout")
        leaked = self.event_urls & channel_only
        self.assertEqual(
            leaked, set(),
            f"{len(leaked)} channel-only route(s) still swept under scope=events",
        )

    def test_1_every_swept_route_comes_from_an_event_card(self):
        """The positive form of the same rule, and the stronger one."""
        from_event_files = _event_urls()
        if not from_event_files:
            self.skipTest("no published event data in this checkout")
        # The script resolves some routes through the playback catalogue that a
        # plain read of the file cannot see, so it legitimately finds more than
        # the naive walker. What must never happen is the reverse.
        self.assertTrue(from_event_files <= self.event_urls)

    def test_1_the_narrowing_is_real_and_large(self):
        """Guard against a scope that silently stops narrowing anything."""
        channels = _channel_urls()
        if not channels:
            self.skipTest("no published channel data in this checkout")
        self.assertLess(len(self.event_rows), len(self.all_rows))
        # The measured split was 535 channel routes to 35 event routes. Assert
        # the shape of that, not the exact numbers, which move with the day.
        self.assertLess(
            len(self.event_rows), len(self.all_rows) * 0.5,
            "events scope should be a small fraction of the full sweep",
        )

    def test_2_every_event_route_is_still_checked(self):
        """100% of what a targeted run is responsible for."""
        published = _event_urls()
        if not published:
            self.skipTest("no published event data in this checkout")
        missing = published - self.event_urls
        self.assertEqual(
            missing, set(),
            f"{len(missing)} event route(s) dropped by scope=events",
        )

    def test_5_the_events_scope_loses_nothing_the_full_sweep_had(self):
        """Every event route in the full sweep survives the narrowing."""
        published = _event_urls()
        in_full = self.all_urls & published
        in_events = self.event_urls & published
        self.assertEqual(in_full, in_events)

    def test_an_event_route_is_requested_identically_under_both_scopes(self):
        """Headers, profile, proxy_mode and stream type all feed the request,
        and a route checked with the wrong headers is a route checked wrong.

        `name` and `where` are deliberately NOT compared. A stream that sits on
        both a channel card and an event card is labelled by whichever card the
        walker reached first, so under scope=all the shared route reads
        "real Madrid TV / backup1" and under scope=events "Real Madrid Vs
        Albacete B / primary". Those two fields are display only - the verdict
        is recorded against the URL by playback_evidence.record().
        """
        REQUEST_FIELDS = ("url", "headers", "profile", "proxy_only", "type",
                          "playback_id")
        by_url = {r["url"]: r for r in self.all_rows}
        compared = 0
        for row in self.event_rows:
            other = by_url.get(row["url"])
            if other is None:
                continue
            compared += 1
            with self.subTest(url=row["url"][:60]):
                for field in REQUEST_FIELDS:
                    self.assertEqual(
                        row.get(field), other.get(field),
                        f"{field} differs between scopes for {row['url']}",
                    )
        self.assertGreater(compared, 0, "no event route was comparable")

    def test_a_targeted_run_publishes_event_routes_so_the_scope_matches_it(self):
        """The premise: targeted writes today-match.json and upcoming.json."""
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(
            tuple(verifier.EVENT_FILES), ("today-match.json", "upcoming.json"))
        self.assertIn("git add data reports state", workflow)


class EveryOtherModeIsUnchanged(unittest.TestCase):
    """3: today, channels, movies, upcoming and all keep the full sweep."""

    def test_3_the_default_scope_is_all(self):
        import argparse
        import contextlib
        import io

        parser_default = None
        # Read the declared default rather than trusting the help text.
        source = SCRIPT.read_text(encoding="utf-8")
        match = re.search(
            r'add_argument\(\s*"--scope",.*?default="([a-z]+)"', source, re.S)
        self.assertIsNotNone(match, "--scope has no declared default")
        self.assertEqual(match.group(1), "all")

    def test_3_the_full_sweep_still_contains_channel_routes(self):
        channels = _channel_urls()
        if not channels:
            self.skipTest("no published channel data in this checkout")
        swept = {row["url"] for row in verifier.published_routes("all")}
        missing = channels - swept
        self.assertEqual(
            missing, set(),
            f"scope=all dropped {len(missing)} channel route(s)",
        )

    def test_3_the_full_sweep_is_identical_to_the_unscoped_call(self):
        self.assertEqual(
            verifier.published_routes(),
            verifier.published_routes("all"),
        )

    def test_3_only_upcoming_targeted_selects_the_narrow_scope(self):
        """The workflow's own selection, read from the workflow."""
        run = _delivery_step_run()
        for mode, expected in (
            ("upcoming-targeted", "events"),
            ("today", "all"),
            ("channels", "all"),
            ("movies", "all"),
            ("upcoming", "all"),
            ("all", "all"),
        ):
            with self.subTest(mode=mode):
                self.assertEqual(_scope_for(run, mode), expected)

    def test_an_unknown_scope_is_refused_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            verifier.published_routes("channels-only")


def _delivery_step_run() -> str:
    text = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = text.index(
        "      - name: Check the delivery path a viewer actually uses\n")
    body = text[start:]
    end = body.index("\n      - name:", 1)
    step = body[:end]
    run_at = step.index("        run: |\n") + len("        run: |\n")
    return "\n".join(
        line[10:] if line.startswith(" " * 10) else line
        for line in step[run_at:].splitlines()
    )


def _scope_for(run: str, mode: str) -> str:
    """Execute the step's own scope selection for one mode."""
    import shutil
    import subprocess
    import tempfile

    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover - CI always has bash
        raise unittest.SkipTest("bash is required")
    body = run.replace("${{ steps.scan_mode.outputs.mode }}", mode)
    # Stop before the python call: this measures the decision, not the sweep.
    body = body.split("python -u scripts/verify-delivery-path.py")[0]
    body += '\necho "SCOPE=$SCOPE"\n'
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sh", delete=False, encoding="utf-8", newline="\n"
    ) as handle:
        handle.write(body)
        path = handle.name
    try:
        result = subprocess.run(
            [bash, path], capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    finally:
        Path(path).unlink(missing_ok=True)
    match = re.search(r"^SCOPE=(\w+)$", result.stdout, re.M)
    if not match:
        raise AssertionError(f"no SCOPE in output:\n{result.stdout}{result.stderr}")
    return match.group(1)


class TheStepIsStillAdvisory(unittest.TestCase):
    """4: failure behaviour and continue-on-error semantics unchanged."""

    def setUp(self) -> None:
        # Read the step's own YAML lines as text. pyyaml is NOT installed on
        # the Actions runner - an `import yaml` here failed the whole suite in
        # run #1315 and with it a production today scan, which is exactly the
        # trap tests/test_scan_mode_selector.py already carries a guard for.
        # Text is enough for these four fields and needs nothing installed.
        text = WORKFLOW.read_text(encoding="utf-8").replace("\r\n", "\n")
        start = text.index(
            "      - name: Check the delivery path a viewer actually uses\n")
        body = text[start:]
        self.header = body[:body.index("        run: |\n")]

    def _header_value(self, key: str) -> str | None:
        for line in self.header.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}:"):
                return stripped[len(key) + 1:].strip()
        return None

    def test_4_continue_on_error_is_still_true(self):
        self.assertEqual(self._header_value("continue-on-error"), "true")

    def test_4_the_run_condition_is_unchanged(self):
        self.assertEqual(
            self._header_value("if"),
            "steps.staleness.outputs.stale != 'yes' "
            "|| steps.catchup.outputs.catchup != ''",
        )

    def test_4_it_still_runs_for_every_mode(self):
        """Narrowed, never skipped: a targeted run must still verify its own
        output, so the step must not become conditional on the mode."""
        run = _delivery_step_run()
        self.assertIn("scripts/verify-delivery-path.py", run)
        self.assertNotIn("steps.scan_mode", self._header_value("if") or "")

    def test_4_the_worker_and_timeout_arguments_are_unchanged(self):
        run = _delivery_step_run()
        self.assertIn("--workers 8", run)
        self.assertIn("--timeout 20", run)

    def test_4_the_scope_never_suppresses_a_failure(self):
        """Nothing about scoping touches how a refusal is recorded."""
        source = SCRIPT.read_text(encoding="utf-8")
        for marker in ("Target host not allowed", "error code: 1003"):
            self.assertIn(marker, source)
        self.assertIn("if not args.dry_run:", source)


class TheMeasuredReasonIsWrittenDown(unittest.TestCase):
    """A number nobody can source is a number nobody can re-check."""

    def test_the_workflow_says_why_targeted_is_narrowed(self):
        run = _delivery_step_run()
        self.assertIn("138.4s", run)
        self.assertIn("865", run)
        self.assertIn("535", run)

    def test_the_script_documents_the_two_scopes(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('SCOPES = ("all", "events")', source)
        self.assertIn("upcoming-targeted", source)


if __name__ == "__main__":
    unittest.main()
