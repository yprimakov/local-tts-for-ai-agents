"""
Claude Code Stop hook — speaks a short blurb of each response via Kokoro TTS.

Toggle voice responses on/off:
  Create file:  %USERPROFILE%\\.claude\\voice_enabled   (ON)
  Delete file:  %USERPROFILE%\\.claude\\voice_enabled   (OFF)
  Or press Ctrl+Alt+V in the AHK hotkey script.
"""

import json
import os
import re
import subprocess
import sys

# ── config ────────────────────────────────────────────────────
TOGGLE_FILE = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    ".claude", "voice_enabled",
)
PYTHON     = os.path.join(os.path.dirname(__file__), "venv", "Scripts", "pythonw.exe")
TTS_SCRIPT = os.path.join(os.path.dirname(__file__), "tts.py")
TMPFILE    = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "kokoro_hook_blurb.txt")
LOG_FILE   = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "kokoro_hook.log")

MAX_CHARS  = 300   # ~2-3 sentences
MIN_CHARS  = 12    # skip if too short to bother


# ── blurb extractor ───────────────────────────────────────────
def extract_blurb(text: str) -> str:
    """Strip markdown and return the last meaningful sentence(s)."""
    # Remove fenced code blocks
    text = re.sub(r"```[\s\S]*?```", " ", text)
    # Remove inline code
    text = re.sub(r"`[^`\n]+`", " ", text)
    # Remove markdown links  [label](url)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Remove markdown headings
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic markers
    text = re.sub(r"\*+([^*\n]+)\*+", r"\1", text)
    text = re.sub(r"_+([^_\n]+)_+", r"\1", text)
    # Remove bullet/numbered list markers
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if not text:
        return ""

    # Split into paragraphs, take the last non-trivial one
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if len(p.strip()) > MIN_CHARS]
    blurb = paragraphs[-1] if paragraphs else text

    # Flatten any remaining newlines within the blurb
    blurb = re.sub(r"\s+", " ", blurb).strip()

    # Truncate at a sentence boundary near MAX_CHARS
    if len(blurb) > MAX_CHARS:
        window = blurb[:MAX_CHARS + 40]
        # Find the last sentence-ending punctuation within/near the limit
        m = re.search(r"(?<=[.!?])\s", window[:MAX_CHARS])
        if m:
            blurb = window[: m.start()].rstrip()
        else:
            blurb = blurb[:MAX_CHARS].rstrip() + "..."

    return blurb.strip()


# ── main ──────────────────────────────────────────────────────
def main():
    # Diagnostic: always log that we were called
    with open(LOG_FILE, "a") as log:
        log.write(f"[tts_hook] called\n")

    if not os.path.exists(TOGGLE_FILE):
        with open(LOG_FILE, "a") as log:
            log.write(f"[tts_hook] disabled (no toggle file)\n")
        return

    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
        with open(LOG_FILE, "a") as log:
            log.write(f"[tts_hook] parsed JSON, stop_hook_active={data.get('stop_hook_active')}\n")
            log.write(f"[tts_hook] message preview: {data.get('last_assistant_message','')[:80]}\n")
    except Exception as e:
        with open(LOG_FILE, "a") as log:
            log.write(f"[tts_hook] JSON parse error: {e}\n")
        return

    if data.get("stop_hook_active"):
        return  # prevent infinite loops

    message = data.get("last_assistant_message", "")
    if not message or not message.strip():
        return

    blurb = extract_blurb(message)
    if len(blurb) < MIN_CHARS:
        return

    try:
        with open(TMPFILE, "w", encoding="utf-8") as f:
            f.write(blurb)
    except Exception as e:
        with open(LOG_FILE, "a") as log:
            log.write(f"[tts_hook] write error: {e}\n")
        return

    try:
        subprocess.Popen(
            [PYTHON, TTS_SCRIPT, "--file", TMPFILE, "--autoplay"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        with open(LOG_FILE, "a") as log:
            log.write(f"[tts_hook] launch error: {e}\n")


if __name__ == "__main__":
    main()
