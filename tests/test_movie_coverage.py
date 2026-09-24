"""ধাপ ১ - the NO-LOSS COVERAGE GATE, checked against the rules it enforces.

Every test here names the clause of ধারা ৪.০ it holds in place, because the
plan went through five review rounds and most of these rules exist because an
earlier version of the plan got them wrong. A test that only says "it counts
correctly" would not stop the next version getting them wrong again.

The four corrections with the most weight behind them:

  * category may NEVER put an entry out of scope (v৩.৪, correction 2)
  * 403 / geo / timeout / repeated failure is NEVER definitive (v৩.২)
  * confirmed_unavailable is NOT a sixth bucket (v৩.৩)
  * a quarantine spike WARNs, it does not BLOCK (v৩.১)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scanner import movie_coverage as mc  # noqa: E402


def _entry(**overrides):
    entry = {
        "source_id": "sm-movie-combined",
        "name": "Hawa (2022)",
        "url": "https://cdn.test/movies/hawa-2022-1080p.mkv",
        "source_pipeline": "movies",
        "year": 2022,
    }
    entry.update(overrides)
    return entry


def _card(identity="hawa-2022", name="Hawa (2022)",
          url="https://cdn.test/movies/hawa-2022-1080p.mkv",
          backups=(), source_id="sm-movie-combined", **extra):
    card = {
        "id": identity,
        "name": name,
        "url": url,
        "source_id": source_id,
        "backups": [
            {"url": backup, "source_id": source_id} for backup in backups
        ],
        "year": 2022,
    }
    card.update(extra)
    return card


def _inventory(*cards_with_scope):
    """The `kind|scope|identity|url` lines ধাপ ০ writes and commits."""
    lines = []
    for scope, card in cards_with_scope:
        lines.append("movie|%s|%s|%s" % (scope, card["id"], card["url"]))
        for backup in card.get("backups") or ():
            lines.append("movie|%s|%s|%s" % (scope, card["id"], backup["url"]))
    return sorted(set(lines))


class ScopeTests(unittest.TestCase):
    """ধারা ৪.০ - out_of_scope is exactly three things and nothing else."""

    def test_an_unknown_category_never_puts_an_entry_out_of_scope(self) -> None:
        """v৩.৪ correction 2, the most important one in the whole revision.

        v৩.৩ said an entry outside the category allowlist was out of scope.
        A real, playing film whose only fault is a wrong tag in the feed would
        then vanish from the accounting - the exact loss this gate exists to
        prevent.
        """
        for category in ("Punjabi", "", "totally-unknown", "Mix", None):
            with self.subTest(category=category):
                in_scope, reason = mc.classify_scope(
                    _entry(category=category, group_title=category))
                self.assertTrue(in_scope, msg=reason)
                self.assertEqual(reason, "")

    def test_a_disabled_source_is_out_of_scope(self) -> None:
        in_scope, reason = mc.classify_scope(_entry(), source_enabled=False)
        self.assertFalse(in_scope)
        self.assertEqual(reason, mc.OUT_OF_SCOPE_DISABLED)

    def test_a_disabled_entry_is_out_of_scope(self) -> None:
        in_scope, reason = mc.classify_scope(_entry(enabled=False))
        self.assertFalse(in_scope)
        self.assertEqual(reason, mc.OUT_OF_SCOPE_DISABLED)

    def test_a_record_with_no_url_or_no_name_is_out_of_scope(self) -> None:
        for broken in ({"url": ""}, {"name": ""}, {"url": "not-a-url"}):
            with self.subTest(broken=broken):
                in_scope, reason = mc.classify_scope(_entry(**broken))
                self.assertFalse(in_scope)
                self.assertEqual(reason, mc.OUT_OF_SCOPE_INVALID)

    def test_a_live_channel_inside_a_movie_source_is_out_of_scope(self) -> None:
        """The router's "this is not a movie" answer, and only that answer."""
        in_scope, reason = mc.classify_scope(_entry(
            name="Somoy TV",
            url="https://cdn.test/live/somoy/index.m3u8",
            source_id="toffee tv",
            original_source_pipeline="tv",
        ))
        self.assertFalse(in_scope)
        self.assertEqual(reason, mc.OUT_OF_SCOPE_NON_MOVIE)

    def test_only_three_reasons_exist(self) -> None:
        self.assertEqual(len(mc.OUT_OF_SCOPE_REASONS), 3)


