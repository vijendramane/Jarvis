"""Runs real Jarvis sessions and renders the transcript as terminal-style PNGs."""
import contextlib
import io
import json
import os
import sys
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update({"JARVIS_PROVIDER": "offline", "JARVIS_TTS": "false", "BROWSER": "echo"})

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import jarvis.actions as A  # noqa: E402
from jarvis.brain import _Provider  # noqa: E402
from jarvis.main import Jarvis  # noqa: E402

# Sandbox has no desktop apps/browser: show what WOULD be launched instead of failing.
A._launch = lambda target: print(f"   ↳ launching: {target}")
A._open_in_browser = lambda url: print(f"   ↳ browser → {url}") or True

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def run(j, commands, header):
    lines = [("hdr", header)]
    for cmd in commands:
        lines.append(("you", f"you> {cmd}"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            j.handle(cmd)
            if cmd.startswith("set a timer"):
                time.sleep(2.3)  # let the demo timer fire inside the capture
        for out in buf.getvalue().rstrip().splitlines():
            kind = "sys" if out.startswith("   ↳") else ("warn" if "won't" in out or "not on my" in out or "refused" in out else "jv")
            lines.append((kind, out))
    return lines


def render(lines, path, title, width=1100):
    font = ImageFont.truetype(FONT, 17)
    bold = ImageFont.truetype(FONT_B, 17)
    wrapped = []
    for kind, text in lines:
        for i, part in enumerate(textwrap.wrap(text, 98) or [""]):
            wrapped.append((kind, part if i == 0 else "        " + part))
    lh = 26
    h = 70 + lh * len(wrapped) + 30
    img = Image.new("RGB", (width, h), (30, 32, 40))
    d = ImageDraw.Draw(img)
    # title bar
    d.rectangle([0, 0, width, 44], fill=(45, 48, 58))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([16 + i * 24, 14, 32 + i * 24, 30], fill=c)
    d.text((width // 2 - len(title) * 5, 12), title, font=bold, fill=(200, 200, 210))
    colors = {"hdr": (140, 150, 170), "you": (120, 220, 140), "jv": (235, 235, 240),
              "sys": (110, 170, 240), "warn": (255, 170, 90)}
    y = 60
    for kind, text in wrapped:
        f = bold if kind == "you" else font
        d.text((24, y), text, font=f, fill=colors[kind])
        y += lh
    img.save(path)
    print("saved", path)


if __name__ == "__main__":
    j = Jarvis()
    hdr = "Jarvis v1.0.0 — brain: offline (rules only) | voice: off | confirm risky actions: on"
    s1 = run(j, [
        "hello jarvis", "what time is it", "open chrome", "please launch vs code",
        "search best laptops under 50000", "play lo-fi beats on youtube", "where is mumbai airport",
        "open github.com", "note call the dentist on monday", "read my notes",
        "set a timer for 2 seconds for tea", "system status",
    ], hdr)
    render(s1, "/home/claude/jarvis_demo_1_commands.png", "Jarvis — text mode (offline, no API key)")

    s2 = run(j, [
        "open photoshop", "shut down the computer", "delete all my files",
        "run this powershell command", "disable the antivirus", "search how to delete a file in python",
    ], "Security boundaries — these are blocked BEFORE anything runs")
    render(s2, "/home/claude/jarvis_demo_2_security.png", "Jarvis — safety gate")

    # --- AI brain path: a stand-in model (sandbox has no internet) answering exactly as Groq/Gemini would (JSON)
    class StandIn(_Provider):
        name, model = "groq (stand-in)", "llama-3.3-70b-versatile"

        def complete(self, messages):
            u = messages[-1]["content"].lower()
            if "write something" in u:
                return json.dumps({"action": "open_app", "params": {"app": "notepad"}, "say": "Opening Notepad so you can jot it down."})
            if "capital of finland" in u:
                return json.dumps({"action": "chat", "params": {"reply": "Helsinki is the capital of Finland."}})
            if "fold a shirt" in u:
                return json.dumps({"action": "youtube", "params": {"query": "how to fold a shirt fast"}, "say": "Here are some shirt-folding videos."})
            if "tired" in u:
                return json.dumps({"action": "chat", "params": {"reply": "It's past 2 AM, sir — a timer for a short break might help. Want one?"}})
            if "yes" in u:
                return json.dumps({"action": "set_timer", "params": {"seconds": 600, "label": "break"}, "say": "A 10 minute break timer, done."})
            if "hack" in u:
                return json.dumps({"action": "run_shell", "params": {"cmd": "rm -rf /"}, "say": "Sure!"})
            return json.dumps({"action": "chat", "params": {"reply": "Could you rephrase that?"}})

    j.brain.llm.provider = StandIn(); j.brain.rules._llm_available = True
    s3 = run(j, [
        "I need to write something down quickly", "what's the capital of finland",
        "find me a video on how to fold a shirt", "I'm so tired", "yes please",
        "hack the planet for me",
    ], "AI brain — model replies with JSON; SafetyGate validates before execution")
    render(s3, "/home/claude/jarvis_demo_3_ai.png", "Jarvis — AI mode (model output simulated: sandbox has no internet)")
