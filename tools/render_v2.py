import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update({"JARVIS_PROVIDER": "offline", "JARVIS_TTS": "false", "JARVIS_COUNTRY": "IN"})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_demo import run, render  # reuses the terminal renderer (patches launch/browser)
from unittest import mock
from jarvis import net, features
import jarvis.features as F
from jarvis.main import Jarvis

# Sandbox has no internet: stand-in payloads in the exact shape the real keyless APIs return.
GEO = {"results": [{"name": "Pune", "country_code": "IN", "latitude": 18.52, "longitude": 73.86}]}
WX = {"current": {"temperature_2m": 24.1, "relative_humidity_2m": 61, "apparent_temperature": 25.0, "weather_code": 1, "wind_speed_10m": 8.2},
      "daily": {"temperature_2m_max": [31.0], "temperature_2m_min": [19.0], "precipitation_probability_max": [5]}}
RSS = b"<rss><channel>" + b"".join(f"<item><title>{t} - Source</title></item>".encode() for t in [
    "ISRO announces next lunar mission timeline", "Sensex ends week higher on IT gains",
    "Heavy rain alert for coastal Karnataka", "India name squad for T20 series", "New EV policy draft released"]) + b"</channel></rss>"
FX = {"rates": {"INR": 8412.50}, "date": "2026-11-14"}
DEF = [{"word": "serendipity", "phonetic": "/ˌsɛɹənˈdɪpɪti/", "meanings": [{"partOfSpeech": "noun", "definitions": [
    {"definition": "The occurrence of events by chance in a happy or beneficial way.", "example": "a fortunate stroke of serendipity"}]}]}]
WIKI = {"extract": "Alan Mathison Turing was an English mathematician, computer scientist, logician, cryptanalyst, philosopher and theoretical biologist. He was highly influential in the development of theoretical computer science, providing a formalisation of the concepts of algorithm and computation with the Turing machine. Turing is widely considered to be the father of theoretical computer science.", "content_urls": {"desktop": {"page": ""}}}
TR = {"responseStatus": 200, "responseData": {"translatedText": "सुप्रभात, आपका दिन शुभ हो"}}

def fake_get_json(url, **kw):
    if "geocoding" in url: return GEO
    if "open-meteo" in url: return WX
    if "frankfurter" in url: return FX
    if "dictionaryapi" in url: return DEF
    if "wikipedia" in url: return WIKI
    if "mymemory" in url: return TR
    raise net.NetError("unknown")

F._clip_read = lambda: "https://github.com/anthropics/claude-code"
F._clip_write = lambda t: True

with mock.patch.object(net, "get_json", side_effect=fake_get_json), mock.patch.object(net, "get", return_value=RSS):
    j = Jarvis()
    s1 = run(j, ["remember my city is Pune", "remember my car number is MH12 AB 1234", "weather", "news about india",
                 "100 dollars to rupees", "define serendipity", "who is alan turing", "translate good morning, have a nice day to hindi"],
             "Jarvis v2.0.0 — offline brain + free keyless web APIs (web responses are stand-ins: sandbox has no internet)")
    render(s1, "/home/claude/jarvis_v2_1_knowledge.png", "Jarvis v2 — weather · news · currency · dictionary · wiki · translate")

    s2 = run(j, ["what is 15% of 2400", "convert 10 km to miles", "remind me to call mom in 20 minutes",
                 "remind me tomorrow at 9am to pay the electricity bill", "list reminders", "what's my car number",
                 "set volume to 40", "next song", "what's on my clipboard", "open downloads folder",
                 "open chrom and play music then set a timer for 25 minutes for focus", "good morning", "what did you do today"],
             "Jarvis v2.0.0 — productivity & device control (volume/media are no-ops on this headless box)")
    render(s2, "/home/claude/jarvis_v2_2_productivity.png", "Jarvis v2 — reminders · memory · calculator · routines · multi-command")
