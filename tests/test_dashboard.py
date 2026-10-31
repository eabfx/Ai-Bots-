import contextlib
import http.client
import io
import json
import os
import tempfile
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


class ChartCache(unittest.TestCase):
    """The dashboard keeps downloaded charts for a minute so a refresh does not wait on Yahoo again."""

    def setUp(self):
        self.calls = []
        self.saved = (dash._fetch_charts, dict(dash._CHARTS), dash.time.time)
        self.clock = [1000.0]
        dash._CHARTS.clear()
        dash.time.time = lambda: self.clock[0]

        def fake(sym, interval, days, offline=None):
            self.calls.append((sym, interval, days, offline))
            return [{"t": 1, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}]
        dash._fetch_charts = fake
        self.addCleanup(self.restore)

    def restore(self):
        dash._fetch_charts, saved, dash.time.time = self.saved
        dash._CHARTS.clear()
        dash._CHARTS.update(saved)

    def test_the_dashboard_reads_charts_through_the_cache(self):
        self.assertIs(dz._raw_bars, dash._cached_charts)

    def test_a_second_request_inside_a_minute_does_not_download_again(self):
        dash._cached_charts("MGC", "5m", 2)
        self.clock[0] += dash.CACHE_SECS - 1
        dash._cached_charts("MGC", "5m", 2)
        self.assertEqual(len(self.calls), 1)

    def test_after_a_minute_it_downloads_again(self):
        dash._cached_charts("MGC", "5m", 2)
        self.clock[0] += dash.CACHE_SECS
        dash._cached_charts("MGC", "5m", 2)
        self.assertEqual(len(self.calls), 2)

    def test_each_market_frame_and_number_of_days_is_kept_apart(self):
        for args in (("MGC", "5m", 2), ("NQ", "5m", 2), ("MGC", "1m", 2), ("MGC", "5m", 7)):
            dash._cached_charts(*args)
        self.assertEqual(len(self.calls), 4)

    def test_saved_charts_are_never_cached(self):
        dash._cached_charts("MGC", "5m", 2, "folder")
        dash._cached_charts("MGC", "5m", 2, "folder")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(dash._CHARTS, {})

    def test_changing_the_bars_you_were_given_does_not_change_the_kept_copy(self):
        first = dash._cached_charts("MGC", "5m", 2)
        first[0]["c"] = 999.0
        first.append({"t": 2})
        again = dash._cached_charts("MGC", "5m", 2)
        self.assertEqual(again, [{"t": 1, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5}])


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

    def test_each_zone_carries_whether_it_was_respected_and_when(self):
        base = dict(self.zone, respect="respected", respect_t=self.zone["t"] + 7200)
        self.zone = base
        zr = self.snap()["zones"][0]
        self.assertEqual(zr["respect"], "respected")
        self.assertEqual(zr["respect_when"], dash.dublin_text(base["respect_t"]))
        self.zone = dict(self.zone, respect="testing", respect_t=None)
        zr = self.snap()["zones"][0]
        self.assertEqual((zr["respect"], zr["respect_when"]), ("testing", None))

    def test_each_zone_carries_its_visits_in_dublin_time(self):
        t0 = self.zone["t"]
        self.zone = dict(self.zone, touches=[
            {"t": t0 + 3600, "end_t": t0 + 5400, "session": "ny", "depth": 0.5, "result": "held", "result_t": t0 + 7200},
            {"t": t0 + 90000, "end_t": t0 + 90000, "session": None, "depth": 1.4, "result": "open", "result_t": None}])
        zr = self.snap()["zones"][0]
        self.assertEqual(len(zr["touches"]), 2)
        one, two = zr["touches"]
        self.assertEqual((one["when"], one["session"], one["depth"], one["result"], one["result_when"]),
                         (dash.dublin_text(t0 + 3600), "ny", 0.5, "held", dash.dublin_text(t0 + 7200)))
        self.assertEqual((two["session"], two["result"], two["result_when"]), (None, "open", None))
        json.dumps(zr)

    def test_a_zone_with_no_visits_has_an_empty_list(self):
        self.assertEqual(dash.zone_row({k: v for k, v in self.zone.items() if k != "touches"})["touches"], [])

    def test_a_zone_with_no_verdict_still_makes_a_row(self):
        zr = dash.zone_row({k: v for k, v in self.zone.items() if k not in ("respect", "respect_t")})
        self.assertEqual((zr["respect"], zr["respect_when"]), (None, None))

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

    def test_my_zones_are_added_to_the_tools_own_and_always_shown(self):
        when = dz.to_dublin(self.zone["t"]).strftime("%Y-%m-%d %H:%M")
        mine = [{"kind": "demand", "low": 99.0, "high": 99.8, "when": when}]
        cfg = dz.read_config("none.json")
        s = dash.snapshot("MGC", cfg, dz.parse_now(NOW), mine=mine)
        self.assertEqual(sorted(r["tf"] for r in s["zones"]), ["30m", "mine"])
        self.assertEqual(s["mine"][0]["index"], 0)
        plain = self.snap()
        self.assertEqual(len(plain["signals"]), len(s["signals"]))           # the same trade, merged not doubled
        self.assertEqual([r["tf"] for r in plain["zones"]], ["30m"])

    def test_an_unknown_market_is_a_plain_error(self):
        with self.assertRaises(dz.ZoneError):
            self.snap("DOGE")

    def test_shift_is_reset_even_when_the_run_fails(self):
        dz._raw_bars = lambda *a, **k: []                      # nothing to line the price up with
        with self.assertRaises(dz.ZoneError):
            self.snap(spot=100.0)
        self.assertEqual(dz.SHIFT, 0.0)


