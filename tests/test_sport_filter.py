"""Cricket and football reach the two event tabs; nothing else does.

The tests that matter most here are the negative ones - the fixtures that were
observed being mislabelled in production and must survive anyway. Every case in
`RECOVERED` is a real row from data/today-match.json or data/upcoming.json on
2026-08-30 whose `sport_type` said "other".
"""
import pathlib
import re
import unittest

from scanner import sport_filter as sf


def event(name, sport_type=None, competition=None, **extra):
    row = {"name": name, "category": "today_match"}
    if sport_type is not None:
        row["sport_type"] = sport_type
    if competition is not None:
        row["competition"] = competition
    row.update(extra)
    return row


def canned(sport, confirmed=True, reason="canned"):
    """A stand-in for the fixture providers, so no test touches the network."""
    def verifier(queries):
        from scanner import fixture_lookup
        return {fixture_lookup.cache_key(a, b, d): {
                    "sport": sport, "confirmed": confirmed, "reason": reason,
                    "signals": [], "unavailable": []}
                for a, b, d, _competition in queries}
    return verifier


def no_answer(_queries):
    """Every provider quiet - the lookup ran and settled nothing."""
    return {}


#: Observed in production labelled "other", and every one of them real.
RECOVERED = [
    ("Machico vs Camacha", "Taca de Portugal", "football"),
    ("Arzignano Valchiampo vs PRO Vercelli", "Serie C - Girone A", "football"),
    ("Dolomiti Bellunesi vs Alcione", "Serie C - Girone A", "football"),
    ("Lumezzane vs Giana Erminio", "Serie C - Girone A", "football"),
    ("Pergolettese vs Union Brescia", "Serie C - Girone A", "football"),
    ("Uniao PR vs Campo Mourao", "Paranaense - 3", "football"),
    ("Aberdeen vs Rangers", None, "football"),
    ("Top End Series, Final Teams", None, "cricket"),
]


class RecoversMislabelledEvents(unittest.TestCase):
    def test_every_observed_mislabel_is_published(self):
        for name, competition, sport in RECOVERED:
            with self.subTest(name=name):
                verdict = sf.classify(event(name, "other", competition))
                self.assertIn(verdict["state"], sf.PUBLISHABLE,
                              "{0} was dropped: {1}".format(name, verdict))
                self.assertIn(sport, verdict["state"])
                self.assertTrue(verdict["evidence"], verdict)

    def test_a_source_saying_other_is_not_a_refusal(self):
        # "other" is the absence of a classification, not a claim about the
        # sport. Reading it as a refusal is what dropped seven Serie C matches.
        verdict = sf.classify(event("Some Club vs Other Club", "other"))
        self.assertNotEqual(verdict["state"], sf.CONFIRMED_OTHER)


class TrustsTheSourceFirst(unittest.TestCase):
    def test_structured_cricket(self):
        self.assertEqual(sf.classify(event("Day 4 2nd Test", "cricket"))["state"],
                         sf.CONFIRMED_CRICKET)

    def test_structured_football(self):
        self.assertEqual(sf.classify(event("A vs B", "football"))["state"],
                         sf.CONFIRMED_FOOTBALL)

    def test_soccer_counts_as_football(self):
        self.assertEqual(sf.classify(event("A vs B", "soccer"))["state"],
                         sf.CONFIRMED_FOOTBALL)


