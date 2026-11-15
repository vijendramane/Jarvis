
"""Voice input and output for Jarvis.

Speech-to-text: SpeechRecognition + Google's web recognizer.
Windows text-to-speech: Native Windows System.Speech.
Other platforms: pyttsx3 fallback.

Keeps compatibility with Jarvis main.py.
"""

from __future__ import annotations

import base64
import queue
import subprocess
import sys
import threading
from typing import Optional

from .config import settings


class Speaker:
    """Queued text-to-speech using Windows native speech."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._available = False
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()

        if not enabled:
            return

        self._available = True

        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="Jarvis-Speaker",
        )
        self._thread.start()

    @property
    def available(self) -> bool:
        return self._available

    def _speak_windows(self, text: str) -> None:
        """Speak one utterance using Windows System.Speech."""

        # Pass text as Base64 to avoid PowerShell quoting and encoding issues.
        encoded_text = base64.b64encode(
            text.encode("utf-8")
        ).decode("ascii")

        script = f"""
Add-Type -AssemblyName System.Speech

$bytes = [Convert]::FromBase64String('{encoded_text}')
$text = [System.Text.Encoding]::UTF8.GetString($bytes)

$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.Volume = 100
$speaker.Rate = 0

try {{
    $speaker.Speak($text)
}}
finally {{
    $speaker.Dispose()
}}
"""

        encoded_script = base64.b64encode(
            script.encode("utf-16le")
        ).decode("ascii")

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded_script,
            ],
            capture_output=True,
            text=True,
            timeout=90,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        if result.returncode != 0:
            error = result.stderr.strip()
            print(f"[TTS ERROR] Windows speech failed: {error}")

    def _speak_pyttsx3(self, text: str) -> None:
        """Fallback speech output for non-Windows platforms."""

        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.setProperty("volume", 1.0)

        engine.say(text)
        engine.runAndWait()
        engine.stop()

    def _loop(self) -> None:
        """Process speech requests one at a time."""

        while True:
            text = self._q.get()

            if text is None:
                break

            try:
                if sys.platform == "win32":
                    self._speak_windows(text)
                else:
                    self._speak_pyttsx3(text)

            except subprocess.TimeoutExpired:
                print("[TTS ERROR] Speech playback timed out.")

            except Exception as exc:
                print(
                    f"[TTS ERROR] "
                    f"{type(exc).__name__}: {exc}"
                )

    def say(self, text: str) -> None:
        """Queue text for speech playback."""

        if self.available and text:
            self._q.put(str(text))

    def stop(self) -> None:
        """Stop the speech queue."""

        if self.available:
            self._q.put(None)


class Listener:
    """Microphone input and speech recognition."""

    def __init__(self):
        self._sr = None
        self._mic = None

        try:
            import speech_recognition as sr

            self._sr = sr
            self._rec = sr.Recognizer()

            # Adaptive background noise detection.
            self._rec.dynamic_energy_threshold = True
            self._rec.dynamic_energy_adjustment_damping = 0.15
            self._rec.dynamic_energy_ratio = 2.0

            # Speech detection.
            self._rec.pause_threshold = 0.7
            self._rec.phrase_threshold = 0.4
            self._rec.non_speaking_duration = 0.5

            self._mic = sr.Microphone()

            with self._mic as source:
                print(
                    "[VOICE] Calibrating microphone. "
                    "Please stay quiet..."
                )

                self._rec.adjust_for_ambient_noise(
                    source,
                    duration=2,
                )

                print("[VOICE] Microphone ready.")

        except Exception as exc:
            print(
                f"[VOICE ERROR] Microphone initialization failed: {exc}"
            )
            self._sr = None
            self._mic = None

    @property
    def available(self) -> bool:
        return self._mic is not None

    def listen(
        self,
        timeout: float = 6,
        phrase_limit: float = 12,
    ) -> Optional[str]:
        """Listen and return recognized speech.

        Returns:
            Recognized text: speech was recognized.
            "": silence or unintelligible speech.
            None: microphone or recognition service failure.
        """

        if not self.available:
            return None

        sr = self._sr

        try:
            with self._mic as source:
                audio = self._rec.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_limit,
                )

        except sr.WaitTimeoutError:
            return ""

        except Exception as exc:
            print(
                f"[VOICE ERROR] Microphone listening failed: {exc}"
            )
            return None

        try:
            result = self._rec.recognize_google(audio)
            return result.strip()

        except sr.UnknownValueError:
            return ""

        except sr.RequestError as exc:
            print(
                f"[VOICE ERROR] Speech recognition failed: {exc}"
            )
            return None

        except Exception as exc:
            print(
                f"[VOICE ERROR] Recognition error: {exc}"
            )
            return None


def strip_wake_word(text: str) -> Optional[str]:
    """Accept commands only when they begin with the wake word.

    Examples:
        'Jarvis, how are you?' -> 'how are you?'
        'hello how are you'    -> None
        'film songs'           -> None
        'Jarvis'               -> ''
    """

    if not text:
        return None

    text = text.strip()
    wake_word = (settings.wake_word or "").strip()

    if not wake_word:
        return text

    if not text.lower().startswith(wake_word.lower()):
        return None

    command = text[len(wake_word):]

    # Require a word boundary after the wake word.
    if command and command[0].isalnum():
        return None

    return command.strip(" ,.!?;:")
