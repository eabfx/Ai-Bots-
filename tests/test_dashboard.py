import contextlib
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from _load import load

dash = load("riskdesk", "dashboard")
NOW = "2026-10-26 20:00"


class Server(unittest.TestCase):
    """RiskDesk running on a throwaway port with an empty trader folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "config.json")
        self.server = dash.make_server(0, self.config)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def get(self, path):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=10) as r:
                return r.status, r.headers.get("Content-Type"), r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read().decode("utf-8")

    def post(self, path, body, headers=None, raw=None):
        data = raw if raw is not None else json.dumps(body).encode("utf-8")
        h = {"Content-Type": "application/json", "X-Requested-With": "zones-dashboard"}
        h.update(headers or {})
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))


class Basics(Server):
    def test_the_page_is_riskdesk_with_a_dark_and_light_theme_and_no_zones_parts(self):
        _, _, body = self.get("/")
        self.assertIn("<title>RiskDesk.ai</title>", body)
        self.assertIn('id="theme"', body)
        self.assertIn('store("zd.theme"', body)
        self.assertIn(':root[data-theme="light"]', body)
        for gone in ('id="tab-zones"', "/api/snapshot", "/api/mine", "/api/levels", "Size this trade", "Log in journal"):
            self.assertNotIn(gone, body)

    def test_home_is_the_first_page_with_a_sidebar_and_its_panels(self):
        _, _, body = self.get("/")
        for words in ('id="tab-home"', 'id="h-mkts"', 'id="h-strength"', 'id="h-news"', 'id="h-cal"', 'id="h-nums"',
                      'id="h-assist-box"', 'id="h-sessions"', "@media(min-width:901px)", "function dockChat", "function homeRender"):
            self.assertIn(words, body)
        self.assertLess(body.index('data-tab="home"'), body.index('data-tab="macro"'))

    def test_home_has_a_daily_plan_and_a_pre_trade_checklist_kept_in_the_browser(self):
        _, _, body = self.get("/")
        for words in ('id="pl-text"', 'id="ck-list"', 'id="ck-rules"', 'store("zd.plan."', "zd.ck.rules", "zd.ck.done.", "function planRender"):
            self.assertIn(words, body)
        start = body.index("function planRender")
        self.assertNotIn("innerHTML", body[start:body.index("/* ---------------- chat box")])

    def test_the_journal_has_a_weekly_review_built_without_innerhtml(self):
        _, _, body = self.get("/")
        for words in ('id="j-review"', "function jReview", "Check next", "does not predict anything"):
            self.assertIn(words, body)
        start = body.index("function jReview")
        self.assertNotIn("innerHTML", body[start:body.index("function jRender")])

    def test_the_journal_saves_to_the_pc_and_has_screenshots_account_filter_and_notion_import(self):
        _, _, body = self.get("/")
        for words in ('/api/journal"', "function jLoad", "function jPushNow", "function liveOnly", 'id="lb"', 'id="nt-card"',
                      "/api/import/notion", "/api/journal/image", 'id="j-img"', "UNITPCT"):
            self.assertIn(words, body)
        start, end = body.index("function openLb"), body.index("var ntReady")
        self.assertNotIn("innerHTML", body[start:end])
        self.assertNotIn("innerHTML", body[body.index("function ntShow"):body.index("function ntCall")])

    def test_backtest_alerts_and_connections_are_their_own_pages(self):
        _, _, body = self.get("/")
        for tab in ("backtest", "alerts", "connect"):
            self.assertIn('data-tab="%s"' % tab, body)
        for words in ('id="tab-alerts"', 'id="tab-connect"', 'id="al-list"', "function alertCheck", "function ruleCheck", "function alFire",
                      "function connRender", "Notification.permission"):
            self.assertIn(words, body)
        connect = body[body.index('id="tab-connect"'):body.index('id="tab-alerts"')]
        self.assertIn('id="nt-card"', connect)                                        # imports live on Connections, not on the Journal page
        self.assertIn('id="imp-card"', connect)
        journal = body[body.index('id="tab-journal"'):]
        self.assertNotIn('id="nt-card"', journal)
        self.assertNotIn('id="imp-card"', journal)
        self.assertNotIn("innerHTML", body[body.index("function alFire"):body.index("function connRender")])

    def test_backtests_never_count_in_home_prop_coach_or_chat(self):
        _, _, body = self.get("/")
        self.assertIn("liveOnly().filter(function(t){return !from", body)                 # prop firm room
        self.assertIn("trades:liveOnly().slice(0,200)", body)                              # the coach
        self.assertIn("trades:liveOnly().slice(0,100)", body)                              # the chat box
        self.assertIn("tl=liveOnly().filter", body)                                        # Home, today

    def test_outside_text_on_home_is_never_put_in_through_innerhtml(self):
        _, _, body = self.get("/")
        start = body.index("function homeRender")
        end = body.index("/* ---------------- chat box")
        self.assertNotIn("innerHTML", body[start:end])

    def test_the_zones_routes_are_gone_from_riskdesk(self):
        for path in ("/api/markets", "/api/snapshot?symbol=MGC"):
            self.assertEqual(self.get(path)[0], 404)
        for path in ("/api/mine/add", "/api/levels/add"):
            self.assertEqual(self.post(path, {})[0], 404)

    def test_the_trades_tab_has_position_size_and_risk_reward_and_the_micro_gold_preset(self):
        _, _, body = self.get("/")
        for words in ('id="ps-inst"', 'id="ps-out"', 'id="rr-out"', "Position size", "Risk / reward",
                      'id:"MGC"', 'id:"NQ"', 'id:"XAUUSD"', "Breakeven win rate"):
            self.assertIn(words, body)
        self.assertIn("Math.floor(raw/step+1e-9)*step", body)                       # size rounds down, never up

    def test_the_journal_keeps_its_format_and_guards_the_csv_and_the_restore(self):
        _, _, body = self.get("/")
        self.assertIn('var JKEY="riskdesk_journal_v1"', body)
        self.assertIn(r"/^[=+\-@\t\r]/.test(s)", body)                              # no spreadsheet formulas from notes
        self.assertIn("data.length>20000", body)
        self.assertIn("cleanTrade", body)


class CoachRoutes(Server):
    def setUp(self):
        Server.setUp(self)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        self.sent = []

        def fake(url, headers, body):
            self.sent.append((headers, json.loads(body)))
            return 200, json.dumps({"content": [{"type": "text", "text": "Gold is your edge."}]})
        self.server.RequestHandlerClass.coach_post = staticmethod(fake)

    TRADES = [{"date": "2026-10-26", "inst": "XAUUSD", "dir": "short", "tag": "15m supply", "risk": 100, "pl": 300, "notes": "x"}]

    def test_status_says_whether_a_key_exists_and_never_shows_it(self):
        _, _, js = self.get("/api/coach/status")
        self.assertFalse(json.loads(js)["ready"])
        with open(os.path.join(self.tmp.name, "anthropic_key.txt"), "w") as fh:
            fh.write("sk-secret")
        _, _, js = self.get("/api/coach/status")
        self.assertTrue(json.loads(js)["ready"])
        self.assertNotIn("sk-secret", js)

    def test_a_gemini_key_alone_is_enough_and_goes_to_google(self):
        with open(os.path.join(self.tmp.name, "gemini_key.txt"), "w") as fh:
            fh.write("g-secret")
        _, _, js = self.get("/api/coach/status")
        self.assertEqual(json.loads(js)["service"], "gemini")
        seen = []
        self.server.RequestHandlerClass.coach_post = staticmethod(lambda u, h, b: (
            seen.append((u, h)) or (200, json.dumps({"candidates": [{"content": {"parts": [{"text": "From Gemini."}]}}]}))))
        code, doc = self.post("/api/coach", {"trades": self.TRADES})
        self.assertEqual((code, doc), (200, {"answer": "From Gemini."}))
        self.assertIn("generativelanguage.googleapis.com", seen[0][0])
        self.assertEqual(seen[0][1]["x-goog-api-key"], "g-secret")

    def test_no_key_is_a_clear_400_and_nothing_goes_out(self):
        code, doc = self.post("/api/coach", {"trades": self.TRADES})
        self.assertEqual(code, 400)
        self.assertIn("anthropic_key.txt", doc["error"])
        self.assertEqual(self.sent, [])

    def test_with_a_key_the_answer_comes_back_and_the_key_stays_on_the_server(self):
        with open(os.path.join(self.tmp.name, "anthropic_key.txt"), "w") as fh:
            fh.write("sk-secret")
        code, doc = self.post("/api/coach", {"trades": self.TRADES, "question": "How am I doing?",
                                             "indicator": {"symbol": "MGC", "entries": 2}})
        self.assertEqual(code, 200)
        self.assertEqual(doc, {"answer": "Gold is your edge."})
        self.assertEqual(self.sent[0][0]["x-api-key"], "sk-secret")
        self.assertNotIn("sk-secret", json.dumps(doc))

    def test_it_is_guarded_like_the_other_posts_and_takes_a_bigger_body(self):
        code, _ = self.post("/api/coach", {"trades": self.TRADES}, headers={"X-Requested-With": "other"})
        self.assertEqual(code, 403)
        code, _ = self.post("/api/coach", {"trades": self.TRADES}, headers={"Host": "evil.example"})
        self.assertEqual(code, 403)
        big = {"trades": [dict(self.TRADES[0], notes="n" * 300)] * 200}        # about 60 KB: over the 20 KB other posts allow
        self.assertGreater(len(json.dumps(big)), 20000)
        code, doc = self.post("/api/coach", big)
        self.assertEqual(code, 400)
        self.assertIn("anthropic_key.txt", doc["error"])                 # it got as far as the key check

    def test_the_page_has_the_coach_card_and_says_what_is_sent(self):
        _, _, body = self.get("/")
        for needle in ('id="coach-go"', "Ask the coach", "sends your logged trades", "/api/coach/status"):
            self.assertIn(needle, body)


class MacroRoutes(Server):
    """The macro desk and the chat box, with fake feeds and a fake AI so nothing goes out."""

    def setUp(self):
        Server.setUp(self)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)
        import test_macro
        self.tm = test_macro
        dash.macro.clear_cache()                       # the dashboard's own copy of macro.py, not test_macro's
        self.addCleanup(dash.macro.clear_cache)
        self.sent = []
        handler = self.server.RequestHandlerClass
        handler.macro_net = self.tm.FakeNet(errors=dash.macro)
        handler.coach_post = staticmethod(lambda u, h, b: (self.sent.append((u, h, json.loads(b))) or (
            200, json.dumps({"content": [{"type": "text", "text": "Tone: Calm.\n\nGold and MGC: Quiet.\n\nNasdaq (NQ): Quiet."}]}))))
        with open(os.path.join(self.tmp.name, "anthropic_key.txt"), "w") as fh:
            fh.write("sk-test")

    NOW = "2026-10-30 08:00"        # New York time: 12:00 UTC, the moment the macro fixtures are built around

    def test_the_macro_route_returns_prices_calendar_headlines_sessions_and_mood(self):
        code, _, js = self.get("/api/macro?now=" + self.NOW.replace(" ", "+"))
        doc = json.loads(js)
        self.assertEqual(code, 200)
        for key in ("quotes", "events", "headlines", "sessions", "mood", "notes", "as_of"):
            self.assertIn(key, doc)
        self.assertEqual(doc["as_of"], "Fri 30 Oct 12:00")
        self.assertEqual(len(doc["events"]), 3)

    def test_a_dead_feed_is_a_note_not_an_error(self):
        self.server.RequestHandlerClass.macro_net = self.tm.FakeNet(fail=["calendar"], errors=dash.macro)
        code, _, js = self.get("/api/macro?now=" + self.NOW.replace(" ", "+"))
        doc = json.loads(js)
        self.assertEqual(code, 200)
        self.assertEqual(doc["events"], [])
        self.assertTrue(any("Calendar" in n for n in doc["notes"]))

    def test_the_brief_is_written_from_the_feeds_and_split_into_parts(self):
        code, doc = self.post("/api/macro/brief", {"now": self.NOW, "focus": "gold into the PCE"})
        self.assertEqual(code, 200)
        self.assertEqual(doc["parts"]["Tone"], "Calm.")
        sent = self.sent[0][2]
        text = sent["messages"][0]["content"]
        self.assertIn("Core PCE Price Index m/m", text)
        self.assertIn("focus on: gold into the PCE", text)
        self.assertIn("macro brief", sent["system"])

    def test_the_brief_without_a_key_is_a_plain_400(self):
        os.remove(os.path.join(self.tmp.name, "anthropic_key.txt"))
        code, doc = self.post("/api/macro/brief", {"now": self.NOW})
        self.assertEqual(code, 400)
        self.assertIn("No API key", doc["error"])
        self.assertEqual(self.sent, [])

    def test_chat_sends_the_conversation_and_what_is_on_screen_and_nothing_else(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "explain my entry"}]
        ctx = {"tab": "zones", "symbol": "XAUUSD", "price": 4000.5, "secret": "do not send",
               "zones": [{"tf": "30m", "kind": "demand", "low": 1, "high": 2, "evil": "x"}],
               "entries": [{"side": "long", "entry": 3, "result": "target"}] * 40,
               "indicator": {"entries": 3, "hit_target": 1, "junk": 9}}
        trades = [{"date": "2026-10-26", "inst": "XAUUSD", "dir": "long", "risk": 100, "pl": 300, "notes": "n"}]
        code, doc = self.post("/api/chat", {"messages": msgs, "context": ctx, "trades": trades, "now": self.NOW})
        self.assertEqual(code, 200)
        self.assertIn("answer", doc)
        body = self.sent[0][2]
        self.assertEqual([m["role"] for m in body["messages"]], ["user", "assistant", "user"])
        system = body["system"]
        self.assertIn('"symbol": "XAUUSD"', system)
        self.assertNotIn("do not send", system)
        self.assertNotIn("evil", system)
        self.assertNotIn("junk", system)
        self.assertEqual(system.count('"side": "long"'), 10)               # capped at ten entries
        self.assertIn("JOURNAL TOTALS", system)
        self.assertIn("not loaded yet", system)                            # the Macro tab has not been opened

    def test_chat_knows_the_macro_numbers_once_the_tab_has_been_opened(self):
        self.get("/api/macro?now=" + self.NOW.replace(" ", "+"))
        self.post("/api/chat", {"messages": [{"role": "user", "content": "hi"}], "now": self.NOW})
        self.assertIn("Core PCE Price Index m/m", self.sent[0][2]["system"])

    def test_chat_needs_a_question_and_a_key_and_is_guarded(self):
        code, doc = self.post("/api/chat", {"messages": [{"role": "assistant", "content": "x"}]})
        self.assertEqual(code, 400)
        self.assertIn("question", doc["error"])
        code, _ = self.post("/api/chat", {"messages": [{"role": "user", "content": "hi"}]}, headers={"X-Requested-With": "x"})
        self.assertEqual(code, 403)
        os.remove(os.path.join(self.tmp.name, "anthropic_key.txt"))
        code, doc = self.post("/api/chat", {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 400)
        self.assertIn("No API key", doc["error"])

    def test_the_page_has_the_macro_tab_and_the_chat_box(self):
        _, _, body = self.get("/")
        for needle in ('data-tab="macro"', 'id="tab-macro"', "Capital flow", "Economic calendar", "AI macro brief",
                       'id="chat-btn"', "/api/chat", "/api/macro/brief", "noopener noreferrer", "Your bias"):
            self.assertIn(needle, body)
        self.assertNotIn("innerHTML", body.split("/* ---------------- macro desk")[1])    # outside text is only ever set as text


class JournalPage(Server):
    def test_the_journal_tab_has_the_overview_charts_and_reflection_fields(self):
        _, _, body = self.get("/")
        for needle in ('id="j-total"', 'id="j-score"', 'id="j-curve"', 'id="j-cal"', 'id="j-week"', 'id="j-groups"',
                       'id="j-range"', 'id="j-grade"', 'id="j-plan"', 'id="j-emo"', "Edge score", "Cumulative P/L",
                       "Plan followed", "Profit factor", "riskdesk_journal_v1"):
            self.assertIn(needle, body)
        journal = body.split("/* ---------------- journal")[1].split("/* ---------------- macro desk")[0]
        self.assertNotIn("innerHTML", journal)                         # trade text is only ever set as text
        self.assertIn("does not predict anything", journal)

    def test_old_backups_still_load_because_the_new_fields_are_optional(self):
        _, _, body = self.get("/")
        self.assertIn('grade:g&&"ABCD".indexOf(g)>=0?g:""', body)
        self.assertIn('plan:t.plan==="followed"||t.plan==="deviated"?t.plan:""', body)


class DefaultPaths(unittest.TestCase):
    def test_settings_and_keys_are_found_from_the_repo_not_the_folder_it_was_started_in(self):
        trader = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(dash.__file__))), "trader")
        self.assertEqual(dash.TRADER, trader)
        self.assertTrue(os.path.isabs(dash.TRADER))
        for name in ("config_path",):
            self.assertTrue(os.path.isabs(getattr(dash.Handler, name)), name)
            self.assertTrue(getattr(dash.Handler, name).startswith(trader))


class TradesPage(Server):
    def test_the_trades_tab_has_the_contract_reference_exchange_rate_margin_and_prop_room(self):
        _, _, body = self.get("/")
        for needle in ('id="ref-rows"', 'id="ps-fx"', 'id="ps-margin"', 'id="pf-out"', 'id="pf-type"', 'id="pf-use"',
                       "Prop firm room", "Contract reference", 'id:"GC",tick:0.1', 'id:"MNQ",tick:0.25', 'id:"ES",tick:0.25'):
            self.assertIn(needle, body)
        prop = body.split("/* prop firm room")[1].split("function setSeg(")[0]
        self.assertNotIn("innerHTML", prop)
        self.assertIn("Firms that trail on open profit", body)          # the page says what it does not model


class JournalImport(Server):
    def test_the_journal_has_a_time_field_session_and_hour_views_and_a_csv_importer(self):
        _, _, body = self.get("/")
        for needle in ('id="j-time"', 'data-by="session"', 'data-by="hour"', 'id="imp-file"', 'id="imp-go"', 'id="imp-pl"',
                       "Import from a broker CSV", "nothing is uploaded", "already in your journal", "function csvParse(",
                       "function parseWhen(", "function parseNum(", "Asia (00:00-07:00)"):
            self.assertIn(needle, body)
        journal = body.split("/* ---------------- journal")[1].split("/* ---------------- macro desk")[0]
        self.assertNotIn("innerHTML", journal)                         # file contents are only ever set as text
        self.assertNotIn("fetch(", journal.split("/* import from a broker CSV")[1].split("function jError(")[0])


class PhoneLayout(Server):
    def test_the_page_has_a_phone_layout_with_a_bottom_tab_bar(self):
        _, _, body = self.get("/")
        self.assertIn("viewport-fit=cover", body)
        phone = body.split("/* phone:")[1].split("</style>")[0]
        for needle in ("@media(max-width:640px)", ".bar nav{position:fixed", "bottom:0", "env(safe-area-inset-bottom)",
                       "font-size:16px", ".chatbtn{bottom:"):
            self.assertIn(needle, phone)


class Launcher(unittest.TestCase):
    def test_the_double_click_launcher_starts_the_dashboard_from_the_repo_folder(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "riskdesk", "RiskDesk.bat")
        with open(path, newline="") as fh:
            text = fh.read()
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertIn("py riskdesk\\dashboard.py", text)
        self.assertIn("\r\n", text)                                   # Windows line endings


class Command(unittest.TestCase):
    def test_a_busy_port_is_a_plain_error(self):
        server = dash.make_server(0, "none.json")
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        err, result = io.StringIO(), []
        # run it in a thread: a second dashboard that started anyway would serve forever and hang the tests
        runner = threading.Thread(target=lambda: result.append(dash.main(["--port", str(port), "--no-browser"])),
                                  daemon=True)
        with contextlib.redirect_stderr(err):
            runner.start()
            runner.join(10)
        self.assertFalse(runner.is_alive(), "a second dashboard started on a port that was already in use")
        self.assertEqual(result, [2])
        self.assertIn("Try --port", err.getvalue())

    def test_starting_it_twice_just_opens_the_one_that_is_running(self):
        server = dash.make_server(0, "none.json")
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        out, result = io.StringIO(), []
        runner = threading.Thread(target=lambda: result.append(dash.main(["--port", str(port), "--no-browser"])), daemon=True)
        with contextlib.redirect_stdout(out):
            runner.start()
            runner.join(10)
        self.assertEqual(result, [0])
        self.assertIn("already running", out.getvalue())

    def test_address_reuse_is_off_on_windows_where_it_lets_two_servers_share_a_port(self):
        self.assertEqual(dash.DashboardServer.allow_reuse_address, os.name != "nt")


if __name__ == "__main__":
    unittest.main()


class KeptButNotCounted(unittest.TestCase):
    """No-trade days, invalid trades and tests are stored but never reach the numbers."""
    PAGE = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()

    def test_pools_leave_out_skipped_entries_and_they_have_their_own_list(self):
        self.assertIn('t.acct!=="backtest"&&!t.skip', self.PAGE)
        self.assertIn('t.acct==="backtest"&&!t.skip', self.PAGE)
        self.assertIn("function jSkipped()", self.PAGE)
        self.assertIn('id="j-skipcard"', self.PAGE)
        self.assertIn('t.skip==="none"||t.skip==="invalid"||t.skip==="test"', self.PAGE)       # survives load and save

    def test_an_older_tab_goes_dormant_and_cannot_overwrite_the_journal(self):
        self.assertIn('new BroadcastChannel("riskdesk-one-tab")', self.PAGE)
        self.assertIn("if(dormant)return;                                   /* an old tab must never overwrite", self.PAGE)
        self.assertIn("if(macroBusy||dormant)return;", self.PAGE)

    def test_the_pnl_hero_has_period_navigation_accounts_and_a_recent_trades_strip(self):
        for s in ('id="h-card"', 'id="h-per"', 'id="h-strip"', 'id="h-accts"', "function jHero()", "function hCount(", "hAuto", "vs previous "):
            self.assertIn(s, self.PAGE)
        self.assertIn("jHero();jAccts();setUnit(list);", self.PAGE)                    # the hero runs first, then the sections below use the page's unit
        self.assertIn("var at=String(t.acctType", self.PAGE)                    # the account type survives load and save

    def test_a_second_import_adds_screenshots_to_trades_already_there(self):
        self.assertIn("a trade imported earlier without its screenshot gets it now", self.PAGE)

    def test_trades_are_grouped_by_day_with_filters_search_and_a_gallery(self):
        for s in ('id="j-days"', 'id="t-q"', 'id="t-day"', 'id="t-view"', 'id="t-pick"', "function tRender(", "function tFilter(", "function tGallery(", "View day"):
            self.assertIn(s, self.PAGE)
        self.assertNotIn('id="j-rows"', self.PAGE)                              # the flat table is gone

    def test_accounts_panel_filters_the_whole_journal_by_account(self):
        for s in ('id="j-accts"', 'id="ac-add"', 'id="j-at"', "function jAccts()", "function jBase()", "function atName(", "acctType:live?$(\"j-at\").value.trim()"):
            self.assertIn(s, self.PAGE)
        self.assertIn("jAT?jBase().filter(", self.PAGE)                          # the hero, stats and trades all read the filtered pool

    def test_monthly_wrapped_is_a_fixed_rule_story_and_the_hero_greeting_has_its_own_id(self):
        for s in ('id="wrap"', 'id="h-wrap"', "function wSlides()", "function wShow(", "function wCount(", "Best trade", "Hardest trade", "How you traded"):
            self.assertIn(s, self.PAGE)
        self.assertEqual(self.PAGE.count('id="h-greet"'), 1)                    # the Home greeting; the hero's is h-hello

    def test_the_big_pnl_number_is_green_when_up_and_red_when_down(self):
        self.assertIn(".h-num.up{color:var(--up)", self.PAGE)
        self.assertIn(".h-num.down{color:var(--down)", self.PAGE)

    def test_a_trade_row_opens_to_editable_notes_and_account(self):
        self.assertIn('ta.setAttribute("aria-label","Notes")', self.PAGE)
        self.assertIn('Save changes', self.PAGE)
        self.assertIn("if(at)t.acctType=at;else delete t.acctType;jSave();jRender()", self.PAGE)

    def test_the_page_can_be_installed_as_an_app(self):
        for s in ('rel="manifest" href="/manifest.webmanifest"', 'rel="apple-touch-icon"', 'serviceWorker.register("/sw.js")', 'name="theme-color"'):
            self.assertIn(s, self.PAGE)
        root = os.path.join(os.path.dirname(__file__), "..", "riskdesk", "assets")
        manifest = json.load(open(os.path.join(root, "manifest.webmanifest"), encoding="utf-8"))
        self.assertEqual((manifest["display"], manifest["start_url"]), ("standalone", "/"))
        sizes = {i["sizes"] for i in manifest["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        for i in manifest["icons"]:
            self.assertTrue(os.path.exists(os.path.join(root, i["src"].lstrip("/"))), i["src"])
        sw = open(os.path.join(root, "sw.js"), encoding="utf-8").read()
        self.assertNotIn("caches.", sw)                                          # the worker keeps nothing: no stale pages, no copy of your data

    def test_share_card_is_drawn_on_a_canvas_and_can_be_saved_or_copied(self):
        for s in ('id="sh-cv"', 'id="h-share"', 'id="sh-dl"', 'id="sh-cp"', "function shDraw()", "function shOpen(", 'toBlob(cb,"image/png")', "Share this month"):
            self.assertIn(s, self.PAGE)


class AllowedHosts(unittest.TestCase):
    """A private tunnel (Tailscale) reaches the dashboard under its own name; nothing else is let in."""

    def start(self, **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = os.path.join(self.tmp.name, "config.json")
        if kw.get("cfg") is not None:
            with open(config, "w", encoding="utf-8") as fh:
                json.dump(kw["cfg"], fh)
        server = dash.make_server(0, config, allowed_hosts=kw.get("cli", ()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        return server.server_address[1]

    def status(self, port, host):
        req = urllib.request.Request("http://127.0.0.1:%d/api/coach/status" % port, headers={"Host": host})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_by_default_only_this_computer_is_answered(self):
        port = self.start()
        self.assertEqual(self.status(port, "127.0.0.1:%d" % port), 200)
        for host in ("pc.tail1234.ts.net", "evil.example.com", "evil.example.com:%d" % port, ""):
            self.assertEqual(self.status(port, host), 403, host)

    def test_a_name_from_the_config_file_or_the_command_line_is_answered_with_or_without_a_port(self):
        port = self.start(cfg={"allowed_hosts": ["PC.tail1234.ts.net"]}, cli=["phone-pc"])
        for host in ("pc.tail1234.ts.net", "pc.tail1234.ts.net:8765", "PC.TAIL1234.TS.NET", "phone-pc"):
            self.assertEqual(self.status(port, host), 200, host)
        for host in ("other.tail1234.ts.net", "tail1234.ts.net", "pc.tail1234.ts.net.evil.com"):
            self.assertEqual(self.status(port, host), 403, host)

    def test_a_config_saved_with_a_byte_order_mark_still_works(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = os.path.join(tmp.name, "config.json")
        with open(config, "w", encoding="utf-8-sig") as fh:
            json.dump({"allowed_hosts": ["pc.tail1234.ts.net"]}, fh)
        self.assertEqual(dash.configured_hosts(config), frozenset({"pc.tail1234.ts.net"}))

    def test_odd_entries_in_the_config_are_ignored(self):
        port = self.start(cfg={"allowed_hosts": ["", "a b", "x/y", 5, None, "ok.example"]})
        self.assertEqual(self.status(port, "ok.example"), 200)
        self.assertEqual(self.status(port, "a b"), 403)

    def test_posts_from_an_allowed_name_still_need_the_dashboard_header(self):
        port = self.start(cli=["pc.tail1234.ts.net"])
        req = urllib.request.Request("http://127.0.0.1:%d/api/journal" % port, data=b'{"trades":[]}', method="POST",
                                     headers={"Host": "pc.tail1234.ts.net", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)
