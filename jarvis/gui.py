"""Optional chat window built on tkinter (bundled with Python; no install).

    python -m jarvis --gui

Features: dark chat log, text entry, push-to-talk mic button (if the
voice libraries are installed), quick-action chips, and a status bar
showing which brain is active. All commands go through the same
``Jarvis.handle`` pipeline as the terminal, so the safety rules are identical.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from . import __version__
from .config import settings

BG, PANEL, FG, ACCENT, DIM, USER = "#0f1117", "#171a23", "#e6e6ea", "#4cc2ff", "#8b90a0", "#7ee787"
QUICK = ["weather", "news", "what time is it", "list reminders", "system status", "take a screenshot", "help"]


class JarvisGUI:
    def __init__(self, jarvis):
        self.j = jarvis
        self.q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.j.on_reply = lambda text: self.q.put(("jarvis", text))
        self.busy = False

        self.root = tk.Tk()
        self.root.title(f"{settings.assistant_name} v{__version__}")
        self.root.geometry("760x560")
        self.root.minsize(520, 380)
        self.root.configure(bg=BG)

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Chip.TButton", background=PANEL, foreground=FG, borderwidth=0, padding=(10, 4))
        style.map("Chip.TButton", background=[("active", "#232736")])
        style.configure("Send.TButton", background=ACCENT, foreground="#001018", borderwidth=0, padding=(14, 6))
        style.configure("Mic.TButton", background="#2a2f3f", foreground=FG, borderwidth=0, padding=(12, 6))

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(head, text=settings.assistant_name.upper(), fg=ACCENT, bg=BG, font=("Segoe UI", 16, "bold")).pack(side="left")
        self.status = tk.Label(head, text="", fg=DIM, bg=BG, font=("Segoe UI", 9))
        self.status.pack(side="right")

        self.log = tk.Text(self.root, bg=PANEL, fg=FG, insertbackground=FG, wrap="word", relief="flat",
                           font=("Segoe UI", 11), padx=14, pady=10, state="disabled", spacing3=6)
        self.log.pack(fill="both", expand=True, padx=14, pady=6)
        self.log.tag_configure("you", foreground=USER, font=("Segoe UI", 11, "bold"))
        self.log.tag_configure("jarvis", foreground=FG)
        self.log.tag_configure("name", foreground=ACCENT, font=("Segoe UI", 11, "bold"))
        self.log.tag_configure("dim", foreground=DIM, font=("Segoe UI", 9))

        chips = ttk.Frame(self.root)
        chips.pack(fill="x", padx=14)
        for text in QUICK:
            ttk.Button(chips, text=text, style="Chip.TButton", command=lambda t=text: self.submit(t)).pack(side="left", padx=(0, 6), pady=4)

        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=14, pady=(6, 14))
        self.entry = tk.Entry(bar, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=("Segoe UI", 12))
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self.submit())
        self.entry.focus_set()
        self.mic_btn = ttk.Button(bar, text="🎤", style="Mic.TButton", command=self.listen_once)
        self.mic_btn.pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Send", style="Send.TButton", command=self.submit).pack(side="left")

        self._set_status()
        self._append("dim", f"brain: {self.j.brain.llm.label} · confirm risky actions: "
                            f"{'on' if settings.confirm_actions else 'off'} · type a command or press 🎤")
        self.j.say(f"Online and ready, {settings.user_name}.")
        self.root.after(100, self._drain)

    # ------------------------------------------------------------------
    def _set_status(self, extra: str = ""):
        mic = "mic ready" if (self.j.listener and self.j.listener.available) else "no mic"
        self.status.config(text=f"{settings.os_name} · {mic}" + (f" · {extra}" if extra else ""))

    def _append(self, who: str, text: str):
        self.log.config(state="normal")
        if who == "you":
            self.log.insert("end", "You  ", "name")
            self.log.insert("end", text + "\n", "you")
        elif who == "jarvis":
            self.log.insert("end", f"{settings.assistant_name}  ", "name")
            self.log.insert("end", text + "\n", "jarvis")
        else:
            self.log.insert("end", text + "\n", "dim")
        self.log.config(state="disabled")
        self.log.see("end")

    def _drain(self):
        try:
            while True:
                who, text = self.q.get_nowait()
                if who == "status":
                    self._set_status(text)
                else:
                    self._append(who, text)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    # ------------------------------------------------------------------
    def submit(self, text: str | None = None):
        text = (text if text is not None else self.entry.get()).strip()
        if not text or self.busy:
            return
        self.entry.delete(0, "end")
        self._append("you", text)
        self.busy = True
        self.q.put(("status", "thinking…"))

        def work():
            try:
                self.j.handle(text)
            finally:
                self.busy = False
                self.q.put(("status", ""))

        threading.Thread(target=work, daemon=True).start()

    def listen_once(self):
        if not (self.j.listener and self.j.listener.available):
            self._append("dim", "No microphone / speech library available. Install: pip install SpeechRecognition pyaudio")
            return
        if self.busy:
            return
        self.busy = True
        self.q.put(("status", "listening…"))

        def work():
            try:
                heard = self.j.listener.listen(timeout=6)
                if heard:
                    self.q.put(("you", heard))
                    self.j.handle(heard)
                elif heard == "":
                    self.q.put(("dim", "(didn't catch that)"))
                else:
                    self.q.put(("dim", "(speech service unavailable)"))
            finally:
                self.busy = False
                self.q.put(("status", ""))

        threading.Thread(target=work, daemon=True).start()

    def run(self):
        self.root.mainloop()


def launch(jarvis) -> None:
    # In the GUI, confirmations come from a dialog instead of the terminal.
    from tkinter import messagebox

    def confirm(prompt: str) -> bool:
        result = {"ok": False}
        done = threading.Event()

        def ask():
            result["ok"] = messagebox.askyesno(settings.assistant_name, prompt)
            done.set()

        gui.root.after(0, ask)
        done.wait()
        return result["ok"]

    gui = JarvisGUI(jarvis)
    jarvis._confirm = confirm
    gui.run()
