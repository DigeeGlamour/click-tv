"""ধাপ ০ - the rollback is proved, not asserted.

The master plan's first step is "ফেরার পথ ছাড়া কিছু শুরু নয়". A backup file
that has never been restored from is not a way back; it is a file. So the
central test here does the whole round trip against a real git repository:

    publish a catalogue -> commit -> take a baseline
      -> damage the catalogue (edit a page, delete a page, add a stray page)
      -> restore
      -> every file is byte for byte what was published

and the refusal path is tested just as hard, because a restore that writes
files it cannot vouch for is the failure mode that matters. `scanner/output.py`
already replaces movie directories atomically for the same reason.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import movie_baseline  # noqa: E402


def _git_available() -> bool:
    try:
        completed = subprocess.run(
            ("git", "--version"), capture_output=True, text=True,
        )
    except (OSError, ValueError):
        return False
    return completed.returncode == 0


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _movie(identity: str, url: str, backups=()) -> dict:
    return {
        "id": identity,
        "name": identity.replace("-", " ").title(),
        "url": url,
        "backups": [{"url": backup} for backup in backups],
        "verification_status": "verified_global",
    }


def _build_catalogue(root: Path) -> None:
    """A small but structurally real catalogue: two categories, one series."""
    _write_json(root / "data" / "movies" / "bangla" / "index.json", {
        "slug": "bangla", "count": 2, "total_pages": 1,
    })
    _write_json(root / "data" / "movies" / "bangla" / "page-001.json", {
        "count": 2,
        "items": [
            _movie("hawa-2022", "https://example.test/hawa.m3u8",
                   ["https://backup.test/hawa.m3u8"]),
            _movie("poran-2022", "https://example.test/poran.m3u8"),
        ],
    })
    _write_json(root / "data" / "movies" / "mix" / "index.json", {
        "slug": "mix", "count": 1, "total_pages": 1,
    })
    _write_json(root / "data" / "movies" / "mix" / "page-001.json", {
        "count": 1,
        "items": [_movie("unknown-title", "https://example.test/unknown.mp4")],
    })
    # A derived surface: counted as a file, never as a catalogue total.
    _write_json(root / "data" / "movies" / "discovery" / "home.json", {
        "items": [_movie("hawa-2022", "https://example.test/hawa.m3u8")],
    })
    _write_json(root / "data" / "series" / "manifest.json", {
        "total_series": 1, "total_episodes": 2,
    })
    _write_json(root / "data" / "series" / "bangla" / "bachelor-point" / "season-01.json", {
        "episodes": [
            {"id": "bp-s01e01", "episode_key": "episode-001",
             "url": "https://example.test/bp1.mp4"},
            {"id": "bp-s01e02", "episode_key": "episode-002",
             "url": "https://example.test/bp2.mp4"},
        ],
    })
    _write_json(root / "data" / "manifest.json", {"schema_version": 1})
    _write_json(root / "state" / "movie-metadata-cache.json", {"records": {}})


class BaselineMeasurementTests(unittest.TestCase):
    """What the record says about the catalogue has to be the catalogue."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="movie-baseline-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        _build_catalogue(self.directory)
        self.baseline = movie_baseline.build_baseline(self.directory)

    def test_derived_surfaces_are_files_but_never_catalogue_totals(self) -> None:
        """data/movies/discovery re-lists films that are already counted.

        Counting it is how a 1,667-film catalogue reports 2,179 - measured on
        the real repository before this was written.
        """
        self.assertEqual(self.baseline["counts"]["movies_total"], 3)
        paths = {entry["path"] for entry in self.baseline["catalogue_files"]}
        self.assertIn("data/movies/discovery/home.json", paths)

    def test_every_playable_link_is_inventoried_primary_and_backup(self) -> None:
        inventory = movie_baseline.stream_inventory(self.directory)
        self.assertIn(
            "movie|bangla|hawa-2022|https://example.test/hawa.m3u8", inventory)
        self.assertIn(
            "movie|bangla|hawa-2022|https://backup.test/hawa.m3u8", inventory)
        self.assertEqual(self.baseline["counts"]["movie_links_total"], 4)
        self.assertEqual(self.baseline["counts"]["movie_backup_links"], 1)

    def test_episodes_are_counted_separately_from_movies(self) -> None:
        self.assertEqual(self.baseline["counts"]["episodes_total"], 2)
        self.assertEqual(self.baseline["counts"]["episode_links_total"], 2)
        self.assertEqual(self.baseline["counts"]["series_total"], 1)

    def test_verification_status_is_recorded_as_the_before_number(self) -> None:
        self.assertEqual(
            self.baseline["verification_status_counts"],
            {"verified_global": 3},
        )

    def test_recorded_paths_use_forward_slashes_on_every_platform(self) -> None:
        for entry in self.baseline["catalogue_files"]:
            self.assertNotIn("\\", entry["path"])

    def test_a_shared_file_is_recorded_but_not_a_catalogue_file(self) -> None:
        """`data/manifest.json` carries Today Match and Upcoming counts too,
        and the event scans rewrite it every few minutes.

        Keeping it under a movie rollback meant two things, both wrong: a drift
        check that went red within hours of Phase 0 on an unrelated scan, and a
        restore that would have rolled another pipeline's counts back to
        whenever the movie baseline was taken.
        """
        catalogue = {entry["path"] for entry in self.baseline["catalogue_files"]}
        shared = {entry["path"] for entry in self.baseline["shared_files"]}
        self.assertNotIn("data/manifest.json", catalogue)
        self.assertIn("data/manifest.json", shared)

    def test_a_changed_shared_file_is_not_reported_as_drift(self) -> None:
        _write_json(self.directory / "data" / "manifest.json",
                    {"schema_version": 1, "today_match": {"count": 99}})
        self.assertEqual(
            movie_baseline.verify_worktree(self.baseline, self.directory), [])

    def test_state_files_that_do_not_exist_are_recorded_as_absent(self) -> None:
        by_path = {entry["path"]: entry for entry in self.baseline["state_files"]}
        self.assertTrue(by_path["state/movie-metadata-cache.json"]["present"])
        self.assertFalse(by_path["state/movie-repair-queue.json"]["present"]
                         if "state/movie-repair-queue.json" in by_path else False)
        self.assertFalse(by_path["state/movie-trending-cache.json"]["present"])


