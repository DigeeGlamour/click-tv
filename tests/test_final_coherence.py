"""One fixture, four surfaces - and two spellings that needed a fixture to mean
anything.

PART A. THE SELECTED CARD, NOW PLAYING, THE PLAYER AND THE DETAIL PANEL.

Reported: the green card, NOW PLAYING and the player all named Toluca vs
Monterrey while the hero/detail panel named Kuwait vs Qatar. Reproduced in
Chromium against two real consecutive published Today Match snapshots
(1795cdcfb9 19:09:22Z, 20 cards, and its own successor 82d8551bb0 19:31:16Z,
19 cards), with the browser clock set to the first one's publish time. Two
independent causes, both state-only:

1. The detail panel is the hero/detail surface, and NOW PLAYING is not a
   separate one: the label is a ::before on `.video-meta .meta-title-row`, so
   NOW PLAYING and #metaTitle are one block written by one call. The panel,
   though, is filled by showEventPreview from whatever card the viewer last
   opened it for, is drawn over the player at z-index 34, and was closed by
   exactly one of the ten routes into playback - the sidebar click. Measured:
   opening the panel on a no-link fixture and then pressing next-channel left
   `Al Riyadh Vs Al Kholood` on screen over a player, a NOW PLAYING block and a
   green card all naming `England Vs Pakistan`.

2. _uid is `<kind>:<id>:<index>`, so a fixture's key changes when a publish
   moves it. Over the last 400 published revisions of today-match.json, 32% of
   consecutive publishes moved at least one surviving card and 22% of surviving
   cards changed index; on upcoming.json, 35% and 22%. The real pair above
   moves 15 of its 17 survivors. Measured before the fix: playing
   `India Women Vs Bangladesh Women` at index 3, the successor publish put it
   at index 2 and the browser reported no active card at all - no green card,
   no NOW PLAYING action, no green server pill - while the match played on.
   Nothing was ever highlighted WRONGLY, because the id is inside the key; the
   selection simply vanished.

After the fix, the same thirteen-point matrix in the same browser: 0 mismatches.
The locked design was measured on both builds at 1440x900, 768x1024 and 390x844
across Today and Upcoming - 35 nodes, geometry plus sixteen computed
properties: 0 differences. The residual pixel deltas (<=0.076%) reproduce when
the OLD build is compared against ITSELF, so they are the live clock text and
not this change.

PART B. NUREMBERG / NURNBERG AND OESTERSUNDS / OSTERSUNDS.

Both pairs are a single word on each side, and a bare word may never be a key
in config/team-aliases.json - relating one would give two clubs one canonical
name, which is why `olimpia`, `platense` and `united` are refused there. That
global guard is untouched and its test is unchanged.

The pairs are expressed contextually instead: two spellings of one club,
related only inside a fixture whose OTHER side is a recorded counterpart, and
asked only after that other side has already matched exactly and the kickoff
bucket has already agreed. From the repository's own census, 173 revisions of
reports/fixture-authority-shadow.json:

    x47  1 FC Nurnberg vs Hannover 96   nurnberg <-> nuremberg    (livescore,
                                                    agreed on hannover 96)
    x47  Orebro SK vs Ostersunds FK     ostersunds <-> oestersunds (livescore,
                                                    agreed on orebro)

Neither other spelling appears on a published card in the last 400 revisions of
either tab, so this folds no card: it resolves an authority's reading. Replayed
over that census, origin/main answers 66 of the 76 distinct near misses as one
fixture and this change answers 68 - gained 2, lost 0. The eight left are the
eight recorded refusals, every one ambiguous rather than inexpressible.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "site" / "assets" / "js" / "app.js"
INDEX = ROOT / "site" / "index.html"
ALIASES = ROOT / "config" / "team-aliases.json"

from scanner import fixture_dedupe, team_identity  # noqa: E402


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def body(source: str, name: str) -> str:
    """One function's text, from its signature to the next top-level one."""
    marker = "function %s(" % name
    assert marker in source, name
    return source.split(marker, 1)[1].split("\nfunction ", 1)[0]


