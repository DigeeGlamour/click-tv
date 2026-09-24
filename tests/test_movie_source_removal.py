"""ধারা ৪.০ - the fourth definitive reason, and the deadlock it ends.

The measured failure these tests are written from, both runs on 2026-09-24:

    06:40   unexplained_live_loss 362   publish BLOCKED
    08:41   unexplained_live_loss 534   publish BLOCKED

The source retired the host `jrtyh.b-cdn.net`. `scanner/movie_retention.py`
stopped re-publishing the withdrawn cards after its grace, exactly as written.
The gate then counted every one of their streams as an unexplained loss and
blocked the publish, exactly as written. Blocking keeps the previous pages up,
which keeps those streams "previously live", which blocks the next run too: all
25 lost samples in the 08:41 report carried `misses: 11` against a grace of 3.

So the tests come in two halves. The first half is about the thing that can go
wrong if this module is careless - calling something terminal that is not - and
it is by far the longer half, because `definitive_rejected` is the one bucket
an entry cannot come back from. The second half proves the deadlock actually
ends, because a fix that is safe and does not work is not a fix.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_coverage as mc  # noqa: E402
from scanner import movie_retention as mrt  # noqa: E402
from scanner import movie_source_removal as msr  # noqa: E402

GONE_URL = "https://jrtyh.b-cdn.net/s2/upload/videos/2024/10/CU.Soon.2020.mkv"


def _removed(**overrides):
    entry = {
        "key": "cu-soon-2020-malayalam",
        "content_id": "cu-soon-2020-malayalam",
        "name": "CU Soon (2020)",
        "category": "south-indian",
        "urls": [GONE_URL],
        "misses": 11,
        "complete_misses": 11,
        "scan_complete": True,
        "dropped_at": "2026-09-24T08:33:43+00:00",
    }
    entry.update(overrides)
    return entry


def _decide(entry, **evidence):
    return msr.decide(
        entry,
        published_families=set(evidence.get("published_families") or ()),
        published_card_ids=set(evidence.get("published_card_ids") or ()),
        live_families=set(evidence.get("live_families") or ()),
        source_families=set(evidence.get("source_families") or ()),
        grace_scans=evidence.get("grace_scans", mrt.GRACE_SCANS),
    )


class TheReasonIsTheOneTheGateAccepts(unittest.TestCase):
    """A reason the gate does not recognise is silently dropped, so the whole
    module would become a no-op without ever failing."""

    def test_it_is_a_definitive_reason(self):
        self.assertIn(msr.REASON, mc.DEFINITIVE_REJECT_REASONS)

    def test_it_is_the_fourth_one_the_plan_names(self):
        self.assertEqual(msr.REASON, "source_removed_with_no_alternative")
        self.assertEqual(len(mc.DEFINITIVE_REJECT_REASONS), 4)


class NothingIsTerminalBeforeTheGraceIsSpent(unittest.TestCase):
    def test_a_card_missing_once_is_not_removed(self):
        families, refusal = _decide(_removed(complete_misses=1))
        self.assertEqual(families, [])
        self.assertEqual(refusal, msr.WITHIN_GRACE)

    def test_the_boundary_is_past_the_grace_not_at_it(self):
        """`retain` drops at `misses > GRACE_SCANS`, so the evidence threshold
        is the same comparison. Equal is still inside the grace."""
        _, refusal = _decide(_removed(complete_misses=mrt.GRACE_SCANS))
        self.assertEqual(refusal, msr.WITHIN_GRACE)
        families, refusal = _decide(_removed(complete_misses=mrt.GRACE_SCANS + 1))
        self.assertEqual(refusal, "")
        self.assertTrue(families)

    def test_misses_on_half_broken_scans_do_not_count(self):
        """`complete_misses` is the field, never `misses`. A run that died a
        third of the way through has not discovered that anything is gone."""
        entry = _removed(misses=40, complete_misses=2)
        _, refusal = _decide(entry)
        self.assertEqual(refusal, msr.WITHIN_GRACE)


class EveryAlternativeStopsIt(unittest.TestCase):
    """The plan's three sub-conditions, plus the payload itself. Any one of
    them true means the entry is NOT terminal - it stays exactly where it was
    and the gate keeps asking about it."""

    def test_a_stream_still_in_the_payload_is_not_gone(self):
        _, refusal = _decide(_removed(),
                             published_families={mc.stream_family(GONE_URL)})
        self.assertEqual(refusal, msr.STREAM_STILL_PUBLISHED)

    def test_a_card_still_on_the_site_is_not_gone(self):
        _, refusal = _decide(_removed(),
                             published_card_ids={"cu-soon-2020-malayalam"})
        self.assertEqual(refusal, msr.CARD_STILL_PUBLISHED)

    def test_a_link_the_ledger_calls_healthy_is_a_live_backup(self):
        _, refusal = _decide(_removed(),
                             live_families={mc.stream_family(GONE_URL)})
        self.assertEqual(refusal, msr.LINK_STILL_HEALTHY)

    def test_another_source_still_listing_it_stops_it(self):
        """Including a last-good snapshot: a repository whose fetch failed
        contributes its cached rows through the ordinary path, so they are in
        this set too."""
        _, refusal = _decide(_removed(),
                             source_families={mc.stream_family(GONE_URL)})
        self.assertEqual(refusal, msr.ANOTHER_SOURCE_HAS_IT)

    def test_one_surviving_backup_saves_the_whole_card(self):
        alive = "https://cdn.test/backup/cu-soon.mkv"
        _, refusal = _decide(
            _removed(urls=[GONE_URL, alive]),
            live_families={mc.stream_family(alive)},
        )
        self.assertEqual(refusal, msr.LINK_STILL_HEALTHY)

    def test_a_removal_with_no_recorded_stream_explains_nothing(self):
        _, refusal = _decide(_removed(urls=[]))
        self.assertEqual(refusal, msr.NO_STREAM_RECORDED)

    def test_rubbish_is_refused_rather_than_raising(self):
        for entry in (None, "", 5, []):
            with self.subTest(entry=entry):
                families, refusal = _decide(entry)
                self.assertEqual(families, [])
                self.assertTrue(refusal)


class AVantageProblemCannotReachThisPath(unittest.TestCase):
    """ধারা ৪.১. 1,312 posters in this repository answer 403 from Bangladesh
    and 200 from a GitHub runner. None of that may become a removal."""

    def test_no_http_status_is_an_input(self):
        """Proven by reading the decision, not by trusting the docstring: a
        status attached to the entry changes nothing at all."""
        base = _decide(_removed())
        for status in (403, 451, 429, 500, 0):
            with self.subTest(status=status):
                self.assertEqual(
                    _decide(_removed(http_status=status,
                                     verification_status="failed")),
                    base,
                )

    def test_the_module_never_reads_a_failure_status(self):
        source = (ROOT / "scanner" / "movie_source_removal.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        read = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        for forbidden in ("failing", "confirmed_unavailable", "dead",
                          "http_status", "verification_status"):
            self.assertNotIn(forbidden, read)

    def test_only_healthy_is_read_from_the_link_ledger(self):
        """And only as a veto. A ledger full of failures produces no removals
        and no vetoes, which leaves the decision exactly where it was."""
        store = {"links": {
            "a": {"status": "failing", "stream_family": GONE_URL},
            "b": {"status": "confirmed_unavailable", "stream_family": GONE_URL},
            "c": {"status": "dead", "stream_family": GONE_URL},
        }}
        self.assertEqual(msr.healthy_families(store), set())
        store["links"]["d"] = {"status": "healthy", "stream_family": GONE_URL}
        self.assertEqual(msr.healthy_families(store),
                         {mc.stream_family(GONE_URL)})


class TheRecordsHandedToTheGate(unittest.TestCase):
    def test_each_record_is_a_family_and_the_reason(self):
        records, summary = msr.terminal_records([_removed()])
        self.assertEqual(records, [
            {"url": mc.stream_family(GONE_URL), "reason": msr.REASON}])
        self.assertEqual(summary["terminal_streams"], 1)
        self.assertEqual(summary["removed_entries"], 1)

    def test_no_entry_key_is_ever_supplied(self):
        """A terminal verdict keyed by entry could reach a row this run's
        sources DID produce. Only the family, which the gate matches after
        every publication layer has already failed."""
        records, _ = msr.terminal_records([_removed()])
        for record in records:
            self.assertEqual(set(record), {"url", "reason"})

    def test_the_same_stream_is_reported_once(self):
        records, _ = msr.terminal_records(
            [_removed(), _removed(key="other", content_id="other")])
        self.assertEqual(len(records), 1)

    def test_refusals_are_counted_rather_than_discarded(self):
        _, summary = msr.terminal_records(
            [_removed(complete_misses=1), _removed(key="b", content_id="b")],
            published_card_ids={"b"},
        )
        self.assertEqual(summary["refused"],
                         {msr.WITHIN_GRACE: 1, msr.CARD_STILL_PUBLISHED: 1})
        self.assertEqual(summary["terminal_streams"], 0)

    def test_the_log_line_says_what_was_kept(self):
        _, summary = msr.terminal_records([_removed(complete_misses=1)])
        line = msr.describe(summary)
        self.assertIn("source removals", line)
        self.assertIn(msr.WITHIN_GRACE, line)

    def test_nothing_to_say_says_nothing(self):
        self.assertEqual(msr.describe({}), "")
        _, summary = msr.terminal_records([])
        self.assertEqual(msr.describe(summary), "")


class TheRetentionLedgerRemembersWhatItDropped(unittest.TestCase):
    """A card that is simply dropped takes its streams with it, and the gate
    is then asked about streams nobody can describe any more."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "movies"
        self.category = self.root / "hindi"
        self.category.mkdir(parents=True)
        self.card = {
            "id": "double-ismart-2024",
            "name": "Double iSmart (2024)",
            "url": GONE_URL,
            "backups": [{"url": "https://jrtyh.b-cdn.net/s2/backup.mkv"}],
        }
        #: Four more, so this category has five published cards. The sizes
        #: matter: `scan_looks_complete` reads the ratio, so a fixture with one
        #: card can never produce an incomplete scan to test against.
        self.others = [dict(self.card, id="filler-%d" % index, backups=[])
                       for index in range(4)]
        self.path = str(Path(self.dir.name) / "retention.json")
        self._publish([self.card] + self.others)

    def _publish(self, cards):
        (self.category / "page-001.json").write_text(
            json.dumps({"items": list(cards)}), encoding="utf-8")

    def _scan(self, incoming, *, when=None):
        return mrt.retain(
            incoming, "hindi", root=str(self.root), path=self.path,
            now=when or dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc),
        )

    def _store(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def _miss_until_dropped(self):
        """Four of the five come back every time - a complete scan that has
        simply stopped listing one film."""
        for _ in range(mrt.GRACE_SCANS + 1):
            self._scan([dict(item) for item in self.others])

    def test_within_the_grace_the_card_is_still_published(self):
        kept, summary = self._scan([dict(item) for item in self.others])
        self.assertEqual(summary["retained"], 1)
        self.assertIn("double-ismart-2024", [item["id"] for item in kept])
        self.assertEqual(self._store().get("removed"), {})

    def test_a_drop_is_recorded_with_its_streams(self):
        self._miss_until_dropped()
        entries = mrt.removed_entries(path=self.path)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["content_id"], "double-ismart-2024")
        self.assertEqual(entry["category"], "hindi")
        self.assertIn(GONE_URL, entry["urls"])
        self.assertIn("https://jrtyh.b-cdn.net/s2/backup.mkv", entry["urls"])
        self.assertGreater(entry["complete_misses"], mrt.GRACE_SCANS)

    def test_a_film_that_comes_back_is_forgotten_on_that_scan(self):
        self._miss_until_dropped()
        self.assertTrue(mrt.removed_entries(path=self.path))
        self._scan([dict(self.card)])
        self.assertEqual(mrt.removed_entries(path=self.path), [])

    def test_an_incomplete_scan_drops_nothing_at_all(self):
        """One film returned where the category published five. That is a
        broken run, not a discovery that four films are gone - so no card is
        dropped and no grace is spent, however often it repeats.

        Measured: `mix` returned 83 of 937 cards and 347 were dropped anyway,
        which is where 403 of the 506 unexplained losses came from.
        """
        for _ in range(mrt.GRACE_SCANS + 4):
            kept, summary = self._scan([dict(self.others[0])])
        self.assertEqual(mrt.removed_entries(path=self.path), [])
        self.assertEqual(summary["dropped_after_grace"], 0)
        self.assertFalse(summary["scan_complete"])
        self.assertEqual(summary["carried_through_incomplete_scan"], 4)
        self.assertIn("double-ismart-2024", [item["id"] for item in kept])

    def test_nothing_is_counted_absent_on_an_incomplete_scan(self):
        for _ in range(mrt.GRACE_SCANS + 4):
            self._scan([dict(self.others[0])])
        self.assertEqual(self._store().get("absent"), {})

    def test_the_countdown_resumes_once_the_category_is_visible_again(self):
        """The grace is paused, not forgiven."""
        for _ in range(mrt.GRACE_SCANS + 4):
            self._scan([dict(self.others[0])])
        for _ in range(mrt.GRACE_SCANS + 1):
            self._scan([dict(item) for item in self.others])
        entries = mrt.removed_entries(path=self.path)
        self.assertEqual([entry["key"] for entry in entries],
                         ["double-ismart-2024"])

    def test_a_ledger_written_before_the_field_existed_inherits_its_count(self):
        """Resetting to zero would tell the gate that a film the source has
        not listed for eleven scans had only just gone missing."""
        legacy = {"version": 1, "absent": {
            "double-ismart-2024": {"misses": 11, "last_missing_at": "x"}},
            "lifecycle": {}}
        Path(self.path).write_text(json.dumps(legacy), encoding="utf-8")
        self._scan([dict(self.card, id="filler-%d" % index)
                    for index in range(4)])
        entry = mrt.removed_entries(path=self.path)[0]
        self.assertEqual(entry["complete_misses"], 12)

    def test_a_removal_is_re_recorded_while_the_card_is_still_on_the_site(self):
        """The publish can be blocked for other reasons, and then the card
        stays up. Its removal has to stay fresh for as long as the gate can
        still be asked about it."""
        self._miss_until_dropped()
        store = self._store()
        store["removed"]["double-ismart-2024"]["dropped_at"] = "2020-01-01T00:00:00+00:00"
        Path(self.path).write_text(json.dumps(store), encoding="utf-8")
        self._scan([dict(item) for item in self.others])
        self.assertIn("double-ismart-2024", self._store()["removed"])

    def test_a_removal_nothing_refreshes_is_eventually_forgotten(self):
        """Once the site itself has let the card go, nothing asks about its
        streams again and keeping them as evidence only grows the file."""
        self._miss_until_dropped()
        store = self._store()
        store["removed"]["double-ismart-2024"]["dropped_at"] = "2020-01-01T00:00:00+00:00"
        Path(self.path).write_text(json.dumps(store), encoding="utf-8")
        self._publish(self.others)
        self._scan([dict(item) for item in self.others])
        self.assertNotIn(
            "double-ismart-2024", self._store().get("removed") or {})

    def test_the_grace_itself_is_unchanged(self):
        """This module adds evidence; it does not make anything disappear
        sooner. The publish grace is still the number it was."""
        self.assertEqual(mrt.GRACE_SCANS, 3)


