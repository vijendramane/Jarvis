"""The 'brain': turns natural language into a structured, validated action.

Two layers:
  1. ``RuleParser`` — instant, offline, no network. Handles clear commands
     ("open chrome", "search cats", "set a timer for 5 minutes").
  2. ``LLMBrain`` — for anything fuzzy or conversational. Talks to a free
     API (Groq / Gemini) or a local Ollama model, and must answer with a
     JSON object naming ONE action from the registry.

The brain never executes anything; it returns a ``Proposal`` which the
``SafetyGate`` in actions.py checks before execution.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .actions import APPS, schema_for_llm
from .config import settings


@dataclass
class Proposal:
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    say: str = ""              # optional spoken preface from the LLM
    source: str = "rules"      # rules | groq | gemini | ollama


# ==========================================================================
# 1. Offline rule parser
# ==========================================================================

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "half": 0.5, "a": 1, "an": 1,
}


def _num(tok: str) -> Optional[float]:
    tok = tok.lower()
    if tok in _NUM_WORDS:
        return float(_NUM_WORDS[tok])
    try:
        return float(tok)
    except ValueError:
        return None


class RuleParser:
    _FILLER = r"(?:please|can you|could you|would you|hey|jarvis|for me|now|up)?"

    def __init__(self, llm_available: bool = False):
        # when an AI brain exists, fuzzy phrasings are left to it instead of guessed by rules
        self._llm_available = llm_available

    def parse(self, text: str) -> Optional[Proposal]:
        t = text.strip().lower()
        raw = text.strip()
        t = re.sub(r"[^\w\s:/.\-?'%*+^=$€£₹()]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        # strip polite / wake-word fillers at the start and end
        t = re.sub(r"^(?:(?:hey|ok|okay|hi|please|can you|could you|would you|jarvis|,)\s+)+", "", t)
        t = re.sub(r"(?:\s+(?:please|now|for me|jarvis))+$", "", t).strip()
        if not t:
            return None

        # --- routines by exact name take priority ("good morning" can be a routine) ---
        from .features import load_routines as _lr0
        if t in _lr0():
            return Proposal("run_routine", {"name": t})

        # --- greetings / small talk (cheap, no API) ---
        if re.fullmatch(r"(hi|hello|hey|yo|good (morning|afternoon|evening))( jarvis)?", t):
            return Proposal("chat", {"reply": f"Hello {settings.user_name}. How can I help?"})
        if re.fullmatch(r"(thanks|thank you|thx|cheers)( jarvis)?", t):
            return Proposal("chat", {"reply": "Anytime."})
        if re.fullmatch(r"(help|what can you do|commands)", t):
            return Proposal("help")
        if re.fullmatch(r"(list|show) (my )?(apps|applications)", t):
            return Proposal("list_apps")

        # --- time / date ---
        if re.search(r"\b(what('s| is)? the )?time\b", t) and "timer" not in t:
            return Proposal("time")
        if re.search(r"\b(what('s| is)? (the )?date|what day is it|today's date)\b", t):
            return Proposal("date")

        # --- system ---
        if re.search(r"\b(system|battery|cpu|memory|ram|disk)\b.*\b(status|usage|level|left|free)?\b", t) and \
                re.search(r"\b(status|usage|level|how much|how is|how's|check|report)\b", t):
            return Proposal("system_status")
        if re.fullmatch(r"(lock|lock (the )?(screen|computer|pc))", t):
            return Proposal("lock_screen")

        # --- notes ---
        m = re.match(r"(?:(?:hey|ok|okay|please|jarvis)[, ]+)*(?:(?:take|make|add|write)(?: a)? note[:\s]+|note (?:that |down )?)(.+)", raw, re.I)
        if m:
            return Proposal("take_note", {"text": m.group(1)})
        if re.search(r"\b(read|show|list)( my)? notes\b", t):
            return Proposal("read_notes")

        # --- timer ---
        m = re.search(r"timer (?:for )?(\S+)\s*(seconds|second|secs|sec|minutes|minute|mins|min|hours|hour|hrs|hr)\b(?:\s+(?:for|to|called|named)\s+(.+))?", t)
        if m:
            n = _num(m.group(1))
            if n is not None:
                unit = m.group(2)
                mult = 1 if unit.startswith("s") else 60 if unit.startswith("m") else 3600
                return Proposal("set_timer", {"seconds": int(n * mult), "label": m.group(3) or ""})


        # --- v2: weather / news / currency / dictionary / translate / calc / units ---
        m = re.match(r"(?:what'?s the |how'?s the |tell me the )?(?:weather|temperature|forecast)(?:\s+(?:like|today|now|tomorrow|outside))*\s*(?:in|at|for)?\s*(.*)$", t)
        if m:
            return Proposal("weather", {"city": m.group(1).strip()})
        m = re.match(r"(?:what'?s |tell me |read |show |get )?(?:the )?(?:latest |today'?s |top )?(?:news|headlines)(?: (?:about|on|for) (.+))?$", t)
        if m:
            return Proposal("news", {"topic": m.group(1) or ""})
        m = re.match(r"(?:convert |how much is |what is |what'?s )?([\d.,]+)\s*([a-z$€£₹]+)\s+(?:to|in|into)\s+([a-z$€£₹]+)\??$", t)
        if m:
            cur = {"$": "usd", "€": "eur", "£": "gbp", "₹": "inr"}
            a, f, to = m.group(1).replace(",", ""), cur.get(m.group(2), m.group(2)), cur.get(m.group(3), m.group(3))
            from .features import _CUR_ALIAS, _UNITS
            unit_names = {u for fam in _UNITS.values() for u in fam} | {"c", "f", "k", "celsius", "fahrenheit", "kelvin"}
            if f in unit_names and to in unit_names:
                return Proposal("convert_units", {"value": a, "from_unit": f, "to_unit": to})
            if f in _CUR_ALIAS or to in _CUR_ALIAS or (len(f) == 3 and len(to) == 3):
                return Proposal("currency", {"amount": a, "from_currency": f, "to_currency": to})
        m = re.match(r"(?:define|definition of|meaning of|what does)\s+(?:the word )?([a-z\-']+)(?: mean)?\??$", t)
        if m:
            return Proposal("define", {"word": m.group(1)})
        m = re.match(r"(?:spell|how do you spell|spelling of)\s+(?:the word )?([a-z\-']+)\??$", t)
        if m:
            return Proposal("spell", {"word": m.group(1)})
        m = re.match(r"(?:translate|say|how do you say)\s+(.+?)\s+(?:in|into|to)\s+([a-z]+)\??$", raw, re.I)
        if m:
            return Proposal("translate", {"text": m.group(1).strip("'\""), "to_language": m.group(2).lower()})
        m = re.match(r"(?:who is|who was|who are|tell me about|what is a|what is an|what is the)\s+(.+?)\??$", t)
        if m and not self._llm_available and not re.search(r"\d", m.group(1)):
            return Proposal("wiki_summary", {"topic": m.group(1)})
        m = re.match(r"(?:summar(?:y|ise|ize) (?:of )?|wiki summary |tell me about )(.+)", t)
        if m:
            return Proposal("wiki_summary", {"topic": m.group(1)})
        if re.match(r"(?:calculate|compute|what is|what'?s|how much is)?\s*[\d(][\d\s+\-*/x^%().,]*(?:plus|minus|times|divided by|into|squared|percent|%|of|[+\-*/^x])[\d\s+\-*/x^%().,a-z]*$", t) \
                or re.match(r"(?:calculate|compute)\s+.+", t) or re.match(r"(?:what is |what'?s )?(?:the )?square root of \d", t) \
                or re.match(r"(?:what is |what'?s )?\d+(?:\.\d+)? (?:to the power(?: of)?|raised to) \d", t):
            return Proposal("calculate", {"expression": t})

        # --- v2: reminders / memory / routines ---
        m = re.match(r"remind me\s+((?:in|at|tomorrow|tonight|this|on)\b.+?)\s+(?:to|that|about)\s+(.+)$", t)
        if m:                                   # time first: "remind me tomorrow at 9am to pay the bill"
            return Proposal("set_reminder", {"text": m.group(2), "when": m.group(1)})
        m = re.match(r"remind me (?:to |that |about )?(.+?)\s+((?:in|at|on|tomorrow|tonight|this)\b.+)$", t)
        if m:                                   # task first: "remind me to call mom in 20 minutes"
            return Proposal("set_reminder", {"text": m.group(1), "when": m.group(2)})
        if re.match(r"remind me", t):
            return Proposal("set_reminder", {"text": re.sub(r"^remind me (?:to |about )?", "", t), "when": ""})
        if re.search(r"\b(list|show|what are|any)\b.*\breminders?\b", t) or t == "reminders":
            return Proposal("list_reminders")
        if re.search(r"\b(cancel|clear|delete)\b.*\breminders\b", t):
            return Proposal("clear_reminders")
        m = re.match(r"(?:(?:hey|ok|okay|please|jarvis)[, ]+)*remember (?:that )?(?:my |the |our )?(.+?)\s+(?:is|are|=)\s+(.+?)[.!]?$", raw, re.I)
        if m:
            return Proposal("remember", {"key": m.group(1).lower(), "value": m.group(2)})
        m = re.match(r"(?:what(?:'s| is| are) (?:my|our) |recall |do you remember (?:my )?)(.+?)\??$", t)
        if m:
            return Proposal("recall", {"key": m.group(1)})
        m = re.match(r"forget (?:about )?(?:my |the )?(.+)", t)
        if m:
            return Proposal("forget", {"key": m.group(1)})
        if re.fullmatch(r"(what do you remember|list memory|show memory|what do you know about me)\??", t):
            return Proposal("list_memory")
        m = re.match(r"(?:start|run|activate|begin|launch|enable)\s+(?:the )?(?:routine\s+)?(.+?)(?:\s+routine)?$", t)
        if m:
            from .features import load_routines
            cand = m.group(1).replace(" mode", "")
            if any(cand == k.replace(" mode", "") for k in load_routines()):
                return Proposal("run_routine", {"name": cand})
        if re.fullmatch(r"(list|show|what) routines( do i have)?\??", t):
            return Proposal("list_routines")
        from .features import load_routines as _lr
        if t in _lr():
            return Proposal("run_routine", {"name": t})

        # --- v2: volume / media / clipboard / screenshot / folders / activity ---
        m = re.match(r"(?:set )?(?:the )?volume (?:to )?(\d{1,3})(?: ?%| percent)?$", t)
        if m:
            return Proposal("volume", {"action": "set", "level": int(m.group(1))})
        m = re.match(r"(?:turn |volume )?(?:the )?(?:volume |sound |it )?(up|down|louder|quieter|softer)(?: the volume| a bit| please)?$", t) or \
            re.match(r"(?:turn (?:it |the volume )?)(up|down)$", t)
        if m and ("volume" in t or "louder" in t or "quieter" in t or "softer" in t or "sound" in t):
            d = m.group(1)
            return Proposal("volume", {"action": "up" if d in ("up", "louder") else "down"})
        if re.fullmatch(r"(mute|unmute|mute (the )?(sound|volume|audio)|unmute (the )?(sound|volume|audio)|silence)", t):
            return Proposal("volume", {"action": "unmute" if t.startswith("un") else "mute"})
        if re.fullmatch(r"(play|pause|resume|play music|pause music|play or pause|toggle music|play pause)", t):
            return Proposal("media", {"action": "playpause"})
        if re.fullmatch(r"(next|skip|next track|next song|skip (this )?(song|track))", t):
            return Proposal("media", {"action": "next"})
        if re.fullmatch(r"(previous|previous (track|song)|go back a (song|track)|last song)", t):
            return Proposal("media", {"action": "previous"})
        if re.fullmatch(r"(stop|stop music|stop playing|stop the music)", t):
            return Proposal("media", {"action": "stop"})
        if re.search(r"\b(read|what'?s|what is|show)\b.*\b(clipboard|copied)\b", t) or t == "clipboard":
            return Proposal("read_clipboard")
        m = re.match(r"copy (?:this |the text |text )?[:\-]?\s*(.+)", raw, re.I | re.S)
        if m and "clipboard" not in m.group(1).lower():
            return Proposal("copy_to_clipboard", {"text": m.group(1)})
        if re.search(r"\b(take|capture|grab)\b.*\b(screenshot|screen shot|screen)\b", t) or t in ("screenshot", "screen shot"):
            return Proposal("screenshot")
        m = re.match(r"(?:open|show|go to)\s+(?:my )?(downloads?|documents?|desktop|pictures?|photos|music|videos?|movies|home|screenshots?)(?: folder)?$", t)
        if m:
            return Proposal("open_folder", {"name": m.group(1)})
        if re.search(r"what (did|have) you (do|done) today|activity log|show activity|today'?s activity", t):
            return Proposal("activity")

        # --- youtube / maps / wikipedia (before generic search) ---
        m = re.match(rf"(?:play|search|find|look up|open)?\s*{self._FILLER}\s*(.+?)\s+on youtube$", t) or \
            re.match(r"youtube (.+)", t)
        if m:
            return Proposal("youtube", {"query": m.group(1)})
        m = re.match(r"(?:show|find|where is|locate|navigate to|directions to)\s+(.+?)\s+on (?:the )?maps?$", t) or \
            re.match(r"(?:where is|directions to|navigate to) (.+)", t)
        if m:
            return Proposal("maps", {"query": m.group(1)})
        m = re.match(r"(?:wikipedia|wiki) (.+)", t) or re.match(r"(.+?) on wikipedia$", t)
        if m:
            return Proposal("wikipedia", {"query": m.group(1)})

        # --- websites ---
        m = re.match(r"(?:open|go to|visit|launch)\s+(?:the )?(?:website |site )?((?:https?://)?[\w\-]+(?:\.[\w\-]+)+(?:/\S*)?)$", t)
        if m:
            return Proposal("open_url", {"url": m.group(1)})

        # --- generic web search ---
        # only unambiguous verbs here; "find me a video on …" is left to the AI brain
        m = re.match(r"(?:search|google|look up|lookup|web search)\s+(?:for )?(?:the web for )?(.+)", t)
        if m and not re.match(r"(?:open|launch|start)", t):
            return Proposal("web_search", {"query": m.group(1)})
        m = re.match(r"find\s+(?!me\b|a\b|an\b|some\b)(.+)", t)      # "find python docs" yes, "find me a video" no
        if m and not self._llm_available:
            return Proposal("web_search", {"query": m.group(1)})

        # --- close app ---
        m = re.match(r"(?:close|quit|exit|kill|stop)\s+(?:the )?(?:app )?(.+)", t)
        if m and APPS.resolve(m.group(1)):
            return Proposal("close_app", {"app": APPS.resolve(m.group(1))})

        # --- open app ---
        m = re.match(r"(?:open|launch|start|run|fire up|bring up)\s+(?:the )?(?:app )?(.+)", t)
        if m:
            name = APPS.resolve(m.group(1))
            if name:
                return Proposal("open_app", {"app": name})
            # Not allowlisted: return explicit refusal from open_app (it explains)
            return Proposal("open_app", {"app": m.group(1)})

        return None


# ==========================================================================
# 2. LLM brain (free APIs / local)
# ==========================================================================

_SYSTEM_PROMPT = """You are {name}, a concise, friendly voice assistant on the user's computer (OS: {os}).
Address the user as "{user}".