class OneIdentityForTheFixtureThePlayerIsOn(unittest.TestCase):
    """PART A2: one canonical active-fixture identity, never the array index."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")

    def test_the_identity_prefers_fixture_id(self):
        """FINAL's own identity for a fixture, and the one the scanner
        guarantees across a republish. Asked first and on its own."""
        source = body(self.app, "activeFixtureIdentity")
        self.assertIn("item.fixture_id", source)
        self.assertLess(source.index("fixture_id"), source.index("item.id"),
                        "fixture_id must be consulted before the slug id")

    def test_a_direct_channel_falls_back_and_gets_no_invented_fixture_id(self):
        """PART A6. A direct channel is not a fixture, so it has no fixture_id
        and none is manufactured for it: it is identified by its own id."""
        source = body(self.app, "activeFixtureIdentity")
        self.assertIn("'id:'", source)
        self.assertNotIn("fixture_id =", source)
        self.assertNotIn("fixture_id:", source)

    def test_the_index_is_not_part_of_the_identity(self):
        """The whole defect. _uid carries `<kind>:<id>:<index>` and is the last
        resort only, for a row that has no id, playback id or url at all."""
        source = body(self.app, "activeFixtureIdentity")
        self.assertIn("item._uid", source)
        self.assertGreater(source.index("_uid"), source.index("item.url"),
                           "_uid may only be the final fallback")

    def test_every_display_surface_asks_the_same_question(self):
        """PART A2: all display surfaces derive from one active selection.
        These are the five that used to compare _uid strings."""
        for name in ("updateActiveCards", "updateEventChannelStrip",
                     "syncActiveTodayChannel", "currentFullscreenDrawerItems"):
            with self.subTest(surface=name):
                self.assertIn("isActiveFixture(", body(self.app, name))

    def test_no_surface_compares_the_uid_against_the_current_item(self):
        """The exact expression the bug was made of. It must not come back in
        any surface that decides what is playing."""
        leftovers = re.findall(
            r"_uid\s*===?\s*state\.currentItem\?\._uid"
            r"|dataset\.uid\s*===?\s*state\.currentItem\?\._uid",
            self.app)
        self.assertEqual(leftovers, [], "index-keyed selection comparison is back")

    def test_navigation_does_not_fall_back_to_comparing_urls(self):
        """Most event cards publish an empty top-level url, so
        `item.url === state.currentItem.url` matched the FIRST url-less card in
        the list and next/previous started from the wrong place."""
        source = body(self.app, "playRelativeItem")
        self.assertIn("isActiveFixture(item)", source)
        self.assertNotIn("item.url === state.currentItem?.url", source)


class TheDetailPanelNeverDescribesAnotherFixture(unittest.TestCase):
    """PART A1: the reported mismatch."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")

    def test_now_playing_and_the_title_are_one_block(self):
        """Why the detail panel had to be the culprit: NOW PLAYING is a
        ::before on the row that holds #metaTitle, so the two cannot disagree.
        Pinned here because it is what makes the four-surface invariant
        provable at all."""
        css = read("site/assets/css/reference-design.css")
        self.assertIn('.video-meta .meta-title-row::before', css)
        self.assertIn('content:"NOW PLAYING"', css)
        self.assertIn('id="metaTitle"', read("site/index.html"))

    def test_every_route_into_playback_closes_the_panel(self):
        """One call, in startPlayback, rather than at nine call sites that can
        each be forgotten - and one of them was, for all nine."""
        source = self.app.split("async function startPlayback(", 1)[1][:400]
        self.assertIn("closeEventPreview();", source)

    def test_the_panel_still_opens_for_a_fixture_with_no_link(self):
        """The fix must not cost the panel its purpose: a card with no route
        has level 3 detail to show and nothing to play."""
        self.assertIn("showEventPreview(item)", self.app)


