import json
import os
import tempfile
import unittest

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
import zones as z


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


class WindowsConfig(unittest.TestCase):
    def test_a_config_file_saved_with_a_byte_order_mark_still_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8-sig") as fh:
                json.dump({"zones": {"days": 3}}, fh)
            self.assertEqual(z.read_config(path)["days"], 3)


if __name__ == "__main__":
    unittest.main()
