import base64
import http.client
import json
import os
import tempfile
import threading
import unittest
import urllib.parse

from _load import load

signin = load("riskdesk", "signin")
dash = load("riskdesk", "dashboard")
NOW = 1_800_000_000


def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def id_token(email="me@gmail.com", aud="cid", iss="https://accounts.google.com", exp=NOW + 3000, verified=True):
    return b64({"alg": "none"}) + "." + b64({"iss": iss, "aud": aud, "exp": exp, "email": email, "email_verified": verified}) + ".sig"


def settings(**kw):
    s = {"public_url": "https://riskdesk.example.org", "emails": ["me@gmail.com"], "client_id": "cid", "client_secret": "sec",
         "secret": b"k" * 32, "secure": True}
    s.update(kw)
    return s


def callback(s, token, state="nonce", cookie_nonce="nonce", now=NOW, code="abc"):
    cookie = signin.sign(s["secret"], {"n": cookie_nonce, "x": now + 600})
    return signin.finish_login(s, {"state": [state], "code": [code]}, cookie, now, lambda url, form: {"id_token": token})


class Cookies(unittest.TestCase):
    def test_a_signed_value_comes_back_and_a_changed_one_does_not(self):
        v = signin.sign(b"k" * 32, {"e": "me@gmail.com", "x": NOW + 10})
        self.assertEqual(signin.unsign(b"k" * 32, v, NOW)["e"], "me@gmail.com")
        body, mac = v.split(".")
        forged = b64({"e": "evil@gmail.com", "x": NOW + 10}) + "." + mac
        self.assertIsNone(signin.unsign(b"k" * 32, forged, NOW))
        self.assertIsNone(signin.unsign(b"other" * 8, v, NOW))                  # another server's secret
        self.assertIsNone(signin.unsign(b"k" * 32, v, NOW + 11))                # expired
        for junk in ("", "x", "a.b.c", None, "....", "e30.e30"):
            self.assertIsNone(signin.unsign(b"k" * 32, junk, NOW))

    def test_cookie_flags(self):
        c = signin.make_cookie("n", "v", True, 60)
        for part in ("HttpOnly", "SameSite=Lax", "Secure", "Max-Age=60", "Path=/"):
            self.assertIn(part, c)
        self.assertNotIn("Secure", signin.make_cookie("n", "v", False, 60))


class Settings(unittest.TestCase):
    def cfg(self, doc, files=None, env=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "config.json")
        with open(path, "w") as fh:
            json.dump(doc, fh)
        for name, text in (files or {}).items():
            with open(os.path.join(tmp.name, name), "w") as fh:
                fh.write(text)
        return path, env or {}

    def test_it_is_off_without_an_auth_section(self):
        path, env = self.cfg({"allowed_hosts": ["x"]})
        self.assertIsNone(signin.load_settings(path, env))
        self.assertIsNone(signin.load_settings(os.path.join(os.path.dirname(path), "missing.json"), env))

    def test_a_half_set_up_auth_section_refuses_instead_of_running_open(self):
        good = {"auth": {"public_url": "https://a.example.org", "emails": ["Me@Gmail.com"]}}
        creds = {"google_oauth.json": json.dumps({"client_id": "cid", "client_secret": "sec"})}
        for doc, files in (({"auth": "yes"}, creds), ({"auth": {"public_url": "http://a.example.org", "emails": ["a@b.c"]}}, creds),
                           ({"auth": {"public_url": "https://a.example.org", "emails": []}}, creds), (good, {})):
            path, env = self.cfg(doc, files)
            with self.assertRaises(signin.AuthError):
                signin.load_settings(path, env)

    def test_a_complete_one_loads_the_secret_is_made_once_and_emails_are_lower_case(self):
        path, env = self.cfg({"auth": {"public_url": "https://a.example.org/", "emails": ["Me@Gmail.com"]}},
                             {"google_oauth.json": json.dumps({"client_id": "cid", "client_secret": "sec"})})
        s = signin.load_settings(path, env)
        self.assertEqual((s["public_url"], s["emails"], s["client_id"], s["secure"]), ("https://a.example.org", ["me@gmail.com"], "cid", True))
        self.assertEqual(signin.load_settings(path, env)["secret"], s["secret"])
        self.assertGreaterEqual(len(s["secret"]), 32)

    def test_the_client_can_come_from_the_environment(self):
        path, env = self.cfg({"auth": {"public_url": "https://a.example.org", "emails": ["a@b.c"]}},
                             env={"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"})
        self.assertEqual(signin.load_settings(path, env)["client_secret"], "y")