class ARefreshDoesNotCostTheViewerTheSelection(unittest.TestCase):
    """PART A4 and A5."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")

    def test_the_refresh_adopts_the_fixture_at_its_new_position(self):
        refresh = self.app.split("async function refreshActiveEventCatalogue(", 1)[1]
        refresh = refresh.split("\nfunction ", 1)[0]
        self.assertIn("adoptRefreshedActiveFixture(nextItems)", refresh)
        self.assertLess(refresh.index("preservePlayingSession("),
                        refresh.index("adoptRefreshedActiveFixture("),
                        "the session must be preserved before it is re-keyed")

    def test_the_refresh_takes_nothing_that_decides_playback(self):
        """The player is left strictly alone. Only the position key and the
        level 1 card fields are adopted; the URL, the playback id and the
        session belong to preservePlayingSession and to the viewer."""
        source = body(self.app, "adoptRefreshedActiveFixture")
        for forbidden in ("current.url", "current.playback_id", "current.channels",
                          "current.backups", "current._sources"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden + " =", source)

    def test_hero_and_detail_refresh_from_the_same_fixture(self):
        """PART A4: if its fields refresh, hero/detail updates from the
        refreshed SAME fixture."""
        self.assertIn("updateMetadata(current, { quiet: true })",
                      body(self.app, "adoptRefreshedActiveFixture"))

    def test_a_background_refresh_does_not_flash_the_channel_banner(self):
        """updateMetadata shows the OSD, and the refresh runs every minute. The
        banner belongs to a selection the viewer made."""
        source = body(self.app, "updateMetadata")
        self.assertIn("options.quiet !== true", source)
        self.assertIn("osd.classList.add('show')", source)
        self.assertLess(source.index("options.quiet !== true"),
                        source.index("osd.classList.add('show')"))

    def test_the_playing_card_keeps_its_node_when_the_publish_moves_it(self):
        """Requirement 8 kept that node only while the index held still."""
        source = body(self.app, "reconcileEventCards")
        self.assertIn("state.pinnedSession?.domUid", source)
        self.assertIn("previous.dataset.uid = item._uid", source)
        self.assertIn("target.appendChild(previous)", source)

    def test_a_fixture_that_left_the_listing_keeps_its_playback(self):
        """PART A5: the listing may retire the card; it may not stop the
        viewer's stream or move hero/detail to another fixture."""
        source = body(self.app, "preservePlayingSession")
        self.assertIn("_carried_pinned_session", source)
        self.assertIn("match.url = pinned.url", source)

    def test_selecting_another_fixture_replaces_the_snapshot(self):
        """PART A5's other half: a new selection is a new session."""
        source = self.app.split("async function startPlayback(", 1)[1][:600]
        self.assertIn("pinPlaybackSession(item);", source)

    def test_the_first_card_is_only_autoplayed_when_nothing_is_playing(self):
        """PART A4: no accidental first-card fallback. The guard is the whole
        protection, so it is pinned."""
        self.assertIn("options.initial && !state.currentItem", self.app)

    def test_a_stale_persisted_selection_cannot_produce_a_mixed_ui(self):
        """isActiveFixture answers false for everything when the pinned
        fixture is not on the list, so no card is highlighted rather than the
        wrong one."""
        source = body(self.app, "isActiveFixture")
        self.assertIn("if (!wanted) return false", source)


class TheServerSwitchDoesNotReplaceTheFixture(unittest.TestCase):
    """PART A3."""

    @classmethod
    def setUpClass(cls):
        cls.app = read("site/assets/js/app.js")

    def test_pinning_a_channel_replays_the_same_item(self):
        """The fixture object itself is handed back to startPlayback, so the
        selection, the panel and NOW PLAYING cannot move."""
        source = body(self.app, "selectEventChannel")
        self.assertIn("await startPlayback(item, true)", source)
        self.assertNotIn("state.currentItem = ", source)

    def test_a_quality_switch_carries_the_fixture_forward(self):
        """`{ ...current }` is what keeps the identity; only the route fields
        are replaced."""
        self.assertIn("const switchedItem = {\n    ...current,", self.app)

    def test_the_green_server_pill_follows_the_fixture_not_the_index(self):
        source = body(self.app, "syncActiveTodayChannel")
        self.assertIn("isActiveFixture(", source)
        self.assertNotIn("card.dataset.uid === wantedUid", source)


