"""Run with:  python -m unittest -v   (from the jarvis folder)"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_PROVIDER", "offline")
os.environ.setdefault("JARVIS_TTS", "false")

from jarvis import actions, brain  # noqa: E402
from jarvis.actions import SafetyGate, is_forbidden  # noqa: E402
from jarvis.brain import RuleParser  # noqa: E402


class RuleParserTests(unittest.TestCase):
    def setUp(self):
        self.p = RuleParser()

    def t(self, utterance, action, **params):
        prop = self.p.parse(utterance)
        self.assertIsNotNone(prop, utterance)
        self.assertEqual(prop.action, action, utterance)
        for k, v in params.items():
            self.assertEqual(prop.params.get(k), v, utterance)

    def test_open_apps(self):
        self.t("open chrome", "open_app", app="chrome")
        self.t("please launch vs code", "open_app", app="vscode")
        self.t("Start the calculator", "open_app", app="calculator")
        self.t("open file explorer", "open_app", app="explorer")

    def test_close_app(self):
        self.t("close chrome", "close_app", app="chrome")

    def test_search_variants(self):
        self.t("search best laptops under 50000", "web_search", query="best laptops under 50000")
        self.t("google python tutorials", "web_search", query="python tutorials")
        self.t("play lo-fi beats on youtube", "youtube", query="lo-fi beats")
        self.t("where is mumbai airport", "maps", query="mumbai airport")
        self.t("wiki alan turing", "wikipedia", query="alan turing")

    def test_urls(self):
        self.t("open github.com", "open_url", url="github.com")
        self.t("go to https://docs.python.org/3/", "open_url", url="https://docs.python.org/3/")

    def test_misc(self):
        self.t("what time is it", "time")
        self.t("what's the date today", "date")
        self.t("battery level", "system_status")
        self.t("note buy milk tomorrow", "take_note", text="buy milk tomorrow")
        self.t("read my notes", "read_notes")
        self.t("set a timer for 5 minutes for tea", "set_timer", seconds=300, label="tea")
        self.t("timer 30 seconds", "set_timer", seconds=30)
        self.t("lock screen", "lock_screen")
        self.t("hello jarvis", "chat")
        self.t("help", "help")

    def test_unknown_returns_none(self):
        self.assertIsNone(self.p.parse("what's the meaning of life"))


class SafetyTests(unittest.TestCase):
    def test_forbidden(self):
        for s in ["shut down the computer", "delete all my files", "run this powershell command",
                  "disable the antivirus", "format c drive", "uninstall chrome"]:
            self.assertIsNotNone(is_forbidden(s), s)
        self.assertIsNone(is_forbidden("open chrome"))
        self.assertIsNone(is_forbidden("search how to delete a file in python"))  # research is fine

    def test_gate_rejects_unknown_action(self):
        g = SafetyGate()
        self.assertIsNotNone(g.validate("run_shell", {"cmd": "rm -rf /"}))

    def test_gate_strips_invented_params_and_casts(self):
        g = SafetyGate()
        params = {"seconds": "90", "label": "x", "evil": "rm -rf"}
        self.assertIsNone(g.validate("set_timer", params))
        self.assertEqual(params, {"seconds": 90, "label": "x"})

    def test_open_app_not_allowlisted(self):
        r = actions.act_open_app(app="notepad++ ; rm -rf /")
        self.assertFalse(r.ok)
        self.assertIn("not on my allowed apps list", r.message)

    def test_open_url_schemes(self):
        with mock.patch.object(actions, "_open_in_browser", return_value=True):
            self.assertFalse(actions.act_open_url(url="file:///etc/passwd").ok)
            self.assertFalse(actions.act_open_url(url="javascript:alert(1)").ok)
            self.assertTrue(actions.act_open_url(url="example.com").ok)

    def test_search_is_url_encoded(self):
        with mock.patch.object(actions, "_open_in_browser", return_value=True):
            r = actions.act_web_search(query='cats & dogs "best"')
            self.assertIn("cats+%26+dogs", r.data["url"])

    def test_risky_needs_confirmation(self):
        g = SafetyGate()
        self.assertTrue(g.needs_confirmation("close_app"))
        self.assertFalse(g.needs_confirmation("open_app"))


class LLMParsingTests(unittest.TestCase):
    def test_extract_json_variants(self):
        self.assertEqual(brain._extract_json('{"action":"time","params":{}}')["action"], "time")
        self.assertEqual(brain._extract_json('```json\n{"action":"date"}\n```')["action"], "date")
        self.assertEqual(brain._extract_json('Sure! {"action":"help"} ok')["action"], "help")
        self.assertIsNone(brain._extract_json("no json here"))


if __name__ == "__main__":
    unittest.main()
