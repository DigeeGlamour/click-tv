"""ধাপ ১২ / A-05 - real episode names, and the one thing that must not move.

    ★ শর্ত: সোর্সের season/episode পরিচয় কখনো overwrite করা যাবে না।

The source knows which episode a file is, because it read it out of the
filename - `Chokro [S02E01]`. A provider matched the *show* by title and is
guessing at everything else. So a provider may say what episode 1 of season 2
is called; it may never say that this file is episode 3.

Get that wrong and the damage is not cosmetic: a viewer clicks episode 4 and
gets episode 7, and the catalogue has no way to notice, because both records
look perfectly well-formed.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import series_episode_metadata as sem  # noqa: E402

NOW = dt.datetime(2026, 9, 24, 4, 37, tzinfo=dt.timezone.utc)


def episode(season=2, number=1, **extra):
    card = {
        "id": f"chokro-2-2026-s{season:02d}-{number:02d}",
        "name": f"Chokro 2 - Episode {number:02d}",
        "title": f"Episode {number:02d}",
        "episode_title": f"Episode {number:02d}",
        "episode_label": f"Episode {number:02d}",
        "episode_key": f"{number:02d}",
        "episode_number": number,
        "number": number,
        "season_number": season,
        "series_id": "chokro-2-2026",
        "url": "https://cdn.example.com/chokro-s02e01.mkv",
    }
    card.update(extra)
    return card


class TheSourceIdentityIsNeverOverwritten(unittest.TestCase):
    """The starred condition, from every angle it could be broken."""

    def test_a_provider_cannot_change_which_episode_this_is(self):
        card = episode(season=2, number=1)
        record = {"episodes": {"S02E01": {
            "episode_name": "The Return",
            # A provider answering with identity fields - the exact attack.
            "season_number": 9,
            "episode_number": 7,
            "episode_key": "07",
            "title": "Episode 07",
        }}}
        sem.apply_to_episode(card, record)
        self.assertEqual(card["season_number"], 2)
        self.assertEqual(card["episode_number"], 1)
        self.assertEqual(card["episode_key"], "01")
        self.assertEqual(card["title"], "Episode 01")
        self.assertEqual(card["episode_name"], "The Return")

    def test_sanitise_drops_every_protected_field(self):
        """Filtered on the way IN, so nothing downstream has to remember."""
        noisy = {field: "hostile" for field in sem.PROTECTED}
        noisy["episode_name"] = "Kept"
        self.assertEqual(sem.sanitise(noisy), {"episode_name": "Kept"})

    def test_the_protected_list_covers_the_published_shape(self):
        """If the published record grows an identity field, it belongs here.
        These are the keys a real episode card carries."""
        for field in ("season_number", "episode_number", "episode_key",
                      "episode_label", "episode_title", "title", "number",
                      "id", "url"):
            self.assertIn(field, sem.PROTECTED)

    def test_nothing_outside_the_three_fields_is_ever_written(self):
        card = episode()
        before = dict(card)
        record = {"episodes": {"S02E01": {
            "episode_name": "A", "air_date": "2026-01-02", "still": "s.jpg",
            "verification_status": "broken", "url": "http://evil",
            "publish_allowed": False,
        }}}
        sem.apply_to_episode(card, record)
        changed = {k for k in card if card.get(k) != before.get(k)}
        self.assertEqual(changed, set(sem.EPISODE_FIELDS))

    def test_the_lookup_uses_the_source_numbers(self):
        """The reference is built from the CARD, never from the provider."""
        card = episode(season=2, number=3)
        record = {"episodes": {
            "S02E01": {"episode_name": "Wrong"},
            "S02E03": {"episode_name": "Right"},
        }}
        sem.apply_to_episode(card, record)
        self.assertEqual(card["episode_name"], "Right")

    def test_an_episode_the_provider_does_not_know_is_left_alone(self):
        card = episode(season=5, number=9)
        sem.apply_to_episode(card, {"episodes": {"S02E01": {"episode_name": "X"}}})
        self.assertNotIn("episode_name", card)

    def test_a_name_already_present_is_not_replaced(self):
        card = episode(episode_name="Already named")
        sem.apply_to_episode(
            card, {"episodes": {"S02E01": {"episode_name": "Provider name"}}})
        self.assertEqual(card["episode_name"], "Already named")


class TheEpisodeReference(unittest.TestCase):
    def test_it_is_zero_padded(self):
        self.assertEqual(sem.episode_ref(2, 7), "S02E07")

    def test_double_digit_seasons_and_episodes_work(self):
        self.assertEqual(sem.episode_ref(12, 137), "S12E137")

    def test_season_zero_is_a_real_season(self):
        """Specials are season 0 on TVMaze and TMDB alike."""
        self.assertEqual(sem.episode_ref(0, 1), "S00E01")

    def test_episode_zero_is_not(self):
        self.assertEqual(sem.episode_ref(1, 0), "")

    def test_nonsense_is_empty_not_an_exception(self):
        for season, number in ((None, 1), (1, None), ("a", "b"), (-1, 1)):
            self.assertEqual(sem.episode_ref(season, number), "")


class WhatCountsAsAnAnswer(unittest.TestCase):
    def test_an_air_date_must_be_a_real_date(self):
        """"2026" or "Spring 2026" in a date field renders as garbage."""
        self.assertEqual(sem.sanitise({"air_date": "2026"}), {})
        self.assertEqual(sem.sanitise({"air_date": "TBA"}), {})
        self.assertEqual(
            sem.sanitise({"air_date": "2026-01-02"}), {"air_date": "2026-01-02"})

    def test_empty_values_are_not_answers(self):
        self.assertEqual(
            sem.sanitise({"episode_name": "", "still": None, "air_date": []}), {})

    def test_a_non_dict_is_not_an_answer(self):
        self.assertEqual(sem.sanitise(None), {})
        self.assertEqual(sem.sanitise("Episode 1"), {})


class AskingOncePerShow(unittest.TestCase):
    """ধারা ৫ সংশোধন-২: the API is needed at show level only. 70 shows, not
    316 episodes."""

    def _series(self, name, **extra):
        card = {"name": name, "status": "ongoing"}
        card.update(extra)
        return card

    def test_a_show_nothing_is_known_about_is_due(self):
        self.assertTrue(sem.is_due(None, NOW))
        self.assertTrue(sem.is_due({}, NOW))

    def test_a_show_asked_about_recently_is_not_due_again(self):
        record = {"episodes": {"S01E01": {"episode_name": "A"}},
                  "updated_at": NOW.isoformat()}
        self.assertFalse(sem.is_due(record, NOW))

    def test_an_ongoing_show_comes_back_sooner_than_a_finished_one(self):
        """A finished show does not gain episodes; an ongoing one does."""
        self.assertLess(sem.refresh_ttl_days({"status": "ongoing"}),
                        sem.refresh_ttl_days({"status": "ended"}))

    def test_a_show_nothing_could_be_found_for_is_not_retried_at_once(self):
        record = {"episodes": {}, "last_attempt_at": NOW.isoformat()}
        self.assertFalse(sem.is_due(record, NOW))

    def test_but_it_is_retried_eventually(self):
        record = {"episodes": {},
                  "last_attempt_at": (NOW - dt.timedelta(days=30)).isoformat()}
        self.assertTrue(sem.is_due(record, NOW))

    def test_unknown_shows_are_asked_about_before_refreshes(self):
        store = {"shows": {sem.show_key({"name": "Known"}): {
            "episodes": {"S01E01": {"episode_name": "A"}},
            "updated_at": (NOW - dt.timedelta(days=90)).isoformat()}}}
        due = sem.due_shows(
            [self._series("Known"), self._series("Unknown")], store,
            budget=1, now=NOW)
        self.assertEqual([item["name"] for item in due], ["Unknown"])

    def test_one_show_listed_twice_is_asked_about_once(self):
        due = sem.due_shows(
            [self._series("Bachelor Point"), self._series("Bachelor Point")],
            {"shows": {}}, now=NOW)
        self.assertEqual(len(due), 1)

    def test_a_season_is_not_a_different_show(self):
        """ধারা ৪.৬: the identity is year-free and season-free, so season 2 of
        a show is not a second show to look up."""
        self.assertEqual(sem.show_key({"name": "Bachelor Point"}),
                         sem.show_key({"name": "Bachelor Point (2026)"}))

    def test_the_budget_is_respected(self):
        shows = [self._series(f"Show {index}") for index in range(50)]
        self.assertEqual(len(sem.due_shows(shows, {"shows": {}}, budget=5,
                                           now=NOW)), 5)


class RememberingAcrossRuns(unittest.TestCase):
    def test_an_answer_is_stored_under_the_show(self):
        store = {"shows": {}}
        sem.remember(store, "bachelor-point",
                     {"S01E01": {"episode_name": "Pilot"}},
                     provider="tvmaze", now=NOW)
        record = store["shows"]["bachelor-point"]
        self.assertEqual(record["episodes"]["S01E01"]["episode_name"], "Pilot")
        self.assertEqual(record["source"], "tvmaze")

    def test_a_later_answer_cannot_delete_an_earlier_name(self):
        """An episode that aired, was named, then dropped out of a provider's
        response is still that episode."""
        store = {"shows": {}}
        sem.remember(store, "x", {"S01E01": {"episode_name": "Pilot"},
                                  "S01E02": {"episode_name": "Second"}}, now=NOW)
        sem.remember(store, "x", {"S01E01": {"episode_name": "Renamed"}}, now=NOW)
        episodes = store["shows"]["x"]["episodes"]
        self.assertEqual(episodes["S01E01"]["episode_name"], "Pilot")
        self.assertIn("S01E02", episodes)

    def test_a_failed_lookup_records_the_attempt_and_nothing_else(self):
        store = {"shows": {}}
        sem.remember(store, "x", None, now=NOW)
        record = store["shows"]["x"]
        self.assertEqual(record["episodes"], {})
        self.assertIn("last_attempt_at", record)
        self.assertNotIn("updated_at", record)

    def test_a_failed_lookup_never_erases_what_was_known(self):
        store = {"shows": {}}
        sem.remember(store, "x", {"S01E01": {"episode_name": "Pilot"}}, now=NOW)
        sem.remember(store, "x", None, now=NOW)
        self.assertEqual(
            store["shows"]["x"]["episodes"]["S01E01"]["episode_name"], "Pilot")

    def test_identity_fields_cannot_enter_the_store(self):
        store = {"shows": {}}
        sem.remember(store, "x",
                     {"S01E01": {"episode_name": "A", "episode_number": 9}},
                     now=NOW)
        self.assertEqual(store["shows"]["x"]["episodes"]["S01E01"],
                         {"episode_name": "A"})

    def test_the_store_round_trips_through_disk(self):
        path = str(Path(tempfile.mkdtemp()) / "series-episode-metadata.json")
        store = sem.load(path)
        sem.remember(store, "x", {"S01E01": {"episode_name": "Pilot"}}, now=NOW)
        self.assertTrue(sem.save(store, path))
        self.assertEqual(
            sem.load(path)["shows"]["x"]["episodes"]["S01E01"]["episode_name"],
            "Pilot",
        )

    def test_a_damaged_store_starts_empty_rather_than_crashing(self):
        path = Path(tempfile.mkdtemp()) / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(sem.load(str(path))["shows"], {})


class ApplyingToTheWholeCatalogue(unittest.TestCase):
    def test_every_episode_of_a_known_show_is_filled(self):
        store = {"shows": {sem.show_key({"name": "Chokro 2"}): {"episodes": {
            "S02E01": {"episode_name": "One", "air_date": "2026-01-01"},
            "S02E02": {"episode_name": "Two", "still": "two.jpg"},
        }}}}
        series = {"name": "Chokro 2",
                  "episodes": [episode(number=1), episode(number=2)]}
        summary = sem.apply_all([series], store)
        self.assertEqual(summary["episodes_named"], 2)
        self.assertEqual(series["episodes"][0]["episode_name"], "One")
        self.assertEqual(series["episodes"][1]["still"], "two.jpg")

    def test_a_show_with_no_record_is_untouched(self):
        series = {"name": "Unknown Show", "episodes": [episode()]}
        before = dict(series["episodes"][0])
        sem.apply_all([series], {"shows": {}})
        self.assertEqual(series["episodes"][0], before)

    def test_no_provider_is_reachable_from_the_apply_path(self):
        """Cache only: this runs on the publish path, where an unbudgeted
        round of requests must not appear."""
        import ast

        source = (ROOT / "scanner" / "series_episode_metadata.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "apply_all")
        names = {node.attr for node in ast.walk(function)
                 if isinstance(node, ast.Attribute)}
        names |= {node.id for node in ast.walk(function)
                  if isinstance(node, ast.Name)}
        for forbidden in ("resolve", "_get_json", "tvmaze_episodes",
                          "tmdb_episodes", "cinemeta_episodes"):
            self.assertNotIn(forbidden, names)

    def test_an_empty_catalogue_is_not_an_error(self):
        self.assertEqual(sem.apply_all([], {"shows": {}})["episodes_named"], 0)

    def test_a_malformed_series_is_skipped(self):
        summary = sem.apply_all(["not a dict", None], {"shows": {}})
        self.assertEqual(summary["episodes_named"], 0)


class TheProviderAdapters(unittest.TestCase):
    """All three return the same shape, so the router can choose freely."""

    def _patch(self, payload):
        from scanner import metadata_providers

        original = metadata_providers._get_json
        metadata_providers._get_json = lambda *a, **k: payload
        self.addCleanup(setattr, metadata_providers, "_get_json", original)

    def test_tvmaze_reads_an_embedded_episode_list(self):
        self._patch({"_embedded": {"episodes": [
            {"season": 2, "number": 1, "name": "The Return",
             "airdate": "2026-03-04",
             "image": {"original": "https://img/still.jpg"}},
        ]}})
        found = sem.tvmaze_episodes({"name": "Chokro 2"})
        self.assertEqual(found["S02E01"]["episode_name"], "The Return")
        self.assertEqual(found["S02E01"]["air_date"], "2026-03-04")
        self.assertEqual(found["S02E01"]["still"], "https://img/still.jpg")

    def test_tvmaze_needs_a_title(self):
        self.assertIsNone(sem.tvmaze_episodes({}))

    def test_cinemeta_reads_the_videos_list(self):
        self._patch({"meta": {"videos": [
            {"season": 1, "episode": 4, "name": "Four",
             "released": "2026-05-06T00:00:00.000Z",
             "thumbnail": "https://img/4.jpg"},
        ]}})
        found = sem.cinemeta_episodes({"name": "X", "imdb_id": "tt123"})
        self.assertEqual(found["S01E04"]["episode_name"], "Four")
        self.assertEqual(found["S01E04"]["air_date"], "2026-05-06")

    def test_cinemeta_without_an_imdb_id_does_not_make_a_request(self):
        """`requires: ["imdb_id"]` in the router says the same thing; this is
        the adapter refusing rather than relying on being routed correctly."""
        called = []
        from scanner import metadata_providers
        original = metadata_providers._get_json
        metadata_providers._get_json = lambda *a, **k: called.append(1)
        self.addCleanup(setattr, metadata_providers, "_get_json", original)
        self.assertIsNone(sem.cinemeta_episodes({"name": "X"}))
        self.assertEqual(called, [])

    def test_tmdb_only_asks_about_seasons_the_site_carries(self):
        import os

        from scanner import metadata_providers

        urls = []
        original = metadata_providers._get_json
        os.environ["TMDB_API_KEY"] = "test-key"
        self.addCleanup(os.environ.pop, "TMDB_API_KEY", None)

        def fake(provider, url, **kwargs):
            urls.append(url)
            if "/search/tv" in url:
                return {"results": [{"id": 55}]}
            return {"episodes": [
                {"season_number": 2, "episode_number": 1, "name": "One",
                 "air_date": "2026-01-01", "still_path": "/s.jpg"}]}

        metadata_providers._get_json = fake
        self.addCleanup(setattr, metadata_providers, "_get_json", original)

        found = sem.tmdb_episodes({"name": "Chokro 2", "season_numbers": [2]})
        self.assertEqual(found["S02E01"]["episode_name"], "One")
        self.assertTrue(found["S02E01"]["still"].startswith("https://image.tmdb.org"))
        self.assertEqual(sum("/season/" in url for url in urls), 1)

    def test_an_adapter_that_answers_nothing_returns_none_not_an_empty_dict(self):
        """None means "this provider could not", which is what makes the
        router move on. An empty dict would look like a settled answer."""
        self._patch({"_embedded": {"episodes": []}})
        self.assertIsNone(sem.tvmaze_episodes({"name": "X"}))

    def test_a_provider_that_raises_does_not_escape_resolve(self):
        from scanner import metadata_providers

        original = metadata_providers._get_json

        def explode(*a, **k):
            raise RuntimeError("provider down")

        metadata_providers._get_json = explode
        self.addCleanup(setattr, metadata_providers, "_get_json", original)
        episodes, provider = sem.resolve({"name": "X"})
        self.assertIsNone(episodes)
        self.assertEqual(provider, "")

    def test_every_adapter_is_a_provider_the_router_knows(self):
        from scanner import provider_router

        for name in sem.ADAPTERS:
            self.assertIn(name, provider_router.DEFAULT_PROVIDERS)
            self.assertIn(
                provider_router.CAPABILITY_SERIES_METADATA,
                provider_router.DEFAULT_PROVIDERS[name]["capabilities"],
            )


class TheWiring(unittest.TestCase):
    SERIES = (ROOT / "scanner" / "series.py").read_text(encoding="utf-8")
    SCAN = (ROOT / "scan.py").read_text(encoding="utf-8")

    def test_the_lookup_is_off_unless_the_caller_asks(self):
        """Tests and ad-hoc calls to prepare_manual_series must never reach a
        provider - the same gate movies._annotate_metadata uses."""
        self.assertIn("allow_lookup: bool = False", self.SERIES)

    def test_only_the_real_publish_path_turns_it_on(self):
        call = self.SCAN[self.SCAN.index("prepared_series = prepare_manual_series"):]
        call = call[:call.index(")") + 1]
        self.assertIn("allow_lookup=True", call)

    def test_the_cache_is_applied_whether_or_not_lookups_ran(self):
        """A run that asks nothing still shows what earlier runs learned."""
        body = self.SERIES[self.SERIES.index("def _annotate_episode_metadata("):]
        body = body[:body.index("def prepare_manual_series(")]
        apply_at = body.index("episode_metadata.apply_all(")
        gate_at = body.index("if allow_lookup:")
        self.assertLess(gate_at, apply_at)
        # and the apply is not inside the gated block
        self.assertNotIn("        if allow_lookup:", body[apply_at:])

    def test_it_cannot_fail_the_series_catalogue(self):
        self.assertIn("episode metadata skipped", self.SERIES)

    def test_a_show_is_asked_about_once_not_once_per_season(self):
        """ধারা ৫ সংশোধন-২: show level only. The loop is over due SHOWS."""
        self.assertIn("for series in episode_metadata.due_shows(", self.SERIES)


class TheLogLine(unittest.TestCase):
    def test_it_reports_what_was_named(self):
        line = sem.describe({"episodes_named": 37, "shows_with_metadata": 2,
                             "episodes_dated": 37, "episodes_with_still": 30})
        self.assertIn("37 episode(s) named", line)
        self.assertIn("2 show(s)", line)

    def test_nothing_named_says_nothing(self):
        self.assertEqual(sem.describe({"episodes_named": 0}), "")
        self.assertEqual(sem.describe({}), "")


if __name__ == "__main__":
    unittest.main()
