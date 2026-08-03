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

    def test_frontend_contains_requested_quality_and_audio_behavior(self) -> None:
        index_html = (Path(__file__).resolve().parents[1] / "site/index.html").read_text(encoding="utf-8")

        self.assertIn("qualityAvailabilityBadge", index_html)
        self.assertIn("৪কে আছে, কোয়ালিটি থেকে বেছে নিন", index_html)
        self.assertIn("setTimeout(() => show4KAvailabilityReminder(item), 10000)", index_html)
        self.assertIn("setInterval(() => show4KAvailabilityReminder(item), 5 * 60 * 1000)", index_html)
        self.assertIn("setTimeout(hide4KAvailabilityReminder, 45000)", index_html)

        self.assertIn("scheduleMovieAudioCompatibilityCheck", index_html)
        self.assertIn("movie4kAudioBlockedQualityKeys", index_html)
        self.assertIn("অডিওসহ চালু হচ্ছে", index_html)
        self.assertIn("selectedSources.slice(0, 6)", index_html)

        self.assertIn("quality-menu-channel", index_html)
        self.assertIn("quality-menu-movie", index_html)
        self.assertIn("formatCompactElapsedTime", index_html)
        self.assertIn("mode = mixedContent ? 'proxy_only' : 'direct_first';", index_html)

        self.assertNotIn("✨ Auto", index_html)
        self.assertNotIn("📶 Stable", index_html)
        self.assertNotIn("⚡ Low Delay", index_html)


if __name__ == "__main__":
    unittest.main()