class EntryIdentityTests(unittest.TestCase):
    """ধারা ৪.০ - "entry identity — শুধু URL যথেষ্ট নয়"."""

    def test_the_same_url_under_two_header_profiles_is_two_entries(self) -> None:
        """Deduping on URL alone is how a working backup gets thrown away."""
        one = _entry(header_profile="android_tv")
        two = _entry(header_profile="web_referer")
        self.assertNotEqual(mc.entry_key(one), mc.entry_key(two))

    def test_the_same_stream_from_two_sources_is_two_entries(self) -> None:
        self.assertNotEqual(
            mc.entry_key(_entry(source_id="sm-movie-combined")),
            mc.entry_key(_entry(source_id="hopeful-research-latest")),
        )

    def test_a_re_signed_token_is_a_version_not_a_new_entry(self) -> None:
        """"সাময়িক টোকেন/query আলাদা সংস্করণ, আলাদা entry নয়"."""
        self.assertEqual(
            mc.stream_family("https://cdn.test/a/b.mkv?token=aaa&expires=1"),
            mc.stream_family("https://cdn.test/a/b.mkv?token=bbb&expires=2"),
        )

    def test_a_routing_query_is_kept_so_two_streams_do_not_collapse(self) -> None:
        self.assertNotEqual(
            mc.stream_family("https://cdn.test/play?id=101"),
            mc.stream_family("https://cdn.test/play?id=102"),
        )

    def test_inline_header_suffix_is_not_part_of_the_url(self) -> None:
        self.assertEqual(
            mc.stream_family("https://cdn.test/a.mkv|Referer=https://x.test/"),
            mc.stream_family("https://cdn.test/a.mkv"),
        )
        self.assertNotEqual(
            mc.entry_key(_entry(url="https://cdn.test/a.mkv|Referer=https://x.test/")),
            mc.entry_key(_entry(url="https://cdn.test/a.mkv")),
        )


class InvariantOneTests(unittest.TestCase):
    """ধারা ৪.০ INVARIANT ১ - every entry reaches exactly one destination."""

    def setUp(self) -> None:
        self.sources = [{"id": "sm-movie-combined", "name": "SM", "enabled": True}]
        self.primary = _card(
            backups=["https://cdn.test/movies/hawa-2022-720p.mkv"])
        self.published = [("bangla", self.primary)]

    def _build(self, raw, **kwargs):
        return mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=raw,
            published_movies=kwargs.pop("published", self.published),
            **kwargs,
        )

    def test_a_published_primary_is_published_movie(self) -> None:
        report = self._build([_entry()])
        self.assertEqual(report["invariant_1"]["published_movie"], 1)
        self.assertTrue(report["invariant_1"]["identity_balanced"])

    def test_a_published_backup_is_merged_stream_not_a_loss(self) -> None:
        report = self._build([
            _entry(url="https://cdn.test/movies/hawa-2022-720p.mkv")])
        self.assertEqual(report["invariant_1"]["merged_stream"], 1)
        self.assertEqual(report["invariant_1"]["published_movie"], 0)

    def test_another_stream_of_a_published_film_is_merged_not_quarantined(self) -> None:
        """Folded into a card that is on the site, so nothing was lost."""
        report = self._build([
            _entry(url="https://other-cdn.test/hawa/2022/hawa.mkv")])
        self.assertEqual(report["invariant_1"]["merged_stream"], 1)

    def test_an_episode_published_under_series_is_its_own_bucket(self) -> None:
        episode = _card(identity="bp-s01e01", name="Bachelor Point S01E01",
                        url="https://cdn.test/series/bp-s01e01.mkv")
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[_entry(name="Bachelor Point S01E01",
                                url="https://cdn.test/series/bp-s01e01.mkv")],
            published_movies=[],
            published_episodes=[("data/series/bangla/bp/season-01.json", episode)],
        )
        self.assertEqual(report["invariant_1"]["published_series_episode"], 1)

    def test_an_unpublished_entry_with_no_terminal_evidence_is_quarantined(self) -> None:
        report = self._build([_entry(name="Nobody (2099)",
                                     url="https://cdn.test/movies/nobody.mkv")])
        self.assertEqual(report["invariant_1"]["quarantined_unresolved"], 1)
        row = report["sources"][0]
        self.assertEqual(
            row["quarantined_reasons"],
            {"no_terminal_evidence_and_not_published": 1},
        )

    def test_the_five_buckets_always_add_up_to_the_in_scope_total(self) -> None:
        raw = [
            _entry(),                                                     # published
            _entry(url="https://cdn.test/movies/hawa-2022-720p.mkv"),     # merged
            _entry(name="Nobody (2099)", url="https://cdn.test/n.mkv"),   # quarantined
            _entry(name="Broken", url=""),                                # out of scope
        ]
        report = self._build(raw)
        one = report["invariant_1"]
        self.assertEqual(one["raw_entries"], 4)
        self.assertEqual(one["out_of_scope"], 1)
        self.assertEqual(one["total_in_scope_entries"], 3)
        self.assertEqual(
            one["total_in_scope_entries"],
            sum(one[bucket] for bucket in mc.DISPOSITIONS),
        )
        self.assertTrue(one["identity_balanced"])
        self.assertFalse(report["invariants"]["block"])

    def test_there_are_exactly_five_destinations(self) -> None:
        """v৩.৩ correction 1: confirmed_unavailable is not a sixth bucket."""
        self.assertEqual(len(mc.DISPOSITIONS), 5)
        self.assertNotIn("confirmed_unavailable", mc.DISPOSITIONS)


