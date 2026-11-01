"""Second Brain: your own editable, Notion-style pages and databases, kept in trader/brain/brain.json.

It starts as a copy of your Notion Trade Journal (the pages from the live link or from notion/hub.json, and the
Trade Journal 2026 table from your Notion export) and from then on it is yours to edit here. Nothing in this module
talks to Notion, and nothing you change here is ever sent back to Notion.

The document:
    {"version": 1, "rev": 7, "root": "main",
     "pages": {id: {"title", "icon", "parent", "nodes": [block, ...]}},
     "dbs":   {id: {"title", "props": [{"id","name","type","options":[{"name","color"}]}],
                    "rows": [{"id", "v": {propId: value}, "nodes": [block, ...]}],
                    "view": {"sort": [{"p","dir"}], "filters": [{"p","op","val"}], "hidden": [propId]}}}}

A block is {"t": h1|h2|h3|paragraph|li|ol|todo|quote|callout|hr|code|toggle|table|cols|page|db|img|file|link, ...}
with text as runs: [{"x": "text", "b","i","u","s","c": bool, "co": "red" or "red_background", "a": url}].
Standard library only.
"""

import copy
import json
import os
import re
import time

import notion_import
import store

VERSION = 1
MAX_BYTES = 12 * 1024 * 1024
MAX_PAGES, MAX_DBS, MAX_ROWS, MAX_PROPS, MAX_DEPTH, MAX_TEXT = 600, 100, 5000, 60, 8, 20000
BLOCKS = ("h1", "h2", "h3", "paragraph", "li", "ol", "todo", "quote", "callout", "hr", "code", "toggle", "table", "cols",
          "page", "db", "img", "file", "link")
PROP_TYPES = ("title", "text", "number", "select", "multi_select", "date", "checkbox", "url", "files")
COLORS = ("default", "gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TJ_PAGE, TJ_DB = "tj2026", "tj2026"


class BrainError(Exception):
    pass


def folder(data_dir):
    return os.path.join(data_dir, "brain")


def doc_path(data_dir):
    return os.path.join(folder(data_dir), "brain.json")


# ---------------------------------------------------------------- cleaning (everything the browser sends goes through here)

def _text(v, limit=MAX_TEXT):
    return v[:limit] if isinstance(v, str) else ""


def _id(v, what="id"):
    if not isinstance(v, str) or not ID.match(v):
        raise BrainError("bad %s" % what)
    return v


def clean_runs(runs):
    out = []
    for r in (runs if isinstance(runs, list) else [])[:400]:
        if not isinstance(r, dict) or not isinstance(r.get("x"), str):
            continue
        c = {"x": r["x"][:MAX_TEXT]}
        for k in ("b", "i", "u", "s", "c"):
            if r.get(k):
                c[k] = True
        co = r.get("co")
        if isinstance(co, str) and re.match(r"^[a-z]{3,10}(_background)?$", co):
            c["co"] = co
        a = r.get("a")
        if isinstance(a, str) and re.match(r"^(https?://|mailto:)", a) and len(a) < 2000:
            c["a"] = a
        out.append(c)
    return out


def clean_node(n, depth=0):
    if not isinstance(n, dict) or n.get("t") not in BLOCKS or depth > MAX_DEPTH:
        return None
    t = n["t"]
    c = {"t": t}
    if t in ("h1", "h2", "h3", "paragraph", "li", "ol", "todo", "quote", "callout", "toggle"):
        c["r"] = clean_runs(n.get("r"))
    if t == "todo" and n.get("done"):
        c["done"] = True
    if t == "callout":
        c["ic"] = _text(n.get("ic"), 8)
    co = n.get("co")
    if isinstance(co, str) and re.match(r"^[a-z]{3,10}(_background)?$", co):
        c["co"] = co
    if t == "code":
        c["x"] = _text(n.get("x"))
    if t in ("li", "ol", "todo", "quote", "callout", "toggle", "paragraph", "h1", "h2", "h3"):
        kids = [k for k in (clean_node(x, depth + 1) for x in (n.get("k") if isinstance(n.get("k"), list) else [])[:500]) if k]
        if kids:
            c["k"] = kids
    if t == "table":
        rows = []
        for row in (n.get("rows") if isinstance(n.get("rows"), list) else [])[:300]:
            if isinstance(row, list):
                rows.append([clean_runs(cell) for cell in row[:40]])
        width = max([len(r) for r in rows] or [1])
        c["rows"] = [r + [[] for _ in range(width - len(r))] for r in rows] or [[[]]]
        c["head"] = bool(n.get("head"))
        c["rowhead"] = bool(n.get("rowhead"))
    if t == "cols":
        cols = []
        for col in (n.get("cols") if isinstance(n.get("cols"), list) else [])[:6]:
            kids = col.get("k") if isinstance(col, dict) and isinstance(col.get("k"), list) else []
            cols.append({"k": [k for k in (clean_node(x, depth + 1) for x in kids[:500]) if k]})
        c["cols"] = cols or [{"k": []}, {"k": []}]
    if t == "page":
        c["title"] = _text(n.get("title"), 200)
        c["id"] = n["id"] if isinstance(n.get("id"), str) and ID.match(n["id"]) else ""
        if n.get("locked"):
            c["locked"] = True
        c["ic"] = _text(n.get("ic"), 8)
    if t == "db":
        c["id"] = _id(n.get("id"), "database id")
    if t == "img":
        name = n.get("img")
        if not (isinstance(name, str) and store.IMAGE_NAME.match(name)):
            return None
        c["img"] = name
    if t == "file":
        c["name"] = _text(n.get("name"), 300)
    if t == "link":
        u = n.get("url")
        c["url"] = u if isinstance(u, str) and len(u) < 2000 else ""
    return c


def clean_nodes(nodes):
    return [k for k in (clean_node(x) for x in (nodes if isinstance(nodes, list) else [])[:2000]) if k]


def clean_value(typ, v):
    if typ in ("title", "text", "url"):
        return _text(v, 5000)
    if typ == "number":
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else None
    if typ == "checkbox":
        return bool(v)
    if typ == "date":
        return v if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v) else ""
    if typ == "select":
        return _text(v, 120)
    if typ == "multi_select":
        return [_text(x, 120) for x in (v if isinstance(v, list) else [])[:40] if isinstance(x, str) and x]
    if typ == "files":
        return [x for x in (v if isinstance(v, list) else [])[:40] if isinstance(x, str) and store.IMAGE_NAME.match(x)]
    return None


