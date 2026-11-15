"""Jarvis — a secure, AI-powered personal desktop assistant.

Design principles
-----------------
* Allowlist-only actions: Jarvis can only run the handful of actions
  defined in ``actions.py``. There is no "run arbitrary shell command".
* The LLM never executes anything. It only *proposes* a structured
  action (JSON); the local ``SafetyGate`` validates it before execution.
* Apps are launched only if they are in ``apps.json`` (the allowlist).
* Nothing is ever deleted, modified, or downloaded without confirmation.
* API keys live in ``.env`` and are never logged or sent anywhere
  except the provider that owns them.
"""

__version__ = "2.0.0"
