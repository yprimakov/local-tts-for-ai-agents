"""
Claude Code Stop hook — speaks a short blurb of each response via Kokoro TTS.

Toggle voice responses on/off:
  Create file:  ~/.claude/voice_enabled   (ON)
  Delete file:  ~/.claude/voice_enabled   (OFF)
  Windows: also toggle with Ctrl+Alt+V in the AHK hotkey script.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata

# ── config ────────────────────────────────────────────────────
TOGGLE_FILE = os.path.join(os.path.expanduser("~"), ".claude", "voice_enabled")

_HERE = os.path.dirname(os.path.abspath(__file__))
if sys.platform == "win32":
    PYTHON = os.path.join(_HERE, "venv", "Scripts", "pythonw.exe")
else:
    PYTHON = os.path.join(_HERE, "venv", "bin", "python")

TTS_SCRIPT = os.path.join(_HERE, "tts.py")
TMPFILE    = os.path.join(tempfile.gettempdir(), "kokoro_hook_blurb.txt")
LOG_FILE   = os.path.join(tempfile.gettempdir(), "kokoro_hook.log")

MAX_CHARS  = 300   # ~2-3 sentences
MIN_CHARS  = 12    # skip if too short to bother


# ── text normalization for Kokoro / espeak-ng ─────────────────
# Map Unicode punctuation/symbols to safe ASCII equivalents.
# espeak-ng pronounces unknown codepoints by Unicode name (e.g. "•" → "bullet"),
# which sounds bad. We normalize to characters the phonemizer handles cleanly.
_TTS_CHAR_MAP = {
    # Dashes → comma for a natural pause
    "\u2014": ", ",  # — em-dash
    "\u2013": ", ",  # – en-dash
    "\u2015": ", ",  # ― horizontal bar
    "\u2212": "-",   # − minus sign
    # Quotes → straight ASCII
    "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
    "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
    "\u00AB": '"', "\u00BB": '"',
    "\u2039": "'", "\u203A": "'",
    # Ellipsis → three dots (espeak handles this as a pause)
    "\u2026": "...",
    # Bullets and list glyphs → space
    "\u2022": " ", "\u2023": " ", "\u25E6": " ",
    "\u2043": " ", "\u204C": " ", "\u204D": " ",
    "\u25AA": " ", "\u25AB": " ", "\u25CF": " ", "\u25CB": " ",
    # Arrows → space
    "\u2190": " ", "\u2192": " ", "\u2191": " ", "\u2193": " ",
    "\u21D0": " ", "\u21D2": " ", "\u21D1": " ", "\u21D3": " ",
    # Check marks / crosses → space
    "\u2713": " ", "\u2714": " ", "\u2715": " ", "\u2717": " ", "\u2718": " ",
    # Common typographic spaces → regular space
    "\u00A0": " ", "\u2002": " ", "\u2003": " ", "\u2009": " ",
    "\u200A": " ", "\u202F": " ", "\u205F": " ", "\u3000": " ",
    # Zero-width / formatting characters → drop
    "\u200B": "", "\u200C": "", "\u200D": "", "\uFEFF": "",
}

# Emoji and pictographic ranges — espeak reads these as Unicode names ("direct hit")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"  # alchemical
    "\U0001F780-\U0001F7FF"  # geometric extended
    "\U0001F800-\U0001F8FF"  # arrows-C
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"  # chess
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs ext-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U00002700-\U000027BF"  # dingbats
    "]+",
    flags=re.UNICODE,
)


def normalize_for_tts(text: str) -> str:
    """Clean text for Kokoro / espeak-ng to avoid Unicode-name mispronunciations."""
    # NFKC: fold compatibility forms (fullwidth digits, ligatures, etc.)
    text = unicodedata.normalize("NFKC", text)
    # Strip emoji
    text = _EMOJI_RE.sub(" ", text)
    # Apply punctuation map
    text = text.translate(str.maketrans(_TTS_CHAR_MAP))
    # Drop any remaining non-printable / control chars (keep newlines and tabs)
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c)[0] != "C")
    # Collapse runs of spaces and stray punctuation that may have doubled up
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"(,\s*){2,}", ", ", text)
    return text.strip()


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
        # Read stdin as raw bytes and decode as UTF-8 explicitly.
        # Default text-mode stdin uses the Windows locale (cp1252), which
        # mangles em-dashes and other non-ASCII into mojibake (e.g. "â€"").
        raw = sys.stdin.buffer.read().decode("utf-8")
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
    blurb = normalize_for_tts(blurb)
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
