"""A live, read-only link to your Notion, so the dashboard follows what you write there.

It needs a Notion "integration" secret: notion.so/profile/integrations, New integration, give it ONLY the "Read content"
capability, and save the secret as  trader/notion_key.txt  (or set NOTION_TOKEN). Then open your Trade Journal page in Notion,
... menu, Connections, and add the integration. Nothing here ever writes to Notion: every call goes through one gate that only
lets through reading a page, its blocks, a database, and querying a database (which only reads). Anything else raises.

What it brings over:
  journal   the rows of the Trade Journal 2026 database, turned into journal trades by the same rules as the Notion export
  hub       the Trade Journal page and the pages under it, with their tables, lists, columns and inline databases, as plain
            data that the dashboard's Notion tab draws in the same layout

A page called "Thoughts + Emotions" is never read, even though the integration can reach it. Standard library only.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request

import notion_import
import store

API = "https://api.notion.com"
VERSION = "2022-06-28"
KEY_FILE = "notion_key.txt"
DEFAULT_JOURNAL_DB = "349fa215289f800a9929f80507ddace1"        # Trade Journal 2026
DEFAULT_HUB_PAGE = "b5dfa215289f822d90698113a6114061"          # Trade Journal
NEVER_READ = ("thoughts + emotions",)
ID = r"[0-9a-fA-F-]{32,36}"
GATE = (("GET", re.compile(r"^/v1/(blocks/%s/children|databases/%s|pages/%s)(\?[A-Za-z0-9_=&%%.-]*)?$" % (ID, ID, ID))),
        ("POST", re.compile(r"^/v1/databases/%s/query$" % ID)))
COLUMNS = ["Date", "Account Type", "Bias", "Pair", "Session", "Long / Short", "Risk", "Win / Loss", "R/R", "Partials", "Strategy",
           "Zone", "Setup", "Trade SS", "Trade Breakdown", "Trade No.", "Profit"]
MAX_BLOCKS = 4000
MAX_PAGES = 40
MAX_DB_ROWS = 300
MAX_IMAGES = 80


class NotionError(Exception):
    pass


def read_token(data_dir, environ=None):
    environ = os.environ if environ is None else environ
    value = (environ.get("NOTION_TOKEN") or "").strip()
    if value:
        return value
    try:
        with open(os.path.join(data_dir, KEY_FILE), encoding="utf-8-sig") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def settings(config_path):
    out = {"journal_db": DEFAULT_JOURNAL_DB, "hub_page": DEFAULT_HUB_PAGE}
    try:
        with open(config_path, encoding="utf-8-sig") as fh:
            sec = (json.load(fh) or {}).get("notion")
        if isinstance(sec, dict):
            for k in out:
                v = str(sec.get(k) or "").replace("-", "").strip()
                if re.fullmatch(r"[0-9a-fA-F]{32}", v):
                    out[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    return out


class Client:
    def __init__(self, token, opener=None, pause=0.35):
        self.token, self.opener, self.pause = token, opener or urllib.request.urlopen, pause
        self.calls = 0

    def call(self, method, path, body=None):
        if not any(m == method and rx.match(path) for m, rx in GATE):
            raise NotionError("refused: this link only reads from Notion (%s %s)" % (method, path.split("?")[0]))
        req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + self.token, "Notion-Version": VERSION,
                                              "Content-Type": "application/json", "Accept": "application/json"})
        if self.calls and self.pause:
            time.sleep(self.pause)
        self.calls += 1
        try:
            with self.opener(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            e.close()
            hint = {401: "the Notion secret was not accepted", 403: "the integration cannot reach that page: add it under Connections on the page",
                    404: "Notion cannot find it: add the integration under Connections on the Trade Journal page", 429: "Notion asked to slow down, will retry"}
            raise NotionError("%s (HTTP %d)" % (hint.get(e.code, "Notion refused the request"), e.code))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise NotionError("could not reach Notion (%s)" % getattr(e, "reason", e))

    def pages(self, method, path, body=None, limit=100000):
        """Every result of a paginated call."""
        out, cursor = [], None
        while True:
            if method == "GET":
                res = self.call("GET", path + ("&" if "?" in path else "?") + "page_size=100" + ("&start_cursor=" + cursor if cursor else ""))
            else:
                b = dict(body or {}, page_size=100)
                if cursor:
                    b["start_cursor"] = cursor
                res = self.call("POST", path, b)
            out += res.get("results") or []
            cursor = res.get("next_cursor")
            if not res.get("has_more") or not cursor or len(out) >= limit:
                return out[:limit]


def runs_text(rich):
    return "".join(str(r.get("plain_text") or "") for r in (rich or []))


def prop_text(p):
    """A property's value as the plain text Notion would show in a table cell."""
    if not isinstance(p, dict):
        return ""
    t, v = p.get("type"), p.get(p.get("type"))
    if t in ("title", "rich_text"):
        return runs_text(v)
    if t in ("select", "status"):
        return (v or {}).get("name", "")
    if t == "multi_select":
        return ", ".join(o.get("name", "") for o in v or [])
    if t == "date":
        d = v or {}
        return (d.get("start") or "") + (" to " + d["end"] if d.get("end") else "")
    if t == "number":
        return "" if v is None else ("%g" % v)
    if t == "checkbox":
        return "yes" if v else ""
    if t in ("url", "email", "phone_number"):
        return v or ""
    if t == "files":
        return ", ".join(f.get("name", "") for f in v or [])
    if t == "formula":
        return prop_text({"type": (v or {}).get("type"), (v or {}).get("type"): (v or {}).get((v or {}).get("type"))}) if v else ""
    if t in ("created_time", "last_edited_time"):
        return str(v or "")[:10]
    if t == "people":
        return ", ".join(x.get("name", "") for x in v or [])
    return ""


