"""Reads a Notion export of the Trade Journey database ("Markdown & CSV") and turns it into journal trades.

Put the export (the .zip, or the unzipped folder) in  trader/import/  and use Import from Notion on the Journal page.
Nothing is changed until you press Import; the preview only reads. Standard library only.

Notion's columns (Date, Account Type, Bias, Pair, Session, Long / Short, Risk, Win / Loss, R/R, Partials, Strategy, Zone,
Setup, Trade SS, Trade Breakdown, Trade No.) become journal fields. Results are in R and risk is a percent of the account,
so each trade is stored with unit "pct": risk is the risk percent and the result is risk percent times R.
"""

import csv
import io
import os
import re
import zipfile
from datetime import date
from urllib.parse import unquote

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


class ImportError_(Exception):
    pass


class Source:
    """Everything in an export, whether it is a folder, a zip, or a zip with zips inside (Notion splits big exports)."""

    def __init__(self):
        self.files = {}          # path (forward slashes) -> callable returning bytes
        self.zips = []           # zip files kept open until close(), because files are read on demand

    def close(self):
        for z in self.zips:
            try:
                z.close()
            except Exception:
                pass

    def add_folder(self, root):
        for base, _dirs, names in os.walk(root):
            for n in names:
                full = os.path.join(base, n)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                self.files[rel] = (lambda p=full: open(p, "rb").read())

    def add_zip(self, zf, prefix=""):
        self.zips.append(zf)
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                continue                                    # never trust a path that climbs out
            if name.lower().endswith(".zip"):
                try:
                    self.add_zip(zipfile.ZipFile(io.BytesIO(zf.read(info))), prefix)
                except zipfile.BadZipFile:
                    pass
                continue
            self.files[prefix + name] = (lambda i=info, z=zf: z.read(i))

    def read(self, path):
        return self.files[path]()


def open_source(import_dir):
    if not os.path.isdir(import_dir):
        raise ImportError_("Put your Notion export (the .zip, or the unzipped folder) in the folder  %s  and try again." % import_dir)
    src = Source()
    for name in sorted(os.listdir(import_dir)):
        full = os.path.join(import_dir, name)
        if os.path.isfile(full) and name.lower().endswith(".zip"):
            try:
                src.add_zip(zipfile.ZipFile(full))
            except zipfile.BadZipFile:
                src.close()
                raise ImportError_("%s is not a readable zip file." % name)
        elif os.path.isdir(full):
            src.add_folder(full)
    if not src.files:
        src.close()
        raise ImportError_("The folder  %s  has no export in it yet. Put the Notion .zip (or its unzipped folder) there." % import_dir)
    return src


def parse_date(text):
    s = (text or "").strip()
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)             # day first, as your Notion shows it
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.match(r"^([A-Za-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", s)             # September 1, 2025
            if m:
                mo, d, y = MONTHS.get(m.group(1).lower()), int(m.group(2)), int(m.group(3))
            else:
                m = re.match(r"^(\d{1,2}) ([A-Za-z]{3})[a-z]*\.?,? (\d{4})", s)             # 1 September 2025
                if not m:
                    return None
                d, mo, y = int(m.group(1)), MONTHS.get(m.group(2).lower()), int(m.group(3))
    try:
        return date(y, mo, d).isoformat()
    except (TypeError, ValueError):
        return None


def num(text):
    m = re.search(r"-?\d+(?:[.,]\d+)?", text or "")
    return float(m.group(0).replace(",", ".")) if m else None


def split_multi(text):
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def column(headers, *names):
    low = [h.strip().lower() for h in headers]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def pick_table(src):
    """The export's trades table: the CSV that has Date and Pair columns, preferring the one with the most rows."""
    best = None
    for path in src.files:
        if not path.lower().endswith(".csv"):
            continue
        try:
            text = src.read(path).decode("utf-8-sig", "replace")
        except Exception:
            continue
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            continue
        head = [h.strip().lower() for h in rows[0]]
        if "date" in head and "pair" in head and (best is None or len(rows) > len(best[1])):
            best = (path, rows)
    if not best:
        raise ImportError_("No Trade Journey table found. Export the database from Notion as Markdown & CSV and include the CSV.")
    return best


