"""
Local TTS for AI Agents — Setup Script
=======================================
Run once after cloning to install everything:

    python setup.py

What this does:
  1. Creates a Python virtual environment at ./venv/
  2. Installs required packages into the venv
  3. Downloads Kokoro-82M model files into ./models/
  4. Generates kokoro_hook.bat (Windows) or kokoro_hook.sh (macOS)
  5. Patches ~/.claude/settings.json to register the Stop hook

Prerequisites: Python 3.10+, internet connection for first run.
Supported platforms: Windows 10/11, macOS 12+.
"""

import json
import os
import stat
import subprocess
import sys
import urllib.request
import venv
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

# -- paths -----------------------------------------------------
REPO_DIR        = Path(__file__).parent.resolve()
VENV_DIR        = REPO_DIR / "venv"
MODELS_DIR      = REPO_DIR / "models"
HOOK_SCRIPT     = REPO_DIR / "tts_hook.py"
CLAUDE_DIR      = Path.home() / ".claude"
CLAUDE_SETTINGS = CLAUDE_DIR / "settings.json"

if IS_WINDOWS:
    HOOK_LAUNCHER = REPO_DIR / "kokoro_hook.bat"
else:
    HOOK_LAUNCHER = REPO_DIR / "kokoro_hook.sh"

# -- model downloads -------------------------------------------
MODELS = {
    "kokoro-v1.0.onnx": (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download"
        "/model-files-v1.0/kokoro-v1.0.onnx"
    ),
    "voices-v1.0.bin": (
        "https://github.com/thewh1teagle/kokoro-onnx/releases/download"
        "/model-files-v1.0/voices-v1.0.bin"
    ),
}

# -- packages --------------------------------------------------
PACKAGES_COMMON = [
    "kokoro-onnx",
    "sounddevice",
    "soundfile",
    "Pillow",       # gradient/glow rendering in the playback controller UI
]

PACKAGES_WINDOWS = [
    "onnxruntime",  # Plain ONNX Runtime — directml has a known ConvTranspose bug
                    # with Kokoro and causes DLL init hangs on some AMD systems
]

PACKAGES_MAC = [
    "onnxruntime",           # Standard ONNX Runtime for macOS
]

PACKAGES = PACKAGES_COMMON + (PACKAGES_WINDOWS if IS_WINDOWS else PACKAGES_MAC)


# -- helpers ---------------------------------------------------
def header(title: str):
    print(f"\n{'-' * 58}")
    print(f"  {title}")
    print(f"{'-' * 58}")


def ok(msg=""):
    print(f"  [OK] {msg}" if msg else "  [OK]")


def fail(msg: str):
    print(f"\n  [FAIL] {msg}")
    sys.exit(1)


def pip() -> str:
    if IS_WINDOWS:
        return str(VENV_DIR / "Scripts" / "pip.exe")
    return str(VENV_DIR / "bin" / "pip")


def python_exe() -> str:
    if IS_WINDOWS:
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python")


# -- steps -----------------------------------------------------
def check_python():
    if sys.version_info < (3, 10):
        fail(f"Python 3.10+ required. Found {sys.version}. "
             "Download from https://www.python.org/downloads/")
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")


def create_venv():
    if (VENV_DIR / "Scripts" / "python.exe").exists():
        ok(f"venv already exists at {VENV_DIR}")
        return
    print(f"  Creating venv at {VENV_DIR} ...")
    venv.create(VENV_DIR, with_pip=True)
    ok("venv created")


def install_packages():
    for pkg in PACKAGES:
        print(f"  Installing {pkg} ...", end=" ", flush=True)
        result = subprocess.run(
            [pip(), "install", pkg, "-q"],
            capture_output=True,
        )
        if result.returncode != 0:
            print("FAILED")
            print(result.stderr.decode(errors="replace"))
            fail(f"Could not install {pkg}")
        print("OK")


def download_models():
    MODELS_DIR.mkdir(exist_ok=True)
    for name, url in MODELS.items():
        dest = MODELS_DIR / name
        if dest.exists():
            ok(f"{name} already present ({dest.stat().st_size:,} bytes)")
            continue
        print(f"  Downloading {name} ...", flush=True)
        last_pct = [-1]

        def _progress(count, block, total):
            pct = int(count * block / total * 100)
            if pct != last_pct[0] and pct % 10 == 0:
                print(f"    {pct}%", flush=True)
                last_pct[0] = pct

        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        ok(f"{name} ({dest.stat().st_size:,} bytes)")


