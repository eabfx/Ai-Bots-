"""The password-locked part of Second Brain (Thoughts + Emotions).

What is in it is kept in trader/brain/vault.json, encrypted with a key made from your password. The password itself is
never stored, and without it the file is unreadable noise, so forgetting it means the pages are gone (there is no reset
that keeps them). The file has the same document layout as the rest of Second Brain, so the same editor edits it.

How: the password is stretched with PBKDF2-HMAC-SHA256 (600,000 rounds, random salt) into two keys. The text is
encrypted with a keystream made from HMAC-SHA256 in counter mode and sealed with an HMAC-SHA256 tag (encrypt, then
authenticate), so a wrong password or a changed file is refused rather than read as garbage. Unlocking gives the page a
random token; the key stays in this program's memory only, for 20 minutes after the last use, and goes when you lock it,
leave Second Brain or close RiskDesk. Standard library only.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import brain

ROUNDS = 600000
IDLE_SECONDS = 20 * 60
MIN_PASSWORD = 8
MAX_TRIES, TRY_WINDOW, LOCKOUT = 5, 600, 120
ROOT = "vroot"


class VaultError(Exception):
    pass


class Locked(VaultError):
    """No valid unlock token: the page should ask for the password."""


def _b(raw):
    return base64.b64encode(raw).decode("ascii")


def _u(text):
    return base64.b64decode(text.encode("ascii"))


def derive(password, salt, rounds=ROUNDS):
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=64)
    return raw[:32], raw[32:]


def _stream(enc_key, nonce, length):
    out, counter = bytearray(), 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def seal(keys, plain):
    enc, mac = keys
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(plain, _stream(enc, nonce, len(plain))))
    tag = hmac.new(mac, nonce + ct, hashlib.sha256).digest()
    return nonce, ct, tag


def unseal(keys, nonce, ct, tag):
    enc, mac = keys
    if not hmac.compare_digest(hmac.new(mac, nonce + ct, hashlib.sha256).digest(), tag):
        raise VaultError("wrong password")
    return bytes(a ^ b for a, b in zip(ct, _stream(enc, nonce, len(ct))))


def empty_doc():
    return {"version": brain.VERSION, "rev": 0, "root": ROOT,
            "pages": {ROOT: {"title": "Thoughts + Emotions", "icon": "\U0001F512", "parent": "",
                             "nodes": [{"t": "paragraph", "r": []}]}}, "dbs": {}}


class Vault:
    def __init__(self, data_dir, clock=time.time, rounds=ROUNDS):
        self.dir = os.path.join(data_dir, "brain")
        self.path = os.path.join(self.dir, "vault.json")
        self.clock, self.rounds = clock, rounds
        self.sessions = {}                    # token -> {"keys", "exp"}; memory only
        self.fails = []                       # times of recent wrong passwords

    # ---- the file
    def exists(self):
        return os.path.isfile(self.path)

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            raise VaultError("the locked file could not be read")

    def _write(self, keys, salt, rounds, doc):
        os.makedirs(self.dir, exist_ok=True)
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > brain.MAX_BYTES:
            raise VaultError("the locked pages are bigger than %d MB" % (brain.MAX_BYTES // 1024 // 1024))
        nonce, ct, tag = seal(keys, raw)
        out = {"v": 1, "kdf": "pbkdf2-sha256", "rounds": rounds, "salt": _b(salt), "nonce": _b(nonce), "ct": _b(ct), "tag": _b(tag)}
        bak = os.path.join(self.dir, "vault.bak.json")
        try:
            if os.path.isfile(self.path) and (not os.path.isfile(bak) or self.clock() - os.path.getmtime(bak) > 1800):
                with open(self.path, "rb") as a, open(bak, "wb") as b:
                    b.write(a.read())
        except OSError:
            pass
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh)
        os.replace(tmp, self.path)

    def _open(self, keys, blob):
        plain = unseal(keys, _u(blob["nonce"]), _u(blob["ct"]), _u(blob["tag"]))
        return brain.clean_doc(json.loads(plain.decode("utf-8")))

    # ---- sessions
    def _check(self, token):
        s = self.sessions.get(token or "")
        if not s or s["exp"] <= self.clock():
            self.sessions.pop(token or "", None)
            raise Locked("locked")
        s["exp"] = self.clock() + IDLE_SECONDS
        return s

    def _start(self, keys):
        now = self.clock()
        for t in [t for t, s in self.sessions.items() if s["exp"] <= now]:
            del self.sessions[t]
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"keys": keys, "exp": now + IDLE_SECONDS}
        return token

    def status(self, token=None):
        unlocked = False
        if token:
            try:
                self._check(token)
                unlocked = True
            except Locked:
                pass
        return {"set": self.exists(), "unlocked": unlocked}

    # ---- password
    def setup(self, password):
        if self.exists():
            raise VaultError("a password is already set")
        if len(password or "") < MIN_PASSWORD:
            raise VaultError("use at least %d characters" % MIN_PASSWORD)
        salt = secrets.token_bytes(16)
        keys = derive(password, salt, self.rounds)
        self._write(keys, salt, self.rounds, empty_doc())
        return self._start(keys)

    def unlock(self, password):
        now = self.clock()
        self.fails = [t for t in self.fails if now - t < TRY_WINDOW]
        if len(self.fails) >= MAX_TRIES and now - self.fails[-1] < LOCKOUT:
            raise VaultError("too many wrong passwords. Wait %d seconds and try again" % int(LOCKOUT - (now - self.fails[-1]) + 1))
        if not self.exists():
            raise VaultError("no password is set yet")
        blob = self._read()
        keys = derive(password or "", _u(blob["salt"]), int(blob.get("rounds") or self.rounds))
        try:
            self._open(keys, blob)
        except VaultError:
            self.fails.append(now)
            raise VaultError("wrong password")
        self.fails = []
        return self._start(keys)

    def lock(self, token):
        self.sessions.pop(token or "", None)

    def lock_all(self):
        self.sessions.clear()

    def change_password(self, token, new_password):
        s = self._check(token)
        if len(new_password or "") < MIN_PASSWORD:
            raise VaultError("use at least %d characters" % MIN_PASSWORD)
        doc = self._open(s["keys"], self._read())
        salt = secrets.token_bytes(16)
        keys = derive(new_password, salt, self.rounds)
        self._write(keys, salt, self.rounds, doc)
        self.sessions.clear()
        return self._start(keys)

    # ---- the pages
    def load(self, token):
        s = self._check(token)
        return self._open(s["keys"], self._read())

    def save(self, token, body):
        s = self._check(token)
        blob = self._read()
        have = self._open(s["keys"], blob)
        new = brain.clean_doc(body)
        if int(body.get("rev") or 0) != have["rev"]:
            raise brain.Conflict("the locked pages were changed somewhere else. Reload to see the latest.")
        new["rev"] = have["rev"] + 1
        self._write(s["keys"], _u(blob["salt"]), int(blob.get("rounds") or self.rounds), new)
        return new["rev"]

    def import_hub(self, token, hub):
        """Bring in the pages copied (read-only) from Notion. An untouched vault takes them as its pages; otherwise they
        arrive as a new sub-page, so nothing you wrote is replaced."""
        s = self._check(token)
        blob = self._read()
        doc = self._open(s["keys"], blob)
        pages, dbs = brain.pages_from_hub(hub, ROOT, doc["pages"].keys())
        fresh = (len(doc["pages"]) == 1 and not doc["dbs"] and not any(
            (n.get("r") or n.get("t") != "paragraph") for n in doc["pages"][ROOT]["nodes"]))
        if fresh:
            doc["pages"][ROOT]["nodes"] = pages[ROOT]["nodes"]
            for pid, pg in pages.items():
                if pid != ROOT:
                    doc["pages"][pid] = pg
        else:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.clock()))
            sub = "n" + secrets.token_hex(4)
            doc["pages"][sub] = {"title": "Copy from Notion %s" % stamp, "icon": "", "parent": ROOT, "nodes": pages[ROOT]["nodes"]}
            for pid, pg in pages.items():
                if pid != ROOT:
                    doc["pages"][pid] = pg
                    if pg["parent"] == ROOT:
                        pg["parent"] = sub
            doc["pages"][ROOT]["nodes"].append({"t": "page", "title": doc["pages"][sub]["title"], "id": sub, "ic": ""})
        doc["dbs"].update(dbs)
        doc = brain.clean_doc(doc)
        doc["rev"] = self._open(s["keys"], blob)["rev"] + 1
        self._write(s["keys"], _u(blob["salt"]), int(blob.get("rounds") or self.rounds), doc)
        return doc