def clean_db(db):
    if not isinstance(db, dict):
        raise BrainError("a database is not an object")
    props, seen = [], set()
    for p in (db.get("props") if isinstance(db.get("props"), list) else [])[:MAX_PROPS]:
        if not isinstance(p, dict) or p.get("type") not in PROP_TYPES:
            continue
        pid = _id(p.get("id"), "property id")
        if pid in seen:
            continue
        seen.add(pid)
        opts = []
        for o in (p.get("options") if isinstance(p.get("options"), list) else [])[:300]:
            if isinstance(o, dict) and isinstance(o.get("name"), str) and o["name"]:
                opts.append({"name": o["name"][:120], "color": o.get("color") if o.get("color") in COLORS else "default"})
        props.append({"id": pid, "name": _text(p.get("name"), 120), "type": p["type"], "options": opts})
    if not any(p["type"] == "title" for p in props):
        props.insert(0, {"id": "title", "name": "Name", "type": "title", "options": []})
    types = {p["id"]: p["type"] for p in props}
    rows, rseen = [], set()
    for r in (db.get("rows") if isinstance(db.get("rows"), list) else [])[:MAX_ROWS]:
        if not isinstance(r, dict):
            continue
        rid = _id(r.get("id"), "row id")
        if rid in rseen:
            continue
        rseen.add(rid)
        vals = r.get("v") if isinstance(r.get("v"), dict) else {}
        v = {}
        for pid, typ in types.items():
            if pid in vals:
                cv = clean_value(typ, vals[pid])
                if cv not in (None, "", [], False):
                    v[pid] = cv
        row = {"id": rid, "v": v}
        nodes = clean_nodes(r.get("nodes"))
        if nodes:
            row["nodes"] = nodes
        rows.append(row)
    view = db.get("view") if isinstance(db.get("view"), dict) else {}
    ok = lambda pid: isinstance(pid, str) and pid in types
    sort = [{"p": s["p"], "dir": "desc" if s.get("dir") == "desc" else "asc"}
            for s in (view.get("sort") if isinstance(view.get("sort"), list) else [])[:3] if isinstance(s, dict) and ok(s.get("p"))]
    filters = [{"p": f["p"], "op": _text(f.get("op"), 20) or "contains", "val": _text(f.get("val"), 200)}
               for f in (view.get("filters") if isinstance(view.get("filters"), list) else [])[:8] if isinstance(f, dict) and ok(f.get("p"))]
    hidden = [h for h in (view.get("hidden") if isinstance(view.get("hidden"), list) else []) if ok(h)]
    return {"title": _text(db.get("title"), 200), "props": props, "rows": rows,
            "view": {"sort": sort, "filters": filters, "hidden": hidden}}


