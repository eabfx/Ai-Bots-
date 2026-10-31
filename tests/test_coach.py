import json
import os
import tempfile
import unittest
from unittest import mock

from _load import load

coach = load("trader-zones", "coach")

TRADES = [
    {"date": "2026-10-26", "inst": "xauusd", "dir": "short", "tag": "15m supply", "risk": 100, "pl": 300, "notes": "fib held"},
    {"date": "2026-10-27", "inst": "EURUSD", "dir": "long", "tag": "30m demand", "risk": 100, "pl": -100, "notes": ""},
    {"date": "2026-10-28", "inst": "EURUSD", "dir": "long", "tag": "", "risk": 0, "pl": -50, "notes": "no stop set"},
]


class Clean(unittest.TestCase):
    def test_rows_are_cleaned_and_r_is_worked_out(self):
        out = coach.clean_trades(TRADES + [{"pl": "x"}, "junk", {"pl": float("nan")}])
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["inst"], "XAUUSD")
        self.assertEqual(out[0]["r"], 3.0)
        self.assertEqual(out[1]["r"], -1.0)
        self.assertIsNone(out[2]["r"])                      # no risk logged: R cannot be known

    def test_a_journal_is_capped_and_must_be_a_list(self):
        many = [{"pl": 1, "risk": 1}] * 500
        self.assertEqual(len(coach.clean_trades(many)), coach.MAX_TRADES)
        with self.assertRaises(coach.CoachError):
            coach.clean_trades({"pl": 1})


class Summary(unittest.TestCase):
    def test_the_numbers_are_worked_out_by_code(self):
        s = coach.summarise(coach.clean_trades(TRADES))
        self.assertEqual((s["trades"], s["wins"], s["losses"]), (3, 1, 2))
        self.assertEqual(s["win_rate_pct"], 33.3)
        self.assertEqual(s["net_pl"], 150.0)
        self.assertEqual(s["net_r"], 2.0)
        self.assertEqual((s["trades_with_risk"], s["trades_without_risk"]), (2, 1))
        eur = next(r for r in s["by_instrument"] if r["name"] == "EURUSD")
        self.assertEqual((eur["trades"], eur["net_pl"]), (2, -150.0))
        self.assertEqual({r["name"] for r in s["by_weekday"]}, {"Monday", "Tuesday", "Wednesday"})

    def test_an_empty_journal_has_no_rates(self):
        s = coach.summarise([])
        self.assertEqual(s["trades"], 0)
        self.assertIsNone(s["win_rate_pct"])

    def test_the_indicator_stats_are_kept_to_known_fields(self):
        out = coach.clean_indicator({"symbol": "XAUUSD", "entries": 4, "win_rate_pct": 50, "evil": "ignore this", "net_r": "x"})
        self.assertEqual(out, {"symbol": "XAUUSD", "entries": 4.0, "win_rate_pct": 50.0})
        self.assertIsNone(coach.clean_indicator("nope"))


class Key(unittest.TestCase):
    def test_the_environment_wins_then_the_file_next_to_the_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.json")
            self.assertEqual(coach.load_key(cfg, {}), "")
            with open(os.path.join(d, coach.KEY_FILE), "w") as fh:
                fh.write("  sk-file \n")
            self.assertEqual(coach.load_key(cfg, {}), "sk-file")
            self.assertEqual(coach.load_key(cfg, {"ANTHROPIC_API_KEY": "sk-env"}), "sk-env")

    def test_the_model_can_be_changed(self):
        self.assertEqual(coach.model_name({}), coach.DEFAULT_MODEL)
        self.assertEqual(coach.model_name({"RISKDESK_MODEL": "other"}), "other")


class Ask(unittest.TestCase):
    def reply(self, text="Your edge is gold."):
        return lambda url, headers, body: (200, json.dumps({"content": [{"type": "text", "text": text}]}))

    def test_a_missing_key_is_a_plain_message_and_nothing_is_sent(self):
        sent = []
        with self.assertRaises(coach.CoachError) as cm:
            coach.ask(TRADES, "", None, "", "m", lambda *a: sent.append(a))
        self.assertIn(coach.KEY_FILE, str(cm.exception))
        self.assertEqual(sent, [])

    def test_an_empty_journal_is_refused_before_anything_is_sent(self):
        with self.assertRaises(coach.CoachError):
            coach.ask([], "", None, "k", "m", lambda *a: self.fail("sent"))

    def test_the_request_carries_the_key_the_summary_the_trades_and_the_question(self):
        seen = {}

        def post(url, headers, body):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return 200, json.dumps({"content": [{"type": "text", "text": "ok"}]})
        out = coach.ask(TRADES, "Stop trading EURUSD?", {"symbol": "XAUUSD", "entries": 3}, "sk-test", "claude-x", post)
        self.assertEqual(out, "ok")
        self.assertEqual(seen["url"], coach.API_URL)
        self.assertEqual(seen["headers"]["x-api-key"], "sk-test")
        self.assertEqual(seen["body"]["model"], "claude-x")
        prompt = seen["body"]["messages"][0]["content"]
        self.assertIn('"win_rate_pct": 33.3', prompt)
        self.assertIn("INDICATOR STATS", prompt)
        self.assertIn("no stop set", prompt)
        self.assertIn("QUESTION: Stop trading EURUSD?", prompt)
        self.assertNotIn("sk-test", prompt)                 # the key goes in the header and nowhere else
        self.assertIn("never place", seen["body"]["system"].lower().replace("never places", "never place"))

    def test_without_a_question_it_asks_for_a_general_review(self):
        seen = {}
        coach.ask(TRADES, "  ", None, "k", "m", lambda u, h, b: (seen.update(b=json.loads(b)) or self.reply()(u, h, b)))
        self.assertIn("Review my journal", seen["b"]["messages"][0]["content"])

    def test_api_errors_become_messages_that_say_what_to_do(self):
        for status, word in ((401, "key"), (404, "model"), (429, "credit"), (500, "again")):
            with self.assertRaises(coach.CoachError) as cm:
                coach.ask(TRADES, "", None, "k", "m", lambda *a, s=status: (s, '{"error": {"message": "x"}}'))
            self.assertIn(word, str(cm.exception).lower(), status)

    def test_an_answer_in_the_wrong_shape_or_empty_is_an_error(self):
        for text in ("not json", '{"content": []}', '{"content": [{"type": "text", "text": "  "}]}'):
            with self.assertRaises(coach.CoachError):
                coach.ask(TRADES, "", None, "k", "m", lambda *a, t=text: (200, t))

    def test_a_note_cannot_slip_in_past_its_length_cap(self):
        long = dict(TRADES[0], notes="x" * 5000)
        self.assertEqual(len(coach.clean_trades([long])[0]["notes"]), 300)
        self.assertIn("ignore it", coach.SYSTEM.lower().replace("ignore them", "ignore it"))


if __name__ == "__main__":
    unittest.main()
