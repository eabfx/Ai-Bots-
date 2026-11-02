import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "riskdesk"))
sys.path.insert(0, os.path.join(HERE, "..", "trader-zones"))
from _load import load  # noqa: E402

brain = load("riskdesk", "brain")
PAGE = open(os.path.join(HERE, "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
SERVER = open(os.path.join(HERE, "..", "riskdesk", "dashboard.py"), encoding="utf-8").read()
JS = open(os.path.join(HERE, "..", "riskdesk", "assets", "brain.js"), encoding="utf-8").read()

HEAD = ["Date", "Account Type", "Bias", "Pair", "Session", "Long / Short", "Risk", "Win / Loss", "R/R", "Partials ", "Strategy ",
        "Zone", "Setup", "Trade SS", "Strategy + Setup", "Trade Breakdown", "Profit", "Trade No."]
ROWS = [HEAD,
        ["01/09/2025", "Backtest", "BULLISH", "GBP/USD", "NY Session", "LONG", "1%", "WIN", "3RR", "NO", "OG", "15m Demand Zone", "Double Bottom, NEWS", "", "OG Strategy", "nice", "$250", "2"],
        ["15/07/2026", "Live", "BEARISH", "NQ", "NY Session", "SHORT", "0.5%", "LOSS", "3RR", "YES", "OG", "30m Supply Zone", "Double Top", "", "", "", "", "201"]]
HUB = {"at": "x", "root": "R", "pages": {"R": {"title": "Trade Journal", "nodes": [
    {"t": "h2", "r": [{"x": "RISK"}], "co": "red_background"},
    {"t": "cols", "cols": [{"k": [{"t": "file", "name": "OG STRATEGY CHECKLIST"}, {"t": "file", "name": "CVD AGGRESSOR CANDLE + FIXED VOLUME PROFILE TOOL AND MORE WORDS"}, {"t": "page", "title": "Thoughts + Emotions", "id": "", "locked": True}]}]},
    {"t": "paragraph", "r": [{"x": "The pages above open once the live Notion link is set up."}]},
    {"t": "db", "title": "Scaling", "cols": ["Name", "Level"], "rows": [["Step 1", "1"]]},
    {"t": "group", "k": [{"t": "todo", "r": [{"x": "task"}], "done": False}]}]}}}


def data_dir(hub=True):
    d = tempfile.mkdtemp()
    if hub:
        os.makedirs(os.path.join(d, "notion"))
        with open(os.path.join(d, "notion", "hub.json"), "w") as fh:
            json.dump(HUB, fh)
    return d


class Database(unittest.TestCase):
    def test_the_journal_table_becomes_a_notion_like_database(self):
        db = brain.db_from_table(ROWS)
        by = {p["name"]: p for p in db["props"]}
        self.assertEqual([p["name"] for p in db["props"]][:3], ["Strategy + Setup", "Date", "Trade No."])
        self.assertEqual((by["Date"]["type"], by["Trade No."]["type"], by["Pair"]["type"], by["Setup"]["type"], by["Trade SS"]["type"]),
                         ("date", "number", "select", "multi_select", "files"))
        self.assertEqual(by["Strategy + Setup"]["type"], "title")
        colors = {o["name"]: o["color"] for o in by["Win / Loss"]["options"]}
        self.assertEqual((colors["WIN"], colors["LOSS"]), ("green", "red"))
        newest = db["rows"][0]["v"]                                          # newest trade number first
        self.assertEqual(newest[by["Trade No."]["id"]], 201)
        old = db["rows"][1]["v"]
        self.assertEqual(old[by["Date"]["id"]], "2025-09-01")                # day first, as Notion shows it
        self.assertEqual(old[by["Setup"]["id"]], ["Double Bottom", "NEWS"])
        self.assertEqual(old[by["Profit"]["id"]], 250)

    def test_options_come_from_what_the_rows_hold(self):
        db = brain.db_from_table(ROWS)
        pair = [p for p in db["props"] if p["name"] == "Pair"][0]
        self.assertEqual(sorted(o["name"] for o in pair["options"]), ["GBP/USD", "NQ"])


class Seed(unittest.TestCase):
    def test_first_copy_has_the_pages_the_table_and_editable_subpages(self):
        d = data_dir()
        doc = brain.seed(d)
        self.assertEqual(doc["root"], "main")
        self.assertIn(brain.TJ_PAGE, doc["pages"])
        self.assertEqual(doc["pages"]["main"]["nodes"][0]["id"], brain.TJ_PAGE)           # the journal is the first thing on the main page
        kinds = [n["t"] for n in brain.clean_nodes(doc["pages"]["main"]["nodes"])]
        self.assertNotIn("group", kinds)
        texts = json.dumps(doc)
        self.assertNotIn("live Notion link is set up", texts)                              # the placeholder note is dropped
        self.assertIn("Thoughts + Emotions", texts)                                          # kept as a locked label, never copied
        pages = [n for n in brain.clean_nodes(doc["pages"]["main"]["nodes"][1:])]
        cols = [n for n in pages if n["t"] == "cols"][0]
        link = cols["cols"][0]["k"][0]
        self.assertEqual(link["t"], "page")                                                  # the checklist placeholder is now a real page
        self.assertIn(link["id"], doc["pages"])
        self.assertEqual(doc["pages"][link["id"]]["title"], "OG STRATEGY CHECKLIST")
        self.assertIn("db_scaling", doc["dbs"])
        self.assertEqual(doc["dbs"]["db_scaling"]["rows"][0]["v"]["title"], "Step 1")

    def test_seed_reads_the_export_table_when_one_is_waiting(self):
        d = data_dir()
        sub = os.path.join(d, "import-done", "Trade Journal", "Trade Journal 2026")
        os.makedirs(sub)
        import csv
        with open(os.path.join(sub, "Trade Journal 2026 abc.csv"), "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(ROWS)
        doc = brain.seed(d)
        self.assertEqual(len(doc["dbs"][brain.TJ_DB]["rows"]), 2)

    def test_without_a_notion_copy_it_still_opens_with_an_empty_page_and_journal(self):
        doc = brain.seed(data_dir(hub=False))
        self.assertEqual(doc["pages"]["main"]["title"], "Trade Journal")
        self.assertEqual(doc["dbs"][brain.TJ_DB]["rows"], [])
        self.assertTrue(any(p["name"] == "Trade No." for p in doc["dbs"][brain.TJ_DB]["props"]))


class Saving(unittest.TestCase):
    def test_a_save_round_trips_and_bumps_the_revision(self):
        d = data_dir()
        doc = brain.seed(d)
        doc["pages"]["main"]["nodes"].append({"t": "paragraph", "r": [{"x": "mine", "b": True, "co": "red"}]})
        rev = brain.save(d, doc)
        self.assertEqual(rev, brain.load(d)["rev"])
        self.assertEqual(brain.load(d)["pages"]["main"]["nodes"][-1]["r"][0]["x"], "mine")

    def test_an_old_tab_cannot_overwrite_newer_work(self):
        d = data_dir()
        doc = brain.seed(d)
        brain.save(d, dict(doc))
        with self.assertRaises(brain.Conflict):
            brain.save(d, dict(doc))                                    # still says rev 0

    def test_junk_is_cleaned_out(self):
        d = data_dir()
        doc = brain.seed(d)
        doc["pages"]["main"]["nodes"] = [{"t": "script", "r": []}, {"t": "img", "img": "../../etc/passwd"},
                                         {"t": "paragraph", "r": [{"x": "ok", "a": "javascript:alert(1)", "co": "<b>"}]},
                                         {"t": "li", "r": [{"x": "x"}], "k": [{"t": "paragraph", "r": []}]}]
        brain.save(d, doc)
        nodes = brain.load(d)["pages"]["main"]["nodes"]
        self.assertEqual([n["t"] for n in nodes], ["paragraph", "li"])
        self.assertEqual(nodes[0]["r"], [{"x": "ok"}])

    def test_bad_page_ids_are_refused(self):
        with self.assertRaises(brain.BrainError):
            brain.clean_doc({"pages": {"../x": {"title": "a"}}})

    def test_values_follow_their_property_type(self):
        db = brain.clean_db({"props": [{"id": "title", "name": "Name", "type": "title"}, {"id": "n", "name": "N", "type": "number"},
                                       {"id": "m", "name": "M", "type": "multi_select", "options": [{"name": "a", "color": "nope"}]},
                                       {"id": "d", "name": "D", "type": "date"}],
                             "rows": [{"id": "r1", "v": {"n": "abc", "m": ["a", 5], "d": "01/02/2026", "title": "t", "ghost": 1}},
                                      {"id": "r2", "v": {"n": 3, "d": "2026-02-01"}}]})
        self.assertEqual(db["rows"][0]["v"], {"title": "t", "m": ["a"]})
        self.assertEqual(db["rows"][1]["v"], {"n": 3.0, "d": "2026-02-01"})
        self.assertEqual(db["props"][2]["options"][0]["color"], "default")

    def test_merging_new_notion_rows_keeps_your_edits(self):
        d = data_dir()
        doc = brain.seed(d)
        brain.save(d, doc)
        back = brain.load(d)
        db = back["dbs"][brain.TJ_DB]
        db["rows"].append({"id": "mine", "v": {"trade_no": 1, "date": "2025-01-01", "trade_breakdown": "mine"}})
        brain.save(d, back)
        rows = [HEAD, ["01/01/2025", "Live", "BULLISH", "NQ", "", "LONG", "1%", "WIN", "3RR", "NO", "OG", "", "", "", "", "from notion", "", "1"],
                ["02/01/2025", "Live", "BULLISH", "NQ", "", "LONG", "1%", "WIN", "3RR", "NO", "OG", "", "", "", "", "new one", "", "2"]]
        out = brain.merge_rows(d, rows)
        got = out["dbs"][brain.TJ_DB]["rows"]
        self.assertEqual(len(got), 2)
        self.assertEqual([r["v"].get("trade_breakdown") for r in got], ["new one", "mine"])


class Wiring(unittest.TestCase):
    def test_server_serves_the_editor_and_the_document(self):
        for needle in ('"/api/brain"', "brain.save(", "brain.seed(", '"brain.js"', '"brain.css"', "brain.Conflict"):
            self.assertIn(needle, SERVER)
        self.assertIn("12000000 if route in (\"/api/journal\", \"/api/brain\", \"/api/vault/doc\")", SERVER)

    def test_the_page_uses_the_editor_and_the_button_is_white_and_black(self):
        for needle in ('<div id="sb-root">', '<script src="/brain.js">', "SecondBrain.show", ".brainbtn{appearance:none;border:2px solid var(--accent)"):
            self.assertIn(needle, PAGE)

    def test_the_editor_never_calls_notion(self):
        self.assertNotIn("notion.com", JS)
        self.assertNotIn("api.notion", JS)
        for needle in ("function sectionBar()", "sb-tabs", "+ New section", "/api/brain", "execCommand", "contentEditable", "multi_select", "/api/journal/image"):
            self.assertIn(needle, JS)


if __name__ == "__main__":
    unittest.main()


class Readability(unittest.TestCase):
    CSS = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "assets", "brain.css"), encoding="utf-8").read()
    JS = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "assets", "brain.js"), encoding="utf-8").read()

    def test_pages_use_the_full_width_and_columns_wrap_instead_of_squeezing(self):
        self.assertIn(".sb-body{max-width:min(100%,1560px)", self.CSS)
        self.assertIn(".bk-cols{display:flex;flex-wrap:wrap", self.CSS)
        self.assertIn(".bk-col:has(.bk-tbl){flex:2 1 560px}", self.CSS)

    def test_width_and_text_size_controls_are_remembered_in_this_browser(self):
        for needle in ('"sb.view"', "function applyView()", 'b.id=id', "Smaller text", "Bigger text", "SIZES"):
            self.assertIn(needle, self.JS)


