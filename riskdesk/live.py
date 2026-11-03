"""Live prices for the page: the last price of every watched market, refreshed every few seconds while someone is looking.

Yahoo's free chart answer carries the current price in its header (regularMarketPrice), so one small request per market
every few seconds is enough to make the numbers on Home and Macro move. It only runs while the page is open (the page
asks every couple of seconds), asks nothing when nobody is watching, and slows itself down if Yahoo says to slow down.
It never invents a price: a market whose answer failed keeps its last real price and says how old it is.

Accuracy checks, all in the open (see quality()):
  freshness  every price says whether it is live, delayed, quiet or closed. Yahoo's free CME futures (gold, Nasdaq, oil,
             dollar index) arrive about 10 minutes late, so a price that keeps that steady gap is labelled "delayed".
  bad ticks  a price that jumps further than a market plausibly moves in seconds is held back until the next reading
             agrees with it. One stray tick never reaches the screen or trips an alert.
  second     when a Twelve Data key is set, gold spot is read live from it, and EUR/USD and GBP/USD are compared with it
  opinion    every few minutes; a gap bigger than the tolerance is flagged. Credits are counted and capped per day.
Standard library only.
"""
import collections
import concurrent.futures
import json
import threading
import time
import urllib.parse

import macro

EVERY = 3.0            # seconds between rounds while someone is watching
WATCHED_FOR = 30.0     # keep going this long after the page last asked
SLOWEST = 60.0         # the longest the backoff can stretch a round to
PATH = "/v8/finance/chart/%s?range=1d&interval=1d"

# what each market is, so the page never passes a delayed futures price off as spot
KIND = {"XAUUSD": "COMEX futures", "NQ": "CME futures", "OIL": "NYMEX futures", "DXY": "ICE futures index",
        "EURUSD": "spot forex", "GBPUSD": "spot forex", "US10Y": "Cboe index", "VIX": "Cboe index", "SPX": "S&P index"}
# the biggest move (in %) believed between two readings a few seconds apart
JUMP = {"EURUSD": 0.25, "GBPUSD": 0.25, "XAUUSD": 0.8, "NQ": 0.8, "SPX": 0.8, "OIL": 2.5, "DXY": 0.4, "US10Y": 5.0, "VIX": 12.0}
DELAY_MIN, DELAY_MAX = 420, 840     # a steady gap of 7 to 14 minutes between the exchange time and now is a delayed feed
CLOSED_AFTER = 1800                 # no new trade for half an hour: the market is shut (or the feed is stale)
CHECK_PAIRS = [("EURUSD", "EUR/USD"), ("GBPUSD", "GBP/USD")]
PIP_TOLERANCE = 2.5                 # two sources further apart than this (in pips) are flagged
VERIFY_EVERY = 300.0                # seconds between second-source checks
SPOT_EVERY = 30.0                   # seconds between live gold spot readings
SPOT_PREV_EVERY = 900.0             # seconds between reads of gold's previous close
DAILY_CREDITS = 780                 # Twelve Data's free plan allows 800 a day; stay under it


def credit_budget(environ=None):
    import os
    environ = os.environ if environ is None else environ
    try:
        return max(0, int(environ.get("TWELVEDATA_DAILY_CREDITS", DAILY_CREDITS)))
    except ValueError:
        return DAILY_CREDITS


