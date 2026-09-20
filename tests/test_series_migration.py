"""ধাপ ৩খ (part 2) - moving 390 cards without losing one stream.

This is the destructive step in the plan, so the tests are mostly about what
must *not* happen:

  * no stream may be lost - not by the split, not by the series pipeline's
    720p floor, and not by two cards colliding on one episode key;
  * no episode number may be invented - a season-only card becomes
    "Complete Season" or "Unspecified", never "Episode 1";
  * no second card may be created for a show the site already publishes;
  * no unproven card may move at all.

Three of these are regressions from faults this migration actually had when it
was first run against the real catalogue, and each one is named at its test.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import series, series_migration as sm, series_signal as ss  # noqa: E402


def _card(identity, name, url, backups=(), category="Mix", height=1080):
    return {
        "id": identity,
        "name": name,
        "url": url,
        "resolution_height": height,
        "resolution": "HD 1080P" if height >= 1080 else "HD 720P",
        "category": category,
        "backups": [
            {"url": backup, "resolution_height": height,
             "resolution": "HD 720P"} for backup in backups
        ],
    }


def _plan(cards, existing=None):
    signals = ss.classify_rows([ss.detect(card["name"]) for card in cards])
    return sm.plan_migration(cards, signals, existing_show_categories=existing)


def _urls(card):
    urls = [str(card.get("url") or "")] if card.get("url") else []
    urls += [str(b["url"]) for b in card.get("backups") or () if b.get("url")]
    return urls


def _plan_urls(plan):
    staying = [url for card in plan["staying"] for url in _urls(card)]
    moved = [
        link["url"]
        for item in plan["series_items"]
        for season in item["seasons"]
        for episode in season["episodes"]
        for link in episode["links"]
    ]
    return staying, moved


class NoStreamIsLostTests(unittest.TestCase):
    """The property the whole step is judged on."""

    def test_the_split_is_a_partition_of_every_link(self) -> None:
        cards = [
            _card("d2", "Drishyam 2", "https://c.test/d2.mkv",
                  ["https://b.test/d2.mkv"]),
            _card("bp1", "Bachelor Point S04E12", "https://c.test/bp12.mkv",
                  ["https://b.test/bp12.mkv"]),
            _card("bp2", "Bachelor Point S04E13", "https://c.test/bp13.mkv"),
            _card("lonely", "Lonely Show S01", "https://c.test/l.mkv"),
        ]
        before = sorted(url for card in cards for url in _urls(card))
        plan = _plan(cards)
        staying, moved = _plan_urls(plan)
        self.assertEqual(sorted(staying + moved), before)

    def test_a_card_whose_links_are_all_below_720_is_not_moved(self) -> None:
        """`series._normalize_sources` drops anything under 720 and then raises
        when nothing survives. Handing it such a card is a choice between
        losing its streams and killing the scan, and this step may do neither.
        """
        cards = [_card("sd", "Old Show S01E01", "https://c.test/sd.mkv",
                       height=480)]
        plan = _plan(cards)
        self.assertEqual(plan["series_items"], [])
        self.assertEqual(len(plan["staying"]), 1)
        self.assertEqual(
            plan["skipped"][0]["reason"], sm.SKIP_BELOW_MIN_HEIGHT)

    def test_a_card_with_one_good_link_moves_and_keeps_the_weak_one(self) -> None:
        card = _card("mix", "Show S01E01", "https://c.test/hd.mkv", height=1080)
        card["backups"] = [{"url": "https://c.test/sd.mkv",
                            "resolution_height": 480}]
        plan = _plan([card])
        _, moved = _plan_urls(plan)
        self.assertEqual(
            sorted(moved),
            ["https://c.test/hd.mkv", "https://c.test/sd.mkv"],
        )

    def test_a_marker_with_no_show_name_stays_a_movie_card(self) -> None:
        plan = _plan([_card("x", "S01E01", "https://c.test/x.mkv")])
        self.assertEqual(plan["series_items"], [])
        self.assertEqual(plan["skipped"][0]["reason"], sm.SKIP_NO_SHOW_KEY)

    def test_two_unnumbered_cards_in_one_season_stay_two_entries(self) -> None:
        """The regression that cost 2 streams on the first real run.

        Both got `episode_key: "complete-season"`, and
        `series._normalize_series` keeps "the first spelling" - right for a
        batch link beside its own episodes, wrong for two different uploads.
        """
        cards = [
            _card("a", "Money Heist S05 Complete Season",
                  "https://c.test/a.mkv"),
            _card("b", "Money Heist S05 Vol 2 Complete Season",
                  "https://c.test/b.mkv"),
        ]
        plan = _plan(cards)
        episodes = plan["series_items"][0]["seasons"][0]["episodes"]
        self.assertEqual(len(episodes), 2)
        self.assertNotEqual(
            episodes[0]["episode_key"], episodes[1]["episode_key"])
        _, moved = _plan_urls(plan)
        self.assertEqual(len(moved), 2)

    def test_two_cards_for_one_numbered_episode_merge_their_links(self) -> None:
        """Two sources for S01E05 are one episode with two links, not two
        episodes - and not one episode with the second link dropped."""
        cards = [
            _card("a", "Show S01E05", "https://c.test/a.mkv"),
            _card("b", "Show S01E05 Dual Audio", "https://c.test/b.mkv"),
        ]
        plan = _plan(cards)
        episodes = plan["series_items"][0]["seasons"][0]["episodes"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(
            sorted(link["url"] for link in episodes[0]["links"]),
            ["https://c.test/a.mkv", "https://c.test/b.mkv"],
        )

    def test_playback_metadata_travels_with_the_stream(self) -> None:
        """An episode that exists and does not play is a subtler way of losing
        a stream than deleting it."""
        card = _card("a", "Show S01E01", "https://c.test/a.mkv")
        card["header_profile"] = "web_referer"
        card["requires_headers"] = True
        plan = _plan([card])
        link = plan["series_items"][0]["seasons"][0]["episodes"][0]["links"][0]
        self.assertEqual(link["header_profile"], "web_referer")
        self.assertTrue(link["requires_headers"])


class NoEpisodeIsInventedTests(unittest.TestCase):
    """ধারা ৪.৬'s hardest rule, at the point it would be broken."""

    def test_an_entry_with_no_stated_number_reads_back_as_no_number(self) -> None:
        """`series.episode_number_range` reads the first digit run it finds, in
        the key and then in the label, so an entry whose source stated nothing
        must carry digits in neither."""
        for title, label in (
            ("Show S01", "Unspecified"),
            ("Show S02 Complete Season", "Complete Season"),
        ):
            with self.subTest(title=title):
                signal = ss.detect(title)
                key, produced = sm._episode_key_and_label(
                    signal, {"id": "card-" + title})
                self.assertEqual(produced, label)
                self.assertFalse(
                    any(character.isdigit() for character in key),
                    msg=f"{key!r} would be read as an episode number",
                )
                self.assertEqual(
                    series.episode_number_range(key, produced), (None, None))

    def test_a_part_sorts_by_its_part_number_but_is_never_called_an_episode(self) -> None:
        """"The Sandman S1 P01" is Season 1 Part 1. The source did state that
        number, so ordering by it is reading the source rather than inventing
        - but a Part is not an Episode, and the label a viewer sees says so.

        The key stays digit-free even here, so the number can only ever come
        from the label, where it is written as a Part.
        """
        signal = ss.detect("The Sandman S1 P01 Hindi Dubbed")
        key, label = sm._episode_key_and_label(signal, {"id": "sandman"})
        self.assertEqual(label, "Part 01")
        self.assertNotIn("Episode", label)
        self.assertFalse(any(character.isdigit() for character in key))

        record = sm.episode_record(
            _card("sandman", "The Sandman S1 P01", "https://c.test/s.mkv"),
            signal,
        )
        self.assertFalse(
            record["episode_number_stated"],
            msg="a part number is not an episode number a source published",
        )

    def test_a_numbered_entry_keeps_exactly_the_number_it_stated(self) -> None:
        for title, expected in (
            ("Show S01E05", (5, 5)),
            ("Show S06E11 20", (11, 20)),
            ("Show S01E01", (1, 1)),
        ):
            with self.subTest(title=title):
                signal = ss.detect(title)
                key, label = sm._episode_key_and_label(signal, {"id": "x"})
                self.assertEqual(
                    series.episode_number_range(key, label), expected)

    def test_the_record_says_whether_a_number_was_stated(self) -> None:
        stated = sm.episode_record(
            _card("a", "Show S01E05", "https://c.test/a.mkv"),
            ss.detect("Show S01E05"))
        unstated = sm.episode_record(
            _card("b", "Show S01", "https://c.test/b.mkv"),
            ss.detect("Show S01"))
        self.assertTrue(stated["episode_number_stated"])
        self.assertFalse(unstated["episode_number_stated"])

    def test_an_unproven_card_never_moves(self) -> None:
        """ধারা ৪.৬ tier 3 stays a visible movie card."""
        plan = _plan([_card("l", "Lonely Show S01", "https://c.test/l.mkv")])
        self.assertEqual(plan["series_items"], [])
        self.assertEqual(len(plan["staying"]), 1)


