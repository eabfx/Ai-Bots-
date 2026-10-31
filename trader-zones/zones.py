#!/usr/bin/env python3
"""Zone Finder for trader-zones: ICT-style supply and demand zones on the 1-hour,
30-minute and 15-minute charts, and the 5-minute and 3-minute entry that follows
a sweep of a zone. Standard library only. It never places an order.

    python3 zones.py markets                          # the five markets and their Yahoo tickers
    python3 zones.py zones GBPUSD                     # today's and yesterday's zones, 60m + 30m + 15m
    python3 zones.py zones XAUUSD --tf 30m --days 2 --max-per-day 3
    python3 zones.py setup NQ                         # sweep + FVG/IFVG in the OTE + M3 CHoCH, long and short
    python3 zones.py setup MGC --now "2026-10-26 08:15" --offline DIR    # replay (New York time)
    python3 zones.py setup EURUSD --write             # also save trader/zones.json

Markets: XAUUSD (gold, read from COMEX gold futures GC=F), GBPUSD, EURUSD,
NQ (Nasdaq 100 futures NQ=F) and MGC (micro gold futures MGC=F).

The zone (one candle, high to low):
  displacement  a candle whose body is at least disp_atr x ATR(14) and
                disp_body of its own range, and that CLOSES beyond the last
                confirmed swing high (demand) or swing low (supply).
  zone candle   a candle up to base_lookback bars before it with a small body
                (under base_body_atr x ATR), wicks making up more than base_wick
                of its range, and a move away from its extreme of at least
                move_atr x ATR. When several qualify the most extreme one is the
                zone (the highest high for supply, the lowest low for demand).
                The zone is that one candle, high to low.
  slow move     no single big candle is needed: a small-bodied, long-wicked candle,
                then price closes away_atr x ATR beyond it within away_bars bars
                without trading beyond its extreme (more than extreme_tol_atr x ATR)
                before that, is a zone too.
  fresh         price has not traded back into it. A zone price has entered is
                "touched".
  caps          only zones from the last --days days, at most --max-per-day per
                day, and (zones_in_sessions) only zone candles that opened in the
                Asia, London or New York window.

The entry (long side shown, short is the mirror image):
  1. price touches a demand zone
  2. on 5-minute bars it sweeps the latest swing low (wick below, close back
     above) with the wick reaching into the zone
  3. after the sweep a 5-minute FVG forms, or a bearish FVG is closed through
     (an IFVG), and its midpoint sits in the 62% to 79% retracement of the leg
     from the sweep low to the highest high, measured when price comes back to
     the gap (or as it stands now, if it has not come back yet)
  4. price trades back into that gap without breaking the sweep low
  5. on 3-minute bars, once price is back in the gap, a close above the last swing
     high made after the sweep (the CHoCH) is the trigger, inside an Asia, London
     or New York window (choch_needs_trend true also asks that swing to be a lower
     high than the one before it)
  stop below the sweep low (sweep_buffer_atr x ATR; stop_at leg_low puts it below the
  low the leg started from instead), target target_r times the risk
  (3 by default; 0 uses the nearest opposing zone or swing), and the trade is dropped
  when the reward to risk is under min_rr.

Session hours are Dublin time by default (session_tz "dublin", Asia 0 to 8, London
8 to 16, New York 13 to 21), with Irish daylight saving; session_tz "ny" reads them as
New York time instead. Zone lines show New York time and Dublin time. The 3-minute
bars are built from 1-minute bars, so Yahoo's roughly week of 1-minute history
is how far back `setup` can look.

Data: Yahoo's free chart JSON (unofficial, can lag, forex and futures bars).
  --offline DIR reads DIR/<SYMBOL>_<interval>.json (a saved Yahoo chart), for
  example GBPUSD_30m.json, GBPUSD_5m.json and GBPUSD_1m.json. A .csv with the same name works
  too: a chart export with a header of time, open, high, low and close (TradingView's
  "Export chart data"), time as Unix seconds or an ISO date and time.
  --save DIR writes those files when it downloads, so a live run can be replayed exactly.
  --zone KIND LOW HIGH WHEN (setup only) uses a box you drew instead of the detected zones.
  --spot PRICE takes the gap between futures and your spot chart off every price: give the
  price your chart shows now and the latest close is shifted to match it.

trader/config.json (optional): a "zones" object overrides any default, for
example {"zones": {"min_rr": 3, "sessions": {"ny": [13, 17]}}}.

Exit codes: 0 when something was found, 1 when nothing was, 2 on an error.
"""

import argparse
import bisect
import csv
import datetime
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"
HOSTS = ("query2", "query1")
BACKOFF = 5.0
EPOCH = datetime.datetime(1970, 1, 1)
INTERVAL_SECS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600}

MARKETS = {
    "XAUUSD": {"yahoo": "GC=F", "digits": 2, "note": "COMEX gold futures stand in for spot gold"},
    "GBPUSD": {"yahoo": "GBPUSD=X", "digits": 5, "note": "spot forex", "spot": True},
    "EURUSD": {"yahoo": "EURUSD=X", "digits": 5, "note": "spot forex", "spot": True},
    "NQ": {"yahoo": "NQ=F", "digits": 2, "note": "Nasdaq 100 futures"},
    "MGC": {"yahoo": "MGC=F", "digits": 2, "note": "micro gold futures"},
}
ALIASES = {"NAS100": "NQ", "US100": "NQ", "NDX": "NQ", "GOLD": "XAUUSD"}

DEFAULTS = {
    "atr_len": 14,
    "swing_n": 2,
    "disp_atr": 1.5,
    "disp_body": 0.6,
    "base_body_atr": 0.4,
    "base_wick": 0.5,
    "base_lookback": 5,
    "move_atr": 2.0,
    "away_bars": 10,
    "away_atr": 2.0,
    "extreme_tol_atr": 0.1,
    "fvg_min_atr": 0.1,
    "sweep_buffer_atr": 0.2,
    "zone_candle": "extreme", # "extreme": the highest high / lowest low of the pause before a displacement candle;
                              # "last": the candle right before the move (nearest one that has the zone shape)
    "respect_atr": 1.0,   # a zone is respected when price comes back to it and bounces this many ATR away
    "min_rr": 2.0,
    "target_r": 3.0,      # take profit at this many times the risk; 0 uses the nearest opposing zone or swing
    "days": 2,
    "max_per_day": 3,
    "choch_needs_trend": False,
    "leg_from_swing": True,   # the leg starts at the last swing low before price breaks up, not the sweep wick
    "gap_at_turn": True,      # a gap left by the turn out of the band counts without a second return to it
    "stop_at": "sweep",       # "sweep": stop beyond the sweep wick; "leg_low": stop beyond the low the leg started from
    "entry_style": "tool",    # "tool": sweep + FVG in the OTE + 3-minute CHoCH. "yours": the user's own way, see yours_long
    "fib_reach": 0.705,       # "yours": price has to retrace this far into the leg (the 0.705 line)
    "fib_deep": 0.79,         # "yours": a close past this line means the fib was not respected
    "leg_min_atr": 7.0,       # "yours": the leg has to be at least this many 5-minute ATRs long (a real swing, not a wobble)
    "leg_max_bars": 96,       # "yours": the leg has to finish within this many 5-minute bars of its start
    "leg_zone_slack": 1.0,    # "yours": the leg may start this many zone heights under the zone (a zone broken through)
    "leg_zone_near": 1.0,     # "yours": a leg may also start this many zone heights short of the zone (a wick that nearly got there)
    "zones_in_sessions": True,
    # Session hours [start, end) in the clock named by session_tz: "dublin" (Irish time, with its
    # own daylight saving) or "ny" (New York time). 24 means midnight.
    "session_tz": "dublin",
    "sessions": {"asia": [0, 8], "london": [8, 16], "ny": [13, 21]},
}
NY_SESSIONS = {"asia": [19, 24], "london": [2, 5], "ny": [7, 10]}   # the short New York kill zones


class ZoneError(Exception):
    pass


