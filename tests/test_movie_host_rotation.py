"""ধারা ৪.১ - a card follows its source to a new CDN host.

The defect this covers, measured on 2026-09-26 against the published
catalogue and the live `sm-movie-combined` feed:

    published movie cards                        1,917
      exact URL still offered by its source          0
      same path on a new host                    1,487
      path not offered at all                      430

Every one of the 1,487 pointed at a BunnyCDN pull zone that answers, for
every path including invented ones and from every vantage tried,

    "Domain suspended or not configured"

and every one of them was badged "Verified". `path_family` already knew the
feed rotates its hostname - it was added so the no-loss gate would accept the
rotation - but nothing moved the card, so the gate passed while 78% of the
catalogue offered viewers a link to a pull zone that no longer exists.

These tests fix the shape of the repair: narrow, evidence-led, and lossless.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import movie_host_rotation as hr  # noqa: E402

OLD = "https://qfdghnb.b-cdn.net/s3/upload/videos/2026/07/[Fibwatch.Com]Supergirl.(2026).Dual.720p.mkv"
NEW = "https://idfgbn.b-cdn.net/s3/upload/videos/2026/07/[Fibwatch.Com]Supergirl.(2026).Dual.720p.mkv"
OTHER = "https://odfgbv.b-cdn.net/s3/upload/videos/2026/07/[Fibwatch.Com]Supergirl.(2026).Dual.720p.mkv"


def _entry(url, source_id="sm-movie-combined", **extra):
    entry = {"url": url, "source_id": source_id}
    entry.update(extra)
    return entry


def _card(url=OLD, **extra):
    card = {
        "id": "supergirl-2026",
        "name": "Supergirl",
        "url": url,
        "source_id": "sm-movie-combined",
        "verification_status": "stale_last_good",
        "backups": [],
    }
    card.update(extra)
    return card


def _offers(*entries):
    return hr.offers_by_path(entries)


class ACardMovesWhenItsSourceHasMoved(unittest.TestCase):
    def setUp(self):
        self.offers = _offers(_entry(NEW))

    def test_the_url_becomes_the_one_the_source_offers(self):
        card = _card()
        hr.apply([card], self.offers)
        self.assertEqual(card["url"], NEW)

    def test_it_is_counted(self):
        summary = hr.apply([_card()], self.offers)
        self.assertEqual(summary[hr.ADOPTED], 1)

    def test_the_card_says_it_moved_and_from_where(self):
        card = _card()
        hr.apply([card], self.offers)
        self.assertIs(card["link_host_rotated"], True)
        self.assertEqual(card["previous_stream_host"], "qfdghnb.b-cdn.net")

    def test_the_headers_the_new_host_needs_come_with_it(self):
        """The retired zone did not check Referer; the live one does."""
        offers = _offers(_entry(
            NEW,
            headers={"Referer": "https://fibwatch.art/"},
            header_profile="android_tv",
            requires_headers=True,
        ))
        card = _card(headers={}, header_profile="none", requires_headers=False)
        hr.apply([card], offers)
        self.assertEqual(card["headers"], {"Referer": "https://fibwatch.art/"})
        self.assertEqual(card["header_profile"], "android_tv")
        self.assertIs(card["requires_headers"], True)

    def test_the_status_is_not_invented(self):
        """MOVIE_STATUS_PRIORITY is a closed vocabulary; a status outside it
        would sort the card off the end of its own category."""
        card = _card()
        hr.apply([card], self.offers)
        self.assertEqual(card["verification_status"], "stale_last_good")

    def test_the_id_never_moves(self):
        card = _card()
        hr.apply([card], self.offers)
        self.assertEqual(card["id"], "supergirl-2026")


class NothingIsLost(unittest.TestCase):
    """INVARIANT ২ asks whether every stream that was reaching viewers still
    appears in the published inventory. A rewrite that dropped the old URL
    would be a real live loss on the run that repaired the catalogue."""

    def test_the_retired_link_is_kept_as_a_backup(self):
        card = _card()
        hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual([b["url"] for b in card["backups"]], [OLD])

    def test_the_backup_is_marked_as_the_retired_host(self):
        card = _card()
        hr.apply([card], _offers(_entry(NEW)))
        self.assertIs(card["backups"][0]["retired_host"], True)

    def test_an_existing_backup_is_not_displaced(self):
        card = _card(backups=[{"url": "https://mirror.example/a.mkv"}])
        hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(
            [b["url"] for b in card["backups"]],
            ["https://mirror.example/a.mkv", OLD],
        )

    def test_the_same_url_is_not_added_twice_over_two_runs(self):
        card = _card()
        hr.apply([card], _offers(_entry(NEW)))
        card["url"] = OLD  # a later run carries the old card back
        hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual([b["url"] for b in card["backups"]], [OLD])

    def test_the_retired_link_is_kept_but_not_counted(self):
        """`available_link_count` promises a PLAYABLE route, and the retired
        host answers "Domain suspended or not configured" to everything. It
        stays in `backups` so INVARIANT 2 can still find the stream that was
        reaching viewers; it does not inflate the count."""
        card = _card()
        hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(card["available_link_count"], 1)
        self.assertEqual(len(card["backups"]), 1)

    def test_a_real_backup_is_still_counted(self):
        card = _card(backups=[{"url": "https://mirror.example/a.mkv"}])
        hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(card["available_link_count"], 2)
        self.assertEqual(len(card["backups"]), 2)

    def test_a_metadata_only_card_counts_no_primary(self):
        card = _card(metadata_only=True)
        hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(card["available_link_count"], 0)


class ACardStaysPutWithoutEvidence(unittest.TestCase):
    def test_a_url_the_source_still_offers_is_never_touched(self):
        card = _card(url=NEW)
        summary = hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(card["url"], NEW)
        self.assertEqual(summary[hr.STILL_OFFERED], 1)
        self.assertEqual(card["backups"], [])

    def test_a_path_nobody_offers_is_left_alone(self):
        card = _card(url="https://gone.example/s3/upload/videos/x/Nothing.Here.mkv")
        summary = hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(summary[hr.NO_OFFER], 1)
        self.assertNotIn("link_host_rotated", card)

    def test_a_second_host_offering_the_same_path_is_not_guessed(self):
        card = _card(source_id="", source_ids=[])
        summary = hr.apply([card], _offers(_entry(NEW, source_id="a"),
                                           _entry(OTHER, source_id="b")))
        self.assertEqual(summary[hr.AMBIGUOUS], 1)
        self.assertEqual(card["url"], OLD)

    def test_but_the_cards_own_source_breaks_the_tie(self):
        card = _card(source_id="sm-movie-combined")
        summary = hr.apply([card], _offers(
            _entry(OTHER, source_id="someone-else"),
            _entry(NEW, source_id="sm-movie-combined"),
        ))
        self.assertEqual(summary[hr.ADOPTED], 1)
        self.assertEqual(card["url"], NEW)

    def test_source_ids_counts_as_the_cards_own_source_too(self):
        card = _card(source_id="", source_ids=["sm-movie-combined"])
        hr.apply([card], _offers(
            _entry(OTHER, source_id="someone-else"),
            _entry(NEW, source_id="sm-movie-combined"),
        ))
        self.assertEqual(card["url"], NEW)

    def test_a_card_with_no_url_is_skipped(self):
        card = _card(url="")
        summary = hr.apply([card], _offers(_entry(NEW)))
        self.assertEqual(summary[hr.NO_PATH], 1)

    def test_a_non_dict_in_the_list_does_not_raise(self):
        hr.apply(["nonsense", None, 7], _offers(_entry(NEW)))


class AGenericPathIsNotAFilename(unittest.TestCase):
    """`/live` or `/index.m3u8` is served by thousands of unrelated hosts.
    A path match only means anything when the path names a file."""

    def test_a_short_path_is_refused(self):
        card = _card(url="https://a.example/live")
        summary = hr.apply([card], _offers(_entry("https://b.example/live")))
        self.assertIn(summary.get(hr.UNSPECIFIC_PATH) or summary.get(hr.NO_OFFER), (1,))
        self.assertEqual(card["url"], "https://a.example/live")

    def test_a_bare_directory_is_refused(self):
        card = _card(url="https://a.example/upload/videos/2026/")
        hr.apply([card], _offers(_entry("https://b.example/upload/videos/2026/")))
        self.assertEqual(card["url"], "https://a.example/upload/videos/2026/")

    def test_a_filename_with_a_directory_is_accepted(self):
        self.assertTrue(hr._specific("/s3/upload/videos/2026/07/Supergirl.mkv"))

    def test_the_offer_index_drops_generic_paths(self):
        self.assertEqual(_offers(_entry("https://b.example/live")), {})


class TheInlineHeaderSuffixIsNotPartOfThePath(unittest.TestCase):
    """The feed writes `...mkv|Referer=https://fibwatch.art/`."""

    def test_a_suffixed_offer_still_matches(self):
        card = _card()
        hr.apply([card], _offers(_entry(NEW + "|Referer=https://fibwatch.art/")))
        self.assertEqual(card["url"], NEW)

    def test_the_suffix_is_not_written_into_the_card(self):
        card = _card()
        hr.apply([card], _offers(_entry(NEW + "|Referer=https://fibwatch.art/")))
        self.assertNotIn("|", card["url"])


class TheRealRotationIsFollowed(unittest.TestCase):
    """The four retired hostnames, exactly as the catalogue held them."""

    RETIRED = (
        "qfdghnb.b-cdn.net",
        "jrtyh.b-cdn.net",
        "urtgh.b-cdn.net",
        "wrtyg.b-cdn.net",
    )

    def test_every_retired_host_moves_to_the_live_one(self):
        path = "/s3/upload/videos/2026/07/[Fibwatch.Com]Supergirl.(2026).Dual.720p.mkv"
        offers = _offers(_entry(f"https://idfgbn.b-cdn.net{path}"))
        for host in self.RETIRED:
            card = _card(url=f"https://{host}{path}")
            hr.apply([card], offers)
            self.assertEqual(
                card["url"], f"https://idfgbn.b-cdn.net{path}", host)


class TheWiringIsInPlace(unittest.TestCase):
    MOVIES = (ROOT / "scanner" / "movies.py").read_text(encoding="utf-8")
    SCAN = (ROOT / "scan.py").read_text(encoding="utf-8")

    def test_the_repair_runs_after_retention(self):
        """A carried card is carried *because* its URL was not found, so it is
        the card that needs this most."""
        body = self.MOVIES
        retain = body.index("prepared = _retain_recent_dropouts(")
        adopt = body.index("_adopt_rotated_hosts(prepared, category_slug)")
        self.assertLess(retain, adopt)

    def test_the_repair_runs_before_the_badge_and_poster_passes(self):
        body = self.MOVIES
        adopt = body.index("_adopt_rotated_hosts(prepared, category_slug)")
        badge = body.index("_unplayable = _mark_unplayable_links(prepared)")
        self.assertLess(adopt, badge)

    def test_the_offers_come_from_what_the_sources_produced(self):
        """Not from bd-results: an entry on a host this run has never reached
        is exactly the entry a rotation repair needs, and it is the one the
        health check cannot confirm."""
        self.assertIn("source_entries=list(normalized.get(", self.SCAN)
        self.assertIn('"raw_collected_items"', self.SCAN)

    def test_a_failure_here_cannot_take_a_scan_down(self):
        block = self.MOVIES[self.MOVIES.index("def _adopt_rotated_hosts("):]
        block = block[:block.index("\n\ndef ")]
        self.assertIn("except Exception", block)
        self.assertIn("return 0", block)

    def test_process_movies_still_works_with_no_entries_passed(self):
        self.assertIn(
            "source_entries if source_entries is not None else movie_candidates",
            self.MOVIES,
        )


if __name__ == "__main__":
    unittest.main()


class ThePosterFollowsTheFilm(unittest.TestCase):
    """Measured on 2026-09-26: 1,112 of 1,917 cards carried an image proxy
    URL wrapping one of the retired pull zones, so the 403 the viewer got was
    BunnyCDN's suspended-domain page passed through, and 58% of the catalogue
    showed the placeholder. Rewriting the hostname inside that query restored
    20 of 20 sampled images as real JPEG bytes."""

    PROXY = ("https://srhady-live-stream.hf.space/image?url="
             "https://qfdghnb.b-cdn.net/upload/photos/2023/08/JdkCyEyBaa.jpg")
    FIXED = ("https://srhady-live-stream.hf.space/image?url="
             "https://idfgbn.b-cdn.net/upload/photos/2023/08/JdkCyEyBaa.jpg")

    def _moved(self):
        card = _card(logo=self.PROXY)
        hr.apply([card], _offers(_entry(NEW)))
        return card

    def test_the_map_is_learned_from_cards_that_actually_moved(self):
        card = self._moved()
        self.assertEqual(
            hr.observed_host_map([card]),
            {"qfdghnb.b-cdn.net": "idfgbn.b-cdn.net"},
        )

    def test_the_embedded_host_is_rewritten(self):
        card = self._moved()
        hr.repair_posters([card], hr.observed_host_map([card]))
        self.assertEqual(card["logo"], self.FIXED)

    def test_the_proxy_is_kept(self):
        """Fetched directly the live zone answers 403 - it is the proxy that
        supplies the Referer the zone now requires."""
        card = self._moved()
        hr.repair_posters([card], hr.observed_host_map([card]))
        self.assertTrue(card["logo"].startswith("https://srhady-live-stream.hf.space/"))

    def test_the_card_says_its_poster_moved(self):
        card = self._moved()
        hr.repair_posters([card], hr.observed_host_map([card]))
        self.assertIs(card["poster_host_rotated"], True)

    def test_a_card_whose_own_link_never_rotated_is_still_repaired(self):
        """The map is catalogue-wide; a private-source card with a poster on a
        retired zone gets it back too."""
        other = {"id": "x", "url": "https://pub-abc.r2.dev/a.mkv", "logo": self.PROXY}
        card = self._moved()
        hr.repair_posters([other], hr.observed_host_map([card]))
        self.assertEqual(other["logo"], self.FIXED)

    def test_an_unrelated_poster_is_untouched(self):
        card = self._moved()
        other = {"id": "y", "logo": "https://image.tmdb.org/t/p/w500/a.jpg"}
        hr.repair_posters([other], hr.observed_host_map([card]))
        self.assertEqual(other["logo"], "https://image.tmdb.org/t/p/w500/a.jpg")
        self.assertNotIn("poster_host_rotated", other)

    def test_a_host_seen_rotating_two_ways_is_dropped(self):
        a = _card(url=OLD); b = _card(url=OLD)
        hr.apply([a], _offers(_entry(NEW)))
        b["link_host_rotated"] = True
        b["previous_stream_host"] = "qfdghnb.b-cdn.net"
        b["url"] = OTHER
        self.assertEqual(hr.observed_host_map([a, b]), {})

    def test_an_empty_map_repairs_nothing(self):
        card = {"id": "z", "logo": self.PROXY}
        self.assertEqual(hr.repair_posters([card], {}), 0)
        self.assertEqual(card["logo"], self.PROXY)

    def test_a_missing_poster_field_is_survived(self):
        hr.repair_posters([{"id": "n"}, None, "junk"],
                          {"qfdghnb.b-cdn.net": "idfgbn.b-cdn.net"})

    def test_it_is_wired_in_after_the_link_repair(self):
        body = TheWiringIsInPlace.MOVIES
        link = body.index("summary = movie_host_rotation.apply(")
        poster = body.index("movie_host_rotation.repair_posters(")
        self.assertLess(link, poster)

    def test_the_map_is_cleared_between_runs(self):
        self.assertIn("_ROTATED_HOSTS.clear()", TheWiringIsInPlace.MOVIES)
