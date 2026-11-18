"""Safe action layer.

Every capability Jarvis has lives here as a small, explicit function.
The LLM (or the offline parser) can only pick from ``REGISTRY``; it
cannot invent new commands, run shell strings, touch arbitrary files,
or shut the machine down. 

Rules enforced by ``SafetyGate``:
  * action must exist in REGISTRY 
  * parameters are type-checked and length-limited
  * apps must be in apps.json (the allowlist)
  * URLs must be http(s) only 
  * "risky" actions (closing apps, locking screen) require confirmation
  * nothing is ever passed through a shell with user-controlled text
""" 
 
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import threading
import webbrowser
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from .config import APPS_FILE, NOTES_FILE, settings

# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------


@dataclass
class ActionResult:
    ok: bool
    message: str          # what Jarvis says back
    data: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# App allowlist
# --------------------------------------------------------------------------


class AppRegistry:
    def __init__(self, path=APPS_FILE):
        self.path = path
        self.apps: Dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        self.apps = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for name, spec in raw.items():
            if name.startswith("_") or not isinstance(spec, dict):
                continue
            self.apps[name.lower()] = spec

    def names(self) -> List[str]:
        return sorted(self.apps)

    def resolve(self, spoken: str) -> Optional[str]:
        """Map what the user said to a canonical allowlisted app name."""
        s = re.sub(r"[^a-z0-9 ]", "", spoken.lower()).strip()
        if not s:
            return None
        if s in self.apps:
            return s
        for name, spec in self.apps.items():
            aliases = [a.lower() for a in spec.get("aliases", [])]
            if s in aliases:
                return name
        # loose match: "the chrome app" -> chrome, but only if the leftover words are harmless fillers
        fillers = {"the", "a", "an", "app", "application", "please", "up", "now", "my", "for", "me", "jarvis"}
        for name, spec in self.apps.items():
            candidates = [name] + [a.lower() for a in spec.get("aliases", [])]
            for c in candidates:
                if re.search(rf"\b{re.escape(c)}\b", s):
                    leftover = set(re.sub(rf"\b{re.escape(c)}\b", " ", s).split())
                    if leftover <= fillers:
                        return name
        # typo tolerance: "chrom", "spotfy", "calcultor"
        import difflib
        core = " ".join(w for w in s.split() if w not in fillers)
        pool = {}
        for name, spec in self.apps.items():
            for c in [name] + [a.lower() for a in spec.get("aliases", [])]:
                pool[c] = name
        close = difflib.get_close_matches(core, list(pool), n=1, cutoff=0.8)
        return pool[close[0]] if close else None

    def target_for(self, name: str) -> Optional[str]:
        spec = self.apps.get(name)
        if not spec:
            return None
        return spec.get(settings.os_name) or spec.get("default")


APPS = AppRegistry()


# --------------------------------------------------------------------------
# Launch helpers (no shell=True anywhere)
# --------------------------------------------------------------------------


def _launch(target: str) -> None:
    system = settings.os_name
    if system == "Darwin":
        subprocess.Popen(["open", "-a", target],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    if system == "Windows":
        exe = shutil.which(target)
        if exe:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            return
        # URI schemes (ms-settings:, whatsapp:) and App-Paths registered names
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - target is allowlisted
        return

    # Linux / other: target may carry fixed flags from apps.json (e.g. "libreoffice --calc")
    argv = shlex.split(target)
    if not shutil.which(argv[0]):
        raise FileNotFoundError(argv[0])
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def _process_names(name: str) -> List[str]:
    """Executable names to use when closing an app (derived from allowlist target)."""
    target = APPS.target_for(name) or name
    base = shlex.split(target)[0] if settings.os_name != "Darwin" else target
    base = os.path.basename(base).rstrip(":")
    out = [base]
    if settings.os_name == "Windows" and not base.lower().endswith(".exe"):
        out.append(base + ".exe")
    return out


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

_MAX_TEXT = 500


def _clean(text: Any, limit: int = _MAX_TEXT) -> str:
    if text is None:
        return ""
    text = str(text).strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text[:limit]


def act_open_app(app: str = "", **_) -> ActionResult:
    name = APPS.resolve(_clean(app, 100))
    if not name:
        return ActionResult(False,
                            f"'{_clean(app, 60)}' is not on my allowed apps list. "
                            f"Add it to apps.json if you want me to open it. "
                            f"I can open: {', '.join(APPS.names())}.")
    target = APPS.target_for(name)
    if not target:
        return ActionResult(False, f"{name} has no launch target for {settings.os_name}.")
    try:
        _launch(target)
    except FileNotFoundError:
        return ActionResult(False, f"{name} doesn't seem to be installed here ({target} not found).")
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"I couldn't open {name}: {e.__class__.__name__}.")
    return ActionResult(True, f"Opening {name}.", {"app": name})


