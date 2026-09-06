"""The lifecycle timings have one home, and the fallbacks mirror the code.

THE SHAPE OF THE FAULT THIS PREVENTS.

The same threshold lived in three files and all three happened to be 30, so
nothing looked wrong:

    config/settings.json        events.targeted_window_minutes = 30
    scanner/event_lifecycle.py  DEFAULT_TODAY_ROUTING_MINUTES  = 30
    scanner/events.py:458       TODAY_NO_LINK_GRACE_MINUTES    = 30

Change one to 25 and the scanner starts hunting for a link at a different moment
than the tab moves the card. This repository has already been bitten by exactly
that shape once: `on.schedule` was changed and the workflow's own mode selector
was not, and the targeted scan then failed to run for six days without producing
a single failed run to look at.

WHAT THESE TESTS PIN.

1. The config is authoritative and parses to the intended values.
2. A missing, empty, malformed or unreadable config falls back to what the code
   does TODAY - not to what it will do later. A fallback is a way to survive a
   missing file, not a second opinion about the timing.
3. Each fallback equals the constant it mirrors, checked against the constant
   itself rather than against a copy of its number. That is what stops the two
   drifting apart again the moment somebody edits one.
4. Nothing outside `scanner/lifecycle_config.py` reads these fields yet. This
   step introduces the values ahead of their consumers on purpose, and PROMPT
   10/11/12/20/21 will each need to update the relevant assertion here when they
   wire one up - which is the intended signal, not an obstacle.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import event_lifecycle, events, live_protection, targeted_scan
from scanner.lifecycle_config import (
    FIELDS,
    SECTION,
    defaults,
    lifecycle_settings,
    lifecycle_value,
)

SETTINGS = ROOT / "config" / "settings.json"

#: What the owner asked this step to put in the config, from FINAL_2 step 2.
INTENDED = {
    "move_to_today_minutes": 25,
    "target_retry_interval_min": 5,
    "target_retry_until_min": 10,
    "post_match_grace_minutes": 20,
    "no_link_today_grace_minutes": 25,
    "confirmations_required": 3,
}


class TheConfigIsAuthoritative(unittest.TestCase):
    def test_the_real_settings_file_carries_every_intended_value(self):
        resolved = lifecycle_settings(settings_path=SETTINGS)
        for name, wanted in INTENDED.items():
            with self.subTest(field=name):
                self.assertEqual(wanted, resolved[name])

    def test_the_block_declares_every_field_the_loader_knows(self):
        """A field in code and not in config would silently run on its fallback;
        a field in config and not in code would silently do nothing."""
        block = json.loads(SETTINGS.read_text(encoding="utf-8"))[SECTION]
        declared = {key for key in block if not key.startswith("_")}
        self.assertEqual(set(FIELDS), declared)

    def test_the_four_pre_existing_values_were_not_disturbed(self):
        """They already had consumers. This step must not move them."""
        resolved = lifecycle_settings(settings_path=SETTINGS)
        self.assertEqual(3, resolved["confirmations_required"])
        self.assertEqual(90, resolved["estimate_grace_minutes"])
        self.assertEqual(3, resolved["unscheduled_carry_hours"])
        self.assertEqual(36, resolved["unscheduled_carry_confirmations"])

    def test_an_already_loaded_settings_dict_needs_no_file(self):
        supplied = {SECTION: {"move_to_today_minutes": 17}}
        self.assertEqual(17, lifecycle_settings(supplied)["move_to_today_minutes"])


class AFallbackMirrorsWhatTheCodeDoesToday(unittest.TestCase):
    """Compared against the constants themselves, never against a copy."""

    def test_the_routing_threshold_fallback_is_the_routing_constant(self):
        self.assertEqual(event_lifecycle.DEFAULT_TODAY_ROUTING_MINUTES,
                         defaults()["move_to_today_minutes"])

    def test_the_no_link_grace_fallback_is_the_no_link_constant(self):
        self.assertEqual(events.TODAY_NO_LINK_GRACE_MINUTES,
                         defaults()["no_link_today_grace_minutes"])

    def test_the_retry_interval_fallback_is_the_slot_width(self):
        self.assertEqual(targeted_scan.BUCKET_MINUTES,
                         defaults()["target_retry_interval_min"])

    def test_the_retry_tail_fallback_is_the_tail_constant(self):
        self.assertEqual(targeted_scan.DEFAULT_RETRY_AFTER_KICKOFF_MINUTES,
                         defaults()["target_retry_until_min"])

    def test_the_confirmation_fallback_is_the_lifecycle_constant(self):
        self.assertEqual(event_lifecycle.DEFAULT_CONFIRMATIONS_REQUIRED,
                         defaults()["confirmations_required"])

    def test_the_estimate_grace_fallback_matches_both_modules(self):
        self.assertEqual(event_lifecycle.DEFAULT_ESTIMATE_GRACE_MINUTES,
                         defaults()["estimate_grace_minutes"])
        self.assertEqual(live_protection.DEFAULT_GRACE_MINUTES,
                         defaults()["estimate_grace_minutes"])

    def test_the_unscheduled_carry_fallbacks_match_live_protection(self):
        self.assertEqual(live_protection.DEFAULT_UNSCHEDULED_CARRY_HOURS,
                         defaults()["unscheduled_carry_hours"])
        self.assertEqual(live_protection.DEFAULT_UNSCHEDULED_CARRY_CONFIRMATIONS,
                         defaults()["unscheduled_carry_confirmations"])

    def test_the_post_match_grace_fallback_is_zero_because_that_is_today(self):
        """There is no constant to mirror: a strong end signal retires a card at
        once, which is a grace of nothing. The config asks for 20, and PROMPT
        20/21 will make the lifecycle read it. Until then the fallback is an
        honest description of current behaviour rather than an aspiration."""
        self.assertEqual(0, defaults()["post_match_grace_minutes"])
        self.assertEqual(20, lifecycle_settings(settings_path=SETTINGS)
                         ["post_match_grace_minutes"])

    def test_where_a_fallback_and_the_config_disagree_it_is_deliberate(self):
        """Exactly three, each owned by a named later step. A fourth appearing
        here means somebody changed a constant or a config value without saying
        which behaviour they meant to change."""
        resolved = lifecycle_settings(settings_path=SETTINGS)
        disagreeing = {name for name, value in defaults().items()
                       if resolved[name] != value}
        self.assertEqual(
            {"move_to_today_minutes", "no_link_today_grace_minutes",
             "post_match_grace_minutes"},
            disagreeing,
        )


class AMissingOrBrokenConfigIsNotAnError(unittest.TestCase):
    def test_no_section_at_all(self):
        self.assertEqual(defaults(), lifecycle_settings({}))

    def test_the_section_is_not_a_dictionary(self):
        for wrong in ([], "", 0, None, ["move_to_today_minutes"]):
            with self.subTest(value=wrong):
                self.assertEqual(defaults(), lifecycle_settings({SECTION: wrong}))

    def test_a_file_that_does_not_exist(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(defaults(),
                             lifecycle_settings(settings_path=Path(folder) / "absent.json"))

    def test_a_file_that_is_not_json(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "settings.json"
            broken.write_text("{ not json at all", encoding="utf-8")
            self.assertEqual(defaults(), lifecycle_settings(settings_path=broken))

    def test_a_file_whose_top_level_is_not_an_object(self):
        with tempfile.TemporaryDirectory() as folder:
            odd = Path(folder) / "settings.json"
            odd.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertEqual(defaults(), lifecycle_settings(settings_path=odd))

    def test_one_missing_field_falls_back_alone(self):
        supplied = {SECTION: {"move_to_today_minutes": 25}}
        resolved = lifecycle_settings(supplied)
        self.assertEqual(25, resolved["move_to_today_minutes"])
        self.assertEqual(defaults()["post_match_grace_minutes"],
                         resolved["post_match_grace_minutes"])

    def test_a_nonsense_value_falls_back_rather_than_raising(self):
        for wrong in ("soon", None, [], {}, "", "twenty-five"):
            with self.subTest(value=wrong):
                supplied = {SECTION: {"move_to_today_minutes": wrong}}
                self.assertEqual(defaults()["move_to_today_minutes"],
                                 lifecycle_settings(supplied)["move_to_today_minutes"])

    def test_a_numeric_string_is_accepted(self):
        supplied = {SECTION: {"move_to_today_minutes": "25"}}
        self.assertEqual(25, lifecycle_settings(supplied)["move_to_today_minutes"])

    def test_an_out_of_range_value_is_pulled_into_range(self):
        for name, (_, minimum, maximum) in FIELDS.items():
            with self.subTest(field=name):
                self.assertEqual(minimum, lifecycle_settings(
                    {SECTION: {name: minimum - 1000}})[name])
                self.assertEqual(maximum, lifecycle_settings(
                    {SECTION: {name: maximum + 1000}})[name])

    def test_no_field_can_be_configured_negative(self):
        for name in FIELDS:
            with self.subTest(field=name):
                self.assertGreaterEqual(
                    lifecycle_settings({SECTION: {name: -99}})[name], 0)

    def test_it_clamps_the_way_the_scanner_already_clamps(self):
        """`_clamp` is `events._safe_int`. If they ever diverge, one half of the
        codebase would read a bad config differently from the other."""
        for value in ("soon", None, [], "7", 7, -5, 10 ** 9, True):
            with self.subTest(value=value):
                self.assertEqual(
                    events._safe_int(value, 30, 1, 24 * 60),
                    lifecycle_settings({SECTION: {"move_to_today_minutes": value}})
                    ["move_to_today_minutes"],
                )


class OneFieldAtATime(unittest.TestCase):
    def test_it_answers_for_a_single_field(self):
        self.assertEqual(25, lifecycle_value("move_to_today_minutes",
                                             settings_path=SETTINGS))

    def test_an_unknown_field_is_refused_by_name(self):
        with self.assertRaises(KeyError) as raised:
            lifecycle_value("move_to_tomorrow_minutes")
        self.assertIn("move_to_today_minutes", str(raised.exception))


class OnlyTheWiredFieldsAreRead(unittest.TestCase):
    """Which fields have consumers, and which are still waiting for one.

    PROMPT 09 introduced all five ahead of any consumer, and each later step
    was told to come back here and move one across: the three targeted
    timings in PROMPT 10, the routing threshold in 11, the no-link grace in
    12, the post-match grace in 20/21. None is left waiting.
    """

    #: Wired: the three targeted timings by PROMPT 10, the routing threshold
    #: by PROMPT 11 (the same key as the hunt window), the no-link Today
    #: grace by PROMPT 12, the post-match grace by PROMPT 20/21.
    WIRED_FIELDS = ("move_to_today_minutes", "target_retry_interval_min",
                    "target_retry_until_min", "no_link_today_grace_minutes",
                    "post_match_grace_minutes")

    #: Nothing is config-only any more. A key added here in future goes in
    #: this tuple until the step that reads it moves it above.
    NEW_FIELDS = ()

    def _production_sources(self):
        for path in sorted((ROOT / "scanner").glob("*.py")):
            if path.name != "lifecycle_config.py":
                yield path
        for path in sorted((ROOT / "scanner" / "parsers").glob("*.py")):
            yield path
        yield ROOT / "scan.py"

    def test_no_other_module_reads_the_new_fields(self):
        for path in self._production_sources():
            text = path.read_text(encoding="utf-8")
            for field in self.NEW_FIELDS:
                with self.subTest(file=path.name, field=field):
                    self.assertNotIn(field, text)

    #: The three targeted keys are consumed under the names
    #: `targeted_timings` gives them, which is the whole point of that
    #: function - the planner reads one section, not three keys whose
    #: names it has to remember. So the check follows the rename.
    CONSUMER_ALIASES = {
        "move_to_today_minutes": ("move_to_today_minutes", "window_minutes",
                                  "routing_minutes"),
        "target_retry_until_min": ("target_retry_until_min",
                                   "after_kickoff_minutes"),
        "target_retry_interval_min": ("target_retry_interval_min",
                                      "retry_interval_minutes"),
    }

    def test_every_wired_field_really_has_a_consumer(self):
        """The other half of the same claim, and the one that would catch a
        key being declared and then quietly ignored - which is what these
        five spent several prompts being."""
        sources = {
            path.name: path.read_text(encoding="utf-8")
            for path in self._production_sources()
        }
        for field in self.WIRED_FIELDS:
            names = self.CONSUMER_ALIASES.get(field, (field,))
            with self.subTest(field=field):
                readers = sorted(
                    name for name, text in sources.items()
                    if any(alias in text for alias in names)
                )
                self.assertTrue(readers, "%s has no consumer" % field)

    def test_the_post_match_grace_reaches_the_lifecycle(self):
        from scanner.lifecycle_config import lifecycle_settings
        self.assertEqual(
            20, lifecycle_settings(settings_path=SETTINGS)[
                "post_match_grace_minutes"]
        )
        self.assertEqual(0, event_lifecycle.DEFAULT_POST_MATCH_GRACE_MINUTES)

    def test_the_old_constants_are_still_where_they_were(self):
        """Nothing was deleted in this step - they are the fallbacks now."""
        self.assertEqual(30, event_lifecycle.DEFAULT_TODAY_ROUTING_MINUTES)
        self.assertEqual(30, events.TODAY_NO_LINK_GRACE_MINUTES)
        self.assertEqual(5, targeted_scan.BUCKET_MINUTES)
        self.assertEqual(10, targeted_scan.DEFAULT_RETRY_AFTER_KICKOFF_MINUTES)

    def test_the_targeted_scan_reads_the_wired_fields(self):
        """One place decides the three targeted timings now."""
        from scanner.lifecycle_config import targeted_timings
        timings = targeted_timings(settings_path=SETTINGS)
        self.assertEqual(25, timings["window_minutes"])
        self.assertEqual(10, timings["after_kickoff_minutes"])
        self.assertEqual(5, timings["retry_interval_minutes"])

    def test_the_retired_duplicate_is_gone_and_covered_by_a_migration(self):
        from scanner.lifecycle_config import LEGACY_KEYS
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        self.assertNotIn("targeted_window_minutes", settings["events"])
        self.assertEqual(("events", "targeted_window_minutes"),
                         LEGACY_KEYS["move_to_today_minutes"])


if __name__ == "__main__":
    unittest.main()