class Rules(unittest.TestCase):
    def test_before_rule_uses_the_candle_before_the_move_and_looser_limits(self):
        cfg = dash.apply_rule(dz.read_config("none.json"), "before")
        self.assertEqual((cfg["zone_candle"], cfg["move_atr"], cfg["base_body_atr"]), ("last", 1.3, 0.7))

    def test_both_rule_asks_for_the_standard_zones_and_the_users_together(self):
        cfg = dash.apply_rule(dz.read_config("none.json"), "both")
        self.assertEqual(cfg["zone_candle"], "extreme")
        self.assertEqual(cfg["union_with"], {"zone_candle": "last", "move_atr": 1.3, "base_body_atr": 0.7})
        self.assertIsNone(dash.apply_rule(cfg, "before")["union_with"])

    def test_standard_rule_changes_nothing(self):
        plain = dz.read_config("none.json")
        cfg = dash.apply_rule(dz.read_config("none.json"), "standard")
        self.assertIsNone(cfg.pop("union_with"))
        self.assertEqual(cfg, plain)

    def test_standard_rule_wins_over_a_config_file_that_sets_the_same_settings(self):
        cfg = dz.read_config("none.json")
        cfg.update(zone_candle="last", move_atr=1.3, base_body_atr=0.7)
        dash.apply_rule(cfg, "standard")
        plain = dz.read_config("none.json")
        self.assertEqual((cfg["zone_candle"], cfg["move_atr"], cfg["base_body_atr"]),
                         (plain["zone_candle"], plain["move_atr"], plain["base_body_atr"]))

    def test_unknown_rule_is_refused(self):
        with self.assertRaises(dz.ZoneError):
            dash.apply_rule(dz.read_config("none.json"), "wild")