def act_close_app(app: str = "", **_) -> ActionResult:
    name = APPS.resolve(_clean(app, 100))
    if not name:
        return ActionResult(False, f"'{_clean(app, 60)}' is not on my allowed apps list, so I won't touch it.")
    procs = _process_names(name)
    try:
        if settings.os_name == "Windows":
            for p in procs:
                subprocess.run(["taskkill", "/IM", p, "/F"], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif settings.os_name == "Darwin":
            subprocess.run(["osascript", "-e", f'tell application "{procs[0]}" to quit'],
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            for p in procs:
                subprocess.run(["pkill", "-x", p], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"Couldn't close {name}: {e.__class__.__name__}.")
    return ActionResult(True, f"Closed {name}.", {"app": name})


_SEARCH_ENGINES = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "brave": "https://search.brave.com/search?q={q}",
}


def _open_in_browser(url: str) -> bool:
    try:
        return webbrowser.open(url, new=2)
    except Exception:  # noqa: BLE001
        return False


def act_web_search(query: str = "", **_) -> ActionResult:
    q = _clean(query, 300)
    if not q:
        return ActionResult(False, "What should I search for?")
    tmpl = _SEARCH_ENGINES.get(settings.search_engine, _SEARCH_ENGINES["duckduckgo"])
    url = tmpl.format(q=quote_plus(q))
    _open_in_browser(url)
    return ActionResult(True, f"Searching the web for {q}.", {"url": url})


def act_youtube(query: str = "", **_) -> ActionResult:
    q = _clean(query, 300)
    if not q:
        return ActionResult(False, "What should I look for on YouTube?")
    url = f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    _open_in_browser(url)
    return ActionResult(True, f"Searching YouTube for {q}.", {"url": url})


def act_maps(query: str = "", **_) -> ActionResult:
    q = _clean(query, 300)
    if not q:
        return ActionResult(False, "Where should I look on the map?")
    url = f"https://www.google.com/maps/search/{quote_plus(q)}"
    _open_in_browser(url)
    return ActionResult(True, f"Showing {q} on the map.", {"url": url})


def act_wikipedia(query: str = "", **_) -> ActionResult:
    q = _clean(query, 200)
    if not q:
        return ActionResult(False, "What should I look up on Wikipedia?")
    url = f"https://en.wikipedia.org/w/index.php?search={quote_plus(q)}"
    _open_in_browser(url)
    return ActionResult(True, f"Looking up {q} on Wikipedia.", {"url": url})


def act_open_url(url: str = "", **_) -> ActionResult:
    u = _clean(url, 2000)
    if not u:
        return ActionResult(False, "Which website?")
    if "://" not in u:
        u = "https://" + u
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ActionResult(False, "I only open http or https links.")
    if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", parsed.netloc):
        return ActionResult(False, "That doesn't look like a valid website address.")
    _open_in_browser(u)
    return ActionResult(True, f"Opening {parsed.netloc}.", {"url": u})


def act_time(**_) -> ActionResult:
    now = _dt.datetime.now()
    return ActionResult(True, f"It's {now.strftime('%I:%M %p').lstrip('0')}.", {"iso": now.isoformat()})


def act_date(**_) -> ActionResult:
    now = _dt.datetime.now()
    return ActionResult(True, f"Today is {now.strftime('%A, %d %B %Y')}.", {"iso": now.date().isoformat()})


def act_system_status(**_) -> ActionResult:
    parts = [f"{platform.system()} {platform.release()}"]
    try:
        import psutil  # optional

        parts.append(f"CPU at {psutil.cpu_percent(interval=0.5):.0f}%")
        mem = psutil.virtual_memory()
        parts.append(f"memory {mem.percent:.0f}% used")
        bat = psutil.sensors_battery()
        if bat is not None:
            state = "charging" if bat.power_plugged else "on battery"
            parts.append(f"battery {bat.percent:.0f}% {state}")
    except ImportError:
        parts.append("(install psutil for CPU, memory and battery details)")
    except Exception:  # noqa: BLE001
        pass
    try:
        du = shutil.disk_usage(os.path.expanduser("~"))
        parts.append(f"disk {du.free / 1e9:.0f} GB free of {du.total / 1e9:.0f} GB")
    except Exception:  # noqa: BLE001
        pass
    return ActionResult(True, "System status: " + ", ".join(parts) + ".")


def act_take_note(text: str = "", **_) -> ActionResult:
    t = _clean(text, 2000)
    if not t:
        return ActionResult(False, "What should I note down?")
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {t}\n")
    return ActionResult(True, "Noted.", {"file": str(NOTES_FILE)})


def act_read_notes(**_) -> ActionResult:
    if not NOTES_FILE.exists():
        return ActionResult(True, "You have no notes yet.")
    lines = NOTES_FILE.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        return ActionResult(True, "You have no notes yet.")
    recent = lines[-5:]
    return ActionResult(True, f"Your last {len(recent)} notes: " + " | ".join(recent), {"count": len(lines)})


_timers: List[threading.Timer] = []
_timer_callback: Optional[Callable[[str], None]] = None


def set_timer_callback(cb: Callable[[str], None]) -> None:
    global _timer_callback
    _timer_callback = cb


def act_set_timer(seconds: int = 0, label: str = "", **_) -> ActionResult:
    try:
        secs = int(float(seconds))
    except (TypeError, ValueError):
        return ActionResult(False, "How long should the timer be?")
    if secs <= 0 or secs > 24 * 3600:
        return ActionResult(False, "Timers must be between 1 second and 24 hours.")
    lbl = _clean(label, 100) or "your timer"

    def fire():
        msg = f"Time's up: {lbl}."
        if _timer_callback:
            _timer_callback(msg)
        else:
            print("\n" + msg)

    t = threading.Timer(secs, fire)
    t.daemon = True
    t.start()
    _timers.append(t)
    mins, s = divmod(secs, 60)
    human = f"{mins} minute{'s' if mins != 1 else ''}" if mins else f"{s} second{'s' if s != 1 else ''}"
    if mins and s:
        human += f" {s} seconds"
    return ActionResult(True, f"Timer set for {human} ({lbl}).", {"seconds": secs})


def act_lock_screen(**_) -> ActionResult:
    try:
        if settings.os_name == "Windows":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
        elif settings.os_name == "Darwin":
            subprocess.run(["pmset", "displaysleepnow"], check=False)
        else:
            for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]):
                if shutil.which(cmd[0]):
                    subprocess.run(cmd, check=False)
                    break
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"Couldn't lock the screen: {e.__class__.__name__}.")
    return ActionResult(True, "Locking the screen.")


