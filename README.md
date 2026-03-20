# Local TTS for AI Agents

Offline, GPU-accelerated text-to-speech powered by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), with deep integration into [Claude Code](https://claude.ai/code).

**Key features:**
- Fully offline after setup — no API keys, no cloud calls
- ~6× faster than real-time on CPU alone
- Draggable playback controller with pause, seek, and ±10s skip
- Claude Code hook: every response is spoken as a short voice blurb
- Global hotkeys via AutoHotkey (Windows only)
- Single-command install on Windows and macOS

---

## For AI Agents: Installation Instructions

> These instructions are written to be executed autonomously. Follow each step exactly. Verify expected outputs before proceeding.

### Prerequisites

#### Windows

| Requirement | Version | How to verify |
|---|---|---|
| Windows 10/11 | — | `ver` in cmd |
| Python | 3.10 or newer | `python --version` |
| Internet connection | — | Required for first-run model download only |
| AutoHotkey v2 *(optional)* | v2.x | https://www.autohotkey.com |

Python must be on `PATH`. If `python --version` fails, download from https://www.python.org/downloads/ and check "Add to PATH" during install.

#### macOS

| Requirement | Version | How to verify |
|---|---|---|
| macOS | 12 Monterey or newer | `sw_vers` |
| Python | 3.10 or newer | `python3 --version` |
| Xcode Command Line Tools | — | `xcode-select --install` (if not already installed) |
| Internet connection | — | Required for first-run model download only |

PortAudio is required by `sounddevice`. Install via Homebrew:
```bash
brew install portaudio
```
If Homebrew is not installed: https://brew.sh

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/yprimakov/local-tts-for-ai-agents.git
cd local-tts-for-ai-agents
```

**Expected:** Directory contains `setup.py`, `tts.py`, `tts_hook.py`, `kokoro_hotkey.ahk`.

---

### Step 2 — Run setup

**Windows:**
```bash
python setup.py
```

**macOS:**
```bash
python3 setup.py
```

This command:
1. Creates `./venv/` — Python virtual environment
2. Installs packages: `kokoro-onnx`, `onnxruntime`, `sounddevice`, `soundfile`
3. Downloads model files into `./models/` (~355 MB total, one-time)
   - `kokoro-v1.0.onnx` (325 MB)
   - `voices-v1.0.bin` (28 MB)
4. Writes `./kokoro_hook.bat` (Windows) or `./kokoro_hook.sh` (macOS) with the correct paths for this install location
5. Patches `~/.claude/settings.json` to register the Claude Code Stop hook

**Expected output ends with:** `Setup complete!`

**If setup fails:**
- Package install error → check internet connection, retry
- macOS `sounddevice` error → run `brew install portaudio` first
- Model download error → GitHub releases may be temporarily unavailable, retry
- settings.json error → ensure `~/.claude/` directory exists (it is created by Claude Code on first run)

---

### Step 3 — Restart Claude Code

The Stop hook is loaded at startup. **Restart any running Claude Code instances** for the hook to take effect.

**Verify the hook is registered:**

Windows:
```bash
python -c "import json; s=json.load(open(r'%USERPROFILE%\.claude\settings.json')); print(s.get('hooks', {}).get('Stop', 'NOT FOUND'))"
```

macOS:
```bash
python3 -c "import json, os; s=json.load(open(os.path.expanduser('~/.claude/settings.json'))); print(s.get('hooks', {}).get('Stop', 'NOT FOUND'))"
```

Expected: a list containing an entry with `kokoro_hook` in the command path.

---

### Step 4 — Enable voice responses

Voice responses are controlled by a toggle file. Create it to enable:

**Windows:**
```bash
# Enable
type nul > "%USERPROFILE%\.claude\voice_enabled"

# Disable
del "%USERPROFILE%\.claude\voice_enabled"
```

**macOS:**
```bash
# Enable
touch ~/.claude/voice_enabled

# Disable
rm ~/.claude/voice_enabled
```

**Verify (Windows):**
```bash
if exist "%USERPROFILE%\.claude\voice_enabled" (echo ON) else (echo OFF)
```

**Verify (macOS):**
```bash
[ -f ~/.claude/voice_enabled ] && echo ON || echo OFF
```

---

### Step 5 — (Optional) Set up global hotkeys

**Windows only.** Double-click `kokoro_hotkey.ahk` to activate hotkeys. Requires AutoHotkey v2.

**Verify:** The AutoHotkey icon (green H) appears in the system tray.

To start hotkeys automatically with Windows:
```bash
# Creates a shortcut in the Windows Startup folder
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\kokoro_hotkey.lnk');$s.TargetPath='%CD%\kokoro_hotkey.ahk';$s.Save()"
```

macOS: AutoHotkey is not available. Use the CLI directly (see Usage section).

---

### Verification — End-to-end test

Run this and confirm you hear audio output:

**Windows:**
```bash
venv\Scripts\python.exe tts.py "Setup is complete. Local TTS is working correctly." --play
```

**macOS:**
```bash
venv/bin/python tts.py "Setup is complete. Local TTS is working correctly." --play
```

**Expected:** A player window appears, audio plays, the playback controller shows a moving progress bar.

Test the Claude Code hook directly:

**Windows:**
```bash
echo {"session_id":"test","stop_hook_active":false,"last_assistant_message":"Setup verified. Voice responses are now active."} | venv\Scripts\python.exe tts_hook.py
```

**macOS:**
```bash
echo '{"session_id":"test","stop_hook_active":false,"last_assistant_message":"Setup verified. Voice responses are now active."}' | venv/bin/python tts_hook.py
```

**Expected:** Audio plays within ~2 seconds (model load + generation).

---

## File Structure

```
local-tts-for-ai-agents/
├── setup.py              ← one-command installer (Windows + macOS)
├── tts.py                ← TTS engine + playback controller
├── tts_hook.py           ← Claude Code Stop hook script
├── kokoro_hotkey.ahk     ← AutoHotkey global hotkeys (Windows only)
├── kokoro_hook.bat       ← generated by setup.py on Windows
├── kokoro_hook.sh        ← generated by setup.py on macOS
├── requirements.txt      ← package list (reference only; setup.py installs)
├── models/               ← downloaded by setup.py (gitignored)
│   ├── kokoro-v1.0.onnx
│   └── voices-v1.0.bin
└── venv/                 ← Python venv, created by setup.py (gitignored)
```

---

## Usage

### Command-line TTS

**Windows:**
```bash
venv\Scripts\python.exe tts.py "Your text here" --play
venv\Scripts\python.exe tts.py --file myfile.txt --play
venv\Scripts\python.exe tts.py "Hello" --voice am_adam --speed 1.1 --out hello.wav
venv\Scripts\python.exe tts.py --list-voices
venv\Scripts\python.exe tts.py --file input.txt --autoplay
```

**macOS:**
```bash
venv/bin/python tts.py "Your text here" --play
venv/bin/python tts.py --file myfile.txt --play
venv/bin/python tts.py "Hello" --voice am_adam --speed 1.1 --out hello.wav
venv/bin/python tts.py --list-voices
venv/bin/python tts.py --file input.txt --autoplay
```

### Global Hotkeys (Windows + AutoHotkey v2 required)

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+R` | Read selected text — opens the TTS player |
| `Ctrl+Alt+V` | Toggle Claude Code voice responses on/off |
| `Ctrl+Alt+S` | Stop playback immediately |

macOS: AutoHotkey is not available. Use the CLI or assign system shortcuts to shell commands as needed.

### Playback Controller

The controller appears when using `--play` or `Ctrl+Alt+R`:

- **Drag** the title bar to reposition
- **Click the progress bar** to seek
- **`▶` / `⏸`** — play / pause
- **`« 10`** — rewind 10 seconds
- **`10 »`** — fast-forward 10 seconds
- **`✕`** — close

### Voice Responses in Claude Code

When `~/.claude/voice_enabled` exists, Claude Code will speak a short summary at the end of every response. The blurb is extracted from the last paragraph of the message (markdown stripped, capped at ~300 characters).

Toggle from within any Claude Code session:
```
/voice          ← toggle
/voice on       ← enable
/voice off      ← disable
```

Or toggle the file directly:
- **Windows:** `Ctrl+Alt+V` (AutoHotkey) — or create/delete `%USERPROFILE%\.claude\voice_enabled`
- **macOS:** `touch ~/.claude/voice_enabled` (on) / `rm ~/.claude/voice_enabled` (off)

---

## Available Voices

| ID | Description |
|---|---|
| `af_heart` | American Female — warm, natural *(default)* |
| `af_bella` | American Female — clear, expressive |
| `af_nicole` | American Female — smooth, professional |
| `af_aoede` | American Female — bright, energetic |
| `af_kore` | American Female — calm, authoritative |
| `af_sarah` | American Female — friendly, conversational |
| `af_sky` | American Female — gentle, airy |
| `am_adam` | American Male — strong, clear |
| `am_michael` | American Male — warm, measured |
| `am_echo` | American Male — resonant |
| `am_eric` | American Male — steady, confident |
| `am_fenrir` | American Male — deep, powerful |
| `am_liam` | American Male — youthful, upbeat |
| `am_onyx` | American Male — rich, baritone |
| `am_puck` | American Male — nimble, lively |
| `bf_emma` | British Female — refined, articulate |
| `bf_isabella` | British Female — warm, elegant |
| `bm_george` | British Male — formal, distinguished |
| `bm_lewis` | British Male — casual, approachable |

Change the default voice by editing the top of `kokoro_hotkey.ahk`:
```ahk
VOICE := "am_adam"
SPEED := "1.1"
```

Or in the Claude Code hook, set the `--voice` flag in `kokoro_hook.bat`.

---

## Troubleshooting

### No audio after setup

**Windows:**
1. Check the hook log: `type %TEMP%\kokoro_hook.log`
2. Test autoplay directly: `venv\Scripts\python.exe tts.py "test" --autoplay`
3. Ensure Claude Code was restarted after setup

**macOS:**
1. Check the hook log: `cat /tmp/kokoro_hook.log`
2. Test autoplay directly: `venv/bin/python tts.py "test" --autoplay`
3. Ensure Claude Code was restarted after setup

### Hook is not firing (log file empty after Claude responds)

1. Verify settings.json contains the hook: check `~/.claude/settings.json`
2. Re-run `python setup.py` (or `python3 setup.py` on macOS) to re-patch
3. Restart Claude Code

### Voice responses enabled but nothing plays

**Windows:** `if exist "%USERPROFILE%\.claude\voice_enabled" echo ON`

**macOS:** `[ -f ~/.claude/voice_enabled ] && echo ON || echo OFF`

### sounddevice error on macOS

Install PortAudio via Homebrew:
```bash
brew install portaudio
pip install --force-reinstall sounddevice
```

### GPU acceleration

Both Windows and macOS use standard `onnxruntime` (CPU execution). CPU inference is ~6× faster than real-time and suitable for all use cases. `onnxruntime-directml` is not used — it has a known bug with Kokoro's ConvTranspose operation and causes initialization hangs on some AMD/Windows systems.

---

## Platform Support

| Platform | Status |
|---|---|
| Windows 10/11 | Fully supported (TTS core + Claude Code hook + AutoHotkey hotkeys) |
| macOS 12+ | Supported (TTS core + Claude Code hook; no AutoHotkey) |
| Linux | Not supported |

---

## License

MIT — see [LICENSE](LICENSE).

Model weights are distributed by [hexgrad](https://huggingface.co/hexgrad/Kokoro-82M) under Apache 2.0.
