"""trader-zones: supply and demand zones and the 5m/3m entry. Offline, on small synthetic bars
built by hand so every expected number can be checked on paper."""

import contextlib
import datetime
import io
import json
import math
import os
import tempfile
import unittest

from _load import load

z = load("trader-zones", "zones")
CFG = z.read_config(None)
NY_CFG = dict(CFG, session_tz="ny", sessions=dict(z.NY_SESSIONS))   # the short New York kill zones


def et(y, mo, d, h=0, mi=0):
    return z.eastern_to_ts(datetime.datetime(y, mo, d, h, mi))


def mk(t0, step, rows):
    return [{"t": t0 + step * i, "o": o, "h": h, "l": l, "c": c} for i, (o, h, l, c) in enumerate(rows)]


def noise(n, base=100.0, amp=0.6):
    """A calm wave around `base`: small bodies, small wicks, real swing highs and lows."""
    rows, prev = [], base
    for i in range(n):
        c = base + amp * math.sin(i * 0.8)
        rows.append((prev, max(prev, c) + 0.25, min(prev, c) - 0.25, c))
        prev = c
    return rows


T0 = et(2026, 10, 26, 7, 0)  # Mon Oct 26 2026, 7:00 AM New York

# --- a demand zone on 30-minute bars: calm, one long-wicked small candle, one big up candle ---
ZONE_CANDLE = (100.0, 100.8, 99.2, 100.05)
DISPLACEMENT = (100.1, 104.6, 100.0, 104.5)
AFTER_UP = [(104.5, 105.0, 104.4, 104.9), (104.9, 105.4, 104.8, 105.3), (105.3, 105.8, 105.2, 105.7)]


# find_zones skips its first 20 bars (ATR and swing warm-up), so the break must come after that
PRE = 22
ZSTART = et(2026, 10, 25, 20, 0)   # Sunday 8 PM New York: bar 22 (the zone candle) opens Monday 7:00 AM
SOLID = (100.0, 100.9, 99.95, 100.85)   # big body, tiny wicks: never a zone candle


def zone_rows(extra=()):
    return noise(PRE) + [ZONE_CANDLE, DISPLACEMENT] + AFTER_UP + list(extra)


def swing_rows(high):
    """The latest swing high before the zone candle is `high`; later highs are all lower."""
    rows = noise(PRE)
    rows[18] = (100.0, high, 99.8, 100.1)
    rows[19] = (99.7, 99.9, 99.4, 99.6)
    rows[20] = (99.6, 99.8, 99.3, 99.5)
    rows[21] = (99.5, 99.7, 99.2, 99.4)
    return rows + [ZONE_CANDLE, DISPLACEMENT] + AFTER_UP


# --- a full long entry on 5-minute and 3-minute bars (hand-built, see the comments) ---
M5_ROWS = noise(16) + [
    (99.6, 100.0, 98.6, 99.9),      # 16 the sweep: wick under the swing low, close back above it
    (99.9, 103.0, 99.8, 102.9),     # 17
    (100.6, 106.0, 100.5, 105.9),   # 18
    (105.9, 110.0, 102.0, 109.8),   # 19 leg high 110.0
    (109.8, 110.0, 107.0, 107.2),   # 20
    (107.2, 107.3, 103.0, 103.2),   # 21
    (101.5, 101.6, 101.0, 101.2),   # 22 first bar of the gap
    (101.3, 103.1, 101.2, 103.0),   # 23
    (102.9, 103.5, 102.4, 103.2),   # 24 gap 101.6 to 102.4, midpoint 102.0
    (103.2, 103.3, 102.3, 102.5),   # 25 price trades back into the gap
    (102.5, 104.0, 102.4, 103.9),
    (103.9, 105.0, 103.8, 104.9),
]
M3_ROWS = [(100.4 + 0.1 * (i % 3), 100.9, 100.1, 100.5) for i in range(27)] + [
    (100.2, 100.6, 98.8, 100.4),    # 27
    (100.4, 103.0, 100.3, 102.9),   # 28
    (102.9, 106.0, 102.8, 105.9),   # 29
    (105.9, 110.0, 105.8, 109.8),   # 30 the high of the leg
    (109.8, 109.9, 107.0, 107.2),   # 31
    (107.2, 107.3, 104.0, 104.2),   # 32
    (104.2, 104.3, 102.0, 102.2),   # 33
    (102.2, 103.9, 102.0, 103.8),   # 34
    (103.8, 104.6, 103.5, 104.4),   # 35 the lower high, 104.6
    (104.4, 104.5, 103.0, 103.2),   # 36
    (103.2, 103.4, 102.2, 102.4),   # 37
    (102.4, 102.8, 101.8, 102.0),   # 38
    (102.0, 102.5, 101.4, 101.6),   # 39
    (101.6, 102.4, 101.0, 102.2),   # 40
    (102.2, 102.6, 101.9, 102.4),   # 41
    (102.4, 104.0, 102.3, 103.9),   # 42
    (103.9, 105.2, 103.8, 105.0),   # 43 closes above 104.6: the CHoCH
    (105.0, 105.5, 104.5, 105.3),
    (105.3, 105.6, 104.8, 105.1),
]


def entry_data(t0=T0):
    m5, m3 = mk(t0, 300, M5_ROWS), mk(t0, 180, M3_ROWS)
    zone = {"kind": "demand", "t": m5[10]["t"], "high": 99.8, "low": 99.0, "touched_t": m5[16]["t"],
            "status": "touched", "di": 5, "tf": "30m"}
    targets = [p[2] for p in z.pivots(m5, 2)[0]]
    return m5, m3, zone, targets


def chart_json(bars):
    return json.dumps({"chart": {"result": [{
        "timestamp": [b["t"] for b in bars],
        "indicators": {"quote": [{"open": [b["o"] for b in bars], "high": [b["h"] for b in bars],
                                  "low": [b["l"] for b in bars], "close": [b["c"] for b in bars]}]}}],
        "error": None}})


