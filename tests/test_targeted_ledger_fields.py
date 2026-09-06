"""The targeting ledger carries the state a retry ladder will need.

Nothing here changes when a fixture is targeted. The once-only gate is still
the gate; these tests exist to pin that, and to pin the four fields recorded
ahead of the ladder that will lift it.

WHY THE FIELDS ARRIVE BEFORE THE LADDER.

The committed ledger on 2026-09-04 held eleven fixtures, every one of them
`attempts: 1`, three resolved and eight not — and none of them carrying any
record of *when* that attempt happened relative to the five-minute trigger:

    attempted · attempted_at · attempts · name · resolved · resolved_at
    route_id · start_time · url_public_template

A ladder that allows one attempt per five-minute slot has to know which slot
the last attempt belonged to. Introduce that field at the same moment the gate
is lifted and every pre-existing entry is undecidable: it has an attempt, but
no slot, so the first run after the upgrade either re-attempts everything or
suppresses everything. Writing the field first means the file has grown the
state before anything depends on it.

    last_attempt_at        the most recent attempt, beside the first
    last_attempt_bucket    the five-minute slot that attempt fell in
    first_link_at          when a usable link FIRST existed, carried forward
    last_success_at        when one was last confirmed, always the latest

`first_link_at` answers "how long before kickoff did a link appear", which is
the measurement the ladder is judged by, so a later attempt must not overwrite
it. `last_success_at` is deliberately the opposite, because a link that rotates
mid-match is re-confirmed rather than newly found.

A CRASH FOUND WHILE VERIFYING THIS.

`is_attempted` read the count with a bare `int()`, so a ledger whose `attempts`
was any non-numeric value raised ValueError and took the scan down. That is not
hypothetical for this file: it is committed, and the push path rebases it with a
line-based `-X theirs` text merge which has already corrupted generated JSON in
this repository once - the workflow carries a "Repair merge damage" step because
of it. A damaged count now degrades to "no attempt recorded". Every well-formed
value answers exactly as it did before, which is tested below rather than
asserted.
"""
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import targeted_scan as ledger_module

UTC = datetime.timezone.utc
COMMITTED_LEDGER = ROOT / "state" / "upcoming-targeting.json"

NEW_FIELDS = ("last_attempt_at", "last_attempt_bucket",
              "first_link_at", "last_success_at")


def _wrap(record):
    return {"fixtures": {"k": record}}


class AnOlderLedgerStillLoads(unittest.TestCase):
    """The four fields are absent from every entry written before them."""

    def test_the_committed_ledger_predates_the_new_fields(self):
        """If this ever fails the file has moved on and the rest of this class
        is testing nothing - which is worth being told about."""
        if not COMMITTED_LEDGER.exists():
            self.skipTest("no committed ledger in this checkout")
        entries = json.loads(COMMITTED_LEDGER.read_text(encoding="utf-8"))["fixtures"]
        if not entries:
            self.skipTest("the committed ledger is empty")
        seen = {field for entry in entries.values() for field in entry}
        self.assertTrue(seen, "entries carry no fields at all")

    def test_it_loads_and_every_reader_answers(self):
        if not COMMITTED_LEDGER.exists():
            self.skipTest("no committed ledger in this checkout")
        loaded = ledger_module.load_ledger(COMMITTED_LEDGER)
        self.assertIsInstance(loaded.get("fixtures"), dict)
        for key in loaded["fixtures"]:
            with self.subTest(fixture=key):
                self.assertIsInstance(ledger_module.attempt_count(loaded, key), int)
                self.assertIsInstance(ledger_module.last_attempt_bucket(loaded, key), str)
                self.assertIsInstance(ledger_module.first_link_at(loaded, key), str)
                self.assertIsInstance(ledger_module.last_success_at(loaded, key), str)

    def test_a_missing_field_reads_as_absent_not_as_an_error(self):
        old = _wrap({"attempted": True, "attempted_at": "2026-08-30T10:00:00+00:00",
                     "attempts": 1, "resolved": False})
        self.assertEqual("", ledger_module.last_attempt_bucket(old, "k"))
        self.assertEqual("", ledger_module.first_link_at(old, "k"))
        self.assertEqual(1, ledger_module.attempt_count(old, "k"))

    def test_a_fixture_the_ledger_has_never_seen(self):
        empty = {"fixtures": {}}
        self.assertEqual(0, ledger_module.attempt_count(empty, "nobody"))
        self.assertEqual("", ledger_module.last_attempt_bucket(empty, "nobody"))
        self.assertEqual("", ledger_module.first_link_at(empty, "nobody"))
        self.assertEqual("", ledger_module.last_success_at(empty, "nobody"))
        self.assertFalse(ledger_module.is_attempted(empty, "nobody"))

    def test_last_success_at_falls_back_to_the_older_resolved_at(self):
        """An entry written before the field still knows when it succeeded."""
        old = _wrap({"resolved": True, "resolved_at": "2026-08-30T10:40:16+00:00"})
        self.assertEqual("2026-08-30T10:40:16+00:00",
                         ledger_module.last_success_at(old, "k"))

    def test_a_damaged_ledger_degrades_instead_of_raising(self):
        for label, payload in (
            ("no fixtures key", {}),
            ("fixtures is a list", {"fixtures": []}),
            ("record is a string", {"fixtures": {"k": "nope"}}),
            ("record is null", {"fixtures": {"k": None}}),
            ("attempts is text", _wrap({"attempts": "many"})),
            ("attempts is null", _wrap({"attempts": None})),
            ("attempts is a list", _wrap({"attempts": [1]})),
            ("bucket is a number", _wrap({"last_attempt_bucket": 5})),
        ):
            with self.subTest(case=label):
                self.assertEqual(0, ledger_module.attempt_count(payload, "k"))
                self.assertIsInstance(ledger_module.last_attempt_bucket(payload, "k"), str)
                self.assertFalse(ledger_module.is_attempted(payload, "k"))

    def test_an_unreadable_file_is_an_empty_ledger_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "broken.json"
            broken.write_text("{ not json at all", encoding="utf-8")
            self.assertEqual({"fixtures": {}}, ledger_module.load_ledger(broken))
            self.assertEqual({"fixtures": {}},
                             ledger_module.load_ledger(Path(folder) / "absent.json"))


