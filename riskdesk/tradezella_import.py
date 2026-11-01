"""Turns trades copied out of TradeZella into journal trades.

TradeZella has no download for this: the trades are read through its Claude connection and saved as plain csv files in
the  trader/tradezella  folder (separate from  trader/import , which is only for the Notion export).

backtest.csv  (uid,symbol,side,entry,qty,pl,rr,session)  trades from TradeZella backtesting sessions. Results are in dollars
              on a practice account, so each one is stored as a percent of that session's running balance, with the R
              multiple TradeZella worked out. A trade with a result but no R is kept, not counted.
demo.csv      (uid,symbol,side,entry,qty,pl,result)  trades from the TradeZella demo account. They have no strategy, no R and
              are in mixed sizes, so they are kept but left out of every number (the same as a test trade).

Standard library only. Nothing here writes anything: the page decides what to add.
"""

import csv
import os
import statistics

START_BALANCE = 50000.0            # both backtesting sessions start with $50,000 in TradeZella
STRATEGY = "OG"                    # the TradeZella strategy these sessions test is "OG Strategy"
ACCT_BACKTEST = "TradeZella Backtest"
ACCT_DEMO = "TradeZella Demo"


class TradeZellaError(Exception):
    pass


def _num(text):
    try:
        return float(str(text).strip())
    except ValueError:
        return None


def _rows(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def _when(text):
    day, _, clock = str(text).strip().partition(" ")
    ok = len(day) == 10 and day[4] == "-" and day[7] == "-" and day[:4].isdigit()
    return (day if ok else ""), (clock[:5] if len(clock) >= 5 and clock[2] == ":" else "")


def _dir(text):
    return "short" if str(text).strip().lower() == "short" else "long"


def _backtest(rows, report):
    """Percent of the running balance, per session, in the order the trades were taken."""
    out, per = [], {}
    for r in sorted(rows, key=lambda x: (x.get("session", ""), x.get("entry", ""))):
        day, clock = _when(r.get("entry"))
        usd = _num(r.get("pl"))
        if not day or usd is None:
            report["unreadable"] += 1
            continue
        s = per.setdefault(r.get("session", "") or "TradeZella", {"balance": START_BALANCE, "trades": 0, "usd": 0.0})
        pct = usd / s["balance"] * 100
        s["balance"] += usd
        s["trades"] += 1
        s["usd"] += usd
        rr = _num(r.get("rr"))
        t = {"date": day, "time": clock, "inst": (r.get("symbol") or "-")[:20], "dir": _dir(r.get("side")), "unit": "pct",
             "acct": "backtest", "acctType": ACCT_BACKTEST, "strategy": STRATEGY, "tag": "TradeZella backtest", "bias": "",
             "notes": "TradeZella backtest %s: %s$%s, %s lots (%s)" % (r.get("session", ""), "+" if usd >= 0 else "-", ("%g" % abs(usd)), r.get("qty", ""), r.get("uid", ""))}
        if rr is None and usd != 0:
            t.update(skip="invalid", risk=0, pl=0.0, r=0.0, tag="Invalid")
            t["notes"] = "No R recorded in TradeZella. " + t["notes"]
            report["invalid"] += 1
        elif rr is None or rr == 0 or usd == 0:
            t.update(pl=0.0, r=0.0, risk=0.0, _be=True)                 # breakeven: the risk is filled in below
        else:
            risk = abs(pct / rr)
            t.update(pl=round(pct, 4), r=round(rr, 4), risk=round(risk, 4), riskPct=round(risk, 4))
        out.append(t)
    typical = statistics.median([t["risk"] for t in out if t["risk"] > 0 and not t.get("_be")] or [0.5])
    for t in out:
        if t.pop("_be", False):
            t["risk"] = t["riskPct"] = round(typical, 4)
    report["backtest"] += len(out)
    report["sessions"] = {k: {"trades": v["trades"], "usd": round(v["usd"], 2)} for k, v in per.items()}
    return out


def _demo(rows, report):
    out = []
    for r in rows:
        day, clock = _when(r.get("entry"))
        usd = _num(r.get("pl"))
        if not day or usd is None:
            report["unreadable"] += 1
            continue
        report["demo_usd"] = round(report["demo_usd"] + usd, 2)
        res = (r.get("result") or "").lower()
        out.append({"date": day, "time": clock, "inst": (r.get("symbol") or "-")[:20], "dir": _dir(r.get("side")),
                    "risk": 0, "pl": round(usd, 2), "r": 0.0, "acct": "backtest", "acctType": ACCT_DEMO, "skip": "test",
                    "tag": "Demo", "bias": "",
                    "notes": "TradeZella demo account%s: %s$%s, size %s (%s). Kept, not counted." % (
                        " (still open)" if res == "open" else "", "+" if usd >= 0 else "-", "%g" % abs(usd), r.get("qty", ""), r.get("uid", ""))})
    report["demo"] += len(out)
    return out


def convert(folder):
    """-> (trades, report). Reads every .csv in the folder; a file with an rr column is backtesting, one with result is the demo account."""
    if not os.path.isdir(folder):
        raise TradeZellaError("there is no trader\\tradezella folder yet: put backtest.csv and demo.csv there")
    names = sorted(n for n in os.listdir(folder) if n.lower().endswith(".csv"))
    if not names:
        raise TradeZellaError("trader\\tradezella has no .csv files in it")
    report = {"files": names, "backtest": 0, "demo": 0, "invalid": 0, "unreadable": 0, "demo_usd": 0.0, "sessions": {}}
    trades = []
    for n in names:
        try:
            rows = _rows(os.path.join(folder, n))
        except (OSError, csv.Error, UnicodeError) as e:
            raise TradeZellaError("could not read %s (%s)" % (n, e))
        cols = set(rows[0]) if rows else set()
        if "rr" in cols:
            trades += _backtest(rows, report)
        elif "result" in cols:
            trades += _demo(rows, report)
        else:
            raise TradeZellaError("%s has neither an rr column (backtest) nor a result column (demo)" % n)
    report["counted"] = sum(1 for t in trades if not t.get("skip"))
    report["kept"] = len(trades) - report["counted"]
    return trades, report
