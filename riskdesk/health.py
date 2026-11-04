"""One look at whether Kelly Terminal is healthy: how long the server has been up, recent crashes and restarts, the last backups
(and the second copy), disk space, and whether kellyterminal.com answers. Read only, and standard library only."""
import json
import os
import re
import shutil
import threading
import time
import urllib.request

LOG = "server-restarts.log"
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(.*)$")
HOSTED = "https://kellyterminal.com"
HOSTED_EVERY = 60.0


def read_log(data_dir, now=None, tail=8):
    """The keeper's log: the newest entries, and how many restarts in the last 24 hours."""
    now = now or time.time()
    out, recent = [], 0
    try:
        with open(os.path.join(data_dir, LOG), encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()[-400:]
    except OSError:
        return {"restarts_24h": 0, "events": []}
    for line in lines:
        m = LINE.match(line)
        if not m:
            continue
        try:
            at = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, OverflowError):
            continue
        what = m.group(2).strip()
        kind = "alert" if what.startswith("alert:") else "restart"
        out.append({"at": int(at), "when": m.group(1), "kind": kind, "text": what})
        if kind == "restart" and now - at <= 86400:
            recent += 1
    return {"restarts_24h": recent, "events": out[-tail:][::-1]}


def folder_bytes(path, skip=()):
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) not in skip and d != "__pycache__"]
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


class Health:
    def __init__(self, data_dir, config_path=None, version="", started=None, clock=time.time, fetch=None):
        self.dir, self.config_path, self.version = data_dir, config_path, version
        self.started = started or clock()
        self.clock = clock
        self.fetch = fetch or self._fetch
        self.hosted = {"ok": None, "checked": 0, "version": "", "ms": 0, "error": ""}
        self.busy = False
        self.size = (0, 0)
        self.lock = threading.Lock()

    def _setting(self, key, default):
        try:
            with open(self.config_path, encoding="utf-8-sig") as fh:
                v = json.load(fh).get(key, default)
            return v if isinstance(v, str) else default
        except (OSError, ValueError, AttributeError, TypeError):
            return default

    @staticmethod
    def _fetch(url):
        req = urllib.request.Request(url, headers={"User-Agent": "Kelly Terminal health check"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _refresh_hosted(self, url):
        t0 = time.time()
        try:
            doc = self.fetch(url.rstrip("/") + "/version.json")
            res = {"ok": True, "version": str((doc or {}).get("version") or ""), "error": ""}
        except Exception as e:
            res = {"ok": False, "version": "", "error": "%s" % (str(e) or type(e).__name__)[:120]}
        res.update(checked=self.clock(), ms=int((time.time() - t0) * 1000), url=url)
        with self.lock:
            self.hosted = res
            self.busy = False

    def _hosted(self, hosted_mode):
        url = self._setting("hosted_url", HOSTED).strip()
        if hosted_mode or not url:
            return None                                      # on the hosted copy itself, or switched off: nothing to check
        with self.lock:
            stale = self.clock() - self.hosted["checked"] >= HOSTED_EVERY
            if stale and not self.busy:
                self.busy = True
                threading.Thread(target=self._refresh_hosted, args=(url,), daemon=True, name="health-hosted").start()
            return dict(self.hosted, url=url)

    def _data_size(self, skip):
        at, size = self.size
        if self.clock() - at > 120:
            size = folder_bytes(self.dir, {os.path.abspath(skip)})
            self.size = (self.clock(), size)
        return size

    def state(self, backups, hosted_mode=False):
        now = self.clock()
        bk = backups.state() if backups else {"last": None, "count": 0, "error": "", "copy_to": ""}
        copy = {"folder": bk.get("copy_to") or "", "count": 0, "last": None, "error": ""}
        if copy["folder"]:
            try:
                names = sorted(n for n in os.listdir(copy["folder"]) if re.match(r"^riskdesk-\d{8}-\d{4}\.zip$", n))
                copy["count"] = len(names)
                if names:
                    p = os.path.join(copy["folder"], names[-1])
                    copy["last"] = {"name": names[-1], "at": int(os.path.getmtime(p)), "bytes": os.path.getsize(p)}
            except OSError as e:
                copy["error"] = "%s" % (str(e) or type(e).__name__)[:120]
        try:
            free = shutil.disk_usage(self.dir).free
        except OSError:
            free = None
        folder = backups.folder() if backups else os.path.join(self.dir, "backups")
        return {"version": self.version, "started": int(self.started), "uptime": int(now - self.started), "now": int(now),
                "log": read_log(self.dir, now), "backup": bk, "copy": copy, "free_bytes": free,
                "data_bytes": self._data_size(folder), "hosted": self._hosted(hosted_mode)}
