"""A route measured unplayable in a real browser must never lead a card.

The ledger these tests write is a temporary one, so nothing here depends on
what the live ledger happens to hold today.
"""
import json
import tempfile
import unittest
from pathlib import Path

from scanner import playback_evidence, unplayable_primary as up


DEAD = "http://rgkkw.live/live/1Aoen7elp5/IgMJ60tmAa/98881.ts"
ALIVE = "https://stream.ottplus.bd/live/zee_bangla_abr/index.m3u8"
ALSO_DEAD = "http://wo0dyefk.dienalt.org/iptv/DV3AC/98881.ts"


#: A name deliberately absent from state/sustained-playback-proof.json. Using
#: a real channel here made every hide test fail, because a channel somebody
#: restored on a sustained measurement is protected from this - which is the
#: right behaviour and the wrong fixture.
UNPROTECTED = "Test Channel Alpha"
#: One of the seven channels restored by hand, which must stay visible.
PROTECTED = "Channel 1 NEWS"


def card(url, backups=(), name=UNPROTECTED, **extra):
    row = {"name": name, "category": "Indian", "url": url,
           "header_profile": "android_tv", "proxy_mode": "proxy_only",
           "stream_type": "hls", "playback_id": "ctv_primary",
           "backups": [dict(b) for b in backups]}
    row.update(extra)
    return row


