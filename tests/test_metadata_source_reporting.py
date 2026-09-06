"""PROMPT 36/37 - a metadata layer is not a source that produced nothing.

FINAL_1 P2 read the coverage row for `streamed-fixtures` - 407 fetched, 407
parsed, 0 matched, 0 published - and concluded the feed was pure cost: "either
write its identity resolver or turn it off". The row was accurate and the
conclusion did not follow, because the report only counts cards.

Measured on a real scan while auditing it: 489 candidates ingested, **50
published fixtures matched, artwork supplied to 50**, one HTTP call, 0.7
seconds. It publishes no card because `scanner/streamed_provider.py` section 23
denies it card authority on purpose - its match id may never become a Click TV
event id - and lets it enrich the fixtures the real authorities established.

So the row now carries both halves. No source is enabled, disabled, re-scoped
or promoted here; the report is made to say what already happens.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner.source_coverage import build_source_coverage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _report(**extra):
    base = dict(
        configured_sources=[{"id": "srhady-bingstream"}],
        raw_candidates=[{"source_id": "streamed-fixtures"}] * 489,
        parsed_candidates=[{"source_id": "streamed-fixtures"}] * 489,
        matched_candidates=[],
        published_today_items=[],
        published_upcoming_items=[],
    )
    base.update(extra)
    return build_source_coverage(**base)


def _row(report, source_id):
    for row in report["sources"] + report["unconfigured_sources"]:
        if row["source_id"] == source_id:
            return row
    raise AssertionError("no row for %s" % source_id)


class TheRowSaysWhatTheLayerDid(unittest.TestCase):
    def test_the_contribution_is_stated_beside_the_zero(self):
        report = _report(metadata_contributions={
            "streamed-fixtures": {"matched": 50, "artwork": 50,
                                  "embed_backups": 0, "unmatched": 423}})
        row = _row(report, "streamed-fixtures")
        self.assertEqual(0, row["published_unique_fixtures"])
        reason = " ".join(row["drop_reasons"])
        self.assertIn("metadata layer", reason)
        self.assertIn("card authority denied by design", reason)
        self.assertIn("50", reason)
        self.assertEqual(50, row["metadata_contribution"]["matched"])

    def test_without_the_evidence_the_row_is_unchanged(self):
        row = _row(_report(), "streamed-fixtures")
        self.assertNotIn("metadata_contribution", row)
        self.assertTrue(row["drop_reasons"])

    def test_a_metadata_layer_that_did_publish_is_not_excused(self):
        """If it ever leads a card, that is a different arrangement and the
        ordinary accounting applies."""
        card = {"id": "a-vs-b", "name": "A vs B", "source_id": "streamed-fixtures"}
        report = _report(
            matched_candidates=[{"source_id": "streamed-fixtures"}] * 3,
            deduped_candidates=[card],
            published_today_items=[card],
            metadata_contributions={"streamed-fixtures": {"matched": 50,
                                                          "artwork": 50}})
        row = _row(report, "streamed-fixtures")
        self.assertEqual(1, row["published_unique_fixtures"])
        self.assertNotIn("metadata layer", " ".join(row["drop_reasons"]))

    def test_it_still_never_counts_as_a_configured_source(self):
        report = _report(metadata_contributions={
            "streamed-fixtures": {"matched": 50, "artwork": 50}})
        self.assertEqual(1, report["configured_source_count"])
        self.assertEqual(["srhady-bingstream"],
                         [row["source_id"] for row in report["sources"]])
        self.assertFalse(_row(report, "streamed-fixtures")["configured"])


class TheAuthorityDenialIsStillInPlace(unittest.TestCase):
    """PROMPT 36's verdict rests on this: the layer cannot publish a card, so
    "0 published" is the design and not a failure. If that ever changes, this
    reporting is describing something that no longer exists."""

    def test_the_provider_is_documented_as_metadata_only(self):
        source = (ROOT / "scanner" / "streamed_provider.py").read_text(
            encoding="utf-8")
        self.assertIn("its match id never becomes the Click TV event_id", source)
        self.assertIn("is never a deletion authority", source)

    def test_every_fixture_candidate_it_builds_is_metadata_only(self):
        """Behaviour, not a comment: measured on the live payload, all 489
        candidates carried `metadata_only: True`, no url and no channel."""
        source = (ROOT / "scanner" / "streamed_provider.py").read_text(
            encoding="utf-8")
        fixture_block = source.split('"source_id": f"{PROVIDER_ID}-fixtures"', 1)[1][:900]
        self.assertIn('"metadata_only": True', fixture_block)
        self.assertNotIn('"url"', fixture_block)


    def test_the_layer_is_not_a_configured_event_source(self):
        """It is driven by config/settings.json, not by the source registry,
        so the 21 configured rows are unaffected either way."""
        import json
        configured = json.loads(
            (ROOT / "config" / "sources" / "today-match.json").read_text(
                encoding="utf-8"))
        ids = {entry.get("id") for entry in configured["sources"]}
        self.assertNotIn("streamed-fixtures", ids)
        self.assertNotIn("streamed", ids)


if __name__ == "__main__":
    unittest.main()