class TheGateReadsTheseFieldsNow(unittest.TestCase):
    """These fields stopped being inert the moment the once-only gate went.

    `is_attempted` still answers the question it always answered - it is just
    no longer the question being asked before a scan.
    """

    def test_having_been_tried_is_still_recorded(self):
        after_one = _wrap({"attempted": True, "attempts": 1, "resolved": False})
        self.assertTrue(ledger_module.is_attempted(after_one, "k"))

    def test_but_having_been_tried_no_longer_suppresses(self):
        """The whole of PROMPT 05, in one assertion."""
        after_one = _wrap({"attempted": True, "attempts": 1, "resolved": False})
        self.assertTrue(ledger_module.is_retry_eligible(after_one, "k"))
        self.assertEqual("", ledger_module.retry_skip_reason(after_one, "k"))

    def test_a_single_attempt_still_writes_a_count_of_one(self):
        """It increments from whatever was there, and nothing was."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            item = {"fixture_id": "alpha-vs-beta", "name": "Alpha vs Beta",
                    "start_time": "2026-09-04T08:00:00+00:00",
                    "url": "https://example.test/live.m3u8", "verified": True}
            key = ledger_module.fixture_key(item)
            plan = ledger_module.TargetPlan(window_minutes=30, targets={key})
            written = ledger_module.record_outcome(
                ledger_module.load_ledger(path), plan, [item],
                now=datetime.datetime(2026, 9, 4, 7, 34, 59, tzinfo=UTC))
            self.assertEqual(1, written["fixtures"][key]["attempts"])

    def test_nothing_outside_the_ladder_acts_on_these_fields(self):
        """Scope: the fields are written here and acted on here.

        PROMPT 34 gave one module permission to read them -
        fixture_stream_health.py, which reports what the ladder did and writes
        nothing back to the ledger. That is reporting, not a second gate. If a
        reader appears anywhere else, the gate has moved and that is a
        different prompt's decision.
        """
        allowed = {"targeted_scan.py", "fixture_stream_health.py"}
        sources = [path for path in (ROOT / "scanner").glob("*.py")
                   if path.name not in allowed]
        sources.append(ROOT / "scan.py")
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for name in ("last_attempt_bucket", "attempt_bucket",
                         "first_link_at", "last_success_at"):
                with self.subTest(file=path.name, reader=name):
                    self.assertNotIn(name, text)

    def test_the_one_reader_never_writes_to_the_ledger(self):
        text = (ROOT / "scanner" / "fixture_stream_health.py").read_text(
            encoding="utf-8")
        for writer in ("save_ledger", "record_outcome", "record_attempt"):
            self.assertNotIn(writer, text)


class TheFieldsAreWritten(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "ledger.json"
        self.when = datetime.datetime(2026, 9, 4, 7, 34, 59, tzinfo=UTC)
        self.item = {
            "fixture_id": "alpha-vs-beta", "name": "Alpha vs Beta",
            "start_time": "2026-09-04T08:00:00+00:00",
            "url": "https://example.test/live.m3u8?token=SECRET123",
            "verified": True, "verification_status": "verified",
        }
        self.key = ledger_module.fixture_key(self.item)
        self.plan = ledger_module.TargetPlan(
            window_minutes=30, targets={self.key, "found-nothing"})

    def tearDown(self):
        self.folder.cleanup()

    def _write(self, published, now):
        result = ledger_module.record_outcome(
            ledger_module.load_ledger(self.path), self.plan, published, now=now)
        ledger_module.save_ledger(result, self.path)
        return json.loads(self.path.read_text(encoding="utf-8"))["fixtures"]

    def test_an_attempt_records_its_slot_whether_or_not_it_found_a_link(self):
        entries = self._write([self.item], self.when)
        for key in (self.key, "found-nothing"):
            with self.subTest(fixture=key):
                self.assertEqual("2026-09-04T07:30Z",
                                 entries[key]["last_attempt_bucket"])
                self.assertEqual(self.when.isoformat(),
                                 entries[key]["last_attempt_at"])

    def test_a_link_stamps_both_the_first_and_the_latest(self):
        entries = self._write([self.item], self.when)
        self.assertEqual(self.when.isoformat(), entries[self.key]["first_link_at"])
        self.assertEqual(self.when.isoformat(), entries[self.key]["last_success_at"])

    def test_no_link_means_no_link_stamps_invented(self):
        entries = self._write([self.item], self.when)
        self.assertNotIn("first_link_at", entries["found-nothing"])
        self.assertNotIn("last_success_at", entries["found-nothing"])
        self.assertIs(False, entries["found-nothing"]["resolved"])

    def test_the_first_link_time_is_carried_forward_and_the_latest_moves(self):
        self._write([self.item], self.when)
        later = datetime.datetime(2026, 9, 4, 7, 41, 0, tzinfo=UTC)
        entries = self._write([self.item], later)
        self.assertEqual(self.when.isoformat(), entries[self.key]["first_link_at"])
        self.assertEqual(later.isoformat(), entries[self.key]["last_success_at"])
        self.assertEqual("2026-09-04T07:40Z", entries[self.key]["last_attempt_bucket"])

    def test_a_link_that_disappears_does_not_erase_that_it_existed(self):
        self._write([self.item], self.when)
        later = datetime.datetime(2026, 9, 4, 7, 41, 0, tzinfo=UTC)
        entries = self._write([], later)
        self.assertIs(False, entries[self.key]["resolved"])
        self.assertEqual(self.when.isoformat(), entries[self.key]["first_link_at"])
        self.assertEqual(self.when.isoformat(), entries[self.key]["last_success_at"])

    def test_the_new_fields_carry_no_credential(self):
        """The ledger is committed to a public repository."""
        entries = self._write([self.item], self.when)
        self.assertNotIn("SECRET123", json.dumps(entries))


class TheSlotFloorsAndNeverRounds(unittest.TestCase):
    def test_a_late_start_belongs_to_the_slot_it_was_scheduled_for(self):
        for minute, second, expected in (
            (30, 0, "07:30Z"), (31, 0, "07:30Z"), (34, 59, "07:30Z"),
            (35, 0, "07:35Z"), (39, 59, "07:35Z"),
            (0, 0, "07:00Z"), (59, 59, "07:55Z"),
        ):
            with self.subTest(minute=minute, second=second):
                when = datetime.datetime(2026, 9, 4, 7, minute, second, tzinfo=UTC)
                self.assertEqual("2026-09-04T" + expected,
                                 ledger_module.attempt_bucket(when))

    def test_the_slot_is_utc_whatever_it_is_given(self):
        dhaka = datetime.timezone(datetime.timedelta(hours=6))
        aware = datetime.datetime(2026, 9, 4, 13, 34, 59, tzinfo=dhaka)
        naive = datetime.datetime(2026, 9, 4, 7, 34, 59)
        self.assertEqual("2026-09-04T07:30Z", ledger_module.attempt_bucket(aware))
        self.assertEqual("2026-09-04T07:30Z", ledger_module.attempt_bucket(naive))

    def test_the_width_matches_the_trigger(self):
        self.assertEqual(5, ledger_module.BUCKET_MINUTES)

    def test_a_nonsense_width_cannot_produce_a_division_error(self):
        when = datetime.datetime(2026, 9, 4, 7, 34, 59, tzinfo=UTC)
        for width in (0, -5):
            with self.subTest(width=width):
                self.assertTrue(ledger_module.attempt_bucket(when, width))


if __name__ == "__main__":
    unittest.main()