class LevelsData(unittest.TestCase):
    def lv(self, **kw):
        d = {"index": 0, "kind": "support", "frame": "daily", "low": 99.0, "high": 101.0, "note": ""}
        d.update(kw)
        return d

    def test_a_range_touching_a_level_is_marked_and_one_that_misses_it_is_not(self):
        levels = [self.lv(), self.lv(index=1, low=110.0, high=120.0, frame="4h", kind="resistance")]
        self.assertEqual([h["index"] for h in dash.levels_at(100.5, 105.0, levels)], [0])
        self.assertEqual([h["index"] for h in dash.levels_at(101.0, 101.0, levels)], [0])     # a price on the edge
        self.assertEqual(dash.levels_at(102.0, 109.0, levels), [])
        self.assertEqual([h["frame"] for h in dash.levels_at(95.0, 130.0, levels)], ["daily", "4h"])

    def test_an_entry_is_marked_by_its_zone_or_by_its_entry_price(self):
        levels = [self.lv(), self.lv(index=1, low=110.0, high=120.0, frame="4h", kind="resistance")]
        r = {"zone": {"low": 99.5, "high": 100.5, "t": 0}, "entry": 115.0, "side": "long", "trigger": "FVG", "sweep_t": 0,
             "gap": (1, 2), "entry_t": 0, "stop": 1, "target": 2, "rr": 3.0}
        row = dash.signal_row(r, levels)
        self.assertEqual([(h["index"], h["via"]) for h in row["levels"]], [(0, "zone"), (1, "entry")])
        self.assertEqual(dash.signal_row(dict(r, entry=105.0), levels)["levels"][0]["via"], "zone")
        self.assertEqual(dash.signal_row(dict(r, entry=105.0, zone={"low": 50.0, "high": 51.0, "t": 0}), levels)["levels"], [])

    def test_where_price_is_now_compared_with_each_level(self):
        got = dash.level_dicts([self.lv(), self.lv(low=110.0, high=120.0), self.lv(low=80.0, high=90.0)], 100.0)
        self.assertEqual([g["where"] for g in got], ["inside", "below", "above"])
        self.assertNotIn("where", dash.level_dicts([self.lv()], None)[0])


