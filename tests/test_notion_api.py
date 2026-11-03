import io
import json
import os
import tempfile
import sys
import unittest
import urllib.error

from _load import ROOT, load

sys.path.insert(0, os.path.join(ROOT, "riskdesk"))            # notion_api imports its neighbours the way the dashboard does
api = load("riskdesk", "notion_api")
PAGE = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
SERVER = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.py"), encoding="utf-8").read()

DB = "a" * 32
HUB = "b" * 32
SUB = "c" * 32
PRIVATE = "d" * 32
INLINE = "e" * 32
TBL = "1" * 32
COLS = "2" * 32
COL = "3" * 32
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


def rt(text, **ann):
    return [{"plain_text": text, "annotations": ann, "href": None}]


def ms(*names):
    return {"type": "multi_select", "multi_select": [{"name": n} for n in names]}


def row(pid, date, pair, dirn, wl, rr, risk, acct, no, profit=None, files=()):
    return {"id": pid, "properties": {
        "Date": {"type": "date", "date": {"start": date}}, "Pair": ms(pair), "Long / Short": ms(dirn), "Win / Loss": ms(*wl), "R/R": ms(rr),
        "Risk": ms(risk), "Account Type": ms(acct), "Trade No.": {"type": "number", "number": no},
        "Profit": {"type": "number", "number": profit}, "Strategy ": ms("OG"), "Setup": ms("Double Top"),
        "Trade Breakdown": {"type": "rich_text", "rich_text": rt("note")},
        "Trade SS": {"type": "files", "files": list(files)}, "Strategy + Setup": {"type": "title", "title": rt("t")}}}


