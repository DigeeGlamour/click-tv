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

    def test_frontend_contains_only_requested_player_corrections(self) -> None:
        root = Path(__file__).resolve().parents[1]
        index_html = (root / "site/index.html").read_text(encoding="utf-8")
        app_js = (root / "site/assets/js/app.js").read_text(encoding="utf-8")
        final_css = (root / "site/assets/css/final-design.css").read_text(encoding="utf-8")

        # The production frontend is modular. The HTML must load one coherent
        # version of the complete CSS/JS bundle rather than relying on inline code.
        self.assertIn("assets/css/app.css?v=20260806-complete-fix-v3", index_html)
        self.assertIn("assets/css/series.css?v=20260806-complete-fix-v3", index_html)
        self.assertIn("assets/css/final-design.css?v=20260806-complete-fix-v3", index_html)
        self.assertIn("assets/js/series.js?v=20260806-complete-fix-v3", index_html)
        self.assertIn("assets/js/app.js?v=20260806-complete-fix-v3", index_html)

        # Selected direct 4K remains selected while browser-compatible companion
        # audio or an alternate 4K source is attempted. FHD is not forced silently.
        self.assertIn("movie4kAudioBlockedQualityKeys", app_js)
        self.assertIn("movie4kAudioBlockedSourceTokens", app_js)
        self.assertIn("alternate source চেষ্টা করা হচ্ছে", app_js)
        self.assertIn("4K video চালু রাখা হয়েছে", app_js)
        self.assertNotIn("decodedNothing", app_js)
        self.assertNotIn("অডিওসহ চালু হচ্ছে", app_js)

        # Live/event playback starts at a conservative level, then returns to ABR.
        self.assertIn("LIVE_FAST_START_RAMP_MS = 6000", app_js)
        self.assertIn("liveFastStartProfile", app_js)
        self.assertIn("startLiveFastStartPhase", app_js)
        self.assertIn("scheduleLiveStartupRamp", app_js)
        self.assertIn("state.hls.loadLevel = -1", app_js)

        # User pause stops live loading, and user resume restarts it.
        self.assertIn("state.userPaused = true", app_js)
        self.assertIn("state.hls?.stopLoad()", app_js)
        self.assertIn("state.userPaused = false", app_js)
        self.assertIn("state.hls?.startLoad(-1)", app_js)
        self.assertIn("resumeVideoSafely('play button'", app_js)

        # Popups close automatically and the locked final visual layer is present.
        self.assertIn("popupAutoCloseTimer = setTimeout(hideAllPopups, 3000)", app_js)
        self.assertIn("CLICKTV_FINAL_VISUAL_LOCK_20260806_V3", final_css)
        self.assertIn("--ct-panel", final_css)
        self.assertNotIn("header.className = 'quality-menu-header'", app_js)

        self.assertNotIn("✨ Auto", app_js)
        self.assertNotIn("📶 Stable", app_js)
        self.assertNotIn("⚡ Low Delay", app_js)



if __name__ == "__main__":
    unittest.main()
