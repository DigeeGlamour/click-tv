import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scanner.security import redact_sensitive_text
from scanner.telegram_notify import build_failure_message


ROOT = Path(__file__).resolve().parent.parent
LOCAL_SCAN_TOOLS = ROOT / "Local and Google Colab"
NOTEBOOK_PATH = LOCAL_SCAN_TOOLS / "ClickTV_Colab_FINAL_EASY_5_MODE.ipynb"
LAUNCHER_PATH = LOCAL_SCAN_TOOLS / "CLICK_TV_EASY_PAT_SCAN.cmd"


class SecretRedactionTests(unittest.TestCase):
    def test_private_movie_token_is_redacted_from_plain_and_encoded_text(self):
        token = "github_pat_PRIVATE_TEST_TOKEN_123456789"
        environment = {"PRIVATE_MOVIE_SOURCE_TOKEN": token}
        message = f"request failed token={token}"
        clean = redact_sensitive_text(message, environment)
        self.assertNotIn(token, clean)
        self.assertIn("[REDACTED]", clean)

    def test_scan_failure_path_uses_redactor(self):
        source = (ROOT / "scan.py").read_text(encoding="utf-8")
        self.assertIn("safe_error = redact_sensitive_text(error)", source)
        self.assertNotIn('error=str(error)', source)

    def test_colab_masks_all_runtime_output(self):
        notebook = json.loads(
            NOTEBOOK_PATH.read_text(encoding="utf-8")
        )
        source = "\n".join(
            line
            for cell in notebook.get("cells", [])
            for line in cell.get("source", [])
        )
        self.assertIn("def redact_runtime_secrets", source)
        self.assertIn("rendered = redact_runtime_secrets", source)
        self.assertIn("PRIVATE_MOVIE_SOURCE_TOKEN", source)

    def test_colab_and_the_easy_launcher_both_offer_every_optional_api_key(self):
        """The poster/logo and sports-data providers added this session each
        read one env var (scanner/poster_providers.py,
        scanner/sports_poster_providers.py) and the private sports source
        reads scanner/source_loader.py's ${PRIVATE_SPORTS_SOURCE_TOKEN}. A
        local PC run or a Colab run has no GitHub Actions secrets store, so
        both entry points must offer to collect and forward the same six
        names - otherwise those features silently degrade off-CI only.
        """
        optional_keys = (
            "PRIVATE_SPORTS_SOURCE_TOKEN", "FANART_API_KEY", "OMDB_API_KEY",
            "THESPORTSDB_API_KEY", "HIGHLIGHTLY_API_KEY", "SPORTMONKS_API_TOKEN",
        )

        notebook = json.loads(
            NOTEBOOK_PATH.read_text(encoding="utf-8")
        )
        source = "\n".join(
            line
            for cell in notebook.get("cells", [])
            for line in cell.get("source", [])
        )
        for key in optional_keys:
            self.assertIn(f'get_secret("{key}")', source, key)
            self.assertIn(f'scan_environment["{key}"] = {key}', source, key)
        self.assertIn("PRIVATE_SPORTS_SOURCE_TOKEN,", source.split("RUNTIME_SECRETS")[1])

        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        for key in optional_keys:
            self.assertIn(f"$env:{key} = ", launcher, key)
            self.assertIn(f"'{key}'", launcher, key)