class CompetitionOutranksABadLabel(unittest.TestCase):
    def test_serie_c_is_football_even_when_labelled_other(self):
        verdict = sf.classify(event("X vs Y", "other", "Serie C - Girone A"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_FOOTBALL)
        # Named in the league map, so it is settled there rather than by the
        # looser competition patterns below it.
        self.assertEqual(verdict["reason"], "league map")

    def test_cricket_league_written_out_is_not_english_football(self):
        for competition in ("Bangladesh Premier League", "Indian Premier League",
                            "Pakistan Super League", "Caribbean Premier League"):
            with self.subTest(competition=competition):
                self.assertEqual(
                    sf.classify(event("A vs B", None, competition))["state"],
                    sf.CONFIRMED_CRICKET)

    def test_world_test_championship_is_cricket_not_football(self):
        self.assertEqual(
            sf.classify(event("IND vs AUS", None, "World Test Championship"))["state"],
            sf.CONFIRMED_CRICKET)

    def test_county_championship_is_cricket(self):
        self.assertEqual(
            sf.classify(event("Surrey vs Kent", None, "County Championship"))["state"],
            sf.CONFIRMED_CRICKET)


class RefusesOtherSports(unittest.TestCase):
    CASES = [
        ("US Open", "tennis", "US Open"),
        ("Miami Marlins vs Washington Nationals", "baseball", "MLB"),
        ("Minnesota Lynx vs Atlanta Dream", "basketball", "WNBA"),
        ("Spain vs Germany", "hockey", "FIH Hockey World Cup 2026"),
        ("Vuelta a Espana, Stage 9", "other", "Cycling"),
        ("SS18 Encarnacion 1", "other", "WRC"),
        ("BC Lions vs Ottawa Redblacks", "other", "CFL"),
        ("Husqvarna British Masters", "golf", "DP World Tour"),
        ("Saratoga Live", "racing", "Horse Racing"),
        ("European Tour 11 Hungarian Darts Trophy", "other", None),
    ]

    def test_each_is_refused(self):
        for name, sport_type, competition in self.CASES:
            with self.subTest(name=name):
                verdict = sf.classify(event(name, sport_type, competition))
                self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER, verdict)

    def test_a_golf_tour_championship_is_not_football(self):
        # "Championship" alone is golf here. It read as football until the bare
        # word was demoted below the named other sports.
        verdict = sf.classify(event("Tour Championship, Day 4", "golf", "PGA Tour"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER)

    def test_gridiron_is_not_association_football(self):
        verdict = sf.classify(event("Chiefs vs Bills", "other", "NFL"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER)


class HoldsBackWhatItCannotName(unittest.TestCase):
    """No evidence is not a verdict, so it does not reach a tab - but it is
    never binned silently either."""

    def test_unknown_is_not_publishable(self):
        self.assertNotIn(sf.UNKNOWN, sf.PUBLISHABLE)

    def test_an_empty_record_is_held_back(self):
        self.assertFalse(sf.is_publishable({}))

    def test_a_word_inside_another_word_does_not_count(self):
        # "test" inside "contest" must not read as a Test match.
        self.assertEqual(sf.classify(event("Talent Contest Final"))["state"],
                         sf.UNKNOWN)

    def test_a_quarantined_event_is_named_in_the_report(self):
        kept, report = sf.apply([event("Talent Contest Final", "other",
                                       source_ids=["feed-z"])])
        self.assertEqual(kept, [])
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(report["quarantined_events"][0]["name"],
                         "Talent Contest Final")
        self.assertEqual(report["quarantined_events"][0]["source"], "feed-z")
        self.assertEqual(report["by_source"]["feed-z"]["quarantined"], 1)

    def test_quarantine_is_counted_apart_from_refusal(self):
        _, report = sf.apply([
            event("Talent Contest Final", "other"),
            event("US Open", "tennis", "US Open"),
        ])
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(report["rejected"], 1)


class TheSecondPassResolvesWhatItCan(unittest.TestCase):
    """The batch is consulted before the network is.

    A sibling card is one source, though, and a team-vs-team fixture needs two
    - so the lookup still runs and still has the last word. These tests check
    what the second pass produces, and that it does not become the answer on
    its own.
    """

    def test_the_same_fixture_from_another_source_is_recognised(self):
        items = [
            event("Machico vs Camacha", "football", "Taca de Portugal"),
            event("Machico vs Camacha", "other"),
        ]
        resolved = sf.resolve(items, verify_fixtures=no_answer)
        self.assertEqual(resolved[1][1]["reason"],
                         "fixture lookup has not run yet")

    def test_a_sibling_card_alone_does_not_publish_a_fixture(self):
        items = [
            event("Machico vs Camacha", "football", "Taca de Portugal"),
            event("Machico vs Camacha", "other"),
        ]
        kept, report = sf.apply(items, verify_fixtures=no_answer)
        self.assertEqual(len(kept), 1, "only the labelled card should publish")
        self.assertEqual(report["quarantined"], 1)

    def test_the_lookup_confirms_what_the_sibling_suggested(self):
        items = [
            event("Machico vs Camacha", "football", "Taca de Portugal"),
            event("Machico vs Camacha", "other"),
        ]
        kept, report = sf.apply(items, verify_fixtures=canned("football"))
        self.assertEqual(len(kept), 2)
        self.assertEqual(report["quarantined"], 0)

    def test_one_matching_side_is_not_enough_for_the_second_pass(self):
        # A club can share a name with a team in another sport, so half a
        # fixture proves nothing - and the lookup is what decides anyway.
        items = [
            event("Machico vs Camacha", "football", "Taca de Portugal"),
            event("Machico vs Someone Else", "other"),
        ]
        _, report = sf.apply(items, verify_fixtures=no_answer)
        self.assertEqual(report["quarantined"], 1)

    def test_two_nations_that_play_both_sports_settle_nothing(self):
        # India and Thailand field cricket and football sides, so another
        # India fixture must not lend this one a verdict.
        items = [
            event("India Women vs Thailand Women", "cricket",
                  "DP World Women's Asia Cup"),
            event("India vs Thailand", "other"),
        ]
        _, report = sf.apply(items, verify_fixtures=no_answer)
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(report["quarantined_events"][0]["name"],
                         "India vs Thailand")


class LeakAudit(unittest.TestCase):
    """An independent re-read of what survived, so a leak is a number."""

    def test_a_clean_list_reports_no_leak(self):
        kept, report = sf.apply([
            event("Napoli vs Como", "football", "Serie A"),
            event("England vs Pakistan", "cricket", "Test match"),
        ])
        self.assertEqual(report["leaks"], [])
        self.assertEqual(sf.audit_visible(kept), [])

    def test_a_likely_card_that_smells_of_another_sport_is_flagged(self):
        leaks = sf.audit_visible([
            {"name": "Sale Sharks vs Saracens", "sport_class": sf.LIKELY_FOOTBALL},
        ])
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0]["sport"], "rugby")

    def test_every_other_sport_the_tabs_have_carried_is_refused(self):
        # The sports observed leaking into Today Match and Upcoming, each of
        # which must now be refused outright.
        cases = [
            ("Boland Cavaliers v Suzuki Griquas", "other", None, "rugby"),
            ("BC Lions vs Ottawa Redblacks", "other", "CFL", "gridiron"),
            ("Boston Red Sox vs New York Yankees", "other", None, "baseball"),
            ("Mens Singles 1st Round", "other", "US Open Tennis", "tennis"),
            ("Husqvarna British Masters", "other", "DP World Tour", "golf"),
            ("Connecticut Sun vs Dallas Wings", "other", "WNBA", "basketball"),
            ("SS18 Encarnacion 1", "other", "WRC", "motorsport"),
            ("Spain vs Germany", "other", "FIH Hockey World Cup", "hockey"),
            ("All In Buy In", "other", None, "poker / cards"),
            ("Saratoga Live", "other", "Horse Racing", "horse racing"),
        ]
        for name, sport_type, competition, expected in cases:
            with self.subTest(name=name):
                verdict = sf.classify(event(name, sport_type, competition))
                self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER, verdict)
                self.assertIn(expected, verdict["reason"])