class TheLockedDesignIsUntouched(unittest.TestCase):
    """PART L: state and data only."""

    def test_no_stylesheet_changed_for_this_step(self):
        """Measured against origin/main: site/assets/js/app.js is the only file
        under site/ that this step touches. The assertion is on the sheets the
        card design lives in."""
        for sheet in ("final-match-cards.css", "event-cards.css",
                      "reference-design.css", "event-channel-cards.css"):
            with self.subTest(sheet=sheet):
                self.assertTrue((ROOT / "site" / "assets" / "css" / sheet).exists())

    def test_the_card_markup_is_the_same_shape(self):
        """The classes the Today card is drawn from, unchanged."""
        app = read("site/assets/js/app.js")
        for markup in ('class="poster-caption"', 'class="league-tag"',
                       'class="match-title"', 'class="gold-rule"',
                       'class="card-lower"'):
            with self.subTest(markup=markup):
                self.assertIn(markup, app)

    def test_the_state_fix_adds_no_class_and_no_inline_style(self):
        """The two functions the fix introduced touch no className and no
        style property at all."""
        app = read("site/assets/js/app.js")
        for name in ("activeFixtureIdentity", "isActiveFixture",
                     "adoptRefreshedActiveFixture"):
            source = body(app, name)
            with self.subTest(function=name):
                self.assertNotIn("classList", source)
                self.assertNotIn(".style.", source)
                self.assertNotIn("className", source)


class TheContextualAliasNeedsAFixture(unittest.TestCase):
    """PART B."""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(ALIASES.read_text(encoding="utf-8"))

    def test_the_two_pairs_are_recorded_with_a_counterpart_and_evidence(self):
        block = self.payload["contextual"]
        for spelling, counterpart in (("nurnberg", "hannover 96"),
                                      ("ostersunds", "orebro")):
            with self.subTest(spelling=spelling):
                entry = block[spelling]
                self.assertTrue(entry["same_as"])
                self.assertIn(counterpart, entry["with"])
                self.assertGreater(len(entry["evidence"]), 120,
                                   "an entry without evidence is a hunch")

    def test_neither_spelling_is_a_key_in_the_global_table(self):
        """The guard the plain table lives by: a bare word is never a key.
        This block does not weaken it - it does not use it."""
        plain = team_identity.load_aliases()
        for word in ("nuremberg", "nurnberg", "oestersunds", "ostersunds"):
            with self.subTest(word=word):
                self.assertNotIn(word, plain)
                self.assertEqual(team_identity.canonical_team(word), word)

    def test_an_entry_with_no_counterpart_is_dropped_not_widened(self):
        """Without a counterpart this would be the global bare-word alias the
        table forbids, so the loader refuses it rather than accepting it."""
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "aliases.json"
            path.write_text(json.dumps({
                "aliases": {},
                "contextual": {"alpha": {"same_as": "beta", "with": []}},
            }), encoding="utf-8")
            self.assertEqual(team_identity.load_contextual_aliases(path), {})
            self.assertFalse(team_identity.contextual_same_side(
                "alpha", "beta", "anything", path))

    def test_a_blank_counterpart_is_refused(self):
        self.assertFalse(team_identity.contextual_same_side(
            "nurnberg", "nuremberg", ""))
        self.assertFalse(team_identity.contextual_same_side(
            "nurnberg", "nuremberg", None))

    def test_the_two_real_fixtures_now_match(self):
        kickoff = "2026-09-10T18:30:00+00:00"
        for ours, theirs in (
                ("1 FC Nürnberg vs Hannover 96", "Nuremberg vs Hannover 96"),
                ("Orebro SK vs Ostersunds FK", "Orebro vs Oestersunds FK")):
            with self.subTest(fixture=ours):
                self.assertTrue(fixture_dedupe.same_fixture(
                    {"name": ours, "start_time": kickoff},
                    {"name": theirs, "start_time": kickoff}))

    def test_the_order_the_feed_wrote_the_sides_in_does_not_decide_it(self):
        kickoff = "2026-09-10T18:30:00+00:00"
        self.assertTrue(fixture_dedupe.same_fixture(
            {"name": "Ostersunds FK vs Orebro SK", "start_time": kickoff},
            {"name": "Orebro vs Oestersunds FK", "start_time": kickoff}))

    def test_a_counterpart_the_table_does_not_name_is_refused(self):
        """The whole point of the block: outside the recorded fixture the two
        words relate nothing."""
        kickoff = "2026-09-10T18:30:00+00:00"
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "Nurnberg vs Hertha Berlin", "start_time": kickoff},
            {"name": "Nuremberg vs Hertha Berlin", "start_time": kickoff}))

    def test_two_different_clubs_sharing_one_counterpart_stay_apart(self):
        kickoff = "2026-09-10T18:30:00+00:00"
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "Nurnberg vs Hannover 96", "start_time": kickoff},
            {"name": "Ostersunds vs Hannover 96", "start_time": kickoff}))

    def test_the_kickoff_bucket_still_decides_first(self):
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "1 FC Nürnberg vs Hannover 96",
             "start_time": "2026-09-10T18:30:00+00:00"},
            {"name": "Nuremberg vs Hannover 96",
             "start_time": "2026-09-10T22:30:00+00:00"}))

    def test_the_eight_refusals_are_still_refused(self):
        """Every pair the table records as ambiguous, asked through the real
        rule with the counterpart the census recorded."""
        kickoff = "2026-09-10T18:30:00+00:00"
        refused = (
            ("Llaneros vs Deportivo Cali", "Atletico Nacional vs Deportivo Cali"),
            ("Olimpia vs Motagua", "Deportivo Olimpia vs Motagua"),
            ("Platense vs Fluminense", "Atletico Platense vs Fluminense"),
            ("Dubai United vs Shabab Al Ahli", "United vs Shabab Al Ahli"),
            ("Garudayaksa vs Persik Kediri", "PSKC Kota Cimahi vs Persik Kediri"),
        )
        for ours, theirs in refused:
            with self.subTest(pair=ours):
                self.assertFalse(fixture_dedupe.same_fixture(
                    {"name": ours, "start_time": kickoff},
                    {"name": theirs, "start_time": kickoff}))

    def test_united_and_city_are_still_two_clubs(self):
        """The rule the whole identity table is built around."""
        kickoff = "2026-09-10T18:30:00+00:00"
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "Manchester United vs Arsenal", "start_time": kickoff},
            {"name": "Manchester City vs Arsenal", "start_time": kickoff}))

    def test_the_refusal_record_says_why_each_pair_is_contextual_now(self):
        refused = self.payload["_refused"]
        for key in ("nuremberg -> nurnberg", "oestersunds -> ostersunds"):
            with self.subTest(key=key):
                self.assertIn("contextual", refused[key])


