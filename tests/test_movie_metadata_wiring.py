"""PART 03: the metadata-lookup network gate inside scanner/movies.py itself.

scanner/movie_metadata_cache.py and scanner/metadata_providers.py are each
tested in isolation already (tests/test_movie_metadata_cache.py,
tests/test_metadata_providers.py). What is not covered there is the actual
wiring point in scanner/movies.py: paginate_movie_list() must only ever
allow a real provider network call on the true publish path
(retain_recent_dropouts=True) - the same rule movie_retention.py already
follows - so that calling it directly (as every other test in this suite,
and any future ad-hoc/manual invocation, does) can never reach the network.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_metadata_cache as mc  # noqa: E402
from scanner import movie_recency as mr  # noqa: E402
from scanner import movie_retention as mrt  # noqa: E402
from scanner import movies as M  # noqa: E402


class MetadataLookupGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)

        self._original_recency_path = mr.DEFAULT_PATH
        self._original_retention_path = mrt.DEFAULT_PATH
        self._original_retention_root = mrt.MOVIES_ROOT
        self._original_metadata_path = mc.DEFAULT_PATH

        mr.DEFAULT_PATH = str(tmp_path / "seen.json")
        mrt.DEFAULT_PATH = str(tmp_path / "ret.json")
        mrt.MOVIES_ROOT = str(tmp_path / "movies")
        mc.DEFAULT_PATH = str(tmp_path / "movie-metadata-cache.json")

        self.addCleanup(self._restore)

    def _restore(self):
        mr.DEFAULT_PATH = self._original_recency_path
        mrt.DEFAULT_PATH = self._original_retention_path
        mrt.MOVIES_ROOT = self._original_retention_root
        mc.DEFAULT_PATH = self._original_metadata_path

    def _movie(self, name):
        return {"name": name, "url": "https://x.example.net/f.mp4"}

    def test_ad_hoc_call_never_reaches_the_network(self):
        with patch("scanner.metadata_providers.resolve_metadata") as resolve:
            M.paginate_movie_list([self._movie("Some Film (2026)")], "Mix", page_size=5)
        resolve.assert_not_called()

    def test_real_publish_path_is_allowed_to_call_the_resolver(self):
        with patch("scanner.metadata_providers.resolve_metadata", return_value=None) as resolve:
            M.paginate_movie_list(
                [self._movie("Some Film (2026)")],
                "Mix",
                page_size=5,
                retain_recent_dropouts=True,
            )
        resolve.assert_called_once()

    def test_a_broken_provider_module_does_not_fail_the_scan(self):
        """_annotate_metadata's own try/except must swallow an import or
        provider crash exactly like _annotate_recency already does."""
        with patch(
            "scanner.metadata_providers.resolve_metadata", side_effect=RuntimeError("boom")
        ):
            result = M.paginate_movie_list(
                [self._movie("Some Film (2026)")],
                "Mix",
                page_size=5,
                retain_recent_dropouts=True,
            )
        self.assertEqual(result["index"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