def journal(client, db_id):
    """-> (trades, report, rows): the Trade Journal database as journal trades. "row" on a trade indexes into rows (page id, files)."""
    pages = client.pages("POST", "/v1/databases/%s/query" % db_id, {"sorts": [{"property": "Date", "direction": "ascending"}]})
    table, meta = [COLUMNS], []
    for pg in pages:
        props = {k.strip().lower(): v for k, v in (pg.get("properties") or {}).items()}
        table.append([prop_text(props.get(c.lower())) for c in COLUMNS])
        meta.append({"id": pg.get("id"), "files": (props.get("trade ss") or {}).get("files") or []})
    trades, report = notion_import.convert_table("notion", table)
    return trades, report, meta


def attach_images(data_dir, trades, meta, fetch, cache):
    """Download each trade's first screenshot once. `cache` maps page id + file name to the stored name and is saved by the caller."""
    got = 0
    for t in trades:
        row = t.pop("row", None)
        m = meta[row] if row is not None and row < len(meta) else None
        if not m or not m["files"]:
            continue
        f = m["files"][0]
        key = "%s|%s" % (m["id"], f.get("name"))
        if key not in cache:
            url = ((f.get("file") or {}).get("url")) or ((f.get("external") or {}).get("url"))
            if not url or got >= MAX_IMAGES:
                continue
            try:
                cache[key] = store.save_image(data_dir, fetch(url))
                got += 1
            except (store.StoreError, NotionError, OSError, ValueError):
                continue
        t["img"] = cache[key]
    return trades


def fetch_bytes(url):
    if not url.startswith("https://"):
        raise NotionError("not an https file")
    with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as r:
        return r.read(store.MAX_IMAGE + 1)


# ---- the hub: pages as plain data ----

def runs(rich):
    out = []
    for r in rich or []:
        a = r.get("annotations") or {}
        x = {"x": str(r.get("plain_text") or "")}
        for k, short in (("bold", "b"), ("italic", "i"), ("underline", "u"), ("strikethrough", "s"), ("code", "c")):
            if a.get(k):
                x[short] = 1
        if a.get("color") and a["color"] != "default":
            x["co"] = a["color"]
        if r.get("href"):
            x["a"] = r["href"]
        out.append(x)
    return out


def icon(b):
    ic = (b.get("callout") or {}).get("icon") or {}
    return ic.get("emoji", "") if ic.get("type") == "emoji" else ""


