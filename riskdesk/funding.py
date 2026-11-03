"""Funding: every prop-firm account you hold, what it is worth, and where it stands.

The numbers come from three places, and they never overwrite each other:
  1. Your Notion page, read-only. The copy the dashboard already keeps of it holds the (CFD) and (Futures) funding
     tables, the pay-outs, the funding goal, the prop-firm rules sheet and the Account Scaling System databases.
     This module only reads that copy. Nothing here ever writes to Notion.
  2. What you add by hand on the Funding page (a new account, a changed stage, a pay-out). Saved in funding.json.
  3. Changes you make to a row that came from Notion (a new stage, a hidden row). Also saved in funding.json, so the
     next Notion copy does not undo them.

Nothing here places an order or logs in anywhere. Standard library only."""

import json
import math
import os
import re
import threading

FILE = "funding.json"
MARKETS = ("cfd", "futures")
MARKET_NAMES = {"cfd": "Forex / CFD", "futures": "Futures"}
STAGES = ("ongoing", "passed", "funded")
STAGE_NAMES = {"ongoing": "In evaluation", "passed": "Passed, not activated", "funded": "Funded and active"}
MAX_ACCOUNTS = 200
MAX_PAYOUTS = 500
DEFAULT_GOAL = 1000000
STATUS_FIELDS = ("phase", "profit", "target", "dd", "maxdd", "state")
NUMBER_FIELDS = ("size", "profit", "target", "dd", "maxdd")
TOTAL_KEYS = ("all", "evals", "funded", "cfd", "futures")
TOTAL_NAMES = {"all": "total funding", "evals": "evaluations", "funded": "funded", "cfd": "forex / CFD", "futures": "futures"}
TEXT_FIELDS = {"firm": 40, "note": 120, "phase": 20, "state": 30}


class FundingError(Exception):
    pass


# ---- reading numbers and names out of Notion text
def money(text):
    """'200k Account' -> 200000, '$50,000 in Funding' -> 50000, '1.5M' -> 1500000, '' -> None."""
    s = str(text or "").replace(",", "").replace("\\", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM]?)\b", s)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2).lower()
    v *= 1000 if unit == "k" else 1000000 if unit == "m" else 1
    return int(v) if v == int(v) else v


def num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) and abs(v) < 1e10 else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "").replace("\\", "")
        try:
            f = float(s)
        except ValueError:
            return None
        return f if math.isfinite(f) and abs(f) < 1e10 else None
    return None


def tidy(v):
    return int(v) if isinstance(v, float) and v == int(v) else v


def firm_key(name):
    """'Apex Trader Funded' and 'Apex Trader Funding' are the same firm: compare the first word."""
    w = re.findall(r"[a-z0-9]+", str(name or "").lower())
    return w[0] if w else ""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:30]


def text_of(runs):
    return "".join(str(r.get("x", "")) for r in runs or [] if isinstance(r, dict)).replace("\\", "").strip()


def cell(row, i):
    return text_of(row[i]) if i < len(row) else ""


# ---- walking the Notion copy
def walk(nodes, out, state):
    """Document order, down into columns and children. `state['head']` is the last heading seen."""
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        t = n.get("t")
        if t in ("h1", "h2", "h3"):
            state["head"] = text_of(n.get("r"))
            out["heads"].append(state["head"])
        out["flat"].append((state["head"], n))
        if t == "cols":
            for c in n.get("cols") or []:
                walk(c.get("k"), out, state)
        elif isinstance(n.get("k"), list):
            walk(n["k"], out, state)


def scan(hub_doc):
    out = {"flat": [], "heads": []}
    pages = (hub_doc or {}).get("pages") or {}
    root = (hub_doc or {}).get("root")
    order = ([root] if root in pages else []) + [k for k in pages if k != root]
    for pid in order:
        walk((pages[pid] or {}).get("nodes"), out, {"head": ""})
    return out