class WorktreeDriftTests(unittest.TestCase):
    """`--check` has to name the file, not just say something changed."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="movie-baseline-drift-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        _build_catalogue(self.directory)
        self.baseline = movie_baseline.build_baseline(self.directory)

    def test_an_untouched_tree_reports_nothing(self) -> None:
        self.assertEqual(
            movie_baseline.verify_worktree(self.baseline, self.directory), [])

    def test_an_edited_page_is_reported_as_changed(self) -> None:
        page = self.directory / "data" / "movies" / "bangla" / "page-001.json"
        payload = json.loads(page.read_text(encoding="utf-8"))
        payload["items"][0]["url"] = "https://example.test/replaced.m3u8"
        _write_json(page, payload)
        findings = movie_baseline.verify_worktree(self.baseline, self.directory)
        self.assertEqual(
            [(f["path"], f["problem"]) for f in findings],
            [("data/movies/bangla/page-001.json", "changed")],
        )

    def test_a_deleted_page_is_reported_as_missing(self) -> None:
        (self.directory / "data" / "movies" / "mix" / "page-001.json").unlink()
        findings = movie_baseline.verify_worktree(self.baseline, self.directory)
        self.assertEqual(
            [(f["path"], f["problem"]) for f in findings],
            [("data/movies/mix/page-001.json", "missing")],
        )

    def test_a_stray_page_nobody_recorded_is_reported_too(self) -> None:
        """A page the baseline never saw publishes cards nobody accounted for."""
        _write_json(
            self.directory / "data" / "movies" / "mix" / "page-002.json",
            {"count": 1, "items": [_movie("ghost", "https://example.test/g.mp4")]},
        )
        findings = movie_baseline.verify_worktree(self.baseline, self.directory)
        self.assertEqual(
            [(f["path"], f["problem"]) for f in findings],
            [("data/movies/mix/page-002.json", "unrecorded")],
        )

    def test_state_drift_is_ignored_unless_it_is_asked_for(self) -> None:
        """State moves on every scan by design; reporting it as damage is noise."""
        _write_json(
            self.directory / "state" / "movie-metadata-cache.json",
            {"records": {"hawa-2022": {"tmdb_id": 1}}},
        )
        self.assertEqual(
            movie_baseline.verify_worktree(self.baseline, self.directory), [])
        findings = movie_baseline.verify_worktree(
            self.baseline, self.directory, include_state=True)
        self.assertEqual(
            [(f["path"], f["problem"]) for f in findings],
            [("state/movie-metadata-cache.json", "changed")],
        )


@unittest.skipUnless(_git_available(), "git is required for the rollback proof")
class RollbackProofTests(unittest.TestCase):
    """The round trip, against a real repository."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="movie-baseline-git-"))
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self._run("init", "-q", "-b", "main")
        self._run("config", "user.email", "test@example.invalid")
        self._run("config", "user.name", "Baseline Test")
        _build_catalogue(self.directory)
        self._run("add", "-A")
        self._run("commit", "-q", "-m", "publish catalogue")
        self.baseline = movie_baseline.build_baseline(
            self.directory, label="rollback-proof")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        completed = subprocess.run(
            ("git", "-C", str(self.directory), *args),
            capture_output=True, text=True,
        )
        self.assertEqual(
            completed.returncode, 0,
            msg=f"git {' '.join(args)} failed: {completed.stderr}",
        )
        return completed

    def _snapshot(self) -> dict:
        """Path -> newline-normalised content.

        Normalised rather than raw, because `core.autocrlf` is true on the
        owner's machine: the working tree holds CRLF and the git object holds
        LF, so a restore that writes the blob back writes the same JSON with
        different bytes. Content is the contract; the byte-for-byte claim that
        matters is made by `test_restored_tree_is_clean_against_the_commit`,
        which asks git itself.
        """
        return {
            movie_baseline._relative(path, self.directory):
                movie_baseline.normalize_newlines(path.read_bytes())
            for path in movie_baseline._catalogue_file_paths(self.directory)
        }

    def _porcelain(self) -> str:
        completed = subprocess.run(
            ("git", "-C", str(self.directory), "status", "--porcelain"),
            capture_output=True, text=True,
        )
        return (completed.stdout or "").strip()

    def test_baseline_records_the_commit_and_a_clean_tree(self) -> None:
        self.assertTrue(self.baseline["git"]["available"])
        self.assertEqual(len(self.baseline["git"]["commit"]), 40)
        self.assertFalse(self.baseline["git"]["dirty"])

    def test_damage_then_restore_gives_back_the_exact_bytes(self) -> None:
        before = self._snapshot()

        # Three different kinds of damage at once, which is what a bad
        # migration actually looks like: a card rewritten, a page deleted, and
        # a page invented.
        page = self.directory / "data" / "movies" / "bangla" / "page-001.json"
        payload = json.loads(page.read_text(encoding="utf-8"))
        payload["items"] = payload["items"][:1]
        payload["count"] = 1
        _write_json(page, payload)
        (self.directory / "data" / "movies" / "mix" / "page-001.json").unlink()
        _write_json(
            self.directory / "data" / "movies" / "mix" / "page-002.json",
            {"count": 1, "items": [_movie("ghost", "https://example.test/g.mp4")]},
        )

        self.assertNotEqual(
            movie_baseline.verify_worktree(self.baseline, self.directory), [])

        result = movie_baseline.restore(
            self.baseline, self.directory, dry_run=False)
        self.assertTrue(result["verified"])
        self.assertEqual(result["files_written"], result["files_checked"])

        # The invented page is not the restore's to delete - it restores what
        # was recorded and says the rest is still different, rather than
        # deleting files on its own initiative.
        stray = self.directory / "data" / "movies" / "mix" / "page-002.json"
        self.assertTrue(stray.exists())
        stray.unlink()

        after = self._snapshot()
        self.assertEqual(after, before)
        self.assertEqual(
            movie_baseline.verify_worktree(self.baseline, self.directory), [])

    def test_restored_tree_is_clean_against_the_commit(self) -> None:
        """git itself is the arbiter: after a restore there is nothing to commit.

        Stronger than comparing bytes in Python, because it applies the same
        checkin filter a real push would and therefore catches a restore that
        produced content git would consider a change.
        """
        page = self.directory / "data" / "movies" / "bangla" / "page-001.json"
        page.write_text("{}", encoding="utf-8")
        (self.directory / "data" / "movies" / "mix" / "page-001.json").unlink()
        self.assertNotEqual(self._porcelain(), "")

        movie_baseline.restore(self.baseline, self.directory, dry_run=False)

        self.assertEqual(
            self._porcelain(), "",
            msg="the restored tree still differs from the recorded commit",
        )

    def test_a_dry_run_verifies_and_writes_nothing(self) -> None:
        page = self.directory / "data" / "movies" / "bangla" / "page-001.json"
        page.write_text("{}", encoding="utf-8")
        result = movie_baseline.restore(
            self.baseline, self.directory, dry_run=True)
        self.assertTrue(result["verified"])
        self.assertEqual(result["files_written"], 0)
        self.assertEqual(page.read_text(encoding="utf-8"), "{}")

    def test_a_tampered_baseline_is_refused_before_anything_is_written(self) -> None:
        """The whole point: it must not write bytes it cannot vouch for."""
        before = self._snapshot()
        tampered = json.loads(json.dumps(self.baseline))
        for entry in tampered["catalogue_files"]:
            if entry["path"].endswith("bangla/page-001.json"):
                entry["sha256"] = "0" * 64
        # Damage a *different* file, so a restore that wrote as it went would
        # have already touched the disk by the time it reached the bad digest.
        (self.directory / "data" / "movies" / "mix" / "page-001.json").write_text(
            "{}", encoding="utf-8")

        with self.assertRaises(movie_baseline.RestoreRefused):
            movie_baseline.restore(tampered, self.directory, dry_run=False)

        after = self._snapshot()
        self.assertEqual(
            after["data/movies/mix/page-001.json"], b"{}",
            msg="the refused restore must not have repaired anything",
        )
        self.assertEqual(
            after["data/movies/bangla/page-001.json"],
            before["data/movies/bangla/page-001.json"],
        )

    def test_a_file_missing_from_the_commit_is_refused(self) -> None:
        extended = json.loads(json.dumps(self.baseline))
        extended["catalogue_files"].append({
            "path": "data/movies/mix/page-099.json",
            "sha256": "1" * 64, "bytes": 2,
        })
        with self.assertRaises(movie_baseline.RestoreRefused):
            movie_baseline.restore(extended, self.directory, dry_run=False)

    def test_a_baseline_without_a_commit_cannot_be_restored_from(self) -> None:
        orphan = json.loads(json.dumps(self.baseline))
        orphan["git"] = {"available": False, "commit": "", "branch": "", "dirty": None}
        with self.assertRaises(movie_baseline.RestoreRefused):
            movie_baseline.restore(orphan, self.directory, dry_run=True)

    def test_copy_to_writes_the_catalogue_and_the_record_together(self) -> None:
        destination = self.directory.parent / (self.directory.name + "-copy")
        self.addCleanup(shutil.rmtree, destination, ignore_errors=True)
        result = movie_baseline.copy_to(
            self.baseline, destination, self.directory)
        self.assertEqual(
            result["files_copied"], len(self.baseline["catalogue_files"]))
        self.assertTrue((destination / "movie-baseline.json").is_file())
        self.assertEqual(
            (destination / "data" / "movies" / "bangla" / "page-001.json").read_bytes(),
            (self.directory / "data" / "movies" / "bangla" / "page-001.json").read_bytes(),
        )


