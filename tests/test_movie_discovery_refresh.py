"""PART 10: provider health state, and the lightweight discovery refresh.

Two things are being defended here.

The first is that a refresh job which cannot read the catalogue writes
NOTHING. "No catalogue" and "an empty catalogue" look identical to a
builder that just iterates what it was handed, and the difference between
them is every discovery row on the site going blank at once.

The second is scope. This job runs on its own schedule alongside a scanner
that owns thousands of sports and live-TV files, so its commit step lists
the movie discovery paths explicitly rather than staging by directory - a
refresh must never be able to carry someone else's change along with it.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import provider_health as ph  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "movie-discovery-refresh.yml"
SCAN_WORKFLOW = ROOT / ".github" / "workflows" / "scan.yml"
REFRESH_SCRIPT = ROOT / "scripts" / "refresh-movie-discovery.py"


def _load_workflow(path):
    with open(path, encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    # PyYAML parses the unquoted `on:` key as the boolean True.
    document["on"] = document.get("on", document.get(True))
    return document


class ProviderHealthShapeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "provider-health.json")
        self._original = ph.DEFAULT_PATH
        ph.DEFAULT_PATH = self.path
        self.addCleanup(lambda: setattr(ph, "DEFAULT_PATH", self._original))
        ph.reset()
        self.addCleanup(ph.reset)

    def _respond(self, *responses):
        from unittest.mock import patch

        queue = list(responses)
        return patch.object(
            ph,
            "_perform_request",
            side_effect=lambda *a, **k: queue.pop(0) if queue else (0, {}, None),
        )

    def test_a_healthy_provider_reports_ok_with_no_last_error(self):
        from unittest.mock import patch

        with patch.object(ph.time, "sleep"), self._respond((200, {}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        record = ph.metrics()["providers"]["tmdb"]
        self.assertTrue(record["ok"])
        self.assertIsNone(record["last_error"])
        self.assertTrue(record["last_success"])

    def test_a_rate_limited_provider_reports_not_ok_and_says_why(self):
        from unittest.mock import patch

        with patch.object(ph.time, "sleep"), self._respond(*[(429, {}, None)] * 5):
            ph.request_json("tmdb", "https://api.test/x")
        record = ph.metrics()["providers"]["tmdb"]
        self.assertFalse(record["ok"])
        self.assertIn("429", record["last_error"])
        self.assertTrue(record["last_429"])

    def test_a_rejected_credential_reports_not_ok_and_says_why(self):
        from unittest.mock import patch

        with patch.object(ph.time, "sleep"), self._respond((401, {}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        record = ph.metrics()["providers"]["tmdb"]
        self.assertFalse(record["ok"])
        self.assertIn("401", record["last_error"])

    def test_recovery_clears_the_error(self):
        from unittest.mock import patch

        with patch.object(ph.time, "sleep"), self._respond((503, {}, None), (200, {}, None)):
            ph.request_json("tmdb", "https://api.test/x")
        record = ph.metrics()["providers"]["tmdb"]
        self.assertTrue(record["ok"])
        self.assertIsNone(record["last_error"])

    def test_the_error_text_can_never_carry_a_secret(self):
        from unittest.mock import patch

        with patch.object(ph.time, "sleep"), self._respond((401, {}, None)):
            ph.request_json(
                "tmdb",
                "https://api.test/x?api_key=super-secret-value",
                headers={"Authorization": "Bearer super-secret-value"},
            )
        ph.save()
        on_disk = Path(self.path).read_text(encoding="utf-8")
        self.assertNotIn("super-secret-value", on_disk)
        self.assertNotIn("Authorization", on_disk)
        self.assertNotIn("api_key", on_disk)


class RefreshScriptTests(unittest.TestCase):
    def _run(self, cwd, extra_args=(), state_root=None):
        import os

        env = dict(os.environ)
        env["CLICKTV_STATE_ROOT"] = state_root or str(Path(cwd) / "state")
        env.pop("TMDB_API_KEY", None)
        env.pop("TMDB_API_TOKEN", None)
        return subprocess.run(
            [sys.executable, str(REFRESH_SCRIPT), *extra_args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_an_unreadable_catalogue_writes_nothing_and_fails_loudly(self):
        """"No catalogue" must never be mistaken for "an empty catalogue"."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data" / "movies").mkdir(parents=True)
            (Path(tmp) / "state").mkdir()
            result = self._run(tmp, ["--skip-trending"])
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("nothing written", result.stdout)
            self.assertFalse((Path(tmp) / "data" / "movies" / "discovery").exists())

    def test_a_real_catalogue_rebuilds_every_discovery_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            category = Path(tmp) / "data" / "movies" / "bangla"
            category.mkdir(parents=True)
            (category / "page-001.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "film-a",
                                "name": "Some Film",
                                "category": "Bangla",
                                "genres": ["Action"],
                                "release_date": "2026-01-02",
                                "first_seen_at": "2099-01-01T00:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (Path(tmp) / "state").mkdir()
            result = self._run(tmp, ["--skip-trending"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            discovery = Path(tmp) / "data" / "movies" / "discovery"
            for name in ("home.json", "just-added.json", "latest.json"):
                self.assertTrue((discovery / name).exists(), f"{name} not written")
            genres = Path(tmp) / "data" / "movies" / "genres"
            self.assertEqual(len(list(genres.glob("*.json"))), 8)
            self.assertEqual(
                json.loads((genres / "action.json").read_text(encoding="utf-8"))["items"],
                ["film-a"],
            )

    def test_the_refresh_never_writes_into_a_category_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            category = Path(tmp) / "data" / "movies" / "bangla"
            category.mkdir(parents=True)
            page = category / "page-001.json"
            page.write_text(
                json.dumps({"items": [{"id": "film-a", "name": "A", "url": "https://x.test/s.mkv"}]}),
                encoding="utf-8",
            )
            before = page.read_bytes()
            (Path(tmp) / "state").mkdir()
            self._run(tmp, ["--skip-trending"])
            self.assertEqual(page.read_bytes(), before, "the published catalogue is read-only here")

    def test_discovery_and_genre_folders_are_not_mistaken_for_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            movies = Path(tmp) / "data" / "movies"
            (movies / "bangla").mkdir(parents=True)
            (movies / "bangla" / "page-001.json").write_text(
                json.dumps({"items": [{"id": "film-a", "first_seen_at": "2099-01-01T00:00:00+00:00"}]}),
                encoding="utf-8",
            )
            (movies / "discovery").mkdir()
            (movies / "discovery" / "page-001.json").write_text(
                json.dumps({"items": [{"id": "should-not-be-read"}]}), encoding="utf-8"
            )
            (Path(tmp) / "state").mkdir()
            result = self._run(tmp, ["--skip-trending"])
            self.assertIn("1 published movie(s) from 1 category folder(s)", result.stdout)

    def test_no_secret_is_printed_by_the_refresh(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            category = Path(tmp) / "data" / "movies" / "bangla"
            category.mkdir(parents=True)
            (category / "page-001.json").write_text(
                json.dumps({"items": [{"id": "film-a", "first_seen_at": "2099-01-01T00:00:00+00:00"}]}),
                encoding="utf-8",
            )
            (Path(tmp) / "state").mkdir()
            env = dict(os.environ)
            env["CLICKTV_STATE_ROOT"] = str(Path(tmp) / "state")
            env["OMDB_API_KEY"] = "secret-omdb-value"
            env["FANART_API_KEY"] = "secret-fanart-value"
            result = subprocess.run(
                [sys.executable, str(REFRESH_SCRIPT), "--skip-trending"],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
            )
            combined = result.stdout + result.stderr
            self.assertNotIn("secret-omdb-value", combined)
            self.assertNotIn("secret-fanart-value", combined)


class RefreshWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = _load_workflow(WORKFLOW)

    def test_the_workflow_is_valid_yaml_with_one_job(self):
        self.assertEqual(list(self.workflow["jobs"]), ["refresh"])

    def test_it_runs_twice_a_day(self):
        crons = [entry["cron"] for entry in self.workflow["on"]["schedule"]]
        self.assertEqual(len(crons), 2, "the plan asks for 1-2 refreshes a day")

    def test_it_never_collides_with_the_movie_scan_hour(self):
        refresh_hours = {entry["cron"].split()[1] for entry in self.workflow["on"]["schedule"]}
        self.assertNotIn("4", refresh_hours, "must stay clear of the 04:37 movie scan")

    def test_the_full_movie_scan_cadence_is_untouched(self):
        scan = _load_workflow(SCAN_WORKFLOW)
        crons = [entry["cron"] for entry in scan["on"]["schedule"]]
        self.assertIn("37 4 * * *", crons, "the daily movie scan must still be scheduled")

    def test_it_has_its_own_concurrency_queue(self):
        self.assertEqual(self.workflow["concurrency"]["group"], "movie-discovery-refresh")
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])

    def test_every_private_credential_comes_from_github_secrets(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for name in ("TMDB_API_KEY", "TMDB_API_TOKEN", "OMDB_API_KEY", "FANART_API_KEY", "RAPIDAPI_KEY"):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", text)

    def test_public_providers_are_not_given_fake_secrets(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for public in ("TVMAZE", "CINEMETA", "ANILIST"):
            self.assertNotIn(f"secrets.{public}", text)

    def test_secrets_are_masked_before_anything_else_runs(self):
        steps = self.workflow["jobs"]["refresh"]["steps"]
        mask_index = next(i for i, s in enumerate(steps) if "Mask" in s.get("name", ""))
        refresh_index = next(i for i, s in enumerate(steps) if s.get("id") == "refresh")
        self.assertLess(mask_index, refresh_index)

    def test_a_failed_refresh_commits_nothing(self):
        steps = self.workflow["jobs"]["refresh"]["steps"]
        commit = next(s for s in steps if "Commit" in s.get("name", ""))
        self.assertIn("steps.refresh.outcome == 'success'", commit["if"])

    def test_the_commit_stages_movie_discovery_paths_only(self):
        steps = self.workflow["jobs"]["refresh"]["steps"]
        commit = next(s for s in steps if "Commit" in s.get("name", ""))
        # Only the actual staging command, not the prose around it.
        lines = [
            line.strip().rstrip("\\").strip()
            for line in commit["run"].splitlines()
            if not line.strip().startswith("#")
        ]
        add_start = next(i for i, line in enumerate(lines) if line.startswith("git add"))
        add_end = next(i for i in range(add_start, len(lines)) if lines[i].endswith("|| true"))
        staged = " ".join(lines[add_start : add_end + 1])

        for owned in ("data/movies/discovery", "data/movies/genres", "state/movie-trending-cache.json"):
            self.assertIn(owned, staged)
        # Nothing that belongs to the sports/live-TV scanner may be staged.
        for foreign in ("data/channels", "data/playback", "today-match.json", "upcoming.json", "-A", " . "):
            self.assertNotIn(foreign, staged, f"refresh must never stage {foreign}")

    def test_the_refresh_does_not_run_the_stream_scanner(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("scan.py", text)
        self.assertIn("scripts/refresh-movie-discovery.py", text)

    def test_the_refresh_script_never_imports_the_stream_scanner(self):
        source = REFRESH_SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("import scan", "from scanner import movies", "merge_candidates", "load_manual_movies"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