def parse_funding_tables(flat):
    """The two 'Prop Firm / Ongoing / Passed / Secured' tables. Returns (accounts, typed totals)."""
    accounts, typed = [], {}
    for head, n in flat:
        if n.get("t") != "table":
            continue
        rows = n.get("rows") or []
        if not rows or cell(rows[0], 0).lower() != "prop firm":
            continue
        h = head.lower()
        market = "futures" if "futures" in h else "cfd" if ("cfd" in h or "forex" in h) else ""
        if not market:
            continue
        cols = [cell(rows[0], i).lower() for i in range(len(rows[0]))]
        stage_of = {}
        for i, c in enumerate(cols):
            if i == 0:
                continue
            stage_of[i] = "funded" if ("secured" in c or "paid activation" in c and "not" not in c) else "passed" if "passed" in c else "ongoing" if "ongoing" in c else ""
        seen = {}
        for r in rows[1:]:
            firm = cell(r, 0)
            if firm.lower() == "total":
                for i, st in stage_of.items():
                    v = money(cell(r, i))
                    if st and v:
                        typed[(market, st)] = v
                continue
            for i, st in stage_of.items():
                size = money(cell(r, i)) if st else None
                if not size or not firm:
                    continue
                key = (market, firm, st, size)
                seen[key] = seen.get(key, 0) + 1
                accounts.append({"id": "n-%s-%s-%s-%d-%d" % (market, slug(firm), st, size, seen[key]), "firm": firm, "market": market,
                                 "stage": st, "size": size, "note": "", "source": "notion"})
    return accounts, typed


def parse_rules(flat):
    out = []
    for head, n in flat:
        if n.get("t") != "table":
            continue
        rows = n.get("rows") or []
        if not rows or cell(rows[0], 0).lower() != "firm" or "daily loss limit" not in [cell(rows[0], i).lower() for i in range(len(rows[0]))]:
            continue
        names = [cell(rows[0], i) for i in range(len(rows[0]))]
        for r in rows[1:]:
            if not cell(r, 0):
                continue
            out.append({"firm": cell(r, 0), "fields": [[names[i], cell(r, i)] for i in range(1, len(names)) if cell(r, i)]})
    return out


def parse_payouts(flat):
    out, seen = [], {}
    pat = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*[-–]\s*(\d{1,2})/(\d{1,2})/(\d{4})")
    for head, n in flat:
        if n.get("t") not in ("h1", "h2", "h3", "paragraph"):
            continue
        m = pat.search(text_of(n.get("r")))
        if not m:
            continue
        amount = float(m.group(1).replace(",", ""))
        date = "%s-%02d-%02d" % (m.group(4), int(m.group(3)), int(m.group(2)))
        key = (date, amount)
        seen[key] = seen.get(key, 0) + 1
        out.append({"id": "np-%s-%d-%d" % (date, amount, seen[key]), "date": date, "amount": tidy(amount), "firm": "", "source": "notion"})
    return out


def parse_goal(flat):
    for head, n in flat:
        if n.get("t") in ("quote", "callout", "paragraph", "h1", "h2", "h3"):
            m = re.search(r"goal\D{0,12}\$?\s*([\d,]{4,})", text_of(n.get("r")), re.I)
            if m:
                return int(m.group(1).replace(",", ""))
    return None


def parse_scaling(flat):
    """The Account Scaling System databases: one row per account with phase, profit, drawdown and status."""
    out = []
    for head, n in flat:
        if n.get("t") != "db" or "scaling" not in str(n.get("title", "")).lower():
            continue
        cols = [str(c).strip().lower() for c in n.get("cols") or []]

        def at(row, *names):
            for nm in names:
                if nm in cols and cols.index(nm) < len(row):
                    return str(row[cols.index(nm)] or "").strip()
            return ""
        group = re.sub(r"^account scaling system\s*", "", str(n.get("title", "")), flags=re.I).strip(" ()") or "Accounts"
        for k, row in enumerate(n.get("rows") or []):
            firm = at(row, "prop firm")
            size = num(at(row, "account size"))
            if not firm and not size:
                continue
            out.append({"id": "s-%s-%d" % (slug(group), k), "firm": firm, "group": group, "size": tidy(size) if size else None,
                        "phase": at(row, "current phase"), "profit": tidy(num(at(row, "current profit"))),
                        "target": tidy(num(at(row, "profit target to pass/scale"))), "dd": tidy(num(at(row, "current drawdown used"))),
                        "maxdd": tidy(num(at(row, "max drawdown limit"))), "state": at(row, "status"),
                        "next": tidy(num(at(row, "next account size"))), "source": "notion"})
    return out


