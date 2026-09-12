"""Featured is an editorial answer, and these tests hold it to being a real one.

Four ways a Hero goes wrong, and the plan names all four.

It becomes Trending with extra steps: the top trending title is simply
promoted, so a film with no artwork, no metadata and an unstable stream
opens the home page. Trending is a candidate here, never a winner by itself.

It becomes a popularity chart of the wrong number: `available_link_count`
counts verified servers, and ranking by it puts a 2018 film with five
mirrors above this week's release. It appears in exactly one place, worth at
most a tenth of one weight, and a test holds that line.

It fills its slots: five slots exist, therefore five things are shown, and
the fifth is whatever was left. Fewer real items is the correct answer and
is what happens here.

It says things that are not true: a NEW badge on a two-year-old arrival, an
IMDb label on a TMDB score, a 4K tag inferred from a filename, a manual pin
that rewrites the title. Each of those has a test below.

The last group is the one that matters most in production: a failed build
must never replace a good featured.json with an empty one.
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scanner import movie_featured as mf


NOW = dt.datetime(2026, 9, 12, 12, 0, tzinfo=dt.timezone.utc)


def days_ago(count, reference=NOW):
    return (reference - dt.timedelta(days=count)).isoformat()


def movie(movie_id, **extra):
    """A record that passes eligibility, so each test changes only its point."""
    record = {
        "id": movie_id,
        "type": "movie",
        "name": extra.pop("name", movie_id.replace("-", " ").title()),
        "category": extra.pop("category", "Hindi"),
        "year": extra.pop("year", "2026"),
        "logo": extra.pop("logo", f"https://posters.example/{movie_id}.jpg"),
        "url": "https://streams.example/one.m3u8",
        "playback_id": f"ctv_{movie_id}",
        "verification_status": extra.pop("verification_status", "verified_global"),
        "verified": True,
        "available_link_count": extra.pop("available_link_count", 1),
        "resolution_height": extra.pop("resolution_height", 1080),
        "metadata_only": False,
    }
    record.update(extra)
    return record


def series(series_id, **extra):
    record = {
        "id": series_id,
        "type": "series",
        "content_kind": "series",
        "name": extra.pop("name", series_id.replace("-", " ").title()),
        "category": extra.pop("category", "Premium"),
        "year": extra.pop("year", "2026"),
        "logo": extra.pop("logo", f"https://posters.example/{series_id}.jpg"),
        "series_manifest": f"data/series/premium/{series_id}/index.json",
        "default_season": 1,
        "verification_status": "manual_trusted",
    }
    record.update(extra)
    return record


#: Enough distinct categories that the diversity cap is not what a test is
#: measuring unless it says so.
CATEGORIES = ("Bangla", "Hindi", "English", "Dubbed", "South Indian", "Premium", "Mix")


def spread(count, prefix="a", **extra):
    """`count` eligible films, each in its own category."""
    return [
        movie(f"{prefix}{i}", category=CATEGORIES[i % len(CATEGORIES)], **extra)
        for i in range(count)
    ]


def pin(item_id, **extra):
    entry = {"id": item_id, "enabled": True, "priority": 100}
    entry.update(extra)
    return entry


def build(candidates, **kwargs):
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("config", mf.load_config("does-not-exist.json"))
    return mf.build_featured(candidates, **kwargs)


def ids(document):
    return [entry["id"] for entry in document["items"]]


# ---------------------------------------------------------------------------


class ManualPinsWinAndOnlyChooseWhich(unittest.TestCase):
    def test_a_pin_outranks_every_auto_score(self):
        # The auto candidate is Premium, brand new and fully verified; the
        # pinned one is none of those. The pin still leads.
        document = build(
            [movie("dull-film"), movie("strong-film", category="Premium",
                                       first_seen_at=days_ago(0))],
            manual_items=[pin("dull-film")],
        )
        self.assertEqual(document["items"][0]["id"], "dull-film")
        self.assertEqual(document["items"][0]["source"], "manual")

    def test_a_pin_carries_no_score(self):
        document = build([movie("a")], manual_items=[pin("a")])
        self.assertIsNone(document["items"][0]["featured_score"])
        self.assertNotIn("score_breakdown", document["items"][0])

    def test_pins_sort_by_priority_descending(self):
        document = build(
            [movie("a"), movie("b"), movie("c")],
            manual_items=[pin("a", priority=10), pin("b", priority=90), pin("c", priority=50)],
        )
        self.assertEqual(ids(document)[:3], ["b", "c", "a"])

    def test_equal_priority_keeps_the_written_order_deterministically(self):
        pins = [pin("c", priority=50), pin("a", priority=50), pin("b", priority=50)]
        first = build([movie("a"), movie("b"), movie("c")], manual_items=pins)
        second = build([movie("a"), movie("b"), movie("c")], manual_items=list(pins))
        self.assertEqual(ids(first)[:3], ["c", "a", "b"])
        self.assertEqual(ids(first), ids(second))

    def test_five_manual_pins_leave_no_auto_slots(self):
        document = build(
            [movie(f"m{i}") for i in range(1, 6)] + [movie("auto-one")],
            manual_items=[pin(f"m{i}") for i in range(1, 6)],
        )
        self.assertEqual(document["manual_count"], 5)
        self.assertEqual(document["auto_count"], 0)
        self.assertNotIn("auto-one", ids(document))

    def test_seven_pins_are_cut_to_the_slot_count_by_priority(self):
        pins = [pin(f"m{i}", priority=100 - i) for i in range(1, 8)]
        document = build([movie(f"m{i}") for i in range(1, 8)], manual_items=pins)
        self.assertEqual(len(document["items"]), 5)
        self.assertEqual(ids(document), ["m1", "m2", "m3", "m4", "m5"])
        dropped = [row["id"] for row in document["selection"]["manual_dropped"]]
        self.assertEqual(dropped, ["m6", "m7"])

    def test_two_manual_leave_three_auto_slots(self):
        document = build(
            [movie("m1", category="Bangla"), movie("m2", category="Hindi")] + spread(5, "a"),
            manual_items=[pin("m1"), pin("m2")],
        )
        self.assertEqual((document["manual_count"], document["auto_count"]), (2, 3))

    def test_a_pin_cannot_restate_the_title_year_or_rating(self):
        document = build(
            [movie("a", name="Real Title", year="2019", rating=7.1, rating_source="IMDb")],
            manual_items=[pin("a", name="Fake Title", year="2026", rating=9.9,
                               custom_label="EDITOR'S PICK")],
        )
        entry = document["items"][0]
        self.assertEqual(entry["name"], "Real Title")
        self.assertEqual(entry["year"], "2019")
        self.assertEqual(entry["rating"], 7.1)
        self.assertEqual(entry["rating_source"], "IMDb")
        # Only the two UI-only decorations survive.
        self.assertEqual(entry["custom_label"], "EDITOR'S PICK")

    def test_a_pin_for_something_not_in_the_catalogue_is_reported_not_rendered(self):
        document = build([movie("a")], manual_items=[pin("ghost")])
        self.assertNotIn("ghost", ids(document))
        self.assertEqual(
            document["selection"]["manual_dropped"],
            [{"id": "ghost", "reason": "not in catalogue"}],
        )

    def test_a_pin_for_an_inactive_title_is_dropped(self):
        document = build([movie("a", is_active=False)], manual_items=[pin("a")])
        self.assertEqual(ids(document), [])
        self.assertEqual(document["selection"]["manual_dropped"][0]["reason"], "inactive")

    def test_a_pin_over_a_stale_title_is_honoured_but_flagged(self):
        document = build(
            [movie("a", verification_status="stale_last_good")], manual_items=[pin("a")]
        )
        self.assertEqual(ids(document), ["a"])
        self.assertIn("stale_last_good", document["items"][0]["manual_verification_note"])


class ManualTimeWindows(unittest.TestCase):
    def test_a_pin_before_its_start_takes_no_slot(self):
        document = build(
            [movie("a"), movie("b")],
            manual_items=[pin("a", start_at=(NOW + dt.timedelta(days=2)).isoformat())],
        )
        self.assertEqual(document["manual_count"], 0)
        self.assertIn("b", ids(document))

    def test_a_pin_inside_its_window_is_active(self):
        document = build(
            [movie("a")],
            manual_items=[pin("a", start_at=days_ago(1),
                               end_at=(NOW + dt.timedelta(days=1)).isoformat())],
        )
        self.assertEqual(document["manual_count"], 1)

    def test_an_expired_pin_blocks_nothing(self):
        document = build(
            spread(3, "a"), manual_items=[pin("a0", end_at=days_ago(1))]
        )
        self.assertEqual(document["manual_count"], 0)
        # The slot it would have taken is filled by a real auto candidate.
        self.assertEqual(len(document["items"]), 3)
        self.assertIn("a0", ids(document))

    def test_a_disabled_pin_is_inactive_even_inside_its_window(self):
        self.assertEqual(
            mf.manual_window_state(pin("a", enabled=False), NOW), "disabled"
        )

    def test_a_pin_with_no_window_is_simply_active(self):
        self.assertEqual(mf.manual_window_state(pin("a"), NOW), "active")


class WhatQualifiesAsACandidate(unittest.TestCase):
    def test_an_inactive_title_is_never_a_candidate(self):
        document = build([movie("a", is_active=False), movie("b")])
        self.assertEqual(ids(document), ["b"])
        self.assertEqual(document["selection"]["rejected"]["inactive"], 1)

    def test_a_title_with_no_stream_is_never_a_candidate(self):
        broken = movie("a")
        broken.pop("url")
        broken.pop("playback_id")
        document = build([broken, movie("b")])
        self.assertEqual(ids(document), ["b"])
        self.assertEqual(document["selection"]["rejected"]["no playable stream"], 1)

    def test_a_title_with_no_artwork_at_all_is_not_shown_in_a_hero(self):
        bare = movie("a")
        bare["logo"] = ""
        document = build([bare, movie("b")])
        self.assertEqual(ids(document), ["b"])
        self.assertEqual(document["selection"]["rejected"]["no usable artwork"], 1)

    def test_a_missing_backdrop_is_a_fallback_not_a_rejection(self):
        document = build([movie("a")])
        self.assertEqual(ids(document), ["a"])
        self.assertEqual(document["items"][0]["artwork_kind"], "poster_fallback")

    def test_a_real_backdrop_scores_higher_than_a_poster_fallback(self):
        with_backdrop = mf.artwork_score({"backdrop": "https://x/b.jpg"}, 8)
        poster_only = mf.artwork_score({"logo": "https://x/p.jpg"}, 8)
        self.assertEqual(with_backdrop, (8.0, "backdrop"))
        self.assertLess(poster_only[0], with_backdrop[0])
        self.assertEqual(poster_only[1], "poster_fallback")

    def test_a_title_only_republished_under_grace_is_not_auto_promoted(self):
        document = build([movie("a", verification_status="stale_last_good"), movie("b")])
        self.assertEqual(ids(document), ["b"])
        self.assertEqual(
            document["selection"]["rejected"]["verification not recent enough"], 1
        )

    def test_a_record_with_no_id_cannot_take_a_slot(self):
        ok, why = mf.eligibility({"name": "Nameless", "logo": "x", "url": "y"})
        self.assertFalse(ok)
        self.assertEqual(why, "no stable id")

    def test_a_series_is_playable_through_its_manifest(self):
        self.assertTrue(mf.is_playable(series("s1")))
        without = series("s1")
        without["series_manifest"] = ""
        self.assertFalse(mf.is_playable(without))


class Scoring(unittest.TestCase):
    def test_trending_rank_one_earns_the_full_weight(self):
        self.assertEqual(mf.trending_score(1, 20, 40), 40.0)

    def test_trending_decays_down_the_list(self):
        self.assertGreater(mf.trending_score(2, 20, 40), mf.trending_score(15, 20, 40))

    def test_no_trending_rank_earns_nothing(self):
        self.assertEqual(mf.trending_score(None, 20, 40), 0.0)

    def test_latest_uses_the_release_date_and_nothing_else(self):
        recent = mf.latest_score("2026-09-09", 20, window_days=365, now=NOW)
        old = mf.latest_score("2024-03-01", 20, window_days=365, now=NOW)
        self.assertGreater(recent, old)

    def test_a_bare_year_is_not_a_release_date(self):
        # Accepting "2010" would mean inventing January the first.
        self.assertEqual(mf.latest_score("2010", 20, window_days=365, now=NOW), 0.0)

    def test_an_unreleased_film_earns_no_latest_score(self):
        future = (NOW + dt.timedelta(days=30)).date().isoformat()
        self.assertEqual(mf.latest_score(future, 20, window_days=365, now=NOW), 0.0)

    def test_just_added_uses_first_seen_and_is_independent_of_release(self):
        # An old film added today: Just Added yes, Latest no. This is the
        # whole reason the two signals are separate.
        old_film_new_here = movie("a", first_seen_at=days_ago(0), release_date="2010-04-02")
        total, breakdown, _ = mf.score_candidate(
            old_film_new_here, config=mf.load_config("nope.json"), now=NOW
        )
        self.assertGreater(breakdown["just_added"], 0)
        self.assertEqual(breakdown["latest"], 0.0)

    def test_just_added_decays_out_of_its_window(self):
        self.assertEqual(
            mf.just_added_score(days_ago(30), 12, window_days=14, now=NOW), 0.0
        )

    def test_a_future_arrival_stamp_cannot_pin_itself_to_the_top(self):
        ahead = (NOW + dt.timedelta(days=3)).isoformat()
        self.assertEqual(mf.just_added_score(ahead, 12, window_days=14, now=NOW), 0.0)

    def test_premium_is_a_signal_not_an_automatic_slot(self):
        config = mf.load_config("nope.json")
        self.assertEqual(mf.premium_score(movie("a", category="Premium"), 10, []), 10.0)
        self.assertEqual(mf.premium_score(movie("a", category="Hindi"), 10, []), 0.0)
        # Six Premium films, and the category cap still holds.
        document = build([movie(f"p{i}", category="Premium") for i in range(6)], config=config)
        self.assertEqual(len(document["items"]), 2)

    def test_available_link_count_is_a_tie_break_and_cannot_outrank_a_trend(self):
        weights = mf.DEFAULT_CONFIG["weights"]
        many_links = mf.playback_score(movie("a", available_link_count=99), weights["playback"])
        one_link = mf.playback_score(movie("b", available_link_count=1), weights["playback"])
        # Its whole possible contribution is smaller than one place of trend.
        self.assertLessEqual(many_links - one_link, weights["playback"] * 0.1 + 1e-9)
        self.assertLess(many_links - one_link, mf.trending_score(20, 20, weights["trending"]) + 1.0)

    def test_link_count_never_reorders_two_differently_trending_titles(self):
        config = mf.load_config("nope.json")
        trending = {
            "freshness": "fresh",
            "movies": [{"id": "ranked-first", "trending_rank": 1},
                       {"id": "ranked-last", "trending_rank": 20}],
        }
        document = build(
            [movie("ranked-last", available_link_count=99, category="Hindi"),
             movie("ranked-first", available_link_count=1, category="English")],
            config=config,
            trending_document=trending,
        )
        self.assertEqual(ids(document)[0], "ranked-first")

    def test_metadata_completeness_rewards_having_more_of_it(self):
        bare = mf.metadata_score(movie("a"), 4)
        full = mf.metadata_score(
            movie("a", genres=["Action"], rating=7.2, backdrop="x",
                  plot="A real overview.", release_date="2026-01-01"),
            4,
        )
        self.assertGreater(full, bare)
        self.assertEqual(full, 4.0)

    def test_the_breakdown_is_published_so_a_slot_can_be_explained(self):
        document = build([movie("a")])
        self.assertEqual(
            sorted(document["items"][0]["score_breakdown"]),
            ["artwork", "just_added", "latest", "metadata", "playback", "premium", "trending"],
        )


class TrendingIsACandidateNotAWinner(unittest.TestCase):
    def test_the_top_trending_title_is_not_automatically_featured(self):
        config = mf.load_config("nope.json")
        trending = {"freshness": "fresh", "movies": [{"id": "thin", "trending_rank": 1}]}
        # Trending #1 with no artwork cannot be shown at all.
        thin = movie("thin")
        thin["logo"] = ""
        document = build([thin, movie("solid")], config=config, trending_document=trending)
        self.assertEqual(ids(document), ["solid"])

    def test_an_expired_trending_snapshot_contributes_nothing(self):
        config = mf.load_config("nope.json")
        trending = {"freshness": "expired", "movies": [{"id": "a", "trending_rank": 1}]}
        document = build([movie("a")], config=config, trending_document=trending)
        self.assertEqual(document["items"][0]["score_breakdown"]["trending"], 0.0)
        self.assertFalse(document["signals"]["trending_available"])
        self.assertNotIn("TRENDING", document["items"][0].get("badges", []))

    def test_trending_being_unavailable_never_empties_the_row(self):
        # The plan's fallback order: with no external signal at all, the
        # remaining real internal signals still produce a real Hero.
        document = build(spread(5, "a", first_seen_at=days_ago(1)), trending_document={})
        self.assertEqual(len(document["items"]), 5)
        self.assertFalse(document["signals"]["trending_available"])

    def test_the_output_names_which_signals_actually_contributed(self):
        document = build([movie("a", first_seen_at=days_ago(1))], trending_document={})
        self.assertNotIn("trending", document["signals"]["signals_contributing"])
        self.assertIn("just_added", document["signals"]["signals_contributing"])


class DuplicatesAndDiversity(unittest.TestCase):
    def test_one_title_cannot_hold_two_slots(self):
        document = build([movie("a"), movie("a")])
        self.assertEqual(ids(document), ["a"])

    def test_a_shared_tmdb_id_is_one_film_however_it_is_published(self):
        document = build([
            movie("bangla-copy", tmdb_id="550", category="Bangla"),
            movie("dubbed-copy", tmdb_id="550", category="Dubbed"),
        ])
        self.assertEqual(len(document["items"]), 1)

    def test_the_same_film_under_two_categories_is_not_shown_twice(self):
        # No external id anywhere - title and year are the last line.
        document = build([
            movie("a", name="Rongin Shurma", year="2026", category="Bangla"),
            movie("b", name="Rongin  Shurma", year="2026", category="Dubbed"),
        ])
        self.assertEqual(len(document["items"]), 1)

    def test_neither_copy_is_merged_or_deleted_by_the_hero(self):
        # Both records go in; the Hero drops a SLOT, not a catalogue entry.
        catalogue = [
            movie("a", name="Same Film", year="2026", category="Bangla"),
            movie("b", name="Same Film", year="2026", category="Dubbed"),
        ]
        before = json.dumps(catalogue, sort_keys=True)
        build(catalogue)
        self.assertEqual(json.dumps(catalogue, sort_keys=True), before)

    def test_one_category_cannot_take_every_slot(self):
        document = build([movie(f"h{i}", category="Hindi") for i in range(6)])
        self.assertEqual(len(document["items"]), 2)

    def test_a_mixed_catalogue_fills_five_slots_across_categories(self):
        catalogue = []
        for category in ("Bangla", "Hindi", "English", "South Indian", "Premium"):
            catalogue.append(movie(category.lower().replace(" ", "-"), category=category))
        document = build(catalogue)
        self.assertEqual(len(document["items"]), 5)
        self.assertEqual(len({entry["category"] for entry in document["items"]}), 5)

    def test_series_are_supported_as_candidates(self):
        document = build([series("show-a", category="Dubbed"), movie("film-a")])
        kinds = {entry["type"] for entry in document["items"]}
        self.assertIn("series", kinds)

    def test_series_cannot_take_more_than_their_slot_cap(self):
        document = build([series(f"s{i}", category=f"Cat{i}") for i in range(5)])
        self.assertEqual(
            sum(1 for entry in document["items"] if entry["type"] == "series"), 2
        )

    def test_a_series_entry_carries_what_the_series_flow_needs(self):
        document = build([series("show-a")])
        entry = document["items"][0]
        self.assertEqual(entry["type"], "series")
        self.assertTrue(entry["series_manifest"])
        self.assertIn("SERIES", entry["badges"])


class CooldownAndRotation(unittest.TestCase):
    def test_a_recently_featured_title_steps_aside(self):
        # Four other real candidates exist, so nothing has to relax for the
        # Hero to reach its minimum - the cooled title is simply skipped.
        history = {"id:cooled": {"last_featured_at": days_ago(1)}}
        catalogue = [movie("cooled", category="Bangla")] + [
            movie(f"fresh{i}", category=cat)
            for i, cat in enumerate(("Hindi", "English", "Dubbed", "Premium"))
        ]
        document = build(catalogue, history=history)
        self.assertNotIn("cooled", ids(document))
        self.assertEqual(len(document["items"]), 4)
        self.assertFalse(document["selection"]["cooldown_relaxed"])

    def test_cooldown_expires(self):
        history = {"id:a": {"last_featured_at": days_ago(5)}}
        document = build([movie("a")], history=history)
        self.assertEqual(ids(document), ["a"])

    def test_a_manual_pin_ignores_cooldown_entirely(self):
        history = {"id:a": {"last_featured_at": days_ago(0)}}
        document = build([movie("a")], manual_items=[pin("a")], history=history)
        self.assertEqual(ids(document), ["a"])

    def test_cooldown_relaxes_rather_than_starving_the_hero(self):
        # Every candidate is inside its cooldown. Leaving the row under the
        # minimum would be worse than reusing real, still-eligible titles -
        # and the relaxation is recorded rather than hidden.
        history = {f"id:a{i}": {"last_featured_at": days_ago(0)} for i in range(5)}
        document = build([movie(f"a{i}", category=f"Cat{i}") for i in range(5)], history=history)
        self.assertGreaterEqual(len(document["items"]), 3)
        self.assertTrue(document["selection"]["cooldown_relaxed"])

    def test_diversity_never_relaxes_even_to_reach_the_minimum(self):
        # Six Hindi films and a minimum of three. Relaxing the cap here is
        # how a Hero ends up showing five titles from one language, which is
        # the outcome the plan names outright - so two is the answer, and
        # the output says why rather than quietly filling.
        document = build([movie(f"h{i}", category="Hindi") for i in range(6)])
        self.assertEqual(len(document["items"]), 2)
        self.assertFalse(document["selection"]["diversity_relaxed"])
        self.assertIn("2 of 5", document["shortfall_reason"])

    def test_history_counts_thirty_days_from_the_run_list_not_by_incrementing(self):
        history = {"id:a": {"last_featured_at": days_ago(2),
                            "runs": [days_ago(2), days_ago(40)]}}
        updated = mf.record_history(history, ["id:a"], now=NOW)
        # The 40-day-old run is dropped; two remain (one kept, one new).
        self.assertEqual(updated["id:a"]["featured_count_30d"], 2)

    def test_history_is_keyed_on_the_same_identity_the_cooldown_reads(self):
        document = build([movie("a", tmdb_id="550")])
        self.assertEqual(document["items"][0]["identity"], "tmdb:550")


class RealBadgesOnly(unittest.TestCase):
    def setUp(self):
        self.config = mf.load_config("nope.json")

    def test_new_means_it_really_arrived_recently(self):
        self.assertIn("NEW", mf.badges_for(movie("a", first_seen_at=days_ago(1)),
                                           config=self.config, now=NOW))

    def test_new_is_not_shown_for_an_old_arrival(self):
        self.assertNotIn("NEW", mf.badges_for(movie("a", first_seen_at=days_ago(60)),
                                              config=self.config, now=NOW))

    def test_new_is_not_shown_when_nothing_is_known_about_arrival(self):
        self.assertNotIn("NEW", mf.badges_for(movie("a"), config=self.config, now=NOW))

    def test_trending_needs_a_rank_and_a_fresh_snapshot(self):
        self.assertIn("TRENDING", mf.badges_for(movie("a"), config=self.config,
                                                trending_rank=3, trending_fresh=True, now=NOW))
        self.assertNotIn("TRENDING", mf.badges_for(movie("a"), config=self.config,
                                                   trending_rank=3, trending_fresh=False, now=NOW))

    def test_premiere_needs_a_real_release_date_not_a_year(self):
        self.assertIn("PREMIERE", mf.badges_for(movie("a", release_date=days_ago(5)[:10]),
                                                config=self.config, now=NOW))
        self.assertNotIn("PREMIERE", mf.badges_for(movie("a", year="2026"),
                                                   config=self.config, now=NOW))

    def test_premium_means_the_premium_category(self):
        self.assertIn("PREMIUM", mf.badges_for(movie("a", category="Premium"),
                                               config=self.config, now=NOW))
        self.assertNotIn("PREMIUM", mf.badges_for(movie("a", category="Hindi"),
                                                  config=self.config, now=NOW))


class RatingAndQualityAreNeverInferred(unittest.TestCase):
    def test_a_title_with_no_rating_publishes_no_rating_field(self):
        entry = build([movie("a")])["items"][0]
        self.assertNotIn("rating", entry)
        self.assertNotIn("rating_source", entry)

    def test_a_rating_always_travels_with_the_source_that_issued_it(self):
        entry = build([movie("a", rating=8.1, rating_source="TMDB")])["items"][0]
        self.assertEqual((entry["rating"], entry["rating_source"]), (8.1, "TMDB"))

    def test_quality_comes_from_the_measured_height(self):
        self.assertEqual(mf.quality_label({"resolution_height": 2160}), "4K")
        self.assertEqual(mf.quality_label({"resolution_height": 1080}), "Full HD")
        self.assertEqual(mf.quality_label({"resolution_height": 720}), "HD")

    def test_four_k_is_never_inferred_from_a_title(self):
        record = movie("a", name="Big Movie 2026 4K UHD REMUX", resolution_height=720)
        self.assertEqual(mf.quality_label(record), "HD")
        self.assertEqual(build([record])["items"][0]["quality"], "HD")

    def test_an_unmeasured_stream_gets_no_quality_badge(self):
        record = movie("a")
        record["resolution_height"] = 0
        self.assertNotIn("quality", build([record])["items"][0])


class NothingIsPaddedAndNothingLeaks(unittest.TestCase):
    def test_two_real_candidates_produce_two_slots(self):
        document = build([movie("a", category="Hindi"), movie("b", category="English")])
        self.assertEqual(len(document["items"]), 2)
        self.assertIn("2 of 5", document["shortfall_reason"])

    def test_an_empty_catalogue_produces_an_empty_row_not_a_fabricated_one(self):
        document = build([])
        self.assertEqual(document["items"], [])
        self.assertEqual(document["count"], 0)

    def test_no_stream_field_reaches_the_published_document(self):
        document = build([movie("a", backups=["https://backup.example/x.m3u8"])])
        text = json.dumps(document)
        for field in mf.FORBIDDEN_FIELDS:
            self.assertNotIn(f'"{field}"', text, f"{field} leaked into featured.json")
        self.assertNotIn("streams.example", text)
        self.assertNotIn("backup.example", text)
        self.assertNotIn("ctv_", text)

    def test_ranks_are_contiguous_from_one(self):
        document = build([movie(f"a{i}", category=f"Cat{i}") for i in range(5)])
        self.assertEqual([e["featured_rank"] for e in document["items"]], [1, 2, 3, 4, 5])

    def test_the_label_travels_with_the_data(self):
        self.assertEqual(build([movie("a")])["label"], mf.DEFAULT_LABEL)
        self.assertNotIn("Trending", build([movie("a")])["label"])


class LastGoodProtection(unittest.TestCase):
    def test_an_empty_build_never_replaces_a_good_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "featured.json"
            good = build([movie("a"), movie("b", category="English")])
            mf.write_featured(good, str(target))
            kept = mf.write_featured(build([]), str(target))
            self.assertTrue(kept["preserved"])
            self.assertFalse(kept["written"])
            stored = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(len(stored["items"]), 2)

    def test_an_empty_build_with_no_existing_file_writes_the_empty_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "featured.json"
            result = mf.write_featured(build([]), str(target))
            self.assertTrue(result["written"])
            self.assertFalse(result["preserved"])

    def test_a_good_build_replaces_an_older_good_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "featured.json"
            mf.write_featured(build([movie("old")]), str(target))
            mf.write_featured(build([movie("new-one")]), str(target))
            stored = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual([e["id"] for e in stored["items"]], ["new-one"])

    def test_a_preserved_build_does_not_stamp_the_rotation_history(self):
        # Recording a run that never reached a viewer would put real titles
        # into cooldown for nothing.
        with tempfile.TemporaryDirectory() as tmp:
            featured = Path(tmp) / "featured.json"
            history = Path(tmp) / "history.json"
            mf.write_featured(build([movie("a")]), str(featured))
            result = mf.generate(
                {},  # no catalogue at all
                output_path=str(featured),
                config_path="nope.json",
                manual_path="nope.json",
                history_path=str(history),
                trending_path="nope.json",
                series_root=str(Path(tmp) / "no-series"),
                now=NOW,
            )
            self.assertTrue(result["preserved"])
            self.assertFalse(history.exists())


class Configuration(unittest.TestCase):
    def test_defaults_match_the_plan(self):
        config = mf.load_config("nope.json")
        self.assertEqual(config["slots"], 5)
        self.assertEqual(config["refresh_hours"], 12)
        self.assertEqual(config["hero_rotate_seconds"], 6.5)
        self.assertEqual(config["cooldown_hours"], 48)
        self.assertEqual(config["max_same_category"], 2)
        self.assertEqual(
            config["weights"],
            {"trending": 40, "latest": 20, "just_added": 12, "premium": 10,
             "artwork": 8, "playback": 6, "metadata": 4},
        )

    def test_the_shipped_config_file_loads_and_agrees_with_the_defaults(self):
        config = mf.load_config()
        self.assertEqual(config["slots"], 5)
        self.assertEqual(config["weights"]["trending"], 40)

    def test_one_weight_can_be_changed_without_restating_the_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"weights": {"trending": 10}}), encoding="utf-8")
            config = mf.load_config(str(path))
            self.assertEqual(config["weights"]["trending"], 10)
            self.assertEqual(config["weights"]["latest"], 20)

    def test_a_typo_falls_back_to_the_default_rather_than_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"slotz": 99, "slots": "five"}), encoding="utf-8")
            self.assertEqual(mf.load_config(str(path))["slots"], 5)

    def test_slots_cannot_exceed_the_maximum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"slots": 40}), encoding="utf-8")
            self.assertEqual(mf.load_config(str(path))["slots"], 6)

    def test_a_corrupt_config_file_does_not_stop_a_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(mf.load_config(str(path))["slots"], 5)


class ShippedStateFiles(unittest.TestCase):
    def test_the_manual_file_ships_empty_rather_than_with_an_example_pin(self):
        # An example entry here would be a fabricated editorial choice
        # sitting in production data.
        self.assertEqual(mf.load_manual(), [])

    def test_the_manual_file_offers_no_way_to_restate_a_fact(self):
        payload = json.loads(
            Path(mf.MANUAL_PATH).read_text(encoding="utf-8")
        )
        for field in ("title", "name", "year", "rating", "genres", "release_date"):
            self.assertNotIn(field, payload["_schema"])


if __name__ == "__main__":
    unittest.main()
