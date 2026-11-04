#!/usr/bin/env python3
"""Kelly Terminal, a local trading dashboard: macro desk, journal, risk calculators and an AI assistant, in a browser.

    py riskdesk\\dashboard.py

opens http://127.0.0.1:8765 on your own computer. Nothing leaves your machine except the price, calendar and news requests
the Macro desk makes, and the AI questions you send with your own key when you press a button. It shows signals only. It never places an order.

Standard library only. The Zones project is separate (zones-app, port 8766). The page (dashboard.html next to this file) is self-contained with no outside scripts or fonts.
"""

import argparse
import html
import json
import os
import sys
import socketserver
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
import brain         # noqa: E402
import coach         # noqa: E402
import macro         # noqa: E402
import notion_api     # noqa: E402
import autobrief     # noqa: E402
import backup        # noqa: E402
import funding       # noqa: E402
import live          # noqa: E402
import markets       # noqa: E402
import notion_import  # noqa: E402
import pusher        # noqa: E402
import signin        # noqa: E402
import store         # noqa: E402
import tradezella_import  # noqa: E402
import vault         # noqa: E402

PORT = 8765


class Handler(BaseHTTPRequestHandler):
    config_path = os.path.join(TRADER, "config.json")
    offline = None
    allowed_hosts = frozenset()                      # extra names this dashboard answers to, for example a Tailscale name (see README)
    data_dir = TRADER                                # where the journal file, screenshots and import folder live
    coach_post = staticmethod(coach.api_post)       # the tests swap this so nothing goes out
    live = None                                      # last prices every few seconds while the page is open (live.py), set by make_server
    backups = None                                   # daily safety copy of the data folder (backup.py), set by make_server
    funding = None                                   # every prop-firm account and pay-out (funding.py), set by make_server
    mkt_fetch = None                                 # the Markets page asks Yahoo through this; tests put a fake here
    pusher = None                                    # phone alerts while the page is closed (pusher.py), set by make_server
    brief_keeper = None                              # writes the market read every hour (autobrief.py), set by make_server
    notion_link = None                               # the live read-only link to your Notion (notion_api.py), set by make_server
    auth_settings = None                             # sign in with Google, only when the config has an "auth" section (see signin.py)
    oauth_post = staticmethod(signin.http_post)     # the one call out to Google; the tests swap it
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

    def send_bytes(self, code, data, kind, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, code, doc):
        self.send(code, json.dumps(doc), "application/json; charset=utf-8")

    def host_ok(self):
        """Only answer requests addressed to this computer by name or number, which blocks other websites
        from reaching the dashboard through the browser (DNS rebinding)."""
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host in ("127.0.0.1:%d" % port, "localhost:%d" % port):
            return True
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host            # a proxy such as tailscale serve sends the name without the port
        return bool(name) and name.lower() in self.allowed_hosts

    def redirect(self, location, cookies=()):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()

    def blocked(self, api):
        """True when sign-in is on and this request has no valid session; the answer has then been sent."""
        s = self.auth_settings
        if not s or signin.current_user(s, self.headers.get("Cookie"), time.time()):
            return False
        if api:
            self.send_json(401, {"error": "sign in required", "signin": "/auth/login"})
        else:
            self.redirect("/auth/login")
        return True

    def auth_route(self, url):
        """The sign-in routes. True when this request was one of them."""
        s = self.auth_settings
        if not s or not url.path.startswith("/auth/"):
            return False
        now = time.time()
        if url.path == "/auth/login":
            where, state = signin.login_url(s, now)
            self.redirect(where, [signin.make_cookie(signin.STATE_COOKIE, state, s["secure"], signin.STATE_SECONDS)])
        elif url.path == "/auth/callback":
            gone = signin.make_cookie(signin.STATE_COOKIE, "", s["secure"], 0)
            try:
                state = signin.parse_cookies(self.headers.get("Cookie")).get(signin.STATE_COOKIE)
                email = signin.finish_login(s, parse_qs(url.query), state, now, self.oauth_post)
            except signin.AuthError as e:
                msg = html.escape(str(e))
                page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                        "<title>Kelly_Terminal sign-in</title><body style='font:16px system-ui;background:#0a0a0a;color:#f2f2f0;padding:40px'>"
                        "<h2>Could not sign you in</h2><p>%s.</p><p><a style='color:#fff' href='/auth/login'>Try again</a></p>" % msg)
                return self.send(403, page, "text/html; charset=utf-8")
            self.redirect("/", [signin.session_cookie(s, email, now), gone])
        elif url.path == "/auth/logout":
            self.redirect("/auth/login", [signin.make_cookie(signin.SESSION_COOKIE, "", s["secure"], 0)])
        else:
            self.send_json(404, {"error": "not found"})
        return True

    SLOW_POSTS = ("/api/chat", "/api/coach", "/api/macro/brief", "/api/notion/refresh", "/api/import/notion", "/api/import/tradezella")
    WRITE_LOCK = threading.Lock()
    timeout = 120                                                # a connection that sends nothing for two minutes is dropped, so it cannot hold a thread

    def do_POST(self):
        """Saves run one at a time (two at once could overwrite each other); the slow AI and import routes do not wait for that lock."""
        if urlparse(self.path).path in self.SLOW_POSTS:
            return self._post()
        with self.WRITE_LOCK:
            return self._post()

    def _post(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        if self.blocked(True):
            return
        if self.headers.get("X-Requested-With") != "zones-dashboard":
            return self.send_json(403, {"error": "missing X-Requested-With header"})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            route = urlparse(self.path).path
            if route == "/api/journal/image":                    # the raw bytes of a screenshot, not JSON
                if not 0 < size <= store.MAX_IMAGE:
                    raise store.StoreError("the image is empty or larger than %d MB" % (store.MAX_IMAGE // 1024 // 1024))
                return self.send_json(200, {"name": store.save_image(self.data_dir, self.rfile.read(size))})
            limit = 150000 if route in ("/api/coach", "/api/chat") else 12000000 if route in ("/api/journal", "/api/brain", "/api/vault/doc") else 20000
            if not 0 < size <= limit:
                raise z.ZoneError("the request body is empty or too large")
            try:
                body = json.loads(self.rfile.read(size).decode("utf-8"))
            except ValueError:
                raise z.ZoneError("the request body is not valid JSON")
            if not isinstance(body, dict):
                raise z.ZoneError("the request body must be a JSON object")
            path = urlparse(self.path).path
            if path == "/api/coach":
                pv = coach.provider(self.config_path)
                answer = coach.ask(body.get("trades"), str(body.get("question") or ""), body.get("indicator"), pv["key"],
                                   pv["model"], self.coach_post, pv["name"])
                return self.send_json(200, {"answer": answer})
            elif path == "/api/journal":
                return self.send_json(200, {"saved": store.save_trades(self.data_dir, body.get("trades"))})
            elif path.startswith("/api/vault/"):
                return self.vault_post(path, body)
            elif path == "/api/brain":
                try:
                    return self.send_json(200, {"rev": brain.save(self.data_dir, body)})
                except brain.Conflict as e:
                    return self.send_json(409, {"error": str(e)})
            elif path == "/api/import/notion":
                return self.notion(bool(body.get("apply")))
            elif path == "/api/brain/fill":
                link = self.notion_link
                if not link:
                    raise brain.BrainError("No Notion link is set up")
                if body.get("refresh"):
                    link.refresh()
                    if link.state.get("error"):
                        raise brain.BrainError(link.state["error"])
                link.filled_at = link.hub_doc.get("at")
                return self.send_json(200, {"filled": brain.fill_from_hub(self.data_dir, link.hub_doc), "hub_at": link.hub_doc.get("at", "")})
            elif path == "/api/notion/refresh":
                if not self.notion_link:
                    return self.send_json(200, {"connected": False})
                return self.send_json(200, self.notion_link.refresh(with_hub=bool(body.get("hub", True))))
            elif path == "/api/push":
                act = body.get("action")
                if act == "enable":
                    return self.send_json(200, self.pusher.enable())
                if act == "disable":
                    return self.send_json(200, self.pusher.disable())
                if act == "test":
                    return self.send_json(200, self.pusher.test())
                if act == "rules":
                    return self.send_json(200, self.pusher.set_rules(body.get("rules")))
                raise z.ZoneError('action must be "enable", "disable", "test" or "rules"')
            elif path == "/api/funding":
                return self.send_json(200, self.funding.act(body))
            elif path == "/api/mkt/layout":
                return self.send_json(200, markets.save_layout(self.data_dir, body))
            elif path == "/api/import/tradezella":
                return self.tradezella(bool(body.get("apply")))
            elif path == "/api/macro/brief":
                now = z.parse_now(str(body.get("now") or "") or None)
                snap = macro.snapshot(now, self.macro_net)
                pv = coach.provider(self.config_path)
                answer = coach.generate(macro.BRIEF_SYSTEM, macro.brief_messages(snap, str(body.get("focus") or "")), pv,
                                        self.coach_post, 3200)
                parts = macro.split_brief(answer)
                doc = self.brief_keeper.store(answer, snap["as_of"], parts) if self.brief_keeper else {}
                return self.send_json(200, dict(doc) if doc else dict(macro.desk(parts), answer=answer, as_of=snap["as_of"],
                                                                      parts=parts, at=None, auto=False))
            elif path == "/api/chat":
                now = z.parse_now(str(body.get("now") or "") or None)
                msgs = coach.clean_messages(body.get("messages"))
                known = macro.latest(now)
                system = coach.chat_system(body.get("context"), body.get("trades"), macro.compact(known) if known else "")
                pv = coach.provider(self.config_path)
                return self.send_json(200, {"answer": coach.generate(system, msgs, pv, self.coach_post, 1200)})
            else:
                return self.send_json(404, {"error": "not found"})
        except (z.ZoneError, coach.CoachError, store.StoreError, brain.BrainError, funding.FundingError, markets.MarketsError, vault.VaultError, notion_import.ImportError_, tradezella_import.TradeZellaError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        url = urlparse(self.path)
        if url.path in APP_FILES:                       # what the browser needs to install Kelly Terminal as an app: the icon, not your data
            data, kind = APP_FILES[url.path]
            cache = ("no-store" if url.path in ("/sw.js", "/brain.js", "/brain.css")
                     else "public, max-age=86400" if url.path.startswith("/fonts/")
                     else "no-cache")                  # name, icons and manifest: the browser asks again, so a rename shows up at once
            return self.send_bytes(200, data, kind, cache)
        if url.path == "/version.json":                 # which version is running: no data in it, so the sign-in does not apply
            return self.send_json(200, {"version": VERSION})
        if self.auth_route(url) or self.blocked(url.path.startswith("/api/")):
            return
        if url.path == "/":
            self.send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/api/macro":
            self.macro(parse_qs(url.query))
        elif url.path == "/api/funding":
            self.send_json(200, self.funding.state())
        elif url.path == "/api/mkt/layout":
            self.send_json(200, dict(markets.load_layout(self.data_dir), suggest=markets.SUGGEST, grids=list(markets.GRIDS), tfs=list(markets.TIMEFRAMES)))
        elif url.path == "/api/mkt/charts":
            q = parse_qs(url.query)
            pairs = []
            for item in (q.get("c") or [""])[0].split(","):
                if ":" in item:
                    pairs.append(tuple(item.rsplit(":", 1)))
            if not pairs:
                return self.send_json(400, {"error": "ask for charts like c=GC=F:5m,NQ=F:5m"})
            try:
                self.send_json(200, markets.many(pairs, self.mkt_fetch, ttl=2.5))
            except Exception as e:
                self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})
        elif url.path == "/api/push/status":
            self.send_json(200, self.pusher.state())
        elif url.path == "/api/notion/status":
            self.send_json(200, self.notion_link.state if self.notion_link else {"connected": False})
        elif url.path == "/api/notion/journal":
            doc = self.notion_link.journal_doc if self.notion_link else {"at": "", "trades": []}
            self.send_json(200, {"at": doc.get("at", ""), "trades": doc.get("trades") or []})
        elif url.path == "/api/vault/status":
            self.send_json(200, self.vault.status(self.headers.get("X-Vault-Token")))
        elif url.path == "/api/vault/doc":
            try:
                self.send_json(200, {"doc": self.vault.load(self.headers.get("X-Vault-Token"))})
            except vault.Locked:
                self.send_json(401, {"error": "locked"})
        elif url.path == "/api/brain":
            try:
                doc = brain.load(self.data_dir)
                seeded = doc is None
                if seeded:
                    doc = brain.seed(self.data_dir)
                link = self.notion_link
                if link and link.hub_doc.get("pages") and getattr(link, "filled_at", None) != link.hub_doc.get("at"):
                    link.filled_at = link.hub_doc.get("at")          # empty pages pick up what the Notion copy has, once per copy
                    if brain.fill_from_hub(self.data_dir, link.hub_doc):
                        doc = brain.load(self.data_dir)
            except brain.BrainError as e:
                return self.send_json(500, {"error": str(e)})
            self.send_json(200, {"doc": doc, "seeded": seeded})
        elif url.path == "/api/macro/brief":
            self.send_json(200, self.brief_keeper.state() if self.brief_keeper else {"brief": None, "error": "", "every": autobrief.EVERY, "next_at": 0})
        elif url.path == "/api/backup":
            self.send_json(200, self.backups.state() if self.backups else {"last": None, "count": 0, "error": ""})
        elif url.path == "/api/notion/hub":
            self.send_json(200, self.notion_link.hub_doc if self.notion_link else {"at": "", "root": "", "pages": {}})
        elif url.path == "/api/tradezella/summary":
            try:
                with open(os.path.join(self.data_dir, "tradezella", "summary.json"), encoding="utf-8-sig") as fh:
                    self.send_json(200, {"exists": True, "summary": json.load(fh)})
            except (OSError, ValueError):
                self.send_json(200, {"exists": False})
        elif url.path == "/api/journal":
            try:
                saved = store.load_trades(self.data_dir)
            except store.StoreError as e:
                return self.send_json(500, {"error": str(e)})
            self.send_json(200, {"exists": saved is not None, "trades": saved or []})
        elif url.path.startswith("/api/img/"):
            found = store.read_image(self.data_dir, url.path[len("/api/img/"):])
            if found:
                self.send_bytes(200, found[0], found[1], "private, max-age=31536000, immutable")     # the name is a fingerprint of the picture
            else:
                self.send_json(404, {"error": "not found"})
        elif url.path == "/api/ticks":
            if not self.live:
                return self.send_json(200, {"at": 0, "every": live.EVERY, "live": False, "error": "", "quotes": {}, "spot": None, "checks": {}})
            self.live.poke()
            self.send_json(200, self.live.state())
        elif url.path == "/api/quality":
            if not self.live:
                return self.send_json(200, {"at": 0, "live": False, "error": "", "rows": [], "spot": None, "checks": {}, "td": {"state": "off"}})
            self.live.poke()
            self.send_json(200, self.live.quality())
        elif url.path == "/api/connections":
            # what is set up, for the Connections page: yes or no only, never a key
            self.send_json(200, {"hosted": bool(self.auth_settings), "backup_prices": bool(macro.twelve_key(self.data_dir))})
        elif url.path == "/api/coach/status":
            pv = coach.provider(self.config_path)
            self.send_json(200, {"ready": bool(pv["key"]), "service": pv["name"], "model": pv["model"]})
        else:
            self.send_json(404, {"error": "not found"})

    def vault_post(self, path, body):
        """The password-locked pages. The password is only ever read from the request, never kept or logged."""
        token = self.headers.get("X-Vault-Token")
        try:
            if path == "/api/vault/setup":
                return self.send_json(200, {"token": self.vault.setup(str(body.get("password") or ""))})
            if path == "/api/vault/unlock":
                return self.send_json(200, {"token": self.vault.unlock(str(body.get("password") or ""))})
            if path == "/api/vault/lock":
                self.vault.lock(token)
                return self.send_json(200, {"locked": True})
            if path == "/api/vault/password":
                return self.send_json(200, {"token": self.vault.change_password(token, str(body.get("password") or ""))})
            if path == "/api/vault/doc":
                try:
                    return self.send_json(200, {"rev": self.vault.save(token, body)})
                except brain.Conflict as e:
                    return self.send_json(409, {"error": str(e)})
            if path == "/api/vault/import":
                self.vault._check(token)                                          # must be unlocked before Notion is read at all
                link = self.notion_link
                if not (link and link.token):
                    raise vault.VaultError("No Notion key yet (trader\\notion_key.txt)")
                cfg = notion_api.settings(self.config_path)
                try:
                    hub = notion_api.private_hub(notion_api.Client(link.token, link.opener, link.pause), self.data_dir, cfg["hub_page"])
                except notion_api.NotionError as e:
                    raise vault.VaultError(str(e))
                return self.send_json(200, {"doc": self.vault.import_hub(token, hub)})
        except vault.Locked:
            return self.send_json(401, {"error": "locked"})
        self.send_json(404, {"error": "not found"})

    def notion(self, apply):
        """Read the Notion export in trader/import. Preview only reads; apply also keeps the screenshots and returns the trades."""
        src = notion_import.open_source(os.path.join(self.data_dir, "import"))
        try:
            trades, report = notion_import.convert(src)
            if apply:
                for t in trades:
                    path = t.pop("image", None)
                    if path:
                        try:
                            t["img"] = store.save_image(self.data_dir, src.read(path))
                        except store.StoreError:
                            report["missing_image"] += 1
                            report["with_image"] -= 1
            else:
                for t in trades:
                    t.pop("image", None)
        finally:
            src.close()
        self.send_json(200, {"applied": apply, "report": report, "sample": trades[:5], "trades": trades if apply else []})

    def tradezella(self, apply):
        """Read the TradeZella csv files in trader/tradezella. Preview only reads; apply also returns the trades."""
        trades, report = tradezella_import.convert(os.path.join(self.data_dir, "tradezella"))
        self.send_json(200, {"applied": apply, "report": report, "sample": trades[:5], "trades": trades if apply else []})

    def macro(self, q):
        try:
            now = z.parse_now((q.get("now") or [""])[0] or None)
            force = (q.get("refresh") or [""])[0] == "1"
            self.send_json(200, macro.snapshot(now, self.macro_net, force))
        except (z.ZoneError, macro.MacroError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})


class DashboardServer(socketserver.ThreadingMixIn, HTTPServer):
    # On Windows, address reuse lets a second dashboard bind a port that is already in use and run beside the
    # first without any error, so it is turned off there. Elsewhere it only helps a quick restart.
    allow_reuse_address = os.name != "nt"
    # Every request gets its own thread, so a slow price refresh or an AI answer never makes the page, the live ticks or the
    # charts wait or be refused behind it. Saves are still taken one at a time (WRITE_LOCK in Handler.do_POST).
    daemon_threads = True
    request_queue_size = 64


def configured_hosts(config_path, extra=()):
    """Names from "allowed_hosts" in the config file plus any given on the command line, lower case, plain host names only."""
    names = list(extra or ())
    try:
        with open(config_path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict) and isinstance(cfg.get("allowed_hosts"), list):
            names += [n for n in cfg["allowed_hosts"] if isinstance(n, str)]
    except (OSError, ValueError):
        pass
    return frozenset(n.strip().lower() for n in names if n.strip() and all(c.isalnum() or c in ".-" for c in n.strip()))


def make_server(port=PORT, config_path=os.path.join(TRADER, "config.json"), offline=None, allowed_hosts=()):
    handler = type("Bound", (Handler,), {"config_path": config_path, "offline": offline, "allowed_hosts": configured_hosts(config_path, allowed_hosts),
                                          "auth_settings": signin.load_settings(config_path),
                                          "data_dir": os.path.dirname(os.path.abspath(config_path))})
    handler.notion_link = notion_api.Link(handler.data_dir, config_path)
    handler.vault = vault.Vault(handler.data_dir)
    handler.brief_keeper = autobrief.Keeper(handler.data_dir, config_path, handler.macro_net, lambda *a, **k: handler.coach_post(*a, **k))
    handler.backups = backup.Backups(handler.data_dir, config_path)
    handler.live = None if offline else live.LiveTicks(handler.macro_net)
    handler.pusher = pusher.Pusher(handler.data_dir, handler.macro_net)
    handler.funding = funding.Funding(handler.data_dir, lambda: handler.notion_link.hub_doc if handler.notion_link else {})
    return DashboardServer(("127.0.0.1", port), handler)


def _version():
    """Which copy of the code this is: the git commit when there is one, otherwise a fingerprint of the page. The page shows it
    in Settings and uses it to notice that the server has been updated, so an app left open on a phone refreshes itself."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        import subprocess
        out = subprocess.run(["git", "-C", here, "rev-parse", "--short=8", "HEAD"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, ValueError, Exception):
        pass
    import hashlib
    with open(os.path.join(here, "dashboard.html"), "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()[:8]


VERSION = _version()
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"), encoding="utf-8") as _fh:
    PAGE = _fh.read().replace("__KELLY_VERSION__", VERSION)


def _app_files():
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    kinds = {"manifest.webmanifest": "application/manifest+json", "sw.js": "application/javascript; charset=utf-8",
             "brain.js": "application/javascript; charset=utf-8", "brain.css": "text/css; charset=utf-8",
             "icon-192.png": "image/png", "icon-512.png": "image/png", "icon-maskable-512.png": "image/png",
             "apple-touch-icon.png": "image/png", "logo.svg": "image/svg+xml", "fonts/geist.woff2": "font/woff2", "fonts/geist-mono.woff2": "font/woff2"}
    out = {}
    for name, kind in kinds.items():
        with open(os.path.join(here, name), "rb") as fh:
            out["/" + name] = (fh.read(), kind)
    return out


APP_FILES = _app_files()


def already_running(port):
    """True when the thing holding the port is this dashboard (it answers the status route)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/coach/status" % port, timeout=2) as r:
            return "ready" in json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return False


def main(argv=None):
    p = argparse.ArgumentParser(prog="dashboard.py", description="Kelly Terminal local dashboard (signals only)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--config", default=os.path.join(TRADER, "config.json"))
    p.add_argument("--offline", help="not used by Kelly Terminal, kept so old shortcuts still work")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    p.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                   help="also answer to this host name, for a private tunnel such as Tailscale (repeatable; or set allowed_hosts in config.json)")
    args = p.parse_args(argv)
    try:
        server = make_server(args.port, args.config, args.offline, args.allow_host)
    except signin.AuthError as e:                                       # sign-in was asked for but is not complete: never serve unlocked
        print("error: sign-in is not set up correctly: %s" % e, file=sys.stderr)
        return 2
    except OSError as e:
        url = "http://127.0.0.1:%d" % args.port
        if already_running(args.port):                                  # a second double-click just opens the one that is running
            print("Kelly Terminal is already running at %s. Opening it." % url)
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
        if server.RequestHandlerClass.notion_link.token:
            server.RequestHandlerClass.notion_link.start()
        server.RequestHandlerClass.backups.start()
        if server.RequestHandlerClass.live and not args.offline:
            server.RequestHandlerClass.live.start()
        if not args.offline:
            server.RequestHandlerClass.brief_keeper.start()
            server.RequestHandlerClass.pusher.start()
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
