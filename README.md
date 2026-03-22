# Local TTS for AI Agents

Offline, GPU-accelerated text-to-speech powered by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M).

Designed to be installed and operated by AI agents. Works as a standalone CLI tool, a Claude Code voice service, or a general-purpose TTS backend.

---

## What it does

- **Voice responses for Claude Code** — a Stop hook reads a short summary of every response aloud
- **On-demand TTS** — read any selected text via a global hotkey (`Ctrl+Alt+R` on Windows)
- **Playback controller** — compact dark-glass floating window with pause, seek, and ±10s skip
- **Fully offline** — no API keys, no cloud calls; model runs locally at ~6× real-time on CPU
- **All platforms** — Windows, macOS, Linux

---

## Using this as a TTS service for AI agents

### Claude Code voice responses

After installation, Claude Code will automatically speak a short voice summary at the end of every response. The hook fires on the built-in `Stop` event and requires no configuration beyond the initial setup.

Enable or disable voice responses at any time:

```
/voice          ← toggle on/off
/voice on
/voice off
```

Or toggle the flag file directly:

| Platform | Enable | Disable |
|---|---|---|
| Windows | `type nul > "%USERPROFILE%\.claude\voice_enabled"` | `del "%USERPROFILE%\.claude\voice_enabled"` |
| macOS / Linux | `touch ~/.claude/voice_enabled` | `rm ~/.claude/voice_enabled` |

### Using from any AI agent or script

Any agent can invoke the TTS engine directly:

```bash
# Speak text silently (no window, blocks until audio finishes)
venv/bin/python tts.py "Your message here" --autoplay

# Show the interactive playback controller
venv/bin/python tts.py "Your message here" --play

# Save to WAV file
venv/bin/python tts.py "Your message here" --out output.wav
```

Windows uses `venv\Scripts\python.exe` instead of `venv/bin/python`.

---

## Installation

### Prerequisites by platform

**Windows 10/11**

