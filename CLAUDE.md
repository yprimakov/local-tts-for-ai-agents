# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Does

Local, offline text-to-speech powered by the Kokoro-82M ONNX model. Core use case: a Claude Code Stop hook that speaks a short voice blurb at the end of every AI response. Also supports standalone CLI playback and global hotkeys (Windows).

## Setup

Run once after cloning (requires Python 3.10+, internet for first run):

```bash
# Windows
python setup.py

# macOS
python3 setup.py
```

After setup, restart Claude Code. Then enable voice responses:
```bash
# Windows
type nul > "%USERPROFILE%\.claude\voice_enabled"

# macOS
touch ~/.claude/voice_enabled
```

## Running TTS

All commands use the venv's Python — never the system Python:

```bash
# Windows
venv\Scripts\python.exe tts.py "Hello world" --play
venv\Scripts\python.exe tts.py --file input.txt --autoplay
venv\Scripts\python.exe tts.py --list-voices

# macOS
venv/bin/python tts.py "Hello world" --play
venv/bin/python tts.py --file input.txt --autoplay
venv/bin/python tts.py --list-voices
```

## Diagnostics

Run `doctor.bat` (Windows) for a health check that verifies venv, models, packages, hook registration, voice toggle state, and audio output.

Check the hook log for why voice isn't firing:
- Windows: `type %TEMP%\kokoro_hook.log`
- macOS: `cat /tmp/kokoro_hook.log`

## Architecture

The project has four main components:

**`tts.py`** — TTS engine + playback UI. Two operating modes:
- `--play`: Launches a tkinter `KokoroController` window with a draggable player. Audio generation runs in a background thread via `_generate_worker`; result is handed back to the main thread via `root.after()`.
- `--autoplay`: Headless mode used by the hook. No GUI, blocks until audio finishes.
- Both modes use `onnxruntime` with `CPUExecutionProvider` only — `directml` is intentionally excluded due to a known `ConvTranspose` bug with Kokoro on some AMD/Windows systems.

**`tts_hook.py`** — Claude Code Stop hook. Reads JSON from stdin (Claude's hook payload), checks for `~/.claude/voice_enabled`, extracts a ~300-char blurb from the last paragraph of `last_assistant_message` (stripping markdown), writes it to a temp file, then launches `tts.py --autoplay` as a detached subprocess via `pythonw.exe` (no console window). Guards against infinite loops via `stop_hook_active` check.

**`setup.py`** — One-shot installer. Creates `./venv/`, installs packages, downloads model files (~355 MB) from GitHub releases, generates `kokoro_hook.bat`/`kokoro_hook.sh` with hardcoded absolute paths, and patches `~/.claude/settings.json` to register the Stop hook.

**`kokoro_hotkey.ahk`** — AutoHotkey v2 script (Windows only). `Ctrl+Alt+R` copies selected text to clipboard and launches `tts.py --play`; `Ctrl+Alt+V` toggles `~/.claude/voice_enabled`; `Ctrl+Alt+S` kills the active player process.

**Generated files** (not in repo, created by `setup.py`):
- `kokoro_hook.bat` / `kokoro_hook.sh` — thin wrappers that invoke `tts_hook.py` with the venv Python
- `models/` — downloaded ONNX model files
- `venv/` — Python virtual environment

## Key Design Decisions

- **Voice toggle is a file**: `~/.claude/voice_enabled` presence = ON, absence = OFF. No config files or environment variables.
- **`pythonw.exe` for autoplay**: Used in hook and AHK to avoid creating a console window on Windows.
- **Absolute paths in hook launcher**: `kokoro_hook.bat`/`.sh` contain hardcoded paths from the install location, so the hook works regardless of working directory when Claude fires it.
- **`CPUExecutionProvider` only**: ~6× faster than real-time on CPU; GPU acceleration via DirectML is excluded.
