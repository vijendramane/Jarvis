"""v2 feature tests. Network calls are mocked so the suite runs offline."""

import datetime as dt
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_PROVIDER", "offline")
os.environ.setdefault("JARVIS_TTS", "false")

from jarvis import features, net  # noqa: E402
from jarvis.actions import APPS, REGISTRY, SafetyGate  # noqa: E402
from jarvis.brain import Brain, RuleParser  # noqa: E402
from jarvis.store import Memory, parse_when  # noqa: E402


class RulesV2(unittest.TestCase):
    def setUp(self):
        self.p = RuleParser()

    def t(self, utterance, expect, **params):
        prop = self.p.parse(utterance)
        self.assertIsNotNone(prop, utterance)
        self.assertEqual(prop.action, expect, utterance)
        for k, v in params.items():
            self.assertEqual(prop.params.get(k), v, utterance)

    def test_info(self):
        self.t("what's the weather in london", "weather", city="london")
        self.t("weather", "weather", city="")
        self.t("news about cricket", "news", topic="cricket")
        self.t("latest headlines", "news", topic="")
        self.t("100 usd to inr", "currency", amount="100", from_currency="usd", to_currency="inr")
        self.t("convert 10 km to miles", "convert_units", value="10", from_unit="km", to_unit="miles")
        self.t("32 f to c", "convert_units")
        self.t("define serendipity", "define", word="serendipity")
        self.t("spell necessary", "spell", word="necessary")
        self.t("translate good morning to hindi", "translate", text="good morning", to_language="hindi")
        self.t("who is alan turing", "wiki_summary", topic="alan turing")
        self.t("what is 15% of 2400", "calculate")
        self.t("calculate 12 * 8", "calculate")

    def test_reminders_memory_routines(self):
        self.t("remind me to call mom in 20 minutes", "set_reminder", text="call mom", when="in 20 minutes")
        self.t("remind me at 5pm to drink water", "set_reminder", text="drink water", when="at 5pm")
        self.t("list reminders", "list_reminders")
        self.t("remember my car number is MH12 AB 1234", "remember", key="car number", value="MH12 AB 1234")
        self.t("what's my car number", "recall", key="car number")
        self.t("forget my car number", "forget", key="car number")
        self.t("start work mode", "run_routine", name="work")
        self.t("good morning", "run_routine", name="good morning")

    def test_device(self):
        self.t("volume up", "volume", action="up")
        self.t("set volume to 40", "volume", action="set", level=40)
        self.t("mute", "volume", action="mute")
        self.t("next song", "media", action="next")
        self.t("pause", "media", action="playpause")
        self.t("what's on my clipboard", "read_clipboard")
        self.t("copy Hello World", "copy_to_clipboard", text="Hello World")
        self.t("take a screenshot", "screenshot")
        self.t("open downloads folder", "open_folder", name="downloads")
        self.t("what did you do today", "activity")

    def test_typo_tolerant_apps(self):
        self.assertEqual(APPS.resolve("chrom"), "chrome")
        self.assertEqual(APPS.resolve("spotfy"), "spotify")
        self.assertEqual(APPS.resolve("calcultor"), "calculator")
        self.assertIsNone(APPS.resolve("photoshop"))

    def test_multi_command_split(self):
        b = Brain()
        self.assertEqual(b.split("open chrome and what time is it"), ["open chrome", "what time is it"])
        self.assertEqual(b.split("open chrome then play music and set volume to 30"), ["open chrome", "play music", "set volume to 30"])
        self.assertEqual(len(b.split("tell me about cats and dogs living together")), 1)


