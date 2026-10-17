"""A 404 recorded on the new hostname explains the stream on the old one.

The movies run of 2026-10-17 06:12 is the measurement. It passed the test
suite, ran the scanner, built a catalogue of 2,528 movies and 1,524
episodes - and the no-loss gate blocked the publish:

    INVARIANT 2 - live preservation
      previously live              3081
      currently visible             711
      merged                        275
      visible pending              1275
      terminal evidence             427
      UNEXPLAINED LIVE LOSS         393

    no-loss gate: 27962 confirmed 404/410 link(s)
    source removals: 576 of 723 past the retention grace with no alternative

Two faults, one cause - `sm-movie-combined` renames its CDN host roughly
weekly, and the published catalogue sat on `jrtyh.b-cdn.net` while the feed
served every one of those files from `dertg.b-cdn.net`:

1.  The run probes the URLs the FEED offers, so a confirmed 404 is recorded
    under the NEW host. The stream that went missing is the OLD host. Same
    path, same file, same answer, and the terminal layer did not match them -
    although the "is it still visible" layers above it already use
    `path_family` for exactly this reason.

2.  `movie_source_removal` answered "no other source has it" with a host+path
    comparison, so a file the feed was still listing looked deleted and was
    given `source_removed_with_no_alternative` - one of the four DEFINITIVE
    reject reasons, and the one mistake that module must not make.

Checked against the live feed on 2026-10-17: of the 25 lost streams the gate
sampled, the old rule found 0 still offered and the file-path rule found 25.

Neither change weakens the gate. The evidence is still a 404 or a 410 that
some vantage actually received - never a 403, never a timeout - and a path
that does not name a file is refused, because `/live` is served by thousands
of unrelated hosts.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_coverage as mc  # noqa: E402
from scanner import movie_source_removal as msr  # noqa: E402

PATH = "/s2/upload/videos/2024/10/[Fibwatch.Com]No.Mercy.2010.1080p.BluRay.mkv"
OLD = f"https://jrtyh.b-cdn.net{PATH}"
NEW = f"https://dertg.b-cdn.net{PATH}"
EMPTY_INDEX = {"by_family": {}, "by_path": {}, "by_card_id": {},
               "by_content": {}, "pending_content": {}}


def _preserve(previous, terminal):
    return mc.live_preservation(previous, [], dict(EMPTY_INDEX), terminal)


class ATerminalVerdictFollowsTheFile(unittest.TestCase):
    def test_a_404_on_the_new_host_explains_the_old_one(self):
        out = _preserve([f"movie|mix|no-mercy|{OLD}"],
                        {mc.stream_family(NEW): "confirmed_404_or_410"})
        self.assertEqual(out["terminal_evidence"], 1)
        self.assertEqual(out["unexplained_live_loss"], 0)

    def test_the_layer_is_named_so_the_report_can_say_so(self):
        out = _preserve([f"movie|mix|no-mercy|{OLD}"],
                        {mc.stream_family(NEW): "confirmed_404_or_410"})
        self.assertEqual(out["match_layers"]["terminal_path_rotated"], 1)

    def test_an_exact_match_is_still_preferred_and_not_double_counted(self):
        out = _preserve([f"movie|mix|no-mercy|{OLD}"],
                        {mc.stream_family(OLD): "confirmed_404_or_410"})
        self.assertEqual(out["terminal_evidence"], 1)
        self.assertEqual(dict(out["match_layers"]).get("terminal_path_rotated", 0), 0)

    def test_a_stream_nobody_has_an_answer_for_is_still_a_loss(self):
        out = _preserve([f"movie|mix|no-mercy|{OLD}"], {})
        self.assertEqual(out["unexplained_live_loss"], 1)

    def test_a_different_file_on_the_new_host_explains_nothing(self):
        other = "https://dertg.b-cdn.net/s2/upload/videos/2024/10/Something.Else.mkv"
        out = _preserve([f"movie|mix|no-mercy|{OLD}"],
                        {mc.stream_family(other): "confirmed_404_or_410"})
        self.assertEqual(out["unexplained_live_loss"], 1)


class AGenericPathProvesNothing(unittest.TestCase):
    def test_live_does_not_match_live(self):
        out = _preserve(["movie|mix|x|https://a.example/live"],
                        {mc.stream_family("https://b.example/live"): "confirmed_404_or_410"})
        self.assertEqual(out["unexplained_live_loss"], 1)
        self.assertEqual(out["terminal_evidence"], 0)

    def test_a_bare_index_manifest_does_not_match(self):
        out = _preserve(["movie|mix|x|https://a.example/hls/index.m3u8"],
                        {mc.stream_family("https://b.example/hls/index.m3u8"):
                         "confirmed_404_or_410"})
        self.assertEqual(out["unexplained_live_loss"], 1)

    def test_the_rule_is_spelled_out_once(self):
        self.assertTrue(mc._specific_path(PATH))
        self.assertFalse(mc._specific_path("/live"))
        self.assertFalse(mc._specific_path(""))


class ARemovalIsRefusedWhenTheSourceStillListsTheFile(unittest.TestCase):
    """`source_removed_with_no_alternative` is DEFINITIVE. Emitting it for a
    file the feed is still offering is the one mistake that must not happen."""

    ENTRY = {"complete_misses": 99, "content_id": "no-mercy", "urls": [OLD]}

    def _decide(self, **kw):
        base = dict(published_families=set(), published_card_ids=set(),
                    live_families=set(), source_families=set(), grace_scans=3)
        base.update(kw)
        return msr.decide(dict(self.ENTRY), **base)

    def test_without_the_path_it_was_called_terminal(self):
        families, refusal = self._decide()
        self.assertEqual(refusal, "")
        self.assertEqual(families, [mc.stream_family(OLD)])

    def test_the_same_file_on_another_host_refuses_it(self):
        families, refusal = self._decide(source_paths={msr._path(NEW)})
        self.assertEqual(refusal, msr.SOURCE_MOVED_IT)
        self.assertEqual(families, [])

    def test_an_exact_host_match_still_reports_its_own_reason(self):
        _families, refusal = self._decide(source_families={mc.stream_family(OLD)})
        self.assertEqual(refusal, msr.ANOTHER_SOURCE_HAS_IT)

    def test_a_generic_path_cannot_refuse_a_removal(self):
        entry = {"complete_misses": 99, "content_id": "x",
                 "urls": ["https://a.example/live"]}
        _families, refusal = msr.decide(
            entry, published_families=set(), published_card_ids=set(),
            live_families=set(), source_families=set(), grace_scans=3,
            source_paths={"/live"})
        self.assertEqual(refusal, "")

    def test_the_path_check_is_asked_last(self):
        """Weaker evidence than any exact test, and used only to refuse."""
        source = (ROOT / "scanner" / "movie_source_removal.py").read_text(encoding="utf-8")
        body = source[source.index("    for family in families:"):]
        body = body[:body.index("return families,")]
        self.assertLess(body.index("ANOTHER_SOURCE_HAS_IT"), body.index("SOURCE_MOVED_IT"))

    def test_the_grace_still_comes_first(self):
        entry = dict(self.ENTRY, complete_misses=1)
        _families, refusal = msr.decide(
            entry, published_families=set(), published_card_ids=set(),
            live_families=set(), source_families=set(), grace_scans=3,
            source_paths={msr._path(NEW)})
        self.assertEqual(refusal, msr.WITHIN_GRACE)


class TheCollectorFeedsItTheFeedsPaths(unittest.TestCase):
    def test_collect_builds_source_paths_from_the_raw_entries(self):
        source = (ROOT / "scanner" / "movie_source_removal.py").read_text(encoding="utf-8")
        block = source[source.index("def collect("):]
        self.assertIn("source_paths={", block)
        self.assertIn("_path(entry.get(\"url\"))", block)

    def test_terminal_records_passes_it_down(self):
        source = (ROOT / "scanner" / "movie_source_removal.py").read_text(encoding="utf-8")
        block = source[source.index("def terminal_records("):]
        self.assertIn("source_paths=source_paths,", block)


if __name__ == "__main__":
    unittest.main()
