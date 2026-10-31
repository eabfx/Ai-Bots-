#!/usr/bin/env python3
"""RiskDesk.ai, a local dashboard for trader-zones: your markets, their zones and the entries found, in a browser.

    py riskdesk\\dashboard.py

opens http://127.0.0.1:8765 on your own computer. Nothing leaves your machine except the chart requests to
Yahoo that zones.py already makes, and the journal coach's question to Anthropic when you press its button. It shows signals only. It never places an order.

Standard library only, like zones.py (which stays in the trader-zones folder). The page (dashboard.html next to this file) is self-contained with no outside scripts or fonts.
"""

import argparse
import contextlib
import io
import json
import math
import os
import copy
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TRADER = os.path.join(os.path.dirname(HERE), "trader")          # your settings and keys, found from the repo whatever folder you start it in
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "trader-zones"))     # zones.py, the zone finder this dashboard shows
sys.path.insert(0, HERE)
import zones as z    # noqa: E402
import coach         # noqa: E402
import macro         # noqa: E402

PORT = 8765
ORDER = ["XAUUSD", "NQ", "MGC", "GBPUSD", "EURUSD"]
CACHE_SECS = 60
# The page's "Zone rule" setting. "before" is the user's own way of drawing a zone: the candle right before the
# big move, even with a middling body, and a smaller move away than the standard rule asks for.
BEFORE = {"zone_candle": "last", "move_atr": 1.3, "base_body_atr": 0.7}
STANDARD = {k: z.DEFAULTS[k] for k in ("zone_candle", "move_atr", "base_body_atr")}
RULES = {"standard": dict(STANDARD, union_with=None), "before": dict(BEFORE, union_with=None),
         "both": dict(STANDARD, union_with=BEFORE)}      # "both" lists the standard zones and the user's as well


def apply_rule(cfg, rule):
    """Put a named zone rule on top of the settings from the config file (the rule decides these settings,
    even if the config file sets them too). Returns the same dict."""
    if rule not in RULES:
        raise z.ZoneError("rule must be %s" % " or ".join('"%s"' % r for r in RULES))
    cfg.update(RULES[rule])
    return cfg
_CHARTS = {}                      # (market, interval, days) -> (when fetched, bars)
_fetch_charts = z._raw_bars


def _cached_charts(sym, interval, days, offline=None):
    """The Yahoo charts a page load needs, kept for CACHE_SECS so a second refresh, or a switch between markets
    and back, does not wait on Yahoo again. Saved charts (--offline) are read every time."""
    if offline:
        return _fetch_charts(sym, interval, days, offline)
    key, now = (sym, interval, days), time.time()
    hit = _CHARTS.get(key)
    if hit is None or now - hit[0] >= CACHE_SECS:
        hit = (now, _fetch_charts(sym, interval, days, offline))
        _CHARTS[key] = hit
    return copy.deepcopy(hit[1])


z._raw_bars = _cached_charts


def dublin_text(ts):
    return z.to_dublin(ts).strftime("%a %d %b %H:%M")


def zone_row(zn, levels=()):
    return {"levels": levels_at(zn["low"], zn["high"], levels), "tf": zn["tf"], "kind": zn["kind"], "low": zn["low"], "high": zn["high"], "status": zn["status"],
            "t": zn["t"], "when": dublin_text(zn["t"]),
            "touched": dublin_text(zn["touched_t"]) if zn.get("touched_t") else None,
            "respect": zn.get("respect"),
            "respect_when": dublin_text(zn["respect_t"]) if zn.get("respect_t") else None,
            "touches": [{"when": dublin_text(tc["t"]), "session": tc["session"], "depth": tc["depth"],
                         "result": tc["result"],
                         "result_when": dublin_text(tc["result_t"]) if tc.get("result_t") else None}
                        for tc in zn.get("touches") or []]}


def outcome(r, m5):
    """What the entry did afterwards, replayed on the closed 5-minute bars that follow it: "target", "stop" or
    "open". A bar that touches both counts as the stop (the bar does not say which came first). `r_mult` is the
    result in units of the risk: the reward to risk on a target, -1 on a stop."""
    long = r["side"] == "long"
    for b in m5:
        if b["t"] < r["entry_t"]:
            continue
        if (b["l"] <= r["stop"]) if long else (b["h"] >= r["stop"]):
            return {"result": "stop", "r_mult": -1.0, "exit_t": b["t"] + 300}
        if r["target"] is not None and ((b["h"] >= r["target"]) if long else (b["l"] <= r["target"])):
            return {"result": "target", "r_mult": r["rr"], "exit_t": b["t"] + 300}
    return {"result": "open", "r_mult": None, "exit_t": None}


