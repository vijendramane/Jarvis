"""Persistent, local-only stores: personal memory and reminders.
Everything lives in ~/.jarvis/ as plain JSON you can read or delete."""

from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import DATA_DIR

MEMORY_FILE = DATA_DIR / "memory.json"
REMINDERS_FILE = DATA_DIR / "reminders.json"
_lock = threading.Lock()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------
# Memory: "remember that my car number is MH12AB1234" -> {"car number": "MH12AB1234"}
# --------------------------------------------------------------------------


class Memory:
    MAX_ITEMS = 200

    def __init__(self):
        with _lock:
            self.data: Dict[str, str] = _load(MEMORY_FILE, {})

    @staticmethod
    def _norm(key: str) -> str:
        key = key.lower().strip()
        key = re.sub(r"^(my|the|our)\s+", "", key)
        return re.sub(r"[^a-z0-9 ]", "", key).strip()

    def remember(self, key: str, value: str) -> str:
        k = self._norm(key)
        if not k or not value.strip():
            return ""
        with _lock:
            self.data[k] = value.strip()[:300]
            if len(self.data) > self.MAX_ITEMS:
                self.data.pop(next(iter(self.data)))
            _save(MEMORY_FILE, self.data)
        return k

    def recall(self, key: str) -> Optional[tuple[str, str]]:
        k = self._norm(key)
        if k in self.data:
            return k, self.data[k]
        close = difflib.get_close_matches(k, list(self.data), n=1, cutoff=0.6)
        if close:
            return close[0], self.data[close[0]]
        for stored in self.data:            # substring either way
            if k and (k in stored or stored in k):
                return stored, self.data[stored]
        return None

    def forget(self, key: str) -> bool:
        hit = self.recall(key)
        if not hit:
            return False
        with _lock:
            self.data.pop(hit[0], None)
            _save(MEMORY_FILE, self.data)
        return True

    def all(self) -> Dict[str, str]:
        return dict(self.data)

    def summary_for_prompt(self, limit: int = 25) -> str:
        items = list(self.data.items())[-limit:]
        return "; ".join(f"{k}: {v}" for k, v in items)


# --------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------

_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
             "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60, "half": 0.5}


def parse_when(text: str, now: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """Understand 'in 20 minutes', 'at 5pm', 'at 17:30', 'tomorrow at 9',
    'tonight', 'this evening', ISO timestamps. Returns a future datetime or None."""
    now = now or dt.datetime.now()
    t = text.lower().strip()

    try:  # ISO (what an LLM may hand us)
        iso = dt.datetime.fromisoformat(t.replace("z", ""))
        return iso if iso > now else None
    except ValueError:
        pass

    t = re.sub(r"\bhalf an hour\b", "30 minutes", t)
    t = re.sub(r"\ban hour and a half\b", "90 minutes", t)
    t = re.sub(r"\bquarter of an hour\b", "15 minutes", t)
    m = re.search(r"in\s+(\d+(?:\.\d+)?|[a-z\-]+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", t)
    if m:
        n = _WORD_NUM.get(m.group(1))
        n = float(m.group(1)) if n is None and re.match(r"[\d.]+$", m.group(1)) else n
        if n is not None:
            u = m.group(2)
            secs = n * (1 if u.startswith("s") else 60 if u.startswith("m") else 3600 if u.startswith("h") else 86400)
            return now + dt.timedelta(seconds=secs)

    day = now.date()
    if "tomorrow" in t:
        day = day + dt.timedelta(days=1)
    elif "day after tomorrow" in t:
        day = day + dt.timedelta(days=2)

    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b(?!\s*(?:minutes?|mins?|hours?|seconds?|days?))", t)
    hour = minute = None
    if m and (m.group(2) or m.group(3) or re.search(r"\bat\s+" + re.escape(m.group(0).strip()), t)):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ap = (m.group(3) or "").replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        elif not ap and hour <= 7 and "morning" not in t:
            hour += 12          # "at 5" usually means 17:00 for a reminder
    elif "noon" in t:
        hour, minute = 12, 0
    elif "midnight" in t:
        hour, minute = 0, 0
    elif "morning" in t:
        hour, minute = 9, 0
    elif "afternoon" in t:
        hour, minute = 15, 0
    elif "evening" in t or "tonight" in t:
        hour, minute = 19, 0
    if hour is None or not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    when = dt.datetime.combine(day, dt.time(hour, minute))
    if when <= now and "tomorrow" not in t:
        when += dt.timedelta(days=1)
    return when


class Reminders:
    def __init__(self):
        with _lock:
            self.items: List[dict] = _load(REMINDERS_FILE, [])
        self._cb: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, callback: Callable[[str], None]) -> None:
        self._cb = callback
        if not self._thread:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def add(self, text: str, when: dt.datetime) -> dict:
        item = {"id": int(dt.datetime.now().timestamp() * 1000), "text": text[:300],
                "when": when.isoformat(timespec="minutes"), "done": False}
        with _lock:
            self.items.append(item)
            _save(REMINDERS_FILE, self.items)
        return item

    def pending(self) -> List[dict]:
        return sorted((i for i in self.items if not i["done"]), key=lambda i: i["when"])

    def cancel_all(self) -> int:
        with _lock:
            n = sum(1 for i in self.items if not i["done"])
            self.items = [i for i in self.items if i["done"]]
            _save(REMINDERS_FILE, self.items)
        return n

    def _loop(self):
        import time

        while True:
            now = dt.datetime.now()
            due = []
            with _lock:
                for i in self.items:
                    if not i["done"] and dt.datetime.fromisoformat(i["when"]) <= now:
                        i["done"] = True
                        due.append(i)
                if due:
                    _save(REMINDERS_FILE, self.items)
            for i in due:
                if self._cb:
                    self._cb(f"Reminder: {i['text']}")
            time.sleep(5)


def human_when(when: dt.datetime, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now()
    delta = when - now
    if delta.total_seconds() < 3600:
        m = max(1, round(delta.total_seconds() / 60))
        return f"in {m} minute{'s' if m != 1 else ''}"
    day = "today" if when.date() == now.date() else "tomorrow" if when.date() == now.date() + dt.timedelta(days=1) \
        else when.strftime("%A %d %b")
    return f"{day} at {when.strftime('%I:%M %p').lstrip('0')}"