class Ledger(unittest.TestCase):
    """Every test gets its own ledger file, written before the module reads it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "measured-playback-failures.json"
        self.path.write_text(json.dumps({"routes": {
            "rgkkw.live/live/1Aoen7elp5/IgMJ60tmAa/98881.ts": {
                "reason": "playback never started within the window",
                "vantage": "bangladesh-residential"},
            "wo0dyefk.dienalt.org/iptv/DV3AC/98881.ts": {
                "reason": "media progress 0.12s < 10s",
                "vantage": "bangladesh-residential"},
        }}), encoding="utf-8")
        self._real = playback_evidence.DEFAULT_PATH
        playback_evidence.DEFAULT_PATH = str(self.path)
        playback_evidence.reset_cache()

    def tearDown(self):
        playback_evidence.DEFAULT_PATH = self._real
        playback_evidence.reset_cache()
        self.dir.cleanup()


class APlayableBackupIsPromoted(Ledger):
    def test_the_dead_primary_is_replaced(self):
        row = card(DEAD, [{"url": ALIVE, "header_profile": "android_chrome",
                           "stream_type": "hls", "playback_id": "ctv_backup"}])
        promoted, hidden, report = up.enforce([row])
        self.assertEqual((promoted, hidden), (1, 0))
        self.assertEqual(row["url"], ALIVE)
        self.assertIsNot(row.get("publish_allowed"), False)
        self.assertTrue(row["primary_promoted_from_backup"])

    def test_the_whole_route_moves_not_just_the_url(self):
        # Header profile, stream type and playback id belong to the route.
        # Moving the URL alone would point the player at one host carrying
        # another host's headers.
        row = card(DEAD, [{"url": ALIVE, "header_profile": "android_chrome",
                           "stream_type": "dash", "playback_id": "ctv_backup"}])
        up.enforce([row])
        self.assertEqual(row["header_profile"], "android_chrome")
        self.assertEqual(row["stream_type"], "dash")
        self.assertEqual(row["playback_id"], "ctv_backup")

    def test_the_old_primary_is_kept_as_a_backup(self):
        row = card(DEAD, [{"url": ALIVE, "playback_id": "ctv_backup"}])
        up.enforce([row])
        self.assertEqual([b["url"] for b in row["backups"]], [DEAD])
        self.assertEqual(row["backups"][0]["playback_id"], "ctv_primary")

    def test_a_field_the_backup_lacks_is_not_inherited(self):
        # The promoted route has no proxy_mode, so the card must not keep the
        # dead route's - that is how a working URL gets sent through a proxy
        # that was only ever right for the other host.
        row = card(DEAD, [{"url": ALIVE}])
        up.enforce([row])
        self.assertNotIn("proxy_mode", row)

    def test_the_first_playable_backup_wins(self):
        row = card(DEAD, [{"url": ALSO_DEAD}, {"url": ALIVE}])
        up.enforce([row])
        self.assertEqual(row["url"], ALIVE)


class WithNoPlayableRouteTheCardIsHeldBack(Ledger):
    def test_it_is_hidden_rather_than_published(self):
        row = card(DEAD)
        promoted, hidden, report = up.enforce([row])
        self.assertEqual((promoted, hidden), (0, 1))
        self.assertIs(row["publish_allowed"], False)
        self.assertEqual(row["player_visibility"], "hidden_measured_unplayable")
        self.assertEqual(row["measured_unplayable_reason"],
                         "playback never started within the window")

    def test_every_backup_being_dead_too_is_the_same_case(self):
        row = card(DEAD, [{"url": ALSO_DEAD}])
        _, hidden, _ = up.enforce([row])
        self.assertEqual(hidden, 1)

    def test_nothing_is_deleted_from_the_card(self):
        row = card(DEAD, [{"url": ALSO_DEAD}], channels=[{"name": UNPROTECTED}])
        up.enforce([row])
        self.assertEqual(row["url"], DEAD)
        self.assertEqual(len(row["backups"]), 1)
        self.assertEqual(row["channels"], [{"name": UNPROTECTED}])

    def test_the_report_names_the_card_and_the_reason(self):
        _, _, report = up.enforce([card(DEAD)])
        self.assertEqual(report[0]["name"], UNPROTECTED)
        self.assertEqual(report[0]["category"], "Indian")
        self.assertIn("no playable route", report[0]["action"])
        self.assertIn("never started", report[0]["reason"])


class AHealthyCardIsLeftAlone(Ledger):
    def test_a_playable_primary_is_untouched(self):
        row = card(ALIVE, [{"url": DEAD}])
        promoted, hidden, report = up.enforce([row])
        self.assertEqual((promoted, hidden, report), (0, 0, []))
        self.assertEqual(row["url"], ALIVE)

    def test_a_route_with_no_measurement_is_not_condemned(self):
        row = card("https://never-measured.test/live.m3u8")
        promoted, hidden, _ = up.enforce([row])
        self.assertEqual((promoted, hidden), (0, 0))

    def test_a_card_already_held_back_is_skipped(self):
        row = card(DEAD, publish_allowed=False)
        promoted, hidden, report = up.enforce([row])
        self.assertEqual((promoted, hidden, report), (0, 0, []))

    def test_a_card_with_no_url_is_skipped(self):
        promoted, hidden, _ = up.enforce([card("")])
        self.assertEqual((promoted, hidden), (0, 0))

    def test_junk_in_the_list_does_not_break_it(self):
        promoted, hidden, _ = up.enforce([None, "junk", card(ALIVE)])
        self.assertEqual((promoted, hidden), (0, 0))


class TheDeadRouteNeverLeads(Ledger):
    """The condition the CI check has been asserting, stated directly."""

    def test_no_card_survives_enforcement_with_a_dead_primary(self):
        rows = [
            card(DEAD),
            card(DEAD, [{"url": ALIVE}]),
            card(ALSO_DEAD, [{"url": ALSO_DEAD}]),
            card(ALIVE),
        ]
        up.enforce(rows)
        visible = [r for r in rows if r.get("publish_allowed") is not False]
        for row in visible:
            with self.subTest(name=row["url"]):
                self.assertEqual(playback_evidence.unproven_reason(row["url"]), "")

class AChannelLevelProofDoesNotVouchForThisRoute(Ledger):
    """A sustained-playback proof is about the channel, not about this route.

    An exemption for proven channels was tried and removed. The registry
    records that a browser played the channel for two full windows and stores
    `fingerprint_at_proof_time: null`, so it cannot say which route it played -
    and with the exemption in place Zee Bangla stayed published on a route that
    decodes nowhere, which is the exact thing the CI check forbids.

    The seven hand-restored channels are safe for a different reason, checked
    by tests/test_promote_proven_channels.py against the real catalogue: this
    rule only fires on a card whose own route the ledger condemns, and none of
    theirs is.
    """

    def test_a_proven_channel_on_a_dead_route_is_still_held_back(self):
        row = card(DEAD, name=PROTECTED)
        promoted, hidden, report = up.enforce([row])
        self.assertEqual((promoted, hidden), (0, 1))
        self.assertIn("no playable route", report[0]["action"])

    def test_it_is_given_a_playable_backup_first(self):
        # Held back only when there is nothing to promote.
        row = card(DEAD, [{"url": ALIVE}], name=PROTECTED)
        promoted, hidden, _ = up.enforce([row])
        self.assertEqual((promoted, hidden), (1, 0))
        self.assertEqual(row["url"], ALIVE)

    def test_the_module_does_not_read_the_proof_registry(self):
        # Guard against the exemption being reintroduced by accident.
        source = Path("scanner/unplayable_primary.py").read_text(encoding="utf-8")
        self.assertNotIn("sustained_proof.has_proof", source)

class PromotingDoesNotDuplicateARoute(unittest.TestCase):
    """A card commonly lists its own primary among the backups as well.

    Putting the demoted primary back unconditionally printed it twice: the
    published Zee Bangla card listed the same rgkkw.live URL as two separate
    backups, and the catalogue's own duplicate-backup test caught it on the
    next scheduled scan.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "measured.json"
        self.path.write_text(json.dumps({"routes": {
            "rgkkw.live/live/1Aoen7elp5/IgMJ60tmAa/98881.ts": {
                "reason": "playback never started within the window"},
        }}), encoding="utf-8")
        self._real = playback_evidence.DEFAULT_PATH
        playback_evidence.DEFAULT_PATH = str(self.path)
        playback_evidence.reset_cache()

    def tearDown(self):
        playback_evidence.DEFAULT_PATH = self._real
        playback_evidence.reset_cache()
        self.dir.cleanup()

    def test_the_demoted_primary_appears_once(self):
        row = card(DEAD, [{"url": DEAD}, {"url": ALIVE}])
        up.enforce([row])
        urls = [b["url"] for b in row["backups"]]
        self.assertEqual(len(urls), len(set(urls)), urls)
        self.assertEqual(urls.count(DEAD), 1)

    def test_the_playable_route_still_leads(self):
        row = card(DEAD, [{"url": DEAD}, {"url": ALIVE}])
        up.enforce([row])
        self.assertEqual(row["url"], ALIVE)

    def test_an_unrelated_backup_is_kept(self):
        other = "https://third.example/z.m3u8"
        row = card(DEAD, [{"url": DEAD}, {"url": ALIVE}, {"url": other}])
        up.enforce([row])
        self.assertIn(other, [b["url"] for b in row["backups"]])



if __name__ == "__main__":
    unittest.main()