class Launcher(unittest.TestCase):
    def test_the_double_click_launcher_starts_the_dashboard_from_the_repo_folder(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trader-zones", "RiskDesk.bat")
        with open(path, newline="") as fh:
            text = fh.read()
        self.assertIn('cd /d "%~dp0.."', text)
        self.assertIn("py trader-zones\\dashboard.py", text)
        self.assertIn("\r\n", text)                                   # Windows line endings

class Server(Patched):
    def setUp(self):
        Patched.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "config.json")
        self.mine_file = os.path.join(self.tmp.name, "my_zones.json")
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

    def mine(self, **kw):
        body = {"symbol": "MGC", "kind": "demand", "low": "99.0", "high": "99.8", "when": "2026-10-26 11:50"}
        body.update(kw)
        return body

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

    def test_a_kept_gap_expires_so_an_old_typed_price_is_never_reused(self):
        _, _, body = self.get("/")
        self.assertIn("GAP_MAX_MS", body)
        self.assertIn("Date.now()-kept.at<GAP_MAX_MS", body)
        self.assertIn("at:Date.now()", body)                   # the time the price was typed is kept with the gap
        self.assertIn("is out of date and is not applied", body)

    def test_the_page_has_a_zone_rule_choice_the_server_understands(self):
        _, _, body = self.get("/")
        self.assertIn('id="rule"', body)
        self.assertIn('"&rule="', body)
        self.assertIn('"zd.rule"', body)
        self.assertIn('<option value="both" selected>', body)         # the page starts on all the zones
        code, _, js = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00&rule=before")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(js)["rule"], "before")
        code, _, js = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(json.loads(js)["rule"], "standard")
        code, _, js = self.get("/api/snapshot?symbol=MGC&rule=nonsense")
        self.assertEqual(code, 400)
        self.assertIn("rule must be", json.loads(js)["error"])

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

    def test_the_page_can_hide_failed_zones_and_remembers_the_choice(self):
        _, _, body = self.get("/")
        self.assertIn('id="respect"', body)
        self.assertIn('<option value="hide" selected>not failed</option>', body)    # failed zones hidden unless asked
        self.assertIn('if(x.tf==="mine")return true', body)                          # your own boxes are never hidden
        self.assertIn('if(mode==="hide")return x.respect!=="failed"', body)
        self.assertIn('if(mode==="held")return x.respect==="respected"', body)
        self.assertIn("Respected?", body)
        self.assertIn('store("zd.respect"', body)
        self.assertIn("var zs=visibleZones(d)", body)                               # the picture follows the same filter

    def test_the_page_has_a_frame_filter_that_the_picture_and_table_both_follow(self):
        _, _, body = self.get("/")
        for f in ("60m", "30m", "15m"):
            self.assertIn('data-frame="%s"' % f, body)
        self.assertIn('if(frames[x.tf]===false)return false', body)
        self.assertIn('store("zd.frames"', body)
        self.assertIn("var zs=visibleZones(d)", body)
        self.assertIn("var vz=visibleZones(d)", body)

    def test_the_page_lists_every_visit_to_a_zone_with_its_session(self):
        _, _, body = self.get("/")
        self.assertIn("Visits", body)
        self.assertIn('var SESSION_TEXT={asia:"Asia",london:"London",ny:"NY"}', body)
        self.assertIn("touchCell(x,d)", body)

    def test_the_page_has_zones_trades_and_journal_sections_and_a_dark_and_light_theme(self):
        _, _, body = self.get("/")
        for t in ("zones", "trades", "journal"):
            self.assertIn('data-tab="%s"' % t, body)
            self.assertIn('id="tab-%s"' % t, body)
        self.assertIn('id="theme"', body)
        self.assertIn('store("zd.theme"', body)
        self.assertIn(':root[data-theme="light"]', body)
        self.assertIn("<title>RiskDesk.ai</title>", body)                           # what the dashboard is called
        self.assertIn('<span class="dot"></span>RiskDesk.ai</div>', body)

    def test_the_trades_tab_has_position_size_and_risk_reward_and_the_micro_gold_preset(self):
        _, _, body = self.get("/")
        for words in ('id="ps-inst"', 'id="ps-out"', 'id="rr-out"', "Position size", "Risk / reward",
                      'id:"MGC"', 'id:"NQ"', 'id:"XAUUSD"', "Breakeven win rate", "Size this trade"):
            self.assertIn(words, body)
        self.assertIn("Math.floor(raw/step+1e-9)*step", body)                       # size rounds down, never up

    def test_the_journal_keeps_riskdesks_format_and_guards_the_csv_and_the_restore(self):
        _, _, body = self.get("/")
        self.assertIn('var JKEY="riskdesk_journal_v1"', body)                       # a RiskDesk backup restores here
        self.assertIn("Log in journal", body)
        self.assertIn(r"/^[=+\-@\t\r]/.test(s)", body)                              # no spreadsheet formulas from notes
        self.assertIn("data.length>5000", body)
        self.assertIn("cleanTrade", body)

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


