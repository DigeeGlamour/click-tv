"""The finalised Today/Upcoming design needs three things the site did not have.

The cards were built from the owner's approved design file and still came out
wrong on the deployed site. Measuring it in a browser said why, precisely:
every size, column and font was correct and **every background and border was
missing** - the schedule list, the 56px logo boxes, the category badge, the row
separators - so the team initials sat as floating text with no box round them.

The design declares its palette in its own `:root`. This site declares none of
it: of `--card-bg`, `--border`, `--gold`, `--red`, `--text-2` and the rest,
only `--live-green` exists anywhere in its stylesheets. So every
`background:var(--card-bg)` resolved to an invalid value and was dropped, while
the rules written with a fallback - `var(--font-display,'Oswald',sans-serif)` -
applied normally. That asymmetry is exactly what the measurements showed.

Two more of the same shape: the design is drawn in Oswald and Hind Siliguri and
neither was being loaded, and the stylesheet has to stay last or the older
event rules win.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "site" / "assets" / "css" / "final-match-cards.css").read_text(
    encoding="utf-8")
INDEX = (ROOT / "site" / "index.html").read_text(encoding="utf-8")

#: Every custom property the ported rules read, with the design's own value.
PALETTE = {
    "--card-bg": "#12141a",
    "--card-bg-hover": "#181b23",
    "--border": "rgba(255,255,255,.08)",
    "--border-hover": "rgba(255,255,255,.18)",
    "--text-1": "#f5f6f8",
    "--text-2": "#8b93a3",
    "--text-3": "#565d6b",
    "--red": "#ff1e3d",
    "--red-dim": "rgba(255,30,61,.14)",
    "--red-border": "rgba(255,30,61,.4)",
    "--gold": "#ffb703",
    "--steel": "#4a9eee",
    "--live-green": "#2be08a",
    "--green-dim": "rgba(43,224,138,.14)",
}


class ThePaletteExists(unittest.TestCase):
    """The root cause, stated as a test."""

    def setUp(self):
        block = re.search(r"\.sidebar-section\.event-list-mode\{([^}]*)\}", CSS)
        self.assertIsNotNone(block, "the palette block is gone")
        self.declared = dict(
            (name.strip(), value.strip())
            for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);",
                                          block.group(1))
        )

    def test_every_colour_the_rules_read_is_declared(self):
        for name, value in PALETTE.items():
            with self.subTest(name=name):
                self.assertIn(name, self.declared)
                self.assertEqual(self.declared[name].replace(" ", ""), value)

    def test_the_two_font_stacks_are_declared(self):
        self.assertIn("Oswald", self.declared.get("--font-display", ""))
        self.assertIn("Hind Siliguri", self.declared.get("--font-body", ""))

    def test_no_variable_is_read_that_is_not_declared(self):
        # The failure mode was silent: an undeclared name makes the whole
        # declaration invalid and the property simply does not apply.
        known = set(self.declared) | {"--font-display", "--font-body"}
        used = set(re.findall(r"var\((--[\w-]+)", CSS))
        self.assertEqual(used - known, set())

    def test_the_palette_stays_off_the_rest_of_the_site(self):
        # Declared on the event list, never on the document root, so no other
        # view can be repainted by it. Checked on selectors, not on the prose:
        # the comment above the block names :root as the thing it is not.
        selectors = [line.strip().split("{")[0].strip()
                     for line in CSS.splitlines()
                     if line.strip().endswith("{") and not line.strip().startswith("*")]
        self.assertNotIn(":root", selectors)
        self.assertIn(".sidebar-section.event-list-mode", selectors)


class TheTypefacesAreLoaded(unittest.TestCase):
    def test_oswald_and_hind_siliguri_are_fetched(self):
        # Naming them in CSS was not enough - neither was being downloaded, so
        # the cards fell back to the site's own face.
        links = re.findall(r'<link[^>]+fonts\.googleapis\.com[^>]*>', INDEX)
        self.assertTrue(links, "no webfont link in the page")
        wanted = [tag for tag in links
                  if "Oswald" in tag and "Hind+Siliguri" in tag]
        self.assertTrue(wanted,
                        "neither typeface the design is drawn in is fetched")


class TheStylesheetStaysLast(unittest.TestCase):
    def test_it_is_the_final_stylesheet_in_the_page(self):
        sheets = re.findall(r'href="(assets/css/[^"?]+)', INDEX)
        self.assertTrue(sheets)
        self.assertEqual(sheets[-1], "assets/css/final-match-cards.css",
                         "the older event rules would win")

    def test_every_rule_is_scoped_to_the_event_list(self):
        for line in CSS.splitlines():
            stripped = line.strip()
            if not stripped.startswith((".", "#")):
                continue
            for selector in stripped.split("{")[0].split(","):
                selector = selector.strip()
                if not selector:
                    continue
                with self.subTest(selector=selector):
                    # `today-mode` and `upcoming-mode` are the two tabs
                    # themselves, set beside `event-list-mode` on the section.
                    self.assertTrue(
                        selector.startswith((
                            ".sidebar-section.event-list-mode",
                            ".sidebar-section.today-mode",
                            ".sidebar-section.upcoming-mode",
                        )),
                        f"reaches outside Today/Upcoming: {selector}")


class NothingTheDesignDoesNotDraw(unittest.TestCase):
    """The approved headers carry the title and the count, and nothing else."""

    def test_the_data_age_line_and_old_filter_are_off_these_two_tabs(self):
        block = CSS[CSS.index("#dataFreshness"):]
        block = block[:block.index("}") + 1]
        self.assertIn("display:none!important", block)
        for hidden in ("#dataFreshness", ".sidebar-data-age",
                       "#eventFilterWrap", ".event-filter-wrap"):
            with self.subTest(hidden=hidden):
                self.assertIn(hidden, block)

    def test_the_rule_is_last_so_it_cannot_be_overwritten_again(self):
        # It lived inside the header section once and was lost when that
        # section was rewritten, which is why the label was still on the page.
        self.assertGreater(CSS.index("#dataFreshness"),
                           CSS.index("@media (max-width:767px)"))


class TheTwoTabsAreToldApart(unittest.TestCase):
    """Today's count is 12.5px on a baseline row; Upcoming's is 14px/600."""

    def test_the_section_says_which_tab_it_shows(self):
        app = (ROOT / "site" / "assets" / "js" / "app.js").read_text(
            encoding="utf-8")
        self.assertIn("classList.toggle('today-mode', state.view === VIEW.EVENT)",
                      app)
        self.assertIn("classList.toggle('upcoming-mode', state.view === VIEW.UPCOMING)",
                      app)

    def test_each_header_has_its_own_measurements(self):
        self.assertIn(".sidebar-section.upcoming-mode .sidebar-top-bar.card-list-meta{",
                      CSS)
        self.assertIn(".sidebar-section.upcoming-mode .sidebar-count-detail{", CSS)


if __name__ == "__main__":
    unittest.main()