class DefinitiveRejectionTests(unittest.TestCase):
    """ধারা ৪.০ - the one bucket an entry cannot come back from."""

    def setUp(self) -> None:
        self.sources = [{"id": "sm-movie-combined", "name": "SM", "enabled": True}]
        self.gone = _entry(name="Gone (2001)",
                           url="https://cdn.test/movies/gone.mkv")

    def _build(self, terminal):
        return mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[self.gone],
            published_movies=[],
            terminal_records=terminal,
        )

    def test_a_confirmed_404_is_definitive(self) -> None:
        report = self._build([
            {"url": self.gone["url"], "reason": "confirmed_404_or_410"}])
        self.assertEqual(report["invariant_1"]["definitive_rejected"], 1)
        self.assertFalse(report["invariants"]["block"])

    def test_403_geo_timeout_and_repeated_failure_are_never_definitive(self) -> None:
        """v৩.২ correction 2, with this codebase's own evidence behind it.

        1,312 posters here answer 403 from Bangladesh and 200 from a GitHub
        runner. A stream that fails from our vantage can be perfectly valid
        somewhere else, so it goes to quarantine, never to a hard reject.
        """
        for reason in ("http_403", "geo_blocked", "timeout", "http_451",
                       "multi_vantage_multi_run_failure", "token_expired",
                       "http_503"):
            with self.subTest(reason=reason):
                report = self._build([
                    {"url": self.gone["url"], "reason": reason}])
                self.assertEqual(report["invariant_1"]["definitive_rejected"], 0)
                self.assertEqual(
                    report["invariant_1"]["quarantined_unresolved"], 1)

    def test_only_four_hard_reasons_exist(self) -> None:
        self.assertEqual(len(mc.DEFINITIVE_REJECT_REASONS), 4)

    def test_a_terminal_verdict_never_overrides_a_published_card(self) -> None:
        """A card on the site is not "rejected" whatever a report says."""
        card = _card()
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[_entry()],
            published_movies=[("bangla", card)],
            terminal_records=[
                {"url": card["url"], "reason": "confirmed_404_or_410"}],
        )
        self.assertEqual(report["invariant_1"]["published_movie"], 1)
        self.assertEqual(report["invariant_1"]["definitive_rejected"], 0)