class WhenParsing(unittest.TestCase):
    def test_relative_and_absolute(self):
        now = dt.datetime(2026, 11, 15, 2, 30)
        self.assertEqual(parse_when("in 20 minutes", now), now + dt.timedelta(minutes=20))
        self.assertEqual(parse_when("in two hours", now), now + dt.timedelta(hours=2))
        self.assertEqual(parse_when("at 5pm", now), dt.datetime(2026, 11, 15, 17, 0))
        self.assertEqual(parse_when("at 17:30", now), dt.datetime(2026, 11, 15, 17, 30))
        self.assertEqual(parse_when("tomorrow at 9am", now), dt.datetime(2026, 11, 16, 9, 0))
        self.assertEqual(parse_when("tonight", now), dt.datetime(2026, 11, 15, 19, 0))
        self.assertEqual(parse_when("at 1am", now), dt.datetime(2026, 11, 16, 1, 0))  # already past -> tomorrow
        self.assertIsNone(parse_when("whenever", now))


class OfflineFeatures(unittest.TestCase):
    def test_calculator_is_safe(self):
        self.assertIn("= 360", features.act_calculate(expression="15% of 2400").message)
        self.assertIn("= 1,024", features.act_calculate(expression="2^10").message)
        self.assertFalse(features.act_calculate(expression="__import__('os').system('x')").ok)
        self.assertFalse(features.act_calculate(expression="9**9**9").ok)

    def test_units(self):
        self.assertIn("6.2137 miles", features.act_convert_units(value=10, from_unit="km", to_unit="miles").message)
        self.assertIn("100°C", features.act_convert_units(value=212, from_unit="f", to_unit="c").message)

    def test_memory_roundtrip(self):
        m = Memory()
        m.data = {}
        m.remember("car number", "MH12")
        self.assertEqual(m.recall("car numbr")[1], "MH12")       # fuzzy
        self.assertTrue(m.forget("car number"))
        self.assertIsNone(m.recall("car number"))

    def test_net_host_allowlist(self):
        with self.assertRaises(net.NetError):
            net.get("https://evil.example.com/x")
        with self.assertRaises(net.NetError):
            net.get("http://api.open-meteo.com/x")               # https only


class MockedNetwork(unittest.TestCase):
    def test_weather(self):
        geo = {"results": [{"name": "London", "country_code": "GB", "latitude": 51.5, "longitude": -0.12}]}
        wx = {"current": {"temperature_2m": 11.2, "relative_humidity_2m": 80, "apparent_temperature": 9.5,
                          "weather_code": 61, "wind_speed_10m": 14.0},
              "daily": {"temperature_2m_max": [12.0], "temperature_2m_min": [7.0], "precipitation_probability_max": [70]}}
        with mock.patch.object(net, "get_json", side_effect=[geo, wx]):
            r = features.act_weather(city="London")
        self.assertTrue(r.ok)
        self.assertIn("light rain", r.message)
        self.assertIn("70% chance of rain", r.message)

    def test_currency(self):
        with mock.patch.object(net, "get_json", return_value={"rates": {"INR": 8350.0}, "date": "2026-11-14"}):
            r = features.act_currency(amount=100, from_currency="dollars", to_currency="rupees")
        self.assertIn("8,350.00 INR", r.message)

    def test_news_parses_rss(self):
        rss = b"<rss><channel><item><title>Headline One - Paper</title></item><item><title>Headline Two - Site</title></item></channel></rss>"
        with mock.patch.object(net, "get", return_value=rss):
            r = features.act_news(topic="")
        self.assertEqual(r.data["headlines"], ["Headline One", "Headline Two"])

    def test_define(self):
        payload = [{"word": "serendipity", "phonetic": "/ˌsɛɹənˈdɪpɪti/", "meanings": [{"partOfSpeech": "noun",
                    "definitions": [{"definition": "A happy accident.", "example": "Pure serendipity."}]}]}]
        with mock.patch.object(net, "get_json", return_value=payload):
            r = features.act_define(word="serendipity")
        self.assertIn("(noun): A happy accident.", r.message)

    def test_registry_all_have_specs(self):
        g = SafetyGate()
        g.MAX_ACTIONS_PER_MINUTE = 10_000
        for name, spec in REGISTRY.items():
            self.assertIsNone(g.validate(name, {}), name)
            self.assertTrue(spec.description)


if __name__ == "__main__":
    unittest.main()