class Reporting(unittest.TestCase):
    def test_apply_keeps_ball_sports_and_counts_per_source(self):
        items = [
            event("A vs B", "football", source_ids=["feed-x"]),
            event("C vs D", "cricket", source_ids=["feed-x"]),
            event("US Open", "tennis", "US Open", source_ids=["feed-y"]),
        ]
        kept, report = sf.apply(items)
        self.assertEqual(len(kept), 2)
        self.assertEqual(report["rejected"], 1)
        self.assertEqual(report["by_source"]["feed-x"]["published"], 2)
        self.assertEqual(report["by_source"]["feed-y"]["rejected"], 1)
        self.assertEqual(report["by_source"]["feed-x"]["football"], 1)
        self.assertEqual(report["by_source"]["feed-x"]["cricket"], 1)

    def test_apply_records_the_verdict_on_the_card(self):
        kept, _ = sf.apply([event("A vs B", "football")])
        self.assertEqual(kept[0]["sport_class"], sf.CONFIRMED_FOOTBALL)
        self.assertTrue(kept[0]["sport_class_reason"])

    def test_apply_survives_a_non_dict(self):
        kept, _ = sf.apply([event("A vs B", "football"), None, "junk"])
        self.assertEqual(len(kept), 1)


class LiveTvIsUntouched(unittest.TestCase):
    def test_only_the_event_pipeline_can_filter_with_it(self):
        """Live TV keeps Sports, Movies and Drama.

        The guard used to be "nothing else may import this module". That
        was a proxy for the thing actually worth protecting - nothing else
        may DROP a card with it - and the proxy stopped fitting when the
        schedule resolver began asking `classify` how long a football match
        lasts (PROMPT 16). Reading a verdict removes nothing; `apply` is the
        call that removes things, and it stays where it was.
        """
        removers = sorted(
            path.name
            for path in pathlib.Path("scanner").glob("*.py")
            if "sport_filter.apply(" in path.read_text(encoding="utf-8")
            and path.name != "sport_filter.py"
        )
        self.assertEqual(removers, ["events.py"], removers)

    def test_and_only_two_modules_call_into_it_at_all(self):
        """Matched on use, not on mention. `event_lifecycle` names
        `sport_filter.CRICKET_FORMATS` in a comment, to say what its own
        duration table is keyed by - which is documentation, not a
        dependency, and must not read as one."""
        callers = sorted(
            path.name
            for path in pathlib.Path("scanner").glob("*.py")
            if re.search(
                r"^\s*(?:from\s+\S+\s+)?import\s+sport_filter|^[^#\n]*\bsport_filter\.",
                path.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            and path.name != "sport_filter.py"
        )
        self.assertEqual(
            callers, ["events.py", "schedule_resolver.py"], callers
        )


class KnownTeamVsTeamGoesToTheFixtureLookup(unittest.TestCase):
    """Two named sides and no trustworthy sport field is not a keyword problem.

    `Boland Cavaliers v Suzuki Griquas` is rugby, `Lumezzane vs Giana Erminio`
    is Serie C football and `India vs Thailand` is either. The shape of all
    three titles is identical, so the decision has to come from a lookup.
    """

    def test_an_unlabelled_fixture_is_sent_for_lookup(self):
        card = event("Boland Cavaliers v Suzuki Griquas", "other")
        self.assertTrue(sf.needs_fixture_check(card, sf.classify(card)))

    def test_a_source_that_names_cricket_is_taken_at_its_word(self):
        card = event("A vs B", "cricket", "Some Trophy")
        self.assertFalse(sf.needs_fixture_check(card, sf.classify(card)))

    def test_a_single_entity_is_not_a_fixture(self):
        card = event("Top End T20 Series 2026", "other")
        self.assertFalse(sf.needs_fixture_check(card, sf.classify(card)))

    def test_a_confirmed_other_sport_is_rejected(self):
        kept, report = sf.apply([event("Boland Cavaliers v Suzuki Griquas", "other")],
                                verify_fixtures=canned("rugby"))
        self.assertEqual(kept, [])
        self.assertEqual(report["rejected"], 1)
        self.assertEqual(report["rejected_examples"][0]["reason"],
                         "fixture lookup: rugby")

    def test_a_confirmed_football_fixture_is_published(self):
        kept, report = sf.apply([event("Lumezzane vs Giana Erminio", "other",
                                       "Serie C - Girone A")],
                                verify_fixtures=canned("football"))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["sport_class"], sf.CONFIRMED_FOOTBALL)
        self.assertEqual(kept[0]["sport_class_reason"], "fixture lookup")

    def test_an_unconfirmed_fixture_is_quarantined_not_published(self):
        kept, report = sf.apply([event("India vs Thailand", "other")],
                                verify_fixtures=canned(
                                    "", confirmed=False, reason="nobody knows"))
        self.assertEqual(kept, [])
        self.assertEqual(report["quarantined"], 1)
        self.assertIn("nobody knows",
                      report["quarantined_events"][0]["reason"])

    def test_a_lookup_that_never_ran_holds_the_card_back(self):
        # Past the budget, or every provider quiet. Either way it waits.
        kept, report = sf.apply([event("Some Club vs Other Club", "other")],
                                verify_fixtures=no_answer)
        self.assertEqual(kept, [])
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(report["quarantined_events"][0]["reason"],
                         "fixture lookup has not run yet")

    def test_the_lookup_overrules_a_keyword_match(self):
        # "Cavaliers" would read as a club name. The fixture says rugby, and
        # the fixture wins - that is the whole point of asking.
        kept, _ = sf.apply([event("Boland Cavaliers v Suzuki Griquas", "other")],
                           verify_fixtures=canned("rugby"))
        self.assertEqual(kept, [])


