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
        index_html = (Path(__file__).resolve().parents[1] / "site/index.html").read_text(encoding="utf-8")

        # Selected direct 4K stays selected; audio probing must not force FHD downgrade.
        self.assertIn("movie4kAudioTriedKeys", index_html)
        self.assertIn("অন্য ৪কে সংস্করণের অডিও চেষ্টা করা হচ্ছে", index_html)
        self.assertIn("এই ৪কে ফাইলে সমর্থিত অডিও নেই। অডিওর জন্য FHD বেছে নিন।", index_html)
        self.assertNotIn("decodedNothing", index_html)
        self.assertNotIn("অডিওসহ চালু হচ্ছে", index_html)

        # Live/event playback starts with a small buffer and later returns to automatic ABR.
        self.assertIn("LIVE_FAST_START_RAMP_MS = 8000", index_html)
        self.assertIn("liveFastStartProfile", index_html)
        self.assertIn("findFastStartHlsLevel", index_html)
        self.assertIn("scheduleLiveStartupRamp", index_html)
        self.assertIn("state.hls.loadLevel = -1", index_html)

        # User pause freezes live loading until the user resumes.
        self.assertIn("pausePlaybackByUser", index_html)
        self.assertIn("state.hls?.stopLoad()", index_html)
        self.assertIn("resumePlaybackByUser", index_html)
        self.assertIn("toggleUserPlayback", index_html)

        # Quality/network/speed popups close automatically and the redundant header is absent.
        self.assertIn("POPUP_AUTO_HIDE_MS = 3000", index_html)
        self.assertIn("schedulePopupAutoHide", index_html)
        self.assertNotIn("header.className = 'quality-menu-header'", index_html)

        # Requested mobile layout corrections.
        self.assertIn("2026-08 issue-only corrections", index_html)
        self.assertIn("height: 4px !important", index_html)
        self.assertIn("gap: 4px !important", index_html)
        self.assertIn("max-height: 158px !important", index_html)

        self.assertNotIn("✨ Auto", index_html)
        self.assertNotIn("📶 Stable", index_html)
        self.assertNotIn("⚡ Low Delay", index_html)


if __name__ == "__main__":
    unittest.main()
