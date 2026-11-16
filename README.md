# Jarvis v2 — your secure, AI-powered desktop assistant

Open apps, search, control volume & music, get weather/news/currency, set reminders,
remember things about you, run routines and chat — by typing, by voice, or in a chat window —
using **only free, keyless APIs** (or none at all). Windows, macOS and Linux.

```
you> good morning
Jarvis: Pune, IN: 24°C, mainly clear, feels like 25°, humidity 61%, wind 8 km/h. Today 19–31°C, 5% chance of rain.
Jarvis: Top headlines: … • … • …
Jarvis: 2 reminders: call mom (today at 5:00 PM); pay electricity bill (tomorrow at 9:00 AM)
you> open chrome and set volume to 40 then remind me in 25 minutes to stretch
Jarvis: Opening chrome.  Volume set to 40%.  I'll remind you to stretch in 25 minutes. 
```

---

## 1. Quick start (2 minutes)

**Requirements:** Python 3.9+. Nothing else is mandatory.

```bash
# unzip, open a terminal in the jarvis folder, then:
copy .env.example .env      # Windows   (cp on macOS/Linux)  — optional
python -m jarvis            # text mode
python -m jarvis --gui      # chat window (tkinter, bundled with Python)
python -m jarvis --voice    # hands-free with wake word (after installing voice extras)
```

Without any API key Jarvis runs in **offline command mode**: all device commands, calculator,
unit conversion, reminders, memory and routines work locally; weather, news, currency, dictionary,
Wikipedia and translation use free keyless web APIs.

### Enable AI conversation (free, pick ONE)

| Provider | Cost | How |
|---|---|---|
| **Groq** (recommended, very fast) | Free tier, no card | Key from <https://console.groq.com/keys> → `GROQ_API_KEY=` in `.env` |
| **Google Gemini** | Free tier, no card | Key from <https://aistudio.google.com/apikey> → `GEMINI_API_KEY=` in `.env` |
| **Ollama** (100% local, private) | Free | Install <https://ollama.com>, `ollama pull llama3.2` — auto-detected |