def signal_row(r, levels=(), m5=()):
    """An entry's levels: the saved ones its zone sits in, or its entry price sits in (the user enters on the
    5 or 3 minute chart, so the entry is often some way from the zone)."""
    by_zone = levels_at(r["zone"]["low"], r["zone"]["high"], levels)
    by_entry = [dict(lv, via="entry") for lv in levels_at(r["entry"], r["entry"], levels)
                if lv["index"] not in {q["index"] for q in by_zone}]
    return {"fib": r.get("fib"), "style": r.get("style", "tool"), "levels": [dict(lv, via="zone") for lv in by_zone] + by_entry, "side": r["side"], "trigger": r["trigger"], "zone": dict(r["zone"], when=dublin_text(r["zone"]["t"])),
            "swept": dublin_text(r["sweep_t"]), "gap": list(r["gap"]), "entry_t": r["entry_t"],
            "entry_when": dublin_text(r["entry_t"]), "entry": r["entry"], "stop": r["stop"], "target": r["target"],
            "rr": r["rr"], "outcome": outcome(r, m5),
            "also": [dict(o, when=dublin_text(o["t"])) for o in r.get("also", [])]}


def mine_path(config_path):
    """The user's own zones live next to the config file, in my_zones.json."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "my_zones.json")


def load_mine(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise z.ZoneError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(doc, dict):
        raise z.ZoneError("%s must hold an object with one list of zones per market" % path)
    return doc


def mine_list(doc, sym):
    """The saved zones for one market; an entry that is not an object stays in place (as {}) so numbering holds."""
    items = doc.get(sym)
    return [m if isinstance(m, dict) else {} for m in items] if isinstance(items, list) else []


def save_mine(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


MAX_MINE = 100


def add_mine(path, sym, kind, low, high, when, note=""):
    sym = z.resolve(sym)
    z.manual_zone((kind, low, high, when), [], flag="the zone")            # raises a plain error when it is wrong
    doc = load_mine(path)
    items = mine_list(doc, sym)
    entry = {"kind": kind.lower(), "low": float(low), "high": float(high), "when": when.strip(),
             "note": str(note or "")[:80]}
    if any(all(m.get(k) == entry[k] for k in ("kind", "low", "high", "when")) for m in items):
        raise z.ZoneError("that zone is already in your list")
    if len(items) >= MAX_MINE:
        raise z.ZoneError("you have %d zones saved for %s already: remove some first" % (MAX_MINE, sym))
    doc[sym] = items + [entry]
    save_mine(path, doc)
    return doc[sym]


LEVEL_KINDS = ("support", "resistance")
LEVEL_FRAMES = ("4h", "daily")
MAX_LEVELS = 50


def levels_path(config_path):
    """The user's daily and 4h support and resistance zones live next to the config file, in my_levels.json."""
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), "my_levels.json")


def add_level(path, sym, kind, frame, low, high, note=""):
    """Save a big support or resistance zone from the 4-hour or daily chart. The tool does not find these itself:
    it marks the zones and entries that sit inside one."""
    sym = z.resolve(sym)
    if not isinstance(kind, str) or kind.lower() not in LEVEL_KINDS:
        raise z.ZoneError('kind must be "support" or "resistance", not %r' % (kind,))
    if not isinstance(frame, str) or frame.lower() not in LEVEL_FRAMES:
        raise z.ZoneError('frame must be "4h" or "daily", not %r' % (frame,))
    try:
        low, high = float(low), float(high)
    except (TypeError, ValueError):
        raise z.ZoneError("a level wants two prices, low then high")
    if not (math.isfinite(low) and math.isfinite(high)):
        raise z.ZoneError("a level wants two prices, low then high")
    if low >= high:
        raise z.ZoneError("a level wants the low price first and it must be under the high price")
    doc = load_mine(path)
    items = mine_list(doc, sym)
    entry = {"kind": kind.lower(), "frame": frame.lower(), "low": low, "high": high, "note": str(note or "")[:80]}
    if any(all(m.get(k) == entry[k] for k in ("kind", "frame", "low", "high")) for m in items):
        raise z.ZoneError("that level is already in your list")
    if len(items) >= MAX_LEVELS:
        raise z.ZoneError("you have %d levels saved for %s already: remove some first" % (len(items), sym))
    doc[sym] = items + [entry]
    save_mine(path, doc)
    return doc[sym]


