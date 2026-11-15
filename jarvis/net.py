"""Tiny HTTPS helper for the keyless public APIs Jarvis uses.

Only ``https://`` hosts from ``ALLOWED_HOSTS`` can be contacted — a
compromised model or a typo cannot make Jarvis call anywhere else.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from .config import settings

ALLOWED_HOSTS = {
    "geocoding-api.open-meteo.com", "api.open-meteo.com",      # weather (no key)
    "api.frankfurter.app", "api.frankfurter.dev",               # currency, ECB rates (no key)
    "api.dictionaryapi.dev",                                    # dictionary (no key)
    "en.wikipedia.org",                                         # summaries (no key)
    "news.google.com", "feeds.bbci.co.uk",                      # RSS headlines (no key)
    "api.mymemory.translated.net",                              # translation (no key)
    "api.groq.com", "generativelanguage.googleapis.com",        # LLM providers
}

_UA = f"Jarvis/2.0 (personal desktop assistant; +https://github.com/) Python-urllib"
_cache: Dict[str, tuple[float, Any]] = {}


class NetError(RuntimeError):
    pass


def _check(url: str) -> None:
    p = urllib.parse.urlparse(url)
    host = p.hostname or ""
    if p.scheme != "https" or (host not in ALLOWED_HOSTS and not host.startswith("localhost")):
        raise NetError(f"blocked host: {host}")


def get(url: str, headers: Optional[Dict[str, str]] = None, timeout: Optional[int] = None,
        cache_ttl: int = 0) -> bytes:
    _check(url)
    now = time.time()
    if cache_ttl and url in _cache and now - _cache[url][0] < cache_ttl:
        return _cache[url][1]
    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout or settings.request_timeout, context=ctx) as r:  # noqa: S310
            data = r.read()
    except Exception as e:  # noqa: BLE001
        raise NetError(e.__class__.__name__) from e
    if cache_ttl:
        _cache[url] = (now, data)
    return data


def get_json(url: str, **kw) -> Any:
    return json.loads(get(url, **kw).decode("utf-8", "replace"))


def q(**params) -> str:
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