class Hub:
    """Walks the hub page and its sub-pages. With private=True it does the opposite: it only reads the private page
    (Thoughts + Emotions) and what is under it, for the password-locked vault, and nothing else is copied."""

    def __init__(self, client, data_dir, fetch=fetch_bytes, cache=None, private=False):
        self.c, self.dir, self.fetch, self.cache = client, data_dir, fetch, cache if cache is not None else {}
        self.pages, self.todo, self.blocks, self.images = {}, [], 0, 0
        self.private, self.allowed, self.cur, self.found = private, set(), "", ""

    def run(self, root_id):
        root = root_id.replace("-", "")
        self.todo = [(root, "Trade Journal", 0)]
        while self.todo and len(self.pages) < MAX_PAGES:
            pid, title, depth = self.todo.pop(0)
            if pid in self.pages:
                continue
            if self.private:
                if pid != root and pid not in self.allowed:
                    continue
            elif title.strip().lower() in NEVER_READ:
                continue
            self.cur = pid
            self.pages[pid] = {"title": title, "nodes": self.children(pid, depth)}
        if self.private:
            if not self.found or self.found not in self.pages:
                raise NotionError("The page Thoughts + Emotions was not found under your Trade Journal page, or the Notion connection cannot see it.")
            keep = {k: v for k, v in self.pages.items() if k in self.allowed}
            return {"root": self.found, "pages": keep}
        return {"root": root, "pages": self.pages}

    def children(self, block_id, depth):
        out = []
        for b in self.c.pages("GET", "/v1/blocks/%s/children" % block_id):
            self.blocks += 1
            if self.blocks > MAX_BLOCKS:
                break
            n = self.node(b, depth)
            if n:
                out.append(n)
        return out

    def kids(self, b, depth):
        return self.children(b["id"], depth) if b.get("has_children") else []

    def node(self, b, depth):
        t = b.get("type")
        v = b.get(t) or {}
        if self.private and self.cur not in self.allowed and t != "child_page":
            if t in ("child_database", "image", "file", "pdf"):
                return None                                 # looking for the private page only: copy nothing else
        co = v.get("color") if isinstance(v, dict) and v.get("color") not in (None, "default") else ""
        if t in ("paragraph", "heading_1", "heading_2", "heading_3", "quote", "bulleted_list_item", "numbered_list_item", "toggle"):
            short = {"heading_1": "h1", "heading_2": "h2", "heading_3": "h3", "bulleted_list_item": "li", "numbered_list_item": "ol"}.get(t, t)
            n = {"t": short, "r": runs(v.get("rich_text")), "co": co}
            if b.get("has_children"):
                n["k"] = self.kids(b, depth)
            return n
        if t == "to_do":
            return {"t": "todo", "r": runs(v.get("rich_text")), "done": bool(v.get("checked")), "k": self.kids(b, depth)}
        if t == "callout":
            return {"t": "callout", "r": runs(v.get("rich_text")), "ic": icon(b), "co": co, "k": self.kids(b, depth)}
        if t == "divider":
            return {"t": "hr"}
        if t == "code":
            return {"t": "code", "x": runs_text(v.get("rich_text"))}
        if t == "table":
            rows = [[runs(c) for c in (r.get("table_row") or {}).get("cells") or []] for r in self.c.pages("GET", "/v1/blocks/%s/children" % b["id"])]
            return {"t": "table", "head": bool(v.get("has_column_header")), "rowhead": bool(v.get("has_row_header")), "rows": rows}
        if t == "column_list":
            return {"t": "cols", "cols": [{"k": self.kids(c, depth)} for c in self.c.pages("GET", "/v1/blocks/%s/children" % b["id"])]}
        if t == "synced_block":
            k = self.kids(b, depth)
            return {"t": "group", "k": k} if k else None
        if t == "child_page":
            title = str(v.get("title") or "")
            pid = b["id"].replace("-", "")
            if title.strip().lower() in NEVER_READ:
                if self.private and not self.found:
                    self.found = pid
                    self.allowed.add(pid)
                    self.todo.insert(0, (pid, title, 0))
                return {"t": "page", "title": title, "id": "", "locked": True}
            if self.private:
                if self.cur in self.allowed and depth < 2:
                    self.allowed.add(pid)
                    self.todo.append((pid, title, depth + 1))
                    return {"t": "page", "title": title, "id": pid}
                return None
            if depth < 2:
                self.todo.append((pid, title, depth + 1))
            return {"t": "page", "title": title, "id": pid}
        if t == "child_database":
            return self.database(b["id"], str(v.get("title") or ""))
        if t in ("image", "file", "pdf"):
            return self.file(b, t, v)
        if t in ("bookmark", "embed", "link_preview"):
            return {"t": "link", "url": v.get("url", ""), "r": runs(v.get("caption"))}
        return None

    def file(self, b, t, v):
        src = (v.get("file") or {}).get("url") or (v.get("external") or {}).get("url") or ""
        name = v.get("name") or os.path.basename(src.split("?")[0])
        if self.private:                                    # pictures would be saved as ordinary files, outside the lock
            return {"t": "file", "name": "picture not copied: keeps this page fully locked" if t == "image" else (name or "file"), "r": []}
        if t == "image" and src and self.images < MAX_IMAGES:
            key = "%s|img" % b["id"]
            if key not in self.cache:
                try:
                    self.cache[key] = store.save_image(self.dir, self.fetch(src))
                except (store.StoreError, NotionError, OSError, ValueError):
                    self.cache[key] = ""
            if self.cache[key]:
                self.images += 1
                return {"t": "img", "img": self.cache[key], "r": runs(v.get("caption"))}
        return {"t": "file", "name": name or "file", "r": runs(v.get("caption"))}

    def database(self, db_id, title):
        try:
            schema = self.c.call("GET", "/v1/databases/%s" % db_id)
            rows = self.c.pages("POST", "/v1/databases/%s/query" % db_id, {}, MAX_DB_ROWS)
        except NotionError as e:
            return {"t": "db", "title": title, "cols": [], "rows": [], "error": str(e)}
        names = list((schema.get("properties") or {}).keys())
        names.sort(key=lambda k: 0 if (schema["properties"][k] or {}).get("type") == "title" else 1)
        names = [k for k in names if (schema["properties"][k] or {}).get("type") not in ("relation", "rollup", "people", "files", "created_by", "last_edited_by")][:12]
        table = [[prop_text((r.get("properties") or {}).get(k)) for k in names] for r in rows]
        return {"t": "db", "title": title or runs_text(schema.get("title")), "cols": names, "rows": table, "more": len(rows) >= MAX_DB_ROWS}


