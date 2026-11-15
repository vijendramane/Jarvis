"""Configuration loading. Reads a .env file (no third-party deps) and
exposes typed settings. Secrets are never printed."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
APPS_FILE = ROOT / "apps.json"
DATA_DIR = Path.home() / ".jarvis"
LOG_FILE = DATA_DIR / "jarvis.log"
NOTES_FILE = DATA_DIR / "notes.txt"
HISTORY_FILE = DATA_DIR / "history.jsonl"


def _load_env(path: Path) -> None:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes.
    Values already present in the real environment are not overridden."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env(ENV_FILE)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name, "").lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


@dataclass
class Settings:
    # Which brain to use: auto | groq | gemini | ollama | offline
    provider: str = field(default_factory=lambda: _env("JARVIS_PROVIDER", "auto").lower())
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "llama-3.3-70b-versatile"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", "llama3.2"))

    assistant_name: str = field(default_factory=lambda: _env("JARVIS_NAME", "Jarvis"))
    user_name: str = field(default_factory=lambda: _env("JARVIS_USER", "sir"))
    wake_word: str = field(default_factory=lambda: _env("JARVIS_WAKE_WORD", "jarvis").lower())

    voice_enabled: bool = field(default_factory=lambda: _env_bool("JARVIS_VOICE", False))
    tts_enabled: bool = field(default_factory=lambda: _env_bool("JARVIS_TTS", True))
    confirm_actions: bool = field(default_factory=lambda: _env_bool("JARVIS_CONFIRM", True))
    search_engine: str = field(default_factory=lambda: _env("JARVIS_SEARCH", "duckduckgo").lower())
    request_timeout: int = field(default_factory=lambda: int(_env("JARVIS_TIMEOUT", "30") or 30))

    default_city: str = field(default_factory=lambda: _env("JARVIS_CITY"))
    country: str = field(default_factory=lambda: _env("JARVIS_COUNTRY", "US"))

    os_name: str = field(default_factory=lambda: platform.system())  # Windows / Darwin / Linux

    def ensure_dirs(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def redacted(self) -> dict:
        """Settings safe to print (keys masked)."""
        d = self.__dict__.copy()
        for k in ("groq_api_key", "gemini_api_key"):
            v = d.get(k) or ""
            d[k] = (v[:4] + "…" + v[-2:]) if len(v) > 8 else ("set" if v else "not set")
        return d


settings = Settings()
settings.ensure_dirs()
