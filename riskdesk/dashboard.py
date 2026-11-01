#!/usr/bin/env python3
"""RiskDesk.ai, a local trading dashboard: macro desk, journal, risk calculators and an AI assistant, in a browser.

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
import notion_import  # noqa: E402
import signin        # noqa: E402
import store         # noqa: E402

PORT = 8765


class Handler(BaseHTTPRequestHandler):
    config_path = os.path.join(TRADER, "config.json")
    offline = None
    allowed_hosts = frozenset()                      # extra names this dashboard answers to, for example a Tailscale name (see README)
    data_dir = TRADER                                # where the journal file, screenshots and import folder live
    coach_post = staticmethod(coach.api_post)       # the tests swap this so nothing goes out
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
                        "<title>RiskDesk sign-in</title><body style='font:16px system-ui;background:#0a0a0a;color:#f2f2f0;padding:40px'>"
                        "<h2>Could not sign you in</h2><p>%s.</p><p><a style='color:#fff' href='/auth/login'>Try again</a></p>" % msg)
                return self.send(403, page, "text/html; charset=utf-8")
            self.redirect("/", [signin.session_cookie(s, email, now), gone])
        elif url.path == "/auth/logout":
            self.redirect("/auth/login", [signin.make_cookie(signin.SESSION_COOKIE, "", s["secure"], 0)])
        else:
            self.send_json(404, {"error": "not found"})
        return True

    def do_POST(self):
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
            limit = 150000 if route in ("/api/coach", "/api/chat") else 12000000 if route == "/api/journal" else 20000
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
            elif path == "/api/import/notion":
                return self.notion(bool(body.get("apply")))
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
        except (z.ZoneError, coach.CoachError, store.StoreError, notion_import.ImportError_) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        if not self.host_ok():
            return self.send_json(403, {"error": "this dashboard only answers requests from this computer"})
        url = urlparse(self.path)
        if self.auth_route(url) or self.blocked(url.path.startswith("/api/")):
            return
        if url.path == "/":
            self.send(200, PAGE, "text/html; charset=utf-8")
        elif url.path == "/api/macro":
            self.macro(parse_qs(url.query))
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
        elif url.path == "/api/coach/status":
            pv = coach.provider(self.config_path)
            self.send_json(200, {"ready": bool(pv["key"]), "service": pv["name"], "model": pv["model"]})
        else:
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

    def macro(self, q):
        try:
            now = z.parse_now((q.get("now") or [""])[0] or None)
            force = (q.get("refresh") or [""])[0] == "1"
            self.send_json(200, macro.snapshot(now, self.macro_net, force))
        except (z.ZoneError, macro.MacroError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})


class DashboardServer(HTTPServer):
    # On Windows, address reuse lets a second dashboard bind a port that is already in use and run beside the
    # first without any error, so it is turned off there. Elsewhere it only helps a quick restart.
    allow_reuse_address = os.name != "nt"


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
    p = argparse.ArgumentParser(prog="dashboard.py", description="RiskDesk.ai local dashboard (signals only)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--config", default=os.path.join(TRADER, "config.json"))
    p.add_argument("--offline", help="not used by RiskDesk, kept so old shortcuts still work")
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