def hub(client, data_dir, root_id, fetch=fetch_bytes, cache=None):
    return Hub(client, data_dir, fetch, cache).run(root_id)


def private_hub(client, data_dir, root_id):
    """Thoughts + Emotions and what is under it, read-only, for the password-locked vault. Never written to disk here."""
    return Hub(client, data_dir, private=True).run(root_id)


class Link:
    """Keeps the latest copy of the journal rows and the hub pages in trader/notion, refreshed in the background."""

    JOURNAL_EVERY = 300          # seconds
    HUB_EVERY = 1200

    def __init__(self, data_dir, config_path, token=None, opener=None, fetch=fetch_bytes, pause=0.35):
        import threading
        self.dir = os.path.join(data_dir, "notion")
        self.data_dir, self.config_path = data_dir, config_path
        self.token = read_token(data_dir) if token is None else token
        self.opener, self.fetch, self.pause = opener, fetch, pause
        self.lock = threading.Lock()
        self.state = {"connected": bool(self.token), "at": "", "error": "", "journal_rows": 0, "hub_at": "", "hub_pages": 0}
        self.journal_doc = self._load("journal.json") or {"at": "", "trades": []}
        self.hub_doc = self._load("hub.json") or {"at": "", "root": "", "pages": {}}
        self.state["at"] = self.journal_doc.get("at", "")
        self.state["hub_at"] = self.hub_doc.get("at", "")
        self.state["journal_rows"] = len(self.journal_doc.get("trades") or [])
        self.state["hub_pages"] = len(self.hub_doc.get("pages") or {})
        self._last_hub = 0.0

    def _load(self, name):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _save(self, name, doc):
        os.makedirs(self.dir, exist_ok=True)
        tmp = os.path.join(self.dir, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.dir, name))

    def refresh(self, with_hub=True):
        """Read Notion now. Never raises: a problem is kept in state["error"] and the last good copy stays."""
        if not self.token:
            self.state["error"] = "No Notion secret yet: save it as notion_key.txt in your trader folder."
            return self.state
        with self.lock:
            cfg = settings(self.config_path)
            client = Client(self.token, self.opener, self.pause)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                trades, report, meta = journal(client, cfg["journal_db"])
                cache = self._load("imgmap.json") or {}
                trades = attach_images(self.data_dir, trades, meta, self.fetch, cache)
                self._save("imgmap.json", cache)
                self.journal_doc = {"at": now, "trades": trades, "report": {k: v for k, v in report.items() if k != "table"}}
                self._save("journal.json", self.journal_doc)
                self.state.update(at=now, journal_rows=len(trades), error="")
                if with_hub:
                    h = hub(client, self.data_dir, cfg["hub_page"], self.fetch, cache)
                    self._save("imgmap.json", cache)
                    h["at"] = now
                    self.hub_doc = h
                    self._save("hub.json", h)
                    self.state.update(hub_at=now, hub_pages=len(h["pages"]))
                    self._last_hub = time.time()
            except NotionError as e:
                self.state["error"] = str(e)
            except Exception as e:                                  # never let the background thread die
                self.state["error"] = "%s: %s" % (type(e).__name__, e)
        return self.state

    def start(self):
        import threading

        def loop():
            while True:
                self.refresh(with_hub=time.time() - self._last_hub >= self.HUB_EVERY)
                time.sleep(self.JOURNAL_EVERY)
        threading.Thread(target=loop, daemon=True, name="notion-link").start()
