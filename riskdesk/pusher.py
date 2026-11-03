"""Phone alerts: the server watches for you when the page is closed and pushes to your phone.

It checks three things once a minute: the price alerts you set on the Alerts page, high-impact news coming up, and the
London and New York opens. Each one is sent once. Messages go through ntfy (the free ntfy.sh service and its app for
iPhone and Android): you subscribe the app to a private topic name made here, and the server posts to that topic.
Anyone who learns the topic name could read the alerts, so it is long and random, and it is never in the page source.
Nothing here places an order. Turn it off and the topic file is deleted.

Standard library only."""

import json
import os
import secrets
import threading
import time
import urllib.request

import macro
import zones as z

TOPIC_FILE = "push_topic.txt"
RULES_FILE = "push_rules.json"
NTFY = "https://ntfy.sh/"
EVERY = 60
CALENDAR_EVERY = 600
KEEP_SENT = 20
MAX_PRICE = 30
KEYS = {item[0]: item for item in macro.WATCH}


def ntfy_post(url, text, headers):
    req = urllib.request.Request(url, data=text.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read(200)


def ascii_only(s):
    return str(s).encode("ascii", "replace").decode("ascii").replace("?", "-")[:120]


class Pusher:
    def __init__(self, data_dir, net, post=ntfy_post, clock=time.time):
        self.dir, self.net, self.post, self.clock = data_dir, net, post, clock
        self.lock = threading.Lock()
        self.error = ""
        self.sent = []
        self.thread = None
        self.stop = threading.Event()
        self.events, self.events_at = [], 0.0
        self.rules = self._load_rules()

    # ---- files
    def _path(self, name):
        return os.path.join(self.dir, name)

    def topic(self):
        try:
            with open(self._path(TOPIC_FILE), encoding="utf-8") as fh:
                t = fh.read().strip()
            return t if t.replace("-", "").isalnum() and 8 <= len(t) <= 64 else ""
        except OSError:
            return ""

    def _load_rules(self):
        out = {"price": [], "news": True, "newsMins": 15, "sess": True, "seen": {}}
        try:
            with open(self._path(RULES_FILE), encoding="utf-8") as fh:
                out.update(self.clean(json.load(fh), keep_seen=True))
        except (OSError, ValueError):
            pass
        return out

    def _save_rules(self):
        tmp = self._path(RULES_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.rules, fh)
        os.replace(tmp, self._path(RULES_FILE))

    @staticmethod
    def clean(doc, keep_seen=False):
        """Only what the page may set: a short list of price levels and two switches. Anything else is dropped."""
        doc = doc if isinstance(doc, dict) else {}
        out = {}
        price = []
        for a in doc.get("price") if isinstance(doc.get("price"), list) else []:
            if isinstance(a, dict) and a.get("sym") in KEYS and a.get("dir") in ("above", "below") \
                    and isinstance(a.get("level"), (int, float)) and not isinstance(a.get("level"), bool) \
                    and a["level"] == a["level"] and abs(a["level"]) < 1e9:
                price.append({"sym": a["sym"], "dir": a["dir"], "level": float(a["level"]),
                              "done": a["done"][:32] if isinstance(a.get("done"), str) else ""})
        out["price"] = price[:MAX_PRICE]
        if isinstance(doc.get("news"), bool):
            out["news"] = doc["news"]
        if isinstance(doc.get("sess"), bool):
            out["sess"] = doc["sess"]
        m = doc.get("newsMins")
        if isinstance(m, (int, float)) and not isinstance(m, bool) and 1 <= m <= 120:
            out["newsMins"] = int(m)
        if keep_seen and isinstance(doc.get("seen"), dict):
            out["seen"] = {str(k)[:10]: [str(x)[:120] for x in v][:200] for k, v in doc["seen"].items() if isinstance(v, list)}
        return out

    # ---- what the page asks
    def state(self):
        with self.lock:
            return {"enabled": bool(self.topic()), "topic": self.topic(), "service": "ntfy.sh", "error": self.error,
                    "sent": list(self.sent), "rules": {k: self.rules[k] for k in ("price", "news", "newsMins", "sess")}}

    def enable(self):
        with self.lock:
            if not self.topic():
                name = "kelly-" + secrets.token_hex(8)
                with open(self._path(TOPIC_FILE), "w", encoding="utf-8") as fh:
                    fh.write(name)
        return self.state()

    def disable(self):
        with self.lock:
            try:
                os.remove(self._path(TOPIC_FILE))
            except OSError:
                pass
        return self.state()

    def set_rules(self, doc):
        with self.lock:
            fresh = self.clean(doc)
            old = {(a["sym"], a["dir"], a["level"]): a["done"] for a in self.rules["price"]}
            for a in fresh.get("price", []):
                a["done"] = a["done"] or old.get((a["sym"], a["dir"], a["level"]), "")      # a fired alert stays fired
            self.rules.update(fresh)
            self._save_rules()
        return self.state()

    # ---- sending
    def send(self, title, message, tags="bell", priority="default"):
        topic = self.topic()
        if not topic:
            return False
        try:
            self.post(NTFY + topic, message, {"Title": ascii_only(title), "Tags": tags, "Priority": priority,
                                              "Content-Type": "text/plain; charset=utf-8"})
        except Exception as e:                          # offline, ntfy down: say so on the page, try again next time
            with self.lock:
                self.error = "Could not reach ntfy: %s" % str(e)[:80]
            return False
        with self.lock:
            self.error = ""
            self.sent.insert(0, {"at": int(self.clock()), "title": title, "message": message})
            del self.sent[KEEP_SENT:]
        return True

    def test(self):
        ok = self.send("Kelly_Terminal test", "Phone alerts are working.", "white_check_mark")
        return dict(self.state(), ok=ok)

    # ---- the watch
    def _once(self, key, now):
        day = z.to_dublin(now).strftime("%Y-%m-%d")
        with self.lock:
            seen = self.rules["seen"]
            for d in [d for d in seen if d != day]:
                del seen[d]
            if key in seen.setdefault(day, []):
                return False
            seen[day].append(key)
            return True

    def check(self, now=None, quote=None, calendar=None):
        """One round. `quote(key)` gives a price and `calendar(now)` gives the releases; the tests swap both."""
        now = self.clock() if now is None else now
        if not self.topic():
            return 0
        quote = quote or (lambda k: macro.fetch_quote(self.net, KEYS[k])["price"])
        calendar = calendar or (lambda t: macro.fetch_calendar(self.net, t))
        fired = 0
        with self.lock:
            waiting = [a for a in self.rules["price"] if not a["done"]]
        prices = {}
        for key in sorted({a["sym"] for a in waiting}):
            try:
                prices[key] = quote(key)
            except Exception as e:
                with self.lock:
                    self.error = "Could not read %s: %s" % (key, str(e)[:60])
        changed = False
        for a in waiting:
            p = prices.get(a["sym"])
            if p is None:
                continue
            if p >= a["level"] if a["dir"] == "above" else p <= a["level"]:
                if self.send("%s %s %s" % (a["sym"], a["dir"], a["level"]), "Price is now %s." % p, "chart_with_upwards_trend", "high"):
                    a["done"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now))
                    changed = True
                    fired += 1
        if changed:
            with self.lock:
                self._save_rules()
        if self.rules["news"]:
            if now - self.events_at > CALENDAR_EVERY:
                try:
                    self.events, self.events_at = calendar(now), now
                except Exception:
                    self.events_at = now - CALENDAR_EVERY + 60      # try again in a minute
            for e in self.events:
                mins = round((e["t"] - now) / 60)
                if e.get("impact") == "High" and 0 < mins <= self.rules["newsMins"] and self._once("n|%s|%s" % (e["t"], e["title"]), now):
                    self.send("High-impact news in %d min" % mins,
                              "%s %s%s" % (e["country"], e["title"], " (forecast %s)" % e["forecast"] if e.get("forecast") else ""), "warning", "high")
                    fired += 1
        if self.rules["sess"]:
            for s in macro.sessions(now):
                if s["name"] in ("London", "New York") and not s["open"] and 0 < s["minutes"] <= 15 and self._once("s|" + s["name"], now):
                    self.send("%s opens in %d min" % (s["name"], s["minutes"]), "Check your plan and the checklist on Home.", "alarm_clock")
                    fired += 1
        with self.lock:
            self._save_rules()
        return fired

    def start(self):
        if self.thread:
            return

        def loop():
            time.sleep(25)
            while not self.stop.is_set():
                try:
                    self.check()
                except Exception as e:
                    with self.lock:
                        self.error = str(e)[:80]
                self.stop.wait(EVERY)
        self.thread = threading.Thread(target=loop, name="phone-alerts", daemon=True)
        self.thread.start()
