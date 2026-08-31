"""A fixture's sport, asked of the world rather than guessed from its name.

Every lookup here is answered from a canned provider, so the suite never
touches the network and never depends on what a free API happens to be serving
today. The canned bodies are the shapes the real providers returned on
2026-08-31 - that is where `strSport`, `P641` and the summary prose come from.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scanner import fixture_lookup as fl


def sportsdb_event(name, sport, league, date="2026-08-30"):
    return json.dumps({"event": [{"strEvent": name, "strSport": sport,
                                  "strLeague": league, "dateEvent": date}]})


def sportsdb_team(name, sport, league):
    return json.dumps({"teams": [{"strTeam": name, "strSport": sport,
                                  "strLeague": league}]})


def wikipedia(title, extract):
    return json.dumps({"title": title, "extract": extract})


EMPTY = json.dumps({})


class Fetcher:
    """Answers by URL fragment; anything unmatched is an honest empty reply."""

    def __init__(self, routes=None, unavailable=()):
        self.routes = routes or {}
        self.unavailable = tuple(unavailable)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for token in self.unavailable:
            if token in url:
                return fl.UNAVAILABLE
        for token, body in self.routes.items():
            if token in url:
                return body
        return EMPTY


class TheFixtureItselfSettlesIt(unittest.TestCase):
    def test_a_found_fixture_stands_alone(self):
        fetch = Fetcher({"searchevents": sportsdb_event(
            "Lumezzane vs Giana Erminio", "Soccer", "Italian Serie C Girone A")})
        answer = fl.verify("Lumezzane", "Giana Erminio", "2026-08-30", "Serie C",
                           fetch=fetch)
        self.assertTrue(answer["confirmed"])
        self.assertEqual(answer["sport"], "football")
        self.assertEqual(answer["reason"], "the fixture itself names this sport")

    def test_a_fixture_on_another_date_still_names_the_sport(self):
        # The dated search finds nothing, so it falls back to these two sides
        # meeting at all - which is weaker about the date and just as good
        # about the sport, and the reason says which of the two it is.
        fetch = Fetcher({"searchevents": sportsdb_event(
            "A vs B", "Soccer", "Some League", date="2020-01-01")})
        answer = fl.verify("A", "B", "2026-08-30", fetch=fetch)
        self.assertTrue(answer["confirmed"])
        self.assertEqual(answer["sport"], "football")
        self.assertIn("not on that date", answer["reason"])

    def test_gridiron_is_not_football(self):
        fetch = Fetcher({"searchevents": sportsdb_event(
            "BC Lions vs Ottawa Redblacks", "American Football", "CFL")})
        answer = fl.verify("BC Lions", "Ottawa Redblacks", "2026-08-30", "CFL",
                           fetch=fetch)
        self.assertTrue(answer["confirmed"])
        self.assertEqual(answer["sport"], "gridiron")


class TwoProvidersOrNothing(unittest.TestCase):
    def test_one_provider_twice_is_still_one_provider(self):
        # The India vs Thailand trap: one provider holding a football side
        # called India and a football side called Thailand says what that
        # provider indexes, not what this match is.
        fetch = Fetcher({"searchteams.php?t=India": sportsdb_team(
                             "India", "Soccer", "World Cup Qualifying AFC"),
                         "searchteams.php?t=Thailand": sportsdb_team(
                             "Thailand", "Soccer", "World Cup Qualifying AFC")})
        answer = fl.verify("India", "Thailand", "2026-08-30", fetch=fetch)
        self.assertFalse(answer["confirmed"])
        self.assertIn("one provider", answer["reason"])

    def test_two_different_providers_agreeing_is_enough(self):
        fetch = Fetcher({
            "searchteams.php?t=Boland": sportsdb_team(
                "Boland Cavaliers", "Rugby", "Currie Cup"),
            "summary/Boland_Cavaliers": wikipedia(
                "Boland Cavaliers",
                "The Boland Cavaliers are a South African rugby union team."),
        })
        answer = fl.verify("Boland Cavaliers", "Suzuki Griquas", "2026-08-30",
                           fetch=fetch)
        self.assertTrue(answer["confirmed"])
        self.assertEqual(answer["sport"], "rugby")

    def test_providers_that_disagree_confirm_nothing(self):
        fetch = Fetcher({
            "searchteams.php?t=Alpha": sportsdb_team("Alpha", "Soccer", "L1"),
            "searchteams.php?t=Beta": sportsdb_team("Beta", "Soccer", "L1"),
            "summary/Alpha": wikipedia("Alpha", "Alpha is a cricket club."),
            "summary/Beta": wikipedia("Beta", "Beta is a cricket club."),
        })
        answer = fl.verify("Alpha", "Beta", "2026-08-30", fetch=fetch)
        self.assertFalse(answer["confirmed"])
        self.assertIn("disagree", answer["reason"])

    def test_nothing_known_confirms_nothing(self):
        answer = fl.verify("Majees Titans", "Muscat Thunders", "2026-08-30",
                           fetch=Fetcher())
        self.assertFalse(answer["confirmed"])
        self.assertEqual(answer["sport"], "")

    def test_one_side_missing_is_not_a_fixture(self):
        answer = fl.verify("Some Team", "", "2026-08-30", fetch=Fetcher())
        self.assertFalse(answer["confirmed"])
        self.assertIn("two identifiable sides", answer["reason"])


class TheCompetitionIsEvidenceToo(unittest.TestCase):
    def test_a_league_article_can_supply_the_second_provider(self):
        # Third-tier Brazilian state football: the sides are in nobody's index,
        # the league has an article.
        fetch = Fetcher({
            "searchteams.php?t=Uni": sportsdb_team(
                "Uniao", "Soccer", "Brazilian Campeonato Paranaense"),
            "summary/Paranaense": wikipedia(
                "Paranaense",
                "Campeonato Paranaense, Brazilian association football league"),
        })
        answer = fl.verify("Uniao PR", "Campo Mourao", "2026-08-30",
                           "Paranaense - 3", fetch=fetch)
        self.assertTrue(answer["confirmed"])
        self.assertEqual(answer["sport"], "football")

    def test_a_round_number_is_stripped_from_the_competition(self):
        self.assertEqual(fl._competition_topic("Paranaense - 3"), "Paranaense")
        self.assertEqual(fl._competition_topic("Serie C - Girone A"), "Serie C")
        self.assertEqual(fl._competition_topic("Premier League"), "Premier League")

    def test_a_fragment_too_short_to_look_up_is_dropped(self):
        self.assertEqual(fl._competition_topic("A"), "")
        self.assertEqual(fl._competition_topic("2026"), "")


class ReadingWikipediaProse(unittest.TestCase):
    def test_canadian_football_is_gridiron_not_football(self):
        # Wikipedia calls the CFL football, which is why the compound forms are
        # read before the bare word.
        sport, _ = fl._wikipedia_topic(
            fl._Session(Fetcher({"summary": wikipedia(
                "BC Lions",
                "The BC Lions are a professional Canadian football team.")})),
            "BC Lions")
        self.assertEqual(sport, "gridiron")

    def test_a_rugby_article_is_rugby(self):
        sport, _ = fl._wikipedia_topic(
            fl._Session(Fetcher({"summary": wikipedia(
                "Griquas", "Griquas is a South African rugby union team "
                           "playing football-style codes.")})),
            "Griquas")
        self.assertEqual(sport, "rugby")

    def test_a_football_club_is_football(self):
        sport, _ = fl._wikipedia_topic(
            fl._Session(Fetcher({"summary": wikipedia(
                "AS Giana Erminio",
                "Giana Erminio is an Italian football club based in "
                "Gorgonzola.")})),
            "Giana Erminio")
        self.assertEqual(sport, "football")


class SilenceIsNotTheSameAsRefusal(unittest.TestCase):
    def test_a_rate_limited_provider_is_recorded_as_unavailable(self):
        fetch = Fetcher(unavailable=("thesportsdb",))
        answer = fl.verify("A", "B", "2026-08-30", fetch=fetch)
        self.assertIn("thesportsdb", answer["unavailable"])
        self.assertFalse(answer["confirmed"])
        self.assertEqual(answer["reason"], "no provider answered")

    def test_a_404_is_a_real_answer_not_an_outage(self):
        # There is no such page; that is the provider answering.
        answer = fl.verify("A", "B", "2026-08-30", fetch=Fetcher())
        self.assertEqual(answer["unavailable"], [])
        self.assertEqual(answer["reason"], "no provider recognised this fixture")

    def test_an_unanswered_lookup_is_retried_much_sooner(self):
        now = 1_000_000.0
        outage = {"confirmed": False, "unavailable": ["wikidata"],
                  "checked_at": now - (fl.UNAVAILABLE_RETRY_SECONDS + 1)}
        silence = {"confirmed": False, "unavailable": [],
                   "checked_at": now - (fl.UNAVAILABLE_RETRY_SECONDS + 1)}
        self.assertFalse(fl._still_good(outage, now))
        self.assertTrue(fl._still_good(silence, now))

    def test_a_confirmed_answer_never_expires(self):
        entry = {"confirmed": True, "sport": "football", "checked_at": 0}
        self.assertTrue(fl._still_good(entry, 10 ** 10))


class TheCacheMeansOneLookupEver(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "lookups.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_a_cached_fixture_is_not_looked_up_again(self):
        fetch = Fetcher({"searchevents": sportsdb_event(
            "A vs B", "Soccer", "L1")})
        query = [("A", "B", "2026-08-30", "")]
        first = fl.verify_many(query, fetch=fetch, cache_path=self.path)
        calls = len(fetch.calls)
        self.assertTrue(list(first.values())[0]["confirmed"])

        second = fl.verify_many(query, fetch=fetch, cache_path=self.path)
        self.assertEqual(len(fetch.calls), calls, "it went back to the network")
        self.assertEqual(first, second)

    def test_the_key_does_not_care_which_side_is_named_first(self):
        self.assertEqual(fl.cache_key("Alpha", "Beta", "2026-08-30"),
                         fl.cache_key("beta", "ALPHA", "2026-08-30"))

    def test_the_same_fixture_twice_in_one_batch_is_one_lookup(self):
        fetch = Fetcher()
        fl.verify_many([("A", "B", "2026-08-30", ""),
                        ("B", "A", "2026-08-30", "")],
                       fetch=fetch, cache_path=self.path)
        events = [c for c in fetch.calls if "searchevents" in c]
        self.assertLessEqual(len(events), 2, fetch.calls)

    def test_the_budget_stops_a_scan_running_away(self):
        fetch = Fetcher()
        fixtures = [(f"Team{n}", f"Other{n}", "2026-08-30", "") for n in range(10)]
        answers = fl.verify_many(fixtures, fetch=fetch, cache_path=self.path,
                                 budget=3)
        self.assertEqual(len(answers), 3)

    def test_an_unreadable_cache_is_simply_empty(self):
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(fl.load_cache(self.path), {})


if __name__ == "__main__":
    unittest.main()