class ABackupThatStopsBeingOfferedIsAMerge(unittest.TestCase):
    """The last matching layer, and the whole of what was left on 2026-09-24.

    `remote-manual-dug-dug-2026` carried three links. Its primary is still
    published - under a DIFFERENT card, because the id changed - and its two
    backups are offered nowhere. Neither the stream layers nor the card-id
    layer could see that, so two backup links on one card blocked a catalogue
    of 1,667 films.

    A link that stopped being offered while the thing it backed up kept playing
    is what ধারা ৪.০ calls `merged_stream`: "একই কনটেন্টের Primary/Backup
    হিসেবে merge". The layer is asked last, after terminal evidence, and it can
    only ever answer for a card that still has a stream somewhere.
    """

    def setUp(self) -> None:
        self.sources = [{"id": "sm-movie-combined", "name": "SM", "enabled": True}]
        self.card = _card(backups=["https://cdn.test/movies/hawa-2022-720p.mkv",
                                   "https://cdn.test/movies/hawa-2022-480p.mkv"])
        self.before = _inventory(("bangla", self.card))

    def _build(self, current_lines, published=(), terminal=()):
        return mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[],
            published_movies=list(published),
            previous_inventory=self.before,
            current_inventory=list(current_lines),
            terminal_records=list(terminal),
        )

    def _survivor(self, url):
        """The primary, republished under a card with a different id."""
        renamed = _card(identity="hawa-2022-remastered", url=url)
        return [("bangla", renamed)], _inventory(("bangla", renamed))

    def test_the_backups_are_merged_rather_than_lost(self):
        published, lines = self._survivor(self.card["url"])
        two = self._build(lines, published)["invariant_2"]
        self.assertEqual(two["unexplained_live_loss"], 0)
        self.assertEqual(two["merged_stream"], 2)
        self.assertEqual(two["match_layers"].get("sibling_stream"), 2)

    def test_the_publish_is_no_longer_blocked_by_them(self):
        published, lines = self._survivor(self.card["url"])
        report = self._build(lines, published)
        blocked, _reasons = mc.publish_blocked(report)
        self.assertFalse(blocked)

    def test_a_card_whose_every_link_vanishes_is_still_a_loss(self):
        """The case this must never swallow."""
        two = self._build([], [])["invariant_2"]
        self.assertEqual(two["unexplained_live_loss"], 3)
        self.assertEqual(two["match_layers"].get("sibling_stream"), None)

    def test_another_cards_surviving_link_does_not_save_it(self):
        """Siblings are the streams of the SAME recorded card, never of any
        card that happens to still be published."""
        other = _card(identity="unrelated-2021",
                      url="https://cdn.test/movies/unrelated.mkv")
        two = self._build(_inventory(("bangla", other)),
                          [("bangla", other)])["invariant_2"]
        self.assertEqual(two["unexplained_live_loss"], 3)

    def test_terminal_evidence_is_still_the_stronger_answer(self):
        """Asked last, so a stream with a confirmed 404 is reported as gone
        rather than dressed up as a merge."""
        published, lines = self._survivor(self.card["url"])
        backup = self.card["backups"][0]["url"]
        two = self._build(lines, published,
                          [{"url": backup, "reason": "confirmed_404_or_410"}])["invariant_2"]
        self.assertEqual(two["terminal_evidence"], 1)
        self.assertEqual(two["match_layers"].get("sibling_stream"), 1)
        self.assertEqual(two["unexplained_live_loss"], 0)

    def test_a_card_still_published_under_its_own_id_never_reaches_this_layer(self):
        """The stronger card-id layer owns that case and says so."""
        kept = _card(url="https://cdn.test/movies/hawa-2022-new.mkv")
        two = self._build(_inventory(("bangla", kept)),
                          [("bangla", kept)])["invariant_2"]
        self.assertEqual(two["unexplained_live_loss"], 0)
        self.assertIn("card_id", two["match_layers"])


