#!/usr/bin/env python3
"""RiskDesk.ai, a local trading dashboard: macro desk, journal, risk calculators and an AI assistant, in a browser.

    py riskdesk\\dashboard.py

opens http://127.0.0.1:8765 on your own computer. Nothing leaves your machine except the price, calendar and news requests
the Macro desk makes, and the AI questions you send with your own key when you press a button. It shows signals only. It never places an order.

Standard library only. The Zones project is separate (zones-app, port 8766). The page (dashboard.html next to this file) is self-contained with no outside scripts or fonts.
"""

import argparse
import json
import os
import sys
import threading
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


class Handler(BaseHTTPRequestHandler):
    config_path = os.path.join(TRADER, "config.json")
    offline = None
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
            if path == "/api/coach":
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
        elif url.path == "/api/macro":
            self.macro(parse_qs(url.query))
        elif url.path == "/api/coach/status":
            pv = coach.provider(self.config_path)
            self.send_json(200, {"ready": bool(pv["key"]), "service": pv["name"], "model": pv["model"]})
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


class DashboardServer(HTTPServer):
    # On Windows, address reuse lets a second dashboard bind a port that is already in use and run beside the
    # first without any error, so it is turned off there. Elsewhere it only helps a quick restart.
    allow_reuse_address = os.name != "nt"


def make_server(port=PORT, config_path=os.path.join(TRADER, "config.json"), offline=None):
    handler = type("Bound", (Handler,), {"config_path": config_path, "offline": offline})
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
