"""The journal's files on the PC (trades file, screenshots) and the Notion import."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile

from _load import load
from test_dashboard import Server

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "riskdesk"))
import notion_import   # noqa: E402
import store           # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"fake image bytes 1"
PNG2 = b"\x89PNG\r\n\x1a\n" + b"fake image bytes 2"
JPG = b"\xff\xd8\xff\xe0" + b"jpeg bytes"

CSV = """Date,Account Type,Bias,Pair,Session,Long / Short,Risk,Win / Loss,R/R,Partials,Strategy,Zone,Setup,Trade SS,Trade Breakdown,Profit,Trade No.
"September 1, 2025",No Setup,NO DATA,EUR/USD,HUGGING,NO DATA,0.0%,NO TRADE,0.0RR,NO,OG,NO TRADE,NO SETUP,,No Trade,,1
"September 1, 2025",Backtest,BULLISH,GBP/USD,NY Session,LONG,1%,WIN,3RR,NO,OG,15m Demand Zone,Double Bottom,Trade%20Journey/Page%20A/one.png,Double Bottom,,2
"11/01/2026",Live Account,BEARISH,XAU/USD,LDN Session,SHORT,1.5%,LOSS,2RR,NO,OG,30m Supply Zone,"Double Top, FIB Retest",Trade%20Journey/Page%20B/two.png,DT,,12
"04/09/2025",Backtest,BULLISH,GBP/USD,Out of Session,LONG,0.5%,INVALID,3RR,NO,OG,30m Demand Zone,Double Bottom,,Invalid one,,8
"03/09/2025",Backtest,BEARISH,EUR/USD,NY Session,SHORT,0.5%,BREAKEVEN,BE,YES,OG,30m Supply Zone,H&S,Trade%20Journey/Missing/nothere.png,H&S,,5
"""


def make_export(folder, nested=False):
    path = os.path.join(folder, "export.zip")
    with zipfile.ZipFile(path, "w") as zf:
        inner = io.BytesIO()
        target = zipfile.ZipFile(inner, "w") if nested else zf
        target.writestr("Data Collection/Trade Journey.csv", CSV)
        target.writestr("Trade Journey/Page A/one.png", PNG)
        target.writestr("Trade Journey/Page B/two.png", PNG2)
        target.writestr("Data Collection/Trade Journey_all.csv", "Name,Other\nx,y\n")
        if nested:
            target.close()
            zf.writestr("Export-Part-1.zip", inner.getvalue())
    return path


REAL_CSV = """Date,Account Type,Bias,Pair,Session,Long / Short,Risk,Win / Loss,R/R,Partials ,Strategy ,Zone,Setup,Trade SS,Strategy + Setup,Trade Breakdown,Profit,Trade No.
"September 1, 2025","No Setup","NO DATA","EUR/USD","HUGGING","NO DATA","0.0%","NO TRADE","0.0RR","NO","OG","NO TRADE","NO SETUP",,No Trade,No Trade,,1
"March 10, 2026","Backtest","BULLISH","XAU/USD","NY Session","LONG","1%","WIN, TEST","3RR","YES","OG","30m Demand Zone","Double Bottom","Trade%20Journey/P1/a.png",OG Strategy,Double Bottom,,28
"March 11, 2026","Funded Account","BEARISH","NAS100","NY Session","SHORT","0.5%","LOSS","-1RR","NO","OG","15m Supply Zone","EQ HIGHS, LIQ GRAB","Trade%20Journey/P2/b.png",OG Strategy,EQ + LQ grab,,31
"March 12, 2026","Evaluation","BULLISH","US30","LDN Session","LONG","0.75%","BREAKEVEN","BE","NO","OG","15m Demand Zone","Double Bottom",,OG Strategy,BE trade,,32
"March 13, 2026","Backtest, Live Account","BEARISH","GBP/USD","NY Session","SHORT","1.5%","WIN","30RR","NO","OG","30m Supply Zone","H&S",,OG Strategy,big one,,33
"March 14, 2026","No Trading","NO DATA","BANK HOLIDAY","No Session","NO DATA","0.0%","NO TRADE","0.0RR","N/A","NONE","NO TRADE","NO SETUP",,NO TRADE,Holiday,,34
"""


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = self.tmp.name

    def test_nothing_saved_yet_is_not_an_empty_journal(self):
        self.assertIsNone(store.load_trades(self.d))

    def test_save_and_load_round_trip_and_the_previous_copy_is_kept(self):
        store.save_trades(self.d, [{"date": "2026-10-01", "pl": 1}])
        store.save_trades(self.d, [{"date": "2026-10-01", "pl": 1}, {"date": "2026-10-02", "pl": 2}])
        self.assertEqual(len(store.load_trades(self.d)), 2)
        with open(os.path.join(self.d, "journal", "trades.prev.json"), encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["trades"]), 1)

    def test_a_bad_shape_is_refused_and_nothing_is_written(self):
        for bad in ("x", [1, 2], {"a": 1}, [{}] * (store.MAX_TRADES + 1)):
            with self.assertRaises(store.StoreError):
                store.save_trades(self.d, bad)
        self.assertFalse(os.path.exists(os.path.join(self.d, "journal")))

    def test_images_are_named_by_content_and_stored_once(self):
        a = store.save_image(self.d, PNG)
        self.assertEqual(a, store.save_image(self.d, PNG))
        self.assertTrue(store.IMAGE_NAME.match(a))
        self.assertEqual(len(os.listdir(os.path.join(self.d, "journal", "images"))), 1)
        self.assertTrue(store.save_image(self.d, JPG).endswith(".jpg"))

    def test_only_real_images_are_kept(self):
        for bad in (b"", b"<html>not an image</html>", b"MZ\x90\x00 an exe", b"x" * (store.MAX_IMAGE + 1)):
            with self.assertRaises(store.StoreError):
                store.save_image(self.d, bad)

    def test_a_name_that_climbs_out_of_the_folder_reads_nothing(self):
        for bad in ("../trades.json", "..%2f", "a.png", "0123456789abcdef.exe", "0123456789abcdef.png/../x", ""):
            self.assertIsNone(store.read_image(self.d, bad))


class Import(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def convert(self, nested=False):
        make_export(self.tmp.name, nested)
        src = notion_import.open_source(self.tmp.name)
        self.addCleanup(src.close)
        return notion_import.convert(src)

    def test_rows_become_trades_in_r_and_percent_and_the_rest_are_counted(self):
        trades, rep = self.convert()
        self.assertEqual(len(trades), 5)                                              # nothing is dropped
        self.assertEqual(rep["skipped"], {"none": 1, "invalid": 1})                   # kept, but left out of the numbers
        win, loss, be = [t for t in trades if not t.get("skip")]
        self.assertEqual(sorted(t["skip"] for t in trades if t.get("skip")), ["invalid", "none"])
        nothing = next(t for t in trades if t.get("skip") == "none")
        self.assertEqual((nothing["pl"], nothing["r"], nothing["inst"] != ""), (0.0, 0.0, True))
        self.assertEqual((win["date"], win["inst"], win["dir"], win["acct"], win["unit"]), ("2025-09-01", "GBPUSD", "long", "backtest", "pct"))
        self.assertEqual((win["risk"], win["pl"], win["r"]), (1.0, 3.0, 3.0))
        self.assertEqual((loss["date"], loss["acct"], loss["inst"], loss["dir"]), ("2026-01-11", "live", "XAUUSD", "short"))   # day first
        self.assertEqual((loss["risk"], loss["pl"], loss["r"]), (1.5, -1.5, -1.0))
        self.assertEqual(loss["tag"], "Double Top + FIB Retest")
        self.assertEqual((be["pl"], be["partials"]), (0.0, "yes"))
        self.assertEqual((win["zone"], win["bias"], win["no"], win["strategy"]), ("15m Demand Zone", "bullish", 2, "OG"))

    def test_a_real_notion_export_with_multi_select_cells(self):
        with zipfile.ZipFile(os.path.join(self.tmp.name, "real.zip"), "w") as zf:
            zf.writestr("Data Collection/Trade Journey.csv", REAL_CSV)
            zf.writestr("Trade Journey/P1/a.png", PNG)
            zf.writestr("Trade Journey/P2/b.png", PNG2)
        src = notion_import.open_source(self.tmp.name)
        self.addCleanup(src.close)
        trades, rep = notion_import.convert(src)
        self.assertEqual(rep["skipped"], {"none": 2})
        gold, nas, us30, gbp = [t for t in trades if not t.get("skip")]
        self.assertEqual((gold["inst"], gold["acct"], gold["r"], gold["partials"]), ("XAUUSD", "backtest", 3.0, "yes"))
        self.assertTrue(gold["notes"].startswith("TEST."))                       # the TEST tag is kept, not lost
        self.assertEqual((nas["inst"], nas["acct"], nas["r"], nas["pl"], nas["tag"]), ("NAS100", "live", -1.0, -0.5, "EQ HIGHS + LIQ GRAB"))   # funded counts as live
        self.assertEqual((us30["acct"], us30["r"], us30["pl"]), ("backtest", 0.0, 0.0))      # an evaluation is not live money
        self.assertEqual((gbp["acct"], gbp["r"], gbp["pl"]), ("live", 30.0, 45.0))           # live wins when both are ticked
        self.assertEqual(rep["with_image"], 2)

    def test_tests_zero_risk_and_no_trade_days_are_kept_but_not_counted(self):
        csv_text = REAL_CSV + (
            '"March 15, 2026","Forward-Test","BULLISH","EUR/USD","NY Session","LONG","0.5%","TEST","3RR","NO","OG","15m Demand Zone","FIB Retest",,,,40\n'
            '"March 16, 2026","Forward-Test","BEARISH","NAS100","LDN Session","SHORT","0.0%","WIN","3.5RR","NO","IFVG","30m Supply Zone","SSL",,,,41\n'
            ',,,,,,,,,,,,,,,,,\n')
        with zipfile.ZipFile(os.path.join(self.tmp.name, "real.zip"), "w") as zf:
            zf.writestr("Data Collection/Trade Journey.csv", csv_text)
        src = notion_import.open_source(self.tmp.name)
        self.addCleanup(src.close)
        trades, rep = notion_import.convert(src)
        self.assertEqual(len(trades), 8)                                      # 4 real + 2 no-trade days + the test + the zero-risk win
        self.assertEqual(rep["skipped"], {"none": 2, "test": 1, "invalid": 1})
        test = next(t for t in trades if t.get("skip") == "test")
        self.assertEqual((test["pl"], test["r"], test["acct"]), (0.0, 0.0, "backtest"))
        self.assertIn("Planned 3.0R", test["notes"])
        self.assertEqual(sorted({t.get("acctType") for t in trades if t["acct"] == "live" and not t.get("skip")}), ["Funded Account"])
        self.assertEqual(test["acctType"], "Forward-Test")
        norisk = next(t for t in trades if t.get("skip") == "invalid")
        self.assertEqual((norisk["pl"], norisk["inst"]), (0.0, "NAS100"))
        self.assertIn("No risk %", norisk["notes"])

    def test_screenshots_are_matched_and_missing_ones_are_counted(self):
        trades, rep = self.convert()
        self.assertEqual(rep["with_image"], 2)
        self.assertEqual(rep["missing_image"], 1)
        counted = [t for t in trades if not t.get("skip")]
        self.assertEqual(counted[0]["image"], "Trade Journey/Page A/one.png")
        self.assertNotIn("image", counted[2])

    def test_an_export_zipped_inside_a_zip_is_read_too(self):
        trades, rep = self.convert(nested=True)
        self.assertEqual(len(trades), 5)
        self.assertEqual(rep["with_image"], 2)

    def test_dates_in_the_formats_notion_uses(self):
        for text, want in (("September 1, 2025", "2025-09-01"), ("Sep 1, 2025", "2025-09-01"), ("1 September 2025", "2025-09-01"),
                           ("01/09/2025", "2025-09-01"), ("2025-09-01", "2025-09-01"), ("31/02/2025", None), ("", None), ("soon", None)):
            self.assertEqual(notion_import.parse_date(text), want, text)

    def test_a_missing_or_empty_import_folder_says_what_to_do(self):
        with self.assertRaises(notion_import.ImportError_):
            notion_import.open_source(os.path.join(self.tmp.name, "nope"))
        with self.assertRaises(notion_import.ImportError_):
            notion_import.open_source(self.tmp.name)

    def test_a_zip_entry_that_climbs_out_is_ignored(self):
        with zipfile.ZipFile(os.path.join(self.tmp.name, "x.zip"), "w") as zf:
            zf.writestr("../evil.csv", "Date,Pair\n1,2\n")
            zf.writestr("ok/Trade Journey.csv", CSV)
        src = notion_import.open_source(self.tmp.name)
        self.addCleanup(src.close)
        self.assertEqual(list(src.files), ["ok/Trade Journey.csv"])


class Routes(Server):
    def raw_post(self, path, data, headers=None):
        h = {"Content-Type": "image/png", "X-Requested-With": "zones-dashboard"}
        h.update(headers or {})
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def get_bytes(self, path):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=10) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_the_journal_starts_empty_then_saves_and_reloads(self):
        code, js = self.get("/api/journal")[0], json.loads(self.get("/api/journal")[2])
        self.assertEqual((code, js["exists"], js["trades"]), (200, False, []))
        self.assertEqual(self.post("/api/journal", {"trades": [{"date": "2026-10-01", "pl": 5}]})[1]["saved"], 1)
        js = json.loads(self.get("/api/journal")[2])
        self.assertEqual((js["exists"], js["trades"][0]["pl"]), (True, 5))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "journal", "trades.json")))

    def test_saving_needs_the_dashboard_header_and_a_list(self):
        req = urllib.request.Request("http://127.0.0.1:%d/api/journal" % self.port, data=b'{"trades":[]}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.post("/api/journal", {"trades": "nope"})[0], 400)

    def test_a_screenshot_is_saved_then_served_with_its_own_type(self):
        code, js = self.raw_post("/api/journal/image", PNG)
        self.assertEqual(code, 200)
        status, headers, body = self.get_bytes("/api/img/" + js["name"])
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", PNG))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_something_that_is_not_an_image_is_refused(self):
        self.assertEqual(self.raw_post("/api/journal/image", b"<script>alert(1)</script>")[0], 400)
        self.assertEqual(self.raw_post("/api/journal/image", b"")[0], 400)

    def test_image_names_cannot_reach_other_files(self):
        for bad in ("/api/img/../trades.json", "/api/img/..%2Fconfig.json", "/api/img/nothing.png", "/api/img/"):
            self.assertEqual(self.get_bytes(bad)[0], 404, bad)

    def test_the_notion_preview_reads_only_and_apply_keeps_the_screenshots(self):
        d = os.path.join(self.tmp.name, "import")
        os.makedirs(d)
        make_export(d)
        code, js = self.post("/api/import/notion", {})
        self.assertEqual(code, 200)
        self.assertEqual((js["applied"], js["report"]["with_image"], len(js["sample"]), js["trades"]), (False, 2, 5, []))
        self.assertNotIn("image", js["sample"][0])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "journal")))                 # nothing written yet
        code, js = self.post("/api/import/notion", {"apply": True})
        self.assertEqual((code, len(js["trades"])), (200, 5))
        names = [t["img"] for t in js["trades"] if "img" in t]
        self.assertEqual(len(names), 2)
        self.assertEqual(self.get_bytes("/api/img/" + names[0])[2], PNG)
        self.assertNotIn("image", js["trades"][0])

    def test_the_notion_import_without_an_export_explains_what_to_do(self):
        code, js = self.post("/api/import/notion", {})
        self.assertEqual(code, 400)
        self.assertIn("import", js["error"])
