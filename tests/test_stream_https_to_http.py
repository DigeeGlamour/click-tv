"""A source whose HTTPS is broken can declare its streams be used over HTTP."""
import unittest

from scanner.source_loader import parse_source_content

PLAYLIST = """#EXTM3U
#EXTINF:-1 group-title="bangla-channel",ATN Bangla
#EXTVLCOPT:http-referrer=https://www.jagobd.com/
https://static.jagobd.com.bd/x/atnbd.stream/playlist.m3u8?wmsAuthSign=
"""


class StreamHttpsToHttpTests(unittest.TestCase):
    def test_declared_source_gets_http_streams(self):
        items, _ = parse_source_content(PLAYLIST, {"id": "sm-jago-bd", "format": "m3u",
                                                   "stream_https_to_http": True})
        self.assertTrue(items[0]["url"].startswith("http://static.jagobd.com.bd/"))

    def test_other_sources_are_untouched(self):
        items, _ = parse_source_content(PLAYLIST, {"id": "other", "format": "m3u"})
        self.assertTrue(items[0]["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
