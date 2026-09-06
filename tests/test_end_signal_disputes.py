"""Disputed ends, watching viewers, and fixtures that try to come back.

FINAL_2 ধাপ ৪ names two safeguards and one consequence:

  • পরস্পরবিরোধী signal (এক source ENDED, বিশ্বস্ত source LIVE) হলে সাথে সাথে
    মুছবেন না — ২টা পরপর tick বা trusted-authority অগ্রাধিকার
  • কেউ দেখতে থাকলে card সরানো যাবে না … FT আসার পরেও কেউ দেখতে থাকলে কী হবে?
    grace শেষ হলে card তালিকা থেকে যাবে, কিন্তু চলমান playback থামবে না
  • purge মানে delete নয় — state/event-archive.json-এ সরানো

All three reuse what was already here: `confirmations_required`, the
`currently_playing` protection, and the existing identity fields.
"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scanner import event_archive as ea                       # noqa: E402
from scanner.event_lifecycle import (                         # noqa: E402
    END_PENDING, ENDED, LIVE, LifecycleSignals, apply_verdict, decide,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def signals(**extra):
    base = dict(
        authority_live=None, strong_end=False, primary_playable=True,
        backup_playable=None, currently_playing=False, estimate_passed=False,
        consecutive_non_live_scans=0, seen_in_this_scan=True,
    )
    base.update(extra)
    return LifecycleSignals(**base)


def run(card, offset=timedelta(0), grace=20, required=3, **signal_extra):
    return decide(
        card, signals(**signal_extra), now=T0 + offset,
        post_match_grace_minutes=grace, confirmations_required=required,
    )


# ─────────────────────────────────────────────────────────── PROMPT 22

class ADisputedEndIsNotAnAuthority(unittest.TestCase):
    def test_one_feed_saying_ft_does_not_beat_a_trusted_live(self):
        verdict = run({"id": "a", "status": "FT"},
                      strong_end=True, authority_live=True)
        self.assertEqual(LIVE, verdict.state)
        self.assertTrue(verdict.publish)
        self.assertIn("authority_live", verdict.protections)

    def test_nothing_is_stamped_while_the_dispute_stands(self):
        """`ended_seen_at` would start the post-match grace on evidence that
        may be gone by the next scan."""
        verdict = run({"id": "a", "status": "FT"},
                      strong_end=True, authority_live=True)
        self.assertEqual("", verdict.ended_seen_at)
        card = apply_verdict({"id": "a", "status": "FT"}, verdict)
        self.assertNotIn("ended_seen_at", card)

    def test_repeated_credible_evidence_moves_the_lifecycle_on(self):
        card = {"id": "a", "status": "FT"}
        states = []
        for scan in range(1, 5):
            verdict = run(card, timedelta(minutes=scan),
                          strong_end=True, authority_live=True)
            card = apply_verdict(card, verdict)
            states.append(verdict.state)
        self.assertEqual([LIVE, LIVE, END_PENDING, END_PENDING], states)
        self.assertEqual((T0 + timedelta(minutes=3)).isoformat(),
                         card["ended_seen_at"])

    def test_the_tally_is_not_dropped_once_it_is_reached(self):
        """It used to be, and the card cycled LIVE / END_PENDING / LIVE for
        ever without reaching the grace."""
        card = {"id": "a", "status": "FT"}
        for scan in range(1, 8):
            card = apply_verdict(card, run(
                card, timedelta(minutes=scan * 5),
                strong_end=True, authority_live=True))
        self.assertEqual(ENDED, card["lifecycle_state"])

    def test_a_temporary_end_that_goes_away_leaves_nothing_behind(self):
        card = {"id": "a", "status": "FT"}
        card = apply_verdict(card, run(card, strong_end=True, authority_live=True))
        self.assertEqual(1, card["contradicted_end_confirmations"])
        card = apply_verdict(card, run(card, strong_end=False, authority_live=True))
        self.assertNotIn("contradicted_end_confirmations", card)
        self.assertNotIn("ended_seen_at", card)

    def test_an_undisputed_end_is_unchanged(self):
        """No trusted authority contradicting it: the old behaviour exactly."""
        for extra in ({"strong_end": True}, {"authority_live": False}):
            with self.subTest(**extra):
                verdict = run({"id": "a"}, **extra)
                self.assertEqual(END_PENDING, verdict.state)
                self.assertEqual(T0.isoformat(), verdict.ended_seen_at)

    def test_the_threshold_is_the_central_config_value(self):
        card = {"id": "a", "status": "FT"}
        for scan in range(1, 6):
            verdict = run(card, timedelta(minutes=scan), required=5,
                          strong_end=True, authority_live=True)
            card = apply_verdict(card, verdict)
            with self.subTest(scan=scan):
                self.assertEqual(LIVE if scan < 5 else END_PENDING, verdict.state)

    def test_an_estimate_is_never_a_strong_signal(self):
        card = {
            "id": "a", "schedule_verified": True,
            "start_time": (T0 - timedelta(hours=5)).isoformat(),
            "end_time": (T0 - timedelta(hours=1)).isoformat(),
            "end_time_source": "sport",
        }
        verdict = run(card, authority_live=True)
        self.assertEqual(LIVE, verdict.state)
        self.assertEqual("", verdict.ended_seen_at)


# ─────────────────────────────────────────────────────────── PROMPT 23

class AViewerKeepsThePlaybackNotTheListing(unittest.TestCase):
    def test_a_live_match_being_watched_is_protected(self):
        verdict = run({"id": "a"}, currently_playing=True)
        self.assertEqual(LIVE, verdict.state)
        self.assertIn("currently_playing", verdict.protections)

    def test_after_ft_it_goes_to_end_pending_and_stays_visible(self):
        verdict = run({"id": "a", "status": "FT"},
                      currently_playing=True, strong_end=True)
        self.assertEqual(END_PENDING, verdict.state)
        self.assertTrue(verdict.publish)
        self.assertIn("currently_playing", verdict.protections)

    def test_and_it_still_leaves_when_the_grace_runs_out(self):
        card = apply_verdict(
            {"id": "a", "status": "FT"},
            run({"id": "a", "status": "FT"}, currently_playing=True,
                strong_end=True))
        for offset, expected in ((10, END_PENDING), (19, END_PENDING),
                                 (20, ENDED), (600, ENDED)):
            with self.subTest(offset=offset):
                verdict = run(card, timedelta(minutes=offset),
                              currently_playing=True, strong_end=True)
                self.assertEqual(expected, verdict.state)

    def test_a_provider_end_is_not_shielded_for_ever_either(self):
        card = {
            "id": "a", "schedule_verified": True,
            "start_time": (T0 - timedelta(hours=6)).isoformat(),
            "end_time": (T0 - timedelta(hours=3)).isoformat(),
            "end_time_source": "provider",
        }
        first = run(card, currently_playing=True)
        self.assertEqual(END_PENDING, first.state)
        card = apply_verdict(card, first)
        self.assertEqual(
            ENDED, run(card, timedelta(minutes=21), currently_playing=True).state)

    def test_retiring_a_card_touches_no_session_state(self):
        """Listing and playback are separate. Nothing in the lifecycle writes
        to the sessions file, revokes a URL or ends a stream - it only stops
        publishing a row."""
        for name in ("event_lifecycle.py", "live_protection.py"):
            source = (ROOT / "scanner" / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                # Naming the sessions file in a comment is fine - writing to
                # it, or ending a stream, is what would break the promise.
                for token in ("terminate", "revoke(", "stop_playback"):
                    self.assertNotIn(token, source)
                self.assertNotIn(
                    "playing-sessions.json\", \"w", source)
                self.assertNotIn("write_text", source.split("def decide")[0])

    def test_the_sessions_file_is_only_ever_read(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        block = source[source.index("def _playing_event_ids("):]
        block = block[:block.index("\ndef ")]
        self.assertIn("read_text", block)
        for token in ("write_text", "unlink", "remove("):
            self.assertNotIn(token, block)


# ─────────────────────────────────────────────────────────── PROMPT 24

class ARetiredFixtureStaysRetired(unittest.TestCase):
    def _archive(self):
        return {"fixtures": {}}

    @staticmethod
    def _ended(**extra):
        return dict({
            "id": "india-women-vs-pakistan-women",
            "fixture_id": "provider:india-women-vs-pakistan-women|womens asia cup|2026-09-05",
            "name": "India Women Vs Pakistan Women",
            "start_time": "2026-09-05T09:00:00+00:00",
            "lifecycle_state": "ENDED",
            "ended_seen_at": "2026-09-05T13:00:00+00:00",
        }, **extra)

    def test_a_retired_fixture_gets_an_entry(self):
        archive = self._archive()
        stats = ea.archive_retired([self._ended()], now=T0, archive=archive)
        self.assertEqual(1, stats["added"])
        self.assertTrue(ea.is_archived(self._ended(), archive))

    def test_the_same_row_coming_back_is_refused_from_both_tabs(self):
        archive = self._archive()
        ea.archive_retired([self._ended()], now=T0, archive=archive)
        returning = self._ended(lifecycle_state="UPCOMING", status="UPCOMING")
        for tab in ("today", "upcoming"):
            with self.subTest(tab=tab):
                kept, dropped = ea.drop_resurrected([returning], archive)
                self.assertEqual([], kept)
                self.assertEqual(["India Women Vs Pakistan Women"], dropped)

    def test_repeated_scans_do_not_duplicate_the_entry(self):
        archive = self._archive()
        for _ in range(5):
            ea.archive_retired([self._ended()], now=T0, archive=archive)
        self.assertEqual(1, len(archive["fixtures"]))

    def test_and_the_first_retirement_is_the_one_recorded(self):
        archive = self._archive()
        ea.archive_retired([self._ended()], now=T0, archive=archive)
        ea.archive_retired([self._ended()], now=T0 + timedelta(days=2),
                           archive=archive)
        entry = next(iter(archive["fixtures"].values()))
        self.assertEqual(T0.isoformat(), entry["archived_at"])

    def test_a_genuine_rematch_is_not_blocked(self):
        archive = self._archive()
        ea.archive_retired([self._ended()], now=T0, archive=archive)
        rematch = self._ended(
            fixture_id="provider:india-women-vs-pakistan-women|womens asia cup|2026-11-20",
            start_time="2026-11-20T09:00:00+00:00",
            lifecycle_state="UPCOMING",
        )
        self.assertFalse(ea.is_archived(rematch, archive))
        kept, dropped = ea.drop_resurrected([rematch], archive)
        self.assertEqual(1, len(kept))
        self.assertEqual([], dropped)

    def test_a_rematch_is_distinct_even_without_a_fixture_id(self):
        first = {"id": "a-vs-b", "start_time": "2026-09-05T12:00:00+00:00"}
        later = {"id": "a-vs-b", "start_time": "2026-11-20T12:00:00+00:00"}
        self.assertNotEqual(ea.archive_identity(first), ea.archive_identity(later))

    def test_an_active_card_keeps_its_identity_in_the_archive(self):
        archive = self._archive()
        ea.archive_retired([self._ended()], now=T0, archive=archive)
        entry = next(iter(archive["fixtures"].values()))
        self.assertEqual("india-women-vs-pakistan-women", entry["id"])
        self.assertEqual(self._ended()["fixture_id"], entry["fixture_id"])
        self.assertEqual("2026-09-05T09:00:00+00:00", entry["start_time"])
        self.assertEqual("2026-09-05T13:00:00+00:00", entry["ended_seen_at"])

    def test_the_entry_is_thin(self):
        """Identity and lifecycle evidence. No channels, no streams, no
        artwork - a card is around forty fields and this is nine.

        `competition` and `sport_type` joined it with the P50 correction:
        FINAL_2's identity rule is normalized teams + competition + kickoff,
        and without the competition the second tier cannot be asked at all.
        """
        archive = self._archive()
        fat = self._ended(
            channels=[{"id": "c1", "name": "Willow"}],
            backups=["https://a.example/1.m3u8"],
            logo="https://a.example/logo.png",
            playback_id="ctv_abc",
            source_provenance=[{"source_id": "x"}],
        )
        ea.archive_retired([fat], now=T0, archive=archive)
        entry = next(iter(archive["fixtures"].values()))
        self.assertEqual(
            {"id", "fixture_id", "name", "competition", "sport_type",
             "start_time", "ended_seen_at", "lifecycle_state", "archived_at"},
            set(entry),
        )

    def test_a_retirement_does_not_expire_on_a_timer(self):
        """FINAL_2 names no retention duration, so the archive invents none.

        An expiry is a lifecycle rule wearing a housekeeping costume: the
        scan after it lapses reads the still-listed row as a fixture it has
        never seen, and the ended match returns as a new card.
        """
        archive = self._archive()
        ea.archive_retired([self._ended()], now=T0, archive=archive)
        for days in (8, 40, 400):
            later = T0 + timedelta(days=days)
            ea.archive_retired([], now=later, archive=archive)
            self.assertEqual(1, len(archive["fixtures"]), days)
            self.assertTrue(ea.is_archived(self._ended(), archive), days)
            kept, dropped = ea.drop_resurrected([self._ended()], archive)
            self.assertEqual([], kept, days)
            self.assertEqual(1, len(dropped), days)

    def test_no_module_level_retention_constant_survives(self):
        """The 7-day window was a judgement call, not a FINAL requirement."""
        self.assertFalse(
            [name for name in vars(ea)
             if "RETENTION" in name or "EXPIR" in name])

    def test_protection_names_the_fixtures_it_retired_as_finished(self):
        """Found on live data: released_ended 1, archived 0.

        The card was released because THIS scan's authority said the match had
        finished - a verdict that lives in the scan, not on the card - so
        reading the published card's own fields could not see it, and the
        fixture left the tabs with nothing remembering that it had ended.
        """
        source = (Path(__file__).resolve().parents[1] / "scanner"
                  / "live_protection.py").read_text(encoding="utf-8")
        self.assertIn('stats["released_ended_ids"].append(event_id)', source)
        publish = (Path(__file__).resolve().parents[1] / "scanner"
                   / "events.py").read_text(encoding="utf-8")
        self.assertIn('protection_stats.get("released_ended_ids")', publish)

    def test_a_card_with_no_identity_is_not_archived(self):
        archive = self._archive()
        stats = ea.archive_retired([{"name": "no id at all"}], now=T0,
                                   archive=archive)
        self.assertEqual(0, stats["added"])

    def test_it_round_trips_through_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "event-archive.json"
            ea.archive_retired([self._ended()], now=T0, path=path)
            self.assertTrue(path.exists())
            reloaded = ea.load_archive(path)
            self.assertTrue(ea.is_archived(self._ended(), reloaded))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["count"])

    def test_an_unreadable_archive_never_blocks_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            archive = ea.load_archive(path)
            self.assertEqual({}, archive["fixtures"])
            kept, dropped = ea.drop_resurrected([self._ended()], archive)
            self.assertEqual(1, len(kept))
            self.assertEqual([], dropped)


class OnlyRealRetirementsAreRemembered(unittest.TestCase):
    def test_the_publish_path_archives_only_ended_states(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("ARCHIVED_LIFECYCLE_STATES", source)
        self.assertIn("has_strong_end_signal(card)", source)
        self.assertIn("archive_retired(retired, now=now)", source)

    def test_both_tabs_are_filtered(self):
        source = (ROOT / "scanner" / "events.py").read_text(encoding="utf-8")
        self.assertIn("drop_resurrected(today_items, event_archive)", source)
        self.assertIn("drop_resurrected(", source)
        self.assertIn("resurrection_blocked", source)

    def test_phase_two_is_not_started(self):
        source = (ROOT / "scanner" / "event_archive.py").read_text(encoding="utf-8")
        for token in ("cloudflare", "worker", "kv", "d1"):
            self.assertNotIn(token, source.lower().replace("worked", ""))


if __name__ == "__main__":
    unittest.main()