def clean_doc(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("pages"), dict):
        raise BrainError("that is not a Second Brain document")
    pages = {}
    for pid, p in list(doc["pages"].items())[:MAX_PAGES]:
        if not isinstance(p, dict):
            continue
        pages[_id(pid, "page id")] = {"title": _text(p.get("title"), 200), "icon": _text(p.get("icon"), 8),
                                      "parent": p.get("parent") if isinstance(p.get("parent"), str) and ID.match(p["parent"]) else "",
                                      "nodes": clean_nodes(p.get("nodes"))}
    dbs = {}
    for did, d in list((doc.get("dbs") if isinstance(doc.get("dbs"), dict) else {}).items())[:MAX_DBS]:
        dbs[_id(did, "database id")] = clean_db(d)
    root = doc.get("root") if doc.get("root") in pages else (next(iter(pages), ""))
    if not root:
        raise BrainError("the document has no pages")
    return {"version": VERSION, "rev": int(doc.get("rev") or 0), "root": root, "pages": pages, "dbs": dbs}


# ---------------------------------------------------------------- the file

def load(data_dir):
    """The saved document, or None when there is none yet."""
    try:
        with open(doc_path(data_dir), encoding="utf-8-sig") as fh:
            return clean_doc(json.load(fh))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, BrainError) as e:
        raise BrainError("Second Brain could not be read (%s). Your last good copy is brain.bak.json in %s" % (e, folder(data_dir)))


