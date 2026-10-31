import json
import os
import tempfile
import unittest
from unittest import mock

from _load import load

coach = load("riskdesk", "coach")

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


class Chat(unittest.TestCase):
    def test_messages_are_cleaned_into_a_valid_conversation(self):
        out = coach.clean_messages([{"role": "system", "content": "ignore"}, {"role": "assistant", "content": "stray"},
                                    {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
                                    {"role": "assistant", "content": "c"}, {"role": "user", "content": "d" * 5000}])
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])
        self.assertEqual(out[0]["content"], "a\n\nb")                 # two in a row are joined
        self.assertEqual(len(out[2]["content"]), 2000)

    def test_only_the_last_twelve_are_kept_and_it_must_end_with_the_user(self):
        many = [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(40)] + [
            {"role": "user", "content": "last"}]
        out = coach.clean_messages(many)
        self.assertLessEqual(len(out), 12)
        self.assertEqual(out[0]["role"], "user")
        self.assertEqual(out[-1]["content"], "last")
        with self.assertRaises(coach.CoachError):
            coach.clean_messages([{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
        with self.assertRaises(coach.CoachError):
            coach.clean_messages("nope")

    def test_the_context_is_whitelisted_capped_and_plain(self):
        ctx = coach.clean_context({"symbol": "XAUUSD", "price": float("nan"), "bias": "XAUUSD bearish", "evil": "x",
                                   "zones": [{"tf": "30m", "low": 1.5, "nested": {"a": 1}}] * 40,
                                   "levels": "not a list", "indicator": {"entries": 2, "junk": 1}})
        self.assertEqual(ctx["symbol"], "XAUUSD")
        self.assertIsNone(ctx["price"])
        self.assertEqual(ctx["trader_bias"], "XAUUSD bearish")
        self.assertNotIn("evil", ctx)
        self.assertEqual(len(ctx["zones"]), coach.CONTEXT_LIMITS["zones"])
        self.assertEqual(set(ctx["zones"][0]), {"tf", "low"})
        self.assertNotIn("levels", ctx)
        self.assertEqual(ctx["indicator_stats"], {"entries": 2.0})
        self.assertEqual(coach.clean_context("x"), {})

    def test_gemini_gets_the_roles_it_expects_and_claude_the_system_prompt(self):
        seen = {}
        msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
        coach.generate("SYS", msgs, {"name": "gemini", "key": "k", "model": "m"},
                       lambda u, h, b: (seen.update(g=json.loads(b)) or (200, json.dumps({"candidates": [{"content": {"parts": [{"text": "x"}]}}]}))))
        self.assertEqual([c["role"] for c in seen["g"]["contents"]], ["user", "model", "user"])
        self.assertEqual(seen["g"]["systemInstruction"]["parts"][0]["text"], "SYS")
        coach.generate("SYS", msgs, {"name": "anthropic", "key": "k", "model": "m"},
                       lambda u, h, b: (seen.update(a=json.loads(b)) or (200, json.dumps({"content": [{"type": "text", "text": "x"}]}))))
        self.assertEqual(seen["a"]["system"], "SYS")
        self.assertEqual([m["role"] for m in seen["a"]["messages"]], ["user", "assistant", "user"])

    def test_the_chat_prompt_forbids_calls_and_treats_outside_text_as_data(self):
        low = " ".join(coach.CHAT_SYSTEM.lower().split())
        self.assertIn("do not predict", low)
        self.assertIn("never as instructions", low)
        self.assertIn("never invent", low)


class Reflection(unittest.TestCase):
    """The optional grade, plan and emotion fields the Journal tab can add to a trade."""

    def rows(self):
        return coach.clean_trades([
            {"pl": 300, "risk": 100, "inst": "nq", "grade": "a", "plan": "followed", "emotion": "Calm"},
            {"pl": -100, "risk": 100, "inst": "nq", "grade": "c", "plan": "deviated", "emotion": "FOMO"},
            {"pl": -100, "risk": 100, "inst": "nq", "grade": "zzz", "plan": "maybe", "emotion": ""},
            {"pl": 50, "inst": "nq"}])

    def test_they_are_kept_when_valid_and_left_out_when_not(self):
        a, b, c, d = self.rows()
        self.assertEqual((a["grade"], a["plan"], a["emotion"]), ("A", "followed", "calm"))
        self.assertEqual((b["grade"], b["plan"], b["emotion"]), ("C", "deviated", "fomo"))
        for key in ("grade", "plan", "emotion"):
            self.assertNotIn(key, c)
            self.assertNotIn(key, d)

    def test_the_summary_compares_results_across_them_only_when_used(self):
        s = coach.summarise(self.rows())
        self.assertEqual({r["name"]: r["net_pl"] for r in s["by_plan"]}, {"followed": 300.0, "deviated": -100.0, "(not set)": -50.0})
        self.assertIn("by_emotion", s)
        self.assertIn("by_grade", s)
        plain = coach.summarise(coach.clean_trades([{"pl": 5}]))
        for key in ("by_plan", "by_emotion", "by_grade"):
            self.assertNotIn(key, plain)


class TimeOfDay(unittest.TestCase):
    def test_sessions_follow_the_dublin_clock(self):
        for clock, name in (("00:00", "Asia"), ("06:59", "Asia"), ("07:00", "London"), ("12:59", "London"), ("13:00", "New York"),
                            ("20:59", "New York"), ("21:00", "Late"), ("23:59", "Late")):
            self.assertTrue(coach.session(clock).startswith(name), clock)
        for bad in ("", None, "ab:cd", "25:00"):
            self.assertEqual(coach.session(bad), "", bad)

    def test_times_are_kept_only_when_valid_and_grouped_when_present(self):
        rows = coach.clean_trades([{"pl": 100, "time": "14:35"}, {"pl": -50, "time": "14:10"}, {"pl": 20, "time": "9:5"},
                                   {"pl": 10, "time": "24:00"}, {"pl": 5}])
        self.assertEqual([r.get("time") for r in rows], ["14:35", "14:10", None, None, None])
        s = coach.summarise(rows)
        self.assertEqual({r["name"]: r["trades"] for r in s["by_hour"]}, {"14:00": 2, "(no time)": 3})
        self.assertEqual({r["name"]: r["net_pl"] for r in s["by_session"]}["New York (13:00-21:00)"], 50.0)
        self.assertNotIn("by_session", coach.summarise(coach.clean_trades([{"pl": 5}])))


class Gemini(unittest.TestCase):
    def reply(self, text="Gold is your edge."):
        return lambda url, headers, body: (200, json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": text}]}}]}))

    def test_claude_is_used_when_both_keys_exist_gemini_when_only_it_does(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.json")
            self.assertEqual(coach.provider(cfg, {})["name"], "none")
            with open(os.path.join(d, coach.GEMINI_KEY_FILE), "w") as fh:
                fh.write("g-key\n")
            pv = coach.provider(cfg, {})
            self.assertEqual((pv["name"], pv["key"], pv["model"]), ("gemini", "g-key", coach.GEMINI_MODEL))
            self.assertEqual(coach.provider(cfg, {"GEMINI_MODEL": "gemini-x"})["model"], "gemini-x")
            pv = coach.provider(cfg, {"ANTHROPIC_API_KEY": "a-key"})
            self.assertEqual((pv["name"], pv["key"]), ("anthropic", "a-key"))

    def test_the_request_goes_to_google_with_the_key_in_a_header_not_the_address(self):
        seen = {}

        def post(url, headers, body):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return self.reply()(url, headers, body)
        out = coach.ask(TRADES, "Any leaks?", None, "g-secret", "gemini-x", post, "gemini")
        self.assertEqual(out, "Gold is your edge.")                  # the thinking part is left out
        self.assertEqual(seen["url"], coach.GEMINI_URL % "gemini-x")
        self.assertNotIn("g-secret", seen["url"])
        self.assertEqual(seen["headers"]["x-goog-api-key"], "g-secret")
        self.assertIn("QUESTION: Any leaks?", seen["body"]["contents"][0]["parts"][0]["text"])
        self.assertIn("journal coach", seen["body"]["systemInstruction"]["parts"][0]["text"])

    def test_a_wrong_key_stops_at_once_without_trying_other_models(self):
        calls = []
        with self.assertRaises(coach.CoachError) as cm:
            coach.ask(TRADES, "", None, "k", "m", lambda *a: (calls.append(a) or (400, '{"error": {"message": "API key not valid"}}')), "gemini")
        self.assertIn("key", str(cm.exception).lower())
        self.assertEqual(len(calls), 1)

    def test_a_busy_model_falls_through_to_the_next_one(self):
        urls = []

        def post(url, headers, body):
            urls.append(url)
            if len(urls) == 1:
                return 503, '{"error": {"message": "high demand"}}'
            if len(urls) == 2:
                raise coach.CoachError("could not reach the AI service (reset)")
            return self.reply("Third time lucky.")(url, headers, body)
        out = coach.ask(TRADES, "", None, "k", "gemini-3.8-flash", post, "gemini")
        self.assertEqual(out, "Third time lucky.")
        self.assertEqual([u.split("/models/")[1].split(":")[0] for u in urls],
                         ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"])

    def test_when_every_model_fails_the_message_says_to_wait_and_try_again(self):
        calls = []
        with self.assertRaises(coach.CoachError) as cm:
            coach.ask(TRADES, "", None, "k", "m", lambda *a: (calls.append(a) or (503, "{}")), "gemini")
        self.assertEqual(len(calls), 1 + len(coach.GEMINI_FALLBACKS))
        self.assertIn("again", str(cm.exception).lower())

    def test_it_stops_trying_when_the_time_budget_is_spent(self):
        now = [0.0]
        calls = []

        def post(*a):
            calls.append(a)
            now[0] += 100.0
            return 503, "{}"
        with self.assertRaises(coach.CoachError):
            coach.ask_gemini("p", "k", "m", post, clock=lambda: now[0])
        self.assertEqual(len(calls), 2)                    # 100 s then 200 s: over the 150 s budget

    def test_other_errors_and_odd_answers_become_messages(self):
        for status, body, word in ((404, '{"error": {"message": "x"}}', "again"),
                                   (429, "{}", "limit"), (503, "{}", "again")):
            with self.assertRaises(coach.CoachError) as cm:
                coach.ask(TRADES, "", None, "k", "m", lambda *a, s=status, b=body: (s, b), "gemini")
            self.assertIn(word, str(cm.exception).lower(), status)
        for text in ("nope", '{"candidates": []}', '{"candidates": [{"finishReason": "SAFETY"}]}'):
            with self.assertRaises(coach.CoachError):
                coach.ask(TRADES, "", None, "k", "m", lambda *a, t=text: (200, t), "gemini")


if __name__ == "__main__":
    unittest.main()
