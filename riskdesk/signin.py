"""Sign in with Google, for a RiskDesk that runs on a server instead of on your own computer.

It is off unless config.json has an "auth" section, so the copy on your PC behaves exactly as before:

    "auth": {"public_url": "https://riskdesk-you.duckdns.org", "emails": ["you@gmail.com"]}

The Google client id and secret are never in config.json or git. They come from the GOOGLE_CLIENT_ID and
GOOGLE_CLIENT_SECRET variables, or from google_oauth.json ({"client_id": "...", "client_secret": "..."}) in the
trader folder. The cookie signing secret comes from SESSION_SECRET or trader/session_secret.txt, which is made
on first use. If the "auth" section is there but anything is missing, RiskDesk refuses to start: it never serves
the journal without a lock because a setting was forgotten.

How it works: /auth/login sends you to Google; Google sends you back to /auth/callback with a one-time code; the
server swaps the code for your identity directly with Google over https (so the identity cannot be forged by
the browser), checks that the address is verified and is on your list, and gives the browser a signed cookie
that lasts 30 days. Every page and every API route needs that cookie. Removing an address from the list
signs that address out at once. Standard library only.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SESSION_COOKIE = "riskdesk_session"
STATE_COOKIE = "riskdesk_state"
SESSION_SECONDS = 30 * 24 * 3600
STATE_SECONDS = 10 * 60
SECRET_FILE = "session_secret.txt"
CLIENT_FILE = "google_oauth.json"


class AuthError(Exception):
    pass


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(secret, payload):
    """A cookie value: the data and a signature over it that only this server can make."""
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    mac = _b64(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return body + "." + mac


def unsign(secret, value, now):
    """The data inside a cookie value, or None when it was altered, is malformed or has expired."""
    try:
        body, mac = str(value).split(".")
        want = _b64(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(mac, want):
            return None
        data = json.loads(_unb64(body).decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("x"), (int, float)) or data["x"] <= now:
            return None
        return data
    except (ValueError, UnicodeError, TypeError):
        return None


def parse_cookies(header):
    out = {}
    for part in str(header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            out[name] = value
    return out


def make_cookie(name, value, secure, max_age):
    bits = ["%s=%s" % (name, value), "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=%d" % max_age]
    if secure:
        bits.append("Secure")
    return "; ".join(bits)


def _read_secret(folder, environ):
    value = (environ.get("SESSION_SECRET") or "").strip()
    if value:
        return value.encode("utf-8")
    path = os.path.join(folder, SECRET_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if len(value) >= 32:
            return value.encode("utf-8")
    except OSError:
        pass
    value = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(value)
    except OSError as e:
        raise AuthError("cannot save %s (%s)" % (path, e))
    return value.encode("utf-8")


def load_settings(config_path, environ=None):
    """None when sign-in is off. Otherwise everything needed, or AuthError saying what is missing."""
    environ = os.environ if environ is None else environ
    try:
        with open(config_path, encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return None
    sec = cfg.get("auth") if isinstance(cfg, dict) else None
    if sec is None or sec is False:
        return None
    if not isinstance(sec, dict):
        raise AuthError('"auth" in config.json must be an object with "public_url" and "emails"')
    folder = os.path.dirname(os.path.abspath(config_path))
    url = str(sec.get("public_url") or "").strip().rstrip("/")
    if not (url.startswith("https://") or url.startswith("http://localhost") or url.startswith("http://127.0.0.1")):
        raise AuthError('"auth.public_url" must be your https address, for example https://riskdesk-you.duckdns.org')
    emails = [e.strip().lower() for e in (sec.get("emails") or []) if isinstance(e, str) and "@" in e]
    if not emails:
        raise AuthError('"auth.emails" must list at least one Google address that may sign in')
    cid, csecret = (environ.get("GOOGLE_CLIENT_ID") or "").strip(), (environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    if not (cid and csecret):
        try:
            with open(os.path.join(folder, CLIENT_FILE), encoding="utf-8-sig") as fh:
                doc = json.load(fh)
            cid, csecret = str(doc.get("client_id") or "").strip(), str(doc.get("client_secret") or "").strip()
        except (OSError, ValueError, AttributeError):
            pass
    if not (cid and csecret):
        raise AuthError("the Google client id and secret are missing: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, "
                        "or save them in %s in the trader folder" % CLIENT_FILE)
    return {"public_url": url, "emails": emails, "client_id": cid, "client_secret": csecret,
            "secret": _read_secret(folder, environ), "secure": url.startswith("https://")}


def login_url(settings, now):
    """Where to send the browser, and the signed value for the state cookie that proves the way back is ours."""
    nonce = secrets.token_urlsafe(24)
    query = urllib.parse.urlencode({
        "client_id": settings["client_id"], "redirect_uri": settings["public_url"] + "/auth/callback",
        "response_type": "code", "scope": "openid email", "state": nonce, "prompt": "select_account"})
    return AUTH_URL + "?" + query, sign(settings["secret"], {"n": nonce, "x": now + STATE_SECONDS})


def http_post(url, form):
    """The one call out to Google. The tests swap this for a fake."""
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        e.close()
        raise AuthError("Google refused the sign-in (HTTP %d)" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise AuthError("could not reach Google (%s)" % getattr(e, "reason", e))


def finish_login(settings, query, state_cookie, now, post=http_post):
    """The verified address of the person who just signed in, or AuthError. `query` is the parsed callback query."""
    def one(name):
        return (query.get(name) or [""])[0]
    if one("error"):
        raise AuthError("Google said: %s" % one("error")[:60])
    state = unsign(settings["secret"], state_cookie, now)
    if not state or not one("state") or not hmac.compare_digest(str(state.get("n")), one("state")):
        raise AuthError("the sign-in took too long or did not start here. Try again")
    if not one("code"):
        raise AuthError("Google sent no sign-in code")
    doc = post(TOKEN_URL, {"code": one("code"), "client_id": settings["client_id"], "client_secret": settings["client_secret"],
                           "redirect_uri": settings["public_url"] + "/auth/callback", "grant_type": "authorization_code"})
    try:
        # this token came straight from Google over https in answer to our own request, so its content can be trusted
        # without checking its signature; still check who it is for and who it names
        claims = json.loads(_unb64(str(doc["id_token"]).split(".")[1]).decode("utf-8"))
        if claims.get("iss") not in ISSUERS or claims.get("aud") != settings["client_id"] or float(claims.get("exp", 0)) <= now:
            raise ValueError
        email = str(claims["email"]).strip().lower()
        verified = claims.get("email_verified") in (True, "true")
    except (KeyError, IndexError, ValueError, TypeError, AttributeError, UnicodeError):
        raise AuthError("Google's answer was not what was expected")
    if not verified:
        raise AuthError("that Google address is not verified")
    if email not in settings["emails"]:
        raise AuthError("%s is not on the list of people who may sign in here" % email)
    return email


def session_cookie(settings, email, now):
    return make_cookie(SESSION_COOKIE, sign(settings["secret"], {"e": email, "x": now + SESSION_SECONDS}),
                       settings["secure"], SESSION_SECONDS)


def current_user(settings, cookie_header, now):
    """The signed-in address from the request's cookies, or None. The address must still be on the list."""
    data = unsign(settings["secret"], parse_cookies(cookie_header).get(SESSION_COOKIE), now)
    email = str(data.get("e") or "").lower() if data else ""
    return email if email in settings["emails"] else None
