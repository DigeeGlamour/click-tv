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

    def test_modular_frontend_preserves_player_mechanisms_and_final_design(self) -> None:
        root = Path(__file__).resolve().parents[1]
        index_html = (root / "site/index.html").read_text(encoding="utf-8")
        app_js = (root / "site/assets/js/app.js").read_text(encoding="utf-8")
        final_css = (root / "site/assets/css/final-design.css").read_text(encoding="utf-8")
        series_js = (root / "site/assets/js/series.js").read_text(encoding="utf-8")

        # Modular design files are loaded explicitly and the approved design is locked.
        self.assertIn("assets/css/final-design.css", index_html)
        self.assertIn("assets/js/series.js", index_html)
        self.assertIn("assets/js/app.js", index_html)
        self.assertIn("Click TV Final Design Lock", final_css)
        self.assertIn("mobile main category text is slightly larger", final_css)

        # Existing playback routing and fallback mechanisms remain in app.js.
        for marker in (
            "direct_first",
            "buildAttemptPlan",
            "failCurrentAttempt",
            "initHls",
            "initShaka",
            "initMpegTs",
            "tryLiveNetworkRecovery",
            "protectLivePlaybackDuringFullscreenTransition",
        ):
            self.assertIn(marker, app_js)

        # Fast startup, staged live quality ramp and eventual Auto/ABR release remain.
        for marker in (
            "startLiveStartupBufferGate",
            "liveStartupQualityStages",
            "startLiveAdaptiveQualityRamp",
            "applyLiveAdaptiveQualityCap",
            "state.hls.loadLevel = -1",
        ):
            self.assertIn(marker, app_js)

        # 4K reminder, alternate audio and companion-audio mechanisms remain.
        for marker in (
            "schedule4KAvailabilityNotice",
            "movieAudioCompanionCandidate",
            "fallbackFromUnsupported4KAudio",
            "Compatible 4K audio source",
        ):
            self.assertIn(marker, app_js)

        # User pause stops live loading; resume restarts it without losing the route.
        self.assertIn("state.hls?.stopLoad()", app_js)
        self.assertIn("state.hls?.startLoad(-1)", app_js)
        self.assertIn("state.userPaused", app_js)

        # Popup auto-close and Series/Season/Episode mechanisms remain modular.
        self.assertIn("setTimeout(hideAllPopups, 3000)", app_js)
        for marker in (
            "SERIES_MANIFEST_URL",
            "loadSeason",
            "playRelativeEpisode",
            "populateFullscreenDrawer",
            "NEXT_EPISODE_SECONDS = 8",
        ):
            self.assertIn(marker, series_js)

        self.assertNotIn("✨ Auto", app_js)
        self.assertNotIn("📶 Stable", app_js)
        self.assertNotIn("⚡ Low Delay", app_js)


if __name__ == "__main__":
    unittest.main()
