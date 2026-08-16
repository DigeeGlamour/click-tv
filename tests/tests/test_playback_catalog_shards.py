"""The playback catalogue is sharded so the proxy Worker can afford a lookup.

Every manifest and every segment a live HLS player requests goes through
`/hls`, and each of those resolves one `playback_id` against the catalogue.
When the catalogue was a single file it had grown to ~17 MB / 21k records, so
the Worker re-fetched and re-parsed megabytes several times a second and ran
out of CPU mid-stream. It is now one file per id prefix under data/playback/,
which makes a lookup a ~70 KB read.

Three copies of the shard function have to agree or a lookup lands on the wrong
file and playback dies: the scanner writes with the Python one, the Pages
validator checks with its own copy (the scanner package is not importable from
a built dist/), and the Worker reads with the JavaScript one.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.playback_profiles import (  # noqa: E402
    PlaybackProfileCollector,
    catalog_shard_for,
    load_public_catalog_records,
    merge_public_catalog,
)

WORKER = ROOT / "workers" / "playback-proxy" / "src" / "index.js"
VALIDATOR = ROOT / "scripts" / "validate-pages.py"

SAMPLE_IDS = [
    "ctv_" + "0" * 32,
    "ctv_" + "f" * 32,
    "ctv_a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "ctv_00112233445566778899aabbccddeeff",
    "ctv_deadbeefdeadbeefdeadbeefdeadbeef",
]


def _profile(url: str) -> dict:
    return {
        "schema_version": 1,
        "status": "active",
        "url": url,
        "headers": {"Referer": "https://example.test/"},
        "drm": {},
        "stream_type": "hls",
        "header_profile": "android_tv",
        "inherit_manifest_query": False,
        "updated_at": "2026-08-16T00:00:00+00:00",
        "scan_mode": "test",
    }


class ShardKeyTests(unittest.TestCase):
    def test_shard_is_the_first_two_hex_characters_of_the_id(self):
        self.assertEqual(catalog_shard_for("ctv_a1b2c3d4e5f60718293a4b5c6d7e8f90"), "a1")
        self.assertEqual(catalog_shard_for("ctv_" + "0" * 32), "00")
        self.assertEqual(catalog_shard_for("ctv_" + "f" * 32), "ff")

    def test_a_malformed_id_still_resolves_to_a_real_shard(self):
        for bad in ("", "nonsense", "ctv_", "ctv_zz", None):
            self.assertRegex(catalog_shard_for(bad), r"^[0-9a-f]{2}$")

    def test_the_validator_copy_agrees_with_the_scanner(self):
        namespace: dict = {}
        source = VALIDATOR.read_text(encoding="utf-8")
        start = source.index("def catalog_shard_for")
        end = source.index('\n    return "00"', start) + len('\n    return "00"')
        exec(compile(source[start:end], "validate-pages", "exec"), namespace)
        validator_shard = namespace["catalog_shard_for"]
        for playback_id in SAMPLE_IDS + ["", "ctv_zz", "garbage"]:
            self.assertEqual(
                validator_shard(playback_id),
                catalog_shard_for(playback_id),
                playback_id,
            )

    def test_the_worker_copy_agrees_with_the_scanner(self):
        source = WORKER.read_text(encoding="utf-8")
        match = re.search(r"function catalogShardFor\(playbackId\) \{[\s\S]*?\n\}", source)
        self.assertIsNotNone(match, "catalogShardFor missing from the Worker")
        script = (
            match.group(0)
            + "\nconsole.log(JSON.stringify("
            + json.dumps(SAMPLE_IDS)
            + ".map(catalogShardFor)));"
        )
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.strip()),
            [catalog_shard_for(i) for i in SAMPLE_IDS],
        )


class CatalogWriteTests(unittest.TestCase):
    def _write(self, temp_dir: str, records: dict, scan_mode: str = "test") -> dict:
        collector = PlaybackProfileCollector(
            scan_mode=scan_mode, timestamp="2026-08-16T00:00:00+00:00"
        )
        collector.records.update(records)
        return merge_public_catalog(Path(temp_dir) / "data", collector)

    def test_records_land_in_the_shard_their_id_selects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data = Path(temp_dir) / "data"
            self._write(temp_dir, {i: _profile(f"https://x.test/{i}.m3u8") for i in SAMPLE_IDS})
            for playback_id in SAMPLE_IDS:
                shard_file = data / "playback" / f"{catalog_shard_for(playback_id)}.json"
                self.assertTrue(shard_file.is_file(), shard_file)
                payload = json.loads(shard_file.read_text(encoding="utf-8"))
                self.assertIn(playback_id, payload["records"])
                self.assertEqual(payload["count"], len(payload["records"]))

    def test_the_index_declares_the_shards_and_the_total(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = self._write(
                temp_dir, {i: _profile(f"https://x.test/{i}.m3u8") for i in SAMPLE_IDS}
            )
        self.assertEqual(index["schema_version"], 2)
        self.assertIs(index["sharded"], True)
        self.assertEqual(index["count"], len(SAMPLE_IDS))
        self.assertEqual(sum(index["shards"].values()), len(SAMPLE_IDS))
        self.assertNotIn("records", index)

    def test_the_index_stays_small_no_matter_how_many_records(self):
        """The whole point: the Worker's first read must not scale with the
        catalogue. A 21k-record catalogue previously meant a 17 MB parse.

        Real playback ids are md5 digests, so hash the counter rather than
        formatting it - sequential ids share a leading-zero prefix and would
        pile every record into one shard, which is not how production looks.
        """
        import hashlib

        with tempfile.TemporaryDirectory() as temp_dir:
            many = {
                "ctv_" + hashlib.md5(str(i).encode()).hexdigest():
                    _profile(f"https://x.test/{i}.m3u8")
                for i in range(4000)
            }
            self._write(temp_dir, many)
            index_bytes = (Path(temp_dir) / "data" / "playback-sources.json").stat().st_size
            shard_sizes = [
                p.stat().st_size
                for p in (Path(temp_dir) / "data" / "playback").glob("*.json")
            ]
        self.assertLess(index_bytes, 64 * 1024)
        self.assertLess(max(shard_sizes), 256 * 1024)

    def test_a_later_partial_scan_keeps_records_it_did_not_rescan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = SAMPLE_IDS[0]
            second = SAMPLE_IDS[1]
            self._write(temp_dir, {first: _profile("https://x.test/one.m3u8")}, "channels")
            self._write(temp_dir, {second: _profile("https://x.test/two.m3u8")}, "today")
            records = load_public_catalog_records(Path(temp_dir) / "data")
        self.assertIn(first, records)
        self.assertIn(second, records)

    def test_a_rescanned_record_is_replaced_not_duplicated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            playback_id = SAMPLE_IDS[0]
            self._write(temp_dir, {playback_id: _profile("https://x.test/old.m3u8")})
            index = self._write(temp_dir, {playback_id: _profile("https://x.test/new.m3u8")})
            records = load_public_catalog_records(Path(temp_dir) / "data")
        self.assertEqual(index["count"], 1)
        self.assertEqual(records[playback_id]["url"], "https://x.test/new.m3u8")

    def test_pre_shard_records_are_migrated_and_never_lost(self):
        """A repository that has only the old single file must survive the
        first sharded scan with every record intact."""
        with tempfile.TemporaryDirectory() as temp_dir:
            data = Path(temp_dir) / "data"
            data.mkdir(parents=True)
            legacy_id = SAMPLE_IDS[2]
            (data / "playback-sources.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "count": 1,
                    "records": {legacy_id: _profile("https://x.test/legacy.m3u8")},
                }),
                encoding="utf-8",
            )
            self._write(temp_dir, {SAMPLE_IDS[3]: _profile("https://x.test/fresh.m3u8")})
            records = load_public_catalog_records(data)
        self.assertIn(legacy_id, records)
        self.assertIn(SAMPLE_IDS[3], records)


class WorkerContractTests(unittest.TestCase):
    def _worker(self) -> str:
        return WORKER.read_text(encoding="utf-8")

    def test_the_catalogue_cache_outlives_a_live_manifest_refresh(self):
        source = self._worker()
        match = re.search(r"const CATALOG_CACHE_MS = ([^;]+);", source)
        self.assertIsNotNone(match)
        result = subprocess.run(
            ["node", "-e", f"console.log({match.group(1)})"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertGreaterEqual(int(result.stdout.strip()), 5 * 60 * 1000)

    def test_the_worker_reads_a_shard_not_the_whole_catalogue(self):
        source = self._worker()
        self.assertIn("CATALOG_SHARD_URL", source)
        self.assertIn("/data/playback/", source)

    def test_the_pre_shard_layout_is_still_readable(self):
        """Lets the Worker be deployed before the first sharded scan lands,
        so the rollout has no window where nothing plays."""
        self.assertIn("loadLegacyCatalogRecords", self._worker())

    def test_every_serving_origin_is_allowed_and_lookalikes_are_not(self):
        source = self._worker()
        start = source.index("const DEFAULT_VERSION")
        end = source.index("const ALLOWED_HOSTS_URL")
        script = (
            source[start:end]
            + "\nconsole.log(JSON.stringify(["
            + "'https://clicktv.pages.dev','https://www.clicktv.pages.dev',"
            + "'https://preview1.clicktv.pages.dev','http://localhost:8000',"
            + "'https://clicktv.pages.dev.evil.com','https://evil.example.com',"
            + "'http://clicktv.pages.dev'"
            + "].map(isAllowedOrigin)));"
        )
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout.strip()),
            [True, True, True, True, False, False, False],
        )


if __name__ == "__main__":
    unittest.main()