# ---------------------------------------------------------------- time

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def eastern_offset(utc):
    """Hours from UTC for New York: -4 in daylight time, -5 otherwise (US rule since 2007)."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))
    naive = utc.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def utc_of(ts):
    return EPOCH + datetime.timedelta(seconds=int(ts))


def to_eastern(ts):
    u = utc_of(ts)
    return u + datetime.timedelta(hours=eastern_offset(u))


def eastern_to_ts(local):
    for off in (-4, -5):
        utc = local - datetime.timedelta(hours=off)
        if eastern_offset(utc) == off:
            return int((utc - EPOCH).total_seconds())
    return int((local + datetime.timedelta(hours=5) - EPOCH).total_seconds())


def now_ts():
    return int(time.time())


def fmt_et(ts):
    return to_eastern(ts).strftime("%a %m-%d %H:%M")


def _last_sunday(year, month):
    d = datetime.date(year, month, 31)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def dublin_offset(utc):
    """Hours from UTC for Ireland: +1 in summer time (EU rule: last Sunday of March 01:00 UTC to
    last Sunday of October 01:00 UTC), 0 otherwise."""
    y = utc.year
    start = datetime.datetime.combine(_last_sunday(y, 3), datetime.time(1, 0))
    end = datetime.datetime.combine(_last_sunday(y, 10), datetime.time(1, 0))
    return 1 if start <= utc.replace(tzinfo=None) < end else 0


def to_dublin(ts):
    u = utc_of(ts)
    return u + datetime.timedelta(hours=dublin_offset(u))


def fmt_dublin(ts, ref_ts):
    """Dublin time of ts, with the weekday only when it differs from the New York weekday."""
    d = to_dublin(ts)
    if d.date() == to_eastern(ref_ts).date():
        return d.strftime("%H:%M")
    return d.strftime("%a %H:%M")


def session_of(ts, cfg):
    """Name of the session window the bar opened in, or None."""
    local = to_eastern(ts) if cfg.get("session_tz") == "ny" else to_dublin(ts)
    minutes = local.hour * 60 + local.minute
    for name, (lo, hi) in cfg["sessions"].items():
        if lo * 60 <= minutes < hi * 60:
            return name
    return None


def in_session(ts, cfg):
    return session_of(ts, cfg) is not None


# ---------------------------------------------------------------- data

def resolve(sym):
    s = sym.upper().replace("/", "")
    s = ALIASES.get(s, s)
    if s not in MARKETS:
        raise ZoneError("unknown market %s: use one of %s" % (sym, ", ".join(sorted(MARKETS))))
    return s


def _open_url(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    last = "no answer"
    for host in HOSTS:
        url = "https://%s.finance.yahoo.com%s" % (host, path)
        for attempt in (0, 1):
            try:
                return _open_url(url)
            except urllib.error.HTTPError as e:
                code = e.code
                e.close()
                last = "HTTP %d" % code
                if code == 429 and attempt == 0:
                    time.sleep(BACKOFF)
                    continue
                if code == 429 or code >= 500:
                    break
                raise ZoneError("Yahoo answered HTTP %d for %s" % (code, url.split("?")[0]))
            except urllib.error.URLError as e:
                last = "could not connect: %s" % (e.reason,)
                break
            except OSError as e:
                last = "network error: %s" % (e,)
                break
    raise ZoneError("Yahoo did not answer (%s): wait a minute and try again" % last)


def parse_chart(text, sym):
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise ZoneError("Yahoo returned something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise ZoneError("Yahoo has no data for %s: %s" % (sym, err.get("description") or "unknown ticker"))
    r = res[0] or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}

    def at(key, i):
        arr = q.get(key) or []
        return arr[i] if i < len(arr) else None

    bars = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = at("close", i)
        if t is None or c is None:
            continue
        o, h, lo = at("open", i), at("high", i), at("low", i)
        bars.append({"t": int(t), "o": float(c if o is None else o), "h": float(c if h is None else h),
                     "l": float(c if lo is None else lo), "c": float(c)})
    bars.sort(key=lambda b: b["t"])
    return bars


SAVE_DIR = None     # set by --save: where get_bars writes the Yahoo charts it downloads
SHIFT = 0.0         # set by --spot: points taken off every price, so futures line up with a spot chart


def get_bars(sym, interval, days, offline=None):
    bars = _raw_bars(sym, interval, days, offline)
    if not SHIFT:
        return bars
    return [{"t": b["t"], "o": b["o"] - SHIFT, "h": b["h"] - SHIFT, "l": b["l"] - SHIFT, "c": b["c"] - SHIFT}
            for b in bars]


def parse_time(text):
    """A bar time from a chart export: Unix seconds or milliseconds, or an ISO date and time (UTC when
    it carries no offset)."""
    text = text.strip()
    try:
        n = float(text)
        return int(n / 1000.0) if n > 1e11 else int(n)
    except ValueError:
        pass
    try:
        d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00").replace(" UTC", "+00:00"))
    except ValueError:
        raise ZoneError("cannot read the time %r in the CSV" % text)
    if d.tzinfo is not None:
        d = d.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return int((d - EPOCH).total_seconds())


def parse_csv(text, name):
    """Bars from a chart export (TradingView's Export chart data): a header with time, open, high, low
    and close, one bar per line."""
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise ZoneError("%s is empty" % name)
    head = [h.strip().lower() for h in rows[0]]
    need = ["time", "open", "high", "low", "close"]
    missing = [k for k in need if k not in head]
    if missing:
        raise ZoneError("%s needs a header with time, open, high, low and close (missing: %s)"
                        % (name, ", ".join(missing)))
    idx = {k: head.index(k) for k in need}
    bars = []
    for row in rows[1:]:
        try:
            vals = [row[idx[k]].strip() for k in need]
        except IndexError:
            continue
        if not all(vals):
            continue
        bars.append({"t": parse_time(vals[0]), "o": float(vals[1]), "h": float(vals[2]), "l": float(vals[3]),
                     "c": float(vals[4])})
    bars.sort(key=lambda b: b["t"])
    return bars


def _raw_bars(sym, interval, days, offline=None):
    if offline:
        path = os.path.join(offline, "%s_%s.json" % (sym, interval))
        if not os.path.exists(path):
            csv_path = os.path.join(offline, "%s_%s.csv" % (sym, interval))
            if os.path.exists(csv_path):
                with open(csv_path, encoding="utf-8-sig") as fh:
                    return parse_csv(fh.read(), csv_path)
            raise ZoneError("no offline file %s" % path)
        with open(path, encoding="utf-8") as fh:
            return parse_chart(fh.read(), sym)
    if interval == "1m":
        rng = "5d"
    elif interval == "60m":
        rng = "1mo"
    else:
        rng = "5d" if days <= 3 else "1mo"
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (MARKETS[sym]["yahoo"], rng, interval)
    text = yahoo_get(path)
    bars = parse_chart(text, sym)
    if SAVE_DIR:
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(os.path.join(SAVE_DIR, "%s_%s.json" % (sym, interval)), "w", encoding="utf-8") as fh:
            fh.write(text)
    return bars


def closed_only(bars, secs, now):
    """A bar counts once it has closed: now >= its start + its length."""
    return [b for b in bars if b["t"] + secs <= now]


def resample(bars, secs):
    """1-minute bars into `secs`-second bars, aligned to UTC midnight."""
    out, cur, key = [], None, None
    for b in bars:
        k = b["t"] - b["t"] % secs
        if k != key:
            if cur:
                out.append(cur)
            cur, key = {"t": k, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"]}, k
        else:
            cur["h"] = max(cur["h"], b["h"])
            cur["l"] = min(cur["l"], b["l"])
            cur["c"] = b["c"]
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- indicators

def atr_series(bars, n):
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            tr.append(b["h"] - b["l"])
        else:
            pc = bars[i - 1]["c"]
            tr.append(max(b["h"] - b["l"], abs(b["h"] - pc), abs(b["l"] - pc)))
    out = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        out[i] = sum(tr[i - n + 1:i + 1]) / float(n)
    return out


def pivots(bars, n):
    """Swing highs and lows as (confirm_index, index, price). A swing is known only
    n bars after it forms, so nothing here looks ahead."""
    ph, pl = [], []
    for j in range(n, len(bars) - n):
        window = bars[j - n:j + n + 1]
        if bars[j]["h"] >= max(b["h"] for b in window):
            ph.append((j + n, j, bars[j]["h"]))
        if bars[j]["l"] <= min(b["l"] for b in window):
            pl.append((j + n, j, bars[j]["l"]))
    return ph, pl


def last_known(pivs, size):
    """For each bar, the price of the latest swing already confirmed (None before the first)."""
    out, p, cur = [], 0, None
    for i in range(size):
        while p < len(pivs) and pivs[p][0] <= i:
            cur = pivs[p][2]
            p += 1
        out.append(cur)
    return out


def flip_bars(bars):
    return [{"t": b["t"], "o": -b["o"], "h": -b["l"], "l": -b["h"], "c": -b["c"]} for b in bars]


# ---------------------------------------------------------------- zones

def is_base(bars, a, k, cfg):
    """A small body and long wicks: the one-candle shape of a zone."""
    z = bars[k]
    rk = z["h"] - z["l"]
    kb = abs(z["c"] - z["o"])
    if a[k] is None or a[k] <= 0 or rk <= 0:
        return False
    return kb < cfg["base_body_atr"] * a[k] and (rk - kb) / rk > cfg["base_wick"]


def pick_extreme(bars, ks, up):
    """Among zone candles in one move, the lowest low (demand) or the highest high (supply);
    the earliest wins a tie."""
    if up:
        return min(ks, key=lambda k: (bars[k]["l"], k))
    return min(ks, key=lambda k: (-bars[k]["h"], k))


def gradual_away(bars, a, k, up, cfg):
    """A slow move away from the zone candle at k: the first bar, within away_bars, that closes
    away_atr x ATR beyond its near edge, with no close back through its far edge on the way.
    Returns (bar index or None, best distance in ATR, why not)."""
    z = bars[k]
    need = cfg["away_atr"] * a[k]
    best = 0.0
    for j in range(k + 1, min(k + cfg["away_bars"], len(bars) - 1) + 1):
        c = bars[j]["c"]
        if (up and c < z["l"]) or (not up and c > z["h"]):
            return None, best / a[k], "price closed back through the far edge of the zone"
        d = (c - z["h"]) if up else (z["l"] - c)
        best = max(best, d)
        if d >= need:
            return j, d / a[k], ""
    return None, best / a[k], "price never got far enough away"


def is_extreme_of_area(bars, a, k, j, up, cfg):
    """Between the zone candle and the bar that confirms the move, price never trades beyond the
    candle's extreme (above its high for supply, below its low for demand) by more than
    extreme_tol_atr x ATR. What came before the candle does not matter: a zone can be a pause
    inside a leg."""
    tol = cfg["extreme_tol_atr"] * a[k]
    if up:
        return bars[k]["l"] <= min(x["l"] for x in bars[k + 1:j + 1]) + tol
    return bars[k]["h"] >= max(x["h"] for x in bars[k + 1:j + 1]) - tol


def displacement_direction(bars, a, sh, sl, i, cfg):
    """True for an up displacement candle, False for a down one, None when bar i is neither."""
    b = bars[i]
    rng = b["h"] - b["l"]
    body = abs(b["c"] - b["o"])
    if a[i] is None or a[i] <= 0 or rng <= 0 or body < cfg["disp_atr"] * a[i] or body < cfg["disp_body"] * rng:
        return None
    up = b["c"] > b["o"]
    if up and not (sh[i - 1] is not None and b["c"] > sh[i - 1]):
        return None
    if not up and not (sl[i - 1] is not None and b["c"] < sl[i - 1]):
        return None
    return up


def base_candidates(bars, a, i, up, cfg):
    """Zone candles before displacement bar i: base shape plus a move away of move_atr x ATR."""
    out = []
    for k in range(i - 1, max(i - 1 - cfg["base_lookback"], 0), -1):
        if not is_base(bars, a, k, cfg):
            continue
        if up:
            move = max(x["h"] for x in bars[k + 1:i + 1]) - bars[k]["h"]
        else:
            move = bars[k]["l"] - min(x["l"] for x in bars[k + 1:i + 1])
        if move >= cfg["move_atr"] * a[i]:
            out.append(k)
    return out


def respect_state(bars, a, zn, cfg):
    """Has price come back to this zone and held it? Returns (state, time of the verdict).
    "respected": after coming back it bounced `respect_atr` x ATR away from the zone before any bar closed
                 through the far edge. "failed": a bar closed through the far edge first.
    "testing":   it came back and neither has happened yet. "untested": price never came back."""
    demand = zn["kind"] == "demand"
    start = None
    for m in range(zn["di"] + 1, len(bars)):
        if (bars[m]["l"] <= zn["high"]) if demand else (bars[m]["h"] >= zn["low"]):
            start = m
            break
    if start is None:
        return "untested", None
    if not a[start]:
        return "testing", None
    need = cfg["respect_atr"] * a[start]
    for m in range(start, len(bars)):
        b = bars[m]
        if demand:
            if b["c"] < zn["low"]:
                return "failed", b["t"]
            if m > start and b["h"] >= zn["high"] + need:
                return "respected", b["t"]
        else:
            if b["c"] > zn["high"]:
                return "failed", b["t"]
            if m > start and b["l"] <= zn["low"] - need:
                return "respected", b["t"]
    return "testing", None


MAX_TOUCHES = 20


def zone_touches(bars, a, zn, cfg, first):
    """Every separate visit price made to a zone, from bar index `first` on (the bar after the move that made it).
    A visit starts when a bar trades into the zone after one that did not, and lasts while bars keep trading
    in or through it. Each visit says when it began, the session that bar opened in (None outside the
    configured windows), how far in the wick went (1.0 is the far edge), and what happened next:
    "held" (price moved `respect_atr` x ATR away before any close through the far edge),
    "closed through", "no clear reaction" (the next visit came first) or "open" (the data ends first).
    The latest MAX_TOUCHES visits are kept."""
    demand = zn["kind"] == "demand"
    height = zn["high"] - zn["low"]
    n = len(bars)

    def inside(b):
        return b["l"] <= zn["high"] if demand else b["h"] >= zn["low"]

    spans, m = [], max(first, 0)
    while m < n:
        if not inside(bars[m]):
            m += 1
            continue
        s = m
        while m < n and inside(bars[m]):
            m += 1
        spans.append((s, m - 1))
    out = []
    for i, (s, e) in enumerate(spans):
        stop = spans[i + 1][0] if i + 1 < len(spans) else n
        need = cfg["respect_atr"] * a[s] if a[s] else None
        result, result_t = ("no clear reaction" if i + 1 < len(spans) else "open"), None
        for k in range(s, stop):
            b = bars[k]
            if (b["c"] < zn["low"]) if demand else (b["c"] > zn["high"]):
                result, result_t = "closed through", b["t"]
                break
            if need is not None and k > s and ((b["h"] >= zn["high"] + need) if demand else (b["l"] <= zn["low"] - need)):
                result, result_t = "held", b["t"]
                break
        if demand:
            reach = zn["high"] - min(x["l"] for x in bars[s:e + 1])
        else:
            reach = max(x["h"] for x in bars[s:e + 1]) - zn["low"]
        out.append({"t": bars[s]["t"], "end_t": bars[e]["t"], "session": session_of(bars[s]["t"], cfg),
                    "depth": round(reach / height, 2) if height > 0 else 0.0, "result": result, "result_t": result_t})
    return out[-MAX_TOUCHES:]


def find_zones(bars, cfg):
    """Supply and demand zones in one chart's bars, with whether price has come back."""
    n = len(bars)
    a = atr_series(bars, cfg["atr_len"])
    ph, pl = pivots(bars, cfg["swing_n"])
    sh, sl = last_known(ph, n), last_known(pl, n)
    first = cfg["atr_len"] + 2 * cfg["swing_n"] + 2
    zones, seen = [], set()

    def add(k, up, di):
        seen.add((k, up))
        zones.append({"kind": "demand" if up else "supply", "t": bars[k]["t"], "disp_t": bars[di]["t"],
                      "high": bars[k]["h"], "low": bars[k]["l"], "di": di, "touched_t": None,
                      "status": "fresh"})

    for i in range(first, n):
        up = displacement_direction(bars, a, sh, sl, i, cfg)
        if up is None:
            continue
        cands = base_candidates(bars, a, i, up, cfg)
        if cands:
            k = cands[0] if cfg["zone_candle"] == "last" else pick_extreme(bars, cands, up)
            if (k, up) not in seen:
                add(k, up, i)
    # a slow move away: no single big candle, but the candle is the top or bottom of the area
    for k in range(first, n - 1):
        if not is_base(bars, a, k, cfg):
            continue
        for up in (True, False):
            kind = "demand" if up else "supply"
            if (k, up) in seen:
                continue
            if any(zz["kind"] == kind and k < zz["di"] <= k + cfg["away_bars"] for zz in zones):
                continue    # that move already has its zone
            j, _, _ = gradual_away(bars, a, k, up, cfg)
            if j is not None and is_extreme_of_area(bars, a, k, j, up, cfg):
                add(k, up, j)
    zones.sort(key=lambda zz: (zz["di"], zz["t"]))
    for z in zones:
        for m in range(z["di"] + 1, n):
            hit = bars[m]["l"] <= z["high"] if z["kind"] == "demand" else bars[m]["h"] >= z["low"]
            if hit:
                z["touched_t"], z["status"] = bars[m]["t"], "touched"
                break
        z["respect"], z["respect_t"] = respect_state(bars, a, z, cfg)
        z["touches"] = zone_touches(bars, a, z, cfg, z["di"] + 1)
    return zones


def limit_zones(zones, now, cfg):
    """Keep zones from the last `days` days, the latest `max_per_day` per New York day,
    and (when asked) only zone candles that opened in a session window."""
    today = to_eastern(now).date()
    first = today - datetime.timedelta(days=cfg["days"] - 1)
    by_day = {}
    for z in zones:
        day = to_eastern(z["t"]).date()
        if day < first or day > today:
            continue
        if cfg["zones_in_sessions"] and not in_session(z["t"], cfg):
            continue
        by_day.setdefault(day, []).append(z)
    out = []
    for day in by_day:
        out.extend(sorted(by_day[day], key=lambda z: z["di"])[-cfg["max_per_day"]:])
    return sorted(out, key=lambda z: z["t"])


# ---------------------------------------------------------------- entry

STAGES = ["no zone touched yet",
          "zone touched, no sweep of a swing low yet",
          "swept, no FVG or IFVG with its midpoint in the OTE",
          "FVG/IFVG in the OTE, price has not come back into it",
          "price is in the gap, no 3-minute CHoCH yet",
          "CHoCH came outside the sessions or the reward to risk was too small",
          "triggered"]


STAGES_YOURS = ["no zone touched yet",
                "zone touched, no leg off it yet",
                "leg drawn, price has not retraced to the fib yet",
                "price reached the fib, no gap has inverted yet",
                "a gap inverted but the entry was outside the sessions or the reward to risk was too small",
                "triggered"]


def ote_band(lo, hi):
    """The 62% to 79% retracement of a leg from lo up to hi, as (low edge, high edge)."""
    rng = hi - lo
    return hi - 0.79 * rng, hi - 0.62 * rng


def gaps_after(m5, j, cfg, a5):
    """Bullish FVGs, and bearish FVGs closed back above (IFVGs), formed after bar j.
    Each is (available_index, low, high, kind)."""
    out = []
    for i in range(j + 2, len(m5)):
        if a5[i] is None:
            continue
        g = cfg["fvg_min_atr"] * a5[i]
        if m5[i]["l"] - m5[i - 2]["h"] >= g:
            out.append((i, m5[i - 2]["h"], m5[i]["l"], "FVG"))
        if m5[i - 2]["l"] - m5[i]["h"] >= g:
            lo, hi = m5[i]["h"], m5[i - 2]["l"]
            for m in range(i + 1, len(m5)):
                if m5[m]["c"] > hi:
                    out.append((m, lo, hi, "IFVG"))
                    break
    return sorted(out)


def m3_choch(m3, ph3, from_idx, sweep_idx, stop_low, need_trend=False):
    """First 3-minute close above the last swing high made after the sweep; None if the sweep low
    breaks first. With need_trend that swing must also be a lower high than the one before it."""
    for k in range(from_idx, len(m3)):
        if m3[k]["l"] < stop_low:
            return None
        known = [p for p in ph3 if p[0] < k and p[1] >= sweep_idx]
        if not known:
            continue
        _, pidx, price = known[-1]
        before = [q for q in ph3 if q[1] < pidx]
        if need_trend and not (before and before[-1][2] > price):
            continue
        if m3[k]["c"] > price:
            return k
    return None


def m3_choch_state(m3, ph3, from_idx, sweep_idx, stop_low, need_trend=False):
    """What the CHoCH check sees at the latest 3-minute bar: the swing it would break, whether that
    swing is a lower high, the latest close, and whether the sweep low went first."""
    n = len(m3)
    broken = None
    for k in range(from_idx, n):
        if m3[k]["l"] < stop_low:
            broken = m3[k]["t"]
            break
    state = {"pivot": None, "pivot_t": None, "lower_high": None, "close": m3[-1]["c"],
             "close_t": m3[-1]["t"], "broken_t": broken}
    known = [p for p in ph3 if p[0] < n and p[1] >= sweep_idx]
    if known:
        _, pidx, price = known[-1]
        before = [q for q in ph3 if q[1] < pidx]
        state.update(pivot=price, pivot_t=m3[pidx]["t"],
                     lower_high=bool(before and before[-1][2] > price) if need_trend else True)
    return state


def leg_origin(m5, ph5, pl5, j, default):
    """Where the leg starts: the last swing low made after the sweep before price breaks up, that is,
    before the first 5-minute close above the latest swing high made after the sweep. Falls back to
    `default` (the sweep low) when there is no such swing low or no break."""
    for b in range(j + 1, len(m5)):
        highs = [p for p in ph5 if p[0] < b and p[1] > j]
        if highs and m5[b]["c"] > highs[-1][2]:
            lows = [p for p in pl5 if p[0] < b and p[1] > j]
            return lows[-1][2] if lows else default
    return default


def long_setups(zone, m5, m3, targets, cfg, trace=None):
    """Every long entry for one demand zone, and how far the best attempt got (an index into STAGES).
    When `trace` is a list, each sweep is appended to it with the gaps looked at after it."""
    best = 0
    out = []
    if zone["touched_t"] is None or len(m5) < 10 or len(m3) < 10:
        return out, best
    best = 1
    a5 = atr_series(m5, cfg["atr_len"])
    ph5, pl5 = pivots(m5, cfg["swing_n"])
    ph3, _ = pivots(m3, cfg["swing_n"])
    t5 = [b["t"] for b in m5]
    t3 = [b["t"] for b in m3]
    for j in range(bisect.bisect_left(t5, zone["touched_t"]), len(m5)):
        prior = [p for p in pl5 if p[0] < j]
        if not prior or a5[j] is None:
            continue
        swing = prior[-1][2]
        sw = m5[j]
        if not (sw["l"] < swing and sw["c"] > swing and sw["l"] <= zone["high"]):
            continue
        best = max(best, 2)
        rec = {"t": sw["t"], "swing": swing, "extreme": sw["l"], "gaps": []}
        if trace is not None:
            trace.append(rec)
        origin = leg_origin(m5, ph5, pl5, j, sw["l"]) if cfg["leg_from_swing"] else sw["l"]
        for avail, lo, hi, kind in gaps_after(m5, j, cfg, a5):
            # price coming back into the gap, without taking out the sweep low first
            touch = None
            for m in range(avail + 1, len(m5)):
                if m5[m]["l"] < sw["l"]:
                    break
                if m5[m]["l"] <= hi:
                    touch = m
                    break
            # a gap left behind by the turn itself: price was already back in the band when it formed
            turn = False
            if cfg["gap_at_turn"] and kind == "FVG" and avail - 1 > j:
                mid = avail - 1
                pre = m5[j:mid]
                if pre and min(b["l"] for b in m5[j + 1:avail]) >= sw["l"]:
                    top = max(range(len(pre)), key=lambda x: (pre[x]["h"], -x))
                    band_hi = ote_band(origin, pre[top]["h"])[1]
                    if any(m5[m]["l"] <= band_hi for m in range(j + top + 1, mid)):
                        touch, turn = mid, True
            # the leg is measured when price comes back, or as it stands now if it has not
            leg_hi = max(b["h"] for b in m5[j:(len(m5) if touch is None else touch)])
            ote_lo, ote_hi = ote_band(origin, leg_hi)
            inside = ote_lo <= (lo + hi) / 2.0 <= ote_hi
            gd = {"kind": kind, "t": t5[avail], "lo": lo, "hi": hi, "ote": (ote_lo, ote_hi),
                  "inside": inside, "touched": touch is not None, "turn": turn}
            rec["gaps"].append(gd)
            if not inside:
                continue
            best = max(best, 3)
            if touch is None:
                continue
            best = max(best, 4)
            gd["choch"] = m3_choch_state(m3, ph3, bisect.bisect_left(t3, t5[touch]),
                                         bisect.bisect_left(t3, sw["t"]), sw["l"], cfg["choch_needs_trend"])
            k = m3_choch(m3, ph3, bisect.bisect_left(t3, t5[touch]), bisect.bisect_left(t3, sw["t"]), sw["l"],
                         cfg["choch_needs_trend"])
            if k is None:
                continue
            best = max(best, 5)
            when = t3[k] + 180
            entry = m3[k]["c"]
            base = origin if cfg["stop_at"] == "leg_low" else sw["l"]
            stop = base - cfg["sweep_buffer_atr"] * a5[touch]
            risk = entry - stop
            if risk <= 0 or not in_session(when, cfg):
                continue
            if cfg["target_r"]:
                target, rr = entry + cfg["target_r"] * risk, float(cfg["target_r"])
            else:
                above = [x for x in targets if x > entry]
                target = min(above) if above else None
                rr = (target - entry) / risk if target is not None else None
            if rr is not None and rr < cfg["min_rr"]:
                continue
            if any(o["entry_t"] == when for o in out):
                break                      # the same trade seen from an earlier sweep
            best = 6
            out.append({"trigger": kind, "sweep_t": sw["t"], "sweep_low": sw["l"], "gap": (lo, hi),
                        "ote": (ote_lo, ote_hi), "entry_t": when, "entry": entry, "stop": stop,
                        "target": target, "rr": rr})
            break
    return out, best


def yours_long(zone, m5, cfg):
    """The user's own entry for a demand zone: the leg off the zone, the fib, a gap that inverts, a tight stop.
    1. A leg starts at the latest swing low at the zone (the last time price was there before it moved away); the
       highest high after it is the end (both known as they happen, so nothing looks ahead). A wick that stops short of the zone by up to
       `leg_zone_near` zone heights still counts. It has to be at least `leg_min_atr` 5-minute ATRs
       long, finish within `leg_max_bars` bars of its start, and start no further under the zone than
       `leg_zone_slack` zone heights (a zone is reused after it breaks, but within reason).
    2. Price retraces down to the `fib_reach` line (0.705). A close beyond `fib_deep` (0.79), or a low under the
       leg's start, means the fib was not respected and this leg is dropped.
    3. A bearish FVG (a gap down) that sits in the 0.79 to 0.5 band. Once price has reached the fib, the first
       5-minute close back above the gap inverts it (an IFVG): that close is the entry. The first later bar that
       comes back into the gap and closes above it again (the retest) is a second entry on the same gap. One gap
       is traded per leg.
    4. The stop sits just under the lowest low since price reached the fib, which is where it was respected; the
       target is `target_r` times the risk (3 means 1:3).
    Supply zones run the same code on mirrored prices. Returns (entries, furthest stage in STAGES_YOURS)."""
    out, best = [], 0
    if zone["touched_t"] is None or len(m5) < 10:
        return out, best
    best = 1
    a5 = atr_series(m5, cfg["atr_len"])
    t5 = [b["t"] for b in m5]
    n = len(m5)
    rr_fixed = float(cfg["target_r"] or 3.0)
    slack = cfg["leg_zone_slack"] * (zone["high"] - zone["low"])
    near = cfg["leg_zone_near"] * (zone["high"] - zone["low"])

    def attempt(L, H, lidx, hidx, reach):
        """Walk forward from the bar price reached the fib. Returns (entries, where the leg search carries on,
        whether the leg's start is kept for the next leg)."""
        nonlocal best
        R = H - L
        f_reach, f_deep, f_half = H - cfg["fib_reach"] * R, H - cfg["fib_deep"] * R, H - 0.5 * R
        gaps, found, low_since, focus = [], [], None, None
        for j in range(hidx + 1, n):
            b = m5[j]
            if j >= reach:
                low_since = b["l"] if low_since is None else min(low_since, b["l"])
                if b["l"] < L:
                    return found, j, False
                if b["c"] < f_deep:
                    return found, j + 1, True
                if b["h"] > H and j > reach:
                    return found, j, True
            if j >= hidx + 2 and a5[j] is not None and m5[j - 2]["l"] - b["h"] >= cfg["fvg_min_atr"] * a5[j]:
                lo, hi = b["h"], m5[j - 2]["l"]
                if hi >= f_deep and lo <= f_half:
                    gaps.append({"i": j, "lo": lo, "hi": hi, "inv": None, "done": False})
            for g in gaps:
                if g["i"] >= j or g["done"] or (focus is not None and g is not focus):
                    continue
                trigger = None
                if g["inv"] is None:
                    if b["c"] > g["hi"]:
                        if j >= reach:
                            g["inv"], trigger = j, "IFVG inverted"
                        else:
                            g["done"] = True               # it inverted before price got to the fib: not this gap
                elif b["c"] < g["lo"]:
                    g["done"] = True                       # closed back through it: no retest to wait for
                elif b["l"] <= g["hi"] and b["c"] > g["hi"] and j > g["inv"]:
                    g["done"], trigger = True, "IFVG retest"
                if trigger is None:
                    continue
                best = max(best, 4)
                when = t5[j] + 300
                entry = b["c"]
                stop = low_since - cfg["sweep_buffer_atr"] * a5[j]
                risk = entry - stop
                if risk <= 0 or not in_session(when, cfg):
                    continue
                target, rr = entry + rr_fixed * risk, rr_fixed
                if rr < cfg["min_rr"] or any(o["entry_t"] == when for o in found):
                    continue
                best = 5
                focus = g
                found.append({"trigger": trigger, "style": "yours", "sweep_t": t5[reach], "sweep_low": low_since,
                              "gap": (g["lo"], g["hi"]), "ote": (f_deep, f_reach), "entry_t": when, "entry": entry,
                              "stop": stop, "target": target, "rr": rr,
                              "fib": {"start": L, "end": H, "l705": f_reach, "l79": f_deep,
                                      "t0": t5[lidx], "t1": t5[hidx]}})
            if focus is not None and focus["done"]:
                return found, j + 1, True                  # this gap has had its inversion and its retest
        return found, n, True

    _, pl5 = pivots(m5, cfg["swing_n"])
    start = bisect.bisect_left(t5, zone["touched_t"])
    pp = 0
    L = Lidx = H = Hidx = None
    i = start
    while i < n:
        b = m5[i]
        while pp < len(pl5) and pl5[pp][0] <= i:           # a swing low at the zone, now confirmed, starts a leg
            _, idx, price = pl5[pp]
            pp += 1
            if idx >= start and zone["low"] - slack <= price <= zone["high"] + near:
                L, Lidx, H, Hidx = price, idx, None, None
                for k in range(idx + 1, i + 1):
                    if H is None or m5[k]["h"] > H:
                        H, Hidx = m5[k]["h"], k
        if L is None:
            i += 1
            continue
        if b["l"] < L:                                     # the start of the leg was taken out: wait for a new one
            L = H = Hidx = None
            i += 1
            continue
        if H is None or b["h"] > H:
            H, Hidx = b["h"], i
            if Hidx - Lidx > cfg["leg_max_bars"]:          # too long to be the leg off the zone
                L = H = Hidx = None
            i += 1
            continue
        if a5[i] is None or H - L < cfg["leg_min_atr"] * a5[i]:
            i += 1
            continue
        best = max(best, 2)                                # a leg is drawn
        if b["l"] > H - cfg["fib_reach"] * (H - L):        # and price has not retraced to the fib yet
            i += 1
            continue
        best = max(best, 3)
        found, i, keep = attempt(L, H, Lidx, Hidx, i)
        out.extend(f for f in found if not any(o["entry_t"] == f["entry_t"] for o in out))
        if keep:
            H, Hidx = None, None                           # the leg's start stays: a new leg can grow from it
        else:
            L = H = Hidx = None
    return out, best


def _unmirror_gap(g):
    out = dict(g, lo=-g["hi"], hi=-g["lo"], ote=(-g["ote"][1], -g["ote"][0]))
    if "choch" in g:
        c = dict(g["choch"], close=-g["choch"]["close"])
        if c["pivot"] is not None:
            c["pivot"] = -c["pivot"]
        out["choch"] = c
    return out


def setups_for(zone, m5, m3, fresh_other, cfg, trace=None):
    """Run the long logic for a demand zone, or for a supply zone on mirrored prices.
    `trace`, when given, receives the sweeps and gaps looked at, in real (not mirrored) prices."""
    if zone["kind"] == "demand" and cfg["entry_style"] == "yours":
        res, best = yours_long(zone, m5, cfg)
        for r in res:
            r["side"] = "long"
        return res, best
    ph5, pl5 = pivots(m5, cfg["swing_n"])
    if zone["kind"] == "demand":
        targets = [z["low"] for z in fresh_other if z["kind"] == "supply"] + [p[2] for p in ph5]
        res, best = long_setups(zone, m5, m3, targets, cfg, trace)
        for r in res:
            r["side"] = "long"
        return res, best
    mirrored = dict(zone, high=-zone["low"], low=-zone["high"])
    if cfg["entry_style"] == "yours":
        res, best = yours_long(mirrored, flip_bars(m5), cfg)
        for r in res:
            r["side"] = "short"
            r["sweep_low"] = -r["sweep_low"]
            r["gap"] = (-r["gap"][1], -r["gap"][0])
            r["ote"] = (-r["ote"][1], -r["ote"][0])
            r["entry"], r["stop"] = -r["entry"], -r["stop"]
            r["target"] = -r["target"]
            r["fib"] = {k: (v if k in ("t0", "t1") else -v) for k, v in r["fib"].items()}      # prices flip, times do not
        return res, best
    targets = [-z["high"] for z in fresh_other if z["kind"] == "demand"] + [-p[2] for p in pl5]
    mirror_trace = [] if trace is not None else None
    res, best = long_setups(mirrored, flip_bars(m5), flip_bars(m3), targets, cfg, mirror_trace)
    for rec in mirror_trace or []:
        trace.append({"t": rec["t"], "swing": -rec["swing"], "extreme": -rec["extreme"], "gaps": [
            _unmirror_gap(g) for g in rec["gaps"]]})
    for r in res:
        r["side"] = "short"
        r["sweep_low"] = -r["sweep_low"]
        r["gap"] = (-r["gap"][1], -r["gap"][0])
        r["ote"] = (-r["ote"][1], -r["ote"][0])
        r["entry"], r["stop"] = -r["entry"], -r["stop"]
        r["target"] = None if r["target"] is None else -r["target"]
    return res, best


# ---------------------------------------------------------------- config and output

def read_config(path):
    cfg = json.loads(json.dumps(DEFAULTS))
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            raise ZoneError("%s is not valid JSON: %s" % (path, e))
        over = doc.get("zones") or {}
        for k, v in over.items():
            if k == "sessions" and isinstance(v, dict):
                cfg["sessions"].update(v)
            elif k in cfg:
                cfg[k] = v
    if cfg["session_tz"] not in ("dublin", "ny"):
        raise ZoneError('session_tz must be "dublin" or "ny"')
    if cfg["zone_candle"] not in ("extreme", "last"):
        raise ZoneError('zone_candle must be "extreme" or "last"')
    if cfg["entry_style"] not in ("tool", "yours"):
        raise ZoneError('entry_style must be "tool" or "yours"')
    if cfg["stop_at"] not in ("sweep", "leg_low"):
        raise ZoneError('stop_at must be "sweep" or "leg_low"')
    return cfg


def px(sym, x):
    return "-" if x is None else "%.*f" % (MARKETS[sym]["digits"], x)


RESPECT_WORDS = {"respected": "respected", "failed": "failed, closed through it", "testing": "being tested"}


def zone_line(sym, tf, z):
    note = RESPECT_WORDS.get(z.get("respect"))
    return "%-6s %-4s zone candle %s ET (%s Dublin)  %s to %s  %s" % (
        z["kind"].upper(), tf, fmt_et(z["t"]), fmt_dublin(z["t"], z["t"]), px(sym, z["low"]), px(sym, z["high"]),
        z["status"] + (", " + note if note else ""))


def touch_lines(z):
    """One indented line per visit to a zone, in Dublin time."""
    out = []
    for i, tc in enumerate(z.get("touches") or [], 1):
        res = tc["result"]
        if tc.get("result_t"):
            res += " " + fmt_dublin(tc["result_t"], tc["result_t"])
        depth = "through it" if tc["depth"] >= 1.0 else "%d%% in" % round(tc["depth"] * 100)
        out.append("    touch %d: %s Dublin (%s), %s, %s" % (i, fmt_dublin(tc["t"], tc["t"]), tc["session"] or "no session",
                                                          depth, res))
    return out


def stage_line(sym, z, best, names=None):
    """One touched zone and the furthest step of the entry it reached."""
    text = (names or STAGES)[best]
    if z["kind"] == "supply":
        text = text.replace("swing low", "swing high")     # the supply side is the mirror image
    return "  %-6s %-4s %s ET (%s Dublin)  %s to %s: %s" % (
        z["kind"].upper(), z["tf"], fmt_et(z["t"]), fmt_dublin(z["t"], z["t"]), px(sym, z["low"]),
        px(sym, z["high"]), text)


def choch_line(sym, z, c):
    """The 3-minute CHoCH check for one gap price has come back to, in plain words."""
    short = z["kind"] == "supply"
    swing, level, way = ("low", "higher low", "below") if short else ("high", "lower high", "above")
    pre = "          3-minute: "
    if c["broken_t"] is not None:
        return pre + "price traded beyond the sweep extreme at %s Dublin, so this attempt is cancelled" % (
            fmt_dublin(c["broken_t"], c["broken_t"]))
    if c["pivot"] is None:
        return pre + "no swing %s after the sweep yet, so there is no CHoCH level; latest close %s (%s Dublin)" % (
            swing, px(sym, c["close"]), fmt_dublin(c["close_t"], c["close_t"]))
    note = "" if c["lower_high"] else ", but it is not a %s so it cannot be the CHoCH level" % level
    return pre + "last swing %s after the sweep %s (%s Dublin)%s; latest close %s (%s Dublin); a close %s %s is the CHoCH" % (
        swing, px(sym, c["pivot"]), fmt_dublin(c["pivot_t"], c["pivot_t"]), note, px(sym, c["close"]),
        fmt_dublin(c["close_t"], c["close_t"]), way, px(sym, c["pivot"]))


def trace_lines(sym, z, trace, max_sweeps=3, max_gaps=4):
    """What the entry logic saw after a zone was touched: each sweep and the gaps after it."""
    out = []
    word = "high" if z["kind"] == "supply" else "low"
    for rec in trace[-max_sweeps:]:
        out.append("      sweep %s ET (%s Dublin): took the swing %s %s, extreme %s, closed back inside"
                   % (fmt_et(rec["t"]), fmt_dublin(rec["t"], rec["t"]), word, px(sym, rec["swing"]),
                      px(sym, rec["extreme"])))
        if not rec["gaps"]:
            out.append("        no FVG or IFVG formed after it")
        for g in rec["gaps"][:max_gaps]:
            mid = (g["lo"] + g["hi"]) / 2.0
            tail = ("formed as price turned back from the band" if g.get("turn") else "price has come back") \
                if g.get("touched") else "price has not come back yet"
            out.append("        %s %s to %s (%s Dublin), midpoint %s, band %s to %s: %s, %s"
                       % (g["kind"], px(sym, g["lo"]), px(sym, g["hi"]), fmt_dublin(g["t"], rec["t"]),
                          px(sym, mid), px(sym, g["ote"][0]), px(sym, g["ote"][1]),
                          "inside" if g["inside"] else "outside", tail))
            if g.get("choch") and g["inside"]:
                out.append(choch_line(sym, z, g["choch"]))
        if len(rec["gaps"]) > max_gaps:
            out.append("        and %d more" % (len(rec["gaps"]) - max_gaps))
    return out


def collect_zones(sym, tfs, now, cfg, offline):
    """The zones for each timeframe. When cfg["union_with"] holds a dict of settings, the zones those settings
    find are added too (a zone both ways is listed once, as the main settings found it)."""
    found = []
    cfgs = [cfg]
    if cfg.get("union_with"):
        cfgs.append(dict(cfg, union_with=None, **cfg["union_with"]))
    for tf in tfs:
        bars = closed_only(get_bars(sym, tf, cfg["days"], offline), INTERVAL_SECS[tf], now)
        seen = set()
        for c in cfgs:
            for z in limit_zones(find_zones(bars, c), now, c):
                key = (z["kind"], z["t"], z["low"], z["high"])
                if key in seen:
                    continue
                seen.add(key)
                z["tf"] = tf
                found.append(z)
    return sorted(found, key=lambda z: z["t"])


def explain_lines(sym, bars, cfg, k):
    """Why the candle at index k is, or is not, a zone: every number the rule looks at."""
    n = len(bars)
    a = atr_series(bars, cfg["atr_len"])
    ph, pl = pivots(bars, cfg["swing_n"])
    sh, sl = last_known(ph, n), last_known(pl, n)
    c = bars[k]
    out = ["%s candle %s ET  open %s high %s low %s close %s"
           % (sym, fmt_et(c["t"]), px(sym, c["o"]), px(sym, c["h"]), px(sym, c["l"]), px(sym, c["c"]))]
    if a[k] is None or a[k] <= 0:
        out.append("Not enough bars before it to work out the ATR, so it cannot be checked.")
        return out
    rng, body = c["h"] - c["l"], abs(c["c"] - c["o"])
    wick = (rng - body) / rng if rng > 0 else 0.0
    out.append("ATR(%d) there: %s" % (cfg["atr_len"], px(sym, a[k])))
    out.append("Body %.2f x ATR (a zone candle needs under %.2f): %s"
               % (body / a[k], cfg["base_body_atr"], "ok" if body < cfg["base_body_atr"] * a[k] else "TOO BIG"))
    out.append("Wicks are %.0f%% of its range (needs over %.0f%%): %s"
               % (100 * wick, 100 * cfg["base_wick"], "ok" if wick > cfg["base_wick"] else "TOO SHORT"))
    sess = session_of(c["t"], cfg)
    out.append("Opened at %s Dublin time. In an Asia, London or New York window: %s"
               % (fmt_dublin(c["t"], c["t"]),
                  "yes (%s)" % sess if sess else "NO, so zones_in_sessions drops it"))
    if not is_base(bars, a, k, cfg):
        out.append("Result: not a zone candle by the rule, so nothing after it is checked.")
        return out
    first = cfg["atr_len"] + 2 * cfg["swing_n"] + 2
    out.append("Next %d bars, looking for one big displacement candle:" % cfg["base_lookback"])
    reasons = []
    disp = None
    for i in range(k + 1, min(k + cfg["base_lookback"], n - 1) + 1):
        b = bars[i]
        brng, bbody = b["h"] - b["l"], abs(b["c"] - b["o"])
        up = b["c"] > b["o"]
        if i < first or a[i] is None or a[i] <= 0:
            out.append("  %s  too early in the data for the ATR and swings" % fmt_et(b["t"]))
            continue
        why = []
        if bbody < cfg["disp_atr"] * a[i]:
            why.append("body %.2f x ATR, needs %.2f" % (bbody / a[i], cfg["disp_atr"]))
        if brng <= 0 or bbody < cfg["disp_body"] * brng:
            why.append("body is %.0f%% of its range, needs %.0f%%"
                       % (100 * bbody / brng if brng > 0 else 0, 100 * cfg["disp_body"]))
        ref = sh[i - 1] if up else sl[i - 1]
        if ref is None or not (b["c"] > ref if up else b["c"] < ref):
            why.append("close %s does not pass the latest swing %s %s"
                       % (px(sym, b["c"]), "high" if up else "low", px(sym, ref)))
        line = "  %s  %s  " % (fmt_et(b["t"]), "up  " if up else "down")
        if why:
            out.append(line + "not a displacement: " + "; ".join(why))
            continue
        out.append(line + "displacement")
        disp = (i, up)
        break
    if disp is not None:
        i, up = disp
        cands = base_candidates(bars, a, i, up, cfg)
        if k not in cands:
            move = (max(x["h"] for x in bars[k + 1:i + 1]) - c["h"]) if up else (c["l"] - min(x["l"] for x in bars[k + 1:i + 1]))
            reasons.append("the move away is %.2f x ATR, needs %.2f" % (move / a[i], cfg["move_atr"]))
        else:
            out.append("  move away is long enough (needs %.2f x ATR)" % cfg["move_atr"])
            pick = pick_extreme(bars, cands, up)
            if pick != k:
                reasons.append("the candle at %s ET is the %s in the same move, so the tool takes that one"
                               % (fmt_et(bars[pick]["t"]), "lowest low" if up else "highest high"))
    else:
        out.append("No displacement candle. Slow move check (a zone can also be a pause that price then leaves slowly: "
                   "then price drifts %.1f x ATR away within %d bars):" % (cfg["away_atr"], cfg["away_bars"]))
        slow = False
        for up in (True, False):
            j, dist, why = gradual_away(bars, a, k, up, cfg)
            side = "demand (price rises)" if up else "supply (price falls)"
            if j is None:
                out.append("  %s: no, best %.2f x ATR away (needs %.2f); %s" % (side, dist, cfg["away_atr"], why))
                continue
            ext = is_extreme_of_area(bars, a, k, j, up, cfg)
            out.append("  %s: %.2f x ATR away by %s ET; price stays %s the candle's %s until then: %s"
                       % (side, dist, fmt_et(bars[j]["t"]), "above" if up else "below",
                          "low" if up else "high", "yes" if ext else "NO"))
            slow = slow or ext
        if not slow:
            reasons.append("no big displacement and no slow move away that the rule accepts")
    listed = [zz for zz in find_zones(bars, cfg) if zz["t"] == c["t"]]
    if listed:
        out.append("Result: this IS a zone by the rule (%s)." % listed[0]["kind"])
    else:
        why = "; ".join(reasons) or "another zone in the same move already claimed it"
        out.append("Result: not a zone: %s." % why)
    return out


def spot_shift(sym, spot, now, offline, cfg):
    """Points to take off the data so its latest close equals `spot`, the price on the user's own chart.
    Futures trade above spot, and the gap moves a little from day to day, so a fresh --spot keeps it true.
    Spot forex pairs need no shift: their prices already line up with a chart to within a pip or two."""
    if MARKETS[sym].get("spot"):
        print("%s data is already spot, so --spot is not applied." % sym, file=sys.stderr)
        return 0.0
    bars = closed_only(_raw_bars(sym, "1m", cfg["days"], offline), 60, now)
    if not bars:
        raise ZoneError("no recent 1-minute bars for %s to line up with --spot" % sym)
    shift = bars[-1]["c"] - spot
    print("Prices shifted by %s so the latest close %s (%s ET) reads %s, your spot price."
          % (px(sym, shift), px(sym, bars[-1]["c"]), fmt_et(bars[-1]["t"]), px(sym, spot)), file=sys.stderr)
    return shift


def cmd_explain(args, cfg, now):
    sym = resolve(args.symbol)
    secs = INTERVAL_SECS[args.tf]
    bars = closed_only(get_bars(sym, args.tf, 5, args.offline), secs, now)
    at = parse_now(args.at, "--at")
    k = None
    for i, b in enumerate(bars):
        if b["t"] <= at < b["t"] + secs:
            k = i
    if k is None:
        raise ZoneError("no %s bar at %s ET in the data Yahoo sent (it keeps about a month, and weekends have no bars)"
                        % (args.tf, args.at))
    for line in explain_lines(sym, bars, cfg, k):
        print(line)
    return 0


def bar_lines(sym, bars, cfg, last):
    """The last `last` bars as text, Dublin time, with the swing highs and lows the rules use marked."""
    ph, pl = pivots(bars, cfg["swing_n"])
    high = {j for _, j, _ in ph}
    low = {j for _, j, _ in pl}
    out = []
    for i in range(max(0, len(bars) - last), len(bars)):
        b = bars[i]
        marks = ", ".join(m for m, hit in (("swing high", i in high), ("swing low", i in low)) if hit)
        out.append("%s  open %s  high %s  low %s  close %s  %s" % (
            fmt_dublin(b["t"], b["t"]).rjust(9), px(sym, b["o"]), px(sym, b["h"]), px(sym, b["l"]), px(sym, b["c"]),
            marks))
    return out


def cmd_bars(args, cfg, now):
    sym = resolve(args.symbol)
    secs = INTERVAL_SECS[args.tf]
    if args.tf == "3m":
        bars = resample(get_bars(sym, "1m", cfg["days"], args.offline), 180)
    else:
        bars = get_bars(sym, args.tf, cfg["days"], args.offline)
    bars = closed_only(bars, secs, now)
    if not bars:
        raise ZoneError("no %s bars for %s" % (args.tf, sym))
    print("%s %s bars, Dublin time, last %d as of %s ET (a swing is marked once %d bars follow it)"
          % (sym, args.tf, min(args.last, len(bars)), fmt_et(now), cfg["swing_n"]))
    for line in bar_lines(sym, bars, cfg, args.last):
        print(line)
    return 0


def cmd_zones(args, cfg, now):
    sym = resolve(args.symbol)
    tfs = ["60m", "30m", "15m"] if args.tf == "all" else [args.tf]
    found = collect_zones(sym, tfs, now, cfg, args.offline)
    if args.hide_failed:
        found = [z for z in found if z.get("respect") != "failed"]
    if args.json:
        print(json.dumps(found, indent=2))
    else:
        print("%s (%s)  zones from the last %d day(s), at most %d per day, as of %s ET"
              % (sym, MARKETS[sym]["yahoo"], cfg["days"], cfg["max_per_day"], fmt_et(now)))
        for z in found:
            print(zone_line(sym, z["tf"], z))
            if args.touches:
                for line in touch_lines(z):
                    print(line)
        if not found:
            print("No zones. The rules are strict: a small-bodied, long-wicked candle, then a strong move away.")
    if args.write:
        write_doc(args.out, {"symbol": sym, "as_of": now, "zones": found})
    return 0 if found else 1


def merge_same_trade(results):
    """One trade found through several zones is listed once; the other zones go in "also"."""
    out, seen = [], {}
    for r in results:
        key = (r["side"], r["entry_t"], r["entry"])
        if key in seen:
            seen[key].setdefault("also", []).append(r["zone"])
        else:
            seen[key] = r
            out.append(r)
    return out


def flat_share(bars):
    """Share of bars whose open, high, low and close are all the same price."""
    if not bars:
        return 0.0
    return sum(1 for b in bars if b["o"] == b["h"] == b["l"] == b["c"]) / float(len(bars))


FLAT_NOTE = ("Note: Yahoo's 1-minute bars for %s carry one price each (no highs or lows), so the 3-minute "
             "candles are built from a few price samples and their wicks and swings are approximate.")


def dublin_to_ts(local):
    for off in (1, 0):
        utc = local - datetime.timedelta(hours=off)
        if dublin_offset(utc) == off:
            return int((utc - EPOCH).total_seconds())
    return int((local - EPOCH).total_seconds())


def parse_dublin(text, flag):
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return dublin_to_ts(datetime.datetime.strptime(text, f))
        except ValueError:
            pass
    raise ZoneError('%s wants Dublin time like "2026-10-28 02:00"' % flag)


def manual_zone(spec, m5, candle_secs=1800, flag="--zone", cfg=None):
    """A zone the user drew by hand: (kind, low, high, Dublin time of the candle). It counts as touched
    from the first 5-minute bar after that candle closes that trades back into it."""
    kind, low, high, when = spec
    if not isinstance(kind, str) or kind.lower() not in ("demand", "supply"):
        raise ZoneError('%s kind must be "demand" or "supply", not %r' % (flag, kind))
    kind = kind.lower()
    try:
        low, high = float(low), float(high)
    except (TypeError, ValueError):
        raise ZoneError("%s wants two prices, low then high" % flag)
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ZoneError("%s wants two prices, low then high" % flag)
    if low >= high:
        raise ZoneError("%s wants the low price first and it must be under the high price" % flag)
    if not isinstance(when, str):
        raise ZoneError('%s wants Dublin time like "2026-10-28 02:00"' % flag)
    t = parse_dublin(when, flag)
    z = {"kind": kind, "tf": "mine", "t": t, "high": high, "low": low, "di": None, "touched_t": None,
         "status": "fresh"}
    for b in m5:
        if b["t"] < t + candle_secs:
            continue
        if (b["l"] <= high) if kind == "demand" else (b["h"] >= low):
            z["touched_t"], z["status"] = b["t"], "touched"
            break
    if cfg is not None:
        first = next((i for i, b in enumerate(m5) if b["t"] >= t + candle_secs), len(m5))
        z["touches"] = zone_touches(m5, atr_series(m5, cfg["atr_len"]), z, cfg, first)
    return z


def analyse(sym, cfg, now, offline, zone_specs=None, extra_specs=None):
    """Zones for a market and every entry found in them, as a dict the command line and the dashboard share.
    `zone_specs` replaces the tool's own zones with the user's; `extra_specs` adds the user's to them."""
    m5 = closed_only(get_bars(sym, "5m", cfg["days"], offline), 300, now)
    if zone_specs:
        zones = [manual_zone(spec, m5, cfg=cfg) for spec in zone_specs]
    else:
        zones = collect_zones(sym, ["60m", "30m", "15m"], now, cfg, offline)
        zones = zones + [manual_zone(spec, m5, cfg=cfg) for spec in (extra_specs or [])]
    m1 = closed_only(get_bars(sym, "1m", cfg["days"], offline), 60, now)
    m3 = closed_only(resample(m1, 180), 180, now)
    fresh = [z for z in zones if z["status"] == "fresh"]
    results, staged, best_all = [], [], 0
    for z in zones:
        if z["touched_t"] is None:
            continue
        trace = []
        res, best = setups_for(z, m5, m3, fresh, cfg, trace)
        staged.append((z, best, trace))
        best_all = max(best_all, best)
        for r in res:
            r["zone"] = {"tf": z["tf"], "kind": z["kind"], "low": z["low"], "high": z["high"], "t": z["t"]}
            results.append(r)
    results.sort(key=lambda r: r["entry_t"])
    results = merge_same_trade(results)
    return {"zones": zones, "fresh": fresh, "results": results, "staged": staged, "best": best_all, "m1": m1, "m5": m5,
            "stage_names": STAGES_YOURS if cfg["entry_style"] == "yours" else STAGES}


def cmd_setup(args, cfg, now):
    sym = resolve(args.symbol)
    found = analyse(sym, cfg, now, args.offline, args.zone)
    zones, fresh, results = found["zones"], found["fresh"], found["results"]
    staged, best_all, m1 = found["staged"], found["best"], found["m1"]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("%s (%s)  as of %s ET  zones: %d (%d fresh, %d touched)"
              % (sym, MARKETS[sym]["yahoo"], fmt_et(now), len(zones), len(fresh), len(zones) - len(fresh)))
        if flat_share(m1) > 0.9:
            print(FLAT_NOTE % sym)
        if not results:
            for z, best, trace in staged:
                print(stage_line(sym, z, best, found["stage_names"]))
                for line in trace_lines(sym, z, trace):
                    print(line)
        for r in results:
            z = r["zone"]
            rr = "no target found" if r["rr"] is None else "R:R %.1f" % r["rr"]
            print("%s  %s %s zone %s-%s  %s %s, %s %s-%s in the OTE  ENTRY %s ET at %s  stop %s  target %s  %s"
                  % (r["side"].upper(), z["tf"], z["kind"], px(sym, z["low"]), px(sym, z["high"]),
                     "price reached the fib" if r.get("style") == "yours" else "swept", fmt_et(r["sweep_t"]), r["trigger"], px(sym, r["gap"][0]), px(sym, r["gap"][1]),
                     fmt_et(r["entry_t"]), px(sym, r["entry"]), px(sym, r["stop"]), px(sym, r["target"]), rr))
            for o in r.get("also", []):
                print("        same trade also seen from the %s %s zone %s-%s"
                      % (o["tf"], o["kind"], px(sym, o["low"]), px(sym, o["high"])))
        if results:
            print("These are signals from past and current bars. Nothing was placed. Set the stop and size BEFORE any order.")
        else:
            print("No entry. Furthest any zone got: %s." % found["stage_names"][best_all])
    if args.write:
        write_doc(args.out, {"symbol": sym, "as_of": now, "setups": results})
    return 0 if results else 1


def write_doc(path, doc):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    print("wrote %s" % path)


def cmd_markets():
    for s in sorted(MARKETS):
        print("%-7s Yahoo %-9s %s" % (s, MARKETS[s]["yahoo"], MARKETS[s]["note"]))
    return 0


def parse_now(text, flag="--now"):
    if not text:
        return now_ts()
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return eastern_to_ts(datetime.datetime.strptime(text, f))
        except ValueError:
            pass
    raise ZoneError('%s wants New York time like "2026-10-26 08:15"' % flag)


def build_parser():
    p = argparse.ArgumentParser(prog="zones.py", description="ICT supply and demand zones and the 5m/3m entry")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("markets")
    for name in ("zones", "setup"):
        s = sub.add_parser(name)
        s.add_argument("symbol")
        s.add_argument("--now", help='New York time, e.g. "2026-10-26 08:15"')
        s.add_argument("--offline", help="folder with <SYMBOL>_<interval>.json saved Yahoo charts")
        s.add_argument("--save", help="folder to save the Yahoo charts into, so a run can be replayed with --offline")
        s.add_argument("--spot", type=float, help="the price on your own chart right now; the data is shifted to match it")
        if name == "setup":
            s.add_argument("--zone", nargs=4, action="append", metavar=("KIND", "LOW", "HIGH", "WHEN"),
                           help='a zone you drew yourself, used instead of the detected ones: demand or supply, '
                                'its low and high price, and the Dublin time of its candle, e.g. '
                                '--zone demand 29841.25 29881.25 "2026-10-28 02:00" (repeat for more)')
        s.add_argument("--config", default="trader/config.json")
        s.add_argument("--days", type=int)
        s.add_argument("--max-per-day", type=int)
        s.add_argument("--min-rr", type=float)
        s.add_argument("--json", action="store_true")
        s.add_argument("--write", action="store_true")
        s.add_argument("--out", default="trader/zones.json")
        if name == "zones":
            s.add_argument("--tf", default="all", choices=["all", "60m", "30m", "15m"])
            s.add_argument("--touches", action="store_true", help="list every visit price made to each zone")
            s.add_argument("--hide-failed", action="store_true",
                           help="leave out zones that price closed through before bouncing off them")
    e = sub.add_parser("explain", help="why one candle is or is not a zone")
    e.add_argument("symbol")
    e.add_argument("--at", required=True, help='New York time of the candle, e.g. "2026-10-27 02:00"')
    e.add_argument("--tf", default="30m", choices=["60m", "30m", "15m"])
    e.add_argument("--now", help='New York time, e.g. "2026-10-26 08:15"')
    e.add_argument("--offline", help="folder with <SYMBOL>_<interval>.json saved Yahoo charts")
    e.add_argument("--save", help="folder to save the Yahoo charts into, so a run can be replayed with --offline")
    e.add_argument("--spot", type=float, help="the price on your own chart right now; the data is shifted to match it")
    e.add_argument("--config", default="trader/config.json")
    b = sub.add_parser("bars", help="print recent candles in Dublin time with the swing highs and lows marked")
    b.add_argument("symbol")
    b.add_argument("--tf", default="3m", choices=["1m", "3m", "5m", "15m", "30m"])
    b.add_argument("--last", type=int, default=40, help="how many bars to print")
    b.add_argument("--now", help='New York time, e.g. "2026-10-26 08:15"')
    b.add_argument("--offline", help="folder with <SYMBOL>_<interval>.json saved Yahoo charts")
    b.add_argument("--save", help="folder to save the Yahoo charts into, so a run can be replayed with --offline")
    b.add_argument("--spot", type=float, help="the price on your own chart right now; the data is shifted to match it")
    b.add_argument("--config", default="trader/config.json")
    return p


def main(argv=None):
    global SAVE_DIR, SHIFT
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "markets":
            return cmd_markets()
        if args.cmd not in ("zones", "setup", "explain", "bars"):
            build_parser().print_help()
            return 2
        cfg = read_config(args.config)
        now = parse_now(args.now)
        SAVE_DIR = args.save
        SHIFT = 0.0
        if args.spot is not None:
            SHIFT = spot_shift(resolve(args.symbol), args.spot, now, args.offline, cfg)
        if args.cmd == "explain":
            return cmd_explain(args, cfg, now)
        if args.cmd == "bars":
            return cmd_bars(args, cfg, now)
        if args.days:
            cfg["days"] = args.days
        if args.max_per_day:
            cfg["max_per_day"] = args.max_per_day
        if args.min_rr is not None:
            cfg["min_rr"] = args.min_rr
        return cmd_zones(args, cfg, now) if args.cmd == "zones" else cmd_setup(args, cfg, now)
    except ZoneError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    finally:
        SAVE_DIR, SHIFT = None, 0.0


if __name__ == "__main__":
    sys.exit(main())
