import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "riskdesk"))
sys.path.insert(0, os.path.join(HERE, "..", "trader-zones"))
sys.path.insert(0, HERE)
from _load import load  # noqa: E402
import test_notion_api as tn  # noqa: E402

vault = load("riskdesk", "vault")
brain = vault.brain
api = tn.api
SERVER = open(os.path.join(HERE, "..", "riskdesk", "dashboard.py"), encoding="utf-8").read()
PAGE = open(os.path.join(HERE, "..", "riskdesk", "dashboard.html"), encoding="utf-8").read()
JS = open(os.path.join(HERE, "..", "riskdesk", "assets", "brain.js"), encoding="utf-8").read()
SECRET = "felt anxious before the open"
PRIVATE_SUB = "9" * 32


class Fake(tn.FakeNotion):
    """Adds what is inside the private page. The base fake raises if anything unexpected is read."""
    def __call__(self, req, timeout=0):
        p = req.full_url.replace(api.API, "").split("?")[0]
        if p == "/v1/blocks/%s/children" % tn.PRIVATE:
            self.log.append((req.get_method(), p))
            return self._ok({"results": [
                {"id": "q1", "type": "paragraph", "has_children": False, "paragraph": {"rich_text": tn.rt(SECRET)}},
                {"id": "q2", "type": "image", "has_children": False, "image": {"type": "file", "file": {"url": "https://files.example/p.png"}, "caption": []}},
                {"id": PRIVATE_SUB, "type": "child_page", "has_children": True, "child_page": {"title": "Weekly mood"}}], "has_more": False})
        if p == "/v1/blocks/%s/children" % PRIVATE_SUB:
            self.log.append((req.get_method(), p))
            return self._ok({"results": [{"id": "q3", "type": "to_do", "has_children": False, "to_do": {"rich_text": tn.rt("breathe"), "checked": False}}], "has_more": False})
        return super().__call__(req, timeout)

    @staticmethod
    def _ok(data):
        raw = json.dumps(data).encode()
        return type("R", (), {"read": lambda s: raw, "__enter__": lambda s: s, "__exit__": lambda s, *a: None})()


def fresh(rounds=1000):
    d = tempfile.mkdtemp()
    now = [1000.0]
    return d, vault.Vault(d, clock=lambda: now[0], rounds=rounds), now


class Crypto(unittest.TestCase):
    def test_sealed_text_opens_only_with_its_key_and_is_not_readable_in_the_file(self):
        d, v, _ = fresh()
        tok = v.setup("correct horse 9")
        doc = v.load(tok)
        doc["pages"][vault.ROOT]["nodes"] = [{"t": "paragraph", "r": [{"x": SECRET}]}]
        v.save(tok, doc)
        raw = open(v.path, encoding="utf-8").read()
        self.assertNotIn(SECRET, raw)
        self.assertNotIn("anxious", raw)
        self.assertNotIn("correct horse", raw)
        again = vault.Vault(d, rounds=1000)                                    # a fresh start: nothing is unlocked
        tok2 = again.unlock("correct horse 9")
        self.assertEqual(again.load(tok2)["pages"][vault.ROOT]["nodes"][0]["r"][0]["x"], SECRET)

    def test_a_changed_file_is_refused(self):
        d, v, _ = fresh()
        v.setup("correct horse 9")
        blob = json.load(open(v.path, encoding="utf-8"))
        ct = bytearray(vault._u(blob["ct"]))
        ct[3] ^= 1
        blob["ct"] = vault._b(bytes(ct))
        json.dump(blob, open(v.path, "w"))
        with self.assertRaises(vault.VaultError):
            vault.Vault(d, rounds=1000).unlock("correct horse 9")


class Passwords(unittest.TestCase):
    def test_short_wrong_and_repeated_passwords(self):
        d, v, now = fresh()
        with self.assertRaises(vault.VaultError):
            v.setup("short")
        v.setup("correct horse 9")
        with self.assertRaises(vault.VaultError):
            v.setup("another password")                                       # cannot be set twice
        for _ in range(5):
            with self.assertRaises(vault.VaultError) as cm:
                v.unlock("nope nope nope")
        self.assertIn("wrong", str(cm.exception))
        with self.assertRaises(vault.VaultError) as cm:
            v.unlock("correct horse 9")                                       # even the right one waits after five misses
        self.assertIn("Wait", str(cm.exception))
        now[0] += vault.LOCKOUT + 1
        self.assertTrue(v.unlock("correct horse 9"))

    def test_changing_the_password_keeps_the_pages_and_retires_the_old_one(self):
        d, v, _ = fresh()
        tok = v.setup("correct horse 9")
        doc = v.load(tok)
        doc["pages"][vault.ROOT]["nodes"] = [{"t": "paragraph", "r": [{"x": SECRET}]}]
        v.save(tok, doc)
        tok = v.change_password(tok, "brand new pass 1")
        with self.assertRaises(vault.VaultError):
            v.unlock("correct horse 9")
        t2 = v.unlock("brand new pass 1")
        self.assertEqual(v.load(t2)["pages"][vault.ROOT]["nodes"][0]["r"][0]["x"], SECRET)