class Login(unittest.TestCase):
    def test_the_login_address_asks_google_for_the_email_with_a_state_that_matches_the_cookie(self):
        s = settings()
        url, cookie = signin.login_url(s, NOW)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["client_id"], ["cid"])
        self.assertEqual(q["redirect_uri"], ["https://riskdesk.example.org/auth/callback"])
        self.assertEqual(q["scope"], ["openid email"])
        self.assertEqual(signin.unsign(s["secret"], cookie, NOW)["n"], q["state"][0])

    def test_the_right_person_gets_in(self):
        self.assertEqual(callback(settings(), id_token()), "me@gmail.com")
        self.assertEqual(callback(settings(emails=["me@gmail.com"]), id_token("ME@Gmail.com")), "me@gmail.com")

    def test_everyone_else_is_turned_away(self):
        s = settings()
        for token in (id_token("stranger@gmail.com"), id_token(verified=False), id_token(aud="other"), id_token(iss="https://evil.example"),
                      id_token(exp=NOW - 1), "garbage", ""):
            with self.assertRaises(signin.AuthError):
                callback(s, token)

    def test_a_wrong_expired_or_missing_state_is_refused(self):
        s = settings()
        with self.assertRaises(signin.AuthError):
            callback(s, id_token(), state="other")
        with self.assertRaises(signin.AuthError):
            signin.finish_login(s, {"state": ["nonce"], "code": ["c"]}, "", NOW, lambda *a: {})          # no state cookie
        with self.assertRaises(signin.AuthError):                                               # the state cookie ran out
            signin.finish_login(s, {"state": ["nonce"], "code": ["c"]}, signin.sign(s["secret"], {"n": "nonce", "x": NOW - 1}), NOW, lambda *a: {})
        with self.assertRaises(signin.AuthError):
            signin.finish_login(s, {"error": ["access_denied"]}, "", NOW, lambda *a: {})
        with self.assertRaises(signin.AuthError):
            signin.finish_login(s, {"state": ["nonce"]}, signin.sign(s["secret"], {"n": "nonce", "x": NOW + 600}), NOW, lambda *a: {})

    def test_a_session_ends_when_the_address_leaves_the_list_or_it_expires(self):
        s = settings()
        header = signin.session_cookie(s, "me@gmail.com", NOW).split(";")[0]
        self.assertEqual(signin.current_user(s, header, NOW + 5), "me@gmail.com")
        self.assertIsNone(signin.current_user(settings(emails=["someone@else.com"]), header, NOW + 5))
        self.assertIsNone(signin.current_user(s, header, NOW + signin.SESSION_SECONDS + 1))
        self.assertIsNone(signin.current_user(s, "", NOW))


