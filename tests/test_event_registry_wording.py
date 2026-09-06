"""PROMPT 40 - the registry has twenty-one feeds and the prose said eleven.

`config/sources/upcoming.json` explained itself with "All eleven event feeds
are registered once in today-match.json". Eleven was true when it was written.
Twenty-one are registered now, and a reader who trusted that sentence would go
looking for ten feeds that were never missing.

Two different faults share the words, and they are fixed differently:

  * a claim about the registry *now* is rewritten with no number at all, so
    twenty-one cannot go stale the way eleven did - the registry is the count;
  * a dated measurement ("the 442 records the eleven feeds served on
    2026-08-20") was true and stays, qualified so it reads as history.

Documentation only. No source is added, removed, enabled or disabled, and the
empty `sources` list that makes this file work stays empty.
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
UPCOMING = ROOT / "config" / "sources" / "upcoming.json"
TODAY = ROOT / "config" / "sources" / "today-match.json"

#: Prose that describes the registry as it is now. A dated measurement is a
#: different thing and is allowed to name the count it measured.
PRESENT_TENSE_CLAIMS = (
    "eleven sources are registered",
    "eleven event feeds are registered",
    "all eleven event feeds",
    "the eleven feeds serve today",
    "eleven feeds actually use",
)


def _sources():
    return [ROOT / "scan.py"] + sorted(
        list((ROOT / "scanner").rglob("*.py"))
        + list((ROOT / "config").rglob("*.json")))


class NoLiveClaimNamesAStaleCount(unittest.TestCase):
    def test_nothing_still_says_the_registry_holds_eleven_feeds(self):
        for path in _sources():
            text = path.read_text(encoding="utf-8").casefold()
            for claim in PRESENT_TENSE_CLAIMS:
                with self.subTest(file=path.name, claim=claim):
                    self.assertNotIn(claim, text)

    def test_the_upcoming_file_explains_itself_without_a_count(self):
        payload = json.loads(UPCOMING.read_text(encoding="utf-8"))
        description = payload["description"].casefold()
        self.assertIn("registered once in today-match.json", description)
        self.assertIn("registry", description)
        for number in ("eleven", "twenty-one", " 11 ", " 21 "):
            self.assertNotIn(number, description)

    def test_every_surviving_eleven_is_dated_history(self):
        """A measurement may name what it measured, as long as a reader cannot
        mistake it for the registry today."""
        # Only the registry sense of the word. "eleven of them can fail" about
        # routes, or eleven fixtures in a ladder, is a different eleven.
        pattern = re.compile(r"eleven[^.\n]{0,40}?(?:feeds?|sources?)",
                             re.IGNORECASE)
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            for line in pattern.findall(text):
                context = text[max(0, text.index(line) - 220):
                               text.index(line) + 220].casefold()
                with self.subTest(file=path.name, phrase=line.strip()[:60]):
                    self.assertTrue(
                        "2026-08" in context or "registered then" in context
                        or "registered at that date" in context
                        or "registered in 2026-08" in context,
                        "undated 'eleven' in %s: %s" % (path.name, line.strip()),
                    )


class NothingAboutTheScanChanged(unittest.TestCase):
    def test_the_upcoming_pipeline_is_still_deliberately_empty(self):
        payload = json.loads(UPCOMING.read_text(encoding="utf-8"))
        self.assertEqual("upcoming", payload["pipeline"])
        self.assertEqual([], payload["sources"])

    def test_the_registry_itself_is_untouched_and_still_loads(self):
        from scanner.source_coverage import load_configured_sources

        configured = load_configured_sources(ROOT / "config")
        declared = [entry for entry in
                    json.loads(TODAY.read_text(encoding="utf-8"))["sources"]
                    if entry.get("enabled") is not False]
        self.assertEqual(len(declared), len(configured))
        self.assertEqual([entry["id"] for entry in declared],
                         [entry["id"] for entry in configured])

    def test_the_count_is_read_from_the_registry_not_written_down(self):
        source = (ROOT / "scanner" / "source_coverage.py").read_text(
            encoding="utf-8")
        docstring = source.split("def load_configured_sources", 1)[1][:1500]
        self.assertIn("as many rows as the registry has enabled sources",
                      docstring)


if __name__ == "__main__":
    unittest.main()