def act_list_apps(**_) -> ActionResult:
    return ActionResult(True, "I can open: " + ", ".join(APPS.names()) + ". Edit apps.json to add more.")


def act_help(**_) -> ActionResult:
    return ActionResult(True, (
        "I can: open or close allowed apps; control volume and music; take screenshots; read or set the clipboard; "
        "open your standard folders; search the web, YouTube, Maps and Wikipedia; give weather, news, currency rates, "
        "definitions, spellings, Wikipedia summaries and translations; do maths and unit conversions; set timers and "
        "reminders; take notes; remember personal facts; run routines like 'good morning'; report system status; "
        "lock the screen; and chat. You can chain commands with 'and' or 'then'. Say 'list apps' or 'list routines'."
    ))


def act_chat(reply: str = "", **_) -> ActionResult:
    return ActionResult(True, _clean(reply, 2000) or "I'm here.")


# --------------------------------------------------------------------------
# Registry (the ONLY things Jarvis can do) and schema for the LLM
# --------------------------------------------------------------------------


@dataclass
class ActionSpec:
    fn: Callable[..., ActionResult]
    params: Dict[str, type]
    description: str
    risky: bool = False   # requires confirmation when settings.confirm_actions


REGISTRY: Dict[str, ActionSpec] = {
    "open_app":      ActionSpec(act_open_app, {"app": str}, "Open an installed application by name.", False),
    "close_app":     ActionSpec(act_close_app, {"app": str}, "Close/quit an application by name.", True),
    "web_search":    ActionSpec(act_web_search, {"query": str}, "Search the web in the browser.", False),
    "youtube":       ActionSpec(act_youtube, {"query": str}, "Search YouTube.", False),
    "maps":          ActionSpec(act_maps, {"query": str}, "Find a place on Google Maps.", False),
    "wikipedia":     ActionSpec(act_wikipedia, {"query": str}, "Look something up on Wikipedia.", False),
    "open_url":      ActionSpec(act_open_url, {"url": str}, "Open a website (http/https only).", False),
    "time":          ActionSpec(act_time, {}, "Tell the current time.", False),
    "date":          ActionSpec(act_date, {}, "Tell today's date.", False),
    "system_status": ActionSpec(act_system_status, {}, "Report CPU, memory, battery and disk.", False),
    "take_note":     ActionSpec(act_take_note, {"text": str}, "Save a note.", False),
    "read_notes":    ActionSpec(act_read_notes, {}, "Read back recent notes.", False),
    "set_timer":     ActionSpec(act_set_timer, {"seconds": int, "label": str}, "Set a countdown timer.", False),
    "lock_screen":   ActionSpec(act_lock_screen, {}, "Lock the computer screen.", True),
    "list_apps":     ActionSpec(act_list_apps, {}, "List the apps Jarvis is allowed to open.", False),
    "help":          ActionSpec(act_help, {}, "Explain what Jarvis can do.", False),
    "chat":          ActionSpec(act_chat, {"reply": str}, "Just reply conversationally (no device action).", False),
}

