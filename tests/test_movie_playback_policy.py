from __future__ import annotations

import unittest
from pathlib import Path

from scanner.movies import paginate_movie_list


class MoviePlaybackPolicyTests(unittest.TestCase):
    def test_every_published_movie_and_backup_is_direct_first(self) -> None:
        movies = [
            {
                "id": "discovered-proxy-first",
                "name": "Discovered Movie",
                "year": 2026,
                "url": "https://example.com/movie-1080p.mkv",
                "proxy_mode": "proxy_first",
                "force_proxy": True,
                "backups": [
                    {
                        "url": "https://example.com/movie-4k.mkv",
                        "proxy_mode": "proxy_only",
                        "force_proxy": True,
                    }
                ],
            }
        ]
        payload = paginate_movie_list(movies, "English", page_size=100)
        item = payload["page_contents"]["page-001.json"]["items"][0]
        self.assertEqual(item["proxy_mode"], "direct_first")
        self.assertFalse(item["force_proxy"])
        self.assertFalse(item["proxy_required"])
        self.assertEqual(item["backups"][0]["proxy_mode"], "direct_first")
        self.assertFalse(item["backups"][0]["force_proxy"])
        self.assertFalse(item["backups"][0]["proxy_required"])

    def test_modular_frontend_contains_latest_series_and_live_guards(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app = (root / "site/assets/js/app.js").read_text(encoding="utf-8")
        index = (root / "site/index.html").read_text(encoding="utf-8")
        css = (root / "site/assets/css/app.css").read_text(encoding="utf-8")

        self.assertIn("['Premium', 'premium']", app)
        self.assertIn("SERIES_ASSET_VERSION = '20260806-manual-series-live-fix6'", app)
        self.assertIn("function ensureSeriesModule()", app)
        self.assertIn("startLiveAdaptiveQualityRamp", app)
        self.assertIn("LIVE_FAST_START_RAMP_MS = 4500", app)
        self.assertIn("LIVE_CHANNEL_STALL_FAILOVER_MS = 11000", app)
        self.assertIn("abrBandWidthFactor", app)
        self.assertIn("abrBandWidthUpFactor", app)
        self.assertIn("protectLivePlaybackDuringFullscreenTransition", app)
        self.assertIn("window.ClickTvSeries?.handleEnded?.()", app)

        # Existing Movie 4K system remains present.
        self.assertIn("directMovieQualityGroups", app)
        self.assertIn("show4KAvailabilityReminder", app)
        self.assertIn("startMovieAudioCompanion", app)
        self.assertIn("selectDirectMovieQuality", app)

        self.assertIn("20260806-manual-series-live-fix6", index)
        self.assertIn("qualityAvailabilityBadge", index)
        self.assertIn("fsDrawerToggle", index)
        self.assertIn(".fs-drawer-toggle", css)
        self.assertIn(".quality-availability-note", css)


if __name__ == "__main__":
    unittest.main()
