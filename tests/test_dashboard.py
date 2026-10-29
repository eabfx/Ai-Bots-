import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request

from _load import load
from test_zones import entry_data

dash = load("trader-zones", "dashboard")
dz = dash.z                      # the zones module the dashboard itself uses
NOW = "2026-10-26 20:00"


class Patched(unittest.TestCase):
    """Run the dashboard on the entry fixture: one demand zone that gives one long entry."""

    def setUp(self):
        self.m5, self.m3, self.zone, _ = entry_data()
        self.saved = (dz.collect_zones, dz.get_bars, dz.resample, dz._raw_bars)
        dz.collect_zones = lambda *a, **k: [self.zone]
        # the real get_bars (which applies the --spot shift) on top of the fixture bars
        dz._raw_bars = lambda sym, tf, *a, **k: self.m3 if tf == "1m" else self.m5
        dz.resample = lambda *a, **k: self.m3
        self.addCleanup(self.restore)

    def restore(self):
        dz.collect_zones, dz.get_bars, dz.resample, dz._raw_bars = self.saved


class Snapshot(Patched):
    def snap(self, sym="MGC", **kw):
        cfg = dz.read_config("none.json")
        return dash.snapshot(sym, cfg, dz.parse_now(NOW), **kw)

    def test_snapshot_carries_the_zone_and_the_entry_in_dublin_time(self):
        s = self.snap()
        self.assertEqual(s["symbol"], "MGC")
        self.assertEqual(len(s["zones"]), 1)
        zr = s["zones"][0]
        self.assertEqual((zr["tf"], zr["kind"], zr["status"]), ("30m", "demand", "touched"))
        self.assertEqual((zr["low"], zr["high"]), (99.0, 99.8))
        self.assertRegex(zr["when"], r"^\w{3} \d{2} \w{3} \d{2}:\d{2}$")
        self.assertEqual(len(s["signals"]), 1)
        sig = s["signals"][0]
        self.assertEqual(sig["side"], "long")
        self.assertEqual(sig["rr"], 3.0)
        self.assertLess(sig["stop"], sig["entry"])
        self.assertGreater(sig["target"], sig["entry"])
        self.assertEqual(sig["entry_when"], dash.dublin_text(sig["entry_t"]))
        self.assertEqual(s["stages"], [])                     # stages only explain a day with no entry
        json.dumps(s)                                         # plain data, ready to send

    def test_the_last_price_is_the_last_one_minute_close(self):
        s = self.snap()
        self.assertEqual(s["price"], self.m3[-1]["c"])
        self.assertEqual(s["price_when"], dash.dublin_text(self.m3[-1]["t"] + 60))

    def test_with_no_entry_it_says_how_far_each_zone_got(self):
        dz._raw_bars = lambda sym, tf, *a, **k: self.m3 if tf == "1m" else self.m5[:20]
        dz.resample = lambda *a, **k: self.m3[:3]
        s = self.snap()
        self.assertEqual(s["signals"], [])
        self.assertTrue(s["stages"])
        self.assertIn("DEMAND", s["stages"][0]["head"])
        self.assertTrue(s["furthest"])

    def test_spot_shifts_the_prices_and_is_not_left_on_afterwards(self):
        plain = self.snap()
        shifted = self.snap(spot=plain["price"] - 5.0)
        self.assertTrue(shifted["shifted"])
        self.assertAlmostEqual(shifted["price"], plain["price"] - 5.0)
        self.assertTrue(any("Prices shifted by 5.00" in n for n in shifted["notes"]))
        self.assertEqual(dz.SHIFT, 0.0)
        self.assertEqual(self.snap()["price"], plain["price"])

    def test_a_gap_measured_earlier_is_reused_not_measured_again(self):
        plain = self.snap()
        first = self.snap(spot=plain["price"] - 5.0)
        self.assertAlmostEqual(first["shift_value"], 5.0)
        # the market moves on: reusing the gap keeps the shift at 5, where re-reading the old price would not
        self.m3 = [dict(b, o=b["o"] + 2, h=b["h"] + 2, l=b["l"] + 2, c=b["c"] + 2) for b in self.m3]
        again = self.snap(shift=first["shift_value"])
        self.assertAlmostEqual(again["shift_value"], 5.0)
        self.assertAlmostEqual(again["price"], plain["price"] + 2 - 5.0)
        self.assertTrue(again["shifted"])
        self.assertTrue(any("the gap measured when you entered your price" in n for n in again["notes"]))
        stale = self.snap(spot=plain["price"] - 5.0)                    # the old price read against the new data
        self.assertAlmostEqual(stale["shift_value"], 7.0)               # shows why re-reading it would be wrong
        self.assertEqual(dz.SHIFT, 0.0)

    def test_a_new_spot_price_wins_over_a_kept_gap(self):
        plain = self.snap()
        both = self.snap(spot=plain["price"] - 3.0, shift=9.0)
        self.assertAlmostEqual(both["shift_value"], 3.0)

    def test_a_kept_gap_is_ignored_for_a_spot_forex_pair(self):
        s = self.snap("EURUSD", shift=4.0)
        self.assertFalse(s["shifted"])
        self.assertEqual(s["shift_value"], 0.0)

    def test_no_price_means_no_shift(self):
        s = self.snap()
        self.assertFalse(s["shifted"])
        self.assertEqual(s["shift_value"], 0.0)

    def test_spot_is_ignored_for_a_spot_forex_pair(self):
        s = self.snap("EURUSD", spot=1.5)
        self.assertFalse(s["shifted"])
        self.assertTrue(s["spot_market"])
        self.assertTrue(any("already spot" in n for n in s["notes"]))

    def test_a_flat_one_minute_feed_is_flagged(self):
        flat = [{"t": b["t"], "o": b["c"], "h": b["c"], "l": b["c"], "c": b["c"]} for b in self.m3]
        dz._raw_bars = lambda sym, tf, *a, **k: flat if tf == "1m" else self.m5
        s = self.snap()
        self.assertTrue(any("carry one price each" in n for n in s["notes"]))

    def test_gold_without_a_spot_price_says_these_are_futures_prices(self):
        plain = self.snap("XAUUSD")
        self.assertTrue(any("COMEX gold futures prices" in n for n in plain["notes"]))
        with_spot = self.snap("XAUUSD", spot=plain["price"] - 5.0)
        self.assertFalse(any("COMEX gold futures prices" in n for n in with_spot["notes"]))
        self.assertFalse(any("COMEX gold futures prices" in n for n in self.snap("MGC")["notes"]))

    def test_snapshot_carries_the_time_it_was_taken_for_working_out_ages(self):
        self.assertEqual(self.snap()["now_t"], dz.parse_now(NOW))

    def test_an_unknown_market_is_a_plain_error(self):
        with self.assertRaises(dz.ZoneError):
            self.snap("DOGE")

    def test_shift_is_reset_even_when_the_run_fails(self):
        dz._raw_bars = lambda *a, **k: []                      # nothing to line the price up with
        with self.assertRaises(dz.ZoneError):
            self.snap(spot=100.0)
        self.assertEqual(dz.SHIFT, 0.0)