class FillFromNotion(unittest.TestCase):
    """Pages that were copied as empty placeholders get the Notion content later; written pages are never touched."""
    HUB2 = {"at": "t2", "root": "R", "pages": {
        "R": {"title": "Trade Journal", "nodes": [{"t": "paragraph", "r": [{"x": "root"}]}]},
        "p1": {"title": "CVD ACCUMULATION", "nodes": [{"t": "h2", "r": [{"x": "Key notes"}]}, {"t": "li", "r": [{"x": "wait for it"}]},
                                                      {"t": "db", "title": "Steps", "cols": ["Name", "Level"], "rows": [["One", "1"]]}]},
        "p2": {"title": "TWT CHECKLIST", "nodes": [{"t": "paragraph", "r": [{"x": "Notion text"}]}]},
        "p3": {"title": "OG BACKTESTING DATA", "nodes": [{"t": "img", "img": "0123456789abcdef.png", "r": []}]}}}

    def doc(self):
        d = data_dir()
        brain.seed(d)
        def add(doc):
            for pid, title, txt in (("cvd_accumulation", "CVD ACCUMULATION", None), ("twt_checklist", "TWT CHECKLIST", "I wrote this myself"),
                                    ("og_backtesting_data", "OG BACKTESTING DATA", None)):
                doc["pages"][pid] = {"title": title, "icon": "", "parent": doc["root"],
                                     "nodes": [{"t": "paragraph", "r": [{"x": txt}] if txt else []}]}
        brain.update(d, add)
        return d

    def test_empty_pages_are_filled_and_written_pages_are_not(self):
        d = self.doc()
        filled = brain.fill_from_hub(d, self.HUB2)
        self.assertEqual(sorted(filled), ["CVD ACCUMULATION", "OG BACKTESTING DATA"])
        doc = brain.load(d)
        kinds = [n["t"] for n in doc["pages"]["cvd_accumulation"]["nodes"]]
        self.assertEqual(kinds, ["h2", "li", "db"])
        dbid = doc["pages"]["cvd_accumulation"]["nodes"][2]["id"]
        self.assertEqual(doc["dbs"][dbid]["rows"][0]["v"]["title"], "One")
        self.assertEqual(doc["pages"]["twt_checklist"]["nodes"][0]["r"][0]["x"], "I wrote this myself")
        self.assertEqual(doc["pages"]["og_backtesting_data"]["nodes"][0]["t"], "img")

    def test_running_it_twice_changes_nothing_more(self):
        d = self.doc()
        brain.fill_from_hub(d, self.HUB2)
        rev = brain.load(d)["rev"]
        self.assertEqual(brain.fill_from_hub(d, self.HUB2), [])
        self.assertEqual(brain.load(d)["rev"], rev + 1)        # one more save, but the content is the same

    def test_no_hub_or_no_document_is_fine(self):
        self.assertEqual(brain.fill_from_hub(data_dir(hub=False), self.HUB2), [])
        self.assertEqual(brain.fill_from_hub(self.doc(), {}), [])

    def test_delete_buttons_and_the_refresh_button_exist(self):
        for needle in ("function deletePageAsk(", "sb-tabx", "Delete this section", "Refresh pages from Notion", "/api/brain/fill"):
            self.assertIn(needle, JS)
        for needle in ("/api/brain/fill", "brain.fill_from_hub("):
            self.assertIn(needle, SERVER)