class HostedServer(unittest.TestCase):
    """The real server with sign-in on and a pretend Google."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = os.path.join(self.tmp.name, "config.json")
        with open(self.config, "w") as fh:
            json.dump({"auth": {"public_url": "http://127.0.0.1:1", "emails": ["me@gmail.com"]}}, fh)
        with open(os.path.join(self.tmp.name, "google_oauth.json"), "w") as fh:
            json.dump({"client_id": "cid", "client_secret": "sec"}, fh)
        self.server = dash.make_server(0, self.config)
        self.server.RequestHandlerClass.oauth_post = staticmethod(lambda url, form: {"id_token": id_token(self.email, exp=int(__import__("time").time()) + 3000)})
        self.email = "me@gmail.com"
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), t.join(5)))

    def req(self, method, path, cookie="", body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Cookie": cookie} if cookie else {}
        if method == "POST":
            h.update({"X-Requested-With": "zones-dashboard", "Content-Type": "application/json"})
            body = json.dumps(body or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read().decode("utf-8")
        c.close()
        return r.status, r, data

    def sign_in(self):
        st, r, _ = self.req("GET", "/auth/login")
        self.assertEqual(st, 302)
        state_cookie = r.getheader("Set-Cookie").split(";")[0]
        state = urllib.parse.parse_qs(urllib.parse.urlparse(r.getheader("Location")).query)["state"][0]
        return self.req("GET", "/auth/callback?code=abc&state=" + state, cookie=state_cookie)

    def test_nothing_is_served_without_signing_in(self):
        st, r, _ = self.req("GET", "/")
        self.assertEqual((st, r.getheader("Location")), (302, "/auth/login"))
        for path in ("/api/journal", "/api/macro", "/api/coach/status", "/api/img/abcdef0123456789.png"):
            self.assertEqual(self.req("GET", path)[0], 401, path)
        self.assertEqual(self.req("POST", "/api/journal", body={"trades": []})[0], 401)
        self.assertEqual(self.req("POST", "/api/chat", body={})[0], 401)

    def test_login_sends_you_to_google(self):
        st, r, _ = self.req("GET", "/auth/login")
        self.assertEqual(st, 302)
        self.assertTrue(r.getheader("Location").startswith("https://accounts.google.com/"))
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))

    def test_the_right_person_signs_in_and_out(self):
        st, r, _ = self.sign_in()
        self.assertEqual((st, r.getheader("Location")), (302, "/"))
        cookies = [v for k, v in r.getheaders() if k == "Set-Cookie"]
        session = [c for c in cookies if c.startswith(signin.SESSION_COOKIE)][0].split(";")[0]
        self.assertEqual(self.req("GET", "/", cookie=session)[0], 200)
        self.assertEqual(self.req("GET", "/api/journal", cookie=session)[0], 200)
        self.assertEqual(self.req("POST", "/api/journal", cookie=session, body={"trades": []})[0], 200)
        st, r, _ = self.req("GET", "/auth/logout", cookie=session)
        self.assertEqual(st, 302)
        self.assertIn("Max-Age=0", r.getheader("Set-Cookie"))

    def test_a_stranger_google_account_is_refused_with_a_page_not_a_session(self):
        self.email = "stranger@gmail.com"
        st, r, body = self.sign_in()
        self.assertEqual(st, 403)
        self.assertIn("not on the list", body)
        self.assertIsNone(r.getheader("Set-Cookie"))

    def test_a_forged_session_cookie_is_refused(self):
        forged = signin.SESSION_COOKIE + "=" + signin.sign(b"guess" * 8, {"e": "me@gmail.com", "x": 9999999999})
        self.assertEqual(self.req("GET", "/api/journal", cookie=forged)[0], 401)

    def test_a_server_with_sign_in_half_set_up_will_not_start(self):
        os.remove(os.path.join(self.tmp.name, "google_oauth.json"))
        with self.assertRaises(dash.signin.AuthError):                      # the dashboard has its own copy of the module
            dash.make_server(0, self.config)


class StaysOffLocally(unittest.TestCase):
    def test_without_an_auth_section_nothing_changes(self):
        with tempfile.TemporaryDirectory() as d:
            server = dash.make_server(0, os.path.join(d, "config.json"))
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                c.request("GET", "/")
                self.assertEqual(c.getresponse().status, 200)
                c.close()
                c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
                c.request("GET", "/auth/login")
                self.assertEqual(c.getresponse().status, 404)
                c.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(5)


class DeployFiles(unittest.TestCase):
    D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "riskdesk", "deploy")

    def read(self, name):
        with open(os.path.join(self.D, name), encoding="utf-8") as fh:
            return fh.read()

    def test_the_server_script_locks_things_down_and_keeps_secrets_out_of_its_text(self):
        s = self.read("setup-server.sh")
        for part in ("set -euo pipefail", "ufw allow OpenSSH", "ufw allow 443/tcp", "read -rsp", "0o600", "ProtectSystem=strict",
                     "reverse_proxy 127.0.0.1:8765", 'cfg["auth"]', "journal-"):
            self.assertIn(part, s)
        self.assertNotRegex(s, r"GOCSPX|AIza|sk-ant|\.apps\.googleusercontent\.com")      # no real secret or client id pasted in

    def test_the_copy_script_does_not_take_a_password(self):
        s = self.read("copy-journal-to-server.ps1")
        self.assertNotIn("password", s.lower().replace("the server password (or uses your ssh key)", ""))


if __name__ == "__main__":
    unittest.main()
