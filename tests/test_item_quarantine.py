"""One broken card is held back; the catalogue around it still publishes.

Built against a small but real data/ tree and the validator's real item
rules, so "removed" means the validator would have failed the build on it.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "quarantine_bad_items", ROOT / "scripts" / "quarantine-bad-items.py")
Q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(Q)


def _channel(identifier, name, **extra):
    card = {"id": identifier, "name": name, "category": "Bangla",
            "url": f"https://{identifier}.example/live.m3u8",
            "resolution": "1080p", "resolution_height": 1080}
    card.update(extra)
    return card


def _movie(identifier, name, year=2024):
    return {"id": identifier, "name": name, "year": year, "category": "Hindi",
            "url": f"https://{identifier}.example/film.mp4",
            "resolution": "1080p", "resolution_height": 1080}


def _tree(channels, movie_pages):
    data = Path(tempfile.mkdtemp()) / "data"
    (data / "channels").mkdir(parents=True)
    (data / "movies" / "hindi").mkdir(parents=True)
    (data / "playback-sources.json").write_text(
        json.dumps({"schema_version": 1, "sources": []}), encoding="utf-8")
    (data / "channels" / "bangla.json").write_text(
        json.dumps({"count": len(channels), "channels": channels}), encoding="utf-8")
    pages = []
    total = 0
    for number, items in enumerate(movie_pages, start=1):
        (data / "movies" / "hindi" / f"page-{number:03d}.json").write_text(json.dumps(
            {"page": number, "count": len(items), "total_count": 0, "items": items}),
            encoding="utf-8")
        pages.append({"page": number, "count": len(items)})
        total += len(items)
    (data / "movies" / "hindi" / "index.json").write_text(json.dumps(
        {"slug": "hindi", "count": total, "total_pages": len(pages), "pages": pages}),
        encoding="utf-8")
    (data / "manifest.json").write_text(json.dumps({
        "channels": {"Bangla": {"url": "data/channels/bangla.json", "count": len(channels)}},
        "movies": {"Hindi": {"url": "data/movies/hindi/index.json", "count": total,
                             "total_pages": len(pages)}},
    }), encoding="utf-8")
    return data


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class OnlyTheBrokenCardGoes(unittest.TestCase):
    def test_a_nameless_channel_is_removed_and_its_neighbours_stay(self):
        good = [_channel(f"c{i}", f"Channel {i}") for i in range(20)]
        data = _tree(good[:10] + [_channel("broken", "")] + good[10:], [])
        code = Q.run(data, report_path=None)
        self.assertEqual(code, 0)
        written = _read(data / "channels" / "bangla.json")
        self.assertEqual([c["id"] for c in written["channels"]], [c["id"] for c in good])
        self.assertEqual(written["count"], 20)
        self.assertEqual(_read(data / "manifest.json")["channels"]["Bangla"]["count"], 20)

    def test_a_later_duplicate_film_is_removed_across_pages_and_counts_follow(self):
        page_one = [_movie(f"m{i}", f"Film {i}") for i in range(10)]
        page_two = [_movie("m3-again", "Film 3")] + [_movie(f"n{i}", f"Other {i}") for i in range(10)]
        data = _tree([_channel("c0", "Channel 0")], [page_one, page_two])
        self.assertEqual(Q.run(data, report_path=None), 0)
        second = _read(data / "movies" / "hindi" / "page-002.json")
        self.assertNotIn("m3-again", [m["id"] for m in second["items"]])
        self.assertEqual(second["count"], 10)
        index = _read(data / "movies" / "hindi" / "index.json")
        self.assertEqual(index["count"], 20)
        self.assertEqual(index["pages"][1]["count"], 10)
        self.assertEqual(_read(data / "manifest.json")["movies"]["Hindi"]["count"], 20)

    def test_every_removal_is_reported_with_the_validators_reason(self):
        data = _tree([_channel("c0", "Channel 0"), _channel("broken", "")], [])
        report = data.parent / "report.json"
        Q.run(data, report_path=report)
        written = _read(report)
        self.assertEqual(len(written["removed"]), 1)
        self.assertEqual(written["removed"][0]["id"], "broken")
        self.assertTrue(any("name missing" in r for r in written["removed"][0]["reasons"]))
        self.assertTrue(written["applied"])

    def test_a_clean_catalogue_is_not_touched(self):
        data = _tree([_channel(f"c{i}", f"Channel {i}") for i in range(5)], [])
        before = (data / "channels" / "bangla.json").read_bytes()
        self.assertEqual(Q.run(data, report_path=None), 0)
        self.assertEqual((data / "channels" / "bangla.json").read_bytes(), before)


class ItWillNotHollowOutACatalogue(unittest.TestCase):
    def test_many_failures_are_a_scan_fault_and_change_nothing(self):
        broken = [_channel(f"b{i}", "") for i in range(Q.MIN_ALLOWANCE + 5)]
        data = _tree([_channel("c0", "Channel 0")] + broken, [])
        before = (data / "channels" / "bangla.json").read_bytes()
        self.assertEqual(Q.run(data, report_path=None), 2)
        self.assertEqual((data / "channels" / "bangla.json").read_bytes(), before)


class TheBuildRunsIt(unittest.TestCase):
    def test_build_pages_screens_before_copying_data(self):
        build = (ROOT / "scripts" / "build-pages.sh").read_text(encoding="utf-8")
        self.assertTrue("quarantine-bad-items.py" in build, "the build does not screen items")
        self.assertLess(build.index("quarantine-bad-items.py"),
                        build.index('cp -a "${DATA_DIR}/." "${DIST_DIR}/data/"'))


if __name__ == "__main__":
    unittest.main()
