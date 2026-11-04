"""Daily safety copy of your Kelly Terminal data (journal, screenshots, notes, settings) so a crash or a bad restart cannot lose it.
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
                    older = sorted(n for n in os.listdir(extra) if NAME.match(n))        # the second folder keeps the newest 60
                    for n in older[:-60]:
                        os.remove(os.path.join(extra, n))
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


# ---------------------------------------------------------------- restoring
KEEP_OUT = ("config.json", "push_topic.txt")          # settings and secrets are never rolled back by a restore
SAFETY = re.compile(r"^before-restore-(\d{8})-(\d{6})\.zip$")
SAFETY_KEEP = 5


class RestoreError(Exception):
    pass


def _secret(name):
    low = name.lower()
    return os.path.basename(low) in KEEP_OUT or "key" in os.path.basename(low) or low.endswith((".pem", ".token", ".secret"))


def _safe(name):
    """A path inside the zip that is a plain relative path with no way out of the data folder."""
    if not name or name.endswith("/") or name.startswith(("/", "\\")) or ":" in name:
        return False
    parts = name.replace("\\", "/").split("/")
    return all(p not in ("", ".", "..") for p in parts) and parts[0] != FOLDER


def _plan(self, name):
    """What restoring this backup would do: the files in it that are new or different from what is on disk."""
    if not NAME.match(str(name)):
        raise RestoreError("that is not one of the backups")
    path = os.path.join(self.folder(), name)
    if not os.path.isfile(path):
        extra = self._copy_to()
        alt = os.path.join(extra, name) if extra else ""
        if alt and os.path.isfile(alt):
            path = alt
        else:
            raise RestoreError("that backup file is not there any more")
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise RestoreError("that backup file is damaged")
    with z:
        if z.testzip() is not None:
            raise RestoreError("that backup file is damaged")
        change, same, skipped = [], 0, 0
        for info in z.infolist():
            n = info.filename
            if info.is_dir():
                continue
            if not _safe(n) or _secret(n) or n.endswith((".part", ".tmp")):
                skipped += 1
                continue
            here = os.path.join(self.dir, *n.split("/"))
            try:
                if os.path.getsize(here) == info.file_size:
                    same += 1
                    continue
            except OSError:
                pass
            change.append((n, info.file_size, os.path.exists(here)))
    return path, change, same, skipped


def preview(self, name):
    path, change, same, skipped = _plan(self, name)
    return {"name": name, "changes": len(change), "same": same, "skipped": skipped,
            "new": sum(1 for c in change if not c[2]), "different": sum(1 for c in change if c[2]),
            "files": [c[0] for c in change[:12]], "bytes": sum(c[1] for c in change)}


def restore(self, name):
    """Put a backup back. First a safety copy of what is there now (before-restore-*.zip), then every changed file is replaced
    one at a time, each written beside its target and swapped in whole so a crash part-way never leaves half a file."""
    with self.lock:
        path, change, same, skipped = _plan(self, name)
        os.makedirs(self.folder(), exist_ok=True)
        at = self.clock()
        while True:                                                  # two restores in the same second must not overwrite each other's safety copy
            safety = os.path.join(self.folder(), "before-restore-%s.zip" % time.strftime("%Y%m%d-%H%M%S", time.localtime(at)))
            if not os.path.exists(safety):
                break
            at += 1
        skip = os.path.abspath(self.folder())
        with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as out:
            for root, dirs, files in os.walk(self.dir):
                dirs[:] = [d for d in dirs if os.path.abspath(os.path.join(root, d)) != skip and d != "__pycache__"]
                for f in files:
                    full = os.path.join(root, f)
                    try:
                        out.write(full, os.path.relpath(full, self.dir))
                    except OSError:
                        continue
        for old in sorted(n for n in os.listdir(self.folder()) if SAFETY.match(n))[:-SAFETY_KEEP]:
            os.remove(os.path.join(self.folder(), old))
        done = 0
        wanted = {c[0] for c in change}
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.filename not in wanted:
                    continue
                target = os.path.join(self.dir, *info.filename.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                tmp = target + ".restoring"
                with z.open(info) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                os.replace(tmp, target)
                done += 1
        return {"restored": done, "unchanged": same, "skipped": skipped, "safety": os.path.basename(safety)}


Backups.preview = preview
Backups.restore = restore
