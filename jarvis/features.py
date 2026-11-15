"""v2 features. Each one is a plain function returning ActionResult and is
registered into actions.REGISTRY, so it inherits the same SafetyGate,
parameter validation, logging and confirmation rules as the core actions.

Online features use ONLY keyless public APIs, through net.py's host allowlist:
  weather  -> open-meteo.com          currency -> frankfurter.app (ECB rates)
  define   -> dictionaryapi.dev       wiki     -> en.wikipedia.org REST summary
  news     -> Google News / BBC RSS   translate-> api.mymemory.translated.net
"""

from __future__ import annotations

import ast
import datetime as dt
import html
import json
import math
import operator
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

from . import net
from .actions import APPS, REGISTRY, ActionResult, ActionSpec, _clean
from .config import ROOT, DATA_DIR, settings
from .store import Memory, Reminders, human_when, parse_when

MEMORY = Memory()
REMINDERS = Reminders()
ROUTINES_FILE = ROOT / "routines.json"
SCREENSHOT_DIR = Path.home() / "Pictures" / "Jarvis"


def _run(argv: List[str], input_text: Optional[str] = None, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a FIXED command (never built from user text) without a shell."""
    return subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=timeout, check=False)


# ==========================================================================
# Weather (Open-Meteo, no key)
# ==========================================================================

_WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "icy fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
        71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "showers", 82: "violent showers",
        95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm"}


def _geocode(city: str) -> Optional[dict]:
    data = net.get_json("https://geocoding-api.open-meteo.com/v1/search?" + net.q(name=city, count=1, language="en"),
                        cache_ttl=86400)
    res = data.get("results") or []
    return res[0] if res else None


def act_weather(city: str = "", **_) -> ActionResult:
    city = _clean(city, 80) or (MEMORY.recall("city") or ("", settings.default_city))[1]
    if not city:
        return ActionResult(False, "Which city? You can also say 'remember my city is Pune' once.")
    try:
        place = _geocode(city)
        if not place:
            return ActionResult(False, f"I couldn't find a place called {city}.")
        url = "https://api.open-meteo.com/v1/forecast?" + net.q(
            latitude=place["latitude"], longitude=place["longitude"],
            current="temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            daily="temperature_2m_max,temperature_2m_min,precipitation_probability_max", timezone="auto", forecast_days=2)
        w = net.get_json(url, cache_ttl=600)
    except net.NetError:
        return ActionResult(False, "The weather service isn't reachable right now.")
    c, d = w["current"], w["daily"]
    desc = _WMO.get(int(c.get("weather_code", -1)), "mixed conditions")
    name = f"{place['name']}" + (f", {place.get('country_code', '')}" if place.get("country_code") else "")
    rain = d.get("precipitation_probability_max", [None])[0]
    msg = (f"{name}: {c['temperature_2m']:.0f}°C, {desc}, feels like {c['apparent_temperature']:.0f}°, "
           f"humidity {c['relative_humidity_2m']}%, wind {c['wind_speed_10m']:.0f} km/h. "
           f"Today {d['temperature_2m_min'][0]:.0f}–{d['temperature_2m_max'][0]:.0f}°C")
    if rain is not None:
        msg += f", {rain}% chance of rain"
    return ActionResult(True, msg + ".", {"city": name})


# ==========================================================================
# News (RSS, no key)
# ==========================================================================


def act_news(topic: str = "", **_) -> ActionResult:
    topic = _clean(topic, 80)
    region = settings.country.upper()
    lang = f"en-{region}"
    if topic:
        url = f"https://news.google.com/rss/search?q={quote(topic)}&hl={lang}&gl={region}&ceid={region}:en"
    else:
        url = f"https://news.google.com/rss?hl={lang}&gl={region}&ceid={region}:en"
    try:
        raw = net.get(url, cache_ttl=300)
        items = ET.fromstring(raw).findall(".//item")
    except (net.NetError, ET.ParseError):
        try:
            raw = net.get("https://feeds.bbci.co.uk/news/rss.xml", cache_ttl=300)
            items = ET.fromstring(raw).findall(".//item")
        except (net.NetError, ET.ParseError):
            return ActionResult(False, "I can't reach the news feeds right now.")
    heads = []
    for it in items[:5]:
        title = html.unescape((it.findtext("title") or "").strip())
        title = re.sub(r"\s+-\s+[^-]+$", "", title)          # drop trailing " - Publisher"
        if title:
            heads.append(title)
    if not heads:
        return ActionResult(False, f"No headlines found for {topic or 'today'}.")
    lead = f"Top headlines on {topic}" if topic else "Top headlines"
    return ActionResult(True, f"{lead}: " + " • ".join(heads), {"headlines": heads})


# ==========================================================================
# Currency (Frankfurter / ECB, no key)
# ==========================================================================

_CUR_ALIAS = {"dollar": "USD", "dollars": "USD", "usd": "USD", "rupee": "INR", "rupees": "INR", "inr": "INR", "rs": "INR",
              "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "yen": "JPY",
              "jpy": "JPY", "dirham": "AED", "aed": "AED", "yuan": "CNY", "cny": "CNY", "cad": "CAD", "aud": "AUD",
              "sgd": "SGD", "chf": "CHF", "franc": "CHF", "won": "KRW", "krw": "KRW", "ringgit": "MYR", "myr": "MYR",
              "baht": "THB", "thb": "THB", "riyal": "SAR", "sar": "SAR"}


def _cur(code: str) -> str:
    c = code.strip().lower().rstrip(".")
    return _CUR_ALIAS.get(c, c.upper()[:3])


def act_currency(amount: float = 1, from_currency: str = "USD", to_currency: str = "INR", **_) -> ActionResult:
    try:
        amt = float(amount)
    except (TypeError, ValueError):
        return ActionResult(False, "How much should I convert?")
    f, t = _cur(str(from_currency)), _cur(str(to_currency))
    if not re.fullmatch(r"[A-Z]{3}", f) or not re.fullmatch(r"[A-Z]{3}", t):
        return ActionResult(False, "I need two currency codes, like USD to INR.")
    if f == t:
        return ActionResult(True, f"{amt:g} {f} is {amt:g} {t}.")
    try:
        data = net.get_json(f"https://api.frankfurter.app/latest?{net.q(amount=amt, **{'from': f, 'to': t})}", cache_ttl=3600)
        val = data["rates"][t]
    except (net.NetError, KeyError):
        return ActionResult(False, f"I couldn't get a rate for {f} to {t} (only major currencies are supported).")
    return ActionResult(True, f"{amt:,.2f} {f} is about {val:,.2f} {t} (ECB reference rate, {data.get('date', '')}).",
                        {"rate": val / amt})


# ==========================================================================
# Dictionary & spelling (dictionaryapi.dev, no key)
# ==========================================================================


def act_define(word: str = "", **_) -> ActionResult:
    w = re.sub(r"[^A-Za-z\-' ]", "", _clean(word, 40)).strip().lower()
    if not w:
        return ActionResult(False, "Which word?")
    try:
        data = net.get_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(w)}", cache_ttl=86400)
    except net.NetError:
        return ActionResult(False, f"I couldn't find a definition for {w}.")
    try:
        entry = data[0]
        meaning = entry["meanings"][0]
        pos = meaning.get("partOfSpeech", "")
        definition = meaning["definitions"][0]["definition"]
        example = meaning["definitions"][0].get("example")
        phon = entry.get("phonetic") or next((p.get("text") for p in entry.get("phonetics", []) if p.get("text")), "")
    except (KeyError, IndexError, TypeError):
        return ActionResult(False, f"I couldn't find a definition for {w}.")
    msg = f"{w}" + (f" {phon}" if phon else "") + f" ({pos}): {definition}"
    if example:
        msg += f' Example: "{example}"'
    return ActionResult(True, msg)


def act_spell(word: str = "", **_) -> ActionResult:
    w = re.sub(r"[^A-Za-z\-']", "", _clean(word, 40))
    if not w:
        return ActionResult(False, "Which word should I spell?")
    return ActionResult(True, f"{w}: " + " ".join(w.upper()))


# ==========================================================================
# Wikipedia summary (spoken, no key)
# ==========================================================================


def act_wiki_summary(topic: str = "", **_) -> ActionResult:
    t = _clean(topic, 100)
    if not t:
        return ActionResult(False, "What should I look up?")
    try:
        title = t.strip().replace(" ", "_")
        data = net.get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}?redirect=true", cache_ttl=86400)
        if data.get("type") == "disambiguation":
            return ActionResult(True, f"{t} could mean several things — try being more specific.")
        extract = data.get("extract")
        if not extract:
            raise KeyError
    except (net.NetError, KeyError, ValueError):
        return ActionResult(False, f"I couldn't find a Wikipedia summary for {t}.")
    sentences = re.split(r"(?<=[.!?])\s+", extract.strip())
    short = " ".join(sentences[:3])
    return ActionResult(True, short, {"url": data.get("content_urls", {}).get("desktop", {}).get("page", "")})


# ==========================================================================
# Translation (MyMemory, no key)
# ==========================================================================

_LANGS = {"english": "en", "hindi": "hi", "marathi": "mr", "tamil": "ta", "telugu": "te", "kannada": "kn",
          "gujarati": "gu", "bengali": "bn", "punjabi": "pa", "malayalam": "ml", "urdu": "ur", "spanish": "es",
          "french": "fr", "german": "de", "italian": "it", "portuguese": "pt", "japanese": "ja", "chinese": "zh-CN",
          "korean": "ko", "arabic": "ar", "russian": "ru", "dutch": "nl", "turkish": "tr", "indonesian": "id"}


def act_translate(text: str = "", to_language: str = "", from_language: str = "english", **_) -> ActionResult:
    txt = _clean(text, 400)
    to_code = _LANGS.get(str(to_language).lower().strip(), str(to_language).lower()[:5])
    from_code = _LANGS.get(str(from_language).lower().strip(), "en")
    if not txt or not to_code:
        return ActionResult(False, "Tell me what to translate and into which language.")
    try:
        data = net.get_json("https://api.mymemory.translated.net/get?" + net.q(q=txt, langpair=f"{from_code}|{to_code}"))
        out = data["responseData"]["translatedText"]
        if data.get("responseStatus") not in (200, "200") or not out:
            raise KeyError
    except (net.NetError, KeyError, TypeError):
        return ActionResult(False, "Translation service isn't available right now.")
    return ActionResult(True, f"In {to_language}: {html.unescape(out)}", {"translation": out})


# ==========================================================================
# Offline calculator + unit conversion (safe AST evaluation, no eval)
# ==========================================================================

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos, "tan": math.tan,
          "log": math.log10, "ln": math.log, "pi": math.pi, "e": math.e}


def _safe_eval(node):
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in ("pi", "e"):
        return _FUNCS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        return _FUNCS[node.func.id](*[_safe_eval(a) for a in node.args])
    raise ValueError("unsupported expression")


def _fmt(x: float) -> str:
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return f"{x:,}" if isinstance(x, int) else f"{x:,.4f}".rstrip("0").rstrip(".")


def act_calculate(expression: str = "", **_) -> ActionResult:
    raw = _clean(expression, 200).lower()
    if not raw:
        return ActionResult(False, "What should I calculate?")
    e = raw.replace("what is", "").replace("what's", "").replace("calculate", "").replace("compute", "").replace("how much is", "")
    e = e.replace("=", "").replace("?", "").replace(" percent", "%").replace("percent", "%").replace("the square root", "square root")
    e = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", r"(\1/100*\2)", e)
    e = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", e)
    e = (e.replace("plus", "+").replace("minus", "-").replace("times", "*").replace("multiplied by", "*")
          .replace("divided by", "/").replace("into", "*").replace("x", "*").replace("^", "**")
          .replace("square root of", "sqrt").replace("squared", "**2").replace("cubed", "**3").replace(",", ""))
    e = re.sub(r"(?:to the power(?: of)?|raised to|power)\s*", "**", e)
    e = re.sub(r"sqrt\s+(\d+(?:\.\d+)?)", r"sqrt(\1)", e)
    e = re.sub(r"[^0-9+\-*/().% a-z]", "", e).strip()
    if not re.search(r"\d", e):
        return ActionResult(False, "I couldn't read that as a calculation.")
    try:
        val = _safe_eval(ast.parse(e, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError, OverflowError):
        return ActionResult(False, "I couldn't read that as a calculation.")
    return ActionResult(True, f"{raw.strip()} = {_fmt(val)}", {"value": val})


_UNITS = {  # to-SI factor within each family
    "length": {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000, "in": 0.0254, "inch": 0.0254, "inches": 0.0254, "ft": 0.3048,
               "feet": 0.3048, "foot": 0.3048, "yd": 0.9144, "yard": 0.9144, "yards": 0.9144, "mi": 1609.344, "mile": 1609.344, "miles": 1609.344},
    "mass": {"g": 0.001, "gram": 0.001, "grams": 0.001, "kg": 1, "kilo": 1, "kilos": 1, "kilogram": 1, "kilograms": 1,
             "lb": 0.453592, "lbs": 0.453592, "pound": 0.453592, "pounds": 0.453592, "oz": 0.0283495, "ounce": 0.0283495, "ounces": 0.0283495},
    "volume": {"ml": 0.001, "l": 1, "litre": 1, "liter": 1, "litres": 1, "liters": 1, "gal": 3.78541, "gallon": 3.78541, "gallons": 3.78541, "cup": 0.24, "cups": 0.24},
    "speed": {"kmh": 1, "kph": 1, "km/h": 1, "mph": 1.609344, "knots": 1.852, "knot": 1.852},
    "data": {"kb": 1e-6, "mb": 1e-3, "gb": 1, "tb": 1e3},
}


def act_convert_units(value: float = 0, from_unit: str = "", to_unit: str = "", **_) -> ActionResult:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ActionResult(False, "Convert how much?")
    f, t = str(from_unit).lower().strip().rstrip("."), str(to_unit).lower().strip().rstrip(".")
    temp = {"c": "c", "celsius": "c", "°c": "c", "f": "f", "fahrenheit": "f", "°f": "f", "k": "k", "kelvin": "k"}
    if f in temp and t in temp:
        c = v if temp[f] == "c" else (v - 32) * 5 / 9 if temp[f] == "f" else v - 273.15
        out = c if temp[t] == "c" else c * 9 / 5 + 32 if temp[t] == "f" else c + 273.15
        return ActionResult(True, f"{_fmt(v)}°{temp[f].upper()} is {_fmt(round(out, 2))}°{temp[t].upper()}.")
    for fam in _UNITS.values():
        if f in fam and t in fam:
            out = v * fam[f] / fam[t]
            return ActionResult(True, f"{_fmt(v)} {f} is {_fmt(round(out, 4))} {t}.", {"value": out})
    return ActionResult(False, f"I don't know how to convert {f} to {t}.")


# ==========================================================================
# Reminders & memory
# ==========================================================================


def act_set_reminder(text: str = "", when: str = "", **_) -> ActionResult:
    txt, w = _clean(text, 300), _clean(when, 100)
    if not txt:
        return ActionResult(False, "What should I remind you about?")
    when_dt = parse_when(w) if w else None
    if not when_dt:
        when_dt = parse_when(f"{txt} {w}".strip())   # LLMs sometimes leave the time inside the text
    if not when_dt:
        return ActionResult(False, "When? Try 'in 20 minutes' or 'at 5:30 pm' or 'tomorrow at 9'.")
    REMINDERS.add(txt, when_dt)
    return ActionResult(True, f"I'll remind you to {txt} {human_when(when_dt)}.", {"when": when_dt.isoformat()})


def act_list_reminders(**_) -> ActionResult:
    items = REMINDERS.pending()
    if not items:
        return ActionResult(True, "You have no pending reminders.")
    parts = [f"{i['text']} ({human_when(dt.datetime.fromisoformat(i['when']))})" for i in items[:6]]
    return ActionResult(True, f"{len(items)} reminder{'s' if len(items) != 1 else ''}: " + "; ".join(parts))


def act_clear_reminders(**_) -> ActionResult:
    n = REMINDERS.cancel_all()
    return ActionResult(True, f"Cancelled {n} reminder{'s' if n != 1 else ''}.")


def act_remember(key: str = "", value: str = "", **_) -> ActionResult:
    k = MEMORY.remember(_clean(key, 60), _clean(value, 300))
    if not k:
        return ActionResult(False, "Tell me what to remember, like 'remember my car number is MH12AB1234'.")
    return ActionResult(True, f"Got it — your {k} is {MEMORY.data[k]}.")


def act_recall(key: str = "", **_) -> ActionResult:
    hit = MEMORY.recall(_clean(key, 60))
    if not hit:
        return ActionResult(False, f"I don't have anything saved for '{_clean(key, 60)}'.")
    return ActionResult(True, f"Your {hit[0]} is {hit[1]}.")


def act_forget(key: str = "", **_) -> ActionResult:
    return ActionResult(True, "Forgotten.") if MEMORY.forget(_clean(key, 60)) else \
        ActionResult(False, "I didn't have that saved.")


def act_list_memory(**_) -> ActionResult:
    d = MEMORY.all()
    if not d:
        return ActionResult(True, "I haven't been told anything to remember yet.")
    return ActionResult(True, "I remember: " + "; ".join(f"{k} = {v}" for k, v in list(d.items())[-10:]))


# ==========================================================================
# Routines: a named list of ordinary Jarvis commands (routines.json)
# ==========================================================================


def load_routines() -> Dict[str, List[str]]:
    try:
        raw = json.loads(ROUTINES_FILE.read_text(encoding="utf-8"))
        return {k.lower(): v for k, v in raw.items() if not k.startswith("_") and isinstance(v, list)}
    except (OSError, ValueError):
        return {}


_routine_runner = None   # set by main: runs each step through the normal handle() pipeline


def set_routine_runner(fn) -> None:
    global _routine_runner
    _routine_runner = fn


def act_run_routine(name: str = "", **_) -> ActionResult:
    routines = load_routines()
    n = _clean(name, 60).lower().replace(" mode", "").replace(" routine", "")
    key = next((k for k in routines if k == n or k.replace(" mode", "") == n), None)
    if not key:
        return ActionResult(False, f"No routine called '{n}'. Routines: {', '.join(routines) or 'none yet'} (edit routines.json).")
    steps = [s for s in routines[key] if isinstance(s, str)][:10]
    if _routine_runner:
        for s in steps:
            _routine_runner(s)
    return ActionResult(True, f"Routine '{key}' done — {len(steps)} step{'s' if len(steps) != 1 else ''}.", {"steps": steps})


def act_list_routines(**_) -> ActionResult:
    r = load_routines()
    return ActionResult(True, ("Routines: " + ", ".join(r)) if r else "No routines yet — add some to routines.json.")


# ==========================================================================
# Volume & media keys
# ==========================================================================


def _win_key(vk: int) -> None:
    import ctypes

    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)


def _win_volume_step(direction: str, times: int) -> None:
    vk = 0xAF if direction == "up" else 0xAE
    for _ in range(max(1, min(times, 50))):
        _win_key(vk)


def act_volume(action: str = "up", level: int = -1, **_) -> ActionResult:
    a = str(action).lower().strip()
    try:
        lvl = int(level)
    except (TypeError, ValueError):
        lvl = -1
    if lvl >= 0:
        a, lvl = "set", max(0, min(100, lvl))
    if a not in ("up", "down", "mute", "unmute", "set"):
        return ActionResult(False, "Volume up, down, mute, unmute, or a level from 0 to 100.")
    try:
        if settings.os_name == "Windows":
            if a == "up":
                _win_volume_step("up", 5)
            elif a == "down":
                _win_volume_step("down", 5)
            elif a in ("mute", "unmute"):
                _win_key(0xAD)
            else:                                   # set: zero out then step up (2% per key press)
                _win_volume_step("down", 50)
                _win_volume_step("up", round(lvl / 2))
        elif settings.os_name == "Darwin":
            script = {"up": "set volume output volume ((output volume of (get volume settings)) + 10)",
                      "down": "set volume output volume ((output volume of (get volume settings)) - 10)",
                      "mute": "set volume with output muted", "unmute": "set volume without output muted",
                      "set": f"set volume output volume {lvl}"}[a]
            _run(["osascript", "-e", script])
        else:
            if shutil.which("pactl"):
                arg = {"up": ["set-sink-volume", "@DEFAULT_SINK@", "+10%"], "down": ["set-sink-volume", "@DEFAULT_SINK@", "-10%"],
                       "mute": ["set-sink-mute", "@DEFAULT_SINK@", "1"], "unmute": ["set-sink-mute", "@DEFAULT_SINK@", "0"],
                       "set": ["set-sink-volume", "@DEFAULT_SINK@", f"{lvl}%"]}[a]
                _run(["pactl"] + arg)
            elif shutil.which("amixer"):
                arg = {"up": ["10%+"], "down": ["10%-"], "mute": ["mute"], "unmute": ["unmute"], "set": [f"{lvl}%"]}[a]
                _run(["amixer", "-q", "sset", "Master"] + arg)
            else:
                return ActionResult(False, "No volume control tool found (need pactl or amixer).")
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"Volume control failed: {e.__class__.__name__}.")
    said = {"up": "Volume up.", "down": "Volume down.", "mute": "Muted.", "unmute": "Unmuted.", "set": f"Volume set to {lvl}%."}[a]
    return ActionResult(True, said)


def act_media(action: str = "playpause", **_) -> ActionResult:
    a = str(action).lower().replace(" ", "").replace("_", "").replace("/", "")
    a = {"play": "playpause", "pause": "playpause", "toggle": "playpause", "resume": "playpause", "skip": "next",
         "forward": "next", "back": "previous", "prev": "previous", "previous": "previous", "next": "next",
         "playpause": "playpause", "stop": "stop"}.get(a)
    if not a:
        return ActionResult(False, "Media: play, pause, next, previous or stop.")
    try:
        if settings.os_name == "Windows":
            _win_key({"playpause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}[a])
        elif settings.os_name == "Darwin":
            for app in ("Spotify", "Music"):
                r = _run(["osascript", "-e", f'tell application "System Events" to (name of processes) contains "{app}"'])
                if r.stdout.strip() == "true":
                    cmd = {"playpause": "playpause", "next": "next track", "previous": "previous track", "stop": "pause"}[a]
                    _run(["osascript", "-e", f'tell application "{app}" to {cmd}'])
                    break
            else:
                return ActionResult(False, "No music app is running.")
        else:
            if not shutil.which("playerctl"):
                return ActionResult(False, "Install playerctl for media control on Linux.")
            _run(["playerctl", {"playpause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}[a]])
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"Media control failed: {e.__class__.__name__}.")
    return ActionResult(True, {"playpause": "Done.", "next": "Next track.", "previous": "Previous track.", "stop": "Stopped."}[a])


# ==========================================================================
# Clipboard
# ==========================================================================


def _clip_read() -> Optional[str]:
    try:
        if settings.os_name == "Windows":
            return _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"]).stdout
        if settings.os_name == "Darwin":
            return _run(["pbpaste"]).stdout
        for cmd in (["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
            if shutil.which(cmd[0]):
                return _run(cmd).stdout
    except Exception:  # noqa: BLE001
        return None
    return None


def _clip_write(text: str) -> bool:
    try:
        if settings.os_name == "Windows":
            return _run(["clip"], input_text=text).returncode == 0
        if settings.os_name == "Darwin":
            return _run(["pbcopy"], input_text=text).returncode == 0
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                return _run(cmd, input_text=text).returncode == 0
    except Exception:  # noqa: BLE001
        return False
    return False


def act_read_clipboard(**_) -> ActionResult:
    txt = _clip_read()
    if txt is None:
        return ActionResult(False, "I can't access the clipboard on this system.")
    txt = txt.strip()
    if not txt:
        return ActionResult(True, "The clipboard is empty.")
    short = txt if len(txt) <= 300 else txt[:300] + "…"
    return ActionResult(True, f"Clipboard: {short}", {"length": len(txt)})


def act_copy_to_clipboard(text: str = "", **_) -> ActionResult:
    t = _clean(text, 5000)
    if not t:
        return ActionResult(False, "What should I copy?")
    return ActionResult(True, "Copied to clipboard.") if _clip_write(t) else ActionResult(False, "Clipboard isn't available here.")


# ==========================================================================
# Screenshot
# ==========================================================================


def act_screenshot(**_) -> ActionResult:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"screenshot_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        try:
            from PIL import ImageGrab   # works on Windows & macOS (and X11 Linux) if Pillow is installed

            ImageGrab.grab().save(path)
        except ImportError:
            if settings.os_name == "Darwin":
                _run(["screencapture", "-x", str(path)])
            elif settings.os_name == "Linux":
                for cmd in (["gnome-screenshot", "-f", str(path)], ["scrot", str(path)], ["import", "-window", "root", str(path)]):
                    if shutil.which(cmd[0]):
                        _run(cmd)
                        break
            else:
                os.startfile("ms-screenclip:")  # type: ignore[attr-defined]
                return ActionResult(True, "Opened the Windows snipping tool (install Pillow for silent screenshots).")
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"Screenshot failed: {e.__class__.__name__}.")
    if path.exists():
        return ActionResult(True, f"Screenshot saved to {path}.", {"path": str(path)})
    return ActionResult(False, "No screenshot tool found (install Pillow: pip install pillow).")


# ==========================================================================
# Standard folders (fixed set — never arbitrary paths)
# ==========================================================================

_FOLDERS = {"home": "", "downloads": "Downloads", "documents": "Documents", "desktop": "Desktop",
            "pictures": "Pictures", "music": "Music", "videos": "Videos", "screenshots": "Pictures/Jarvis"}


def act_open_folder(name: str = "", **_) -> ActionResult:
    n = re.sub(r"[^a-z]", "", _clean(name, 30).lower().replace("folder", ""))
    n = {"download": "downloads", "document": "documents", "picture": "pictures", "photos": "pictures",
         "video": "videos", "movies": "videos", "jarvis": "screenshots"}.get(n, n)
    if n not in _FOLDERS:
        return ActionResult(False, f"I can open these folders: {', '.join(_FOLDERS)}.")
    path = Path.home() / _FOLDERS[n]
    if not path.exists():
        return ActionResult(False, f"The {n} folder doesn't exist on this machine.")
    try:
        if settings.os_name == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif settings.os_name == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"Couldn't open {n}: {e.__class__.__name__}.")
    return ActionResult(True, f"Opening your {n} folder.", {"path": str(path)})


# ==========================================================================
# Activity log
# ==========================================================================


def act_activity(**_) -> ActionResult:
    from .config import HISTORY_FILE

    if not HISTORY_FILE.exists():
        return ActionResult(True, "No activity logged yet.")
    today = dt.date.today().isoformat()
    rows = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", "").startswith(today) and r.get("action") not in ("chat", "activity"):
            rows.append(r)
    if not rows:
        return ActionResult(True, "I haven't done anything for you today yet.")
    last = rows[-6:]
    desc = "; ".join(f"{r['ts'][11:16]} {r['action'].replace('_', ' ')} {next(iter(r.get('params', {}).values()), '')}".strip()
                     for r in last)
    return ActionResult(True, f"{len(rows)} actions today. Most recent: {desc}.")


# ==========================================================================
# Register
# ==========================================================================

REGISTRY.update({
    "weather":          ActionSpec(act_weather, {"city": str}, "Current weather and today's forecast for a city (empty = remembered city).", False),
    "news":             ActionSpec(act_news, {"topic": str}, "Top news headlines, optionally about a topic.", False),
    "currency":         ActionSpec(act_currency, {"amount": float, "from_currency": str, "to_currency": str}, "Convert money between currencies.", False),
    "define":           ActionSpec(act_define, {"word": str}, "Dictionary definition of an English word.", False),
    "spell":            ActionSpec(act_spell, {"word": str}, "Spell a word letter by letter.", False),
    "wiki_summary":     ActionSpec(act_wiki_summary, {"topic": str}, "Short spoken Wikipedia summary of a topic/person/place.", False),
    "translate":        ActionSpec(act_translate, {"text": str, "to_language": str, "from_language": str}, "Translate text into another language.", False),
    "calculate":        ActionSpec(act_calculate, {"expression": str}, "Do arithmetic, percentages, square roots.", False),
    "convert_units":    ActionSpec(act_convert_units, {"value": float, "from_unit": str, "to_unit": str}, "Convert units (km/miles, kg/lb, °C/°F, ...).", False),
    "set_reminder":     ActionSpec(act_set_reminder, {"text": str, "when": str}, "Remind the user about something at a time ('in 20 minutes', 'at 5pm', 'tomorrow 9am').", False),
    "list_reminders":   ActionSpec(act_list_reminders, {}, "List pending reminders.", False),
    "clear_reminders":  ActionSpec(act_clear_reminders, {}, "Cancel all pending reminders.", True),
    "remember":         ActionSpec(act_remember, {"key": str, "value": str}, "Store a personal fact (key -> value), e.g. 'car number' -> 'MH12AB1234'.", False),
    "recall":           ActionSpec(act_recall, {"key": str}, "Retrieve a stored personal fact.", False),
    "forget":           ActionSpec(act_forget, {"key": str}, "Delete a stored personal fact.", False),
    "list_memory":      ActionSpec(act_list_memory, {}, "List everything remembered.", False),
    "run_routine":      ActionSpec(act_run_routine, {"name": str}, "Run a named routine from routines.json (e.g. 'work mode').", False),
    "list_routines":    ActionSpec(act_list_routines, {}, "List available routines.", False),
    "volume":           ActionSpec(act_volume, {"action": str, "level": int}, "Volume up/down/mute/unmute or set level 0-100.", False),
    "media":            ActionSpec(act_media, {"action": str}, "Media control: play/pause, next, previous, stop.", False),
    "read_clipboard":   ActionSpec(act_read_clipboard, {}, "Read what's on the clipboard.", False),
    "copy_to_clipboard": ActionSpec(act_copy_to_clipboard, {"text": str}, "Put text on the clipboard.", False),
    "screenshot":       ActionSpec(act_screenshot, {}, "Take a screenshot and save it to Pictures/Jarvis.", False),
    "open_folder":      ActionSpec(act_open_folder, {"name": str}, "Open a standard folder: downloads, documents, desktop, pictures, music, videos, home.", False),
    "activity":         ActionSpec(act_activity, {}, "Summarise what Jarvis did today.", False),
})