class WorkflowSafetyTests(unittest.TestCase):
    def test_actions_removes_progress_before_clean_check(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        cleanup = workflow.index("rm -f working/scan-progress.json")
        clean_check = workflow.index('REMAINING="$(git status')
        self.assertLess(cleanup, clean_check)
        self.assertIn("Send Telegram after failed scan or push", workflow)
        self.assertIn("::add-mask::$VALUE", workflow)
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7", workflow)
        self.assertIn("actions/setup-node@v7", workflow)
        self.assertIn("actions/cache@v6", workflow)
        self.assertIn("actions/upload-artifact@v6", workflow)
        self.assertIn("git restore --staged --worktree -- .", workflow)

    def test_all_shell_scripts_are_forced_to_lf(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)
        shell_scripts = [path for path in ROOT.rglob("*.sh") if ".git" not in path.parts]
        self.assertTrue(shell_scripts)
        for path in shell_scripts:
            self.assertNotIn(b"\r", path.read_bytes(), path.relative_to(ROOT))

    def test_runtime_progress_and_virtualenv_are_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("working/scan-progress.json", ignored)
        self.assertIn(".venv/", ignored)

    def test_untracked_progress_file_cannot_block_a_git_push_check(self):
        with tempfile.TemporaryDirectory(prefix="clicktv-progress-test-") as temp:
            repository = Path(temp)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / ".gitignore").write_text(
                (ROOT / ".gitignore").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            progress = repository / "working" / "scan-progress.json"
            progress.parent.mkdir(parents=True)
            progress.write_text('{"status":"running"}', encoding="utf-8")

            status = subprocess.run(
                ["git", "-C", str(repository), "status", "--porcelain"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertNotIn("working/scan-progress.json", status)

            progress.unlink()
            self.assertFalse(progress.exists())

    def test_recommended_launcher_uses_virtualenv_without_legacy_cleanup(self):
        launcher = LAUNCHER_PATH.read_text(encoding="utf-8")
        self.assertIn('Scripts\\python.exe', launcher)
        self.assertIn("$VenvPython -m pip install", launcher)
        self.assertNotIn("$PythonCommand.Source -m pip install -r", launcher)
        self.assertNotIn("CLICK_TV_ONE_CLICK_ALL.cmd", launcher)
        self.assertNotIn("RUN_CLICK_TV_LOCAL_SCAN.cmd", launcher)
        self.assertNotIn("scripts\\one-click-all.ps1", launcher)
        self.assertNotIn("$FilesToSync", launcher)
        self.assertNotIn("Fix scanner and add easy PAT scan launcher", launcher)
        self.assertNotIn("Copy-Item -LiteralPath $Source", launcher)
        self.assertIn('"data", "reports", "state"', launcher)
        self.assertIn('"reset", "--hard", "origin/main"', launcher)
        self.assertIn("ClickTV-Data-Scanner", launcher)
        self.assertIn("Invoke-RebaseAndPush", launcher)
        self.assertIn("Reset-DedicatedScannerRuntimeChanges", launcher)
        recovery_cleanup = launcher.index(
            "Reset-DedicatedScannerRuntimeChanges -RepositoryPath $ClonePath"
        )
        recovery_rebase = launcher.index(
            "Invoke-RebaseAndPush -RepositoryPath $ClonePath", recovery_cleanup
        )
        self.assertLess(recovery_cleanup, recovery_rebase)
        self.assertIn(
            '@("restore", "--staged", "--worktree", "--", ".")', launcher
        )
        self.assertIn('"rebase", "--abort"', launcher)
        self.assertIn('"clone", "--depth", "1", "--no-tags"', launcher)
        self.assertIn("Test-UsableScannerClone", launcher)
        self.assertIn("Move-IncompleteScannerClone", launcher)
        self.assertIn("http.lowSpeedTime=30", launcher)

        advanced_launcher = (ROOT / "scripts/run-local-scan.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('Scripts\\python.exe', advanced_launcher)
        self.assertIn("$VenvPython -m pip install", advanced_launcher)
        self.assertNotIn("$PythonCommand.Source -m pip install -r", advanced_launcher)
        self.assertIn(
            '@("restore", "--staged", "--worktree", "--", ".")',
            advanced_launcher,
        )

    def test_actions_requires_complete_schedule_update(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        # Requirement 4: Today every 20 minutes, plus the 5-minute trigger
        # that drives the targeted -15 minute Upcoming scan.
        self.assertIn('cron: "0,20,40 * * * *"', workflow)
        self.assertIn('cron: "*/5 * * * *"', workflow)
        self.assertIn('cron: "9 5,17 * * *"', workflow)
        # The requirement is that the workflow refuses to run without these
        # files, not that it uses one particular shell idiom to check them.
        for required in (
            "config/event-fixtures.json",
            "scanner/schedule_resolver.py",
            "tests/test_schedule_resolver.py",
        ):
            self.assertIn(required, self._required_files(workflow), required)

    @staticmethod
    def _required_files(workflow: str) -> set:
        """The paths the Validate step will not start without."""
        block = workflow.split("REQUIRED_FILES=(", 1)
        if len(block) != 2:
            return set()
        listing = block[1].split(")", 1)[0]
        return {line.strip() for line in listing.splitlines() if line.strip()}

    def test_a_missing_file_is_named_rather_than_just_exiting_one(self):
        """Five un-uploaded config files cost hours because the step said only
        "Process completed with exit code 1"."""
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        required = self._required_files(workflow)
        self.assertGreater(len(required), 40, len(required))
        # Every config file the scanner loads has to be on the list, because
        # these are exactly the ones that went missing.
        for config in (
            "config/settings.json",
            "config/event-fixtures.json", "config/channel-aliases.json",
            "config/channel-categories.json", "config/header-profiles.json",
        ):
            self.assertIn(config, required, config)
        # And the failure has to report all of them, not stop at the first.
        self.assertIn("MISSING+=", workflow)
        self.assertIn("::error", workflow)
        self.assertIn("MISSING  $GONE", workflow)
        self.assertNotIn("test -f config/", workflow)

    def test_the_new_scanner_modules_cannot_be_left_behind(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        required = self._required_files(workflow)
        for module in (
            "scanner/channel_resolver.py", "scanner/channel_groups.py",
            "scanner/event_lifecycle.py", "scanner/streamed_provider.py",
            "scanner/live_protection.py", "scanner/snapshot_publish.py",
            "scanner/targeted_scan.py", "site/assets/css/embed-player.css",
            "site/sw.js", "tests/test_sports_channel_system.py",
            "tests/test_event_fixture_catalogue.py",
        ):
            self.assertIn(module, required, module)

    def test_every_workflow_uses_node24_action_majors(self):
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / ".github/workflows").glob("*.yml")
        )
        for legacy in (
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "actions/setup-node@v4",
            "actions/cache@v4",
            "actions/upload-artifact@v4",
        ):
            self.assertNotIn(legacy, workflows)

    def test_colab_restores_temporary_settings_override(self):
        notebook = NOTEBOOK_PATH.read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '\\"restore\\", \\"--staged\\", \\"--worktree\\", \\"--\\", \\".\\"',
            notebook,
        )

    def test_failure_message_contains_run_link_but_no_secret(self):
        message = build_failure_message(
            "channels", "abc123", "main", "https://github.com/example/actions/runs/1"
        )
        self.assertIn("FAILED", message)
        self.assertIn("Open failed GitHub Actions run", message)
        self.assertIn("Published:</b> No", message)


if __name__ == "__main__":
    unittest.main()