class InvariantTwoTests(unittest.TestCase):
    """ধারা ৪.০ INVARIANT ২ - unexplained_live_loss = 0, or publish BLOCKs."""

    def setUp(self) -> None:
        self.sources = [{"id": "sm-movie-combined", "name": "SM", "enabled": True}]
        self.card = _card(backups=["https://cdn.test/movies/hawa-2022-720p.mkv"])
        self.before = _inventory(("bangla", self.card))

    def _build(self, published, raw=(), terminal=()):
        current = _inventory(*published)
        return mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=raw,
            published_movies=published,
            previous_inventory=self.before,
            current_inventory=current,
            terminal_records=terminal,
        )

    def test_a_catalogue_that_did_not_move_loses_nothing(self) -> None:
        report = self._build([("bangla", self.card)])
        two = report["invariant_2"]
        self.assertEqual(two["previously_live"], 2)
        self.assertEqual(two["currently_visible"], 2)
        self.assertEqual(two["unexplained_live_loss"], 0)
        self.assertFalse(report["invariants"]["block"])

    def test_a_stream_that_vanishes_with_no_explanation_blocks_the_publish(self) -> None:
        report = self._build([])
        two = report["invariant_2"]
        self.assertEqual(two["unexplained_live_loss"], 2)
        blocked, reasons = mc.publish_blocked(report)
        self.assertTrue(blocked)
        self.assertTrue(
            any("unexplained_live_loss" in reason for reason in reasons),
            msg=reasons,
        )

    def test_a_card_that_moves_to_series_is_preserved_not_lost(self) -> None:
        """ধাপ ৩ is exactly this move, and it must not read as a loss."""
        episode = _card(identity="hawa-2022", name="Hawa S01E01",
                        url=self.card["url"])
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[],
            published_movies=[],
            published_episodes=[("data/series/bangla/hawa/season-01.json", episode)],
            previous_inventory=self.before,
            current_inventory=[
                "episode|data/series/bangla/hawa/season-01.json|hawa-2022|%s"
                % self.card["url"],
            ],
        )
        two = report["invariant_2"]
        self.assertEqual(two["currently_visible"], 1)
        self.assertEqual(two["merged_stream"], 1)
        self.assertEqual(two["unexplained_live_loss"], 0)

    def test_a_backup_that_drops_while_the_card_stays_is_merged_not_lost(self) -> None:
        stripped = _card()
        report = self._build([("bangla", stripped)])
        two = report["invariant_2"]
        self.assertEqual(two["currently_visible"], 1)
        self.assertEqual(two["merged_stream"], 1)
        self.assertEqual(two["unexplained_live_loss"], 0)

    def test_a_pending_card_is_visible_pending_not_a_loss(self) -> None:
        """"quarantine মানে অদৃশ্য নয়" - the entry is still on the site."""
        pending = _card(
            backups=["https://cdn.test/movies/hawa-2022-720p.mkv"],
            classification_pending=True,
        )
        report = self._build([("bangla", pending)])
        two = report["invariant_2"]
        self.assertEqual(two["visible_pending"], 2)
        self.assertEqual(two["unexplained_live_loss"], 0)
        self.assertFalse(report["invariants"]["block"])

    def test_a_terminal_verdict_explains_a_disappearance(self) -> None:
        report = self._build(
            [],
            terminal=[
                {"url": self.card["url"], "reason": "confirmed_404_or_410"},
                {"url": self.card["backups"][0]["url"],
                 "reason": "confirmed_not_media"},
            ],
        )
        two = report["invariant_2"]
        self.assertEqual(two["terminal_evidence"], 2)
        self.assertEqual(two["unexplained_live_loss"], 0)

    def test_every_previously_live_stream_gets_exactly_one_outcome(self) -> None:
        report = self._build([("bangla", _card())])
        two = report["invariant_2"]
        self.assertEqual(
            two["previously_live"],
            two["currently_visible"] + two["merged_stream"]
            + two["visible_pending"] + two["terminal_evidence"]
            + two["unexplained_live_loss"],
        )


