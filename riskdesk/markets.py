"""Markets page: live charts for any symbol you choose, and the layout you arrange them in.

Prices come from Yahoo (the same free feed as the rest of the dashboard: forex is live, futures run about ten minutes late).
Any Yahoo symbol works, for example GC=F (gold futures), NQ=F, EURUSD=X, ^GSPC, BTC-USD, NVDA. The layout (which charts, in
which order, which timeframe) is saved in markets.json next to the journal so the PC, the server and the phone each keep
their own. Read only: nothing here places an order.

Standard library only."""

import json
import os
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import zones as z

FILE = "markets.json"
# timeframe -> (Yahoo interval, Yahoo range, bars sent to the page)
TIMEFRAMES = {"1m": ("1m", "1d", 400), "5m": ("5m", "5d", 300), "15m": ("15m", "5d", 260), "1h": ("60m", "1mo", 240), "1d": ("1d", "1y", 260)}
STYLES = ("area", "candles")
GRIDS = {"1x1": 1, "2x1": 2, "2x2": 4, "3x2": 6, "3x3": 9, "4x3": 12}
MAX_TILES = 12
SYMBOL_RE = re.compile(r"^[A-Za-z0-9^.=\-]{1,16}$")
CACHE_SECS = 10
SUGGEST = [
    ("GC=F", "Gold futures"), ("MGC=F", "Micro gold"), ("SI=F", "Silver futures"), ("CL=F", "Crude oil"), ("NQ=F", "Nasdaq futures"),
    ("ES=F", "S&P 500 futures"), ("YM=F", "Dow futures"), ("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"), ("USDJPY=X", "USD/JPY"),
    ("AUDUSD=X", "AUD/USD"), ("USDCHF=X", "USD/CHF"), ("USDCAD=X", "USD/CAD"), ("EURGBP=X", "EUR/GBP"), ("GBPJPY=X", "GBP/JPY"),
    ("DX-Y.NYB", "Dollar index"), ("^VIX", "VIX"), ("^TNX", "US 10-year yield"), ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq composite"),
    ("^DJI", "Dow Jones"), ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"), ("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"),
    ("AAPL", "Apple"), ("NVDA", "Nvidia"), ("MSFT", "Microsoft"), ("TSLA", "Tesla"), ("AMZN", "Amazon"),
]
DEFAULT = {"grid": "3x2", "tf": "5m", "style": "area", "tiles": [
    {"s": "GC=F", "tf": "5m", "style": "area"}, {"s": "NQ=F", "tf": "5m", "style": "area"}, {"s": "EURUSD=X", "tf": "5m", "style": "area"},
    {"s": "GBPUSD=X", "tf": "5m", "style": "area"}, {"s": "DX-Y.NYB", "tf": "5m", "style": "area"}, {"s": "CL=F", "tf": "5m", "style": "area"}]}


class MarketsError(Exception):
    pass


_CACHE = {}
_LOCK = threading.Lock()


def clean_symbol(s):
    s = str(s or "").strip()
    if not SYMBOL_RE.match(s):
        raise MarketsError("%r is not a symbol Yahoo understands. Try GC=F, EURUSD=X, ^GSPC, BTC-USD or NVDA." % s[:20])
    return s.upper() if not s.endswith("=X") and "=" not in s else s.upper()


def digits_for(price):
    return 5 if price < 2 else 4 if price < 20 else 2