def classify(hist):
    """('live' | 'delayed' | 'quiet' | 'closed' | 'unknown', seconds) from the last few (fetched at, exchange time) pairs.
    A delayed feed keeps a steady gap while its clock moves; a quiet market's gap just keeps growing."""
    got, when = hist[-1]
    if not when:
        return "unknown", 0
    lag = got - when
    if lag > CLOSED_AFTER:
        return "closed", lag
    lags = sorted(g - w for g, w in hist if w)
    if len(lags) >= 3 and hist[-1][1] > hist[0][1] and DELAY_MIN <= lags[len(lags) // 2] <= DELAY_MAX and lags[-1] - lags[0] <= 90:
        return "delayed", lags[len(lags) // 2]
    if lag <= 120:
        return "live", max(lag, 0)
    return "quiet", lag


def parse_price(text):
    """(price, time of the price) from a Yahoo chart answer. Raises MacroError when it holds no price."""
    try:
        meta = json.loads(text)["chart"]["result"][0].get("meta") or {}
        price = float(meta["regularMarketPrice"])
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise macro.MacroError("no live price in the answer")
    when = meta.get("regularMarketTime")
    return price, int(when) if isinstance(when, (int, float)) else 0


class LiveTicks:
    def __init__(self, net, items=None, clock=time.time, every=EVERY):
        self.net = net
        self.items = list(items or macro.WATCH)
        self.clock = clock
        self.every = every
        self.lock = threading.Lock()
        self.quotes = {}                # key -> {"price", "when", "got"}
        self.at = 0.0                   # when the last round finished with at least one price
        self.error = ""
        self.slow = 0.0                 # extra wait after Yahoo asked us to slow down
        self.wanted_until = 0.0
        self.thread = None
        self.stop = threading.Event()
        self.hist = {}                  # key -> last few (fetched at, exchange time)
        self.pending = {}               # key -> a jump waiting for a second reading to agree
        self.rejected = {}              # key -> how many stray ticks were held back
        self.health = {}                # key -> {"ok", "fail", "ms"}
        self.delay_seen = {}            # key -> (seconds, when seen): remembers a delayed feed through a quiet spell
        self.spot = None                # gold spot from Twelve Data: {"price", "got", "prev"}
        self.checks = {}                # key -> second-source comparison
        self.td = {"state": "off", "error": "", "used": 0, "day": ""}
        self.td_pause = 0.0
        self.spot_at = self.check_at = self.prev_at = 0.0

    # ---- what the page sees
    def poke(self):
        """The page is open: keep fetching for a while."""
        self.wanted_until = self.clock() + WATCHED_FOR

    def state(self):
        with self.lock:
            now = self.clock()
            quotes = {}
            for k, v in self.quotes.items():
                st, secs = self.fresh(k)
                quotes[k] = {"price": v["price"], "when": v["when"], "state": st, "age": int(secs),
                             "delay": int(secs) if st == "delayed" else 0}
            return {"at": self.at, "every": self.every + self.slow,
                    "live": bool(self.at) and now - self.at < 4 * (self.every + self.slow) + 5,
                    "error": self.error, "quotes": quotes, "spot": self._spot_view(now),
                    "checks": {k: dict(c) for k, c in self.checks.items()}}

    def fresh(self, key):
        """(state, seconds) for one market. Call with the lock held or from a single thread."""
        hist = self.hist.get(key)
        if not hist:
            return "unknown", 0
        st, secs = classify(hist)
        seen = self.delay_seen.get(key)
        if st == "delayed":
            self.delay_seen[key] = (secs, self.clock())
        elif st == "quiet" and seen and self.clock() - seen[1] < 900 and DELAY_MIN <= secs <= CLOSED_AFTER:
            return "delayed", seen[0]
        return st, secs

    def _spot_view(self, now):
        sp = self.spot
        if not sp:
            return None
        out = {"price": sp["price"], "age": int(max(0, now - sp["got"])), "prev": sp.get("prev")}
        if sp.get("prev"):
            out["change"] = sp["price"] - sp["prev"]
            out["pct"] = out["change"] / sp["prev"] * 100.0
        return out

    def quality(self):
        """Everything the Data quality panel shows."""
        st = self.state()
        rows = []
        with self.lock:
            for key, name, sym, digits in self.items:
                q = st["quotes"].get(key)
                h = self.health.get(key) or {}
                rows.append({"key": key, "name": name, "symbol": sym, "kind": KIND.get(key, ""), "price": q["price"] if q else None,
                             "state": q["state"] if q else "waiting", "age": q["age"] if q else 0, "delay": q["delay"] if q else 0,
                             "ok": h.get("ok", 0), "fail": h.get("fail", 0), "ms": h.get("ms"),
                             "held": self.rejected.get(key, 0)})
            td = dict(self.td, budget=credit_budget())
        return {"at": st["at"], "live": st["live"], "error": st["error"], "rows": rows, "spot": st["spot"], "checks": st["checks"],
                "td": td, "pip_tolerance": PIP_TOLERANCE}

    # ---- one round
    def round(self):
        """Ask for every price once. Returns how many answered."""
        got, errors, throttled = {}, [], False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futs = {item[0]: pool.submit(self._one, item) for item in self.items}
            for key, fut in futs.items():
                try:
                    got[key] = fut.result()
                except macro.MacroError as e:
                    errors.append("%s: %s" % (key, e))
                    self.health.setdefault(key, {"ok": 0, "fail": 0, "ms": None})["fail"] += 1
                    if "429" in str(e):
                        throttled = True
        now = self.clock()
        with self.lock:
            for key, (price, when) in got.items():
                self.health.setdefault(key, {"ok": 0, "fail": 0, "ms": None})["ok"] += 1
                self.hist.setdefault(key, collections.deque(maxlen=6)).append((now, when))
                if self._believe(key, price, now):
                    self.quotes[key] = {"price": price, "when": when, "got": now}
            if got:
                self.at = now
            self.error = "; ".join(errors[:2]) if errors and not got else ""
            if throttled:
                self.slow = min(SLOWEST, max(self.every * 2, self.slow * 2))
            elif got:
                self.slow = 0.0
        return len(got)

    def _believe(self, key, price, now):
        """False for a reading that jumps too far to be real until a second reading agrees with it."""
        last = self.quotes.get(key)
        if not last or not last["price"] or now - last["got"] > 120:
            self.pending.pop(key, None)
            return True                                          # first price, or the market just reopened after a gap
        if abs(price / last["price"] - 1) * 100 <= JUMP.get(key, 1.0):
            self.pending.pop(key, None)
            return True
        held = self.pending.get(key)
        if held and abs(price / held - 1) * 100 <= JUMP.get(key, 1.0) / 3:
            self.pending.pop(key, None)                          # two readings in a row agree: the jump is real
            return True
        self.pending[key] = price
        self.rejected[key] = self.rejected.get(key, 0) + 1
        return False

    def _one(self, item):
        key, name, sym, digits = item
        started = time.time()
        got = parse_price(self.net.yahoo(PATH % urllib.parse.quote(sym, safe="")))
        self.health.setdefault(key, {"ok": 0, "fail": 0, "ms": None})["ms"] = int((time.time() - started) * 1000)
        return got

    # ---- the second opinion (Twelve Data), only when a key is set
    def _credit(self, now):
        """Count one Twelve Data credit against today's allowance. False when today's allowance is spent."""
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if self.td["day"] != day:
            self.td["day"], self.td["used"] = day, 0
        if self.td["used"] >= credit_budget():
            self.td["error"] = "today's credit allowance is used up; resumes at midnight UTC"
            return False
        self.td["used"] += 1
        return True

    def _twelve(self, call, symbol, now):
        """One Twelve Data answer as a dict, or None. Counts the credit and backs off on a rate limit."""
        if not self._credit(now):
            return None
        try:
            text = call(symbol)
            d = json.loads(text) if text is not None else None
        except (macro.MacroError, ValueError) as e:
            d = {"status": "error", "code": 429 if "429" in str(e) else 0, "message": str(e)}
        if d is None:
            self.td["state"], self.td_pause = "off", now + 600       # no key: look again in ten minutes
            self.td["used"] -= 1
            return None
        if not isinstance(d, dict) or d.get("status") == "error":
            code = d.get("code") if isinstance(d, dict) else 0
            self.td["state"] = "limited" if code == 429 else "error"
            self.td["error"] = str((d or {}).get("message", "unexpected answer"))[:90] if isinstance(d, dict) else "unexpected answer"
            self.td_pause = now + (60 if code == 429 else 30)
            return None
        self.td["state"], self.td["error"] = "on", ""
        return d

    def verify(self):
        """Gold spot, the pair cross-checks and gold's previous close, each on its own timer. Returns how many calls it made."""
        price_call, quote_call = getattr(self.net, "twelve_price", None), getattr(self.net, "twelve", None)
        now = self.clock()
        if not price_call or now < self.td_pause:
            return 0
        n = 0
        if now - self.spot_at >= SPOT_EVERY:
            self.spot_at = now
            d = self._twelve(price_call, "XAU/USD", now)
            n += 1
            try:
                px = float(d["price"]) if d else None
            except (KeyError, TypeError, ValueError):
                px = None
            if px:
                self.spot = dict(self.spot or {}, price=px, got=now)
        if self.td["state"] in ("limited", "error") or now < self.td_pause:
            return n
        if quote_call and now - self.prev_at >= SPOT_PREV_EVERY:
            self.prev_at = now
            d = self._twelve(quote_call, "XAU/USD", now)
            n += 1
            try:
                if d and d.get("previous_close") not in (None, "") and self.spot:
                    self.spot["prev"] = float(d["previous_close"])
            except (TypeError, ValueError):
                pass
        if now - self.check_at >= VERIFY_EVERY and now >= self.td_pause:
            self.check_at = now
            for key, sym in CHECK_PAIRS:
                d = self._twelve(price_call, sym, now)
                n += 1
                try:
                    other = float(d["price"]) if d else None
                except (KeyError, TypeError, ValueError):
                    other = None
                ours = (self.quotes.get(key) or {}).get("price")
                if other and ours:
                    pips = (other - ours) * 10000.0
                    self.checks[key] = {"other": other, "ours": ours, "diff": round(pips, 1), "unit": "pips", "at": now,
                                        "ok": abs(pips) <= PIP_TOLERANCE, "source": "Twelve Data"}
        return n

    # ---- the background loop
    def start(self):
        if self.thread:
            return
        self.thread = threading.Thread(target=self._loop, name="live-prices", daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            if self.clock() < self.wanted_until:
                started = time.time()
                try:
                    self.round()
                    self.verify()
                except Exception as e:                       # never let the loop die
                    with self.lock:
                        self.error = str(e)[:120]
                self.stop.wait(max(0.5, self.every + self.slow - (time.time() - started)))
            else:
                self.stop.wait(0.25)