def remove_level(path, sym, index):
    sym = z.resolve(sym)
    doc = load_mine(path)
    items = mine_list(doc, sym)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
        raise z.ZoneError("there is no saved level number %s for %s" % (index, sym))
    doc[sym] = items[:index] + items[index + 1:]
    save_mine(path, doc)
    return doc[sym]


def level_dicts(items, price=None):
    """The saved levels with their position: where price is now (inside, above or below), for the page."""
    out = []
    for i, m in enumerate(items):
        lv = dict(m, index=i)
        lo, hi = m.get("low"), m.get("high")
        if price is not None and isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            lv["where"] = "inside" if lo <= price <= hi else "above" if price > hi else "below"
        out.append(lv)
    return out


def levels_at(low, high, levels):
    """The saved levels a price range touches (a single price is a range with low equal to high)."""
    return [{"index": lv["index"], "kind": lv.get("kind"), "frame": lv.get("frame"), "low": lv["low"],
             "high": lv["high"], "note": lv.get("note", "")}
            for lv in levels if isinstance(lv.get("low"), (int, float)) and isinstance(lv.get("high"), (int, float))
            and lv["low"] <= high and lv["high"] >= low]


def remove_mine(path, sym, index):
    sym = z.resolve(sym)
    doc = load_mine(path)
    items = mine_list(doc, sym)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
        raise z.ZoneError("there is no saved zone number %s for %s" % (index, sym))
    doc[sym] = items[:index] + items[index + 1:]
    save_mine(path, doc)
    return doc[sym]


def snapshot(sym, cfg, now, spot=None, offline=None, shift=None, mine=None, levels=None):
    """Everything the page shows for one market, as plain JSON-ready data. Errors raise z.ZoneError.
    `spot` is the price on the user's chart right now: the gap to the data is measured and applied.
    `shift` is a gap measured earlier, reused as it is (a price typed an hour ago would give a wrong gap now).
    Spot wins when both are given. Spot forex pairs take neither.
    `mine` is the user's own saved zones for this market: they are checked for entries next to the tool's own.
    `levels` is their saved daily and 4h support and resistance: zones and entries inside one are marked."""
    sym = z.resolve(sym)
    notes = []
    shifted = False
    used = 0.0
    saved = z.SHIFT
    try:
        z.SHIFT = 0.0
        if spot is not None:
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                z.SHIFT = z.spot_shift(sym, spot, now, offline, cfg)
            shifted = z.SHIFT != 0.0
            if buf.getvalue().strip():
                notes.append(buf.getvalue().strip())
        elif shift is not None and not z.MARKETS[sym].get("spot"):
            z.SHIFT = shift
            shifted = shift != 0.0
            notes.append("Prices shifted by %s, the gap measured when you entered your price. It drifts through the "
                         "day: type a fresh price to measure it again." % z.px(sym, shift))
        used = z.SHIFT
        specs = []
        for i, m in enumerate(mine or []):
            spec = (m.get("kind"), m.get("low"), m.get("high"), m.get("when"))
            try:
                z.manual_zone(spec, [], flag="the zone")
                specs.append(spec)
            except z.ZoneError as e:
                notes.append("Your saved zone number %d was skipped: %s" % (i + 1, e))
        found = z.analyse(sym, cfg, now, offline, extra_specs=specs)
    finally:
        z.SHIFT = saved
    m1 = found["m1"]
    if z.flat_share(m1) > 0.9:
        notes.append(z.FLAT_NOTE % sym)
    if sym == "XAUUSD" and not shifted:
        notes.append("These are COMEX gold futures prices, about 11 to 16 points above spot gold. Type the price your "
                     "spot chart shows now into the box above and press Refresh to line everything up with it.")
    stages = []
    if not found["results"]:
        for zn, best, trace in found["staged"]:
            stages.append({"head": z.stage_line(sym, zn, best, found["stage_names"]).strip(),
                           "lines": [ln.strip() for ln in z.trace_lines(sym, zn, trace)]})
    market = z.MARKETS[sym]
    level_list = level_dicts(levels or [], m1[-1]["c"] if m1 else None)
    return {"symbol": sym, "yahoo": market["yahoo"], "market_note": market["note"], "spot_market": bool(market.get("spot")),
            "digits": market["digits"], "rule": "both" if cfg.get("union_with") else "before" if cfg["zone_candle"] == "last" else "standard", "shifted": shifted, "shift_value": used,
            "as_of": dublin_text(now), "now_t": now, "price": m1[-1]["c"] if m1 else None,
            "price_when": dublin_text(m1[-1]["t"] + 60) if m1 else None,
            "days": cfg["days"], "per_day": cfg["max_per_day"], "notes": notes,
            "mine": [dict(m, index=i) for i, m in enumerate(mine or [])],
            "levels": level_list,
            "zones": [zone_row(zn, level_list) for zn in found["zones"]],
            "signals": [signal_row(r, level_list, found.get("m5") or []) for r in found["results"]],
            "stages": stages, "furthest": found["stage_names"][found["best"]],
            "style": cfg["entry_style"]}


