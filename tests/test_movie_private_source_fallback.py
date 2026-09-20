"""ধাপ ২ / S-03 - a transient GitHub failure must not cost the owner's catalogue.

The plan calls this the highest-risk item in the whole movie system and the
smallest to fix: 350 of the 1,667 published films are `manual_trusted`, they all
come from one private repository, and the fetch had no fallback whatsoever.

Two things were wrong, and only the first was visible in the config:

  * `use_last_valid_cache: false` / `require_fresh: true` - so a failed fetch
    either raised and took the whole scan down, or contributed nothing.
  * the per-file cache could not have helped even switched on, because it is
    consulted per *discovered* file and a failed archive fetch discovers none.
    The fallback had to be built, not just enabled.

Preference order, as ধাপ ২ states it: fresh fetch → local checkout →
last-good snapshot → and only then, if nothing exists at all, fail.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import movies  # noqa: E402

REPOSITORY_ID = "test-private-repository"

MOVIE_TXT = """Movie-1
Movie name: Hawa
Movie Category: Bangla Movies
Movie year: 2022

RESOLUTION 1: HD 1080P
STREAM Link 1: https://cdn.test/hawa-1080p.mp4
"""

SECOND_TXT = """Movie-1
Movie name: Poran
Movie Category: Bangla Movies
Movie year: 2022

