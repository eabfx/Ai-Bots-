import json
import os
import tempfile
import unittest

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
import zones as z
macro = load("riskdesk", "macro")
markets = load("riskdesk", "markets")


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
                       'if(name==="markets"){mkShow();crLoad()}', 'data-tab="settings"', 'id="tab-settings"', 'id="set-conn"', 'id="h-mk-go"', 'class="fd-hero"'):
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

    def test_a_chart_click_sets_a_price_alert(self):
        page = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
        for needle in ("function mkAlertClick", "function mkAlertCheck", "var MK_KEYS=", '"GC=F":"XAUUSD"', "mk-pop", "c.map={lo:lo,hi:hi"):
            self.assertIn(needle, page)
        macro = load("riskdesk", "macro")
        watched = {sym: key for key, _, sym, _ in macro.WATCH}
        for sym, key in watched.items():                     # every watched market can take a chart alert, under the key the Alerts page uses
            self.assertIn('"%s":"%s"' % (sym, key), page)

    def test_journal_heatmap_last_week_panel_and_news_countdown_are_in_the_page(self):
        page = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
        for needle in ('id="j-heat"', "function jHeat", "When you make money", "jExtras(list);jHeat(list);",       # weekday by hour map in the Journal
                       'id="h-week"', "function hWeek", "zd.wk.ai.", "hWeek();",                                  # last week on Home, AI read written once a week
                       'id="h-newsbar"', "function nbTick", "setInterval(nbTick,1000)"):                          # countdown to the next high-impact release
            self.assertIn(needle, page)

    def test_journal_can_name_the_mistake_on_a_trade(self):
        for needle in ('id="j-mis"', 'data-by="mistake"', 'var MISTAKES=["Early entry"', 'if(MISTAKES.indexOf(t.mistake)>=0)o.mistake=t.mistake;',
                       'mistake:$("j-mis").value', '"j-mis":t.mistake', 'mistake:function(t){return t.mistake||""}'):
            self.assertIn(needle, self.page)

    def test_the_price_line_at_the_bottom_of_home_slides_in_a_loop(self):
        for needle in ('@keyframes tkslide', 'translateX(-50%)', 'class="tk-track"' if False else 'el("div","tk-track")', 'tkCopy<2',
                       'animation-play-state:paused', '.hticker .tk-set+.tk-set{display:none}'):
            self.assertIn(needle, self.page)

    def test_a_broken_market_link_can_raise_an_alert(self):
        for needle in ('id="al-corr"', 'corr:true,dayLoss:0', '["al-corr","corr","b"]', 'function crAlertCheck()', 'alOnce("corr|"+p.label)',
                       'setInterval(crAlertCheck,300000)', '"news","sess","check","corr","sound"'):
            self.assertIn(needle, self.page)

    def test_charts_open_as_candles_and_keep_the_line_button(self):
        self.assertIn('var MK_DEF={grid:"3x2",tf:"5m",style:"candles"', self.page)
        self.assertIn('[["area","Line"],["candles","Candles"]]', self.page)
        self.assertIn('zd.mk.candles1', self.page)
        self.assertEqual(markets.DEFAULT["style"], "candles")
        self.assertTrue(all(t["style"] == "candles" for t in markets.DEFAULT["tiles"]))

    def test_key_levels_are_saved_drawn_ranked_and_turn_into_alerts(self):
        for needle in ('id="mk-lv"', 'id="h-levels"', 'function lvAlert(L)', 'function lvHome()', 'lvFor(t.s).forEach', 'wsSet("levels"', '"/api/workspace"',
                       'WS_KEYS=["levels","openrisk","goal","wrapups","cooldown","startday","newsdays"]'):
            self.assertIn(needle, self.page)

    def test_the_command_box_takes_alerts_levels_charts_and_ctrl_k(self):
        for needle in ('placeholder="Ctrl+K  commands"', 'window.KT={', 'e.key==="k"||e.key==="K"', 'function dyn(q)', 'Alert when "+(PRETTY[key]||key)',
                       '["candles","Charts as candles"', 'var lk=m[3]||"note"'):
            self.assertIn(needle, self.page)

    def test_open_risk_cooldown_and_the_bad_run_simulator_are_in_the_page(self):
        for needle in ('id="h-open"', 'function oxBets(items)', 'are the same bet:', 'id="h-cool"', 'function cdStart(mins)', 'cdStart();\n  if(AL.check&&',
                       'id="al-cool"', 'coolMins:10', 'id="j-sim"', 'function jSim(list)', 'jSim(list);jNews(list);jWall(list);jWraps();jGroups(list);', 'It does not predict anything.'):
            self.assertIn(needle, self.page)

    def test_start_of_day_wrap_up_goal_news_wall_report_and_pop_out_are_in_the_page(self):
        for needle in ('id="kt-modal"', 'function ktOpen(title,build,foot)', 'id="sd-open"', 'function sdOpen()', 'wsSet("startday"', 'id="wu-open"', 'function wuOpen()',
                       'wsSet("wrapups"', 'id="j-wraps"', 'id="h-goal"', 'function goalRender()', 'function goalDays()', 'Behind an even pace by', 'id="j-news"', 'function newsRecord(d)',
                       'wsSet("newsdays"', 'function jNews(list)', 'id="j-wall"', 'function jWall(list)', 'openLb(t,shown)', 'id="j-report"', 'function repOpen()',
                       'window.print()', '@media print', 'function popOut(slug)', '"#pop="', 'body.popout', 'id="mk-pop"', '["start","Start my day"', '["report","Monthly report"'):
            self.assertIn(needle, self.page)

    def test_the_screenshot_wall_state_exists_before_the_first_journal_draw(self):
        # jRender() runs once while the page loads; anything it uses must already be set up, or the whole script stops
        self.assertLess(self.page.index('var WL={f:"all"'), self.page.index("\njRender();\n\n/* ---------------- start"))
        self.assertIn('id="kt-fatal"', self.page)

    def test_funding_settings_can_be_copied_to_another_copy(self):
        for needle in ('id="fd-copy"', 'id="fd-paste"', 'action:"import"', 'Paste Funding settings', 'FD.settings'):
            self.assertIn(needle, self.page)
        settings = self.page[self.page.index('id="tab-settings"'):self.page.index('id="tab-markets"')]
        funding_tab = self.page[self.page.index('id="tab-funding"'):self.page.index('id="tab-alerts"')]
        self.assertIn('id="fd-copy"', settings)                      # the buttons live in Settings
        self.assertNotIn('id="fd-copy"', funding_tab)

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