| Requirement | Notes |
|---|---|
| Python 3.10+ | [python.org](https://www.python.org/downloads/) — check "Add to PATH" during install |
| Git | [git-scm.com](https://git-scm.com/) or `winget install Git.Git` |
| AutoHotkey v2 *(optional)* | [autohotkey.com](https://www.autohotkey.com) — required for global hotkeys only |

**macOS 12+**

| Requirement | Notes |
|---|---|
| Python 3.10+ | `brew install python` or [python.org](https://www.python.org/downloads/) |
| Git | Included with Xcode Command Line Tools: `xcode-select --install` |
| PortAudio | `brew install portaudio` — required by `sounddevice` |

**Linux (Ubuntu/Debian and derivatives)**

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv python3-tk portaudio19-dev git
```

For other distributions, install the equivalents of the above. `python3-tk` is required for the playback controller GUI. `portaudio19-dev` is required by `sounddevice`.

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/yprimakov/local-tts-for-ai-agents.git
cd local-tts-for-ai-agents
```

---

### Step 2 — Run setup

**Windows:**
```bash
python setup.py
```

**macOS / Linux:**
```bash
python3 setup.py
```

This single command:
1. Creates a Python virtual environment at `./venv/`
2. Installs all required packages: `kokoro-onnx`, `onnxruntime`, `sounddevice`, `soundfile`, `Pillow`
3. Downloads the Kokoro-82M model files into `./models/` (~355 MB, one-time)
   - `kokoro-v1.0.onnx` (325 MB)
   - `voices-v1.0.bin` (28 MB)
4. Writes `./kokoro_hook.bat` (Windows) or `./kokoro_hook.sh` (macOS/Linux) with absolute paths for this install location
5. Patches `~/.claude/settings.json` to register the Claude Code Stop hook

**Expected output ends with:** `Setup complete!`

**If setup fails:**

| Error | Fix |
|---|---|
| Package install error | Check internet connection; retry |
| `sounddevice` build error on macOS | Run `brew install portaudio` first |
| `sounddevice` build error on Linux | Run `sudo apt install portaudio19-dev python3-dev` first |
| Model download error | GitHub releases temporarily unavailable; retry |
| `settings.json` error | Run Claude Code once first to create `~/.claude/`, then retry |

---

### Step 3 — Restart Claude Code

The Stop hook is registered at startup. **Restart any running Claude Code instances** for it to take effect.

Verify the hook is registered:

**Windows:**
```bash
python -c "import json; s=json.load(open(r'%USERPROFILE%\.claude\settings.json')); print(s.get('hooks',{}).get('Stop','NOT FOUND'))"
```

**macOS / Linux:**
```bash
python3 -c "import json,os; s=json.load(open(os.path.expanduser('~/.claude/settings.json'))); print(s.get('hooks',{}).get('Stop','NOT FOUND'))"
```

Expected output: a list containing an entry with `kokoro_hook` in the command path.

---

### Step 4 — Enable voice responses

Voice responses are off by default. Create the toggle file to enable them:

**Windows:**
```bash
type nul > "%USERPROFILE%\.claude\voice_enabled"
```

**macOS / Linux:**
```bash
touch ~/.claude/voice_enabled
```

---

### Step 5 — (Optional) Global hotkeys — Windows only

Double-click `kokoro_hotkey.ahk` to activate. Requires AutoHotkey v2.

To start hotkeys automatically with Windows, run this once:
```bash
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\kokoro_hotkey.lnk');$s.TargetPath='%CD%\kokoro_hotkey.ahk';$s.Save()"
```

macOS / Linux: AutoHotkey is not available. Use the CLI directly or assign system shortcuts to shell commands.

---

### Verification — end-to-end test

Run this and confirm you hear audio:

**Windows:**
```bash
venv\Scripts\python.exe tts.py "Setup is complete. Local TTS is working correctly." --play
```

**macOS / Linux:**
```bash
venv/bin/python tts.py "Setup is complete. Local TTS is working correctly." --play
```

Expected: a floating playback controller appears, audio plays, the progress bar moves.

Test the Claude Code hook directly:

**Windows:**
```bash
echo {"session_id":"test","stop_hook_active":false,"last_assistant_message":"Voice responses are now active."} | venv\Scripts\python.exe tts_hook.py
```

**macOS / Linux:**
```bash
echo '{"session_id":"test","stop_hook_active":false,"last_assistant_message":"Voice responses are now active."}' | venv/bin/python tts_hook.py
```

Expected: audio plays within ~2 seconds.

---

## Usage

### Command-line reference

```bash
# Interactive playback controller
python tts.py "Hello world" --play

# Speak silently, no window (used by hooks and agents)
python tts.py "Hello world" --autoplay

# Save to file
python tts.py "Hello world" --out hello.wav

# Read from file
python tts.py --file input.txt --play

# Choose voice and speed
python tts.py "Hello" --voice am_adam --speed 1.1 --play

# List available voices
python tts.py --list-voices
```

Use `venv\Scripts\python.exe` (Windows) or `venv/bin/python` (macOS/Linux) to ensure the correct environment.

---

### Playback controller

The controller is a compact floating window (256×146 px) with a dark glass card design. It appears in the bottom-right corner and stays above other windows.

| Control | Action |
|---|---|
| Drag title bar | Reposition the window |
| Click / drag progress bar | Seek to position |
| `▶` / `⏸` | Play / pause |
| `« 10` | Rewind 10 seconds |
| `10 »` | Fast-forward 10 seconds |
| `✕` | Close |

The title bar shows the iMadeFire brand logo if `svglib` and `reportlab` are installed, otherwise a styled text fallback. See [SVG logo not showing](#svg-logo-not-showing-in-the-title-bar) below.

---

### Global hotkeys (Windows + AutoHotkey v2)

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+R` | Read selected text — opens the playback controller |
| `Ctrl+Alt+V` | Toggle Claude Code voice responses on/off |
| `Ctrl+Alt+S` | Stop playback immediately |

---

### Voice responses in Claude Code

When `~/.claude/voice_enabled` exists, the Stop hook generates and plays a short voice summary at the end of every Claude Code response. The blurb is the last paragraph of the message, with markdown stripped, capped at ~300 characters.

Toggle from within a Claude Code session:
```
/voice          ← toggle
/voice on       ← enable
/voice off      ← disable
```

---

## Available voices

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

Change the default voice in `kokoro_hotkey.ahk`:
```ahk
VOICE := "am_adam"
SPEED := "1.1"
```

Or pass `--voice` and `--speed` on the CLI.

---

## Troubleshooting

### Run the diagnostic script first (Windows)

Double-click `doctor.bat` to check the full install state: venv, models, packages, hook registration, voice toggle, and a live audio test.

### No audio after setup

1. Check the hook log:
   - Windows: `type %TEMP%\kokoro_hook.log`
   - macOS / Linux: `cat /tmp/kokoro_hook.log`
2. Test autoplay directly:
   - Windows: `venv\Scripts\python.exe tts.py "test" --autoplay`
   - macOS / Linux: `venv/bin/python tts.py "test" --autoplay`
3. Confirm Claude Code was restarted after setup

### Hook not firing (log file empty after Claude responds)

1. Check `~/.claude/settings.json` for an entry containing `kokoro_hook`
2. Re-run `python setup.py` (or `python3 setup.py`) to re-patch
3. Restart Claude Code

### `stop_hook error: command not found`

The hook path in `settings.json` must use forward slashes, not backslashes. Re-run `setup.py` — it writes the path correctly. If editing manually, use forward slashes: `C:/path/to/kokoro_hook.bat`.

### Voice toggle is on but nothing plays

Verify the flag file exists:
- Windows: `if exist "%USERPROFILE%\.claude\voice_enabled" (echo ON) else (echo OFF)`
- macOS / Linux: `[ -f ~/.claude/voice_enabled ] && echo ON || echo OFF`

### SVG logo not showing in the title bar

A styled text fallback is shown automatically when `svglib`/`reportlab` are not installed — this is expected. To render the actual SVG logo:

```bash
venv\Scripts\pip.exe install svglib reportlab   # Windows
venv/bin/pip install svglib reportlab           # macOS / Linux
```

### `sounddevice` error on macOS

```bash
brew install portaudio
venv/bin/pip install --force-reinstall sounddevice
```

### `sounddevice` error on Linux

```bash
sudo apt install portaudio19-dev python3-dev
venv/bin/pip install --force-reinstall sounddevice
```

### Playback controller window does not appear (Linux)

Tkinter may not be installed:
```bash
sudo apt install python3-tk
```

---

## File structure

```
local-tts-for-ai-agents/
├── setup.py              ← one-command installer (Windows, macOS, Linux)
├── tts.py                ← TTS engine + playback controller UI
├── tts_hook.py           ← Claude Code Stop hook script
├── kokoro_hotkey.ahk     ← AutoHotkey global hotkeys (Windows only)
├── doctor.bat            ← diagnostic script — double-click to check install health
├── kokoro_hook.bat       ← generated by setup.py on Windows
├── kokoro_hook.sh        ← generated by setup.py on macOS / Linux
├── requirements.txt      ← package list (reference; setup.py handles install)
├── brand/
│   └── logo/
│       └── iMadeFire-simple-white-on-black-v2.svg
├── models/               ← downloaded by setup.py (gitignored)
│   ├── kokoro-v1.0.onnx
│   └── voices-v1.0.bin
└── venv/                 ← created by setup.py (gitignored)
```

---

## Platform support

| Platform | TTS core | Claude Code hook | Playback controller | Global hotkeys |
|---|---|---|---|---|
| Windows 10/11 | ✓ | ✓ | ✓ | ✓ (AutoHotkey v2) |
| macOS 12+ | ✓ | ✓ | ✓ | — |
| Linux (Ubuntu 22.04+) | ✓ | ✓ | ✓ | — |

GPU acceleration: all platforms use standard `onnxruntime` with `CPUExecutionProvider`. CPU inference runs at ~6× real-time, which is sufficient for all use cases. `onnxruntime-directml` is explicitly excluded — it has a known bug with Kokoro's ConvTranspose operation that causes initialization hangs on some AMD/Windows systems.

---

## License

MIT — see [LICENSE](LICENSE).

Model weights are distributed by [hexgrad](https://huggingface.co/hexgrad/Kokoro-82M) under Apache 2.0.