class RealRepositoryBaselineTests(unittest.TestCase):
    """The baseline committed in this repository has to describe this catalogue.

    A digest file that stops matching the files it names is worse than no
    digest file, so this is checked on every run rather than only when ধাপ ০ is
    re-run by hand.
    """

    ROOT = Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        path = self.ROOT / movie_baseline.DEFAULT_BASELINE_PATH
        if not path.is_file():
            self.skipTest("no baseline recorded yet")
        self.baseline = movie_baseline.load_baseline(root=self.ROOT)

    def test_recorded_counts_match_the_published_catalogue(self) -> None:
        self.assertEqual(
            self.baseline["counts"], movie_baseline.catalogue_counts(self.ROOT))

    def test_recorded_digests_match_the_published_files(self) -> None:
        findings = movie_baseline.verify_worktree(self.baseline, self.ROOT)
        self.assertEqual(
            findings, [],
            msg="state/movie-baseline.json no longer describes data/movies; "
                "re-run scripts/movie-baseline-backup.py",
        )

    def test_the_stream_inventory_digest_still_holds(self) -> None:
        inventory = movie_baseline.stream_inventory(self.ROOT)
        self.assertEqual(
            movie_baseline.digest_bytes("\n".join(inventory).encode("utf-8")),
            self.baseline["stream_inventory"]["sha256"],
        )

    def test_the_recorded_inventory_is_the_catalogue_it_claims(self) -> None:
        """INVARIANT ২ reads these lines, so they are verified, not trusted."""
        self.assertEqual(
            movie_baseline.recorded_inventory(self.baseline),
            movie_baseline.stream_inventory(self.ROOT),
        )

    def test_a_hand_edited_inventory_is_refused_rather_than_believed(self) -> None:
        """A publish is blocked on this comparison, so an edited list must not
        be able to change the answer quietly."""
        tampered = json.loads(json.dumps(self.baseline))
        tampered["stream_inventory"]["entries"].pop()
        with self.assertRaises(ValueError):
            movie_baseline.recorded_inventory(tampered)