With a brain enabled, fuzzy requests work (*"I need to jot something down"* → Notepad; *"find me a
video on folding shirts"* → YouTube) and Jarvis answers general questions. The AI also sees the facts
you asked it to remember, so *"book a cab to my office"* can resolve "my office" if you've told it.
If a free-tier model name is retired, Jarvis auto-selects an available one.

### Enable voice

```bash
pip install -r requirements.txt     # pyttsx3, SpeechRecognition, PyAudio, psutil, pillow
python -m jarvis --voice            # say: "Jarvis, what's the weather"
```
Speech-to-text uses Google's free web recogniser (no key); text-to-speech is fully offline.
PyAudio install tips are in `requirements.txt`. In `--gui` mode the 🎤 button is push-to-talk.

---

## 2. Everything Jarvis can do

### Apps & device
| Say… | Action |
|---|---|
| `open chrome`, `launch vs code`, `open chrom` *(typos ok)* | open an **allowlisted** app |
| `close chrome` | quit an app *(asks to confirm)* |
| `volume up/down`, `set volume to 40`, `mute`, `unmute` | system volume |
| `play`, `pause`, `next song`, `previous`, `stop music` | media keys |
| `take a screenshot` | saved to `Pictures/Jarvis/` |
| `what's on my clipboard`, `copy <text>` | clipboard |
| `open downloads folder` (documents, desktop, pictures, music, videos, home) | standard folders only |
| `lock screen` | lock the computer *(asks to confirm)* |
| `system status`, `battery level` | OS, CPU, RAM, battery, disk |
| `what did you do today` | activity summary from the audit log |

### Web & knowledge (free keyless APIs)
| Say… | Source |
|---|---|
| `weather`, `weather in London`, `temperature tomorrow` | Open-Meteo |
| `news`, `news about cricket` | Google News RSS (region = `JARVIS_COUNTRY`), BBC fallback |
| `100 usd to inr`, `how much is 50 euros in rupees` | Frankfurter (ECB rates) |
| `define serendipity`, `spell necessary` | dictionaryapi.dev |
| `who is Alan Turing`, `tell me about black holes` | Wikipedia summary, spoken |
| `translate good morning to hindi` | MyMemory |
| `search …`, `… on youtube`, `where is …`, `wiki …`, `open github.com` | opens in your browser |

### Productivity
| Say… | Action |
|---|---|
| `remind me to call mom in 20 minutes`, `remind me at 5pm to …`, `remind me tomorrow at 9 to …` | persistent reminders (survive restarts) |
| `list reminders`, `cancel reminders` | manage reminders |
| `set a timer for 10 minutes for tea` | countdown |
| `note buy milk`, `read my notes` | notes in `~/.jarvis/notes.txt` |
| `remember my car number is MH12AB1234`, `what's my car number`, `forget my car number`, `what do you remember` | personal memory |
| `what is 15% of 2400`, `square root of 169`, `2 to the power 10` | safe calculator (no `eval`) |
| `convert 10 km to miles`, `32 f to c`, `5 kg to lbs` | unit conversion |
| `start work mode`, `good morning`, `list routines` | routines from `routines.json` |
| `open chrome and play music then set volume to 30` | multiple commands in one sentence |
| `what time is it`, `what's the date`, `help`, `list apps` | basics |

### Customise
* **Apps:** edit `apps.json` (per-OS launch targets + aliases). Jarvis never launches anything not listed.
* **Routines:** edit `routines.json` — each routine is a list of ordinary Jarvis commands, e.g.
  `"study mode": ["open vscode", "volume mute", "set a timer for 50 minutes for study", "news about technology"]`.
* **Personality:** `JARVIS_NAME`, `JARVIS_USER`, `JARVIS_WAKE_WORD` in `.env`.
* **Location:** `JARVIS_CITY`, `JARVIS_COUNTRY` in `.env` (or just say *remember my city is Pune*).

### Flags
```
python -m jarvis --gui                      # chat window
python -m jarvis --voice                    # wake-word listening
python -m jarvis --once "open calculator"   # one-shot (bind to a hotkey)
python -m jarvis --no-tts                   # silent
python -m jarvis --no-confirm               # don't ask before risky actions
python -m jarvis --config                   # effective settings (keys masked)
```

---

## 3. Security model — why this can't harm your device

1. **Closed action set.** Jarvis can only run the ~42 functions registered in `actions.py` /
   `features.py`. There is no "run a command" or "open a path" action. The AI only *proposes* one
   of them as JSON; the local `SafetyGate` validates the name and parameter types and drops the rest.
2. **App & folder allowlists.** Apps come from `apps.json`; folders are a fixed set of standard
   user folders. Arbitrary paths are impossible.
3. **No shell injection.** Every process is started with an argument list, never `shell=True`.
   The few fixed OS helpers (`pactl`, `osascript`, `clip`, …) receive constant arguments only.
4. **Network allowlist.** Outbound requests go through `net.py`, which only permits HTTPS to the
   specific hosts of the free APIs and your chosen LLM provider.
5. **Safe maths.** The calculator parses expressions with Python's `ast` and evaluates a tiny
   whitelist of operators — never `eval`.
6. **Hard refusals.** Shutdown/restart, delete/format, running scripts, uninstalling, disabling
   antivirus/firewall are blocked before the AI even sees them.
7. **Confirmation** for closing apps, locking the screen and clearing reminders;
   **rate limit** of 30 actions/minute so a confused model can't spam your desktop.
8. **Secrets stay local.** Keys live in git-ignored `.env`, are masked in `--config`, never logged,
   and sent only to their own provider over verified HTTPS.
9. **Minimal footprint & full audit.** Jarvis writes only to `~/.jarvis/` (notes, memory, reminders,
   `history.jsonl` audit log) and `Pictures/Jarvis/` for screenshots. Delete the folder to reset.

Privacy: voice input goes to Google's free recogniser; with Groq/Gemini your text (and your
remembered facts, so the AI can use them) goes to that provider. For fully private use choose
Ollama + typed input. Weather/news/currency requests contain only the city / topic / currencies.

---

## 4. Project layout

```
jarvis/
├── apps.json           # app allowlist          ├── routines.json      # your routines
├── .env.example        # settings template      ├── requirements.txt   # optional extras
├── run.bat / run.sh    # launchers              └── tests/             # python -m unittest discover -s tests
└── jarvis/
    ├── config.py   settings      ├── actions.py   core actions + SafetyGate   ├── features.py  v2 features
    ├── brain.py    rules + LLMs  ├── store.py     memory & reminders          ├── net.py       HTTPS allowlist
    ├── voice.py    mic / TTS     ├── gui.py       tkinter window              └── main.py      CLI loop
```

## 5. Troubleshooting

* **"not on my allowed apps list"** → add the app to `apps.json`.
* **"doesn't seem to be installed"** → fix the launch target in `apps.json` for your OS.
* **Brain shows `offline (rules only)`** → no working key; check `.env` / `python -m jarvis --config`.
* **429 from Groq/Gemini** → free-tier rate limit; wait or switch provider.
* **Weather/news "isn't reachable"** → no internet, or a firewall blocking the API hosts.
* **No speech** → `pip install pyttsx3` (Linux also `sudo apt install espeak`).
* **Mic not found** → install PyAudio (see `requirements.txt`), check OS mic permissions.
* **Linux media/volume** → needs `playerctl` / `pactl` (PulseAudio or PipeWire).
