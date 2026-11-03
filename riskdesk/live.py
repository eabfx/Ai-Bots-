"""Live prices for the page: the last price of every watched market, refreshed every few seconds while someone is looking.

Yahoo's free chart answer carries the current price in its header (regularMarketPrice), so one small request per market
every few seconds is enough to make the numbers on Home and Macro move. It only runs while the page is open (the page
asks every couple of seconds), asks nothing when nobody is watching, and slows itself down if Yahoo says to slow down.
It never invents a price: a market whose answer failed keeps its last real price and says how old it is.
Standard library only.
"""
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

    # ---- what the page sees
    def poke(self):
        """The page is open: keep fetching for a while."""
        self.wanted_until = self.clock() + WATCHED_FOR

    def state(self):
        with self.lock:
            now = self.clock()
            return {"at": self.at, "every": self.every + self.slow,
                    "live": bool(self.at) and now - self.at < 4 * (self.every + self.slow) + 5,
                    "error": self.error,
                    "quotes": {k: {"price": v["price"], "when": v["when"]} for k, v in self.quotes.items()}}

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
                    if "429" in str(e):
                        throttled = True
        now = self.clock()
        with self.lock:
            for key, (price, when) in got.items():
                self.quotes[key] = {"price": price, "when": when, "got": now}
            if got:
                self.at = now
            self.error = "; ".join(errors[:2]) if errors and not got else ""
            if throttled:
                self.slow = min(SLOWEST, max(self.every * 2, self.slow * 2))
            elif got:
                self.slow = 0.0
        return len(got)

    def _one(self, item):
        key, name, sym, digits = item
        return parse_price(self.net.yahoo(PATH % urllib.parse.quote(sym, safe="")))

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
                except Exception as e:                       # never let the loop die
                    with self.lock:
                        self.error = str(e)[:120]
                self.stop.wait(max(0.5, self.every + self.slow - (time.time() - started)))
            else:
                self.stop.wait(0.25)
