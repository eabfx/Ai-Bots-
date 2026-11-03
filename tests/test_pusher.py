import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from _load import load

dash = load("riskdesk", "dashboard")     # loaded first: it puts the zones folder on the import path
pusher = load("riskdesk", "pusher")

NOW = 1793095200          # a Tuesday morning, UTC


class Watch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sent = []
        self.p = pusher.Pusher(self.tmp.name, None, post=lambda url, text, headers: self.sent.append((url, text, headers)))

    def turn_on(self):
        self.p.enable()
        return self.p.topic()

    def test_nothing_is_sent_until_the_topic_exists(self):
        self.p.set_rules({"price": [{"sym": "XAUUSD", "dir": "above", "level": 3900}]})
        self.assertEqual(self.p.check(NOW, quote=lambda k: 4000, calendar=lambda t: []), 0)
        self.assertEqual(self.sent, [])

    def test_the_topic_is_long_random_and_deleted_when_turned_off(self):
        a = self.turn_on()
        self.assertTrue(a.startswith("kelly-") and len(a) >= 20)
        self.assertEqual(self.p.enable()["topic"], a)           # turning it on twice keeps the same topic
        self.p.disable()
        self.assertEqual(self.p.topic(), "")
        self.assertNotEqual(self.turn_on(), a)

    def test_a_price_alert_fires_once_and_stays_fired(self):
        topic = self.turn_on()
        self.p.set_rules({"price": [{"sym": "XAUUSD", "dir": "above", "level": 3900},
                                    {"sym": "NQ", "dir": "below", "level": 100}]})
        n = self.p.check(NOW, quote=lambda k: 3950 if k == "XAUUSD" else 21000, calendar=lambda t: [])
        self.assertEqual(n, 1)
        self.assertEqual(self.sent[0][0], "https://ntfy.sh/" + topic)
        self.assertIn("3950", self.sent[0][1])
        self.assertEqual(self.p.check(NOW + 60, quote=lambda k: 3950, calendar=lambda t: []), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.p.state()["rules"]["price"][0]["done"])
        # saving the rules again from the page must not re-arm what already fired
        self.p.set_rules({"price": [{"sym": "XAUUSD", "dir": "above", "level": 3900}]})
        self.assertTrue(self.p.state()["rules"]["price"][0]["done"])

    def test_a_price_that_cannot_be_read_does_not_stop_the_other_checks(self):
        self.turn_on()
        self.p.set_rules({"price": [{"sym": "XAUUSD", "dir": "above", "level": 3900}]})

        def boom(k):
            raise RuntimeError("yahoo is down")
        self.assertEqual(self.p.check(NOW, quote=boom, calendar=lambda t: []), 0)
        self.assertIn("Could not read XAUUSD", self.p.state()["error"])

    def test_high_impact_news_is_sent_once_inside_the_warning_window(self):
        self.turn_on()
        ev = [{"t": NOW + 10 * 60, "impact": "High", "title": "CPI", "country": "USD", "forecast": "0.3%"},
              {"t": NOW + 12 * 60, "impact": "Medium", "title": "Other", "country": "USD"},
              {"t": NOW + 90 * 60, "impact": "High", "title": "Later", "country": "EUR"}]
        self.p.set_rules({"news": True, "newsMins": 15, "sess": False})
        self.assertEqual(self.p.check(NOW, quote=None, calendar=lambda t: ev), 1)
        self.assertIn("CPI", self.sent[0][1])
        self.assertEqual(self.p.check(NOW + 30, calendar=lambda t: ev), 0)

    def test_news_can_be_switched_off(self):
        self.turn_on()
        self.p.set_rules({"news": False, "sess": False})
        ev = [{"t": NOW + 5 * 60, "impact": "High", "title": "CPI", "country": "USD"}]
        self.assertEqual(self.p.check(NOW, calendar=lambda t: ev), 0)

    def test_rules_from_the_page_are_cleaned(self):
        out = self.p.set_rules({"price": [{"sym": "XAUUSD", "dir": "above", "level": 3900},
                                          {"sym": "<script>", "dir": "above", "level": 1},
                                          {"sym": "NQ", "dir": "sideways", "level": 1},
                                          {"sym": "NQ", "dir": "below", "level": "5"},
                                          {"sym": "NQ", "dir": "below", "level": True}] + [{"sym": "VIX", "dir": "above", "level": i} for i in range(80)],
                                "newsMins": 5000, "extra": "x"})["rules"]
        self.assertEqual(out["price"][0]["sym"], "XAUUSD")
        self.assertEqual(len(out["price"]), pusher.MAX_PRICE)
        self.assertEqual(out["newsMins"], 15)               # out of range, so the old value stays
        self.assertNotIn("extra", out)

    def test_rules_survive_a_restart(self):
        self.p.set_rules({"price": [{"sym": "DXY", "dir": "below", "level": 99.5}], "news": False})
        again = pusher.Pusher(self.tmp.name, None, post=lambda *a: None)
        self.assertEqual(again.state()["rules"]["price"][0]["level"], 99.5)
        self.assertFalse(again.state()["rules"]["news"])

    def test_a_failed_send_is_reported_and_retried_next_round(self):
        self.turn_on()
        self.p.set_rules({"price": [{"sym": "XAUUSD", "dir": "above", "level": 3900}], "news": False, "sess": False})

        def down(url, text, headers):
            raise OSError("offline")
        self.p.post = down
        self.assertEqual(self.p.check(NOW, quote=lambda k: 4000), 0)
        self.assertIn("Could not reach ntfy", self.p.state()["error"])
        self.assertFalse(self.p.state()["rules"]["price"][0]["done"])       # still armed
        self.p.post = lambda url, text, headers: self.sent.append(text)
        self.assertEqual(self.p.check(NOW + 60, quote=lambda k: 4000), 1)

    def test_titles_are_plain_ascii_because_http_headers_must_be(self):
        self.turn_on()
        self.p.send("Gold ↑ above 3900", "x")
        self.assertTrue(self.sent[0][2]["Title"].isascii())


class Route(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = dash.make_server(0, os.path.join(self.tmp.name, "config.json"))
        self.sent = []
        self.server.RequestHandlerClass.pusher.post = lambda url, text, headers: self.sent.append(url)
        self.port = self.server.server_address[1]
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), t.join(5)))

    def call(self, path, body=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Requested-With": "zones-dashboard"},
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_turn_on_test_and_off_from_the_page(self):
        self.assertFalse(self.call("/api/push/status")[1]["enabled"])
        code, s = self.call("/api/push", {"action": "enable"})
        self.assertTrue(s["enabled"] and s["topic"].startswith("kelly-"))
        code, s = self.call("/api/push", {"action": "test"})
        self.assertTrue(s["ok"])
        self.assertEqual(self.sent, ["https://ntfy.sh/" + s["topic"]])
        self.assertFalse(self.call("/api/push", {"action": "disable"})[1]["enabled"])

    def test_an_unknown_action_is_a_400(self):
        self.assertEqual(self.call("/api/push", {"action": "nuke"})[0], 400)

    def test_the_post_needs_the_header_like_every_other_write(self):
        req = urllib.request.Request("http://127.0.0.1:%d/api/push" % self.port, data=b'{"action":"enable"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 403)

    def test_the_alerts_page_explains_the_phone_setup(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port, timeout=10) as r:
            page = r.read().decode()
        self.assertIn('id="push-on"', page)
        self.assertIn("ntfy", page)


if __name__ == "__main__":
    unittest.main()