# Things people may ask for that Jarvis deliberately refuses.
# Patterns are matched against the *command* (leading fillers removed), so
# "search how to delete a file" is allowed but "delete my files" is not.
_FILLER_PREFIX = r"^(?:(?:hey|ok|okay|hi|please|can you|could you|would you|i want you to|jarvis|,)\s+)*"
FORBIDDEN_PATTERNS = [
    r"(shut ?down|power ?off|turn off)(?! (?:the )?(?:timer|music|tts|voice|speech|lights?))\b",
    r"(restart|reboot)(?: the| my)? (pc|computer|laptop|system|machine|windows|mac)\b",
    r"(delete|remove|erase|wipe|format)\b",
    r"(run|execute)\b.*\b(command|script|powershell|cmd|bash)\b",
    r"(disable|turn off|stop)\b.*\b(antivirus|firewall|defender|security|protection)\b",
    r"uninstall\b",
    r"(sudo|rm -rf|format c|reg delete|del /|rmdir)\b",
]
_RESEARCH_PREFIX = re.compile(r"^(?:search|google|look ?up|find|wiki|wikipedia|youtube|how (?:do|can|to)|what|why|explain)\b")


def is_forbidden(text: str) -> Optional[str]:
    t = re.sub(_FILLER_PREFIX, "", text.lower().strip())
    if _RESEARCH_PREFIX.match(t):
        return None   # asking *about* something dangerous is fine; doing it is not
    for pat in FORBIDDEN_PATTERNS:
        if re.match(pat, t) or re.search(r"\b(sudo|rm -rf|format c:|reg delete|del /[a-z])", t):
            return ("I won't do that. Shutting down, deleting files, running raw commands, "
                    "or touching security settings is outside what I'm allowed to do — "
                    "that's a deliberate safety boundary.")
    return None


def schema_for_llm() -> str:
    lines = []
    for name, spec in REGISTRY.items():
        params = ", ".join(f"{k}: {v.__name__}" for k, v in spec.params.items()) or "none"
        lines.append(f'- "{name}" ({params}): {spec.description}')
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Safety gate + executor
# --------------------------------------------------------------------------


class SafetyGate:
    """Validates a proposed action before anything runs."""

    MAX_ACTIONS_PER_MINUTE = 30

    def __init__(self):
        self._recent: List[float] = []

    def _rate_limited(self) -> bool:
        import time

        now = time.time()
        self._recent = [t for t in self._recent if now - t < 60]
        if len(self._recent) >= self.MAX_ACTIONS_PER_MINUTE:
            return True
        self._recent.append(now)
        return False

    def validate(self, action: str, params: Dict[str, Any]) -> Optional[str]:
        if action not in REGISTRY:
            return f"Unknown action '{_clean(action, 40)}' — refused."
        if action != "chat" and self._rate_limited():
            return "Too many actions in one minute — slowing down for safety."
        spec = REGISTRY[action]
        for key in list(params):
            if key not in spec.params:
                params.pop(key)   # drop anything the LLM invented
        for key, typ in spec.params.items():
            if key in params and params[key] is not None:
                try:
                    params[key] = typ(params[key])
                except (TypeError, ValueError):
                    return f"Bad value for '{key}'."
                if typ is str and len(params[key]) > 2000:
                    return f"'{key}' is too long."
        return None

    def needs_confirmation(self, action: str) -> bool:
        return settings.confirm_actions and REGISTRY[action].risky


def execute(action: str, params: Dict[str, Any]) -> ActionResult:
    spec = REGISTRY[action]
    try:
        return spec.fn(**params)
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"That failed: {e.__class__.__name__}.")


# v2 features register themselves into REGISTRY (import kept at the bottom to avoid a cycle)
from . import features  # noqa: E402,F401
