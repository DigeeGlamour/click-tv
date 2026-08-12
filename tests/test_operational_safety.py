import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scanner.security import redact_sensitive_text
from scanner.telegram_notify import build_failure_message


ROOT = Path(__file__).resolve().parent.parent


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
            (ROOT / "ClickTV_Colab_FINAL_EASY_5_MODE.ipynb").read_text(encoding="utf-8")
        )
        source = "\n".join(
            line
            for cell in notebook.get("cells", [])
            for line in cell.get("source", [])
        )
        self.assertIn("def redact_runtime_secrets", source)
        self.assertIn("rendered = redact_runtime_secrets", source)
        self.assertIn("PRIVATE_MOVIE_SOURCE_TOKEN", source)


class WorkflowSafetyTests(unittest.TestCase):
    def test_actions_removes_progress_before_clean_check(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        cleanup = workflow.index("rm -f working/scan-progress.json")
        clean_check = workflow.index('REMAINING="$(git status')
        self.assertLess(cleanup, clean_check)
        self.assertIn("Send Telegram after failed scan or push", workflow)
        self.assertIn("::add-mask::$VALUE", workflow)

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
        launcher = (ROOT / "CLICK_TV_EASY_PAT_SCAN.cmd").read_text(encoding="utf-8")
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
        self.assertIn('"rebase", "--abort"', launcher)

        advanced_launcher = (ROOT / "scripts/run-local-scan.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('Scripts\\python.exe', advanced_launcher)
        self.assertIn("$VenvPython -m pip install", advanced_launcher)
        self.assertNotIn("$PythonCommand.Source -m pip install -r", advanced_launcher)

    def test_actions_requires_complete_schedule_update(self):
        workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
        self.assertIn("test -f config/event-fixtures.json", workflow)
        self.assertIn("test -f scanner/schedule_resolver.py", workflow)
        self.assertIn("test -f tests/test_schedule_resolver.py", workflow)

    def test_colab_restores_temporary_settings_override(self):
        notebook = (ROOT / "ClickTV_Colab_FINAL_EASY_5_MODE.ipynb").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            '\\"config/settings.json\\", \\"working\\"',
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
