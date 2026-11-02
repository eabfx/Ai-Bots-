"""Daily safety copy of your RiskDesk data (journal, screenshots, notes, settings) so a crash or a bad restart cannot lose it.
It only ever adds a zip file to trader/backups and removes its own older zips (it keeps the newest 14). It never touches the
originals. Add "backup_copy_to": "D:/Backups" (any folder, e.g. a OneDrive folder) to the config to get a second copy there.
Standard library only."""

import json
import os
import re
import shutil
import threading
import time
import zipfile

FOLDER = "backups"
KEEP = 14
EVERY = 20 * 3600               # a new copy once the newest is older than this
CHECK = 1800                    # how often the thread looks, in seconds
NAME = re.compile(r"^riskdesk-(\d{8})-(\d{4})\.zip$")


class Backups:
    def __init__(self, data_dir, config_path=None, keep=KEEP, clock=time.time):
        self.dir, self.config_path, self.keep, self.clock = data_dir, config_path, keep, clock
        self.lock = threading.Lock()
        self.error = ""

    def folder(self):
        return os.path.join(self.dir, FOLDER)

    def listing(self):
        try:
            names = sorted(n for n in os.listdir(self.folder()) if NAME.match(n))
        except OSError:
            return []
        out = []
        for n in names:
            p = os.path.join(self.folder(), n)
            try:
                out.append({"name": n, "at": int(os.path.getmtime(p)), "bytes": os.path.getsize(p)})
            except OSError:
                pass
        return out

    def due(self):
        files = self.listing()
        return not files or self.clock() - files[-1]["at"] >= EVERY

    def _copy_to(self):
        try:
            with open(self.config_path, encoding="utf-8") as fh:
                where = json.load(fh).get("backup_copy_to")
        except (OSError, ValueError, TypeError, AttributeError):
            return ""
        return where.strip() if isinstance(where, str) and where.strip() else ""

    def run(self):
        """Make one backup now. Returns its file name, or "" when it failed (the reason is in self.error). Never raises."""
        with self.lock:
            try:
                os.makedirs(self.folder(), exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M", time.localtime(self.clock()))
                final = os.path.join(self.folder(), "riskdesk-%s.zip" % stamp)
                tmp = final + ".part"
                skip = os.path.abspath(self.folder())
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                    for root, dirs, files in os.walk(self.dir):
                        dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) != skip and d != "__pycache__"]
                        for f in files:
                            if f.endswith(".part") or f.endswith(".tmp"):
                                continue
                            full = os.path.join(root, f)
                            try:
                                z.write(full, os.path.relpath(full, self.dir))
                            except OSError:
                                continue            # a file that is being written right now: the next copy gets it
                with zipfile.ZipFile(tmp) as z:
                    if z.testzip() is not None:
                        raise OSError("the new backup did not check out")
                os.replace(tmp, final)
                os.utime(final, (self.clock(), self.clock()))
                extra = self._copy_to()
                if extra:
                    os.makedirs(extra, exist_ok=True)
                    shutil.copy2(final, os.path.join(extra, os.path.basename(final)))
                for old in self.listing()[:-self.keep]:
                    os.remove(os.path.join(self.folder(), old["name"]))
                self.error = ""
                return os.path.basename(final)
            except Exception as e:
                self.error = "%s: %s" % (type(e).__name__, e)
                try:
                    os.remove(tmp)
                except (OSError, UnboundLocalError):
                    pass
                return ""

    def state(self):
        files = self.listing()
        return {"last": files[-1] if files else None, "count": len(files), "error": self.error, "folder": self.folder(),
                "copy_to": self._copy_to()}

    def start(self):
        def loop():
            time.sleep(60)
            while True:
                if self.due():
                    self.run()
                time.sleep(CHECK)
        threading.Thread(target=loop, name="daily-backup", daemon=True).start()
