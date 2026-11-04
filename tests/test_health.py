import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

from _load import load

dash = load("riskdesk", "dashboard")
backup = load("riskdesk", "backup")
health = load("riskdesk", "health")


def make(files):
    d = tempfile.mkdtemp()
    for name, text in files.items():
        p = os.path.join(d, *name.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(text)
    return d


class Log(unittest.TestCase):
    def test_restarts_and_alerts(self):
        d = make({"server-restarts.log": "2026-11-03 18:01:53  server stopped (exit -1), starting it again\n"
                                         "2026-11-03 18:20:00  alert: PC started\n"
                                         "junk line\n"
                                         "2026-11-04 06:00:00  server stopped (exit 1), starting it again\n"})
        now = time.mktime(time.strptime("2026-11-04 07:00:00", "%Y-%m-%d %H:%M:%S"))
        out = health.read_log(d, now)
        self.assertEqual(out["restarts_24h"], 2)
        self.assertEqual([e["kind"] for e in out["events"]], ["restart", "alert", "restart"])    # newest first
        self.assertEqual(health.read_log(tempfile.mkdtemp(), now), {"restarts_24h": 0, "events": []})


class State(unittest.TestCase):
    def test_summary_and_hosted_check(self):
        d = make({"journal.json": "x" * 100, "config.json": json.dumps({})})
        seen = []

        def fetch(url):
            seen.append(url)
            return {"version": "abc12345"}
        h = health.Health(d, os.path.join(d, "config.json"), "v1", started=time.time() - 120, fetch=fetch)
        b = backup.Backups(d, os.path.join(d, "config.json"))
        b.run()
        first = h.state(b)
        self.assertEqual(first["version"], "v1")
        self.assertGreaterEqual(first["uptime"], 120)
        self.assertEqual(first["backup"]["count"], 1)
        self.assertGreater(first["data_bytes"], 99)
        for _ in range(50):                                    # the hosted check runs in the background
            if h.state(b)["hosted"]["ok"]:
                break
            time.sleep(0.05)
        self.assertEqual(h.state(b)["hosted"]["version"], "abc12345")
        self.assertEqual(seen, ["https://kellyterminal.com/version.json"])           # asked once, then reused for a minute
        self.assertIsNone(h.state(b, hosted_mode=True)["hosted"])                   # on the hosted copy it does not check itself

    def test_a_failed_hosted_check_says_so(self):
        d = make({"config.json": "{}"})

        def fetch(url):
            raise OSError("no route")
        h = health.Health(d, os.path.join(d, "config.json"), "v1", fetch=fetch)
        h.state(None)
        for _ in range(50):
            if h.state(None)["hosted"]["ok"] is False:
                break
            time.sleep(0.05)
        self.assertFalse(h.state(None)["hosted"]["ok"])
        self.assertIn("no route", h.state(None)["hosted"]["error"])


class Restore(unittest.TestCase):
    def setUp(self):
        self.dir = make({"journal.json": "NEW", "brain/notes.txt": "keep me", "config.json": "{}", "notion_key.txt": "secret-new"})
        self.b = backup.Backups(self.dir, os.path.join(self.dir, "config.json"))
        name = self.b.run()
        self.assertTrue(name)
        self.name = name
        for rel, text in (("journal.json", "OLD-AND-LONGER"), ("notion_key.txt", "secret-changed"), ("config.json", '{"x":1}')):
            with open(os.path.join(self.dir, rel), "w") as fh:
                fh.write(text)

    def test_preview_changes_nothing(self):
        p = self.b.preview(self.name)
        self.assertEqual(p["different"], 1)                    # only the journal differs; keys and config are never touched
        self.assertEqual(p["skipped"], 2)
        self.assertEqual(open(os.path.join(self.dir, "journal.json")).read(), "OLD-AND-LONGER")

    def test_restore_puts_back_data_keeps_settings_and_saves_a_safety_copy(self):
        out = self.b.restore(self.name)
        self.assertEqual(open(os.path.join(self.dir, "journal.json")).read(), "NEW")
        self.assertEqual(open(os.path.join(self.dir, "notion_key.txt")).read(), "secret-changed")
        self.assertEqual(open(os.path.join(self.dir, "config.json")).read(), '{"x":1}')
        self.assertEqual(out["restored"], 1)
        with zipfile.ZipFile(os.path.join(self.dir, "backups", out["safety"])) as z:
            self.assertEqual(z.read("journal.json").decode(), "OLD-AND-LONGER")        # what was there is kept, so a restore can be undone
        self.assertEqual(len(self.b.listing()), 1)                                    # the safety copy is not counted as a daily backup

    def test_only_real_backup_names_and_no_way_out_of_the_folder(self):
        for bad in ("../journal.json", "riskdesk-1.zip", "", "journal.json"):
            with self.assertRaises(backup.RestoreError):
                self.b.preview(bad)
        evil = os.path.join(self.dir, "backups", "riskdesk-20200101-0000.zip")
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../escaped.txt", "x")
            z.writestr("/abs.txt", "x")
            z.writestr("ok/inside.txt", "fine")
        out = self.b.restore("riskdesk-20200101-0000.zip")
        self.assertEqual(out["restored"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "ok", "inside.txt")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.dir), "escaped.txt")))

    def test_a_damaged_backup_is_refused(self):
        bad = os.path.join(self.dir, "backups", "riskdesk-20190101-0000.zip")
        with open(bad, "wb") as fh:
            fh.write(b"not a zip")
        with self.assertRaises(backup.RestoreError):
            self.b.preview("riskdesk-20190101-0000.zip")

    def test_old_safety_copies_are_pruned(self):
        for i in range(8):
            time.sleep(0.01)
            self.b.clock = lambda i=i: time.time() + i * 5
            self.b.restore(self.name)
        safe = [n for n in os.listdir(os.path.join(self.dir, "backups")) if n.startswith("before-restore-")]
        self.assertEqual(len(safe), 5)