RESOLUTION 1: HD 1080P
STREAM Link 1: https://cdn.test/poran-1080p.mp4
"""


class _FetchFails(Exception):
    pass


class PrivateSourceFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "movie-sources.json"
        self.cache_path = self.root / "manual-movie-remote-cache.json"
        self.series_path = self.root / "manual-series-catalog.json"
        self.checkout = self.root / "private-checkout"

        self._original_fetch = movies._github_repository_snapshot_files
        self.addCleanup(
            setattr, movies, "_github_repository_snapshot_files",
            self._original_fetch,
        )

    # -- helpers ---------------------------------------------------------

    def _write_config(self, *, require_fresh=True, use_cache=True,
                      with_checkout=False, checkout_optional=True) -> None:
        directory_sources = []
        if with_checkout:
            directory_sources.append({
                "id": "test-local-checkout",
                "name": "Local checkout",
                "path": str(self.checkout),
                "fallback_for": REPOSITORY_ID,
                "optional": checkout_optional,
                "recursive": True,
                "extensions": [".txt"],
                "enabled": True,
            })
        self.config_path.write_text(json.dumps({
            "version": 3,
            "enabled": True,
            "timeout_seconds": 5,
            "use_last_valid_cache": True,
            "repository_sources": [{
                "id": REPOSITORY_ID,
                "name": "Private Test Repository",
                "repository": "owner/private",
                "ref": "main",
                "root": "categories",
                "recursive": True,
                "extensions": [".txt"],
                "require_fresh": require_fresh,
                "require_any_valid_file": True,
                "require_all_files": False,
                "use_last_valid_cache": use_cache,
                "enabled": True,
            }],
            "directory_sources": directory_sources,
            "sources": [],
        }), encoding="utf-8")

    def _serve(self, files) -> None:
        def fake(source, timeout_seconds):
            return files, {
                "repository": "owner/private", "ref": "main",
                "root": "categories", "authenticated": True, "revision": "",
                "resolved_url": "https://codeload.test/x", "file_count": len(files),
            }
        movies._github_repository_snapshot_files = fake

    def _fail(self, error=None) -> None:
        def fake(source, timeout_seconds):
            raise (error or OSError("connection reset by peer"))
        movies._github_repository_snapshot_files = fake

    def _run(self):
        return movies._remote_source_items(
            self.config_path, self.cache_path, self.series_path)

    def _rows(self, report):
        return {row["id"]: row for row in report["sources"]}

    # -- the healthy run, which has to keep behaving as it did -----------

    def test_a_healthy_fetch_publishes_fresh_and_fills_the_cache(self) -> None:
        self._write_config()
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        items, report = self._run()

        self.assertEqual([item["name"] for item in items], ["Hawa"])
        self.assertEqual(report["degraded_repositories"], {})
        statuses = {row["status"] for row in report["sources"]}
        self.assertEqual(statuses, {"fresh"})
        self.assertTrue(self.cache_path.exists())

    # -- the fault this phase exists for ---------------------------------

    def test_a_failed_fetch_serves_the_last_good_snapshot(self) -> None:
        """Previously: raise, or publish nothing. Now: yesterday's catalogue."""
        self._write_config()
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        self._run()

        self._fail()
        items, report = self._run()

        self.assertEqual([item["name"] for item in items], ["Hawa"])
        self.assertIn(REPOSITORY_ID, report["degraded_repositories"])
        self.assertTrue(
            report["degraded_repositories"][REPOSITORY_ID].startswith(
                "last_good_snapshot:"),
            msg=report["degraded_repositories"],
        )
        rows = self._rows(report)
        self.assertEqual(rows[REPOSITORY_ID]["status"], "degraded_fallback")
        cached_rows = [row for row in report["sources"]
                       if row["status"] == "cached"]
        self.assertEqual(len(cached_rows), 1)
        self.assertEqual(cached_rows[0]["item_count"], 1)

    def test_require_fresh_no_longer_raises_when_a_fallback_exists(self) -> None:
        """`require_fresh` means "do not publish nothing", not "do not publish"."""
        self._write_config(require_fresh=True)
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        self._run()

        self._fail()
        items, _ = self._run()  # must not raise
        self.assertTrue(items)

    def test_require_fresh_still_raises_when_there_is_nothing_to_fall_back_to(self) -> None:
        """A run with no content at all must stop rather than publish empty."""
        self._write_config(require_fresh=True)
        self._fail()
        with self.assertRaises(RuntimeError) as caught:
            self._run()
        self.assertIn("no fallback was available", str(caught.exception))

    def test_a_local_checkout_is_preferred_over_the_snapshot(self) -> None:
        """ধাপ ২'s stated order. A checkout is content someone put there on
        purpose; the snapshot is only what we saw last time."""
        self._write_config(with_checkout=True)
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        self._run()

        self.checkout.mkdir(parents=True, exist_ok=True)
        (self.checkout / "poran.txt").write_text(SECOND_TXT, encoding="utf-8")

        self._fail()
        items, report = self._run()

        self.assertEqual(
            report["degraded_repositories"][REPOSITORY_ID],
            "local_checkout:test-local-checkout",
        )
        self.assertEqual([item["name"] for item in items], ["Poran"])
        self.assertNotIn(
            "cached", {row["status"] for row in report["sources"]},
            msg="the snapshot must not be served as well as the checkout",
        )

    def test_a_standby_checkout_contributes_nothing_on_a_healthy_run(self) -> None:
        """Otherwise every private title is published twice."""
        self._write_config(with_checkout=True)
        self.checkout.mkdir(parents=True, exist_ok=True)
        (self.checkout / "poran.txt").write_text(SECOND_TXT, encoding="utf-8")
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])

        items, report = self._run()

        self.assertEqual([item["name"] for item in items], ["Hawa"])
        rows = self._rows(report)
        self.assertEqual(rows["test-local-checkout"]["status"], "standby")
        self.assertEqual(rows["test-local-checkout"]["available_files"], 1)

    def test_an_absent_optional_checkout_is_not_reported_as_a_failure(self) -> None:
        """A report that cries wolf every run is a report nobody reads."""
        self._write_config(with_checkout=True)
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])

        _, report = self._run()

        rows = self._rows(report)
        self.assertEqual(rows["test-local-checkout"]["status"], "standby")

        # And when the repository fails too, so the checkout is really wanted:
        self._fail()
        _, report = self._run()
        rows = self._rows(report)
        self.assertEqual(rows["test-local-checkout"]["status"], "skipped_absent")
        self.assertEqual(
            rows["test-local-checkout"]["message"], "optional_directory_absent")

    def test_the_snapshot_fallback_is_strict_about_whose_snapshot_it_is(self) -> None:
        """The real cache holds keys from two earlier spellings of this source.
        Serving those would resurrect a configuration no longer in force."""
        self._write_config()
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        self._run()

        cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        renamed = {}
        for key, value in cache["sources"].items():
            renamed["legacy-spelling:" + key.split(":", 1)[-1]] = value
        cache["sources"] = renamed
        self.cache_path.write_text(json.dumps(cache), encoding="utf-8")

        self._fail()
        with self.assertRaises(RuntimeError):
            self._run()

    def test_a_cache_entry_with_no_items_is_not_a_fallback(self) -> None:
        """An empty snapshot is not content; treating it as one would publish
        an empty private catalogue and call the run degraded-but-fine."""
        self._write_config()
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        self._run()

        cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        for value in cache["sources"].values():
            value["items"] = []
            value["series_items"] = []
        self.cache_path.write_text(json.dumps(cache), encoding="utf-8")

        self._fail()
        with self.assertRaises(RuntimeError):
            self._run()

    def test_a_degraded_run_never_empties_the_cache_it_is_reading(self) -> None:
        """Two failures in a row must be as survivable as one."""
        self._write_config()
        self._serve([("Bangla_Movies/bangla.txt", MOVIE_TXT)])
        self._run()

        self._fail()
        first, _ = self._run()
        second, _ = self._run()
        self.assertEqual(
            [item["name"] for item in first], [item["name"] for item in second])


