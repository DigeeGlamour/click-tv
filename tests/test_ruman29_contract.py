import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class Ruman29ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = (ROOT / "site/assets/js/app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "site/assets/css/reference-design.css").read_text(encoding="utf-8")
        cls.index = (ROOT / "site/index.html").read_text(encoding="utf-8")

    def test_general_live_buffers_change_without_event_regression(self) -> None:
        self.assertIn("maxBufferLength: isEvent ? 8 : 8", self.app)
        self.assertIn("maxBufferLength: isEvent ? 5 : 6", self.app)
        self.assertIn("maxBufferLength: isEvent ? 12 : 16", self.app)

    def test_mobile_movie_transport_controls_exist(self) -> None:
        for control_id in (
            "movieLockBtn", "prevChBtn", "skipBackBtn", "playPauseBtn",
            "skipFwdBtn", "nextChBtn", "movieRotateBtn",
        ):
            self.assertIn(f'id="{control_id}"', self.index)
        self.assertIn("movie-controls-locked", self.css)
        self.assertIn("setMovieControlsLocked", self.app)

    def test_ruman29_mobile_layout_is_last_authoritative_block(self) -> None:
        marker = "/* RUMAN-29: final authoritative mobile contract. Keep this block last. */"
        self.assertIn(marker, self.css)
        final_block = self.css.split(marker, 1)[1]
        self.assertIn("grid-template-columns:repeat(5,minmax(0,1fr))", final_block)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", self.css)
        self.assertIn("object-fit:contain", final_block)
        self.assertIn("-webkit-line-clamp:3", final_block)
        self.assertIn("body::after{display:none", final_block)

    def test_today_channel_status_is_distinct_from_fixture_live(self) -> None:
        # The two states stay separate. The wording is Bengali now, like every
        # other line on the card - "CHANNEL LIVE" described how the scanner
        # found the match rather than anything a viewer can act on.
        self.assertIn("CHANNEL_LIVE: 'চ্যানেলে সরাসরি'", self.app)
        self.assertIn("LIVE_NOW: 'সরাসরি'", self.app)
        self.assertIn("uiStatus === 'LIVE_NOW' || uiStatus === 'CHANNEL_LIVE'", self.app)


if __name__ == "__main__":
    unittest.main()
