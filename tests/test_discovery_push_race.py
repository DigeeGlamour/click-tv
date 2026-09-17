"""The Movie Discovery Refresh must survive losing a push race.

The bug this file pins down, as it appeared in production:

    error: cannot rebase: You have unstaged changes.
    error: Please commit or stash them.
    Rebase failed; leaving main untouched.

The sports scanner commits every few minutes and this job takes longer than
that, so losing the race is the normal case, not the exceptional one. The
retry ran `git rebase origin/main -X ours`, and the refresh leaves files it
does not publish - reports/, working/, other state/ entries - dirty in the
working tree after the allowlisted paths are staged. Rebase refuses to start
in that state, so the retry never ran at all and a lost race silently dropped
the refresh. That is how trending.json failed to reach production.

`-X ours` was a second, quieter bug. During a rebase "ours" is the upstream
being replayed onto, not the local work, so on a genuine conflict that flag
would have resolved in favour of origin/main and discarded the very files the
job exists to publish - the opposite of what its comment claimed.

Rather than assert the shape of the script, this runs it: the commit step is
extracted from the workflow YAML and executed against real local repositories,
with a concurrent sports commit landing first. The scenario is the one the
requirement names, and the assertions are about data, not about wording.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "movie-discovery-refresh.yml"

#: Written by the refresh but deliberately never published by it.
UNPUBLISHED_NOISE = "reports/keep.json"


def _commit_step_script() -> str:
    try:
        import yaml
    except ImportError:  # pragma: no cover - depends on the runner
        raise unittest.SkipTest("pyyaml unavailable")
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["refresh"]["steps"]
    for step in steps:
        if str(step.get("name") or "").startswith("Commit"):
            return step["run"]
    raise AssertionError("the workflow has no commit step")


class _Sandbox:
    """A bare 'origin' plus two clones, driven with real git."""

    def __init__(self, root: Path):
        self.root = root
        self.env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "sim", "GIT_AUTHOR_EMAIL": "sim@example.com",
            "GIT_COMMITTER_NAME": "sim", "GIT_COMMITTER_EMAIL": "sim@example.com",
            "GIT_CONFIG_GLOBAL": str(root / "gitconfig"),
            "GIT_CONFIG_SYSTEM": str(root / "gitconfig"),
        }
        (root / "gitconfig").write_text("[core]\n\tautocrlf = false\n", encoding="utf-8")

    def git(self, cwd: Path, *args: str, check: bool = True):
        result = subprocess.run(
            ["git", *args], cwd=str(cwd), env=self.env,
            capture_output=True, text=True,
        )
        if check and result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
        return result

    def write(self, cwd: Path, relative: str, content: str) -> None:
        path = cwd / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read(self, cwd: Path, relative: str) -> str:
        return (cwd / relative).read_text(encoding="utf-8")


class DiscoveryPushRaceTests(unittest.TestCase):
    """The scenario: sports lands first, discovery must still arrive."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None:  # pragma: no cover
            raise unittest.SkipTest("git unavailable")
        if shutil.which("bash") is None:  # pragma: no cover
            raise unittest.SkipTest("bash unavailable")
        cls.script = _commit_step_script()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.box = _Sandbox(self.root)

        self.origin = self.root / "origin.git"
        self.box.git(self.root, "init", "-q", "--bare", "-b", "main", str(self.origin))

        # Baseline content on main.
        seed = self.root / "seed"
        self.box.git(self.root, "clone", "-q", str(self.origin), str(seed))
        self.box.write(seed, "data/movies/discovery/trending.json", '{"trending":"old"}\n')
        self.box.write(seed, "data/today-match.json", '{"sports":"baseline"}\n')
        self.box.write(seed, "data/channels/bangla.json", '{"channels":"baseline"}\n')
        self.box.write(seed, "state/movie-trending-cache.json", '{"cache":"old"}\n')
        self.box.write(seed, UNPUBLISHED_NOISE, "placeholder\n")
        self.box.git(seed, "add", "-A")
        self.box.git(seed, "commit", "-q", "-m", "baseline")
        self.box.git(seed, "push", "-q", "origin", "HEAD:main")
        self.seed = seed

        # The refresh job's clone, with its outputs and its working-tree noise.
        self.job = self.root / "job"
        self.box.git(self.root, "clone", "-q", str(self.origin), str(self.job))
        self.box.write(self.job, "data/movies/discovery/trending.json", '{"trending":"REFRESHED"}\n')
        self.box.write(self.job, "state/movie-trending-cache.json", '{"cache":"REFRESHED"}\n')
        # The unstaged change that made rebase refuse to start.
        self.box.write(self.job, UNPUBLISHED_NOISE, '{"report":"this run"}\n')

    def _land_concurrent_sports_commit(self) -> str:
        self.box.git(self.seed, "pull", "-q", "origin", "main")
        self.box.write(self.seed, "data/today-match.json", '{"sports":"CONCURRENT"}\n')
        self.box.write(self.seed, "data/channels/bangla.json", '{"channels":"CONCURRENT"}\n')
        self.box.git(self.seed, "add", "-A")
        self.box.git(self.seed, "commit", "-q", "-m", "Auto update: sports")
        self.box.git(self.seed, "push", "-q", "origin", "HEAD:main")
        return self.box.git(self.seed, "rev-parse", "HEAD").stdout.strip()

    def _run_commit_step(self):
        script = self.root / "commit-step.sh"
        script.write_text(self.script, encoding="utf-8", newline="\n")
        return subprocess.run(
            ["bash", str(script)], cwd=str(self.job), env=self.box.env,
            capture_output=True, text=True,
        )

    def _final_main(self) -> Path:
        final = self.root / "final"
        self.box.git(self.root, "clone", "-q", str(self.origin), str(final))
        return final

    # ------------------------------------------------------------------ tests

    def test_a_lost_race_still_delivers_the_refresh_and_keeps_the_sports_commit(self):
        sports_sha = self._land_concurrent_sports_commit()
        result = self._run_commit_step()
        self.assertEqual(
            result.returncode, 0,
            f"the commit step failed:\n{result.stdout}\n{result.stderr}",
        )

        final = self._final_main()
        self.assertEqual(
            self.box.read(final, "data/movies/discovery/trending.json").strip(),
            '{"trending":"REFRESHED"}',
            "the discovery output was lost to the race",
        )
        self.assertEqual(
            self.box.read(final, "state/movie-trending-cache.json").strip(),
            '{"cache":"REFRESHED"}',
        )
        self.assertEqual(
            self.box.read(final, "data/today-match.json").strip(),
            '{"sports":"CONCURRENT"}',
            "the concurrent sports update was overwritten",
        )
        self.assertEqual(
            self.box.read(final, "data/channels/bangla.json").strip(),
            '{"channels":"CONCURRENT"}',
            "the concurrent live-TV update was overwritten",
        )

    def test_the_sports_commit_is_still_an_ancestor_so_nothing_was_force_pushed(self):
        sports_sha = self._land_concurrent_sports_commit()
        self._run_commit_step()
        final = self._final_main()
        ancestor = self.box.git(
            final, "merge-base", "--is-ancestor", sports_sha, "HEAD", check=False
        )
        self.assertEqual(
            ancestor.returncode, 0,
            "the concurrent sports commit is no longer reachable from main",
        )

    def test_the_published_commit_touches_nothing_outside_the_allowlist(self):
        sports_sha = self._land_concurrent_sports_commit()
        self._run_commit_step()
        final = self._final_main()
        changed = [
            line.strip()
            for line in self.box.git(
                final, "diff", "--name-only", sports_sha, "HEAD"
            ).stdout.splitlines()
            if line.strip()
        ]
        self.assertTrue(changed, "nothing was published at all")
        for path in changed:
            self.assertTrue(
                path.startswith("data/movies/discovery/")
                or path.startswith("data/movies/genres/")
                or path.startswith("state/movie-")
                or path == "state/provider-health.json",
                f"{path} is outside the movie discovery allowlist",
            )

    def test_the_unpublished_working_tree_noise_never_reaches_main(self):
        """The files that made rebase refuse must also never be committed."""
        self._land_concurrent_sports_commit()
        self._run_commit_step()
        final = self._final_main()
        self.assertEqual(
            self.box.read(final, UNPUBLISHED_NOISE).strip(),
            "placeholder",
            "a report this job merely wrote was published as if it were output",
        )

    def test_an_uncontested_push_still_works(self):
        """The ordinary path must not have been traded away for the retry."""
        result = self._run_commit_step()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        final = self._final_main()
        self.assertEqual(
            self.box.read(final, "data/movies/discovery/trending.json").strip(),
            '{"trending":"REFRESHED"}',
        )
        self.assertEqual(
            self.box.read(final, "data/today-match.json").strip(),
            '{"sports":"baseline"}',
        )