class ShowIdentityTests(unittest.TestCase):
    """ধারা ৪.৬ - no year in a series identity, and no second card."""

    def test_a_year_no_longer_splits_one_show_into_several_cards(self) -> None:
        """Live defect this fixes: Star Trek: Strange New Worlds is published
        as four cards (2022, 2023, 2025, 2026) holding 2+2+2+6 episodes."""
        identities = {
            series._series_identity({"name": "Star Trek: Strange New Worlds",
                                     "year": year, "category": "Premium"})
            for year in (2022, 2023, 2025, 2026)
        }
        self.assertEqual(len(identities), 1)

    def test_a_stray_season_marker_in_a_name_does_not_split_a_show(self) -> None:
        """This catalogue publishes a series named "Dirilis Ertugrul (Season 1"
        - a truncated line from the private TXT source."""
        self.assertEqual(
            series._series_identity(
                {"name": "Dirilis Ertugrul (Season 1", "category": "Dubbed"}),
            series._series_identity(
                {"name": "Dirilis Ertugrul", "category": "Dubbed"}),
        )

    def test_the_category_stays_in_the_identity(self) -> None:
        """Which shelf a show sits on is a publishing decision, not an identity
        claim, and moving shows between categories is ধাপ ১১'s job."""
        self.assertNotEqual(
            series._series_identity({"name": "Resort", "category": "Premium"}),
            series._series_identity({"name": "Resort", "category": "Dubbed"}),
        )

    def test_a_migrated_show_adopts_the_category_it_is_already_published_in(self) -> None:
        """Otherwise Resort - whose episodes are all filed under Mix - starts a
        third card instead of joining the one that exists."""
        plan = _plan(
            [_card("r", "Resort S01E05", "https://c.test/r.mkv",
                   category="Mix")],
            existing={"show:resort": "South Indian"},
        )
        self.assertEqual(plan["series_items"][0]["category"], "South Indian")

    def test_a_new_show_uses_the_owners_category_precedence(self) -> None:
        """Nine migrating shows span two categories. Peaky Blinders is 8 in Mix
        and 1 in English; the precedence pulls it out of the Mix junk drawer."""
        plan = _plan([
            _card("a", "Peaky Blinders S01E01", "https://c.test/a.mkv",
                  category="Mix"),
            _card("b", "Peaky Blinders S01E02", "https://c.test/b.mkv",
                  category="English"),
        ])
        self.assertEqual(plan["series_items"][0]["category"], "English")

    def test_choose_category_prefers_the_owners_order_over_the_count(self) -> None:
        self.assertEqual(
            sm.choose_category(Counter({"Mix": 8, "English": 1})), "English")
        self.assertEqual(sm.choose_category(Counter()), "Mix")

    def test_a_malformed_published_name_still_matches_its_show(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="pub-"))
        index = directory / "series" / "dubbed" / "index.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(json.dumps({"items": [
            {"name": "Dirilis Ertugrul (Season 1", "category": "Dubbed"}]}),
            encoding="utf-8")
        found = sm.published_show_categories(directory)
        self.assertEqual(found.get("show:dirilis-ertugrul"), "Dubbed")


