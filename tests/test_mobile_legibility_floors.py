"""Nothing on a phone below 11px, and nothing pressable below 44px.

Report items [11] and [12]. These were read off the stylesheet last time and
declared fixed; a 390px Chromium then measured, on the live site and on a local
build:

    tm-state              7.5px   "লিংক খোঁজা হচ্ছে"  - the state a viewer most needs
    event-art-versus em   7.5px   "vs"
    event-card-action     40x32   the Upcoming card's press target

The 460px block in event-channel-cards.css set that 7.5px, and that sheet loads
after event-cards.css, so a floor written in the earlier file loses. The rules
have to be restated in the sheet that wins, which is the whole reason this test
names the file as well as the rule.

Report item [13] is not here. The Today card's press affordance is the channel
button - the owner's reference design is a poster with "channel buttons and
nothing else", guarded by TodayMatchCardV2 - and the live site measures those
at 173x44 and 350x44. Adding a second action to that card is a contract
violation, not a fix.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "site" / "assets" / "css"
CARDS = (CSS / "event-cards.css").read_text(encoding="utf-8")
CHANNEL = (CSS / "event-channel-cards.css").read_text(encoding="utf-8")
INDEX = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

#: The phone breakpoint the floors are written at.
PHONE = "@media (max-width:560px){"


def block_at(css, header, name):
    """The body of the media block starting at `header` that mentions `name`."""
    for match in re.finditer(re.escape(header), css):
        depth, index = 0, match.end() - 1
        while index < len(css):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    body = css[match.end():index]
                    if name in body:
                        return body
                    break
            index += 1
    return ""


def font_sizes(body, selector):
    sizes = []
    for match in re.finditer(re.escape(selector) + r"[^{}]*\{([^{}]*)\}", body):
        sizes += [float(value) for value in
                  re.findall(r"font-size:\s*([\d.]+)px", match.group(1))]
    return sizes


class ElevenPixelsIsTheFloor(unittest.TestCase):
    def test_the_state_badge_is_raised_in_the_sheet_that_wins(self):
        # event-channel-cards.css loads after event-cards.css, so the floor for
        # .tm-state has to live here or the 460px rule keeps winning.
        body = block_at(CHANNEL, PHONE, ".tm-state")
        sizes = font_sizes(body, ".tm-state")
        self.assertTrue(sizes, "no phone font-size for .tm-state")
        for size in sizes:
            self.assertGreaterEqual(size, 11.0)

    def test_the_earlier_seven_and_a_half_rule_is_still_overridden(self):
        # Guards the ordering rather than the value: if the 460px rule ever
        # moves after the 560px one, this is the test that notices.
        self.assertLess(CHANNEL.index("font-size:7.5px"),
                        CHANNEL.index(PHONE),
                        "the 7.5px rule must come before the floor that beats it")

    def test_both_versus_glyphs_are_raised(self):
        # There are two of them - the abbreviated-name art and the two-crest
        # art - and `.event-art-versus em` is declared in both sheets, so the
        # floor has to be in the later one to win. Raising it only in
        # event-cards.css left a 390px Chromium still measuring 7.5px.
        for css, selector in ((CARDS, ".event-art-versus em"),
                              (CHANNEL, ".event-art-versus em"),
                              (CHANNEL, ".event-art-crests em")):
            with self.subTest(selector=selector):
                body = block_at(css, PHONE, selector)
                sizes = font_sizes(body, selector)
                self.assertTrue(sizes, f"no phone font-size for {selector}")
                for size in sizes:
                    self.assertGreaterEqual(size, 11.0)

    def test_the_today_badges_and_title_are_raised(self):
        body = block_at(CHANNEL, PHONE, ".tm-state")
        for selector, floor in ((".tm-category", 11.0), (".tm-league", 11.0),
                                (".tm-title", 11.0)):
            with self.subTest(selector=selector):
                sizes = font_sizes(body, selector)
                self.assertTrue(sizes, f"no phone font-size for {selector}")
                for size in sizes:
                    self.assertGreaterEqual(size, floor)

    def test_the_upcoming_serial_badge_is_raised(self):
        # 8px from final-design.css, against 11px for the Today card's
        # equivalent. A number a viewer reads to say "the third one down".
        body = block_at(CARDS, PHONE, ".sidebar-channel-num")
        sizes = font_sizes(body, ".sidebar-channel-num")
        self.assertTrue(sizes, "no phone font-size for the serial badge")
        for size in sizes:
            self.assertGreaterEqual(size, 11.0)

    def test_the_data_age_label_is_on_the_floor_too(self):
        body = block_at(CARDS, PHONE, ".sidebar-data-age")
        sizes = font_sizes(body, ".sidebar-data-age")
        self.assertTrue(sizes)
        for size in sizes:
            self.assertGreaterEqual(size, 11.0)


class FortyFourPixelsIsTheTarget(unittest.TestCase):
    def test_the_card_action_is_a_finger_target_on_a_phone(self):
        body = block_at(CARDS, PHONE, ".event-card-action")
        self.assertIn("min-height:44px", body)
        self.assertIn("min-width:44px", body)

    def test_the_icon_inside_it_is_not_left_at_thirty_two(self):
        # The 480px block shrinks the icon to 32px, and the icon is what sets
        # the rendered height - 40x32 was measured on the live site.
        body = block_at(CARDS, PHONE, ".event-card-action i")
        icon = re.search(r"\.event-card-action i\{([^}]*)\}", body)
        self.assertIsNotNone(icon, "no phone rule for the action icon")
        for dimension in ("width:44px", "height:44px"):
            self.assertIn(dimension, icon.group(1))

    def test_a_touch_device_gets_it_at_any_width(self):
        # A touch laptop is wide and still has no pointer.
        body = block_at(CARDS, "@media (hover:none){", ".event-card-action")
        self.assertIn("min-height:44px", body)
        self.assertIn("min-width:44px", body)

    def test_the_channel_chip_already_had_it(self):
        body = block_at(CHANNEL, "@media (hover:none){", ".event-channel-chip")
        self.assertIn("min-height:44px", body)


class TheseRulesStayScopedToTheEventList(unittest.TestCase):
    """Both sheets are under a scoping contract; new rules must respect it."""

    def test_every_new_selector_names_the_event_list(self):
        allowed = (".sidebar-section.event-list-mode", ".sidebar-count-detail",
                   ".sidebar-data-age", ".event-preview-facts",
                   ".event-preview-fact", "@keyframes")
        for label, css in (("event-cards.css", CARDS),
                           ("event-channel-cards.css", CHANNEL)):
            for line in css.splitlines():
                stripped = line.strip()
                if not stripped.startswith("."):
                    continue
                for selector in stripped.split("{")[0].split(","):
                    selector = selector.strip()
                    if not selector:
                        continue
                    with self.subTest(sheet=label, selector=selector):
                        self.assertTrue(selector.startswith(allowed))


class TheBrowserCheckIsKeptAround(unittest.TestCase):
    """These numbers came from a browser, and have to be re-measurable."""

    def test_the_probe_script_exists(self):
        probe = ROOT / "scripts" / "browser-data-freshness-check.py"
        self.assertTrue(probe.is_file())
        source = probe.read_text(encoding="utf-8")
        for measured in ("smallest_text", "touch_targets", "play_cue",
                         "readiness", "dataFreshness"):
            with self.subTest(measured=measured):
                self.assertIn(measured, source)

    def test_it_measures_at_phone_width(self):
        source = (ROOT / "scripts" / "browser-data-freshness-check.py"
                  ).read_text(encoding="utf-8")
        self.assertIn('"width": 390', source)


if __name__ == "__main__":
    unittest.main()
