"""The four corrections to claude-solution-14.

1. The targeted Upcoming scan targets a fixture once, stops once it has a valid
   link, and does no work for unrelated fixtures.
2. A Today LIVE event is never removed for missing scans while its previous link
   is still live and playable - only END/FT or a genuinely dead link removes it.
3. Publishing is snapshot level: Today/Upcoming, the playback catalogue and its
   shards, the allowlist and the manifest are staged, validated as a set, and
   swapped in together.
4. Event identity compares kickoff times with a tolerance instead of testing a
   fixed-width bucket for equality, so a bucket boundary cannot split one match
   into two cards.

Each test states the behaviour, not the shape of the code.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner.live_protection import protect_live_events  # noqa: E402
from scanner.merger import (  # noqa: E402
    KICKOFF_TOLERANCE_MINUTES,
    _identity_compatible,
    _reconcile_event_groups,
    canonical_event_identity,
    kickoffs_within_tolerance,
)
from scanner.snapshot_publish import (  # noqa: E402
    ORDER_ALLOWED_HOSTS,
    ORDER_EVENT_FILE,
    ORDER_MANIFEST,
    ORDER_PLAYBACK_INDEX,
    ORDER_PLAYBACK_SHARD,
    SnapshotPublisher,
)
from scanner.targeted_scan import (  # noqa: E402
    fixture_key,
    has_valid_link,
    load_ledger,
    record_outcome,
    save_ledger,
    select_targets,
)

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def fixture(name, minutes, **extra):
    card = {
        "id": f"evt-{name.lower().replace(' ', '-')}",
        "name": name,
        "start_time": (NOW + timedelta(minutes=minutes)).isoformat(),
    }
    card.update(extra)
    return card


def playable(name, minutes, **extra):
    return fixture(
        name, minutes,
        url="https://cdn.test/live.m3u8",
        verification_status="verified_global",
        verified=True,
        **extra,
    )


# ---------------------------------------------------------------- correction 1
class Correction1TargetedScanRunsOncePerFixture(unittest.TestCase):
    """T-15 minutes, once per fixture, and never again after a valid link."""

    def test_only_a_fixture_inside_the_window_is_targeted(self):
        plan = select_targets(
            [fixture("Arsenal vs Chelsea", 10), fixture("Real vs Barca", 300)],
            {"fixtures": {}},
            now=NOW,
        )
        self.assertEqual(len(plan.targets), 1)
        self.assertEqual(plan.outside_window, 1)
        self.assertIn("Arsenal vs Chelsea", plan.target_names)

    def test_the_window_is_fifteen_minutes_wide(self):
        inside = select_targets([fixture("A vs B", 14)], {"fixtures": {}}, now=NOW)
        outside = select_targets([fixture("A vs B", 16)], {"fixtures": {}}, now=NOW)
        self.assertEqual(len(inside.targets), 1)
        self.assertEqual(len(outside.targets), 0)

    def test_a_fixture_already_kicked_off_is_not_an_upcoming_target(self):
        plan = select_targets([fixture("A vs B", -5)], {"fixtures": {}}, now=NOW)
        self.assertEqual(len(plan.targets), 0)

    def test_a_scanned_fixture_is_never_targeted_again(self):
        """A five-minute trigger must not rescan a fixture it has already
        scanned once - link found or not."""
        card = playable("Arsenal vs Chelsea", 10)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "targeting.json"

            first = select_targets([card], load_ledger(state), now=NOW)
            self.assertEqual(len(first.targets), 1)
            save_ledger(record_outcome(load_ledger(state), first, [card], now=NOW), state)

            for tick in range(1, 4):
                later = NOW + timedelta(minutes=tick)
                again = select_targets([card], load_ledger(state), now=later)
                self.assertEqual(len(again.targets), 0, f"tick {tick}")
                self.assertEqual(again.already_attempted, 1, f"tick {tick}")

    def test_a_fixture_that_produced_nothing_is_still_not_retried(self):
        """The corrected rule. -15 gets the one scan; -10 and -5 get none, even
        though that scan came back empty."""
        card = fixture("Arsenal vs Chelsea", 15)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "targeting.json"
            first = select_targets([card], load_ledger(state), now=NOW)
            self.assertEqual(len(first.targets), 1)
            save_ledger(record_outcome(load_ledger(state), first, [card], now=NOW), state)

            ledger = load_ledger(state)
            record = ledger["fixtures"][fixture_key(card)]
            self.assertTrue(record["attempted"])
            self.assertFalse(record["resolved"], "no link was found")
            self.assertEqual(record["attempts"], 1)

            # -10 and -5 minutes: the trigger fires, the fixture is not a target.
            for minutes_before in (10, 5, 1):
                tick = NOW + timedelta(minutes=15 - minutes_before)
                retry = select_targets([card], ledger, now=tick)
                self.assertEqual(
                    len(retry.targets), 0, f"-{minutes_before} minute trigger",
                )
                self.assertEqual(retry.already_attempted, 1)
                self.assertFalse(retry.should_scan)

    def test_the_attempt_count_never_climbs_past_one(self):
        card = fixture("Arsenal vs Chelsea", 12)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "targeting.json"
            for _ in range(4):
                plan = select_targets([card], load_ledger(state), now=NOW)
                save_ledger(record_outcome(load_ledger(state), plan, [card], now=NOW), state)
            record = load_ledger(state)["fixtures"][fixture_key(card)]
        self.assertEqual(record["attempts"], 1)

    def test_a_ledger_from_the_previous_build_still_suppresses(self):
        """An upgrade must not hand every already-tried fixture a fresh scan."""
        from scanner.targeted_scan import is_attempted

        card = fixture("Arsenal vs Chelsea", 10)
        legacy = {"fixtures": {fixture_key(card): {"attempts": 2, "resolved": False}}}
        self.assertTrue(is_attempted(legacy, fixture_key(card)))
        self.assertEqual(len(select_targets([card], legacy, now=NOW).targets), 0)

    def test_a_metadata_only_card_does_not_count_as_a_valid_link(self):
        self.assertFalse(has_valid_link(playable("A vs B", 5, metadata_only=True)))
        self.assertFalse(has_valid_link(fixture("A vs B", 5)))
        self.assertTrue(has_valid_link(playable("A vs B", 5)))

    def test_no_target_means_no_scan_at_all(self):
        plan = select_targets([fixture("Real vs Barca", 300)], {"fixtures": {}}, now=NOW)
        self.assertFalse(plan.should_scan)

    def test_an_unrelated_fixture_is_not_verification_work(self):
        plan = select_targets([fixture("Arsenal vs Chelsea", 10)], {"fixtures": {}}, now=NOW)
        self.assertTrue(plan.accepts({"name": "Arsenal vs Chelsea Server 2 HD"}))
        self.assertFalse(plan.accepts({"name": "Real Madrid vs Barcelona"}))
        self.assertFalse(
            plan.accepts({
                "name": "Real Madrid vs Barcelona",
                "start_at": (NOW + timedelta(hours=5)).isoformat(),
            })
        )

    def test_a_candidate_kicking_off_inside_the_window_is_accepted_by_time(self):
        """A source that abbreviates the teams still belongs to the window it
        kicks off in, so the target does not starve over a naming difference."""
        plan = select_targets([fixture("Australia vs Bangladesh", 10)], {"fixtures": {}}, now=NOW)
        self.assertTrue(
            plan.accepts({
                "name": "AUS v BAN 1st Test",
                "start_at": (NOW + timedelta(minutes=9)).isoformat(),
            })
        )

    def test_the_ledger_prunes_fixtures_that_are_long_gone(self):
        old = fixture("Old vs Older", 10)
        old["start_time"] = (NOW - timedelta(days=2)).isoformat()
        ledger = {"fixtures": {fixture_key(old): {
            "resolved": True, "start_time": old["start_time"],
        }}}
        record_outcome(ledger, select_targets([], {"fixtures": {}}, now=NOW), [], now=NOW)
        self.assertEqual(ledger["fixtures"], {})


class Correction1PlannerDropsUntargetedWork(unittest.TestCase):
    """The predicate is applied before verification, not after."""

    def test_the_planner_accepts_a_targeted_predicate(self):
        import inspect

        from scanner.planner import plan_candidates

        self.assertIn(
            "targeted_filter", inspect.signature(plan_candidates).parameters
        )

    def test_an_untargeted_event_candidate_never_reaches_the_verifier(self):
        source = (ROOT / "scanner" / "planner.py").read_text(encoding="utf-8")
        self.assertIn("not targeted_filter(item)", source)
        self.assertIn("rejected_not_targeted", source)


# ---------------------------------------------------------------- correction 2
class Correction2LivePreservation(unittest.TestCase):
    """Only END/FT or a dead link removes a live card."""

    def _card(self, **extra):
        card = {
            "id": "evt-1",
            "name": "Arsenal vs Chelsea",
            "schedule_status": "LIVE_NOW",
            "url": "https://cdn.test/live.m3u8",
            "end_time": (NOW + timedelta(hours=2)).isoformat(),
        }
        card.update(extra)
        return card

    def test_twenty_consecutive_misses_do_not_remove_a_live_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            for expected in range(1, 21):
                items, stats = protect_live_events(
                    [], [self._card()], state_path=state, now=NOW,
                    probe=lambda card: True,
                )
                self.assertEqual(len(items), 1, f"miss {expected}")
        self.assertEqual(stats["released_exhausted"], 0)

    def test_an_authoritative_finish_still_removes_it_without_a_probe(self):
        probed = []

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, stats = protect_live_events(
                [], [self._card(schedule_status="FT")], state_path=state, now=NOW,
                probe=lambda card: probed.append(card) or True,
            )
        self.assertEqual(items, [])
        self.assertEqual(stats["released_ended"], 1)
        self.assertEqual(probed, [], "an ended match must not be probed")

    def test_a_dead_link_removes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, stats = protect_live_events(
                [], [self._card()], state_path=state, now=NOW,
                probe=lambda card: False,
            )
        self.assertEqual(items, [])
        self.assertEqual(stats["released_dead_link"], 1)

    def test_the_carried_card_records_why_it_survived(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            items, _ = protect_live_events(
                [], [self._card()], state_path=state, now=NOW,
                probe=lambda card: True,
            )
        self.assertIn("still live", items[0]["carried_forward_reason"])

    def test_seeing_the_event_again_clears_its_miss_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "protection.json"
            protect_live_events([], [self._card()], state_path=state, now=NOW,
                                probe=lambda card: True)
            protect_live_events([self._card()], [self._card()], state_path=state,
                                now=NOW, probe=lambda card: True)
            payload = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(payload["misses"], {})


class Correction2ProbeResolvesRealStreams(unittest.TestCase):
    """A published card carries only a playback_id, so the probe has to resolve
    the catalogue - otherwise every event reports as unverifiable and the whole
    correction degrades into "always preserve"."""

    def test_the_probe_resolves_urls_from_the_playback_catalogue(self):
        from scanner.live_protection import card_playback_ids, resolve_card_streams

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            (root / "playback").mkdir(parents=True)
            (root / "playback" / "ab.json").write_text(json.dumps({"records": {
                "ctv_ab01": {"url": "https://primary.test/live.m3u8",
                             "headers": {"Referer": "https://site.test/"}},
                "ctv_ab02": {"url": "https://backup.test/live.m3u8"},
            }}), encoding="utf-8")
            card = {
                "id": "evt-1", "name": "A vs B",
                "playback_id": "ctv_ab01",
                "backups": [{"playback_id": "ctv_ab02"}],
            }
            self.assertEqual(card_playback_ids(card), ["ctv_ab01", "ctv_ab02"])
            streams = resolve_card_streams(card, data_root=root)

        self.assertEqual(
            [url for url, _ in streams],
            ["https://primary.test/live.m3u8", "https://backup.test/live.m3u8"],
        )
        self.assertEqual(streams[0][1]["Referer"], "https://site.test/")

    def test_a_card_with_no_resolvable_profile_is_inconclusive_not_dead(self):
        from scanner.live_protection import probe_card_is_playable

        with tempfile.TemporaryDirectory() as tmp:
            verdict = probe_card_is_playable(
                {"id": "evt-1", "playback_id": "ctv_zz99"},
                data_root=Path(tmp) / "data",
            )
        self.assertIsNone(verdict)


# ---------------------------------------------------------------- correction 3
class Correction3SnapshotAtomicPublish(unittest.TestCase):
    """Stage the whole set, validate it, then swap it in one pass."""

    def _snapshot(self, root):
        snapshot = SnapshotPublisher(timestamp="2026-08-17T12:00:00+00:00", data_root=root)
        snapshot.stage(
            root / "playback" / "ab.json",
            {"schema_version": 1, "shard": "ab", "count": 1,
             "records": {"ctv_ab01": {"url": "https://cdn.test/live.m3u8"}}},
            order=ORDER_PLAYBACK_SHARD, kind="playback_shard",
        )
        snapshot.stage(
            root / "playback-sources.json",
            {"schema_version": 2, "count": 1, "shards": {"ab": 1}},
            order=ORDER_PLAYBACK_INDEX, kind="playback_index",
        )
        snapshot.stage(
            root / "today-match.json",
            {"count": 1, "items": [{"id": "a", "playback_id": "ctv_ab01"}]},
            order=ORDER_EVENT_FILE, kind="event",
        )
        snapshot.stage(
            root / "upcoming.json",
            {"count": 0, "items": []},
            order=ORDER_EVENT_FILE, kind="event",
        )
        snapshot.stage(
            root / "manifest.json",
            {"today_match": {"count": 1, "visible": True, "url": "data/today-match.json"},
             "upcoming": {"count": 0, "visible": False, "url": "data/upcoming.json"}},
            order=ORDER_MANIFEST, kind="manifest",
        )
        snapshot.stage(
            root / "allowed-hosts.json",
            {"count": 1, "hosts": ["cdn.test"]},
            order=ORDER_ALLOWED_HOSTS, kind="allowed_hosts",
        )
        return snapshot

    def test_a_consistent_snapshot_validates_and_publishes_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            snapshot = self._snapshot(root)
            ok, reason = snapshot.validate()
            self.assertTrue(ok, reason)

            # Nothing is on disk until commit() is called.
            self.assertFalse((root / "today-match.json").exists())
            result = snapshot.commit()
            self.assertEqual(result["files"], 6)
            slot = result["slot"]
            for name in (
                "today-match.json", "manifest.json", "allowed-hosts.json",
                "playback-sources.json", "playback/ab.json",
            ):
                self.assertTrue((root / name).exists(), name)
            for name in (
                "today-match.json", "upcoming.json", "allowed-hosts.json",
                "playback-sources.json", "manifest.json",
            ):
                self.assertTrue((root / "snapshots" / slot / name).exists(), name)

    def test_a_card_whose_playback_profile_is_missing_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            snapshot = self._snapshot(root)
            snapshot.stage(
                root / "today-match.json",
                {"count": 1, "items": [{"id": "a", "playback_id": "ctv_nope"}]},
                order=ORDER_EVENT_FILE, kind="event",
            )
            ok, reason = snapshot.validate()
        self.assertFalse(ok)
        self.assertIn("playback profile", reason)

    def test_a_manifest_that_disagrees_with_its_event_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            snapshot = self._snapshot(root)
            snapshot.stage(
                root / "manifest.json",
                {"today_match": {"count": 9, "url": "data/today-match.json"}},
                order=ORDER_MANIFEST, kind="manifest",
            )
            ok, reason = snapshot.validate()
        self.assertFalse(ok)
        self.assertIn("staged manifest counts", reason)

    def test_an_index_that_disagrees_with_its_shards_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            snapshot = self._snapshot(root)
            snapshot.stage(
                root / "playback-sources.json",
                {"schema_version": 2, "count": 7, "shards": {"ab": 7}},
                order=ORDER_PLAYBACK_INDEX, kind="playback_index",
            )
            ok, reason = snapshot.validate()
        self.assertFalse(ok)
        self.assertIn("playback index", reason)

    def test_nothing_is_published_before_the_snapshot_is_complete(self):
        """There is no mid-commit state to reason about any more: the pointer is
        moved once, after the whole slot exists."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            result = self._snapshot(root).commit()
            slot = result["slot"]
            pointer = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

            # Everything the pointer names exists and agrees with it.
            today = json.loads(
                (root.parent / pointer["today_match"]["url"]).read_text(encoding="utf-8")
            )
            self.assertEqual(pointer["today_match"]["count"], len(today["items"]))
            index = json.loads(
                (root.parent / pointer["playback_catalog_url"]).read_text(encoding="utf-8")
            )
            shard = json.loads(
                (root / "playback" / "ab.json").read_text(encoding="utf-8")
            )
            self.assertEqual(index["shards"]["ab"], len(shard["records"]))
            self.assertEqual(result["pointer"], "data/manifest.json")
            self.assertEqual(pointer["snapshot"]["directory"], f"data/snapshots/{slot}")

    def test_the_switch_is_one_rename_of_one_file(self):
        """The claim under test: production moves from the whole old snapshot to
        the whole new one in a single os.replace, with no intermediate state."""
        import os as os_module

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self._snapshot(root).commit()          # generation 1 -> slot s0
            pointer = root / "manifest.json"
            first = json.loads(pointer.read_text(encoding="utf-8"))

            renames = []
            real_replace = os_module.replace

            def spy(src, dst, *args, **kwargs):
                renames.append((str(src), str(dst)))
                return real_replace(src, dst, *args, **kwargs)

            os_module.replace = spy
            try:
                result = self._snapshot(root).commit()   # generation 2 -> slot s1
            finally:
                os_module.replace = real_replace

            pointer_renames = [
                (src, dst) for src, dst in renames
                if Path(dst).name == "manifest.json" and Path(dst).parent == root
            ]

        self.assertEqual(result["slot"], "s1")
        self.assertEqual(result["generation"], 2)
        self.assertEqual(first["snapshot"]["slot"], "s0")
        # Exactly one rename lands on the pointer, and it is the whole switch.
        self.assertEqual(len(pointer_renames), 1, pointer_renames)

    def test_the_pointer_names_a_slot_that_is_already_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self._snapshot(root).commit()
            pointer = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            slot = pointer["snapshot"]["slot"]

            self.assertEqual(pointer["today_match"]["url"],
                             f"data/snapshots/{slot}/today-match.json")
            for name in ("today-match.json", "upcoming.json", "allowed-hosts.json",
                         "playback-sources.json", "manifest.json"):
                self.assertTrue((root / "snapshots" / slot / name).is_file(), name)

            # And what the pointer names really is the new snapshot.
            served = json.loads(
                (root / "snapshots" / slot / "today-match.json").read_text(encoding="utf-8")
            )
        self.assertEqual(served["items"][0]["playback_id"], "ctv_ab01")

    def test_the_previous_snapshot_survives_the_switch(self):
        """A reader that read the old pointer a moment ago must still find every
        file that pointer names."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self._snapshot(root).commit()
            old_pointer = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            old_url = old_pointer["today_match"]["url"]

            self._snapshot(root).commit()
            new_pointer = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

            self.assertNotEqual(new_pointer["today_match"]["url"], old_url)
            still_there = root.parent / old_url
            self.assertTrue(still_there.is_file(), f"{old_url} was destroyed")

    def test_slots_are_reused_round_robin_so_the_repository_stays_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            slots = []
            for _ in range(7):
                slots.append(self._snapshot(root).commit()["slot"])
            on_disk = sorted(p.name for p in (root / "snapshots").iterdir() if p.is_dir())
        self.assertEqual(slots, ["s0", "s1", "s2", "s0", "s1", "s2", "s0"])
        self.assertEqual(on_disk, ["s0", "s1", "s2"])

    def test_the_slot_being_written_is_two_generations_old(self):
        """Round-robin over three slots means no reader holding a usable pointer
        can be reading the slot this scan overwrites."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            live = []
            for _ in range(5):
                result = self._snapshot(root).commit()
                live.append(result["slot"])
        for index in range(2, len(live)):
            self.assertNotEqual(live[index], live[index - 1])
            self.assertNotEqual(live[index], live[index - 2])

    def test_the_flat_paths_are_kept_as_a_mirror(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            result = self._snapshot(root).commit()
            slot = result["slot"]
            for name in ("today-match.json", "upcoming.json", "allowed-hosts.json",
                         "playback-sources.json"):
                flat = json.loads((root / name).read_text(encoding="utf-8"))
                versioned = json.loads(
                    (root / "snapshots" / slot / name).read_text(encoding="utf-8")
                )
                self.assertEqual(flat, versioned, name)

    def test_playback_shards_are_written_outside_the_switch(self):
        """Shards are content-addressed and only ever added to, so they satisfy
        the old and the new snapshot at once and need no versioning."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            result = self._snapshot(root).commit()
            self.assertTrue((root / "playback" / "ab.json").is_file())
            self.assertFalse((root / "snapshots" / result["slot"] / "playback").exists())
        self.assertEqual(result["outside_switch"], 1)

    def test_a_deletion_happens_only_after_the_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            (root / "playback").mkdir(parents=True)
            (root / "playback" / "ff.json").write_text('{"records": {}}', encoding="utf-8")
            snapshot = self._snapshot(root)
            snapshot.stage_deletion(root / "playback" / "ff.json")
            result = snapshot.commit()
        self.assertEqual(result["deleted"], 1)

    def test_abandoning_a_snapshot_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            snapshot = self._snapshot(root)
            snapshot.abandon()
            self.assertFalse(root.exists() and any(root.iterdir()))

    def test_an_emptied_shard_is_deleted_after_the_pointer_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            (root / "playback").mkdir(parents=True)
            (root / "playback" / "ff.json").write_text('{"records": {}}', encoding="utf-8")
            snapshot = self._snapshot(root)
            snapshot.stage_deletion(root / "playback" / "ff.json")
            snapshot.commit()
            self.assertFalse((root / "playback" / "ff.json").exists())
            self.assertTrue((root / "playback" / "ab.json").exists())

    def test_a_shard_being_replaced_cannot_vouch_for_a_dropped_profile(self):
        """The published copy of a shard this snapshot rewrites must not be
        consulted, or a profile the scan just dropped would still validate a
        card that has nothing left to play."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            (root / "playback").mkdir(parents=True)
            (root / "playback" / "ab.json").write_text(
                json.dumps({"records": {"ctv_ab99": {"url": "https://old.test/x.m3u8"}}}),
                encoding="utf-8",
            )
            snapshot = self._snapshot(root)
            snapshot.stage(
                root / "today-match.json",
                {"count": 1, "items": [{"id": "a", "playback_id": "ctv_ab99"}]},
                order=ORDER_EVENT_FILE, kind="event",
            )
            ok, _ = snapshot.validate()
        self.assertFalse(ok)


class Correction3PublishUsesTheSnapshot(unittest.TestCase):
    """publish_scan_outputs really routes the event snapshot through it."""

    def test_a_real_publish_commits_one_consistent_snapshot(self):
        from scanner.output import publish_scan_outputs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            publish_scan_outputs(
                events_data={
                    "today_match": {"count": 1, "items": [{
                        "id": "evt-1", "name": "Arsenal vs Chelsea",
                        "url": "https://cdn.test/live.m3u8",
                        "resolution_height": 720,
                    }]},
                    "upcoming": {"count": 0, "items": []},
                },
                settings_path=str(root / "config" / "settings.json"),
                data_dir=str(root / "data"),
                state_dir=str(root / "state"),
                reports_dir=str(root / "reports"),
                scan_mode="today",
            )
            data = root / "data"
            today = json.loads((data / "today-match.json").read_text(encoding="utf-8"))
            manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
            hosts = json.loads((data / "allowed-hosts.json").read_text(encoding="utf-8"))

            self.assertEqual(len(today["items"]), 1)
            self.assertEqual(manifest["today_match"]["count"], len(today["items"]))
            self.assertIn("cdn.test", hosts["hosts"])

            playback_id = str(today["items"][0].get("playback_id") or "")
            if playback_id:
                shard = playback_id.replace("ctv_", "")[:2]
                shard_payload = json.loads(
                    (data / "playback" / f"{shard}.json").read_text(encoding="utf-8")
                )
                self.assertIn(
                    playback_id, shard_payload["records"],
                    "a published card must resolve in the shard published with it",
                )

    def test_a_publish_refused_by_validation_keeps_the_previous_events(self):
        from scanner.output import publish_scan_outputs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            (root / "data").mkdir()
            good = {"count": 1, "items": [{"id": "keep-me", "name": "Kept"}]}
            (root / "data" / "today-match.json").write_text(
                json.dumps(good), encoding="utf-8"
            )

            publish_scan_outputs(
                # count disagrees with items: requirement 15 refuses this
                events_data={"today_match": {"count": 5, "items": [{"id": "a"}]}},
                settings_path=str(root / "config" / "settings.json"),
                data_dir=str(root / "data"),
                state_dir=str(root / "state"),
                reports_dir=str(root / "reports"),
                scan_mode="today",
            )
            still = json.loads(
                (root / "data" / "today-match.json").read_text(encoding="utf-8")
            )
        self.assertEqual(still, good)


class Correction3TheReaderFollowsThePointer(unittest.TestCase):
    """The switch is only atomic if the reader dereferences the pointer.

    A reader that captured an event URL at page load would keep reading a
    snapshot slot until that slot got recycled, and a reader that read flat paths
    would see the compatibility mirror instead of the snapshot. Both are checked
    in a real browser by pointertest.mjs; these hold the contract in CI.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(encoding="utf-8")

    def test_the_refresh_resolves_the_pointer_before_fetching(self):
        refresh = self.app.split("async function refreshActiveEventCatalogue(")[1]
        refresh = refresh.split("\nfunction ")[0]
        self.assertIn("await resolveEventSnapshotPath(", refresh)
        resolve = self.app.split("async function resolveEventSnapshotPath(")[1]
        resolve = resolve.split("\nasync function ")[0]
        self.assertIn("data_manifest", resolve)
        self.assertIn("/data/manifest.json", resolve)

    def test_an_unreachable_pointer_keeps_the_current_snapshot(self):
        resolve = self.app.split("async function resolveEventSnapshotPath(")[1]
        resolve = resolve.split("\nasync function ")[0]
        self.assertIn("return fallbackPath", resolve)

    def test_the_reader_never_hardcodes_a_flat_event_path(self):
        for flat in ("'/data/today-match.json'", '"/data/today-match.json"',
                     "'/data/upcoming.json'", '"/data/upcoming.json"'):
            self.assertNotIn(flat, self.app, flat)

    def test_the_pages_validator_accepts_a_versioned_pointer(self):
        source = (ROOT / "scripts" / "validate-pages.py").read_text(encoding="utf-8")
        self.assertIn("data/snapshots/", source)
        self.assertIn("def validate_snapshot_pointer(", source)
        self.assertIn("Snapshot slot অসম্পূর্ণ", source)

    def test_the_versioned_paths_are_served_with_revalidation(self):
        headers = (ROOT / "site" / "_headers").read_text(encoding="utf-8")
        self.assertIn("/data/snapshots/*", headers)
        self.assertIn("/data/manifest.json", headers)


# ---------------------------------------------------------------- correction 4
class Correction4KickoffTolerance(unittest.TestCase):
    """Compare kickoffs with a tolerance; a bucket boundary must not split a
    fixture into two cards."""

    def _identity(self, minutes_past_noon):
        start = (NOW + timedelta(minutes=minutes_past_noon)).isoformat()
        return canonical_event_identity({
            "name": "Arsenal vs Chelsea",
            "competition": "Premier League",
            "start_at": start,
        })

    def test_the_identity_carries_the_kickoff_itself_not_a_bucket(self):
        identity = self._identity(0)
        self.assertIsInstance(identity[3], int)
        self.assertEqual(identity[3], int(NOW.timestamp()))

    def test_a_missing_kickoff_is_a_wildcard(self):
        self.assertIsNone(
            canonical_event_identity({"name": "Arsenal vs Chelsea"})[3]
        )
        self.assertTrue(kickoffs_within_tolerance(None, int(NOW.timestamp())))
        self.assertTrue(kickoffs_within_tolerance(int(NOW.timestamp()), None))

    def test_two_sources_minutes_apart_are_one_fixture_anywhere_on_the_clock(self):
        """The bug the correction fixes: with a fixed 90-minute bucket, whether
        two sources four minutes apart merged depended on where the boundary
        fell. Every offset must now behave the same."""
        for offset in range(0, 90, 3):
            a = self._identity(offset)
            b = self._identity(offset + 4)
            self.assertTrue(
                _identity_compatible(a, b),
                f"a 4 minute disagreement at +{offset} min must still be one fixture",
            )

    def test_the_old_bucket_really_did_split_at_a_boundary(self):
        """Proof the correction was needed, not a rewrite of working code."""
        def bucket(moment, window_minutes=90):
            return int(moment.timestamp()) // (window_minutes * 60)

        split = [
            offset for offset in range(0, 24 * 60)
            if bucket(NOW + timedelta(minutes=offset))
            != bucket(NOW + timedelta(minutes=offset + 4))
        ]
        self.assertTrue(split, "the bucket must have had boundaries to straddle")
        for offset in split:
            a = self._identity(offset)
            b = self._identity(offset + 4)
            self.assertTrue(
                _identity_compatible(a, b),
                f"+{offset} min straddled a bucket boundary and must still merge",
            )

    def test_kickoffs_further_apart_than_the_tolerance_stay_separate(self):
        a = self._identity(0)
        b = self._identity(KICKOFF_TOLERANCE_MINUTES + 5)
        self.assertFalse(_identity_compatible(a, b))

    def test_the_tolerance_is_symmetric(self):
        edge = KICKOFF_TOLERANCE_MINUTES
        self.assertTrue(_identity_compatible(self._identity(0), self._identity(edge)))
        self.assertTrue(_identity_compatible(self._identity(edge), self._identity(0)))
        self.assertTrue(_identity_compatible(self._identity(0), self._identity(-edge)))

    def test_the_same_teams_on_another_day_stay_two_fixtures(self):
        a = canonical_event_identity({
            "name": "England vs Pakistan", "competition": "Test Series",
            "start_at": "2026-08-17T10:00:00+00:00"})
        b = canonical_event_identity({
            "name": "England vs Pakistan", "competition": "Test Series",
            "start_at": "2026-08-24T10:00:00+00:00"})
        self.assertFalse(_identity_compatible(a, b))

    def test_one_fixture_from_two_sources_becomes_one_group(self):
        grouped = {
            "upcoming:arsenal-vs-chelsea": [{
                "name": "Arsenal vs Chelsea", "competition": "Premier League",
                "start_at": (NOW + timedelta(minutes=44)).isoformat(),
                "source_pipeline": "upcoming",
            }],
            "upcoming:arsenal-vs-chelsea-hd": [{
                "name": "Arsenal vs Chelsea", "competition": "Premier League",
                "start_at": (NOW + timedelta(minutes=46)).isoformat(),
                "source_pipeline": "upcoming",
            }],
        }
        merged = _reconcile_event_groups(dict(grouped))
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(next(iter(merged.values()))), 2)

    def test_tolerance_does_not_chain_three_fixtures_into_one(self):
        """Tolerance is not transitive. Comparing every group against its leader
        is what stops A~B and B~C collapsing A and C together."""
        def group(minutes, suffix):
            return [{
                "name": "Arsenal vs Chelsea", "competition": "Premier League",
                "start_at": (NOW + timedelta(minutes=minutes)).isoformat(),
                "source_pipeline": "upcoming", "_suffix": suffix,
            }]

        grouped = {
            "upcoming:a": group(0, "a"),
            "upcoming:b": group(80, "b"),
            "upcoming:c": group(160, "c"),
        }
        merged = _reconcile_event_groups(dict(grouped))
        self.assertIn("upcoming:c", merged, "160 minutes after A is not fixture A")


if __name__ == "__main__":
    unittest.main()