def _write(data_dir, doc):
    os.makedirs(folder(data_dir), exist_ok=True)
    path = doc_path(data_dir)
    raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise BrainError("Second Brain is bigger than %d MB" % (MAX_BYTES // 1024 // 1024))
    bak = os.path.join(folder(data_dir), "brain.bak.json")
    try:                                              # a copy of the previous state, refreshed at most every 30 minutes
        if os.path.isfile(path) and (not os.path.isfile(bak) or time.time() - os.path.getmtime(bak) > 1800):
            with open(path, "rb") as a, open(bak, "wb") as b:
                b.write(a.read())
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(raw)
    os.replace(tmp, path)


def save(data_dir, body):
    """Keep what the page sent. `rev` must be the revision the page loaded, so an old tab cannot overwrite newer work."""
    new = clean_doc(body)
    have = load(data_dir)
    if have is not None and int(body.get("rev") or 0) != have["rev"]:
        raise Conflict("Second Brain was changed somewhere else (another tab or a sync). Reload the page to see the latest.")
    new["rev"] = (have["rev"] if have else 0) + 1
    _write(data_dir, new)
    return new["rev"]


class Conflict(BrainError):
    pass


def update(data_dir, fn):
    """Change the stored document from the server side (used by the Notion live link). fn(doc) edits it in place."""
    doc = load(data_dir)
    if doc is None:
        return None
    fn(doc)
    doc = clean_doc(doc)
    doc["rev"] = load(data_dir)["rev"] + 1
    _write(data_dir, doc)
    return doc


# ---------------------------------------------------------------- the Trade Journal 2026 database

TJ_SPEC = [   # (column name, type) in the order Notion shows them; "auto" is select, or multi-select when a cell holds several
    ("Strategy + Setup", "title"), ("Date", "date"), ("Trade No.", "number"), ("Pair", "auto"), ("Long / Short", "auto"),
    ("Win / Loss", "auto"), ("R/R", "auto"), ("Risk", "auto"), ("Account Type", "auto"), ("Strategy", "auto"),
    ("Setup", "multi_select"), ("Zone", "auto"), ("Session", "auto"), ("Bias", "auto"), ("Partials", "auto"),
    ("Profit", "number"), ("Trade Breakdown", "text"), ("Trade SS", "files")]
NOTION_COLORS = {   # the colours Notion gives these options
    "win / loss": {"WIN": "green", "LOSS": "red", "BREAKEVEN": "yellow", "NO TRADE": "default", "INVALID": "pink", "TEST": "purple"},
    "long / short": {"LONG": "green", "SHORT": "red"},
    "bias": {"BULLISH": "green", "BEARISH": "red", "HUGGING": "orange"},
    "account type": {"FUNDED": "green", "LIVE": "red", "BACKTEST": "purple", "FORWARD-TEST": "blue", "DEMO": "orange",
                     "EVALUATION": "yellow", "NO SETUP": "gray", "NO TRADING": "pink"},
    "partials": {"YES": "green", "NO": "red"}}
PALETTE = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")


def option_color(prop_name, option):
    key = option.strip().upper()
    for pname, table in NOTION_COLORS.items():
        if prop_name.strip().lower() == pname:
            for k, col in table.items():
                if key == k or key.startswith(k):
                    return col
    if prop_name.strip().lower() == "pair":
        return "yellow" if "/" in option else "purple"
    return PALETTE[sum(ord(ch) for ch in option) % len(PALETTE)]


def _slug(name, used):
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30].strip("_") or "p"
    pid, n = base, 2
    while pid in used:
        pid, n = "%s_%d" % (base, n), n + 1
    used.add(pid)
    return pid


def _images(src, cell, index):
    """All the stored-file paths a Trade SS cell points at."""
    out = []
    for part in re.findall(r"[^,]*?\.(?:png|jpe?g|webp|gif)", cell or "", flags=re.I):
        path = notion_import.unquote(part.strip()).replace("\\", "/")
        hit = None
        if path in src.files:
            hit = path
        else:
            tails = [p for p in src.files if p.endswith("/" + path) or path.endswith("/" + p)]
            hit = tails[0] if tails else None
            if not hit:
                names = index.get(os.path.basename(path).lower(), [])
                hit = names[0] if len(names) == 1 else None
        if hit and hit not in out:
            out.append(hit)
    return out


def db_from_table(rows, data_dir=None, src=None, title="Trade Journal 2026"):
    """A database built from the export's table (its first row is the header). Screenshots are copied into the journal's image folder."""
    if not rows or len(rows) < 2:
        raise BrainError("the table has no rows")
    head = [h.strip() for h in rows[0]]
    low = [h.lower() for h in head]
    spec = [(n, t) for n, t in TJ_SPEC if n.lower() in low]
    known = {n.lower() for n, _ in TJ_SPEC}
    spec += [(h, "text") for h in head if h and h.lower() not in known]
    body = [r for r in rows[1:] if any(c.strip() for c in r)]
    used, props, cols = set(), [], {}
    for name, typ in spec:
        idx = low.index(name.lower())
        cells = [(r[idx] if idx < len(r) else "").strip() for r in body]
        if typ == "auto":
            typ = "multi_select" if any("," in c for c in cells) else "select"
        pid = "title" if typ == "title" else _slug(name, used)
        used.add(pid)
        opts = []
        if typ in ("select", "multi_select"):
            seen = []
            for c in cells:
                for part in ([p.strip() for p in c.split(",")] if typ == "multi_select" else [c]):
                    if part and part not in seen:
                        seen.append(part)
            opts = [{"name": o, "color": option_color(name, o)} for o in sorted(seen, key=str.lower)]
        props.append({"id": pid, "name": name, "type": typ, "options": opts})
        cols[pid] = idx
    index = {}
    if src is not None:
        for p in src.files:
            if p.lower().endswith(notion_import.IMAGE_EXT):
                index.setdefault(os.path.basename(p).lower(), []).append(p)
    out = []
    for n, r in enumerate(body):
        v = {}
        for p in props:
            cell = (r[cols[p["id"]]] if cols[p["id"]] < len(r) else "").strip()
            if not cell:
                continue
            if p["type"] == "date":
                d = notion_import.parse_date(cell)
                if d:
                    v[p["id"]] = d
            elif p["type"] == "number":
                x = notion_import.num(cell)
                if x is not None:
                    v[p["id"]] = x
            elif p["type"] == "multi_select":
                v[p["id"]] = [x.strip() for x in cell.split(",") if x.strip()]
            elif p["type"] == "files":
                names = []
                if src is not None and data_dir:
                    for path in _images(src, cell, index):
                        try:
                            names.append(store.save_image(data_dir, src.read(path)))
                        except (store.StoreError, KeyError, OSError):
                            pass
                if names:
                    v[p["id"]] = names
            else:
                v[p["id"]] = cell
        out.append({"id": "r%d" % (n + 1), "v": v})
    no = next((p["id"] for p in props if p["name"] == "Trade No."), None)
    dt = next((p["id"] for p in props if p["name"] == "Date"), None)
    out.sort(key=lambda r: (r["v"].get(no) is not None, r["v"].get(no) or 0, r["v"].get(dt) or ""), reverse=True)
    return {"title": title, "props": props, "rows": out, "view": {"sort": [], "filters": [], "hidden": []}}


# ---------------------------------------------------------------- the first copy

def _convert(nodes, pages, dbs, parent, used):
    """Hub blocks -> editable blocks. A placeholder for a page that was never read becomes a new empty page you can write in."""
    out = []
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        t = n.get("t")
        if t == "group":
            out.extend(_convert(n.get("k"), pages, dbs, parent, used))
            continue
        n = dict(n)
        if t == "file" and parent == "main" and n.get("name") and not re.search(r"\.(png|jpe?g|gif|webp|pdf|zip)$", n["name"], re.I):
            pid = _slug(n["name"], used)
            pages[pid] = {"title": n["name"], "icon": "", "parent": parent, "nodes": [{"t": "paragraph", "r": []}]}
            out.append({"t": "page", "title": n["name"], "id": pid, "ic": ""})
            continue
        if t == "paragraph" and parent == "main" and "live Notion link is set up" in "".join(r.get("x", "") for r in n.get("r") or []):
            continue                                       # the note that only made sense while the pages were placeholders
        if t == "db":
            did = _slug("db_" + (n.get("title") or "table"), used)
            props, rows = [], []
            cols = [str(c) for c in (n.get("cols") or [])]
            for i, c in enumerate(cols):
                props.append({"id": "title" if i == 0 else "c%d" % i, "name": c or "Name", "type": "title" if i == 0 else "text", "options": []})
            for ri, r in enumerate(n.get("rows") or []):
                rows.append({"id": "r%d" % (ri + 1), "v": {p["id"]: str(r[i]) for i, p in enumerate(props) if i < len(r) and str(r[i])}})
            dbs[did] = {"title": n.get("title") or "Table", "props": props or [{"id": "title", "name": "Name", "type": "title", "options": []}],
                        "rows": rows, "view": {"sort": [], "filters": [], "hidden": []}}
            out.append({"t": "db", "id": did})
            continue
        if t == "page" and n.get("locked"):
            out.append({"t": "page", "title": n.get("title", ""), "id": "", "locked": True})
            continue
        if t == "page" and n.get("id"):
            n["id"] = _slug(n["id"], used) if not ID.match(n["id"]) else n["id"]
        for key in ("k",):
            if n.get(key):
                n[key] = _convert(n[key], pages, dbs, parent, used)
        if n.get("cols"):
            n["cols"] = [{"k": _convert(c.get("k"), pages, dbs, parent, used)} for c in n["cols"] if isinstance(c, dict)]
        out.append(n)
    return out


def seed(data_dir, log=None):
    """Make the first copy: your Notion pages (from the live link, or the hand copy in notion/hub.json) and the Trade Journal 2026 table
    (from the Notion export in import-done or import). Returns the saved document."""
    hub = None
    for name in ("hub.json",):
        try:
            with open(os.path.join(data_dir, "notion", name), encoding="utf-8-sig") as fh:
                hub = json.load(fh)
        except (OSError, ValueError):
            pass
    pages, dbs, used = {}, {}, {"main", TJ_PAGE}
    root = "main"
    if isinstance(hub, dict) and isinstance(hub.get("pages"), dict) and hub["pages"]:
        hub_root = hub.get("root") if hub.get("root") in hub["pages"] else next(iter(hub["pages"]))
        ids = {hub_root: "main"}
        for pid in hub["pages"]:
            if pid != hub_root:
                ids[pid] = _slug(pid, used) if not ID.match(pid) else pid
                used.add(ids[pid])
        for pid, p in hub["pages"].items():
            new = ids[pid]
            pages[new] = {"title": str(p.get("title") or "Page"), "icon": str(p.get("icon") or ""), "parent": "" if new == "main" else "main", "nodes": []}
        for pid, p in hub["pages"].items():
            new = ids[pid]
            nodes = _convert(p.get("nodes"), pages, dbs, new, used)
            for n in _walk(nodes):
                if n.get("t") == "page" and n.get("id") in ids:
                    n["id"] = ids[n["id"]]
            pages[new]["nodes"] = nodes
    else:
        pages["main"] = {"title": "Trade Journal", "icon": "", "parent": "",
                         "nodes": [{"t": "h1", "r": [{"x": "Trade Journal"}]}, {"t": "paragraph", "r": []}]}
    pages["main"]["nodes"].insert(0, {"t": "page", "title": "Trade Journal 2026", "id": TJ_PAGE, "ic": "\U0001F4D2"})
    pages[TJ_PAGE] = {"title": "Trade Journal 2026", "icon": "\U0001F4D2", "parent": "main", "nodes": [{"t": "db", "id": TJ_DB}]}
    dbs[TJ_DB] = _journal_db(data_dir, log)
    doc = clean_doc({"version": VERSION, "rev": 0, "root": root, "pages": pages, "dbs": dbs})
    _write(data_dir, doc)
    return doc


def _walk(nodes):
    for n in nodes or []:
        yield n
        for k in _walk(n.get("k")):
            yield k
        for c in n.get("cols") or []:
            if isinstance(c, dict):
                for k in _walk(c.get("k")):
                    yield k


def _journal_db(data_dir, log=None):
    """The Trade Journal 2026 table from an export in import-done or import, or an empty one with the same columns."""
    for sub in ("import-done", "import"):
        path = os.path.join(data_dir, sub)
        try:
            src = notion_import.open_source(path)
        except notion_import.ImportError_:
            continue
        try:
            table_path, rows = notion_import.pick_table(src)
            return db_from_table(rows, data_dir, src)
        except (notion_import.ImportError_, BrainError):
            pass
        finally:
            src.close()
    empty = [[n for n, _ in TJ_SPEC]]
    props = db_from_table(empty + [["" for _ in TJ_SPEC]])["props"]
    return {"title": "Trade Journal 2026", "props": props, "rows": [], "view": {"sort": [], "filters": [], "hidden": []}}


def merge_rows(data_dir, trades_table_rows, src=None):
    """New Notion rows (by Trade No. and date) join the Trade Journal 2026 database. Rows you already have are left as you edited them."""
    def apply(doc):
        db = doc["dbs"].get(TJ_DB)
        if not db:
            return
        fresh = db_from_table(trades_table_rows, data_dir, src)
        by_name = {p["name"]: p["id"] for p in db["props"]}
        no, dt = by_name.get("Trade No."), by_name.get("Date")
        have = {(r["v"].get(no), r["v"].get(dt)) for r in db["rows"]}
        fp = {p["id"]: p for p in fresh["props"]}
        added = 0
        for r in fresh["rows"]:
            fno, fdt = fresh_val(fresh, r, "Trade No."), fresh_val(fresh, r, "Date")
            if (fno, fdt) in have:
                continue
            v = {}
            for pid, val in r["v"].items():
                name = fp[pid]["name"]
                if name in by_name:
                    v[by_name[name]] = val
                    tgt = next(p for p in db["props"] if p["id"] == by_name[name])
                    if tgt["type"] in ("select", "multi_select"):
                        for o in ([val] if isinstance(val, str) else val):
                            if o and o not in [x["name"] for x in tgt["options"]]:
                                tgt["options"].append({"name": o, "color": option_color(name, o)})
            db["rows"].insert(0, {"id": "n%d_%d" % (int(time.time()), added), "v": v})
            added += 1
    return update(data_dir, apply)


def fresh_val(db, row, prop_name):
    pid = next((p["id"] for p in db["props"] if p["name"] == prop_name), None)
    return row["v"].get(pid)


def status(doc):
    return {"pages": len(doc["pages"]), "dbs": len(doc["dbs"]), "rows": sum(len(d["rows"]) for d in doc["dbs"].values())}