class Mine(Server):
    def test_adding_a_zone_saves_it_and_the_next_snapshot_checks_it(self):
        code, doc = self.post("/api/mine/add", self.mine(note="my box"))
        self.assertEqual(code, 200)
        self.assertEqual(doc["mine"][0]["low"], 99.0)
        self.assertEqual(doc["mine"][0]["index"], 0)
        with open(self.mine_file, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["MGC"][0], {"kind": "demand", "low": 99.0, "high": 99.8, "when": "2026-10-26 11:50",
                                           "note": "my box"})
        _, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        snap = json.loads(body)
        self.assertEqual(len(snap["mine"]), 1)
        mine_rows = [r for r in snap["zones"] if r["tf"] == "mine"]
        self.assertEqual(len(mine_rows), 1)
        self.assertEqual((mine_rows[0]["kind"], mine_rows[0]["low"], mine_rows[0]["high"]), ("demand", 99.0, 99.8))
        self.assertEqual(mine_rows[0]["status"], "touched")
        # the user's zone is run through the same entry logic: it sees the trade the tool's own zone sees
        seen = [sig["zone"]["tf"] for sig in snap["signals"]] + [o["tf"] for sig in snap["signals"] for o in sig["also"]]
        self.assertIn("mine", seen)

    def test_saved_zones_belong_to_their_market_and_survive_a_restart(self):
        self.post("/api/mine/add", self.mine())
        _, _, other = self.get("/api/snapshot?symbol=NQ&now=2026-10-26+20:00")
        self.assertEqual(json.loads(other)["mine"], [])
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.server = dash.make_server(0, self.config)                      # a new server, the same folder
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        _, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(len(json.loads(body)["mine"]), 1)

    def test_removing_a_zone_takes_it_out_of_the_file(self):
        self.post("/api/mine/add", self.mine())
        self.post("/api/mine/add", self.mine(kind="supply", low="104", high="106"))
        code, doc = self.post("/api/mine/remove", {"symbol": "MGC", "index": 0})
        self.assertEqual(code, 200)
        self.assertEqual([m["kind"] for m in doc["mine"]], ["supply"])
        self.assertEqual(doc["mine"][0]["index"], 0)
        with open(self.mine_file, encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["MGC"]), 1)
        self.assertFalse(os.path.exists(self.mine_file + ".tmp"))           # the temporary copy is not left behind

    def test_bad_zones_and_bad_removals_get_a_plain_error(self):
        cases = [(self.mine(kind="middle"), 'kind must be "demand" or "supply"'),
                 (self.mine(kind=5), 'kind must be "demand" or "supply"'),
                 (self.mine(low="abc"), "wants two prices"),
                 (self.mine(low="nan"), "wants two prices"),
                 (self.mine(high="inf"), "wants two prices"),
                 (self.mine(low=None), "wants two prices"),
                 (self.mine(low="100", high="99"), "low price first"),
                 (self.mine(when="yesterday"), "wants Dublin time"),
                 (self.mine(when=None), "wants Dublin time"),
                 (self.mine(symbol="DOGE"), "unknown market")]
        for body, words in cases:
            code, doc = self.post("/api/mine/add", body)
            self.assertEqual(code, 400, body)
            self.assertIn(words, doc["error"], body)
            self.assertNotIn("--zone", doc["error"], body)                   # these words belong to the command line
        self.assertFalse(os.path.exists(self.mine_file))                     # nothing was saved
        self.post("/api/mine/add", self.mine())
        for index in (1, -1, "0", True, None):
            code, doc = self.post("/api/mine/remove", {"symbol": "MGC", "index": index})
            self.assertEqual(code, 400, index)
            self.assertIn("no saved zone", doc["error"])

    def test_the_same_zone_cannot_be_added_twice(self):
        self.assertEqual(self.post("/api/mine/add", self.mine())[0], 200)
        code, doc = self.post("/api/mine/add", self.mine())
        self.assertEqual(code, 400)
        self.assertIn("already in your list", doc["error"])

    def test_there_is_a_cap_on_how_many_zones_one_market_can_hold(self):
        with open(self.mine_file, "w", encoding="utf-8") as fh:
            json.dump({"MGC": [dict(self.mine(), low=float(i), high=i + 0.5) for i in range(dash.MAX_MINE)]}, fh)
        code, doc = self.post("/api/mine/add", self.mine(low="1000", high="1001"))
        self.assertEqual(code, 400)
        self.assertIn("remove some first", doc["error"])

    def test_a_broken_saved_zone_is_skipped_with_a_note_and_the_rest_still_work(self):
        with open(self.mine_file, "w", encoding="utf-8") as fh:
            json.dump({"MGC": [{"kind": "demand", "low": "x", "high": 99.8, "when": "2026-10-26 11:50"}, "junk",
                               dict(self.mine(), low=99.0)]}, fh)
        code, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(code, 200)
        snap = json.loads(body)
        self.assertEqual(len(snap["mine"]), 3)
        self.assertEqual(len([r for r in snap["zones"] if r["tf"] == "mine"]), 1)
        self.assertTrue(any("saved zone number 1 was skipped" in n for n in snap["notes"]))
        self.assertTrue(any("saved zone number 2 was skipped" in n for n in snap["notes"]))

    def test_a_corrupt_file_is_a_plain_error_not_a_crash(self):
        with open(self.mine_file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        code, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(code, 400)
        self.assertIn("is not valid JSON", json.loads(body)["error"])
        code, doc = self.post("/api/mine/add", self.mine())
        self.assertEqual(code, 400)
        with open(self.mine_file, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{not json")                         # not overwritten

    def test_a_file_that_is_not_an_object_is_a_plain_error(self):
        with open(self.mine_file, "w", encoding="utf-8") as fh:
            fh.write("[1, 2]")
        code, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(code, 400)
        self.assertIn("must hold an object", json.loads(body)["error"])

    def test_posts_need_the_dashboard_header_so_other_websites_cannot_write(self):
        req = urllib.request.Request("http://127.0.0.1:%d/api/mine/add" % self.port,
                                     data=json.dumps(self.mine()).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)
        self.assertFalse(os.path.exists(self.mine_file))
        code, _ = self.post("/api/mine/add", self.mine(), headers={"X-Requested-With": "something-else"})
        self.assertEqual(code, 403)

    def test_requests_addressed_to_another_host_name_are_refused(self):
        for method, path, body in (("GET", "/", None), ("GET", "/api/markets", None),
                                   ("POST", "/api/mine/add", json.dumps(self.mine()))):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", "evil.example:%d" % self.port)
            conn.putheader("X-Requested-With", "zones-dashboard")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body or "")))
            conn.endheaders(body.encode("utf-8") if body else None)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 403, (method, path))
            conn.close()
        self.assertFalse(os.path.exists(self.mine_file))
        code, _, _ = self.get("/api/markets")                               # the real name still works
        self.assertEqual(code, 200)
        conn = http.client.HTTPConnection("localhost", self.port, timeout=10)
        conn.request("GET", "/api/markets")
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_bad_post_bodies_are_plain_errors(self):
        self.assertEqual(self.post("/api/mine/add", None, raw=b"not json")[0], 400)
        self.assertEqual(self.post("/api/mine/add", None, raw=b"[1]")[0], 400)
        self.assertEqual(self.post("/api/mine/add", None, raw=b"")[0], 400)
        self.assertEqual(self.post("/api/mine/add", None, raw=b"x" * 20001)[0], 400)
        self.assertEqual(self.post("/api/mine/nope", {})[0], 404)
        self.assertEqual(self.get("/api/markets")[0], 200)                  # still serving

    def test_the_page_has_the_my_zones_form_and_its_own_lane_on_the_ladder(self):
        _, _, body = self.get("/")
        self.assertIn("My zones (", body)
        self.assertIn('"/api/mine/add"', body)
        self.assertIn('"/api/mine/remove"', body)
        self.assertIn('"X-Requested-With":"zones-dashboard"', body)
        self.assertIn('lanes.push("mine")', body)
        self.assertNotIn("innerHTML", body)


