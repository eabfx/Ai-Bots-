import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
markets = load("riskdesk", "markets")


def chart(price=100.0, prev=98.0, n=30, day=1_699_920_000 + 3600):
    ts = [day + i * 300 for i in range(n)]
    px = [price - (n - i) * 0.1 for i in range(n)]
    return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": price, "previousClose": prev, "gmtoffset": 0, "longName": "Test Co"},
                                             "timestamp": ts, "indicators": {"quote": [{"open": px, "high": [p + .2 for p in px], "low": [p - .2 for p in px], "close": px, "volume": [5] * n}]}}],
                                "error": None}})


class Parse(unittest.TestCase):
    def test_tile(self):
        t = markets.parse(chart(), "GC=F", "5m")
        self.assertEqual(t["symbol"], "GC=F")
        self.assertEqual(t["price"], 100.0)
        self.assertEqual(t["prev"], 98.0)
        self.assertAlmostEqual(t["pct"], 2 / 98 * 100)
        self.assertEqual(len(t["bars"]), 30)
        self.assertEqual(len(t["bars"][0]), 6)
        self.assertEqual(t["digits"], 2)

    def test_digits(self):
        self.assertEqual(markets.digits_for(1.08), 5)
        self.assertEqual(markets.digits_for(15), 4)

    def test_nulls_dropped_and_empty_rejected(self):
        doc = json.loads(chart(n=3))
        doc["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
        self.assertEqual(len(markets.parse(json.dumps(doc), "X", "5m")["bars"]), 2)
        with self.assertRaises(markets.MarketsError):
            markets.parse(json.dumps({"chart": {"result": None, "error": {"description": "No data found"}}}), "ZZZ", "5m")
        with self.assertRaises(markets.MarketsError):
            markets.parse("not json", "X", "5m")

    def test_symbol_rules(self):
        self.assertEqual(markets.clean_symbol(" eurusd=x "), "EURUSD=X")
        for bad in ("", "a b", "x" * 40, "A/B", "<script>"):
            with self.assertRaises(markets.MarketsError):
                markets.clean_symbol(bad)


class Fetch(unittest.TestCase):
    def setUp(self):
        markets._CACHE.clear()

    def test_batch_cache_and_errors(self):
        calls = []

        def fake(path):
            calls.append(path)
            if "BAD" in path:
                raise markets.z.ZoneError("Yahoo answered HTTP 404")
            return chart()
        out = markets.many([("GC=F", "5m"), ("BAD", "5m"), ("GC=F", "5m"), ("NQ=F", "1h")], fake)
        self.assertEqual(sorted(out), ["BAD|5m", "GC=F|5m", "NQ=F|1h"])
        self.assertIn("error", out["BAD|5m"])
        self.assertEqual(out["GC=F|5m"]["price"], 100.0)
        n = len(calls)
        markets.many([("GC=F", "5m")], fake)
        self.assertEqual(len(calls), n)                    # second ask is served from the short cache
        self.assertIn("interval=60m", [c for c in calls if "NQ" in c][0])

    def test_a_short_reuse_window_spares_the_feed(self):
        calls = []
        now = [1000.0]

        def fetch(path):
            calls.append(path)
            return chart(price=100.0 + len(calls))
        first = markets.many([("ZZ=F", "5m")], fetch, clock=lambda: now[0], ttl=2.5)
        again = markets.many([("ZZ=F", "5m")], fetch, clock=lambda: now[0] + 1, ttl=2.5)
        self.assertEqual(len(calls), 1)                                   # inside 2.5 seconds: same answer, no new request
        self.assertEqual(first["ZZ=F|5m"]["price"], again["ZZ=F|5m"]["price"])
        now[0] += 3
        later = markets.many([("ZZ=F", "5m")], fetch, clock=lambda: now[0], ttl=2.5)
        self.assertEqual(len(calls), 2)                                   # after it: a fresh price
        self.assertNotEqual(later["ZZ=F|5m"]["price"], first["ZZ=F|5m"]["price"])

    def test_bad_timeframe(self):
        with self.assertRaises(markets.MarketsError):
            markets.one("GC=F", "7m", lambda p: chart())


class Layout(unittest.TestCase):
    def test_clean(self):
        out = markets.clean_layout({"grid": "9x9", "tf": "2m", "style": "x", "tiles": [{"s": "gc=f", "tf": "1h"}, {"s": "bad sym"}, "nope", {"s": "nq=f", "style": "candles"}] })
        self.assertEqual(out["grid"], "3x2")
        self.assertEqual([t["s"] for t in out["tiles"]], ["GC=F", "NQ=F"])
        self.assertEqual(out["tiles"][0]["tf"], "1h")
        self.assertEqual(out["tiles"][1]["style"], "candles")
        self.assertEqual(len(markets.clean_layout({"tiles": [{"s": "A"}] * 40})["tiles"]), markets.MAX_TILES)

    def test_save_load(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(len(markets.load_layout(d)["tiles"]), 6)          # the starter set
            markets.save_layout(d, {"grid": "2x2", "tiles": [{"s": "BTC-USD"}]})
            got = markets.load_layout(d)
            self.assertEqual((got["grid"], got["tiles"][0]["s"]), ("2x2", "BTC-USD"))
            self.assertEqual(markets.save_layout(d, {"tiles": []})["tiles"], [])
            self.assertEqual(markets.load_layout(d)["tiles"], [])              # an emptied board stays empty


class Routes(unittest.TestCase):
    def setUp(self):
        markets._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = os.path.join(self.tmp.name, "config.json")
        with open(cfg, "w") as fh:
            json.dump({}, fh)
        self.srv = dash.make_server(0, cfg, offline=True)
        self.srv.RequestHandlerClass.mkt_fetch = staticmethod(lambda path: chart())
        self.addCleanup(setattr, self.srv.RequestHandlerClass, "mkt_fetch", None)
        self.addCleanup(self.srv.server_close)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_routes(self):
        code, doc = self.get("/api/mkt/layout")
        self.assertEqual(code, 200)
        self.assertEqual(len(doc["tiles"]), 6)
        self.assertIn("GC=F", [s for s, _ in doc["suggest"]])
        req = urllib.request.Request(self.base + "/api/mkt/layout", data=json.dumps({"grid": "2x1", "tiles": [{"s": "ES=F"}]}).encode(), method="POST",
                                     headers={"X-Requested-With": "zones-dashboard", "Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(json.load(resp)["grid"], "2x1")
        self.assertEqual(self.get("/api/mkt/layout")[1]["tiles"][0]["s"], "ES=F")
        code, out = self.get("/api/mkt/charts?c=GC%3DF:5m,NQ%3DF:1d")
        self.assertEqual(code, 200)
        self.assertEqual(out["GC=F|5m"]["price"], 100.0)
        self.assertEqual(self.get("/api/mkt/charts")[0], 400)

    def test_a_slow_request_does_not_make_the_others_wait(self):
        import time
        started = threading.Event()

        def slow(path):
            started.set()
            time.sleep(1.5)
            return chart()
        self.srv.RequestHandlerClass.mkt_fetch = staticmethod(slow)
        first = []
        t = threading.Thread(target=lambda: first.append(self.get("/api/mkt/charts?c=GC%3DF:5m")))
        t.start()
        self.assertTrue(started.wait(5))
        t0 = time.time()
        code, doc = self.get("/version.json")                       # asked while the slow chart is still being fetched
        self.assertEqual(code, 200)
        self.assertLess(time.time() - t0, 1.0)
        t.join(10)
        self.assertEqual(first[0][0], 200)

    def test_saves_are_taken_one_at_a_time(self):
        self.assertTrue(hasattr(self.srv.RequestHandlerClass, "WRITE_LOCK"))
        self.assertNotIn("/api/journal", self.srv.RequestHandlerClass.SLOW_POSTS)
        self.assertIn("/api/chat", self.srv.RequestHandlerClass.SLOW_POSTS)    # the AI answer never holds the saves back


if __name__ == "__main__":
    unittest.main()
