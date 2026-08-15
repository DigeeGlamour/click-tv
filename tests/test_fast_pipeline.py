import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scanner.fast_pipeline import run_fast_verification_pipeline


class _MediaHandler(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *_args):
        return

    def do_GET(self):
        self.__class__.hits.append(self.path)
        if self.path == "/movie.mp4":
            body = b"\x00\x00\x00\x18ftypisom" + (b"0" * 20000)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()


class FastPipelineTests(unittest.TestCase):
    def setUp(self):
        _MediaHandler.hits = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_target_success_skips_later_candidates(self):
        port = self.server.server_address[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for directory in ("working", "config", "state", "reports"):
                (root / directory).mkdir()

            settings = {
                "stream_timeout_seconds": 2,
                "verification_workers": 4,
                "verification": {
                    "workers": 4,
                    "timeout_seconds": 2,
                    "retry_attempts": 1,
                    "retry_delays_seconds": [0],
                    "probe_media_segments": True,
                    "maximum_hls_variant_probes": 1,
                    "media_sample_bytes": 4096,
                    "maximum_manifest_bytes": 65536,
                    "progress_interval": 50,
                },
                "network": {"verify_ssl": True},
                "pipeline": {
                    "global_workers": 4,
                    "minimum_global_inflight": 2,
                    "bd_workers": 2,
                    "per_host_limit": 2,
                    "time_budget_seconds": {"movies": 60},
                },
                "bd_verification": {"workers": 2},
            }
            (root / "config" / "settings.json").write_text(
                json.dumps(settings), encoding="utf-8"
            )

            urls = [
                f"http://127.0.0.1:{port}/movie.mp4",
                f"http://127.0.0.1:{port}/missing-2.mp4",
                f"http://127.0.0.1:{port}/missing-3.mp4",
            ]
            items = []
            for index, url in enumerate(urls):
                items.append(
                    {
                        "id": "same-movie",
                        "name": "Same Movie",
                        "url": url,
                        "source_pipeline": "movies",
                        "category": "English",
                        "resolution": "1080p",
                        "resolution_height": 1080,
                        "_verification_group": "movies:same-movie",
                        "_verification_rank": index,
                        "_verification_wave": 0 if index == 0 else index,
                        "_verification_target": 1,
                    }
                )
            (root / "working" / "candidates.json").write_text(
                json.dumps({"mode": "movies", "items": items}), encoding="utf-8"
            )

            result = run_fast_verification_pipeline(
                candidates_path=root / "working" / "candidates.json",
                settings_path=root / "config" / "settings.json",
                global_output_path=root / "working" / "global.json",
                bd_output_path=root / "working" / "bd.json",
                history_path=root / "state" / "history.json",
                protection_state_path=root / "state" / "protection.json",
                bd_report_path=root / "reports" / "bd.json",
                pipeline_report_path=root / "reports" / "pipeline.json",
                checkpoint_path=root / "working" / "checkpoint.json",
            )

        self.assertEqual(result["total_publishable"], 1)
        self.assertEqual(result["total_adaptive_skipped"], 2)
        self.assertNotIn("/missing-2.mp4", _MediaHandler.hits)


if __name__ == "__main__":
    unittest.main()
