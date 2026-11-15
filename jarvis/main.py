"""Jarvis entry point.

    python -m jarvis            # text mode
    python -m jarvis --voice    # voice mode (needs mic + optional libs)
    python -m jarvis --once "open chrome"
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from typing import Optional

from . import __version__
from .actions import SafetyGate, execute, is_forbidden, set_timer_callback
from .brain import Brain, Proposal
from .config import HISTORY_FILE, settings
from .features import REMINDERS, set_routine_runner
from .voice import Listener, Speaker, strip_wake_word


class Jarvis:
    def __init__(self, voice: bool = False, tts: Optional[bool] = None):
        self.name = settings.assistant_name
        self.brain = Brain()
        self.gate = SafetyGate()
        self.speaker = Speaker(enabled=settings.tts_enabled if tts is None else tts)
        self.listener = Listener() if voice else None
        self.voice_mode = bool(self.listener and self.listener.available)
        set_timer_callback(self.say)
        REMINDERS.start(self.say)                 # background reminder announcer
        set_routine_runner(self._handle_one)      # routines re-enter the same safe pipeline
        self.on_reply = None                      # optional hook used by the GUI

    # ---- output -----------------------------------------------------------

    def say(self, text: str) -> None:
        print(f"{self.name}: {text}")
        self.speaker.say(text)
        if self.on_reply:
            try:
                self.on_reply(text)
            except Exception:  # noqa: BLE001
                pass

    def _log(self, text: str, proposal: Proposal, ok: bool, message: str) -> None:
        try:
            with HISTORY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                    "input": text[:300], "action": proposal.action,
                    "params": {k: str(v)[:100] for k, v in proposal.params.items()},
                    "source": proposal.source, "ok": ok, "reply": message[:300],
                }) + "\n")
        except OSError:
            pass

    # ---- confirmation -------------------------------------------------------

    def _confirm(self, prompt: str) -> bool:
        self.say(prompt + " Say yes to confirm.")
        if self.voice_mode:
            heard = self.listener.listen(timeout=6) or ""
            answer = heard.lower()
        else:
            try:
                answer = input("  confirm (yes/no)> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
        return answer in ("y", "yes", "yeah", "yep", "sure", "do it", "confirm", "ok", "okay")

    # ---- core ---------------------------------------------------------------

    def handle(self, text: str) -> str:
        """Handle one user utterance; may contain several commands joined by 'and'/'then'."""
        text = text.strip()
        if not text:
            return ""
        replies = [self._handle_one(part) for part in self.brain.split(text)]
        return " ".join(r for r in replies if r)

    def _handle_one(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        refusal = is_forbidden(text)
        if refusal:
            self.say(refusal)
            self._log(text, Proposal("refused"), False, refusal)
            return refusal

        proposal = self.brain.decide(text)
        err = self.gate.validate(proposal.action, proposal.params)
        if err:
            self.say(err)
            self._log(text, proposal, False, err)
            return err

        if self.gate.needs_confirmation(proposal.action):
            what = proposal.params.get("app") or proposal.action.replace("_", " ")
            if not self._confirm(f"Are you sure you want me to {proposal.action.replace('_', ' ')} {what}?"):
                msg = "Cancelled."
                self.say(msg)
                self._log(text, proposal, False, msg)
                return msg

        result = execute(proposal.action, proposal.params)
        message = result.message
        if proposal.say and proposal.action != "chat" and result.ok:
            message = proposal.say if proposal.say.strip().lower() != message.strip().lower() else message
        self.say(message)
        self._log(text, proposal, result.ok, message)
        return message

    # ---- loops --------------------------------------------------------------

    def banner(self) -> None:
        print(f"{self.name} v{__version__} — {settings.os_name} — brain: {self.brain.llm.label}")
        print(f"voice input: {'on' if self.voice_mode else 'off'} | "
              f"speech output: {'on' if self.speaker.available else 'off'} | "
              f"confirm risky actions: {'on' if settings.confirm_actions else 'off'}")
        if self.voice_mode:
            print(f"Say \"{settings.wake_word} ...\" followed by a command. Ctrl+C to quit.")
        else:
            print("Type a command (e.g. 'open chrome', 'search python tutorials'). 'quit' to exit.")
        print()

    def run_text(self) -> None:
        self.banner()
        self.say(f"Online and ready, {settings.user_name}.")
        while True:
            try:
                text = input("you> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.strip().lower() in ("quit", "exit", "bye", "goodbye"):
                self.say("Goodbye.")
                break
            self.handle(text)

    def run_voice(self) -> None:
        self.banner()
        if not self.voice_mode:
            self.say("No microphone or speech library found. Falling back to text mode.")
            return self.run_text()
        self.say(f"Listening, {settings.user_name}.")
        awake_until = 0.0
        import time

        while True:
            try:
                heard = self.listener.listen()
            except KeyboardInterrupt:
                print()
                break
            if heard is None:
                self.say("Speech recognition is unavailable right now. Switching to text mode.")
                return self.run_text()
            if not heard:
                continue
            print(f"you (heard)> {heard}")
            now = time.time()
            cmd = strip_wake_word(heard)
            if cmd is None:
                if now < awake_until:          # still in follow-up window
                    cmd = heard
                else:
                    continue
            if cmd == "":
                self.say("Yes?")
                awake_until = now + 8
                continue
            if cmd.lower() in ("stop listening", "go to sleep", "goodbye", "quit", "exit"):
                self.say("Goodbye.")
                break
            self.handle(cmd)
            awake_until = time.time() + 8      # allow a follow-up without the wake word


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis", description="A secure, AI-powered desktop assistant.")
    ap.add_argument("--voice", action="store_true", help="listen on the microphone (wake word + command)")
    ap.add_argument("--gui", action="store_true", help="open the chat window (tkinter)")
    ap.add_argument("--no-tts", action="store_true", help="never speak, only print")
    ap.add_argument("--once", metavar="TEXT", help="run one command and exit")
    ap.add_argument("--no-confirm", action="store_true", help="skip confirmation for risky actions")
    ap.add_argument("--config", action="store_true", help="print effective (redacted) settings and exit")
    args = ap.parse_args(argv)

    if args.no_confirm:
        settings.confirm_actions = False
    if args.config:
        print(json.dumps(settings.redacted(), indent=2, default=str))
        return 0

    j = Jarvis(voice=args.voice, tts=False if args.no_tts else None)
    if args.once:
        j.handle(args.once)
        return 0
    if args.gui:
        try:
            from .gui import launch
        except ImportError:
            print("tkinter is not available in this Python; falling back to text mode.")
            j.run_text()
            return 0
        if not j.listener:
            j.listener = Listener()
            j.voice_mode = False
        launch(j)
        return 0
    if args.voice:
        j.run_voice()
    else:
        j.run_text()
    return 0


if __name__ == "__main__":
    sys.exit(main())