class ConfiguredPolicyTests(unittest.TestCase):
    """The shipped config has to actually turn the fallback on."""

    CONFIG = Path(__file__).resolve().parents[1] / "manual" / "movie-sources.json"

    def setUp(self) -> None:
        self.payload = json.loads(self.CONFIG.read_text(encoding="utf-8"))

    def test_the_last_valid_cache_is_enabled_at_both_levels(self) -> None:
        """S-03's evidence was `use_last_valid_cache: false` in two places.
        The per-source one is the one that gates `allow_cache`."""
        self.assertIsNot(self.payload.get("use_last_valid_cache"), False)
        for source in self.payload["repository_sources"]:
            self.assertIs(
                source.get("use_last_valid_cache"), True,
                msg=f"{source.get('id')} would refuse its own snapshot",
            )

    def test_a_local_checkout_path_is_configured_as_a_fallback(self) -> None:
        """S-03's third piece of evidence was `directory_sources: []`."""
        directories = self.payload.get("directory_sources") or []
        self.assertTrue(directories, "no local checkout path is configured")
        repository_ids = {
            source["id"] for source in self.payload["repository_sources"]}
        fallbacks = {
            source.get("fallback_for") for source in directories
            if source.get("enabled") is not False
        }
        self.assertTrue(
            fallbacks & repository_ids,
            msg="no directory source is a fallback for any repository",
        )

    def test_the_checkout_path_is_the_one_git_already_ignores(self) -> None:
        """`.gitignore` reserved `working/private-movie-source/` for exactly
        this; pointing somewhere else would commit the private catalogue."""
        ignored = (self.CONFIG.parents[1] / ".gitignore").read_text(
            encoding="utf-8")
        for source in self.payload.get("directory_sources") or ():
            path = str(source.get("path") or "")
            self.assertIn(
                path.rstrip("/") + "/", ignored,
                msg=f"{path} is not git-ignored",
            )

    def test_require_fresh_is_still_on(self) -> None:
        """It now means "never publish nothing" rather than "never publish",
        and turning it off would give up the empty-catalogue guard for free."""
        for source in self.payload["repository_sources"]:
            self.assertIsNot(source.get("require_fresh"), False)


if __name__ == "__main__":
    unittest.main()