class Server(Patched):
    def setUp(self):
        Patched.setUp(self)
        self.server = dash.make_server(0, "none.json")
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

    def test_it_only_listens_on_this_computer(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_the_page_is_served_and_says_signals_only(self):
        code, kind, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", kind)
        self.assertIn("Nothing is ever placed from here", body)

    def test_the_page_loads_nothing_from_the_internet(self):
        _, _, body = self.get("/")
        self.assertNotIn("http://", body.replace("http://www.w3.org/2000/svg", ""))
        self.assertNotIn("https://", body)
        self.assertNotIn("<script src", body)

    def test_the_page_keeps_the_gap_and_reuses_it_until_a_new_price_is_typed(self):
        _, _, body = self.get("/")
        self.assertIn('"zd.gap."', body)
        self.assertIn('"&shift="', body)

    def test_snapshot_takes_a_kept_gap_and_rejects_a_bad_one(self):
        code, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00&shift=4.5")
        self.assertEqual(code, 200)
        self.assertAlmostEqual(json.loads(body)["shift_value"], 4.5)
        code, _, body = self.get("/api/snapshot?symbol=MGC&shift=abc")
        self.assertEqual(code, 400)
        self.assertIn("shift must be a number", json.loads(body)["error"])

    def test_zones_per_day_is_a_setting_the_page_sends(self):
        _, _, body = self.get("/")
        self.assertIn('id="perday"', body)
        self.assertIn('"&per_day="', body)
        code, _, text = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00&per_day=6")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(text)["per_day"], 6)
        self.assertEqual(self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")[0], 200)
        for bad in ("0", "21", "x"):
            code, _, text = self.get("/api/snapshot?symbol=MGC&per_day=" + bad)
            self.assertEqual(code, 400, bad)
            self.assertIn("per_day must be", json.loads(text)["error"])

    def test_the_page_filters_entries_by_age_and_folds_the_repeats_away(self):
        _, _, body = self.get("/")
        self.assertIn('id="window"', body)
        self.assertIn("last 24 hours", body)
        self.assertIn("d.now_t-r.entry_t<=hrs*3600", body)
        self.assertIn('el("details"', body)
        self.assertIn("Same trade also seen from", body)

    def test_the_page_lists_entries_newest_first_with_their_age(self):
        _, _, body = self.get("/")
        self.assertIn("d.signals.slice().reverse()", body)
        self.assertIn("min ago", body)

    def test_the_page_writes_server_text_with_textContent_not_innerHTML(self):
        _, _, body = self.get("/")
        self.assertNotIn("innerHTML", body)

    def test_markets_lists_the_five_in_order_and_marks_the_spot_pairs(self):
        code, _, body = self.get("/api/markets")
        self.assertEqual(code, 200)
        got = json.loads(body)["markets"]
        self.assertEqual([m["symbol"] for m in got], ["XAUUSD", "NQ", "MGC", "GBPUSD", "EURUSD"])
        self.assertEqual({m["symbol"] for m in got if m["spot"]}, {"GBPUSD", "EURUSD"})

    def test_snapshot_returns_json_for_a_market(self):
        code, kind, body = self.get("/api/snapshot?symbol=mgc&now=2026-10-26+20:00")
        self.assertEqual(code, 200)
        self.assertIn("application/json", kind)
        doc = json.loads(body)
        self.assertEqual(doc["symbol"], "MGC")
        self.assertEqual(len(doc["signals"]), 1)

    def test_snapshot_takes_a_spot_price_and_a_days_setting(self):
        code, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00&days=3&spot=95")
        self.assertEqual(code, 200)
        doc = json.loads(body)
        self.assertEqual(doc["days"], 3)
        self.assertTrue(doc["shifted"])

    def test_bad_requests_get_a_plain_error_and_the_server_keeps_going(self):
        for path, words in [("/api/snapshot?symbol=DOGE", "unknown market"),
                            ("/api/snapshot?symbol=MGC&spot=abc", "must be a number"),
                            ("/api/snapshot?symbol=MGC&days=0", "days must be"),
                            ("/api/snapshot?symbol=MGC&days=x", "days must be"),
                            ("/api/snapshot?symbol=MGC&now=yesterday", "wants New York time")]:
            code, _, body = self.get(path)
            self.assertEqual(code, 400, path)
            self.assertIn(words, json.loads(body)["error"], path)
        self.assertEqual(self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")[0], 200)

    def test_a_data_failure_is_reported_as_an_error_not_a_crash(self):
        def boom(*a, **k):
            raise OSError("no network")
        dz._raw_bars = boom
        code, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(code, 500)
        self.assertIn("no network", json.loads(body)["error"])
        self.assertEqual(self.get("/api/markets")[0], 200)

    def test_unknown_paths_are_404(self):
        self.assertEqual(self.get("/nope")[0], 404)

    def test_nothing_is_cached(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/api/markets" % self.port, timeout=10) as r:
            self.assertEqual(r.headers.get("Cache-Control"), "no-store")


class Command(unittest.TestCase):
    def test_a_busy_port_is_a_plain_error(self):
        server = dash.make_server(0, "none.json")
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = dash.main(["--port", str(port), "--no-browser"])
        self.assertEqual(code, 2)
        self.assertIn("Try --port", err.getvalue())


if __name__ == "__main__":
    unittest.main()
