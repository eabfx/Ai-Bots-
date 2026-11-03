import json
import os
import tempfile
import unittest

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
import zones as z
macro = load("riskdesk", "macro")


class PageFeatures(unittest.TestCase):
    """Page code for the restart retry, Home layout, report, streaks and the AI week review."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "riskdesk", "dashboard.html"), encoding="utf-8") as fh:
            cls.page = fh.read()

    def test_requests_retry_quietly_while_the_server_restarts(self):
        self.assertIn("Response.prototype.json", self.page)
        self.assertIn("r.status===502", self.page)
        self.assertIn("The dashboard is restarting", self.page)

    def test_home_panels_can_be_hidden_and_reordered(self):
        for needle in ('id="h-cust"', "function layApply", 'id="cust-reset"', '"zd.layout"'):
            self.assertIn(needle, self.page)

    def test_reports_streaks_and_the_ai_review_are_there(self):
        for needle in ('id="h-report"', "function jReport", "function jExtras", "function jAiWeek", 'id="j-extras"'):
            self.assertIn(needle, self.page)

    def test_there_is_no_chart_tab(self):
        self.assertNotIn('data-tab="chart"', self.page)
        self.assertNotIn("/api/chart", self.page)


    def test_funding_page_is_wired_in(self):
        for needle in ('data-tab="funding"', 'id="tab-funding"', "function fdLoad", "function fdRender", 'id="fd-conn"', 'id="fd-connpanel"',
                       '"funding","macro"'.replace('"funding","macro"', '"trades","funding","alerts"'), 'if(name==="funding")fdLoad()', '"/api/funding"'):
            self.assertIn(needle, self.page)
        self.assertNotIn("prompt(", self.page.split("/* ---------------- funding ---------------- */")[1].split("/* ---------------- connections page")[0])

    def test_markets_and_settings_pages_are_wired_in(self):
        for needle in ('data-tab="markets"', 'id="tab-markets"', 'id="mk-grid"', "function mkDraw", "function mkFetch", '"/api/mkt/charts?c="', '"/api/mkt/layout"',
                       'if(name==="markets")mkShow()', 'data-tab="settings"', 'id="tab-settings"', 'id="set-conn"', 'id="h-mk-go"', 'class="fd-hero"'):
            self.assertIn(needle, self.page)
        self.assertNotIn('data-tab="connect"', self.page)               # Connections now lives inside Settings
        mk = self.page.split("/* ---------------- markets: a board of live charts you choose ---------------- */")[1].split("/* settings page */")[0]
        self.assertNotIn("prompt(", mk)
        self.assertNotIn("innerHTML", mk)

    def test_the_assistant_is_called_ai_assistant_and_starts_a_new_chat_each_day(self):
        for needle in ('<span>AI Assistant</span>', 'aria-label="AI Assistant"', '<h3>AI Assistant</h3>', 'content:"AI Assistant"', 'store("zd.chat.day")===today()', "function chatDayCheck", "function chatFmt", ">New chat<"):
            self.assertIn(needle, self.page)
        self.assertNotIn("AI Bot", self.page.split("</style>")[1])      # the old name is gone from the page itself

    def test_markets_refresh_every_three_seconds_and_back_off_when_the_feed_struggles(self):
        page = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
        self.assertIn("var gap=MK.gap||3000", page)
        self.assertIn("MK.gap=Math.min((MK.gap||3000)*2,20000)", page)
        self.assertIn(", every 3 s", page)
        self.assertIn("ttl=2.5", open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.py"), encoding="utf-8").read())
        live = load("riskdesk", "live")
        macro = load("riskdesk", "macro")
        for key, *_ in macro.WATCH:
            self.assertIn(key, live.KIND)                                  # every watched market says what kind of price it is

    def test_market_tiles_use_plain_names(self):
        for needle in ("function mkLabel", '"GC=F":["Gold"', '"DX-Y.NYB":["US Dollar Index"', '"Forex \\u00B7 "'):
            self.assertIn(needle, self.page)

    def test_home_strip_order_and_the_two_new_markets(self):
        self.assertIn('var TILE_ORDER=["GBPUSD","EURUSD","USDJPY","NQ","SPX","XAUUSD","XAGUSD"', self.page)
        keys = [item[0] for item in macro.WATCH]
        self.assertIn("USDJPY", keys)
        self.assertIn("XAGUSD", keys)
        self.assertIn('var QM_FIRST=["GBPUSD","EURUSD","USDJPY","NQ","SPX","XAUUSD","XAGUSD"]', self.page)   # the quote monitor follows the same order and drops nothing
        self.assertIn("VIX", keys)                                      # still read: the mood and capital flow use it, it is just off the top strip


class WindowsConfig(unittest.TestCase):
    def test_a_config_file_saved_with_a_byte_order_mark_still_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8-sig") as fh:
                json.dump({"zones": {"days": 3}}, fh)
            self.assertEqual(z.read_config(path)["days"], 3)


if __name__ == "__main__":
    unittest.main()
