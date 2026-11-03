import json
import math
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
chart = load("riskdesk", "chart")


def payload(n, step, start=3900.0):
    """A Yahoo chart answer with n bars of a wavy price, ending now."""
    import time
    t0 = int(time.time()) // step * step - n * step
    ts, o, h, l, c = [], [], [], [], []
    p = start
    for i in range(n):
        d = math.sin(i / 6) * 5 + (22 if i % 41 == 0 else 0) * (1 if i % 2 else -1)
        ts.append(t0 + i * step)
        o.append(p)
        c.append(p + d)
        h.append(max(p, p + d) + 1.5)
        l.append(min(p, p + d) - 1.5)
        p += d
    return json.dumps({"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"open": o, "high": h, "low": l, "close": c}]}}]}})


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "config.json")
        chart._CACHE.clear()

    def snap(self, sym="XAUUSD", tf="15m", n=600, step=None):
        step = step or (3600 if tf in ("1h", "4h") else 86400 if tf == "1d" else 900 if tf == "15m" else 300)
        return chart.snapshot(sym, tf, self.config, fetch=lambda path: payload(n, step))

    def test_candles_come_back_oldest_first_with_the_market_details(self):
        d = self.snap()
        self.assertEqual((d["symbol"], d["tf"], d["digits"], d["secs"]), ("XAUUSD", "15m", 2, 900))
        self.assertEqual(len(d["bars"]), 300)
        times = [b[0] for b in d["bars"]]
        self.assertEqual(times, sorted(times))
        t, o, h, l, c = d["bars"][-1]
        self.assertTrue(l <= min(o, c) and h >= max(o, c))

    def test_zones_are_demand_or_supply_with_a_price_range(self):
        d = self.snap()
        self.assertTrue(d["zones"])
        for zn in d["zones"]:
            self.assertIn(zn["kind"], ("demand", "supply"))
            self.assertLess(zn["low"], zn["high"])
            self.assertIn(zn["status"], ("fresh", "touched"))

    def test_four_hour_candles_are_built_from_hourly_ones(self):
        d = self.snap(tf="4h", n=900)
        gaps = {b[0] - a[0] for a, b in zip(d["bars"], d["bars"][1:])}
        self.assertTrue(all(g % 14400 == 0 for g in gaps))

    def test_every_timeframe_works(self):
        for tf in chart.TIMEFRAMES:
            self.assertTrue(self.snap(tf=tf, n=700)["bars"], tf)

    def test_a_short_history_has_no_zones_but_still_draws(self):
        d = self.snap(n=10)
        self.assertEqual(d["zones"], [])
        self.assertEqual(len(d["bars"]), 10)

    def test_saved_zones_and_levels_are_read_from_the_zones_app_files(self):
        with open(os.path.join(self.tmp.name, "my_zones.json"), "w") as fh:
            json.dump({"XAUUSD": [{"kind": "demand", "low": 3900, "high": 3905, "when": "x", "note": "OB"},
                                  {"kind": "demand", "low": 5, "high": 1}, "junk"]}, fh)
        with open(os.path.join(self.tmp.name, "my_levels.json"), "w") as fh:
            json.dump({"XAUUSD": [{"kind": "support", "frame": "daily", "low": 3800, "high": 3810}]}, fh)
        d = self.snap()
        self.assertEqual([m["low"] for m in d["mine"]], [3900])           # the bad entries are skipped
        self.assertEqual(d["levels"][0]["frame"], "daily")

    def test_a_broken_saved_file_is_just_an_empty_list(self):
        with open(os.path.join(self.tmp.name, "my_levels.json"), "w") as fh:
            fh.write("{not json")
        self.assertEqual(self.snap()["levels"], [])

    def test_unknown_market_or_timeframe_is_refused_in_plain_words(self):
        with self.assertRaises(chart.z.ZoneError):
            chart.snapshot("TSLA", "15m", self.config, fetch=lambda p: "")
        with self.assertRaises(chart.ChartError):
            chart.snapshot("NQ", "7m", self.config, fetch=lambda p: "")

    def test_candles_are_kept_for_a_few_seconds_so_the_page_does_not_hammer_yahoo(self):
        calls = []
        f = lambda path: calls.append(path) or payload(300, 900)
        chart.snapshot("NQ", "15m", self.config, fetch=f)
        chart.snapshot("NQ", "15m", self.config, fetch=f)
        self.assertEqual(len(calls), 1)


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = os.path.join(self.tmp.name, "config.json")
        self.server = dash.make_server(0, config)
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), t.join(5)))

    def get(self, path):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_a_bad_market_is_a_400_with_a_message(self):
        code, doc = self.get("/api/chart?symbol=TSLA&tf=15m")
        self.assertEqual(code, 400)
        self.assertIn("use one of", doc["error"])

    def test_the_page_has_a_chart_tab_in_the_menu(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=10) as r:
            page = r.read().decode()
        self.assertIn('data-tab="chart"', page)
        self.assertIn('id="tab-chart"', page)
        self.assertIn('"home","macro","chart","journal"', page)


if __name__ == "__main__":
    unittest.main()


class PageFeatures(unittest.TestCase):
    """The page code for the Chart, Home layout, report and streaks is in the page and keeps its guard rails."""

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

    def test_the_chart_draws_zones_levels_and_the_live_price(self):
        for needle in ("function chDraw", "function chartLive", "/api/chart?symbol=", "My zones", "Levels"):
            self.assertIn(needle, self.page)
