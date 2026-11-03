"""Keeps a fresh market read: every hour it takes the real prices, calendar and headlines and has the AI assistant write the
brief, then saves it (trader/macro_brief.json) so it is already on Home when the page opens. Nothing is placed or sent anywhere
except the same request the "Write today's brief" button makes. Standard library only."""

import json
import os
import threading
import time

import coach
import macro

EVERY = 3600                    # seconds between briefs
RETRY = 600                     # seconds before trying again after a failure
FILE = "macro_brief.json"
HISTORY = "macro_bias_history.json"      # every brief's biases over time, so changes and "with the desk" can be worked out
KEEP = 600


class Keeper:
    def __init__(self, data_dir, config_path, net, post=coach.api_post, clock=time.time):
        self.dir, self.config_path, self.net, self.post, self.clock = data_dir, config_path, net, post, clock
        self.lock = threading.Lock()
        self.error = ""
        self.tried = 0.0
        self.brief = self._load()
        self.history = self._load_history()

    def _path(self):
        return os.path.join(self.dir, FILE)

    def _load(self):
        try:
            with open(self._path(), encoding="utf-8") as fh:
                doc = json.load(fh)
            if isinstance(doc, dict) and isinstance(doc.get("answer"), str) and isinstance(doc.get("at"), (int, float)):
                parts = macro.split_brief(doc["answer"])          # read it again, so a better reader fixes an older brief
                if parts:
                    doc.update(macro.desk(parts), parts=parts)
                return doc
        except (OSError, ValueError):
            pass
        return None

    def _load_history(self):
        try:
            with open(os.path.join(self.dir, HISTORY), encoding="utf-8") as fh:
                doc = json.load(fh)
            if isinstance(doc, dict) and isinstance(doc.get("history"), list) and isinstance(doc.get("changes"), list):
                return {"history": doc["history"][-KEEP:], "changes": doc["changes"][-KEEP:]}
        except (OSError, ValueError):
            pass
        return {"history": [], "changes": []}

    def _log(self, at, bias):
        """Add this brief's biases to the log and note which markets changed since the last one."""
        now = {k: v["bias"] for k, v in bias.items()}
        if not now:
            return
        last = self.history["history"][-1][1] if self.history["history"] else {}
        for k, v in now.items():
            if k in last and last[k] != v:
                self.history["changes"].append({"market": k, "from": last[k], "to": v, "at": at})
        self.history["history"].append([at, now])
        self.history["history"] = self.history["history"][-KEEP:]
        self.history["changes"] = self.history["changes"][-KEEP:]
        try:
            with open(os.path.join(self.dir, HISTORY + ".tmp"), "w", encoding="utf-8") as fh:
                json.dump(self.history, fh, ensure_ascii=False)
            os.replace(os.path.join(self.dir, HISTORY + ".tmp"), os.path.join(self.dir, HISTORY))
        except (OSError, TypeError, ValueError):
            pass

    def _save(self, doc):
        try:
            os.makedirs(self.dir, exist_ok=True)
            with open(self._path() + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(doc, fh, ensure_ascii=False)
            os.replace(self._path() + ".tmp", self._path())
        except (OSError, TypeError, ValueError):
            pass

    def store(self, answer, as_of, parts, auto=False):
        """Keep a brief that was just written (by the button or by the hourly job) so the next page load has it."""
        at = self.clock()
        doc = dict(macro.desk(parts), answer=answer, as_of=as_of, parts=parts or {}, at=at, auto=bool(auto))
        with self.lock:
            self.brief = doc
            self.error = ""
            self._log(at, doc["bias"])
        self._save(doc)
        return doc

    def due(self):
        have = self.brief["at"] if self.brief else 0
        return self.clock() - have >= EVERY - 60 and self.clock() - self.tried >= (RETRY if self.error else 0)

    def refresh(self):
        """Write a brief now from live data. Never raises: a problem is kept in .error and the last brief stays."""
        self.tried = self.clock()
        try:
            pv = coach.provider(self.config_path)
            if pv["name"] == "none":
                self.error = "No AI key is set, so the market read cannot be written (see Connections)."
                return None
            now = macro.z.parse_now(None)
            snap = macro.snapshot(now, self.net, True)
            answer = coach.generate(macro.BRIEF_SYSTEM, macro.brief_messages(snap, ""), pv, self.post, 3200)
            return self.store(answer, snap["as_of"], macro.split_brief(answer), auto=True)
        except Exception as e:                                  # the background job must never die
            self.error = "%s: %s" % (type(e).__name__, e)
            return None

    def state(self):
        have = self.brief["at"] if self.brief else 0
        if self.error:
            nxt = max(have + EVERY, self.tried + RETRY)
        elif have:
            nxt = have + EVERY
        else:
            nxt = 0                                   # the first one is being written right now
        return {"brief": self.brief, "error": self.error, "every": EVERY, "next_at": nxt,
                "changes": self.history["changes"][-8:], "history": self.history["history"][-300:]}

    def start(self):
        def loop():
            time.sleep(20)
            while True:
                if self.due():
                    self.refresh()
                time.sleep(30)
        threading.Thread(target=loop, daemon=True, name="hourly-brief").start()
