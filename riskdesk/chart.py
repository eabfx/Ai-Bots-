"""Candles and zones for the Chart page.

The candles come from Yahoo (the same free feed as the prices, so futures run about 10 minutes late and forex is live).
The supply and demand zones are the ones trader-zones/zones.py finds on the chart's own bars. The zones and the daily/4h
levels you saved in the Zones app (my_zones.json, my_levels.json next to config.json) are drawn too. Read only: nothing is
ever written, and nothing here places an order.

Standard library only."""

import json
import os
import time

import zones as z

# timeframe -> (Yahoo interval, Yahoo range, bar seconds, bars to resample from the Yahoo bars (0 = none), bars sent to the page)
TIMEFRAMES = {
    "5m": ("5m", "5d", 300, 0, 320),
    "15m": ("15m", "1mo", 900, 0, 300),
    "1h": ("60m", "3mo", 3600, 0, 260),
    "4h": ("60m", "6mo", 14400, 14400, 220),
    "1d": ("1d", "2y", 86400, 0, 240),
}
SYMBOLS = ("XAUUSD", "NQ", "EURUSD", "GBPUSD", "MGC")
CACHE_SECS = 20
_CACHE = {}


class ChartError(z.ZoneError):
    pass


def _bars(sym, tf, offline=None, fetch=None, clock=time.time):
    interval, rng, secs, resample, _ = TIMEFRAMES[tf]
    if offline:
        raw = z._raw_bars(sym, interval if interval != "60m" else "60m", 14, offline)
    else:
        key, now = (sym, tf), clock()
        hit = _CACHE.get(key)
        if hit and now - hit[0] < CACHE_SECS:
            raw = hit[1]
        else:
            path = "/v8/finance/chart/%s?range=%s&interval=%s" % (z.MARKETS[sym]["yahoo"], rng, interval)
            raw = z.parse_chart((fetch or z.yahoo_get)(path), sym)
            _CACHE[key] = (now, raw)
    return z.resample(raw, resample) if resample else raw


def _saved(path, sym):
    """One market's list from a Zones app file (my_zones.json or my_levels.json). A missing or broken file is no list."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    items = doc.get(sym) if isinstance(doc, dict) else None
    out = []
    for m in items if isinstance(items, list) else []:
        if isinstance(m, dict) and isinstance(m.get("low"), (int, float)) and isinstance(m.get("high"), (int, float)) \
                and m["low"] < m["high"]:
            out.append({"kind": str(m.get("kind", ""))[:12], "frame": str(m.get("frame", ""))[:8],
                        "low": m["low"], "high": m["high"], "note": str(m.get("note", ""))[:80]})
    return out


def _zones(bars, cfg, secs):
    """The zones on this chart's own bars that still matter: not far behind, and the latest few of each kind."""
    if len(bars) < cfg["atr_len"] + 12:
        return []
    found = z.find_zones(bars, cfg)
    first = bars[0]["t"]
    keep = []
    for kind in ("demand", "supply"):
        mine = [zn for zn in found if zn["kind"] == kind and zn["t"] >= first]
        fresh = [zn for zn in mine if zn["status"] == "fresh"][-4:]        # zones price has not come back to are the ones that matter most
        used = [zn for zn in mine if zn["status"] != "fresh"][-2:]
        keep.extend(fresh + used)
    return [{"kind": zn["kind"], "t": zn["t"], "low": zn["low"], "high": zn["high"], "status": zn["status"],
             "touched_t": zn.get("touched_t"), "respect": bool(zn.get("respect"))} for zn in sorted(keep, key=lambda q: q["t"])]


def snapshot(sym, tf, config_path, offline=None, fetch=None, clock=time.time):
    """Everything the Chart page draws for one market and timeframe, as JSON-ready data. Errors raise ChartError."""
    sym = z.resolve(sym or "")
    if sym not in SYMBOLS:
        raise ChartError("pick one of %s" % ", ".join(SYMBOLS))
    if tf not in TIMEFRAMES:
        raise ChartError("timeframe must be one of %s" % ", ".join(TIMEFRAMES))
    secs, keep = TIMEFRAMES[tf][2], TIMEFRAMES[tf][4]
    bars = _bars(sym, tf, offline, fetch, clock)
    if not bars:
        raise ChartError("Yahoo sent no candles for %s" % sym)
    cfg = z.read_config(config_path)
    zs = _zones(bars, cfg, secs)
    bars = bars[-keep:]
    first = bars[0]["t"]
    folder = os.path.dirname(os.path.abspath(config_path))
    market = z.MARKETS[sym]
    return {"symbol": sym, "tf": tf, "secs": secs, "digits": market["digits"], "note": market["note"],
            "spot": bool(market.get("spot")), "at": int(clock()),
            "bars": [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in bars],
            "zones": [dict(zn) for zn in zs if zn["t"] >= first - secs * 40 or zn["status"] == "fresh"],
            "mine": _saved(os.path.join(folder, "my_zones.json"), sym),
            "levels": _saved(os.path.join(folder, "my_levels.json"), sym)}