class Sessions(unittest.TestCase):
    def test_no_token_or_an_old_one_is_locked_out(self):
        d, v, now = fresh()
        tok = v.setup("correct horse 9")
        for bad in (None, "", "guess"):
            with self.assertRaises(vault.Locked):
                v.load(bad)
        now[0] += vault.IDLE_SECONDS - 5
        v.load(tok)                                                              # use keeps it open
        now[0] += vault.IDLE_SECONDS - 5
        v.load(tok)
        now[0] += vault.IDLE_SECONDS + 5
        with self.assertRaises(vault.Locked):
            v.load(tok)

    def test_locking_forgets_the_key(self):
        d, v, _ = fresh()
        tok = v.setup("correct horse 9")
        v.lock(tok)
        with self.assertRaises(vault.Locked):
            v.load(tok)
        self.assertEqual(v.status(tok), {"set": True, "unlocked": False})

    def test_an_old_tab_cannot_overwrite_newer_work(self):
        d, v, _ = fresh()
        tok = v.setup("correct horse 9")
        doc = v.load(tok)
        v.save(tok, dict(doc))
        with self.assertRaises(brain.Conflict):
            v.save(tok, dict(doc))


class Copy(unittest.TestCase):
    def test_only_the_private_page_is_read_and_pictures_are_not_saved_outside_the_lock(self):
        d, fake = tempfile.mkdtemp(), Fake()
        h = api.private_hub(api.Client("tok", fake, pause=0), d, tn.HUB)
        self.assertEqual(h["root"], tn.PRIVATE)
        self.assertEqual(set(h["pages"]), {tn.PRIVATE, PRIVATE_SUB})
        paths = [p for _, p in fake.log]
        self.assertNotIn("/v1/blocks/%s/children" % tn.SUB, paths)                # no other page was walked
        self.assertFalse([p for p in paths if "databases" in p])                     # no database was queried
        self.assertTrue(all(m == "GET" for m, _ in fake.log))                         # read only
        self.assertEqual(h["pages"][tn.PRIVATE]["nodes"][0]["r"][0]["x"], SECRET)
        self.assertEqual(h["pages"][tn.PRIVATE]["nodes"][1]["t"], "file")
        self.assertFalse(os.path.isdir(os.path.join(d, "journal", "images")))        # nothing written next to it, unlocked
        self.assertEqual(h["pages"][PRIVATE_SUB]["nodes"][0]["t"], "todo")

    def test_the_normal_copy_still_never_reads_it(self):
        fake = Fake()
        api.hub(api.Client("tok", fake, pause=0), tempfile.mkdtemp(), tn.HUB, tn.fetch, {})
        self.assertNotIn("/v1/blocks/%s/children" % tn.PRIVATE, [p for _, p in fake.log])

    def test_it_lands_in_the_vault_as_pages_and_arrives_beside_anything_already_written(self):
        d, v, _ = fresh()
        tok = v.setup("correct horse 9")
        h = api.private_hub(api.Client("tok", Fake(), pause=0), d, tn.HUB)
        doc = v.import_hub(tok, h)
        self.assertEqual(doc["pages"][vault.ROOT]["nodes"][0]["r"][0]["x"], SECRET)
        sub = [p for k, p in doc["pages"].items() if p["title"] == "Weekly mood"]
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub[0]["parent"], vault.ROOT)
        doc["pages"][vault.ROOT]["nodes"].append({"t": "paragraph", "r": [{"x": "mine"}]})
        v.save(tok, doc)
        again = v.import_hub(tok, h)                                                 # a second copy never replaces my writing
        texts = json.dumps(again)
        self.assertIn("mine", texts)
        self.assertEqual(sum(1 for p in again["pages"].values() if p["title"].startswith("Copy from Notion")), 1)


class Wiring(unittest.TestCase):
    def test_server_routes_and_page(self):
        for needle in ("/api/vault/", "X-Vault-Token", "vault.Vault(", "private_hub(", "_check(token)", "vault.Locked"):
            self.assertIn(needle, SERVER)
        self.assertIn("SecondBrain.leave()", PAGE)
        for needle in ("openVault", "/api/vault/unlock", "/api/vault/setup", "X-Vault-Token", "Lock and leave", "lockedOut"):
            self.assertIn(needle, JS)

    def test_the_password_is_never_stored_in_the_page_or_the_server_state(self):
        self.assertNotIn("localStorage", JS)
        self.assertNotIn("sessionStorage", JS)


if __name__ == "__main__":
    unittest.main()