class FakeNotion:
    def __init__(self, fail=None):
        self.log, self.fail = [], fail

    def __call__(self, req, timeout=0):
        method, path = req.get_method(), req.full_url.replace(api.API, "")
        self.log.append((method, path))
        if self.fail:
            raise urllib.error.HTTPError(req.full_url, self.fail, "x", {}, io.BytesIO(b"{}"))
        p = path.split("?")[0]
        if p == "/v1/databases/%s/query" % DB:
            data = {"results": [
                row("p1", "2026-10-28", "MNQ", "SHORT", ["WIN"], "3RR", "0.5%", "Funded Account", 475, 800,
                    [{"name": "t.png", "type": "file", "file": {"url": "https://files.example/t.png"}}]),
                {"id": "p2", "properties": {"Date": {"type": "date", "date": {"start": "2026-11-02"}}, "Trade No.": {"type": "number", "number": 485},
                                            "Pair": ms(), "Win / Loss": ms(), "Account Type": ms()}}], "has_more": False}
        elif p == "/v1/blocks/%s/children" % HUB:
            data = {"results": [
                {"id": "h1", "type": "heading_2", "has_children": False, "heading_2": {"rich_text": rt("RISK", bold=True), "color": "red_background"}},
                {"id": TBL, "type": "table", "has_children": True, "table": {"has_column_header": True, "has_row_header": False}},
                {"id": COLS, "type": "column_list", "has_children": True, "column_list": {}},
                {"id": SUB, "type": "child_page", "has_children": True, "child_page": {"title": "OG CHECKLIST"}},
                {"id": PRIVATE, "type": "child_page", "has_children": True, "child_page": {"title": "Thoughts + Emotions"}},
                {"id": INLINE, "type": "child_database", "has_children": False, "child_database": {"title": "Scaling"}},
                {"id": "i1", "type": "image", "has_children": False, "image": {"type": "file", "file": {"url": "https://files.example/i.png"}, "caption": []}},
                {"id": "x1", "type": "synced_block", "has_children": False, "synced_block": {}}], "has_more": False}
        elif p == "/v1/blocks/%s/children" % TBL:
            data = {"results": [{"type": "table_row", "table_row": {"cells": [rt("Firm"), rt("Limit")]}},
                                {"type": "table_row", "table_row": {"cells": [rt("FTMO"), rt("5%")]}}], "has_more": False}
        elif p == "/v1/blocks/%s/children" % COLS:
            data = {"results": [{"id": COL, "type": "column", "has_children": True, "column": {}}], "has_more": False}
        elif p == "/v1/blocks/%s/children" % COL:
            data = {"results": [{"id": "b1", "type": "to_do", "has_children": False, "to_do": {"rich_text": rt("Back-test 500"), "checked": True}}], "has_more": False}
        elif p == "/v1/blocks/%s/children" % SUB:
            data = {"results": [{"id": "s1", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": rt("check one")}}], "has_more": False}
        elif p == "/v1/databases/%s" % INLINE:
            data = {"title": rt("Scaling"), "properties": {"Level": {"type": "number"}, "Name": {"type": "title"}}}
        elif p == "/v1/databases/%s/query" % INLINE:
            data = {"results": [{"properties": {"Name": {"type": "title", "title": rt("Step 1")}, "Level": {"type": "number", "number": 1}}}], "has_more": False}
        else:
            raise AssertionError("unexpected call " + method + " " + path)
        raw = json.dumps(data).encode()
        return type("R", (), {"read": lambda s: raw, "__enter__": lambda s: s, "__exit__": lambda s, *a: None})()


def fetch(url):
    return PNG


class Gate(unittest.TestCase):
    def test_only_reading_is_allowed(self):
        c = api.Client("tok", FakeNotion(), pause=0)
        for method, path in (("PATCH", "/v1/pages/%s" % DB), ("DELETE", "/v1/blocks/%s" % DB), ("POST", "/v1/pages"), ("POST", "/v1/blocks/%s/children" % DB),
                             ("PUT", "/v1/databases/%s" % DB), ("POST", "/v1/search"), ("GET", "/v1/users")):
            with self.assertRaises(api.NotionError, msg=method + path):
                c.call(method, path)
        self.assertEqual(c.opener.log, [])                              # nothing was even sent


class Journal(unittest.TestCase):
    def test_rows_become_journal_trades_with_their_screenshot_and_profit(self):
        d = tempfile.mkdtemp()
        c = api.Client("tok", FakeNotion(), pause=0)
        trades, report, meta = api.journal(c, DB)
        trades = api.attach_images(d, trades, meta, fetch, {})
        by = {t["no"]: t for t in trades}
        t = by[475]
        self.assertEqual((t["date"], t["inst"], t["dir"], t["acct"], t["acctType"], t["r"]), ("2026-10-28", "MNQ", "short", "live", "Funded Account", 3.0))
        self.assertAlmostEqual(t["pl"], 1.5)
        self.assertIn("Profit +$800", t["notes"])
        self.assertRegex(t["img"], r"^[0-9a-f]{16}\.png$")
        self.assertTrue(os.path.isfile(os.path.join(d, "journal", "images", t["img"])))
        self.assertEqual(by[485]["skip"], "none")                       # a row made ahead of time, nothing in it yet
        self.assertTrue(all("row" not in x for x in trades))


class HubPages(unittest.TestCase):
    def test_pages_tables_columns_databases_images_and_the_private_page_stays_unread(self):
        d, fake = tempfile.mkdtemp(), FakeNotion()
        h = api.hub(api.Client("tok", fake, pause=0), d, HUB, fetch, {})
        root = h["pages"][HUB]["nodes"]
        kinds = [n["t"] for n in root]
        self.assertEqual(kinds, ["h2", "table", "cols", "page", "page", "db", "img"])      # the empty synced block is left out
        self.assertEqual(root[0]["co"], "red_background")
        self.assertEqual(root[1]["rows"][1][1][0]["x"], "5%")
        self.assertEqual(root[2]["cols"][0]["k"][0], {"t": "todo", "r": [{"x": "Back-test 500"}], "done": True, "k": []})
        self.assertEqual(root[5]["cols"], ["Name", "Level"])
        self.assertEqual(root[5]["rows"], [["Step 1", "1"]])
        self.assertIn(SUB, h["pages"])
        self.assertEqual(h["pages"][SUB]["nodes"][0]["r"][0]["x"], "check one")
        self.assertNotIn(PRIVATE, h["pages"])
        self.assertTrue(root[4]["locked"])
        self.assertFalse([c for c in fake.log if PRIVATE in c[1]])        # never even asked for
        self.assertTrue(set(m for m, _ in fake.log) <= {"GET", "POST"})


class LinkRuns(unittest.TestCase):
    def test_refresh_saves_a_copy_and_keeps_it_when_notion_later_fails(self):
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"notion": {"journal_db": DB, "hub_page": HUB}}, fh)
        link = api.Link(d, cfg, token="tok", opener=FakeNotion(), fetch=fetch, pause=0)
        st = link.refresh()
        self.assertEqual((st["error"], st["journal_rows"], st["hub_pages"]), ("", 2, 2))
        self.assertTrue(os.path.isfile(os.path.join(d, "notion", "hub.json")))
        again = api.Link(d, cfg, token="tok", opener=FakeNotion(fail=401), fetch=fetch, pause=0)
        self.assertEqual(again.state["journal_rows"], 2)                  # the saved copy is loaded at start
        st = again.refresh()
        self.assertIn("secret was not accepted", st["error"])
        self.assertEqual(len(again.journal_doc["trades"]), 2)             # and kept

    def test_no_secret_means_a_plain_instruction_not_a_crash(self):
        d = tempfile.mkdtemp()
        link = api.Link(d, os.path.join(d, "config.json"), token="")
        self.assertFalse(link.state["connected"])
        self.assertIn("notion_key.txt", link.refresh()["error"])

    def test_the_secret_comes_from_the_environment_or_the_trader_folder(self):
        d = tempfile.mkdtemp()
        self.assertEqual(api.read_token(d, {}), "")
        with open(os.path.join(d, "notion_key.txt"), "w") as fh:
            fh.write("  secret_abc \n")
        self.assertEqual(api.read_token(d, {}), "secret_abc")
        self.assertEqual(api.read_token(d, {"NOTION_TOKEN": "envtok"}), "envtok")


class Wiring(unittest.TestCase):
    def test_server_routes(self):
        for r in ("/api/notion/status", "/api/notion/journal", "/api/notion/hub", "/api/notion/refresh"):
            self.assertIn(r, SERVER)
        self.assertIn("notion_link.start()", SERVER)

    def test_the_second_brain_tab_and_the_journal_merge_are_in_the_page(self):
        for needle in ('data-tab="brain">Second Brain<', 'class="brainbtn"', "background:var(--accent);color:var(--onaccent)", 'id="tab-brain"', 'id="sb-root"', 'src="/brain.js"', "function nsSync(force)", "/api/notion/journal",
                       "/api/notion/refresh", 'id="nl-card"', "Read content", "Thoughts + Emotions"):
            self.assertIn(needle, PAGE)
        self.assertNotIn("#9a92ff", PAGE)                            # the button is plain white and black now
        self.assertIn('"home","macro","journal","backtest","brain"', PAGE)

    def test_the_sync_buttons_pull_from_notion_and_say_where_each_trade_went(self):
        self.assertEqual(PAGE.count("data-nsync"), 5)                # Home, Journal and the Connections routine share one handler
        for needle in ("function nsPull(", "new in Journal", "new in Backtest", "Already up to date", 'visibilitychange', "body.hasnotion .nsb"):
            self.assertIn(needle, PAGE)
        self.assertIn('refresh(with_hub=bool(body.get("hub", True)))', SERVER)     # the button reads the journal only, so it takes seconds

    def test_a_row_filled_in_later_updates_the_trade_but_keeps_what_you_wrote_here(self):
        self.assertIn('n.no!==undefined?byNo[n.no+"|"+n.date]:null', PAGE)
        self.assertIn("if(!o.notes&&n.notes)", PAGE)
        self.assertIn("if(!o.img&&n.img)", PAGE)


if __name__ == "__main__":
    unittest.main()