class TheGateSeesThisRunsEpisodes(unittest.TestCase):
    """`prepare_manual_series` normalises, and the normalised shape keeps its
    episodes in `episode_payloads` rather than inside each season.

    Reading only `season["episodes"]` found nothing on every real run, so the
    caller fell back to the episodes already on disk - the PREVIOUS scan's -
    which is precisely the reading this function exists to avoid. Measured on
    2026-09-24: the gate counted 316 episodes while the run published 653, and
    337 migrated streams read as lost.
    """

    def test_the_normalised_shape_is_read(self):
        prepared = {"items": [{
            "id": "bachelor-point-2026",
            "seasons": [{"number": 1, "title": "Season 1", "count": 2}],
            "episode_payloads": {1: [
                {"episode_key": "01", "url": "https://cdn/e1.mkv"},
                {"episode_key": "02", "url": "https://cdn/e2.mkv"},
            ]},
        }]}
        found = movie_baseline.episodes_from_prepared_series(prepared)
        self.assertEqual(len(found), 2)
        self.assertEqual(
            sorted(episode.get("url") for _scope, episode in found),
            ["https://cdn/e1.mkv", "https://cdn/e2.mkv"],
        )

    def test_the_staging_shape_still_works(self):
        """Both shapes, because a caller that hands back raw staging records
        must not silently start returning nothing."""
        prepared = {"items": [{
            "id": "x",
            "seasons": [{"number": 1, "episodes": [
                {"episode_key": "01", "url": "https://cdn/a.mkv"}]}],
        }]}
        self.assertEqual(
            len(movie_baseline.episodes_from_prepared_series(prepared)), 1)

    def test_a_season_with_episodes_is_not_read_twice(self):
        prepared = {"items": [{
            "id": "x",
            "seasons": [{"number": 1, "episodes": [
                {"episode_key": "01", "url": "https://cdn/a.mkv"}]}],
            "episode_payloads": {1: [
                {"episode_key": "01", "url": "https://cdn/a.mkv"}]},
        }]}
        self.assertEqual(
            len(movie_baseline.episodes_from_prepared_series(prepared)), 1)

    def test_a_payload_for_a_season_that_is_not_listed_is_ignored(self):
        """The seasons list is the catalogue of record; a stray payload key is
        not a reason to invent a season nobody published."""
        prepared = {"items": [{
            "id": "x",
            "seasons": [{"number": 1, "count": 0}],
            "episode_payloads": {9: [
                {"episode_key": "01", "url": "https://cdn/ghost.mkv"}]},
        }]}
        self.assertEqual(
            movie_baseline.episodes_from_prepared_series(prepared), [])

    def test_nothing_prepared_is_still_nothing(self):
        self.assertEqual(movie_baseline.episodes_from_prepared_series(None), [])
        self.assertEqual(
            movie_baseline.episodes_from_prepared_series({"items": []}), [])


if __name__ == "__main__":
    unittest.main()