class Markets(unittest.TestCase):
    def test_the_five_markets_include_mgc(self):
        self.assertEqual(sorted(z.MARKETS), ["EURUSD", "GBPUSD", "MGC", "NQ", "XAUUSD"])
        self.assertEqual(z.MARKETS["MGC"]["yahoo"], "MGC=F")
        self.assertEqual(z.MARKETS["NQ"]["yahoo"], "NQ=F")
        self.assertEqual(z.MARKETS["GBPUSD"]["yahoo"], "GBPUSD=X")

    def test_aliases_and_case(self):
        self.assertEqual(z.resolve("nas100"), "NQ")
        self.assertEqual(z.resolve("US100"), "NQ")
        self.assertEqual(z.resolve("GBP/USD"), "GBPUSD")
        self.assertEqual(z.resolve("gold"), "XAUUSD")
        self.assertEqual(z.resolve("mgc"), "MGC")

    def test_unknown_market_is_an_error_not_a_guess(self):
        with self.assertRaises(z.ZoneError):
            z.resolve("TSLA")

    def test_markets_command_lists_all_five(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(z.main(["markets"]), 0)
        for name in ("XAUUSD", "GBPUSD", "EURUSD", "NQ", "MGC"):
            self.assertIn(name, out.getvalue())
        self.assertIn("COMEX gold futures", out.getvalue())  # says gold is futures, not spot


class Time(unittest.TestCase):
    def test_new_york_daylight_saving_edges(self):
        d = datetime.datetime
        self.assertEqual(z.eastern_offset(d(2026, 7, 1, 12)), -4)
        self.assertEqual(z.eastern_offset(d(2026, 12, 1, 12)), -5)
        self.assertEqual(z.eastern_offset(d(2026, 3, 8, 6, 59)), -5)   # the minute before spring forward
        self.assertEqual(z.eastern_offset(d(2026, 3, 8, 7, 0)), -4)
        self.assertEqual(z.eastern_offset(d(2026, 11, 1, 5, 59)), -4)  # the minute before fall back
        self.assertEqual(z.eastern_offset(d(2026, 11, 1, 6, 0)), -5)

    def test_now_in_new_york_time_round_trips(self):
        ts = z.parse_now("2026-10-26 08:15")
        self.assertEqual(z.fmt_et(ts), "Mon 10-26 08:15")
        self.assertEqual(z.utc_of(ts), datetime.datetime(2026, 10, 26, 12, 15))  # EDT is UTC-4
        with self.assertRaises(z.ZoneError):
            z.parse_now("26/10/2026 8am")

    def test_new_york_kill_zone_windows(self):
        for h, mi, want in ((2, 30, True), (4, 59, True), (5, 0, False), (7, 0, True), (9, 59, True),
                            (10, 0, False), (12, 0, False), (19, 0, True), (23, 30, True), (0, 0, False)):
            self.assertEqual(z.in_session(et(2026, 10, 26, h, mi), NY_CFG), want, "%d:%02d" % (h, mi))

    def utc(self, y, mo, d, h=0, mi=0):
        return int((datetime.datetime(y, mo, d, h, mi) - z.EPOCH).total_seconds())

    def test_the_default_sessions_are_dublin_time(self):
        self.assertEqual(CFG["session_tz"], "dublin")
        # 26 Oct 2026 is winter time in Ireland (UTC+0): UTC clock = Dublin clock
        for h, mi, want in ((0, 0, "asia"), (4, 30, "asia"), (7, 59, "asia"), (8, 0, "london"), (12, 59, "london"),
                            (13, 0, "london"), (15, 59, "london"), (16, 0, "ny"), (20, 59, "ny"),
                            (21, 0, None), (23, 59, None)):
            self.assertEqual(z.session_of(self.utc(2026, 10, 26, h, mi), CFG), want, "%d:%02d" % (h, mi))

    def test_a_zone_at_0430_utc_is_in_the_asia_window(self):
        # the 00:30 New York candle on Wed 28 Oct 2026 is 04:30 Dublin time
        self.assertTrue(z.in_session(et(2026, 10, 28, 0, 30), CFG))
        self.assertFalse(z.in_session(et(2026, 10, 28, 0, 30), NY_CFG))

    def test_irish_summer_time_moves_the_window_an_hour(self):
        # 1 July 2026: Dublin is UTC+1, so 23:30 UTC is 00:30 the next day, inside Asia
        self.assertTrue(z.in_session(self.utc(2026, 7, 1, 23, 30), CFG))
        # 1 December 2026: Dublin is UTC+0, so 23:30 UTC is 23:30, in no window
        self.assertFalse(z.in_session(self.utc(2026, 12, 1, 23, 30), CFG))

    def test_irish_clock_change_edges(self):
        self.assertEqual(z.dublin_offset(datetime.datetime(2026, 3, 29, 0, 59)), 0)
        self.assertEqual(z.dublin_offset(datetime.datetime(2026, 3, 29, 1, 0)), 1)
        self.assertEqual(z.dublin_offset(datetime.datetime(2026, 10, 25, 0, 59)), 1)
        self.assertEqual(z.dublin_offset(datetime.datetime(2026, 10, 25, 1, 0)), 0)

    def test_dublin_time_in_a_zone_line(self):
        ts = et(2026, 10, 28, 2, 0)
        self.assertEqual(z.fmt_dublin(ts, ts), "06:00")
        late = et(2026, 10, 27, 21, 0)      # 01:00 on Wed in Dublin: the weekday is shown
        self.assertEqual(z.fmt_dublin(late, late), "Wed 01:00")


class Bars(unittest.TestCase):
    def test_resample_one_minute_to_three(self):
        base = T0 - T0 % 180
        m1 = mk(base, 60, [(1, 2, 0.5, 1.5), (1.5, 3, 1, 2), (2, 2.5, 1.8, 2.2), (2.2, 2.4, 2.0, 2.1)])
        m3 = z.resample(m1, 180)
        self.assertEqual(len(m3), 2)
        self.assertEqual((m3[0]["o"], m3[0]["h"], m3[0]["l"], m3[0]["c"]), (1, 3, 0.5, 2.2))
        self.assertEqual(m3[1]["t"], base + 180)

    def test_a_bar_counts_only_once_it_has_closed(self):
        bars = mk(T0, 300, [(1, 2, 0, 1)] * 3)
        self.assertEqual(len(z.closed_only(bars, 300, T0 + 599)), 1)
        self.assertEqual(len(z.closed_only(bars, 300, T0 + 600)), 2)

    def test_swings_are_known_only_after_they_confirm(self):
        rows = [(1, 1, 0.5, 1)] * 3 + [(1, 5, 0.5, 1)] + [(1, 1, 0.5, 1)] * 4
        ph, _ = z.pivots(mk(T0, 60, rows), 2)
        self.assertEqual(ph[0][1], 3)   # the swing is bar 3
        self.assertEqual(ph[0][0], 5)   # and is only known at bar 5
        known = z.last_known(ph, len(rows))
        self.assertIsNone(known[4])
        self.assertEqual(known[5], 5)

    def test_parse_chart_skips_null_closes_and_sorts(self):
        text = json.dumps({"chart": {"result": [{"timestamp": [120, 60, 180], "indicators": {"quote": [{
            "open": [1, 1, 1], "high": [2, 2, 2], "low": [0, 0, 0], "close": [1.5, None, 1.6]}]}}]}})
        bars = z.parse_chart(text, "NQ")
        self.assertEqual([b["t"] for b in bars], [120, 180])

    def test_ote_band_is_62_to_79_percent_back_from_the_high(self):
        lo, hi = z.ote_band(100.0, 110.0)
        self.assertAlmostEqual(lo, 102.1)
        self.assertAlmostEqual(hi, 103.8)

    def test_flip_swaps_highs_and_lows(self):
        f = z.flip_bars([{"t": 1, "o": 10, "h": 12, "l": 9, "c": 11}])[0]
        self.assertEqual((f["o"], f["h"], f["l"], f["c"]), (-10, -9, -12, -11))


class FindZones(unittest.TestCase):
    def find(self, rows, **over):
        cfg = dict(CFG)
        cfg.update(over)
        return z.find_zones(mk(ZSTART, 1800, rows), cfg)

    # the slow-move rule is switched off (away_bars=0) in the tests that isolate the single-candle rule
    ONE_CANDLE = {"away_bars": 0}

    def test_a_long_wicked_small_candle_before_a_strong_break_is_a_demand_zone(self):
        zones = self.find(zone_rows())
        self.assertEqual(len(zones), 1)
        got = zones[0]
        self.assertEqual(got["kind"], "demand")
        self.assertEqual((got["low"], got["high"]), (99.2, 100.8))  # the one candle, high to low
        self.assertEqual(got["status"], "fresh")
        self.assertEqual(got["t"], ZSTART + 1800 * PRE)
        self.assertEqual(z.fmt_et(got["t"]), "Mon 10-26 07:00")

    def test_the_zone_is_touched_once_price_trades_back_into_it(self):
        got = self.find(zone_rows(extra=[(105.7, 105.8, 100.5, 101.0)]))[0]
        self.assertEqual(got["status"], "touched")
        self.assertEqual(got["touched_t"], ZSTART + 1800 * (PRE + 5))

    def test_a_dip_that_stays_above_the_zone_leaves_it_fresh(self):
        got = self.find(zone_rows(extra=[(105.7, 105.8, 101.0, 101.5)]))[0]
        self.assertEqual(got["status"], "fresh")

    def test_a_supply_zone_is_the_mirror_image(self):
        got = z.find_zones(z.flip_bars(mk(ZSTART, 1800, zone_rows())), CFG)
        self.assertEqual([g["kind"] for g in got], ["supply"])
        self.assertEqual((got[0]["low"], got[0]["high"]), (-100.8, -99.2))

    def test_the_latest_qualifying_candle_in_the_lookback_is_the_zone(self):
        got = self.find(noise(PRE - 3) + [ZONE_CANDLE, SOLID, SOLID, DISPLACEMENT] + AFTER_UP)
        self.assertEqual([(g["low"], g["high"]) for g in got], [(99.2, 100.8)])

    def test_a_candidate_six_bars_back_is_outside_the_lookback(self):
        self.assertEqual(len(self.find(noise(PRE - 6) + [ZONE_CANDLE] + [SOLID] * 5 + [DISPLACEMENT] + AFTER_UP)), 0)

    def test_big_bodied_candles_before_the_break_are_not_a_zone(self):
        self.assertEqual(len(self.find(noise(PRE - 5) + [SOLID] * 5 + [DISPLACEMENT] + AFTER_UP)), 0)

    def test_a_weak_move_away_is_not_a_zone(self):
        # a displacement body that clears 1.5 x ATR but only travels 1.85 from the zone, under 2 x ATR
        weak = (100.8, 102.65, 100.75, 102.6)
        self.assertEqual(len(self.find(noise(PRE) + [ZONE_CANDLE, weak] + AFTER_UP[:1], **self.ONE_CANDLE)), 0)

    def test_a_break_must_close_beyond_the_latest_swing_high(self):
        self.assertEqual(len(self.find(swing_rows(104.4), **self.ONE_CANDLE)), 1)   # 104.4 is under the 104.5 close: a break
        self.assertEqual(len(self.find(swing_rows(106.0), **self.ONE_CANDLE)), 0)   # 106.0 is above it: no break

    def test_nothing_is_read_before_the_warm_up(self):
        # the same zone shape but with the break inside the first 20 bars: skipped, on purpose
        early = noise(16) + [ZONE_CANDLE, DISPLACEMENT] + AFTER_UP
        self.assertEqual(len(self.find(early)), 0)

    # --- a slow move away: no single big candle ---
    DRIFT = [(100.0, 100.7, 99.9, 100.55), (100.55, 101.2, 100.45, 101.05), (101.05, 101.7, 100.95, 101.55),
             (101.55, 102.2, 101.45, 102.05), (102.05, 102.7, 101.95, 102.55), (102.55, 103.2, 102.45, 103.05)]

    def test_a_slow_move_away_makes_a_zone_when_the_candle_is_the_bottom(self):
        got = self.find(noise(PRE) + [ZONE_CANDLE] + self.DRIFT)
        self.assertEqual([(g["kind"], g["low"], g["high"]) for g in got], [("demand", 99.2, 100.8)])
        self.assertEqual(got[0]["t"], ZSTART + 1800 * PRE)
        # ATR there is 0.863, so a close of 100.8 + 2 x 0.863 = 102.53 is needed: the 102.55 close, 5 bars on
        self.assertEqual(got[0]["di"], PRE + 5)

    def test_the_single_candle_rule_alone_would_miss_that_slow_move(self):
        self.assertEqual(self.find(noise(PRE) + [ZONE_CANDLE] + self.DRIFT, **self.ONE_CANDLE), [])

    def test_a_slow_move_that_stalls_is_not_a_zone(self):
        stall = [(100.0, 100.7, 99.9, 100.55)] * 6
        self.assertEqual(self.find(noise(PRE) + [ZONE_CANDLE] + stall), [])

    def test_a_close_back_through_the_far_edge_cancels_a_slow_move(self):
        # one bar closes at 99.19, just under the zone low 99.2 (its own low, 99.15, is inside the
        # area tolerance, so the only thing that can stop the zone is that close)
        broken = [(100.0, 100.4, 99.15, 99.19)] + self.DRIFT
        self.assertEqual(self.find(noise(PRE) + [ZONE_CANDLE] + broken, away_bars=8), [])
        # the same bar closing at 99.3, just inside the zone, leaves the slow move alone
        inside = [(100.0, 100.4, 99.25, 99.3)] + self.DRIFT
        self.assertEqual(len(self.find(noise(PRE) + [ZONE_CANDLE] + inside, away_bars=8)), 1)

    def test_a_slow_move_cancels_when_price_trades_well_beyond_the_candle_first(self):
        # a wick to 98.6 (0.6 under the zone low 99.2) before the move is confirmed: no zone
        wick = [(100.0, 100.4, 98.6, 99.4)]
        got = self.find(noise(PRE) + [ZONE_CANDLE] + wick + self.DRIFT)
        self.assertNotIn(ZSTART + 1800 * PRE, [g["t"] for g in got])
        # a wick to 99.25 stays inside the candle: the zone stands
        calm = [(100.0, 100.4, 99.25, 99.4)]
        got = self.find(noise(PRE) + [ZONE_CANDLE] + calm + self.DRIFT)
        self.assertIn(ZSTART + 1800 * PRE, [g["t"] for g in got])

    def test_a_dip_a_hair_under_the_candle_is_inside_the_tolerance(self):
        # a bar after it dips to 99.15, 0.05 under the zone low 99.2 (the tolerance is 0.1 x ATR = 0.086)
        near = [(100.0, 100.4, 99.15, 99.4)]
        got = self.find(noise(PRE) + [ZONE_CANDLE] + near + self.DRIFT)
        self.assertIn(ZSTART + 1800 * PRE, [g["t"] for g in got])
        # a dip to 99.0 is outside it
        far = [(100.0, 100.4, 99.0, 99.4)]
        got = self.find(noise(PRE) + [ZONE_CANDLE] + far + self.DRIFT)
        self.assertNotIn(ZSTART + 1800 * PRE, [g["t"] for g in got])

    def test_what_came_before_the_candle_does_not_matter(self):
        # price was lower two bars before (it rose into the pause): the pause is still a zone
        before = [(98.2, 98.6, 98.0, 98.5), (98.5, 99.0, 98.4, 98.9)]
        got = self.find(noise(PRE - 2) + before + [ZONE_CANDLE] + self.DRIFT)
        self.assertIn(ZSTART + 1800 * PRE, [g["t"] for g in got])

    def test_the_supply_side_has_the_same_extreme_rule(self):
        def supply(rows):
            return z.find_zones(z.flip_bars(mk(ZSTART, 1800, rows)), CFG)
        wick = [(100.0, 100.4, 98.6, 99.4)]        # flipped, a spike 0.6 above the zone's high
        self.assertNotIn(ZSTART + 1800 * PRE, [g["t"] for g in supply(noise(PRE) + [ZONE_CANDLE] + wick + self.DRIFT)])
        calm = [(100.0, 100.4, 99.25, 99.4)]
        self.assertIn(ZSTART + 1800 * PRE, [g["t"] for g in supply(noise(PRE) + [ZONE_CANDLE] + calm + self.DRIFT)])
        # and a supply pause inside a fall: price was higher before it
        before = [(98.2, 98.6, 98.0, 98.5), (98.5, 99.0, 98.4, 98.9)]
        self.assertIn(ZSTART + 1800 * PRE, [g["t"] for g in supply(noise(PRE - 2) + before + [ZONE_CANDLE] + self.DRIFT)])

    def test_one_move_gets_its_first_candle_not_the_one_two_bars_later(self):
        rows = noise(PRE - 2) + [(100.0, 100.2, 98.4, 99.9)] + [(99.9, 100.1, 99.5, 100.0)] + [ZONE_CANDLE] + self.DRIFT
        got = self.find(rows)
        self.assertEqual([(g["low"], g["high"]) for g in got], [(98.4, 100.2)])
        self.assertEqual(got[0]["t"], ZSTART + 1800 * (PRE - 2))

    def test_a_slow_supply_zone_is_the_mirror_image(self):
        rows = noise(PRE) + [ZONE_CANDLE] + self.DRIFT
        got = z.find_zones(z.flip_bars(mk(ZSTART, 1800, rows)), CFG)
        self.assertEqual([(g["kind"], g["low"], g["high"]) for g in got], [("supply", -100.8, -99.2)])

    def test_one_move_gets_one_zone_not_one_per_small_candle(self):
        rows = noise(PRE) + [ZONE_CANDLE, (100.0, 100.8, 99.25, 100.05)] + self.DRIFT
        self.assertEqual(len(self.find(rows)), 1)

    # --- several zone candles before one displacement: the most extreme one is the zone ---
    def test_the_lowest_low_among_several_small_candles_is_the_demand_zone(self):
        second = (100.0, 100.8, 99.3, 100.05)       # closer to the move, but its low is higher
        got = self.find(noise(PRE) + [ZONE_CANDLE, SOLID, second, DISPLACEMENT] + AFTER_UP, **self.ONE_CANDLE)
        self.assertEqual([(g["low"], g["high"]) for g in got], [(99.2, 100.8)])
        self.assertEqual(got[0]["t"], ZSTART + 1800 * PRE)

    def test_the_highest_high_among_several_small_candles_is_the_supply_zone(self):
        second = (100.0, 100.8, 99.3, 100.05)
        rows = noise(PRE) + [ZONE_CANDLE, SOLID, second, DISPLACEMENT] + AFTER_UP
        got = z.find_zones(z.flip_bars(mk(ZSTART, 1800, rows)), dict(CFG, **self.ONE_CANDLE))
        self.assertEqual([(g["kind"], g["low"], g["high"]) for g in got], [("supply", -100.8, -99.2)])

    def test_equal_extremes_take_the_earliest(self):
        twin = (100.0, 100.8, 99.2, 100.05)
        got = self.find(noise(PRE) + [ZONE_CANDLE, twin, DISPLACEMENT] + AFTER_UP, **self.ONE_CANDLE)
        self.assertEqual(got[0]["t"], ZSTART + 1800 * PRE)


class LimitZones(unittest.TestCase):
    NOW = et(2026, 10, 26, 12, 0)

    def zone(self, day, h, mi, di):
        return {"kind": "demand", "t": et(2026, 10, day, h, mi), "di": di, "high": 1.0, "low": 0.0}

    def test_last_two_days_only(self):
        zs = [self.zone(24, 8, 0, 1), self.zone(25, 8, 0, 2), self.zone(26, 8, 0, 3)]
        got = z.limit_zones(zs, self.NOW, CFG)
        self.assertEqual([g["di"] for g in got], [2, 3])

    def test_at_most_three_a_day_the_latest_three(self):
        zs = [self.zone(26, 7, 0, 1), self.zone(26, 7, 30, 2), self.zone(26, 8, 0, 3), self.zone(26, 8, 30, 4)]
        got = z.limit_zones(zs, self.NOW, CFG)
        self.assertEqual([g["di"] for g in got], [2, 3, 4])

    def test_zone_candles_outside_asia_london_new_york_are_dropped(self):
        zs = [self.zone(26, 11, 0, 1), self.zone(26, 8, 0, 2), self.zone(26, 3, 0, 3)]
        got = z.limit_zones(zs, self.NOW, NY_CFG)
        self.assertEqual(sorted(g["di"] for g in got), [2, 3])  # 11:00 New York is in no kill zone

    def test_in_dublin_time_the_dropped_hours_are_21_to_24(self):
        # New York 16:30 = 20:30 Dublin (kept), 17:00 = 21:00 and 19:59 = 23:59 (dropped), 20:00 = 00:00 (kept)
        zs = [self.zone(26, 16, 30, 1), self.zone(26, 17, 0, 2), self.zone(26, 19, 59, 3), self.zone(26, 20, 0, 4)]
        got = z.limit_zones(zs, et(2026, 10, 26, 23, 0), CFG)
        self.assertEqual(sorted(g["di"] for g in got), [1, 4])

    def test_sessions_can_be_switched_off(self):
        cfg = dict(CFG, zones_in_sessions=False)
        got = z.limit_zones([self.zone(26, 11, 0, 1)], self.NOW, cfg)
        self.assertEqual(len(got), 1)


class Entry(unittest.TestCase):
    def test_a_full_long_entry(self):
        m5, m3, zone, targets = entry_data()
        res, best = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual(best, 6)
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual(r["trigger"], "FVG")
        self.assertEqual(r["sweep_low"], 98.6)
        self.assertEqual(r["sweep_t"], m5[16]["t"])
        self.assertEqual(r["gap"], (101.6, 102.4))
        self.assertAlmostEqual(r["ote"][0], 100.994)
        self.assertAlmostEqual(r["ote"][1], 102.932)
        self.assertTrue(r["ote"][0] <= sum(r["gap"]) / 2 <= r["ote"][1])
        self.assertEqual(r["entry"], 105.0)                       # the close of the CHoCH bar
        self.assertEqual(r["entry_t"], m3[43]["t"] + 180)         # known when that bar closed
        self.assertLess(r["stop"], 98.6)                          # under the sweep low
        self.assertEqual(r["target"], 110.0)                      # the swing high of the leg
        self.assertAlmostEqual(r["rr"], (110.0 - 105.0) / (105.0 - r["stop"]))

    def test_a_small_reward_to_risk_drops_the_entry_and_says_why(self):
        m5, m3, zone, targets = entry_data()
        res, best = z.long_setups(zone, m5, m3, targets, CFG)  # min_rr 2
        self.assertEqual(res, [])
        self.assertEqual(best, 5)
        self.assertIn("reward to risk", z.STAGES[best])

    def test_no_entry_when_the_choch_comes_outside_the_sessions(self):
        m5, m3, zone, targets = entry_data(et(2026, 10, 26, 15, 0))     # CHoCH about 21:10 Dublin: in no window
        res, best = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual(res, [])
        self.assertEqual(best, 5)

    def test_no_entry_when_the_choch_comes_outside_the_new_york_kill_zones(self):
        m5, m3, zone, targets = entry_data(et(2026, 10, 26, 12, 0))
        res, best = z.long_setups(zone, m5, m3, targets, dict(NY_CFG, min_rr=0.5))
        self.assertEqual(res, [])
        self.assertEqual(best, 5)

    # --- the 62% to 79% band is measured when price comes back to the gap, not when the gap forms ---
    LEG = [(99.6, 100.0, 98.6, 99.9),       # 16 the sweep
           (99.9, 102.4, 99.8, 102.3),      # 17
           (101.0, 103.0, 100.6, 102.9),    # 18 forms the gap 100.0 to 100.6 (midpoint 100.3)
           (102.9, 106.0, 102.7, 105.9)]    # 19 the leg goes on to 106.0
    BACK = (105.9, 106.0, 100.4, 100.5)     # 20 price comes back into the gap

    def late_leg(self, rows):
        m5 = mk(T0, 300, noise(16) + rows)
        m3 = mk(T0, 180, M3_ROWS)
        zone = {"kind": "demand", "t": m5[10]["t"], "high": 99.8, "low": 99.0, "touched_t": m5[16]["t"],
                "status": "touched", "di": 5, "tf": "30m"}
        trace = []
        res, best = z.long_setups(zone, m5, m3, [], dict(CFG, min_rr=0.5), trace)
        return best, trace

    def test_a_gap_formed_mid_leg_is_in_the_band_of_the_finished_leg(self):
        best, trace = self.late_leg(self.LEG + [self.BACK])
        self.assertGreaterEqual(best, 4)                  # in the OTE and price came back into the gap
        gap = [g for g in trace[0]["gaps"] if g["lo"] == 100.0][0]
        self.assertTrue(gap["inside"])
        self.assertTrue(gap["touched"])
        # the leg when price came back was 98.6 to 106.0, so the band is 100.15 to 101.41
        self.assertAlmostEqual(gap["ote"][0], 106.0 - 0.79 * 7.4, places=6)
        self.assertAlmostEqual(gap["ote"][1], 106.0 - 0.62 * 7.4, places=6)

    def test_before_price_comes_back_the_band_uses_the_leg_so_far(self):
        best, trace = self.late_leg(self.LEG)
        self.assertEqual(best, 3)                         # in the OTE, price has not come back yet
        gap = [g for g in trace[0]["gaps"] if g["lo"] == 100.0][0]
        self.assertTrue(gap["inside"])
        self.assertFalse(gap["touched"])

    def test_a_new_high_after_price_came_back_does_not_move_the_band(self):
        best, trace = self.late_leg(self.LEG + [self.BACK, (100.5, 130.0, 100.4, 129.0)])
        self.assertGreaterEqual(best, 4)
        gap = [g for g in trace[0]["gaps"] if g["lo"] == 100.0][0]
        self.assertTrue(gap["inside"])

    def test_the_bar_that_comes_back_into_the_gap_is_not_part_of_the_leg(self):
        # it spikes to 108.0 on the way back: the leg was still 98.6 to 106.0 when price came back
        best, trace = self.late_leg(self.LEG + [(105.9, 108.0, 100.4, 100.5)])
        gap = [g for g in trace[0]["gaps"] if g["lo"] == 100.0][0]
        self.assertTrue(gap["touched"])
        self.assertTrue(gap["inside"])
        self.assertAlmostEqual(gap["ote"][0], 106.0 - 0.79 * 7.4, places=6)

    def test_price_that_breaks_the_sweep_low_before_coming_back_gives_no_touch(self):
        best, trace = self.late_leg(self.LEG + [(105.9, 106.0, 98.0, 98.5), self.BACK])
        gap = [g for g in trace[0]["gaps"] if g["lo"] == 100.0][0]
        self.assertFalse(gap["touched"])

    def test_no_sweep_no_entry(self):
        m5, m3, zone, targets = entry_data()
        m5[16] = dict(m5[16], l=99.5)  # the wick no longer goes under the swing low
        res, best = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual(res, [])
        self.assertLess(best, 3)

    def test_a_close_below_the_swing_low_is_a_breakdown_not_a_sweep(self):
        m5, m3, zone, targets = entry_data()
        m5[16] = dict(m5[16], c=99.0)  # same wick to 98.6, but it closes under the 99.15 swing low
        res, best = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual(res, [])
        self.assertEqual(best, 1)  # touched the zone, never swept it

    def test_a_zone_price_never_touched_gives_nothing(self):
        m5, m3, zone, targets = entry_data()
        res, best = z.long_setups(dict(zone, touched_t=None), m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual((res, best), ([], 0))

    def test_the_sweep_low_breaking_first_cancels_the_choch(self):
        m5, m3, zone, targets = entry_data()
        m3[42] = dict(m3[42], l=98.0)  # a 3-minute low under the sweep low before the CHoCH
        res, _ = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual(res, [])

    def test_a_supply_zone_gives_the_short_side(self):
        m5, m3, zone, targets = entry_data()
        long_res, _ = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        supply = dict(zone, kind="supply", low=-zone["high"], high=-zone["low"])
        res, best = z.setups_for(supply, z.flip_bars(m5), z.flip_bars(m3), [], dict(CFG, min_rr=0.5))
        self.assertEqual(best, 6)
        self.assertEqual(len(res), 1)
        r = res[0]
        self.assertEqual(r["side"], "short")
        self.assertEqual(r["entry"], -long_res[0]["entry"])
        self.assertGreater(r["stop"], r["entry"])      # a short's stop is above the entry
        self.assertLess(r["target"], r["entry"])       # and its target below
        self.assertEqual(r["gap"], (-102.4, -101.6))


class StageLines(unittest.TestCase):
    ZONE = {"kind": "supply", "tf": "30m", "t": et(2026, 10, 28, 0, 30), "high": 1.32965, "low": 1.32945}

    def test_a_touched_zone_line_says_how_far_it_got(self):
        line = z.stage_line("GBPUSD", self.ZONE, 2)
        self.assertEqual(line, "  SUPPLY 30m  Wed 10-28 00:30 ET (04:30 Dublin)  1.32945 to 1.32965: "
                               "swept, no FVG or IFVG with its midpoint in the OTE")

    def test_every_stage_has_a_line(self):
        demand = dict(self.ZONE, kind="demand")
        for best in range(len(z.STAGES)):
            self.assertIn(z.STAGES[best], z.stage_line("GBPUSD", demand, best))

    def test_a_supply_zone_talks_about_the_swing_high(self):
        self.assertIn("zone touched, no sweep of a swing high yet", z.stage_line("GBPUSD", self.ZONE, 1))
        demand = dict(self.ZONE, kind="demand")
        self.assertIn("zone touched, no sweep of a swing low yet", z.stage_line("GBPUSD", demand, 1))

    def test_trace_lines_show_the_sweep_and_each_gap(self):
        rec = {"t": et(2026, 10, 28, 1, 0), "swing": 1.32990, "extreme": 1.33010, "gaps": [
            {"kind": "FVG", "t": et(2026, 10, 28, 1, 15), "lo": 1.32940, "hi": 1.32960, "ote": (1.32950, 1.32990),
             "inside": False}]}
        lines = z.trace_lines("GBPUSD", self.ZONE, [rec])
        self.assertEqual(lines[0], "      sweep Wed 10-28 01:00 ET (05:00 Dublin): took the swing high 1.32990, "
                                   "extreme 1.33010, closed back inside")
        self.assertEqual(lines[1], "        FVG 1.32940 to 1.32960 (05:15 Dublin), midpoint 1.32950, "
                                   "band 1.32950 to 1.32990: outside, price has not come back yet")
        rec["gaps"][0].update(inside=True, touched=True)
        self.assertTrue(z.trace_lines("GBPUSD", self.ZONE, [rec])[1].endswith("inside, price has come back"))

    def test_a_sweep_with_no_gap_says_so_and_long_lists_are_cut(self):
        rec = {"t": et(2026, 10, 28, 1, 0), "swing": 1.0, "extreme": 1.1, "gaps": []}
        self.assertIn("no FVG or IFVG formed after it", z.trace_lines("GBPUSD", self.ZONE, [rec])[1])
        gap = {"kind": "FVG", "t": rec["t"], "lo": 1.0, "hi": 1.2, "ote": (1.0, 1.1), "inside": True}
        many = dict(rec, gaps=[gap] * 6)
        out = z.trace_lines("GBPUSD", self.ZONE, [many])
        self.assertEqual(len(out), 1 + 4 + 1)
        self.assertEqual(out[-1], "        and 2 more")
        five = [dict(rec, t=et(2026, 10, 28, h, 0)) for h in (1, 2, 3, 4, 5)]
        only_last = [ln for ln in z.trace_lines("GBPUSD", self.ZONE, five) if "sweep" in ln]
        self.assertEqual(len(only_last), 3)
        self.assertEqual([ln.split(" ET")[0].split()[-1] for ln in only_last], ["03:00", "04:00", "05:00"])   # the latest three

    def test_a_long_trace_lists_the_sweep_and_the_gaps_it_checked(self):
        m5, m3, zone, targets = entry_data()
        trace = []
        res, best = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5), trace)
        self.assertEqual(best, 6)
        self.assertEqual(len(trace), 1)
        rec = trace[0]
        self.assertEqual(rec["t"], m5[16]["t"])
        self.assertEqual(rec["extreme"], 98.6)
        self.assertTrue(any(g["inside"] for g in rec["gaps"]))
        self.assertTrue(all(g["ote"][0] < g["ote"][1] for g in rec["gaps"]))

    def test_a_trace_asks_for_nothing_when_no_trace_is_given(self):
        m5, m3, zone, targets = entry_data()
        res, best = z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5))
        self.assertEqual(best, 6)

    def test_a_supply_trace_is_in_real_prices(self):
        m5, m3, zone, targets = entry_data()
        supply = dict(zone, kind="supply", low=-zone["high"], high=-zone["low"])
        trace = []
        z.setups_for(supply, z.flip_bars(m5), z.flip_bars(m3), [], dict(CFG, min_rr=0.5), trace)
        self.assertEqual(trace[0]["extreme"], -98.6)           # the highest high of a supply sweep
        inside = [g for g in trace[0]["gaps"] if g["inside"]]
        self.assertIn((-102.4, -101.6), [(g["lo"], g["hi"]) for g in inside])
        for g in trace[0]["gaps"]:
            self.assertLess(g["lo"], g["hi"])
            self.assertLess(g["ote"][0], g["ote"][1])

    def test_a_long_gap_that_price_came_back_to_carries_the_choch_state(self):
        m5, m3, zone, targets = entry_data()
        trace = []
        z.long_setups(zone, m5, m3, targets, dict(CFG, min_rr=0.5), trace)
        states = [g["choch"] for g in trace[0]["gaps"] if "choch" in g]
        self.assertTrue(states)
        c = states[0]
        self.assertEqual(c["pivot"], 104.6)
        self.assertTrue(c["lower_high"])
        self.assertEqual(c["close"], 105.1)
        self.assertEqual(c["pivot_t"], 1793018700)
        self.assertIsNone(c["broken_t"])
        for g in trace[0]["gaps"]:
            if not g["inside"] or not g["touched"]:
                self.assertNotIn("choch", g)

    def test_a_supply_trace_choch_state_is_in_real_prices(self):
        m5, m3, zone, targets = entry_data()
        supply = dict(zone, kind="supply", low=-zone["high"], high=-zone["low"])
        trace = []
        z.setups_for(supply, z.flip_bars(m5), z.flip_bars(m3), [], dict(CFG, min_rr=0.5), trace)
        c = [g["choch"] for g in trace[0]["gaps"] if "choch" in g][0]
        self.assertEqual(c["pivot"], -104.6)
        self.assertEqual(c["close"], -105.1)

    def test_the_sweep_extreme_going_first_is_reported(self):
        m5, m3, zone, targets = entry_data()
        ph3, _ = z.pivots(m3, 2)
        st = z.m3_choch_state(m3, ph3, 0, 0, max(b["l"] for b in m3) + 1.0)
        self.assertEqual(st["broken_t"], m3[0]["t"])
        st = z.m3_choch_state(m3, ph3, 0, 0, min(b["l"] for b in m3) - 1.0)
        self.assertIsNone(st["broken_t"])

    def test_no_swing_after_the_sweep_means_no_choch_level(self):
        m5, m3, zone, targets = entry_data()
        ph3, _ = z.pivots(m3, 2)
        st = z.m3_choch_state(m3, ph3, 0, len(m3) - 1, 0.0)
        self.assertIsNone(st["pivot"])
        self.assertIsNone(st["lower_high"])
        self.assertEqual(st["close"], m3[-1]["c"])

    def test_choch_line_wording_for_a_short(self):
        c = {"pivot": 1.32870, "pivot_t": et(2026, 10, 28, 5, 30), "lower_high": True, "close": 1.32885,
             "close_t": et(2026, 10, 28, 6, 40), "broken_t": None}
        self.assertEqual(z.choch_line("GBPUSD", self.ZONE, c),
                         "          3-minute: last swing low after the sweep 1.32870 (09:30 Dublin); "
                         "latest close 1.32885 (10:40 Dublin); a close below 1.32870 is the CHoCH")
        self.assertIn("but it is not a higher low so it cannot be the CHoCH level",
                      z.choch_line("GBPUSD", self.ZONE, dict(c, lower_high=False)))
        none = z.choch_line("GBPUSD", self.ZONE, dict(c, pivot=None, pivot_t=None, lower_high=None))
        self.assertIn("no swing low after the sweep yet", none)
        self.assertIn("latest close 1.32885", none)
        cut = z.choch_line("GBPUSD", self.ZONE, dict(c, broken_t=et(2026, 10, 28, 6, 5)))
        self.assertEqual(cut, "          3-minute: price traded beyond the sweep extreme at 10:05 Dublin, "
                              "so this attempt is cancelled")

    def test_choch_line_wording_for_a_long(self):
        demand = dict(self.ZONE, kind="demand")
        c = {"pivot": 1.32870, "pivot_t": et(2026, 10, 28, 5, 30), "lower_high": False, "close": 1.32850,
             "close_t": et(2026, 10, 28, 6, 40), "broken_t": None}
        line = z.choch_line("GBPUSD", demand, c)
        self.assertIn("last swing high after the sweep", line)
        self.assertIn("not a lower high", line)
        self.assertIn("a close above 1.32870 is the CHoCH", line)

    def test_trace_lines_print_the_choch_line_only_under_gaps_in_the_band(self):
        c = {"pivot": 1.32870, "pivot_t": et(2026, 10, 28, 5, 30), "lower_high": True, "close": 1.32885,
             "close_t": et(2026, 10, 28, 6, 40), "broken_t": None}
        g = {"kind": "FVG", "t": et(2026, 10, 28, 5, 15), "lo": 1.0, "hi": 1.2, "ote": (1.0, 1.1),
             "inside": True, "touched": True, "choch": c}
        rec = {"t": et(2026, 10, 28, 5, 0), "swing": 1.0, "extreme": 1.1, "gaps": [g]}
        out = z.trace_lines("GBPUSD", self.ZONE, [rec])
        self.assertEqual(len(out), 3)
        self.assertEqual(out[2], z.choch_line("GBPUSD", self.ZONE, c))
        g["inside"] = False
        self.assertEqual(len(z.trace_lines("GBPUSD", self.ZONE, [rec])), 2)