class BlockPolicyTests(unittest.TestCase):
    """ধারা ৪.০ - BLOCK is for real damage; a quarantine spike WARNs."""

    def setUp(self) -> None:
        self.sources = [{"id": "sm-movie-combined", "name": "SM", "enabled": True}]

    def test_a_quarantine_spike_does_not_block(self) -> None:
        """v৩.১ correction 2: a new public source can legitimately bring in a
        day's worth of messy titles, and stopping the whole site for that is
        wrong. The ">10% growth blocks" rule was struck out."""
        raw = [
            _entry(name="Unknown %d" % index,
                   url="https://cdn.test/movies/u%d.mkv" % index)
            for index in range(500)
        ]
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=raw,
            published_movies=[],
        )
        self.assertEqual(report["invariant_1"]["quarantined_unresolved"], 500)
        self.assertFalse(report["invariants"]["block"])

    def test_missing_metadata_poster_or_category_never_blocks(self) -> None:
        card = _card(metadata_pending=True, artwork_pending=True,
                     category_pending=True, logo="", category="Mix")
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[_entry()],
            published_movies=[("mix", card)],
            previous_inventory=_inventory(("mix", card)),
            current_inventory=_inventory(("mix", card)),
        )
        self.assertFalse(report["invariants"]["block"])
        self.assertEqual(report["invariant_1"]["published_movie"], 1)

    def test_an_unbalanced_identity_blocks(self) -> None:
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[_entry()],
            published_movies=[("bangla", _card())],
        )
        self.assertFalse(report["invariants"]["block"])
        # Damage the report the way a counting bug would, and re-check it.
        report["invariant_1"]["bucket_sum"] += 1
        invariants = mc.check_invariants(report)
        self.assertTrue(invariants["block"])
        self.assertIn(
            "INVARIANT_1 total_in_scope_entries == sum of the five dispositions",
            invariants["failures"],
        )

    def test_a_category_shaped_out_of_scope_reason_blocks(self) -> None:
        """The v৩.৪ regression guard: if category ever starts excluding
        entries again, the gate says so instead of the catalogue shrinking."""
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[_entry()],
            published_movies=[("bangla", _card())],
        )
        report["invariant_1"]["out_of_scope_reasons"] = {"category_not_allowed": 7}
        invariants = mc.check_invariants(report)
        self.assertTrue(invariants["block"])
        self.assertIn(
            "INVARIANT_1 out_of_scope uses only the three sanctioned reasons",
            invariants["failures"],
        )

    def test_no_report_at_all_is_not_a_block(self) -> None:
        """A channels or events run has nothing to say about movies."""
        self.assertEqual(mc.publish_blocked(None), (False, []))

    def test_a_malformed_report_blocks_rather_than_passing(self) -> None:
        """An unanswered gate that waves a publish through is worse than no
        gate, because it looks like one. The earlier version fell through to a
        fresh check that compared 0 against 0 and passed."""
        for broken in (
            {"kind": "movie_source_coverage", "invariants": "not-a-mapping"},
            {"kind": "movie_source_coverage"},
            {"kind": "movie_source_coverage", "invariant_1": {}},
            "not a report at all",
        ):
            with self.subTest(broken=broken):
                blocked, reasons = mc.publish_blocked(broken)
                self.assertTrue(blocked)
                self.assertTrue(reasons)

    def test_check_invariants_can_re_read_a_report_it_did_not_build(self) -> None:
        """The publish gate loads the file; it does not rebuild the numbers."""
        report = mc.build_movie_coverage(
            configured_sources=self.sources,
            raw_entries=[_entry()],
            published_movies=[("bangla", _card())],
        )
        reloaded = {key: value for key, value in report.items()
                    if key != "invariants"}
        blocked, reasons = mc.publish_blocked(reloaded)
        self.assertFalse(blocked)
        self.assertEqual(reasons, [])