class TheBadgeMatchesTheDecision(unittest.TestCase):
    def test_an_established_sport_is_written_back_to_sport_type(self):
        # The card's badge reads sport_type, so a card published as football
        # while sport_type still said "other" showed an OTHER badge on
        # Upcoming - seen on Lumezzane vs Giana Erminio.
        kept, _ = sf.apply([event("X vs Y", "other", "Serie C - Girone A")],
                           verify_fixtures=canned("football"))
        self.assertEqual(kept[0]["sport_type"], "football")
        self.assertEqual(kept[0]["sport_type_from_source"], "other")

    def test_a_source_that_already_agrees_is_left_alone(self):
        kept, _ = sf.apply([event("X vs Y", "football", "Serie A")],
                           verify_fixtures=no_answer)
        self.assertEqual(kept[0]["sport_type"], "football")
        self.assertNotIn("sport_type_from_source", kept[0])

class TheEarlyPassOnlyDropsWhatIsCertain(unittest.TestCase):
    """Before the merge, a card has a name and often nothing else.

    Applying the full rules there was measured on 2026-08-31: 517 of 1062
    events came out unresolved and 201 real cricket and football fixtures went
    with them, because the evidence had not been assembled yet. So the early
    pass removes only what is already provable and never quarantines.
    """

    def test_a_named_other_sport_goes_immediately(self):
        kept, report = sf.discard_confirmed_other([
            event("Connecticut Sun vs Dallas Wings", "other", "WNBA"),
            event("Napoli vs Como", "football", "Serie A"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "Napoli vs Como")
        self.assertEqual(report["discarded"], 1)

    def test_an_unlabelled_card_survives_to_be_decided_later(self):
        kept, report = sf.discard_confirmed_other([
            event("Lumezzane vs Giana Erminio", "other"),
            event("Some Fixture With No Clues"),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(report["discarded"], 0)

    def test_it_never_reaches_the_network(self):
        # No fixture lookup at this stage: there is nothing to look one up
        # with yet, and the whole point is that it costs nothing.
        original = sf.classify
        seen = []
        try:
            sf.classify = lambda item: seen.append(item) or original(item)
            kept, _ = sf.discard_confirmed_other([event("A vs B", "other")])
        finally:
            sf.classify = original
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(seen), 1, "one classification, no second pass")

    def test_the_discard_record_carries_enough_to_audit(self):
        # The record has to survive being read back later, which means keeping
        # the competition and the source's own label beside the name - reading
        # "Main Feed Day 4" on its own tells nobody anything.
        _, report = sf.discard_confirmed_other([
            event("Main Feed Day 4", "golf", "TOUR Championship 2026",
                  source_ids=["sm-fancode"]),
        ])
        row = report["examples"][0]
        self.assertEqual(row["competition"], "TOUR Championship 2026")
        self.assertEqual(row["sport_type"], "golf")
        self.assertEqual(row["source"], "sm-fancode")
        self.assertTrue(row["reason"])


class NothingBallShapedWasRefused(unittest.TestCase):
    def test_a_clean_refusal_list_reports_nothing(self):
        audit = sf.never_dropped_audit(
            [{"name": "US Open", "sport_type": "tennis", "reason": "tennis"}], [])
        self.assertEqual(audit["wrongly_refused"], [])

    def test_a_football_match_in_the_refusal_list_is_named(self):
        audit = sf.never_dropped_audit(
            [{"name": "Napoli vs Como", "competition": "Serie A",
              "reason": "some mistake", "source": "feed-x"}], [])
        self.assertEqual(len(audit["wrongly_refused"]), 1)
        self.assertEqual(audit["wrongly_refused"][0]["reads_as"],
                         sf.CONFIRMED_FOOTBALL)

    def test_quarantined_rows_are_re_read_too(self):
        audit = sf.never_dropped_audit(
            [], [{"name": "England vs Pakistan", "competition": "Test match",
                  "reason": "held back"}])
        self.assertEqual(len(audit["wrongly_refused"]), 1)

    def test_a_golf_broadcast_is_not_read_back_as_football(self):
        # "TOUR Championship 2026" - refused as golf, and the audit has to
        # agree rather than flag it. Bare "championship" was removed from the
        # football shapes for exactly this.
        audit = sf.never_dropped_audit(
            [{"name": "Main Feed Day 4", "competition": "TOUR Championship 2026",
              "sport_type": "golf", "reason": "golf gazetteer"}], [])
        self.assertEqual(audit["wrongly_refused"], [])

class TheCompetitionOutranksATeamName(unittest.TestCase):
    """A league name is a fact about the sport; a team name is a coincidence.

    Three real fixtures were lost to one word inside a team's name, and one
    was published as the wrong sport because the feed's own tag was wrong.
    """

    def test_a_cricket_league_survives_a_baseball_team_name(self):
        # "Guardians" is a Cleveland baseball team and also half of an ETPL
        # cricket fixture.
        verdict = sf.classify(event("Guardians vs Dockers", None, "ETPL"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_CRICKET)

    def test_a_football_league_survives_a_baseball_team_name(self):
        # "Angels" is a Los Angeles baseball team and also half of a Korean
        # women's football fixture.
        verdict = sf.classify(
            event("Seoul W vs Incheon Red Angels W", None, "WK-League"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_FOOTBALL)

    def test_the_league_map_outranks_a_wrong_source_tag(self):
        # The feed tagged this football; ETPL is a T20 cricket league and the
        # sides are Belfast Wolves and Edinburgh Castle Rockers.
        verdict = sf.classify(event("Wolves vs Castle Rockers", "football", "ETPL"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_CRICKET)
        self.assertEqual(verdict["reason"], "league map")

    def test_a_team_name_still_decides_when_there_is_no_competition(self):
        # Nothing else to go on, so the names are the evidence.
        verdict = sf.classify(event("Boland Cavaliers v Suzuki Griquas", "other"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER)

    def test_a_team_name_against_an_unknown_competition_waits_for_the_fixture(self):
        # The competition names no sport this module knows, so a word in a
        # team's name is not allowed to refuse the event on its own.
        verdict = sf.classify(
            event("Guardians vs Dockers", None, "Some Unlisted Trophy"))
        self.assertEqual(verdict["state"], sf.UNKNOWN)
        self.assertIn("needs the fixture", verdict["reason"])

    def test_a_league_that_names_another_sport_is_still_refused(self):
        for name, competition, expected in (
            ("BC Lions vs Ottawa Redblacks", "CFL", "gridiron"),
            ("Boston Red Sox vs New York Yankees", "MLB", "baseball"),
            ("Spain vs Germany", "FIH Hockey World Cup 2026", "hockey"),
            ("Some Pair vs Another", "Currie Cup", "rugby"),
        ):
            with self.subTest(competition=competition):
                verdict = sf.classify(event(name, "other", competition))
                self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER)
                self.assertIn(expected, verdict["reason"])

    def test_a_season_year_does_not_hide_the_league(self):
        for competition in ("European T20 Premier League 2026",
                            "JITO Premier League 2026", "Oman D50 2026",
                            "LaLiga 2026/27", "WK League 2026"):
            with self.subTest(competition=competition):
                sport, _ = sf.league_sport(competition)
                self.assertIn(sport, ("cricket", "football"), competition)

    def test_an_unlisted_competition_returns_nothing(self):
        self.assertEqual(sf.league_sport("Nonexistent Cup"), ("", ""))
        self.assertEqual(sf.league_sport(""), ("", ""))

class AFeedHeaderIsNotAFixture(unittest.TestCase):
    """`EUROPEAN T20 Vs Premier League` was a Today Match card.

    It had three channels under it - FOX CRICKET, WILLOW SPORTS, Willow
    Cricbuzz - while the actual ETPL match at that hour, `Dublin Guardians vs
    Rotterdam Dockers`, sat beside it with its own two. Nobody plays "European
    T20": the feed's generic header had been read as a team-vs-team fixture,
    and a viewer had two cards for one match, one of them fictional.
    """

    def test_the_observed_header_is_refused(self):
        verdict = sf.classify(event("EUROPEAN T20 Vs Premier League", None,
                                    "EUROPEAN T20 Premier League"))
        self.assertEqual(verdict["state"], sf.CONFIRMED_OTHER)
        self.assertEqual(verdict["reason"], "not a fixture")

    def test_two_competition_names_facing_each_other_are_refused(self):
        for name in ("Serie A vs Bundesliga", "LaLiga vs Eredivisie",
                     "IPL vs BBL"):
            with self.subTest(name=name):
                self.assertEqual(sf.classify(event(name))["state"],
                                 sf.CONFIRMED_OTHER)

    def test_the_real_fixture_in_the_same_competition_survives(self):
        for name in ("Dublin Guardians vs Rotterdam Dockers",
                     "Belfast Wolves vs Edinburgh Castle Rockers"):
            with self.subTest(name=name):
                verdict = sf.classify(
                    event(name, None, "European T20 Premier League"))
                self.assertEqual(verdict["state"], sf.CONFIRMED_CRICKET)

    def test_a_club_whose_name_contains_a_competition_word_is_safe(self):
        # The competition name has to be essentially the whole side, so a club
        # is never mistaken for a league.
        for name, competition in (("Napoli vs Como", "Serie A"),
                                  ("Real Sociedad vs RC Celta", "LaLiga"),
                                  ("Machico vs Camacha", "Taca de Portugal")):
            with self.subTest(name=name):
                self.assertIn(sf.classify(event(name, None, competition))["state"],
                              (sf.CONFIRMED_FOOTBALL, sf.CONFIRMED_CRICKET))

    def test_a_single_entity_title_is_not_treated_as_a_header(self):
        # Nothing to compare, so this rule does not apply and the later stages
        # decide.
        self.assertEqual(sf.is_generic_fixture(event("Top End T20 Series 2026")), "")

    def test_a_header_needs_a_recognisable_competition_on_each_side(self):
        # A known limit, written down rather than papered over: "Championship"
        # on its own is golf as often as football, so it is in no league map -
        # and a header built from it is not recognised as one.
        verdict = sf.classify(event("Premier League vs Championship"))
        self.assertNotEqual(verdict["reason"], "not a fixture")



if __name__ == "__main__":
    unittest.main()
