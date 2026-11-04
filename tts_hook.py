"""
Claude Code Stop hook — hands a short blurb of each response to the voice queue (voiced.py),
which speaks one at a time, says which project it came from, and keeps a 7-day history.

Do Not Disturb (Ember Launchpad): ~/.claude/voice_dnd pauses speech, optionally until a time.

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
# Do Not Disturb (set from Ember Launchpad): file present = muted. Content is an
# epoch-ms end time, or empty for "until turned off". Expired files are removed here.
DND_FILE = os.path.join(os.path.expanduser("~"), ".claude", "voice_dnd")

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
def dnd_active() -> bool:
    import time
    try:
        with open(DND_FILE, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return False
    if raw:
        try:
            if float(raw) <= time.time() * 1000:
                os.remove(DND_FILE)
                return False
        except ValueError:
            pass
    return True


# ── voice queue daemon (voiced.py) ─────────────────────────────
VOICED_URL = "http://127.0.0.1:" + os.environ.get("VOICED_PORT", "4712")
VOICED     = os.path.join(_HERE, "voiced.py")


def _log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as log:
            log.write(f"[tts_hook] {msg}\n")
    except OSError:
        pass


def _post(path: str, payload: dict, timeout: float = 1.5) -> bool:
    import urllib.request
    req = urllib.request.Request(VOICED_URL + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def _start_daemon() -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([PYTHON, VOICED], cwd=_HERE, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)


def send(payload: dict, autostart: bool = True) -> bool:
    """Hand the blurb to the one voice queue; start it if it is not running. Never speaks on its own."""
    import time
    if _post("/say", payload):
        return True
    if not autostart:
        return False
    _start_daemon()
    deadline = time.time() + 7  # the hook timeout is 10 s
    while time.time() < deadline:
        time.sleep(0.4)
        if _post("/say", payload):
            return True
    return False


def main():
    _log("called")
    if not os.path.exists(TOGGLE_FILE):
        _log("disabled (no toggle file)")
        return

    try:
        # Read stdin as raw bytes and decode as UTF-8 explicitly: the Windows locale (cp1252) mangles
        # em-dashes and other non-ASCII into mojibake.
        data = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except Exception as e:
        _log(f"JSON parse error: {e}")
        return

    if data.get("stop_hook_active"):
        return  # prevent infinite loops

    message = data.get("last_assistant_message", "")
    if not message or not message.strip():
        return

    blurb = normalize_for_tts(extract_blurb(message))
    if len(blurb) < MIN_CHARS:
        return

    payload = {
        "text": blurb,
        "cwd": data.get("cwd") or os.getcwd(),
        "session_id": data.get("session_id") or "",
        # "cli" for an interactive session; "sdk-*" for claude -p and SDK runs (spoken only with --speak-headless)
        "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT", ""),
    }
    dnd = dnd_active()
    if dnd:
        payload["silent"] = "do not disturb"
    _log(f"{payload['entrypoint'] or '?'} {payload['cwd']} {payload['session_id'][:8]}: {blurb[:80]}")
    if not send(payload, autostart=not dnd):
        _log("voice queue unreachable" if not dnd else "do not disturb (queue not running, not logged)")


if __name__ == "__main__":
    main()