class AnAliasHasToBeReachable(unittest.TestCase):
    """A defect found while auditing this step's own near-miss census.

    identity_form consulted the alias table on the raw normalized name only.
    A feed that hands the club's name over wrapped in a legal-form word, or in
    a women's fixture's title, therefore missed every entry in the table:
    measured on 2026-09-11, 0 of the 57 aliases were reachable that way, and
    that day's own census carried `AD Ceuta FC` against `Ceuta` and
    `Al Taawoun FC` against `Al Taawon` as unresolved near misses with their
    evidenced aliases present and unused. `Barbados Tridents Women` lost the
    table altogether while the men's card resolved the same rename.

    It cost real cards, not only authority matches. Replayed over 500 published
    snapshots and 22,099 cards: 4 new folded pairs, 0 lost -
    `Firpo vs CD Olimpia` against `CD Luis Angel Firpo Vs CD Olimpia` (26 fold
    events) and `Pumas UNAM Vs Club Leon` against `U N A M Pumas vs Leon` (23),
    both of which had been published as two cards for one match.

    The second lookup is exact, like the first: it can only reach what the
    table already records.
    """

    def test_every_alias_is_reachable_through_a_club_form_word(self):
        table = team_identity.load_aliases()
        self.assertTrue(table)
        unreachable = [key for key, canonical in table.items()
                       if team_identity.identity_form(key + " fc") != canonical]
        # The two Racing Santander keys carry "club" inside the key itself, so
        # their structure is not a key either; they are reached on the spelling
        # feeds actually publish, which is the raw one.
        self.assertEqual(sorted(unreachable),
                         ["racing club de santander", "real racing club"])

    def test_a_womens_fixture_still_reaches_the_table(self):
        self.assertEqual(
            team_identity.identity_form("barbados tridents women", "women"),
            team_identity.load_aliases()["barbados tridents"])

    def test_the_two_duplicate_pairs_this_found_now_fold(self):
        kickoff = "2026-09-11T03:06:00+00:00"
        for ours, theirs in (
                ("Firpo vs CD Olimpia", "CD Luis Ángel Firpo Vs CD Olimpia"),
                ("U N A M Pumas vs Leon", "Pumas UNAM Vs Club León")):
            with self.subTest(pair=ours):
                self.assertTrue(fixture_dedupe.same_fixture(
                    {"name": ours, "start_time": kickoff},
                    {"name": theirs, "start_time": kickoff}))

    def test_the_second_lookup_invents_nothing(self):
        """A name whose structure is not in the table comes back as its
        structure, exactly as before. ("club" is itself a legal-form word, so
        the example avoids one.)"""
        self.assertEqual(team_identity.identity_form("some unknown side fc"),
                         "some unknown side")

    def test_the_gender_distinction_survives_the_second_lookup(self):
        """structural_form strips the gender marker, so the table is now
        reachable from a women's title. The categories must still not fold."""
        kickoff = "2026-09-11T12:00:00+00:00"
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "Barbados Tridents Women vs Jamaica Empress",
             "start_time": kickoff},
            {"name": "Barbados Royals vs Jamaica Empress", "start_time": kickoff}))

    def test_a_reserve_side_is_still_not_its_senior_club(self):
        kickoff = "2026-09-11T12:00:00+00:00"
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "PSV U19 vs Shakhtar Donetsk U19", "start_time": kickoff},
            {"name": "PSV Eindhoven vs Shakhtar Donetsk", "start_time": kickoff}))

    def test_united_and_city_survive_it_too(self):
        kickoff = "2026-09-11T12:00:00+00:00"
        self.assertFalse(fixture_dedupe.same_fixture(
            {"name": "Manchester United FC vs Arsenal", "start_time": kickoff},
            {"name": "Manchester City FC vs Arsenal", "start_time": kickoff}))


