import os
import unittest

PAGE = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()


class TradeCards(unittest.TestCase):
    def test_one_card_builder_feeds_the_strip_and_the_gallery(self):
        self.assertEqual(PAGE.count("function tCard(t,list)"), 1)
        self.assertIn("strip.appendChild(tCard(t,last))", PAGE)
        self.assertIn("g.appendChild(tCard(t,tShown))", PAGE)
        for chip in ('"chip pl"', '"chip dir"', '"chip rr '):
            self.assertIn(chip, PAGE)

    def test_the_picture_viewer_steps_through_the_trades_on_screen(self):
        for needle in ('id="lb-prev"', 'id="lb-next"', "function lbStep(d)", 'ev.key==="ArrowLeft"', 'ev.key==="ArrowRight"', "lbList.indexOf(t)"):
            self.assertIn(needle, PAGE)

    def test_the_page_was_not_duplicated_by_an_edit(self):
        for once in ("function jHero()", "function tRender(list)", "function openLb(t,list)", "function tGallery(list,box)"):
            self.assertEqual(PAGE.count(once), 1, once)


if __name__ == "__main__":
    unittest.main()