def find_image(src, cell):
    """The file a Trade SS cell points at. Notion writes paths URL-encoded; fall back to the file name if the folders differ."""
    by_name = {}
    for p in src.files:
        if p.lower().endswith(IMAGE_EXT):
            by_name.setdefault(os.path.basename(p).lower(), []).append(p)
    for part in re.findall(r"[^,]*?\.(?:png|jpe?g|webp|gif)", cell or "", flags=re.I):
        path = unquote(part.strip()).replace("\\", "/")
        if not path.lower().endswith(IMAGE_EXT):
            continue
        if path in src.files:
            return path
        tails = [p for p in src.files if p.endswith("/" + path) or path.endswith("/" + p) or p == path]
        if tails:
            return tails[0]
        names = by_name.get(os.path.basename(path).lower(), [])
        if len(names) == 1:
            return names[0]
    return None


def convert(src):
    """-> (trades, report). A trade's "image" is the export path of its screenshot; the server swaps it for a stored name."""
    path, rows = pick_table(src)
    head = rows[0]
    ix = {k: column(head, *v) for k, v in {
        "date": ("date",), "acct": ("account type", "account"), "bias": ("bias",), "pair": ("pair", "instrument"),
        "session": ("session",), "dir": ("long / short", "long/short", "direction"), "risk": ("risk", "risk %"),
        "wl": ("win / loss", "win/loss", "result"), "rr": ("r/r", "rr", "r:r"), "partials": ("partials",),
        "strategy": ("strategy",), "zone": ("zone",), "setup": ("setup", "setups"), "ss": ("trade ss", "screenshot", "image"),
        "notes": ("trade breakdown", "breakdown", "notes"), "no": ("trade no.", "trade no", "no.", "#")}.items()}

    def get(row, key):
        i = ix[key]
        return row[i].strip() if i is not None and i < len(row) else ""

    skipped, trades = {}, []
    report = {"table": path, "rows": len(rows) - 1, "skipped": skipped, "with_image": 0, "missing_image": 0, "live": 0, "backtest": 0}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        wl, acct = get(row, "wl").upper(), get(row, "acct")
        if acct.lower().startswith("no setup") or wl in ("NO TRADE", ""):
            skip("no trade that day")
            continue
        if wl == "INVALID":
            skip("marked invalid")
            continue
        d = parse_date(get(row, "date"))
        if not d:
            skip("date could not be read")
            continue
        direction = get(row, "dir").lower()
        if direction not in ("long", "short"):
            skip("no long or short")
            continue
        pair = re.sub(r"[^A-Z0-9]", "", get(row, "pair").upper())
        if not pair or pair == "NODATA":
            skip("no pair")
            continue
        risk = num(get(row, "risk"))
        if not risk or risk <= 0:
            skip("no risk percent")
            continue
        rr = num(get(row, "rr"))
        if "WIN" in wl:
            r = abs(rr) if rr else None
        elif "BREAK" in wl or wl == "BE":
            r = 0.0
        elif "LOSS" in wl or wl == "LOSE":
            r = -1.0
        else:
            r = None
        if r is None:
            skip("result could not be read")
            continue
        al = acct.lower()
        kind = "live" if "live" in al else "backtest"
        t = {"date": d, "inst": pair[:20], "dir": direction, "tag": " + ".join(split_multi(get(row, "setup")))[:60],
             "risk": round(risk, 4), "pl": round(risk * r, 4), "unit": "pct", "r": round(r, 4), "riskPct": round(risk, 4),
             "notes": get(row, "notes")[:500], "acct": kind, "zone": get(row, "zone")[:40], "strategy": get(row, "strategy")[:30],
             "session": get(row, "session")[:20], "partials": get(row, "partials").lower() if get(row, "partials").lower() in ("yes", "no") else ""}
        b = get(row, "bias").lower()
        t["bias"] = b if b in ("bullish", "bearish", "neutral") else ""
        n = num(get(row, "no"))
        if n is not None:
            t["no"] = int(n)
        cell = get(row, "ss")
        if cell:
            found = find_image(src, cell)
            if found:
                t["image"] = found
                report["with_image"] += 1
            else:
                report["missing_image"] += 1
        report[kind] += 1
        trades.append(t)
    return trades, report