class TheDeadlockEnds(unittest.TestCase):
    """The regression test in the literal sense - this is the 2026-09-24
    shape, and it must stop blocking."""

    def setUp(self) -> None:
        self.sources = [{"id": "sm-movie-combined", "name": "SM", "enabled": True}]
        self.previous = ["movie|hindi|double-ismart-2024|" + GONE_URL]

    def _build(self, terminal):
        return mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[],
            published_movies=[],
            previous_inventory=self.previous,
            current_inventory=[],
            terminal_records=terminal,
        )

    def test_without_the_evidence_the_publish_is_blocked(self):
        report = self._build([])
        self.assertEqual(report["invariant_2"]["unexplained_live_loss"], 1)
        self.assertTrue(report["invariants"]["block"])

    def test_with_it_the_loss_is_explained_and_the_publish_proceeds(self):
        records, _ = msr.terminal_records([
            _removed(content_id="double-ismart-2024", urls=[GONE_URL])])
        report = self._build(records)
        two = report["invariant_2"]
        self.assertEqual(two["terminal_evidence"], 1)
        self.assertEqual(two["unexplained_live_loss"], 0)
        self.assertFalse(report["invariants"]["block"])

    def test_a_card_still_published_is_never_explained_away(self):
        """The same removal, but the film is on the site. It must count as
        visible, not as terminally gone."""
        card = {"id": "double-ismart-2024", "name": "Double iSmart",
                "url": GONE_URL}
        records, summary = msr.terminal_records(
            [_removed(content_id="double-ismart-2024", urls=[GONE_URL])],
            published_card_ids={"double-ismart-2024"},
        )
        self.assertEqual(records, [])
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[],
            published_movies=[("hindi", card)],
            previous_inventory=self.previous,
            current_inventory=self.previous,
            terminal_records=records,
        )
        self.assertEqual(report["invariant_2"]["currently_visible"], 1)
        self.assertEqual(report["invariant_2"]["terminal_evidence"], 0)
        self.assertEqual(summary["refused"], {msr.CARD_STILL_PUBLISHED: 1})


class TheScanWiresItIn(unittest.TestCase):
    SCAN = (ROOT / "scan.py").read_text(encoding="utf-8")

    def _function(self, name):
        tree = ast.parse(self.SCAN)
        return next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == name)

    def _calls(self, node):
        found = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute):
                    found.add(func.attr)
                elif isinstance(func, ast.Name):
                    found.add(func.id)
        return found

    def test_the_gate_is_given_both_kinds_of_evidence(self):
        calls = self._calls(self._function("_movie_terminal_records"))
        self.assertIn("terminal_records", calls)   # the link ledger
        self.assertIn("collect", calls)            # the source removals

    def test_neither_kind_can_fail_a_scan(self):
        source = ast.get_source_segment(
            self.SCAN, self._function("_movie_terminal_records")) or ""
        self.assertEqual(source.count("except Exception"), 2)

    def test_the_payload_is_passed_so_the_vetoes_can_be_applied(self):
        source = ast.get_source_segment(
            self.SCAN, self._function("_build_movie_coverage")) or ""
        for argument in ("published_cards=", "current_inventory=",
                         "raw_entries="):
            self.assertIn(argument, source)


if __name__ == "__main__":
    unittest.main()
