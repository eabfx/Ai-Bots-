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


class TileWallAndRing(unittest.TestCase):
    def test_gallery_has_three_tile_sizes_and_remembers_the_choice(self):
        for needle in ('id="t-size"', 'data-s="sm"', 'data-s="lg"', '"zd.gsize"', '.t-gal.sm{', 'el("div","t-gal "+tSize)'):
            self.assertIn(needle, PAGE)

    def test_edge_score_is_a_ring(self):
        for needle in ('"jring ', "stroke-dashoffset", '"EDGE"'):
            self.assertIn(needle, PAGE)


class Motion(unittest.TestCase):
    def test_the_assistant_button_is_called_ai_bot_and_animated(self):
        self.assertIn('<span>AI Bot</span>', PAGE)
        self.assertNotIn("Ask RiskDesk", PAGE)
        for needle in ("@keyframes botfloat", "@keyframes botring", "@keyframes botblink", 'class="bot"'):
            self.assertIn(needle, PAGE)

    def test_pages_play_an_entrance_once_and_motion_can_be_turned_off(self):
        for needle in ("@keyframes fadeup", ".tool.enter", 'ent.classList.add("enter")', "pathLength:1"):
            self.assertIn(needle, PAGE)
        self.assertIn("prefers-reduced-motion:reduce){*{animation:none!important", PAGE)


class ManualEntry(unittest.TestCase):
    ORDER = ["Date", "Account Type", "Bias", "Pair", "Session", "Long / Short", "Risk", "Win / Loss", "R/R", "Partials", "Strategy", "Zone", "Setup",
             "Trade SS", "Strategy + Setup", "Trade Breakdown", "Profit", "Trade No."]

    def test_the_form_lists_the_notion_columns_in_the_same_order(self):
        start = PAGE.index('<div class="card" id="j-form">')
        form = PAGE[start:PAGE.index('<div class="card" id="j-trades">')]
        at = -1
        for name in self.ORDER:
            i = form.index('<div class="np"><label>%s</label>' % name)
            self.assertGreater(i, at, name)
            at = i

    def test_three_kinds_of_trade_and_editing(self):
        for needle in ('data-k="live"', 'data-k="forward"', 'data-k="backtest"', '"Forward-Test"', "function jEdit(t)", "Edit all fields", "function readTradeForm()",
                       "var pf=parseFloat(t.profit)"):
            self.assertIn(needle, PAGE)

    def test_no_duplicate_element_ids_in_the_form(self):
        import re
        ids = re.findall(r'\sid="([^"]+)"', PAGE)
        dup = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dup, [])