def from_notion(hub_doc):
    flat = scan(hub_doc)["flat"]
    accounts, typed = parse_funding_tables(flat)
    return {"accounts": accounts, "typed": typed, "rules": parse_rules(flat), "payouts": parse_payouts(flat),
            "goal": parse_goal(flat), "status": parse_scaling(flat)}


# ---- what the page may set
def clean_fields(raw, full=False):
    """Only known keys, bounded. `full` (a new account) requires firm, market, stage and size."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k, limit in TEXT_FIELDS.items():
        if isinstance(raw.get(k), str):
            out[k] = raw[k].strip()[:limit]
    if raw.get("market") in MARKETS:
        out["market"] = raw["market"]
    if raw.get("stage") in STAGES:
        out["stage"] = raw["stage"]
    for k in NUMBER_FIELDS:
        if k in raw:
            v = None if raw[k] in (None, "") else num(raw[k])
            if raw[k] not in (None, "") and v is None:
                raise FundingError("%s must be a number" % k)
            if v is not None and k == "size" and v <= 0:
                raise FundingError("size must be more than zero")
            out[k] = None if v is None else tidy(v)
    if full:
        if not out.get("firm"):
            raise FundingError("firm is required")
        for k in ("market", "stage", "size"):
            if not out.get(k):
                raise FundingError("%s is required" % k)
    return out


def clean_payout(raw):
    raw = raw if isinstance(raw, dict) else {}
    amount = num(raw.get("amount"))
    if amount is None or amount <= 0:
        raise FundingError("the pay-out amount must be a number above zero")
    date = str(raw.get("date") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise FundingError("the pay-out date must look like 2026-09-30")
    return {"date": date, "amount": tidy(amount), "firm": str(raw.get("firm") or "").strip()[:40]}


class Funding:
    def __init__(self, data_dir, hub_getter=lambda: {}):
        self.dir, self.hub = data_dir, hub_getter
        self.lock = threading.Lock()
        self.my = self._load()

    # ---- file
    def _path(self):
        return os.path.join(self.dir, FILE)

    def _load(self):
        out = {"accounts": [], "payouts": [], "over": {}, "hidden": [], "goal": None, "totals": {}, "seq": 0}
        try:
            with open(self._path(), encoding="utf-8-sig") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return out
        if not isinstance(doc, dict):
            return out
        out["accounts"] = [a for a in doc.get("accounts") or [] if isinstance(a, dict) and a.get("id")][:MAX_ACCOUNTS]
        out["payouts"] = [p for p in doc.get("payouts") or [] if isinstance(p, dict) and p.get("id")][:MAX_PAYOUTS]
        out["over"] = {str(k)[:60]: v for k, v in (doc.get("over") or {}).items() if isinstance(v, dict)} if isinstance(doc.get("over"), dict) else {}
        out["hidden"] = [str(x)[:60] for x in doc.get("hidden") or []][:1000]
        g = num(doc.get("goal"))
        out["goal"] = tidy(g) if g and g > 0 else None
        if isinstance(doc.get("totals"), dict):
            out["totals"] = {k: tidy(num(v)) for k, v in doc["totals"].items() if k in TOTAL_KEYS and num(v) is not None and num(v) >= 0}
        out["seq"] = int(doc["seq"]) if isinstance(doc.get("seq"), int) else 0
        return out

    def _save(self):
        tmp = self._path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.my, fh, indent=1)
        os.replace(tmp, self._path())

    def _new_id(self, prefix):
        self.my["seq"] += 1
        return "%s-%d" % (prefix, self.my["seq"])

    # ---- the merged picture
    def state(self):
        with self.lock:
            return self._state()

    def _state(self):
        hub = self.hub() or {}
        n = from_notion(hub)
        hidden, over = set(self.my["hidden"]), self.my["over"]

        def merged(item):
            it = dict(item)
            it.update({k: v for k, v in (over.get(item["id"]) or {}).items() if k in STATUS_FIELDS + NUMBER_FIELDS + ("firm", "market", "stage", "note")})
            return it
        accounts = [merged(a) for a in n["accounts"] if a["id"] not in hidden] + [dict(a, source="manual") for a in self.my["accounts"]]
        payouts = [p for p in n["payouts"] if p["id"] not in hidden] + [dict(p, source="manual") for p in self.my["payouts"]]
        status = [merged(s) for s in n["status"] if s["id"] not in hidden]
        status += [dict(a, group="Added by you") for a in accounts if a["source"] == "manual" and any(a.get(k) is not None for k in ("profit", "dd", "target", "maxdd"))]
        goal = self.my["goal"] or n["goal"] or DEFAULT_GOAL

        total = {m: {s: 0 for s in STAGES} for m in MARKETS}
        count = {m: {s: 0 for s in STAGES} for m in MARKETS}
        for a in accounts:
            if a.get("market") in MARKETS and a.get("stage") in STAGES and isinstance(a.get("size"), (int, float)):
                total[a["market"]][a["stage"]] += a["size"]
                count[a["market"]][a["stage"]] += 1
        by_stage = {s: sum(total[m][s] for m in MARKETS) for s in STAGES}
        everything = sum(by_stage.values())
        mine = self.my["totals"]
        calc = {m: sum(total[m].values()) for m in MARKETS}
        calc["all"] = everything
        calc["evals"] = by_stage["ongoing"] + by_stage["passed"]            # in evaluation, or passed and waiting for the paid activation
        calc["funded"] = by_stage["funded"]
        shown = {k: mine.get(k, calc[k]) for k in ("cfd", "futures", "evals", "funded")}
        if "all" in mine:
            shown["all"] = mine["all"]
        elif "evals" in mine or "funded" in mine:
            shown["all"] = shown["evals"] + shown["funded"]
        elif "cfd" in mine or "futures" in mine:
            shown["all"] = shown["cfd"] + shown["futures"]
        else:
            shown["all"] = everything
        custom = {k: k in mine for k in TOTAL_KEYS}
        paid = sum(p["amount"] for p in payouts)

        warnings = []
        if n["accounts"]:
            for (market, stage), typed in sorted(n["typed"].items()):
                real = sum(a["size"] for a in n["accounts"] if a["market"] == market and a["stage"] == stage)
                if real != typed:
                    warnings.append("Notion says %s %s in %s, but its rows add up to %s." % (
                        MARKET_NAMES[market], STAGE_NAMES[stage].lower(), "{:,}".format(int(typed)), "{:,}".format(int(real))))
        firms = {}
        for a in accounts:
            f = firms.setdefault(firm_key(a.get("firm")), {"firm": a.get("firm", ""), "total": 0, "count": 0, "funded": 0, "markets": set()})
            f["total"] += a.get("size") or 0
            f["count"] += 1
            f["funded"] += (a.get("size") or 0) if a.get("stage") == "funded" else 0
            f["markets"].add(a.get("market"))
        rules = {firm_key(r["firm"]): r for r in n["rules"]}
        by_firm = []
        for k, f in sorted(firms.items(), key=lambda kv: -kv[1]["total"]):
            by_firm.append({"firm": f["firm"], "total": tidy(f["total"]), "count": f["count"], "funded": tidy(f["funded"]),
                            "markets": sorted(x for x in f["markets"] if x), "rules": rules.get(k)})
        return {"accounts": accounts, "payouts": sorted(payouts, key=lambda p: p["date"]), "status": status, "by_firm": by_firm,
                "total": total, "count": count, "by_stage": by_stage, "all": tidy(shown["all"]), "calc": {k: tidy(v) for k, v in calc.items()}, "shown": {k: tidy(v) for k, v in shown.items()}, "custom": custom, "paid": tidy(paid), "goal": tidy(goal),
                "goal_custom": bool(self.my["goal"]), "warnings": warnings, "rules": n["rules"],
                "notion": {"rows": len(n["accounts"]), "at": (hub or {}).get("at", ""), "found": bool(n["accounts"] or n["status"])},
                "hidden": len(hidden), "markets": MARKET_NAMES, "stages": STAGE_NAMES}

    # ---- changes from the page
    def act(self, body):
        body = body if isinstance(body, dict) else {}
        action = body.get("action")
        with self.lock:
            m = self.my
            if action == "add":
                if len(m["accounts"]) >= MAX_ACCOUNTS:
                    raise FundingError("that is the most accounts that can be added by hand (%d)" % MAX_ACCOUNTS)
                f = clean_fields(body.get("account"), full=True)
                f["id"] = self._new_id("m")
                m["accounts"].append(f)
            elif action == "edit":
                ident = str(body.get("id") or "")
                f = clean_fields(body.get("fields"))
                if not f:
                    raise FundingError("nothing to change")
                mine = next((a for a in m["accounts"] if a["id"] == ident), None)
                if mine is not None:
                    mine.update(f)
                    for k in [k for k, v in mine.items() if v is None]:
                        del mine[k]
                else:
                    if not any(ident == a["id"] for a in from_notion(self.hub() or {})["accounts"] + from_notion(self.hub() or {})["status"]):
                        raise FundingError("that account is not in the Notion copy")
                    o = m["over"].setdefault(ident, {})
                    o.update(f)
            elif action == "remove":
                ident = str(body.get("id") or "")
                before = len(m["accounts"]) + len(m["payouts"])
                m["accounts"] = [a for a in m["accounts"] if a["id"] != ident]
                m["payouts"] = [p for p in m["payouts"] if p["id"] != ident]
                if len(m["accounts"]) + len(m["payouts"]) == before:
                    if ident not in m["hidden"]:
                        m["hidden"].append(ident[:60])                    # a Notion row: hidden here, untouched in Notion
            elif action == "restore":
                m["hidden"] = []
                m["over"] = {}
            elif action == "payout":
                if len(m["payouts"]) >= MAX_PAYOUTS:
                    raise FundingError("too many pay-outs added by hand")
                p = clean_payout(body.get("payout"))
                p["id"] = self._new_id("mp")
                m["payouts"].append(p)
            elif action == "totals":
                raw = body.get("totals") if isinstance(body.get("totals"), dict) else {}
                fresh = {}
                for k in TOTAL_KEYS:
                    if k not in raw:
                        if k in m["totals"]:
                            fresh[k] = m["totals"][k]
                        continue
                    if raw[k] in (None, ""):
                        continue                                       # cleared: back to the calculated number
                    v = num(raw[k])
                    if v is None or v < 0:
                        raise FundingError("%s must be a number, zero or more" % TOTAL_NAMES[k])
                    fresh[k] = tidy(v)
                m["totals"] = fresh
            elif action == "goal":
                g = num(body.get("goal")) if body.get("goal") not in (None, "") else None
                if body.get("goal") not in (None, "") and (g is None or g <= 0):
                    raise FundingError("the goal must be a number above zero")
                m["goal"] = tidy(g) if g else None
            else:
                raise FundingError('action must be "add", "edit", "remove", "restore", "payout", "totals" or "goal"')
            self._save()
            return self._state()
