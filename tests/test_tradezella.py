import os
import tempfile
import unittest

from _load import load

tz = load("riskdesk", "tradezella_import")
PAGE = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
SERVER = open(os.path.join(os.path.dirname(__file__), "..", "riskdesk", "dashboard.py"), encoding="utf-8").read()

BACKTEST = """uid,symbol,side,entry,qty,pl,rr,session
a1,GBPUSD,short,2025-05-05 14:30,6,0,,S1
a2,GBPUSD,short,2025-05-05 14:37,6,500,3,S1
a3,GBPUSD,long,2025-05-06 10:00,3,-250,-1,S1
a4,GBPUSD,long,2025-05-07 10:00,3,-34,,S1
b1,EURUSD,long,2026-01-30 07:15,5,630,3,S2
"""
DEMO = """uid,symbol,side,entry,qty,pl,result
d1,GBPUSD,long,2023-05-02 04:21,5,-415,loss
d2,NGAS,long,2023-06-02 02:21,1,0,open
"""


def folder(**files):
    d = tempfile.mkdtemp()
    for name, text in files.items():
        with open(os.path.join(d, name + ".csv"), "w", encoding="utf-8") as fh:
            fh.write(text)
    return d


class Convert(unittest.TestCase):
    def test_backtest_trades_become_percent_of_the_running_balance_with_their_r(self):
        trades, rep = tz.convert(folder(backtest=BACKTEST))
        by = {t["notes"].split("(")[-1][:-1]: t for t in trades}
        self.assertEqual(rep["backtest"], 5)
        win = by["a2"]
        self.assertEqual((win["unit"], win["acct"], win["acctType"], win["strategy"]), ("pct", "backtest", "TradeZella Backtest", "OG"))
        self.assertAlmostEqual(win["pl"], 1.0, 3)                       # $500 on the $50,000 start
        self.assertEqual(win["r"], 3.0)
        self.assertAlmostEqual(win["risk"], 1 / 3, 3)                   # the percent at risk that makes 3R come to 1%
        self.assertAlmostEqual(win["pl"], win["risk"] * win["r"], 3)
        loss = by["a3"]
        self.assertAlmostEqual(loss["pl"], -250 / 50500 * 100, 3)       # the balance had grown by the earlier win
        self.assertEqual(loss["r"], -1.0)
        self.assertEqual((win["date"], win["time"], win["dir"]), ("2025-05-05", "14:37", "short"))

    def test_each_session_has_its_own_balance_and_totals_match_tradezella(self):
        _t, rep = tz.convert(folder(backtest=BACKTEST))
        self.assertEqual(rep["sessions"]["S1"], {"trades": 4, "usd": 216.0})
        self.assertEqual(rep["sessions"]["S2"], {"trades": 1, "usd": 630.0})

    def test_breakeven_keeps_a_normal_risk_and_a_result_with_no_r_is_kept_not_counted(self):
        trades, rep = tz.convert(folder(backtest=BACKTEST))
        by = {t["notes"].split("(")[-1][:-1]: t for t in trades}
        self.assertEqual((by["a1"]["pl"], by["a1"]["r"]), (0.0, 0.0))
        self.assertGreater(by["a1"]["risk"], 0)
        self.assertNotIn("skip", by["a1"])
        self.assertEqual(by["a4"]["skip"], "invalid")
        self.assertEqual(by["a4"]["pl"], 0.0)
        self.assertIn("-$34", by["a4"]["notes"])                        # the dollar result stays readable
        self.assertEqual(rep["invalid"], 1)
        self.assertEqual((rep["counted"], rep["kept"]), (4, 1))

    def test_demo_trades_are_kept_but_never_counted(self):
        trades, rep = tz.convert(folder(demo=DEMO))
        self.assertTrue(all(t["skip"] == "test" and t["acctType"] == "TradeZella Demo" and t["acct"] == "backtest" for t in trades))
        self.assertEqual(rep["demo"], 2)
        self.assertEqual(rep["demo_usd"], -415.0)
        self.assertIn("still open", trades[1]["notes"])
        self.assertNotIn("unit", trades[0])                             # dollars, so they never turn the others into dollars

    def test_both_files_together_and_bad_input(self):
        trades, rep = tz.convert(folder(backtest=BACKTEST, demo=DEMO))
        self.assertEqual((rep["backtest"], rep["demo"], len(trades)), (5, 2, 7))
        with self.assertRaises(tz.TradeZellaError):
            tz.convert(os.path.join(tempfile.mkdtemp(), "nope"))
        with self.assertRaises(tz.TradeZellaError):
            tz.convert(tempfile.mkdtemp())
        with self.assertRaises(tz.TradeZellaError):
            tz.convert(folder(other="a,b\n1,2\n"))
        _t, rep = tz.convert(folder(backtest="uid,symbol,side,entry,qty,pl,rr,session\nx,GBPUSD,long,not a date,1,5,1,S\n"))
        self.assertEqual(rep["unreadable"], 1)


class Wiring(unittest.TestCase):
    def test_the_server_and_the_connections_page_are_wired(self):
        self.assertIn("/api/import/tradezella", SERVER)
        self.assertIn("tradezella_import.TradeZellaError", SERVER)
        for needle in ('id="tz-prev"', 'id="tz-go"', "/api/import/tradezella", "function tzCall"):
            self.assertIn(needle, PAGE)

    def test_a_notion_backtest_trade_of_the_same_day_pair_and_side_is_not_added_twice(self):
        self.assertIn('[t.date,t.inst,t.dir].join("|")', PAGE)
        self.assertIn("!have[ntKey(t)]&&(t.skip||!day[", PAGE)


if __name__ == "__main__":
    unittest.main()
