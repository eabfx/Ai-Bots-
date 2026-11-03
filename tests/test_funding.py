import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
funding = load("riskdesk", "funding")


def r(text):
    return [{"x": text}]


def table(rows):
    return {"t": "table", "head": True, "rowhead": True, "rows": [[r(c) for c in row] for row in rows]}


HEAD = ["Prop Firm", "Ongoing", "Passed  ( Not Paid Activation)", "Secured Funding + Paid Activation"]
CFD = [HEAD,
       ["Atlas Funded", "", "200k Account ", ""],
       ["Atlas Funded", "", "\\$50,000 Account ", ""],
       ["Atlas Funded", "200k Account", "", ""],
       ["Atlas Funded", "", "\\$50,000 Account", ""],
       ["Atlas Funded", "", "\\$50,000 Account", ""],
       ["Atlas Funded", "10k Account", "", ""],
       ["FTMO", "10k Account", "", ""],
       ["Total", "210k in Funding", "350k in Funding", ""]]
FUT = [HEAD,
       ["Apex Trader Funded", "", "", ""],
       ["Apex Trader Funded", "\\$50,000 in Funding", "", ""],
       ["Apex Trader Funded", "\\$50,000 in Funding", "", ""],
       ["Apex Trader Funded", "\\$50,000 in Funding", "", ""],
       ["", "", "", ""],
       ["Total", "\\$50,000 in Funding", "", ""]]
RULES = [["Firm", "Account Type", "Daily Loss Limit", "Profit Split"],
         ["FTMO", "10k", "5%", "80%"], ["Apex Trader Funded", "50k", "varies", "100%"]]


def hub():
    nodes = [
        {"t": "cols", "cols": [{"k": [
            {"t": "h2", "r": r("PROP FIRM RULES CHEAT SHEET")}, table(RULES)]},
            {"k": [{"t": "h2", "r": r("(CFD) Funding Breakdown")}, table(CFD),
                   {"t": "h2", "r": r("(Futures) Funding Breakdown")}, table(FUT),
                   {"t": "quote", "r": r("FUNDING GOAL : \\$1,000,000 In Funding")},
                   {"t": "h1", "r": r("Pay-outs Accumulated Total")},
                   {"t": "h2", "r": r("\\$1,500 - 30/09/2026")}]}]},
        {"t": "db", "title": "Account Scaling System (Funded ACCs)",
         "cols": ["Property", "Prop Firm", "Account Size", "Current Phase", "Current Profit", "Profit Target to Pass/Scale",
                  "Current Drawdown Used", "Max Drawdown Limit", "Status", "Next Account Size"],
         "rows": [["", "Apex Trader Funding", "50000", "FUNDED", "3600", "3600", "0", "2000", "Payout Requested", "53600"]]},
    ]
    return {"root": "root", "at": "2026-11-03T08:00:00", "pages": {"root": {"title": "Trade Journal", "nodes": nodes}}}


class Parse(unittest.TestCase):
    def test_money(self):
        self.assertEqual(funding.money("200k Account"), 200000)
        self.assertEqual(funding.money("\\$50,000 in Funding"), 50000)
        self.assertEqual(funding.money("1.5M"), 1500000)
        self.assertIsNone(funding.money(""))

    def test_tables_payouts_goal_rules_status(self):
        got = funding.from_notion(hub())
        by = {}
        for a in got["accounts"]:
            by[(a["market"], a["stage"])] = by.get((a["market"], a["stage"]), 0) + a["size"]
        self.assertEqual(by[("cfd", "passed")], 350000)
        self.assertEqual(by[("cfd", "ongoing")], 220000)
        self.assertEqual(by[("futures", "ongoing")], 150000)
        self.assertEqual(len({a["id"] for a in got["accounts"]}), len(got["accounts"]))
        self.assertEqual(got["goal"], 1000000)
        self.assertEqual(got["payouts"][0]["date"], "2026-09-30")
        self.assertEqual(got["payouts"][0]["amount"], 1500)
        self.assertEqual([x["firm"] for x in got["rules"]], ["FTMO", "Apex Trader Funded"])
        s = got["status"][0]
        self.assertEqual((s["firm"], s["size"], s["phase"], s["profit"], s["maxdd"], s["state"], s["group"]),
                         ("Apex Trader Funding", 50000, "FUNDED", 3600, 2000, "Payout Requested", "Funded ACCs"))

    def test_empty_copy(self):
        got = funding.from_notion({})
        self.assertEqual((got["accounts"], got["payouts"], got["status"], got["goal"]), ([], [], [], None))