You can ONLY do these actions:
{schema}

Allowed app names for open_app/close_app: {apps}

Things the user has asked you to remember (use them when relevant): {memory}
Today is {today}.

Rules:
- For multi-part requests pick the FIRST action; the user will be asked about the rest.
- For reminders, put the time phrase in params.when exactly as the user said it (e.g. "in 20 minutes", "tomorrow at 9am").
- Reply with ONE JSON object and nothing else: {{"action": "<name>", "params": {{...}}, "say": "<short sentence to speak>"}}
- If the user just wants to talk or ask a general question, use action "chat" with the answer in params.reply (keep it under 60 words).
- If the user asks for something not in the list (shutdown, delete files, run commands, change settings), use "chat" and politely say you can't do that.
- Never invent actions or parameters. Never include markdown.
"""


def _http_json(url: str, payload: dict, headers: Dict[str, str], timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310 - fixed https endpoints
        return json.loads(resp.read().decode("utf-8"))


def _http_get(url: str, headers: Dict[str, str], timeout: int) -> dict:
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


class ProviderError(RuntimeError):
    pass


class _Provider:
    name = "base"

    def complete(self, messages: List[dict]) -> str:
        raise NotImplementedError


class GroqProvider(_Provider):
    name = "groq"
    BASE = "https://api.groq.com/openai/v1"

    def __init__(self):
        if not settings.groq_api_key:
            raise ProviderError("GROQ_API_KEY not set")
        self.model = settings.groq_model
        self._headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    def _pick_model(self) -> str:
        data = _http_get(self.BASE + "/models", self._headers, settings.request_timeout)
        ids = [m["id"] for m in data.get("data", []) if m.get("active", True)]
        prefs = ("llama-3.3", "llama-3.1", "llama", "gpt-oss", "qwen", "gemma", "mixtral")
        for p in prefs:
            for i in ids:
                if p in i and "whisper" not in i and "tts" not in i and "guard" not in i:
                    return i
        if ids:
            return ids[0]
        raise ProviderError("no Groq models available")

    def complete(self, messages: List[dict]) -> str:
        payload = {"model": self.model, "messages": messages, "temperature": 0.2,
                   "max_tokens": 300, "response_format": {"type": "json_object"}}
        try:
            out = _http_json(self.BASE + "/chat/completions", payload, self._headers, settings.request_timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if e.code in (400, 404) and "model" in body.lower():
                self.model = self._pick_model()          # model was retired -> auto-switch
                payload["model"] = self.model
                out = _http_json(self.BASE + "/chat/completions", payload, self._headers, settings.request_timeout)
            else:
                raise ProviderError(
                    f"Groq HTTP {e.code}: {body}"
                ) from None
        return out["choices"][0]["message"]["content"]


class GeminiProvider(_Provider):
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        if not settings.gemini_api_key:
            raise ProviderError("GEMINI_API_KEY not set")
        self.model = settings.gemini_model
        self._headers = {"x-goog-api-key": settings.gemini_api_key}

    def _pick_model(self) -> str:
        data = _http_get(self.BASE + "/models", self._headers, settings.request_timeout)
        ids = [m["name"].split("/")[-1] for m in data.get("models", [])
               if "generateContent" in m.get("supportedGenerationMethods", [])]
        for pref in ("flash-lite", "flash"):
            for i in ids:
                if pref in i and "preview" not in i and "exp" not in i:
                    return i
        for i in ids:
            if "flash" in i:
                return i
        if ids:
            return ids[0]
        raise ProviderError("no Gemini models available")

    def _call(self, messages: List[dict]) -> str:
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                    for m in messages if m["role"] != "system"]
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300,
                                 "responseMimeType": "application/json"},
        }
        url = f"{self.BASE}/models/{self.model}:generateContent"
        out = _http_json(url, payload, self._headers, settings.request_timeout)
        return out["candidates"][0]["content"]["parts"][0]["text"]

    def complete(self, messages: List[dict]) -> str:
        try:
            return self._call(messages)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.model = self._pick_model()
                return self._call(messages)
            raise ProviderError(f"Gemini HTTP {e.code}") from None


class OllamaProvider(_Provider):
    name = "ollama"

    def __init__(self):
        self.model = settings.ollama_model
        self.base = settings.ollama_url.rstrip("/")
        try:
            tags = _http_get(self.base + "/api/tags", {}, 3)
        except Exception as e:  # noqa: BLE001
            raise ProviderError("Ollama not reachable") from e
        names = [m["name"] for m in tags.get("models", [])]
        if names and not any(n.startswith(self.model) for n in names):
            self.model = names[0]

    def complete(self, messages: List[dict]) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False, "format": "json",
                   "options": {"temperature": 0.2}}
        try:
            out = _http_json(self.base + "/api/chat", payload, {}, max(settings.request_timeout, 90))
        except urllib.error.HTTPError as e:
            raise ProviderError(f"Ollama HTTP {e.code}") from None
        return out["message"]["content"]


def _build_provider() -> Optional[_Provider]:
    order = {
        "auto": [GroqProvider, GeminiProvider, OllamaProvider],
        "groq": [GroqProvider], "gemini": [GeminiProvider], "ollama": [OllamaProvider], "offline": [],
    }.get(settings.provider, [GroqProvider, GeminiProvider, OllamaProvider])
    for cls in order:
        try:
            return cls()
        except ProviderError:
            continue
    return None


class LLMBrain:
    def __init__(self):
        self.provider = _build_provider()
        self.history: List[dict] = []
        self._base_system = _SYSTEM_PROMPT

    @property
    def system(self) -> str:
        from .features import MEMORY
        import datetime as _dt

        return self._base_system.format(
            name=settings.assistant_name, os=settings.os_name, user=settings.user_name,
            schema=schema_for_llm(), apps=", ".join(APPS.names()),
            memory=MEMORY.summary_for_prompt() or "nothing yet",
            today=_dt.datetime.now().strftime("%A %d %B %Y, %H:%M"),
        )

    @property
    def available(self) -> bool:
        return self.provider is not None

    @property
    def label(self) -> str:
        if not self.provider:
            return "offline (rules only)"
        return f"{self.provider.name} / {self.provider.model}"

    def think(self, text: str) -> Optional[Proposal]:
        if not self.provider:
            return None
        messages = [{"role": "system", "content": self.system}] + self.history[-6:] + \
                   [{"role": "user", "content": text[:1000]}]
        try:
            raw = self.provider.complete(messages)
        except Exception as e:
            print(f"\n[AI ERROR] {type(e).__name__}: {e}\n")
            return None
        obj = _extract_json(raw)
        if not isinstance(obj, dict) or not isinstance(obj.get("action"), str):
            return None
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        say = obj.get("say") if isinstance(obj.get("say"), str) else ""
        self.history += [{"role": "user", "content": text[:1000]},
                         {"role": "assistant", "content": json.dumps(obj)[:1000]}]
        return Proposal(obj["action"], params, say, source=self.provider.name)


# ==========================================================================
# Combined
# ==========================================================================


class Brain:
    def __init__(self):
        self.llm = LLMBrain()
        self.rules = RuleParser(llm_available=self.llm.available)

    def split(self, text: str) -> List[str]:
        """'open chrome and play music then tell me the weather' -> 3 commands.
        Only splits when every part is itself a recognised command, so chatty
        sentences containing 'and' are left intact."""
        parts = [p.strip() for p in re.split(r"\s*(?:,?\s+and then\s+|,?\s+then\s+|,?\s+and\s+(?=(?:open|close|launch|start|play|pause|search|google|set|remind|tell|what|take|mute|unmute|turn|volume|note|lock|show|read|copy|run)\b)|;\s*)", text) if p.strip()]
        if len(parts) <= 1 or len(parts) > 5:
            return [text]
        if all(self.rules.parse(p) is not None for p in parts):
            return parts
        return [text]

    def decide(self, text: str) -> Proposal:
        p = self.rules.parse(text)
        if p is not None:
            return p
        p = self.llm.think(text)
        if p is not None:
            return p
        if self.llm.available:
            return Proposal("chat", {"reply": "I didn't catch that. Could you rephrase?"})
        return Proposal("chat", {"reply": (
            "I'm running in offline mode, so I only understand direct commands. "
            "Add a free GROQ_API_KEY or GEMINI_API_KEY to .env (or run Ollama) to enable conversation. "
            "Say 'help' for what I can do.")})