class Routes(unittest.TestCase):
    def setUp(self):
        self.dir = make({"config.json": "{}", "journal.json": "A"})
        self.srv = dash.make_server(0, os.path.join(self.dir, "config.json"), offline=True)
        self.srv.RequestHandlerClass.health.fetch = lambda url: {"version": "x"}
        self.addCleanup(self.srv.server_close)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def call(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), method="GET" if body is None else "POST",
                                     headers={"X-Requested-With": "zones-dashboard", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_health_backup_and_restore_routes(self):
        code, h = self.call("/api/health")
        self.assertEqual(code, 200)
        self.assertIn("uptime", h)
        self.assertEqual(self.call("/api/backup/list")[1]["files"], [])
        code, made = self.call("/api/backup/run", {})
        self.assertEqual(code, 200)
        self.assertTrue(made["name"])
        files = self.call("/api/backup/list")[1]["files"]
        self.assertEqual(files[0]["name"], made["name"])
        with open(os.path.join(self.dir, "journal.json"), "w") as fh:
            fh.write("B-changed")
        code, prev = self.call("/api/backup/restore", {"name": made["name"]})
        self.assertEqual((code, prev["different"]), (200, 1))
        code, done = self.call("/api/backup/restore", {"name": made["name"], "apply": True})
        self.assertEqual((code, done["restored"]), (200, 1))
        self.assertEqual(open(os.path.join(self.dir, "journal.json")).read(), "A")
        self.assertEqual(self.call("/api/backup/restore", {"name": "../../x"})[0], 400)

    def test_the_page_has_the_health_section(self):
        page = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
        for needle in ('id="set-health"', 'id="hl-bk-run"', "/api/health", "/api/backup/restore", "hlLoad()"):
            self.assertIn(needle, page)


if __name__ == "__main__":
    unittest.main()