class Command(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = z.main(argv)
        return code, out.getvalue(), err.getvalue()

    def folder(self, sym="MGC"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bars = mk(ZSTART, 1800, zone_rows())
        with open(os.path.join(tmp.name, "%s_30m.json" % sym), "w") as fh:
            fh.write(chart_json(bars))
        return tmp.name

    def test_zones_reads_a_saved_chart_and_prints_the_zone(self):
        code, out, _ = self.run_cli(["zones", "MGC", "--tf", "30m", "--offline", self.folder(),
                                     "--now", "2026-10-26 12:00", "--config", "none.json"])
        self.assertEqual(code, 0)
        self.assertIn("MGC (MGC=F)", out)
        self.assertIn("DEMAND 30m", out)
        self.assertIn("99.20 to 100.80", out)
        self.assertIn("fresh", out)

    def test_save_keeps_the_downloaded_charts_and_they_replay_to_the_same_answer(self):
        text = chart_json(mk(ZSTART, 1800, zone_rows()))
        calls = []

        def fake(path, *a, **k):
            calls.append(path)
            return text

        saved = tempfile.TemporaryDirectory()
        self.addCleanup(saved.cleanup)
        argv = ["zones", "MGC", "--tf", "30m", "--now", "2026-10-26 12:00", "--config", "none.json"]
        real = z.yahoo_get
        z.yahoo_get = fake
        try:
            code, live, _ = self.run_cli(argv + ["--save", saved.name])
            self.assertEqual(code, 0)
            with open(os.path.join(saved.name, "MGC_30m.json"), encoding="utf-8") as fh:
                self.assertEqual(fh.read(), text)
            code, again, _ = self.run_cli(argv)
            self.assertEqual(len(os.listdir(saved.name)), 1)
        finally:
            z.yahoo_get = real
        code, replay, _ = self.run_cli(argv + ["--offline", saved.name])
        self.assertEqual(replay, live)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(z.SAVE_DIR)

    def test_the_days_window_drops_an_old_zone(self):
        code, out, _ = self.run_cli(["zones", "MGC", "--tf", "30m", "--offline", self.folder(),
                                     "--now", "2026-10-26 12:00", "--config", "none.json", "--days", "1"])
        self.assertEqual(code, 0)
        # moving "now" two days on leaves the zone outside the one-day window
        code, out, _ = self.run_cli(["zones", "MGC", "--tf", "30m", "--offline", self.folder(),
                                     "--now", "2026-10-28 12:00", "--config", "none.json", "--days", "1"])
        self.assertEqual(code, 1)
        self.assertIn("No zones", out)

    def test_json_and_write(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dest = os.path.join(tmp.name, "out", "zones.json")
        code, out, _ = self.run_cli(["zones", "MGC", "--tf", "30m", "--offline", self.folder(),
                                     "--now", "2026-10-26 12:00", "--config", "none.json", "--write", "--out", dest])
        self.assertEqual(code, 0)
        with open(dest) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["symbol"], "MGC")
        self.assertEqual(doc["zones"][0]["kind"], "demand")

    def test_missing_offline_file_is_a_plain_error(self):
        code, _, err = self.run_cli(["zones", "GBPUSD", "--tf", "30m", "--offline", self.folder("MGC"),
                                     "--now", "2026-10-26 12:00", "--config", "none.json"])
        self.assertEqual(code, 2)
        self.assertIn("no offline file", err)

    def test_unknown_market_exits_2(self):
        code, _, err = self.run_cli(["zones", "TSLA"])
        self.assertEqual(code, 2)
        self.assertIn("unknown market", err)

    def test_config_overrides_one_setting_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as fh:
                json.dump({"zones": {"min_rr": 3, "sessions": {"ny": [8, 9]}}}, fh)
            cfg = z.read_config(path)
        self.assertEqual(cfg["min_rr"], 3)
        self.assertEqual(cfg["sessions"]["ny"], [8, 9])
        self.assertEqual(cfg["sessions"]["london"], [8, 16])
        self.assertEqual(cfg["session_tz"], "dublin")
        self.assertEqual(cfg["max_per_day"], 3)

    def test_bad_config_json_is_a_plain_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w") as fh:
                fh.write("{not json")
            with self.assertRaises(z.ZoneError):
                z.read_config(path)

    def test_the_tool_never_places_an_order(self):
        with open(os.path.join(os.path.dirname(z.__file__), "zones.py"), encoding="utf-8") as fh:
            src = fh.read().lower()
        for word in ("alpaca", "api_key", "secret", "place_order", "submit_order", "os.environ"):
            self.assertNotIn(word, src)


class Explain(unittest.TestCase):
    """The `explain` command: why one candle is or is not a zone, in the rule's own numbers."""

    OFF = {"away_bars": 0}   # switches the slow-move rule off, to isolate the single-candle rule

    def run_explain(self, rows, at, over=None, extra=()):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "MGC_30m.json"), "w") as fh:
            fh.write(chart_json(mk(ZSTART, 1800, rows)))
        cfg = os.path.join(tmp.name, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"zones": over or {}}, fh)
        out, err = io.StringIO(), io.StringIO()
        argv = ["explain", "MGC", "--at", at, "--offline", tmp.name, "--now", "2026-10-26 12:00",
                "--config", cfg] + list(extra)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = z.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_real_zone_is_confirmed_with_its_numbers(self):
        code, out, _ = self.run_explain(zone_rows(), "2026-10-26 07:00")
        self.assertEqual(code, 0)
        self.assertIn("MGC candle Mon 10-26 07:00 ET", out)
        self.assertIn("(a zone candle needs under 0.40): ok", out)
        self.assertIn("up    displacement", out)
        self.assertIn("move away is long enough (needs 2.00 x ATR)", out)
        self.assertIn("this IS a zone by the rule (demand)", out)

    def test_a_big_bodied_candle_says_too_big_and_stops(self):
        rows = noise(PRE) + [SOLID, DISPLACEMENT] + AFTER_UP
        code, out, _ = self.run_explain(rows, "2026-10-26 07:00")
        self.assertEqual(code, 0)
        self.assertIn("TOO BIG", out)
        self.assertIn("not a zone candle by the rule", out)
        self.assertNotIn("Next 5 bars", out)

    def test_a_close_that_does_not_pass_the_swing_is_named(self):
        code, out, _ = self.run_explain(swing_rows(106.0), "2026-10-26 07:00", self.OFF)
        self.assertIn("does not pass the latest swing high 106.00", out)
        self.assertIn("Result: not a zone: no big displacement and no slow move", out)

    def test_a_short_move_away_is_named(self):
        weak = (100.8, 102.65, 100.75, 102.6)
        code, out, _ = self.run_explain(noise(PRE) + [ZONE_CANDLE, weak] + AFTER_UP[:1], "2026-10-26 07:00", self.OFF)
        self.assertIn("Result: not a zone: the move away is 1.", out)
        self.assertIn("needs 2.00", out)

    def test_a_more_extreme_candle_in_the_same_move_takes_the_zone(self):
        second = (100.0, 100.8, 99.3, 100.05)       # closer to the move, but its low is higher
        rows = noise(PRE) + [ZONE_CANDLE, second, DISPLACEMENT] + AFTER_UP
        code, out, _ = self.run_explain(rows, "2026-10-26 07:30", self.OFF)
        self.assertIn("the candle at Mon 10-26 07:00 ET is the lowest low in the same move", out)
        code, out, _ = self.run_explain(rows, "2026-10-26 07:00", self.OFF)
        self.assertIn("this IS a zone by the rule", out)

    def test_a_slow_move_is_checked_when_there_is_no_big_candle(self):
        rows = noise(PRE) + [ZONE_CANDLE] + FindZones.DRIFT
        code, out, _ = self.run_explain(rows, "2026-10-26 07:00")
        self.assertIn("No displacement candle. Slow move check", out)
        self.assertIn("demand (price rises): 2.", out)
        self.assertIn("price stays above the candle's low until then: yes", out)
        self.assertIn("supply (price falls): no", out)
        self.assertIn("this IS a zone by the rule (demand)", out)

    def test_a_slow_move_that_the_rule_does_not_accept_says_so(self):
        rows = noise(PRE) + [ZONE_CANDLE] + FindZones.DRIFT
        code, out, _ = self.run_explain(rows, "2026-10-26 07:00", self.OFF)
        self.assertIn("price never got far enough away", out)
        self.assertIn("Result: not a zone: no big displacement and no slow move", out)

    def test_a_candle_outside_the_sessions_is_flagged(self):
        windows = {"sessions": {"asia": [0, 2], "london": [2, 4], "ny": [4, 6]}}      # Dublin hours
        code, out, _ = self.run_explain(zone_rows(), "2026-10-26 06:00", windows)    # 10:00 Dublin
        self.assertIn("Opened at 10:00 Dublin time", out)
        self.assertIn("NO, so zones_in_sessions drops it", out)
        code, out, _ = self.run_explain(zone_rows(), "2026-10-26 07:00")             # 11:00 Dublin
        self.assertIn("Opened at 11:00 Dublin time. In an Asia, London or New York window: yes (london)", out)

    def test_a_bad_session_clock_is_a_plain_error(self):
        code, _, err = self.run_explain(zone_rows(), "2026-10-26 07:00", {"session_tz": "paris"})
        self.assertEqual(code, 2)
        self.assertIn('session_tz must be "dublin" or "ny"', err)

    def test_a_time_with_no_bar_is_a_plain_error(self):
        code, _, err = self.run_explain(zone_rows(), "2026-10-20 07:00")
        self.assertEqual(code, 2)
        self.assertIn("no 30m bar at 2026-10-20 07:00 ET", err)

    def test_a_bad_time_names_the_flag(self):
        code, _, err = self.run_explain(zone_rows(), "tuesday")
        self.assertEqual(code, 2)
        self.assertIn("--at wants New York time", err)


if __name__ == "__main__":
    unittest.main()