def parse(text, sym, tf):
    """A Yahoo chart answer -> the small dictionary one tile draws."""
    try:
        data = json.loads(text)
        res = data["chart"]["result"]
    except (ValueError, KeyError, TypeError):
        raise MarketsError("Yahoo sent something unexpected for %s" % sym)
    if not res:
        err = (data.get("chart") or {}).get("error") or {}
        raise MarketsError("Yahoo has no data for %s (%s)" % (sym, err.get("description") or "unknown symbol"))
    r = res[0] or {}
    meta = r.get("meta") or {}
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    ts = r.get("timestamp") or []
    bars = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c):
            continue
        v = (q.get("volume") or [None] * (i + 1))[i] if i < len(q.get("volume") or []) else None
        bars.append([int(t), float(o), float(h), float(l), float(c), v or 0])
    if not bars:
        raise MarketsError("Yahoo has no candles for %s right now" % sym)
    off = int(meta.get("gmtoffset") or 0)
    last = bars[-1]
    price = meta.get("regularMarketPrice")
    price = float(price) if isinstance(price, (int, float)) and price else last[4]
    prev = None
    if tf == "1d":
        prev = bars[-2][4] if len(bars) > 1 else None
    else:                                                       # the close of the session before the latest one
        day = (last[0] + off) // 86400
        for b in reversed(bars):
            if (b[0] + off) // 86400 < day:
                prev = b[4]
                break
    if prev is None:
        p = meta.get("previousClose") or meta.get("chartPreviousClose")
        prev = float(p) if isinstance(p, (int, float)) and p else bars[0][1]
    if tf != "1d":                                              # draw the latest session, plus the one before for context when it is short
        day = (last[0] + off) // 86400
        today = [b for b in bars if (b[0] + off) // 86400 == day]
        if tf in ("1m", "5m") and len(today) >= 12:
            bars = today
    keep = TIMEFRAMES[tf][2]
    bars = bars[-keep:]
    return {"symbol": sym, "tf": tf, "name": str(meta.get("longName") or meta.get("shortName") or sym)[:40],
            "exchange": str(meta.get("fullExchangeName") or meta.get("exchangeName") or "")[:20], "currency": str(meta.get("currency") or "")[:5],
            "price": price, "prev": prev, "chg": price - prev, "pct": (price - prev) / prev * 100 if prev else 0.0,
            "digits": digits_for(price), "last_t": last[0], "at": int(time.time()), "bars": bars}


def one(sym, tf, fetch=None, clock=time.time, ttl=None):
    sym = clean_symbol(sym)
    if tf not in TIMEFRAMES:
        raise MarketsError("timeframe must be one of %s" % ", ".join(TIMEFRAMES))
    key, now = (sym, tf), clock()
    with _LOCK:
        hit = _CACHE.get(key)
    if hit and now - hit[0] < (CACHE_SECS if ttl is None else ttl):
        return hit[1]
    interval, rng, _ = TIMEFRAMES[tf]
    path = "/v8/finance/chart/%s?range=%s&interval=%s" % (urllib.parse.quote(sym, safe=""), rng, interval)
    try:
        out = parse((fetch or z.yahoo_get)(path), sym, tf)
    except z.ZoneError as e:
        raise MarketsError(str(e))
    with _LOCK:
        _CACHE[key] = (now, out)
        if len(_CACHE) > 200:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:50]:
                del _CACHE[k]
    return out


def many(pairs, fetch=None, clock=time.time, ttl=None):
    """[(symbol, timeframe), ...] -> {"SYMBOL|tf": tile or {"error": "..."}}. Fetched side by side so six charts take one round trip.
    ttl is how many seconds an earlier answer for the same chart is reused (the page asks every few seconds, so the server passes a short one)."""
    pairs = list(dict.fromkeys((str(s), str(t)) for s, t in pairs))[:MAX_TILES]

    def get(p):
        try:
            return one(p[0], p[1], fetch, clock, ttl)
        except (MarketsError, z.ZoneError) as e:
            return {"error": str(e), "symbol": str(p[0])[:16], "tf": p[1]}
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(get, pairs))
    return {"%s|%s" % (p[0].upper(), p[1]): r for p, r in zip(pairs, results)}


# ---- the saved layout
def clean_layout(doc):
    doc = doc if isinstance(doc, dict) else {}
    grid = doc.get("grid") if doc.get("grid") in GRIDS else DEFAULT["grid"]
    tf = doc.get("tf") if doc.get("tf") in TIMEFRAMES else DEFAULT["tf"]
    style = doc.get("style") if doc.get("style") in STYLES else DEFAULT["style"]
    tiles = []
    for t in doc.get("tiles") if isinstance(doc.get("tiles"), list) else []:
        if not isinstance(t, dict):
            continue
        try:
            s = clean_symbol(t.get("s"))
        except MarketsError:
            continue
        tiles.append({"s": s, "tf": t.get("tf") if t.get("tf") in TIMEFRAMES else tf, "style": t.get("style") if t.get("style") in STYLES else style})
    return {"grid": grid, "tf": tf, "style": style, "tiles": tiles[:MAX_TILES]}


def load_layout(data_dir):
    try:
        with open(os.path.join(data_dir, FILE), encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return clean_layout(DEFAULT)
    out = clean_layout(doc)
    return out if isinstance(doc, dict) and isinstance(doc.get("tiles"), list) else clean_layout(DEFAULT)


def save_layout(data_dir, doc):
    out = clean_layout(doc)
    tmp = os.path.join(data_dir, FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    os.replace(tmp, os.path.join(data_dir, FILE))
    return out