def write_hook_launcher():
    """
    Generate the platform-appropriate hook launcher script.
    Windows: kokoro_hook.bat  (uses python.exe so stdin pipe works)
    macOS:   kokoro_hook.sh   (chmod 755)
    """
    if IS_WINDOWS:
        py = REPO_DIR / "venv" / "Scripts" / "python.exe"
        content = f'@echo off\n"{py}" "{HOOK_SCRIPT}"\n'
        HOOK_LAUNCHER.write_text(content, encoding="utf-8")
        ok(f"kokoro_hook.bat written to {HOOK_LAUNCHER}")
    else:
        py = REPO_DIR / "venv" / "bin" / "python"
        content = f'#!/bin/sh\n"{py}" "{HOOK_SCRIPT}"\n'
        HOOK_LAUNCHER.write_text(content, encoding="utf-8")
        # Make executable
        current = HOOK_LAUNCHER.stat().st_mode
        HOOK_LAUNCHER.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        ok(f"kokoro_hook.sh written to {HOOK_LAUNCHER}")


def patch_settings():
    """
    Add (or update) the Stop hook entry in ~/.claude/settings.json.
    Existing settings are preserved; only the hook list is modified.
    """
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if CLAUDE_SETTINGS.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("  Warning: existing settings.json could not be parsed — starting fresh")

    # Use forward slashes — Claude Code runs hooks via bash, which strips backslashes
    hook_command = str(HOOK_LAUNCHER).replace("\\", "/")
    new_hook = {"type": "command", "command": hook_command, "timeout": 10}

    hooks    = settings.setdefault("hooks", {})
    stop     = hooks.setdefault("Stop", [])

    # Remove any previous entry from this repo (identified by tts_hook.py path)
    for section in stop:
        section["hooks"] = [
            h for h in section.get("hooks", [])
            if "tts_hook" not in h.get("command", "").replace("\\", "/")
        ]
    hooks["Stop"] = [s for s in stop if s.get("hooks")]

    # Add the fresh entry
    hooks["Stop"].append({"hooks": [new_hook]})

    CLAUDE_SETTINGS.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    ok(f"settings.json patched at {CLAUDE_SETTINGS}")


# -- main ------------------------------------------------------
def main():
    print("\n" + "=" * 58)
    print("  Local TTS for AI Agents — Setup")
    print("=" * 58)
    print(f"  Install directory: {REPO_DIR}")

    header("Step 1 — Python version check")
    check_python()

    header("Step 2 — Virtual environment")
    create_venv()

    header("Step 3 — Python packages")
    install_packages()

    header("Step 4 — Kokoro-82M model files")
    download_models()

    header("Step 5 — Hook launcher")
    write_hook_launcher()

    header("Step 6 — Claude Code settings.json")
    patch_settings()

    print("\n" + "=" * 58)
    print("  Setup complete!")
    print("=" * 58)
    if IS_WINDOWS:
        py_cmd = r"venv\Scripts\python.exe"
        hotkey_note = (
            "\n3. (Optional) Start the global hotkeys:\n"
            "   - Double-click kokoro_hotkey.ahk\n"
            "   - Requires AutoHotkey v2: https://www.autohotkey.com\n"
            "\nHOTKEYS (while AutoHotkey is running)\n"
            "--------------------------------------\n"
            "  Ctrl+Alt+R   Select any text -> open TTS player\n"
            "  Ctrl+Alt+V   Toggle Claude Code voice responses on/off\n"
            "  Ctrl+Alt+S   Stop playback\n"
        )
    else:
        py_cmd = "venv/bin/python"
        hotkey_note = (
            "\n3. Global hotkeys: AutoHotkey is Windows-only.\n"
            "   Use the CLI directly or assign your own keyboard shortcuts.\n"
        )

    print(f"""
NEXT STEPS
----------
1. Restart Claude Code so the Stop hook is loaded.
   (The hook fires at the end of every response.)

2. Enable voice responses:
   Create the file:  {CLAUDE_DIR / "voice_enabled"}
{hotkey_note}
MANUAL USAGE
------------
  {py_cmd} tts.py "Hello world"
  {py_cmd} tts.py --list-voices
  {py_cmd} tts.py --file myfile.txt --play
""")


if __name__ == "__main__":
    main()
