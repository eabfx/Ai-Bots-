import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _load import load

dash = load("riskdesk", "dashboard")
workspace = load("riskdesk", "workspace")


class Notes(unittest.TestCase):
    def test_save_replace_remove_and_reject(self):
        d = tempfile.mkdtemp()
        self.assertEqual(workspace.load(d), {})
        self.assertEqual(workspace.save(d, "levels", [{"s": "GC=F", "p": 3950}]), {"levels": [{"s": "GC=F", "p": 3950}]})
        workspace.save(d, "goal", {"month": "2026-11", "target": 5})
        self.assertEqual(set(workspace.load(d)), {"levels", "goal"})
        self.assertEqual(workspace.save(d, "levels", None), {"goal": {"month": "2026-11", "target": 5}})
        with self.assertRaises(workspace.WorkspaceError):
            workspace.save(d, "passwords", {})
        with self.assertRaises(workspace.WorkspaceError):
            workspace.save(d, "wrapups", {"x": "y" * (workspace.MAX_NOTE + 1)})
        self.assertFalse([n for n in os.listdir(d) if n.endswith(".tmp")])

    def test_a_damaged_file_reads_as_empty(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, workspace.FILE), "w") as fh:
            fh.write("{not json")
        self.assertEqual(workspace.load(d), {})
        with open(os.path.join(d, workspace.FILE), "w") as fh:
            json.dump({"levels": [], "stray": 1}, fh)
        self.assertEqual(workspace.load(d), {"levels": []})


class Routes(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        cfg = os.path.join(self.dir, "config.json")
        open(cfg, "w").write("{}")
        self.srv = dash.make_server(0, cfg, offline=True)
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

    def test_get_and_post(self):
        self.assertEqual(self.call("/api/workspace"), (200, {}))
        code, doc = self.call("/api/workspace", {"key": "levels", "value": [{"s": "NQ=F", "p": 25000}]})
        self.assertEqual((code, doc), (200, {"levels": [{"s": "NQ=F", "p": 25000}]}))
        self.assertEqual(self.call("/api/workspace")[1]["levels"][0]["p"], 25000)
        self.assertEqual(self.call("/api/workspace", {"key": "nope", "value": 1})[0], 400)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "workspace.json")))


if __name__ == "__main__":
    unittest.main()