class Levels(Server):
    def level(self, **kw):
        body = {"symbol": "MGC", "kind": "support", "frame": "daily", "low": "98.5", "high": "100.5", "note": "big one"}
        body.update(kw)
        return body

    def test_adding_a_level_saves_it_and_the_next_snapshot_marks_the_zone_and_the_entry(self):
        code, doc = self.post("/api/levels/add", self.level())
        self.assertEqual(code, 200)
        self.assertEqual((doc["levels"][0]["frame"], doc["levels"][0]["low"], doc["levels"][0]["index"]),
                         ("daily", 98.5, 0))
        saved = os.path.join(self.tmp.name, "my_levels.json")
        with open(saved, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["MGC"][0]["note"], "big one")
        _, _, body = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        snap = json.loads(body)
        self.assertEqual(len(snap["levels"]), 1)
        self.assertIn(snap["levels"][0]["where"], ("inside", "above", "below"))
        zr = snap["zones"][0]
        self.assertEqual((zr["low"], zr["high"]), (99.0, 99.8))
        self.assertEqual([lv["frame"] for lv in zr["levels"]], ["daily"])
        self.assertEqual([lv["via"] for lv in snap["signals"][0]["levels"]][:1], ["zone"])

    def test_levels_belong_to_their_market_and_can_be_removed(self):
        self.post("/api/levels/add", self.level())
        self.post("/api/levels/add", self.level(low="110", high="120", kind="resistance", frame="4h"))
        _, _, other = self.get("/api/snapshot?symbol=NQ&now=2026-10-26+20:00")
        self.assertEqual(json.loads(other)["levels"], [])
        code, doc = self.post("/api/levels/remove", {"symbol": "MGC", "index": 0})
        self.assertEqual(code, 200)
        self.assertEqual([lv["frame"] for lv in doc["levels"]], ["4h"])
        code, doc = self.post("/api/levels/remove", {"symbol": "MGC", "index": 5})
        self.assertEqual(code, 400)
        self.assertIn("no saved level number 5", doc["error"])

    def test_a_bad_level_is_refused_with_a_plain_message(self):
        for bad, text in ((dict(kind="floor"), "support"), (dict(frame="weekly"), '"4h" or "daily"'),
                          (dict(low="abc"), "two prices"), (dict(low="101", high="100"), "under the high"),
                          (dict(low="nan"), "two prices")):
            code, doc = self.post("/api/levels/add", self.level(**bad))
            self.assertEqual(code, 400, bad)
            self.assertIn(text, doc["error"])
        self.post("/api/levels/add", self.level())
        code, doc = self.post("/api/levels/add", self.level())
        self.assertEqual((code, doc["error"]), (400, "that level is already in your list"))

    def test_posting_levels_needs_the_same_guards_as_my_zones(self):
        code, _ = self.post("/api/levels/add", self.level(), headers={"X-Requested-With": "nope"})
        self.assertEqual(code, 403)

    def test_the_page_has_a_levels_card_tags_and_ladder_bands(self):
        _, _, body = self.get("/")
        for needle in ("levelsCard(d)", "/api/levels/add", "/api/levels/remove", "frameCell(x,d)", "At your ",
                       "your daily and 4h levels"):
            self.assertIn(needle, body)