def _executable_lines(script: str) -> str:
    """The script with its commentary removed.

    The comments explain the two bugs by name, so a naive substring search
    finds `git rebase` and `-X ours` in the explanation of why neither is
    used any more. Only what bash would actually run is checked here.
    """
    return "\n".join(
        line for line in script.splitlines()
        if not line.lstrip().startswith("#")
    )


class TheRetryShapeTests(unittest.TestCase):
    """A couple of things that must not come back."""

    @classmethod
    def setUpClass(cls):
        cls.step = _executable_lines(_commit_step_script())

    def test_the_retry_does_not_rebase(self):
        self.assertNotIn(
            "git rebase", self.step,
            "rebase cannot start with a dirty working tree, which is exactly "
            "the state this job is in when it needs to retry",
        )

    def test_no_merge_strategy_flag_decides_the_outcome(self):
        for flag in ("-X ours", "-X theirs"):
            self.assertNotIn(
                flag, self.step,
                f"{flag} silently picks a side on conflict; the scope check "
                "is what keeps this job honest instead",
            )

    def test_nothing_is_force_pushed(self):
        for flag in ("--force", "-f ", "+HEAD", "--force-with-lease"):
            self.assertNotIn(flag, self.step, f"{flag} present in the push path")

    def test_the_scope_is_asserted_rather_than_assumed(self):
        self.assertIn("assert_scope", self.step)
        self.assertIn("merge-base", self.step)


if __name__ == "__main__":
    unittest.main()