class StagingTests(unittest.TestCase):
    """Running twice must not double a show's episodes."""

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="stage-")) / "staging.json"
        self.path.write_text(json.dumps({"items": [
            {"name": "Private Show", "category": "Dubbed", "year": 2026,
             "seasons": [{"number": 1, "episodes": []}]}]}), encoding="utf-8")

    def _items(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["items"]

    def test_migrated_records_are_appended_beside_the_private_ones(self) -> None:
        plan = _plan([_card("a", "Show S01E01", "https://c.test/a.mkv")])
        sm.stage_into_catalog(plan["series_items"], self.path)
        names = [item["name"] for item in self._items()]
        self.assertIn("Private Show", names)
        self.assertIn("Show", names)

    def test_a_second_run_replaces_rather_than_doubles(self) -> None:
        plan = _plan([_card("a", "Show S01E01", "https://c.test/a.mkv")])
        sm.stage_into_catalog(plan["series_items"], self.path)
        sm.stage_into_catalog(plan["series_items"], self.path)
        migrated = [i for i in self._items() if i.get("migrated_from_movies")]
        self.assertEqual(len(migrated), 1)
        self.assertEqual(len(self._items()), 2)

    def test_a_private_record_is_never_removed(self) -> None:
        sm.stage_into_catalog([], self.path)
        self.assertEqual(
            [item["name"] for item in self._items()], ["Private Show"])


class EndToEndTests(unittest.TestCase):
    """Through the real series pipeline, which is the only proof that counts."""

    def test_every_migrated_stream_survives_normalisation(self) -> None:
        cards = [
            _card("bp1", "Bachelor Point S04E12", "https://c.test/bp12.mkv",
                  ["https://b.test/bp12.mkv"]),
            _card("bp2", "Bachelor Point S04E13", "https://c.test/bp13.mkv"),
            _card("pack", "Dark Desire S02 Complete Season",
                  "https://c.test/dd.mkv"),
            _card("pb1", "Peaky Blinders S01 English", "https://c.test/pb.mkv"),
            _card("pb2", "Peaky Blinders S01E05", "https://c.test/pbe.mkv"),
        ]
        plan = _plan(cards)
        _, moved = _plan_urls(plan)

        directory = Path(tempfile.mkdtemp(prefix="e2e-"))
        staging = directory / "staging.json"
        staging.write_text(json.dumps({"items": []}), encoding="utf-8")
        sm.stage_into_catalog(plan["series_items"], staging)
        prepared = series.prepare_manual_series(
            project_root=directory, catalog_path=staging)

        published = set()
        for item in prepared["items"]:
            for episodes in (item.get("episode_payloads") or {}).values():
                for episode in episodes:
                    if episode.get("url"):
                        published.add(episode["url"])
                    for backup in episode.get("backups") or ():
                        if backup.get("url"):
                            published.add(backup["url"])
        self.assertEqual(sorted(published), sorted(moved))

    def test_an_unnumbered_entry_publishes_without_a_number(self) -> None:
        plan = _plan([
            _card("pack", "Dark Desire S02 Complete Season",
                  "https://c.test/dd.mkv"),
            _card("pb1", "Peaky Blinders S01 English", "https://c.test/pb.mkv"),
            _card("pb2", "Peaky Blinders S01E05", "https://c.test/pbe.mkv"),
        ])
        directory = Path(tempfile.mkdtemp(prefix="e2e2-"))
        staging = directory / "staging.json"
        staging.write_text(json.dumps({"items": []}), encoding="utf-8")
        sm.stage_into_catalog(plan["series_items"], staging)
        prepared = series.prepare_manual_series(
            project_root=directory, catalog_path=staging)

        for item in prepared["items"]:
            for episodes in (item.get("episode_payloads") or {}).values():
                for episode in episodes:
                    label = str(episode.get("episode_label") or "")
                    if label in {"Unspecified", "Complete Season"}:
                        self.assertIsNone(
                            episode.get("episode_start_number"), msg=label)
                        self.assertNotIn("Episode", label)


class RealCatalogueMigrationTests(unittest.TestCase):
    """Against the 1,667 cards the site is serving."""

    @classmethod
    def setUpClass(cls) -> None:
        from scanner import movie_baseline

        pairs = movie_baseline.published_movies(ROOT)
        if not pairs:
            raise unittest.SkipTest("no published movie catalogue")
        cls.cards = [
            dict(card, category=card.get("category") or category)
            for category, card in pairs
        ]
        cls.plan = _plan(
            cls.cards,
            existing=sm.published_show_categories(ROOT / "data"),
        )

    def test_the_split_loses_no_stream(self) -> None:
        before = sorted(url for card in self.cards for url in _urls(card))
        staying, moved = _plan_urls(self.plan)
        self.assertEqual(sorted(staying + moved), before)

    def test_no_card_is_skipped_for_a_reason_that_would_lose_it(self) -> None:
        """A skipped card stays a movie card, so a skip is safe - but it should
        be rare, and today it is zero."""
        for entry in self.plan["skipped"]:
            self.assertIn(
                entry["reason"],
                {sm.SKIP_BELOW_MIN_HEIGHT, sm.SKIP_NO_SHOW_KEY,
                 sm.SKIP_NO_PLAYABLE_LINK},
            )

    def test_no_migrated_episode_carries_an_invented_number(self) -> None:
        for item in self.plan["series_items"]:
            for season in item["seasons"]:
                for episode in season["episodes"]:
                    if not episode["episode_number_stated"]:
                        self.assertFalse(
                            any(c.isdigit() for c in episode["episode_key"]),
                            msg=episode["episode_key"],
                        )

    def test_the_shows_the_plan_names_become_one_card_each(self) -> None:
        by_key = {item["show_key"]: item for item in self.plan["series_items"]}
        bachelor = by_key.get("show:bachelor-point")
        self.assertIsNotNone(bachelor)
        self.assertEqual(
            sum(len(season["episodes"]) for season in bachelor["seasons"]), 37)

    def test_no_show_key_carries_a_year(self) -> None:
        import re

        for item in self.plan["series_items"]:
            self.assertIsNone(
                re.search(r"\b(?:19|20)\d{2}\b", item["show_key"]),
                msg=item["show_key"],
            )


if __name__ == "__main__":
    unittest.main()