class EntryStyle(Server):
    def test_the_page_has_an_entry_style_choice_that_starts_on_the_users_own(self):
        _, _, body = self.get("/")
        self.assertIn('id="entry"', body)
        self.assertIn('<option value="yours" selected>', body)
        self.assertIn('"zd.entry"', body)
        self.assertIn('"&entry="', body)
        self.assertIn("price reached the fib", body)
        self.assertIn("0.705 is at", body)
        self.assertIn("Entry on the inversion of the gap", body)         # one card per gap, with its retest inside it
        self.assertIn("Entry on the retest of the gap", body)
        self.assertIn("frames[r.zone.tf]!==false", body)               # the Frames buttons filter entries too

    def test_the_server_takes_the_entry_style_and_says_which_it_used(self):
        _, _, js = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00&entry=yours")
        self.assertEqual(json.loads(js)["style"], "yours")
        _, _, js = self.get("/api/snapshot?symbol=MGC&now=2026-10-26+20:00")
        self.assertEqual(json.loads(js)["style"], "tool")
        code, _, js = self.get("/api/snapshot?symbol=MGC&entry=other")
        self.assertEqual(code, 400)
        self.assertIn('entry must be "tool" or "yours"', json.loads(js)["error"])


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

    def test_address_reuse_is_off_on_windows_where_it_lets_two_servers_share_a_port(self):
        self.assertEqual(dash.DashboardServer.allow_reuse_address, os.name != "nt")


if __name__ == "__main__":
    unittest.main()