class Handler(BaseHTTPRequestHandler):
    config_path = os.path.join(TRADER, "config.json")
    offline = None
    mine_file = os.path.join(TRADER, "my_zones.json")
    levels_file = os.path.join(TRADER, "my_levels.json")
    coach_post = staticmethod(coach.api_post)       # the tests swap this so nothing goes out
    macro_net = macro.Net()                         # and this, for the price, calendar and news feeds

    def log_message(self, fmt, *args):        # keep the console quiet
        pass

    def send(self, code, body, kind):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, doc):
        self.send(code, json.dumps(doc), "application/json; charset=utf-8")

    def host_ok(self):
        """Only answer requests addressed to this computer by name or number, which blocks other websites
        from reaching the dashboard through the browser (DNS rebinding)."""
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def do_POST(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        if self.headers.get("X-Requested-With") != "zones-dashboard":
            return self.send_json(403, {"error": "missing X-Requested-With header"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            limit = 150000 if urlparse(self.path).path in ("/api/coach", "/api/chat") else 20000     # a journal is bigger than a zone
            if not 0 < size <= limit:
                raise z.ZoneError("the request body is empty or too large")
            try:
                body = json.loads(self.rfile.read(size).decode("utf-8"))
            except ValueError:
                raise z.ZoneError("the request body is not valid JSON")
            if not isinstance(body, dict):
                raise z.ZoneError("the request body must be a JSON object")
            path = urlparse(self.path).path
            if path == "/api/mine/add":
                items = add_mine(self.mine_file, str(body.get("symbol", "")), body.get("kind"), body.get("low"),
                                 body.get("high"), body.get("when"), body.get("note", ""))
            elif path == "/api/mine/remove":
                items = remove_mine(self.mine_file, str(body.get("symbol", "")), body.get("index"))
            elif path == "/api/levels/add":
                items = add_level(self.levels_file, str(body.get("symbol", "")), body.get("kind"), body.get("frame"),
                                  body.get("low"), body.get("high"), body.get("note", ""))
                return self.send_json(200, {"levels": level_dicts(items)})
            elif path == "/api/levels/remove":
                items = remove_level(self.levels_file, str(body.get("symbol", "")), body.get("index"))
                return self.send_json(200, {"levels": level_dicts(items)})
            elif path == "/api/coach":
                pv = coach.provider(self.config_path)
                answer = coach.ask(body.get("trades"), str(body.get("question") or ""), body.get("indicator"), pv["key"],
                                   pv["model"], self.coach_post, pv["name"])
                return self.send_json(200, {"answer": answer})
            elif path == "/api/macro/brief":
                now = z.parse_now(str(body.get("now") or "") or None)
                snap = macro.snapshot(now, self.macro_net)
                pv = coach.provider(self.config_path)
                answer = coach.generate(macro.BRIEF_SYSTEM, macro.brief_messages(snap, str(body.get("focus") or "")), pv,
                                        self.coach_post, 1200)
                return self.send_json(200, {"answer": answer, "as_of": snap["as_of"], "parts": macro.split_brief(answer)})
            elif path == "/api/chat":
                now = z.parse_now(str(body.get("now") or "") or None)
                msgs = coach.clean_messages(body.get("messages"))
                known = macro.latest(now)
                system = coach.chat_system(body.get("context"), body.get("trades"), macro.compact(known) if known else "")
                pv = coach.provider(self.config_path)
                return self.send_json(200, {"answer": coach.generate(system, msgs, pv, self.coach_post, 1200)})
            else:
                return self.send_json(404, {"error": "not found"})
            self.send_json(200, {"mine": [dict(m, index=i) for i, m in enumerate(items)]})
        except (z.ZoneError, coach.CoachError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        url = urlparse(self.path)
        if url.path == "/":
            self.send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/api/markets":
            self.send_json(200, {"markets": [{"symbol": s, "spot": bool(z.MARKETS[s].get("spot")),
                                              "note": z.MARKETS[s]["note"]} for s in ORDER]})
        elif url.path == "/api/macro":
            self.macro(parse_qs(url.query))
        elif url.path == "/api/coach/status":
            pv = coach.provider(self.config_path)
            self.send_json(200, {"ready": bool(pv["key"]), "service": pv["name"], "model": pv["model"]})
        elif url.path == "/api/snapshot":
            self.snapshot(parse_qs(url.query))
        else:
            self.send_json(404, {"error": "not found"})

    def macro(self, q):
        try:
            now = z.parse_now((q.get("now") or [""])[0] or None)
            force = (q.get("refresh") or [""])[0] == "1"
            self.send_json(200, macro.snapshot(now, self.macro_net, force))
        except (z.ZoneError, macro.MacroError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def snapshot(self, q):
        try:
            sym = (q.get("symbol") or [""])[0]
            spot_text = (q.get("spot") or [""])[0].strip()
            try:
                spot = float(spot_text) if spot_text else None
            except ValueError:
                raise z.ZoneError("your price must be a number like 4021.5")
            shift_text = (q.get("shift") or [""])[0].strip()
            try:
                shift = float(shift_text) if shift_text else None
            except ValueError:
                raise z.ZoneError("shift must be a number")
            cfg = apply_rule(z.read_config(self.config_path), (q.get("rule") or ["standard"])[0] or "standard")
            style = (q.get("entry") or [""])[0]
            if style:
                if style not in ("tool", "yours"):
                    raise z.ZoneError('entry must be "tool" or "yours"')
                cfg["entry_style"] = style
            days = (q.get("days") or [""])[0]
            if days:
                if not days.isdigit() or not 1 <= int(days) <= 14:
                    raise z.ZoneError("days must be a whole number from 1 to 14")
                cfg["days"] = int(days)
            per_day = (q.get("per_day") or [""])[0]
            if per_day:
                if not per_day.isdigit() or not 1 <= int(per_day) <= 20:
                    raise z.ZoneError("per_day must be a whole number from 1 to 20")
                cfg["max_per_day"] = int(per_day)
            now = z.parse_now((q.get("now") or [""])[0] or None)
            mine = mine_list(load_mine(self.mine_file), z.resolve(sym))
            levels = mine_list(load_mine(self.levels_file), z.resolve(sym))
            self.send_json(200, snapshot(sym, cfg, now, spot, self.offline, shift, mine, levels))
        except z.ZoneError as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:                      # a network or data problem: say so, keep serving
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})


class DashboardServer(HTTPServer):
    # On Windows, address reuse lets a second dashboard bind a port that is already in use and run beside the
    # first without any error, so it is turned off there. Elsewhere it only helps a quick restart.
    allow_reuse_address = os.name != "nt"


def make_server(port=PORT, config_path=os.path.join(TRADER, "config.json"), offline=None):
    handler = type("Bound", (Handler,), {"config_path": config_path, "offline": offline,
                                         "mine_file": mine_path(config_path),
                                         "levels_file": levels_path(config_path)})
    return DashboardServer(("127.0.0.1", port), handler)


with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"), encoding="utf-8") as _fh:
    PAGE = _fh.read()


def already_running(port):
    """True when the thing holding the port is this dashboard (it answers the status route)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/coach/status" % port, timeout=2) as r:
            return "ready" in json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False


def main(argv=None):
    p = argparse.ArgumentParser(prog="dashboard.py", description="Local dashboard for trader-zones (signals only)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--config", default=os.path.join(TRADER, "config.json"))
    p.add_argument("--offline", help="folder with saved Yahoo charts, to try the dashboard without the internet")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = p.parse_args(argv)
    try:
        server = make_server(args.port, args.config, args.offline)
    except OSError as e:
        url = "http://127.0.0.1:%d" % args.port
        if already_running(args.port):                                  # a second double-click just opens the one that is running
            print("RiskDesk is already running at %s. Opening it." % url)
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        print("error: cannot use port %d (%s). Try --port 8766." % (args.port, e), file=sys.stderr)
        return 2
    url = "http://127.0.0.1:%d" % args.port
    print("Dashboard running at %s  (press Ctrl+C to stop). Signals only, nothing is placed." % url)
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