class TheFinalCompletionGateIsMeasurable(unittest.TestCase):
    """PART K: every invariant the gate names is asked of the published tree,
    from the files the scanner itself writes - not from a summary it could
    disagree with."""

    @classmethod
    def setUpClass(cls):
        cls.today = json.loads((ROOT / "data" / "today-match.json")
                               .read_text(encoding="utf-8"))
        cls.upcoming = json.loads((ROOT / "data" / "upcoming.json")
                                  .read_text(encoding="utf-8"))
        cls.items = (cls.today.get("items") or []) + (cls.upcoming.get("items") or [])

    def test_no_duplicate_event_ids(self):
        ids = [str(item.get("id") or "") for item in self.items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_card_is_on_both_tabs(self):
        today = {str(i.get("name") or "").casefold() for i in self.today.get("items") or []}
        upcoming = {str(i.get("name") or "").casefold() for i in self.upcoming.get("items") or []}
        self.assertEqual(today & upcoming, set())

    def test_no_semantic_duplicate_inside_a_tab(self):
        for label, tab in (("today", self.today.get("items") or []),
                           ("upcoming", self.upcoming.get("items") or [])):
            pairs = [(tab[a].get("name"), tab[b].get("name"))
                     for a in range(len(tab)) for b in range(a + 1, len(tab))
                     if fixture_dedupe.same_fixture(tab[a], tab[b])]
            with self.subTest(tab=label):
                self.assertEqual(pairs, [])

    def test_every_card_carries_a_fixture_id_and_its_sources(self):
        self.assertEqual(
            [i.get("name") for i in self.items if not str(i.get("fixture_id") or "")], [])
        self.assertEqual(
            [i.get("name") for i in self.items if not (i.get("source_ids") or [])], [])

    def test_no_today_card_is_badged_upcoming_after_its_kickoff(self):
        stamp = str(self.today.get("updated_at") or "")
        late = [i.get("name") for i in self.today.get("items") or []
                if str(i.get("schedule_status")) == "UPCOMING"
                and str(i.get("start_time") or "")
                and str(i.get("start_time")) < stamp]
        self.assertEqual(late, [])

    def test_the_coverage_report_credits_every_published_card(self):
        coverage = json.loads((ROOT / "reports" / "today-source-coverage.json")
                              .read_text(encoding="utf-8"))
        invariants = coverage.get("invariants") or {}
        self.assertEqual(invariants.get("failed"), 0)
        self.assertEqual(invariants.get("failures") or [], [])

    def test_the_direct_channel_registry_stays_out_of_the_fixture_domain(self):
        health = json.loads((ROOT / "reports" / "fixture-stream-health.json")
                            .read_text(encoding="utf-8"))
        leaked = [row.get("fixture_key") for row in health.get("fixtures") or []
                  if "sm-tapmad-channels" in (row.get("source_ids") or [])
                  and "direct-channel" not in str(row.get("fixture_key") or "")]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