class PrivateSnapshotTests(unittest.TestCase):
    """The owner's catalogue is the most valuable source and the easiest to
    miscount, because it never passes through `collect_candidates`."""

    def setUp(self) -> None:
        import json
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cache = self.root / "state" / "manual-movie-remote-cache.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "version": 3,
            "sources": {
                "hopeful-research-bangla": {
                    "category": "Bangla",
                    "items": [
                        {
                            "name": "Rockstar", "year": 2026, "enabled": True,
                            "links": [
                                {"url": "https://r2.test/rockstar-1080.mkv",
                                 "resolution": "HD 1080P"},
                                {"url": "https://r2.test/rockstar-720.mkv",
                                 "resolution": "HD 720P"},
                            ],
                        },
                        {"name": "No Links", "links": []},
                    ],
                },
                "some-other-repository": {
                    "category": "Hindi",
                    "items": [{"name": "Elsewhere",
                               "links": [{"url": "https://r2.test/x.mkv"}]}],
                },
            },
        }), encoding="utf-8")

    def test_one_entry_per_link_not_one_per_card(self) -> None:
        """INVARIANT ১ counts entries. Reading a row as a single entry with no
        top-level url marked all 858 private rows invalid on the first run."""
        entries = mc.private_snapshot_entries(
            self.root, "hopeful-research-latest")
        urls = sorted(entry["url"] for entry in entries if entry["url"])
        self.assertEqual(urls, [
            "https://r2.test/rockstar-1080.mkv",
            "https://r2.test/rockstar-720.mkv",
        ])

    def test_entries_are_attributed_to_the_configured_source(self) -> None:
        """The cache spells the repository differently from the published
        cards; a row per spelling would split one source into several."""
        entries = mc.private_snapshot_entries(
            self.root, "hopeful-research-latest")
        self.assertEqual(
            {entry["source_id"] for entry in entries},
            {"hopeful-research-latest"},
        )
        self.assertEqual(
            {entry["source_file"] for entry in entries},
            {"hopeful-research-bangla"},
        )

    def test_a_row_with_no_playable_link_is_counted_then_explained(self) -> None:
        """It stays in `raw_entries` and leaves through `empty_or_invalid_record`.

        Dropping it at the reader would be the quieter option and the wrong
        one: a row the source really contains would then never appear in the
        accounting at all, which is the single thing this gate exists to
        prevent. Counted, then explained.
        """
        entries = mc.private_snapshot_entries(
            self.root, "hopeful-research-latest")
        self.assertIn("No Links", [entry.get("name") for entry in entries])

        by_name = {entry.get("name"): entry for entry in entries}
        self.assertEqual(
            mc.classify_scope(by_name["No Links"]),
            (False, mc.OUT_OF_SCOPE_INVALID),
        )
        self.assertTrue(mc.classify_scope(by_name["Rockstar"])[0])

    def test_a_missing_cache_is_empty_not_an_exception(self) -> None:
        self.assertEqual(
            mc.private_snapshot_entries(self.root / "nowhere", "any"), [])


class ReportShapeTests(unittest.TestCase):
    """ধারা ৪.০ names the fields the report must carry."""

    def setUp(self) -> None:
        self.report = mc.build_movie_coverage(
            configured_sources=[{"id": "sm", "name": "SM", "enabled": True}],
            raw_entries=[_entry(source_id="sm")],
            published_movies=[("bangla", _card(source_id="sm"))],
        )

    def test_invariant_one_row_fields(self) -> None:
        row = self.report["sources"][0]
        for field in ("raw_entries", "out_of_scope", "published_movie",
                      "published_series_episode", "merged_stream",
                      "quarantined_unresolved", "definitive_rejected",
                      "identity_balanced"):
            self.assertIn(field, row)

    def test_invariant_two_fields(self) -> None:
        for field in ("previously_live", "currently_visible", "visible_pending",
                      "terminal_evidence", "unexplained_live_loss",
                      "block_reasons"):
            self.assertIn(field, self.report["invariant_2"])

    def test_the_report_says_how_its_evidence_was_obtained(self) -> None:
        """A report that watched a publish and one that reconstructed it are
        different claims, and a reader has to be able to tell them apart."""
        self.assertEqual(self.report["evidence"], "scan")
        audited = mc.build_movie_coverage(
            configured_sources=[], raw_entries=[], published_movies=[],
            evidence="audit")
        self.assertEqual(audited["evidence"], "audit")

    def test_a_source_that_produced_nothing_still_has_a_row(self) -> None:
        report = mc.build_movie_coverage(
            configured_sources=[{"id": "silent", "name": "Silent", "enabled": True}],
            raw_entries=[], published_movies=[])
        self.assertEqual(len(report["sources"]), 1)
        self.assertEqual(report["sources"][0]["raw_entries"], 0)

    def test_an_entry_from_a_source_nobody_configured_is_reported_beside_them(self) -> None:
        report = mc.build_movie_coverage(
            configured_sources=[{"id": "sm", "name": "SM", "enabled": True}],
            raw_entries=[_entry(source_id="mystery")],
            published_movies=[])
        self.assertEqual(
            [row["source_id"] for row in report["unconfigured_sources"]],
            ["mystery"],
        )

    def test_format_is_readable_and_names_the_blocking_number(self) -> None:
        text = mc.format_movie_coverage(self.report)
        self.assertIn("UNEXPLAINED LIVE LOSS", text)
        self.assertIn("TOTAL IN SCOPE", text)


if __name__ == "__main__":
    unittest.main()