class Keeper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.doc = hub()
        self.f = funding.Funding(self.tmp.name, lambda: self.doc)

    def test_totals_and_warnings(self):
        s = self.f.state()
        self.assertEqual(s["total"]["cfd"]["passed"], 350000)
        self.assertEqual(s["total"]["futures"]["ongoing"], 150000)
        self.assertEqual(s["all"], 350000 + 220000 + 150000)
        self.assertEqual(s["paid"], 1500)
        self.assertEqual(s["goal"], 1000000)
        self.assertEqual(len(s["warnings"]), 2)                       # 210k vs 220k, 50k vs 150k
        self.assertTrue(any("210,000" in w and "220,000" in w for w in s["warnings"]))
        firms = {f["firm"]: f for f in s["by_firm"]}
        self.assertEqual(firms["Atlas Funded"]["total"], 560000)
        self.assertEqual(firms["Apex Trader Funded"]["rules"]["firm"], "Apex Trader Funded")

    def test_add_edit_remove_by_hand(self):
        s = self.f.act({"action": "add", "account": {"firm": "Topstep", "market": "futures", "size": "150,000", "stage": "funded"}})
        mine = [a for a in s["accounts"] if a["source"] == "manual"][0]
        self.assertEqual(s["total"]["futures"]["funded"], 150000)
        s = self.f.act({"action": "edit", "id": mine["id"], "fields": {"stage": "passed", "profit": 1200, "maxdd": 4500, "dd": 300}})
        self.assertEqual(s["total"]["futures"]["funded"], 0)
        self.assertTrue(any(x["firm"] == "Topstep" for x in s["status"]))
        s = self.f.act({"action": "remove", "id": mine["id"]})
        self.assertFalse([a for a in s["accounts"] if a["source"] == "manual"])

    def test_a_notion_row_can_be_changed_and_hidden_without_touching_notion(self):
        before = json.dumps(self.doc, sort_keys=True)
        nid = [a for a in self.f.state()["accounts"] if a["market"] == "cfd" and a["stage"] == "ongoing" and a["size"] == 200000][0]["id"]
        s = self.f.act({"action": "edit", "id": nid, "fields": {"stage": "passed"}})
        self.assertEqual(s["total"]["cfd"]["ongoing"], 20000)
        self.assertEqual(s["total"]["cfd"]["passed"], 550000)
        s = self.f.act({"action": "remove", "id": nid})
        self.assertEqual(s["total"]["cfd"]["passed"], 350000)
        self.assertEqual(s["hidden"], 1)
        s = self.f.act({"action": "restore"})
        self.assertEqual(s["total"]["cfd"]["ongoing"], 220000)
        self.assertEqual(before, json.dumps(self.doc, sort_keys=True))

    def test_changes_survive_a_restart_and_a_new_notion_copy(self):
        self.f.act({"action": "add", "account": {"firm": "Topstep", "market": "futures", "size": 50000, "stage": "ongoing"}})
        self.f.act({"action": "goal", "goal": 2000000})
        self.f.act({"action": "payout", "payout": {"date": "2026-10-15", "amount": 800, "firm": "Apex"}})
        again = funding.Funding(self.tmp.name, lambda: {})
        s = again.state()
        self.assertEqual((s["goal"], s["paid"], s["all"]), (2000000, 800, 50000))
        self.assertTrue(s["goal_custom"])

    def test_evaluations_and_funded_are_separate_and_can_be_overridden(self):
        s = self.f.state()
        self.assertEqual((s["shown"]["evals"], s["shown"]["funded"], s["all"]), (720000, 0, 720000))
        s = self.f.act({"action": "add", "account": {"firm": "Topstep", "market": "futures", "size": 150000, "stage": "funded"}})
        self.assertEqual((s["shown"]["evals"], s["shown"]["funded"], s["all"]), (720000, 150000, 870000))
        s = self.f.act({"action": "totals", "totals": {"evals": "500000", "funded": 200000}})
        self.assertEqual((s["shown"]["evals"], s["shown"]["funded"], s["all"]), (500000, 200000, 700000))
        self.assertEqual((s["calc"]["evals"], s["calc"]["funded"]), (720000, 150000))
        self.assertTrue(s["custom"]["evals"] and s["custom"]["funded"] and not s["custom"]["all"])
        s = self.f.act({"action": "totals", "totals": {"evals": "500000", "funded": 200000, "all": 999}})
        self.assertEqual(s["all"], 999)
        s = self.f.act({"action": "totals", "totals": {"evals": "", "funded": "", "all": ""}})
        self.assertEqual((s["shown"]["evals"], s["shown"]["funded"], s["all"]), (720000, 150000, 870000))
        self.assertFalse(any(s["custom"].values()))
        again = funding.Funding(self.tmp.name, lambda: self.doc)
        self.assertEqual(again.state()["all"], 870000)
        for bad in ({"all": "abc"}, {"funded": -5}):
            with self.assertRaises(funding.FundingError):
                self.f.act({"action": "totals", "totals": bad})

    def test_status_cards_can_be_added_edited_and_removed(self):
        base = len(self.f.state()["status"])
        s = self.f.act({"action": "status", "status": {"firm": "Topstep", "size": 50000, "phase": "EVAL", "profit": 800, "target": 3000, "dd": 200, "maxdd": 2000}})
        mine = [x for x in s["status"] if x["source"] == "manual"][0]
        self.assertEqual((len(s["status"]), mine["firm"], mine["profit"]), (base + 1, "Topstep", 800))
        self.assertEqual(s["all"], 720000)                                  # a status card is not funding, so no totals move
        s = self.f.act({"action": "edit", "id": mine["id"], "fields": {"profit": 1500}})
        self.assertEqual([x for x in s["status"] if x["id"] == mine["id"]][0]["profit"], 1500)
        nid = [x for x in s["status"] if x["source"] == "notion"][0]["id"]
        s = self.f.act({"action": "edit", "id": nid, "fields": {"profit": 100}})
        self.assertEqual([x for x in s["status"] if x["id"] == nid][0]["profit"], 100)
        s = self.f.act({"action": "remove", "id": mine["id"]})
        s = self.f.act({"action": "remove", "id": nid})
        self.assertEqual(len(s["status"]), base - 1)
        again = funding.Funding(self.tmp.name, lambda: self.doc)
        self.assertEqual(len(again.state()["status"]), base - 1)
        with self.assertRaises(funding.FundingError):
            self.f.act({"action": "status", "status": {"size": 5}})

    def test_clearing_the_numbers_of_an_account_keeps_the_account(self):
        s = self.f.act({"action": "add", "account": {"firm": "Topstep", "market": "futures", "size": 50000, "stage": "funded", "profit": 10, "maxdd": 2000}})
        mine = [a for a in s["accounts"] if a["source"] == "manual"][0]
        self.assertTrue(any(x["id"] == mine["id"] for x in s["status"]))
        s = self.f.act({"action": "edit", "id": mine["id"], "fields": {"phase": "", "state": "", "profit": None, "target": None, "dd": None, "maxdd": None}})
        self.assertFalse(any(x["id"] == mine["id"] for x in s["status"]))
        self.assertEqual(s["total"]["futures"]["funded"], 50000)

    def test_bad_input_is_refused(self):
        for body in ({"action": "add", "account": {"firm": "", "market": "cfd", "size": 5, "stage": "funded"}},
                     {"action": "add", "account": {"firm": "X", "market": "stocks", "size": 5, "stage": "funded"}},
                     {"action": "add", "account": {"firm": "X", "market": "cfd", "size": "abc", "stage": "funded"}},
                     {"action": "add", "account": {"firm": "X", "market": "cfd", "size": -1, "stage": "funded"}},
                     {"action": "payout", "payout": {"date": "30/09/2026", "amount": 5}},
                     {"action": "payout", "payout": {"date": "2026-09-30", "amount": 0}},
                     {"action": "edit", "id": "nope", "fields": {"stage": "funded"}},
                     {"action": "goal", "goal": -4}, {"action": "wat"}):
            with self.assertRaises(funding.FundingError, msg=str(body)):
                self.f.act(body)

    def test_unknown_keys_are_dropped(self):
        s = self.f.act({"action": "add", "account": {"firm": "X", "market": "cfd", "size": 5, "stage": "funded", "password": "hunter2", "id": "evil"}})
        mine = [a for a in s["accounts"] if a["source"] == "manual"][0]
        self.assertNotIn("password", mine)
        self.assertNotEqual(mine["id"], "evil")

    def test_a_file_with_a_byte_order_mark_still_loads(self):
        with open(os.path.join(self.tmp.name, "funding.json"), "w", encoding="utf-8-sig") as fh:
            json.dump({"goal": 500000}, fh)
        self.assertEqual(funding.Funding(self.tmp.name, lambda: {}).state()["goal"], 500000)


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = os.path.join(self.tmp.name, "config.json")
        with open(cfg, "w") as fh:
            json.dump({}, fh)
        self.srv = dash.make_server(0, cfg, offline=True)
        self.addCleanup(self.srv.server_close)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/funding", data=json.dumps(body).encode(), method="POST",
                                     headers={"X-Requested-With": "zones-dashboard", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_get_and_post(self):
        with urllib.request.urlopen(self.base + "/api/funding") as resp:
            doc = json.load(resp)
        self.assertEqual(doc["all"], 0)
        code, doc = self.post({"action": "add", "account": {"firm": "Topstep", "market": "futures", "size": 50000, "stage": "funded"}})
        self.assertEqual((code, doc["all"]), (200, 50000))
        code, doc = self.post({"action": "add", "account": {"firm": "", "market": "cfd", "size": 1, "stage": "funded"}})
        self.assertEqual(code, 400)
        self.assertIn("firm", doc["error"])

    def test_post_needs_the_header(self):
        req = urllib.request.Request(self.base + "/api/funding", data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req)


if __name__ == "__main__":
    unittest.main()
