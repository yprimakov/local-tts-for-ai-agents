"""The "Voice player" shortcut: bring the player back.

Asks the running daemon to show the player (onto a visible screen, redrawn). If the daemon is not
running, starts it, which shows the player. Run it with the venv's pythonw.exe.
"""
import subprocess
import sys
import urllib.request
from pathlib import Path

URL = "http://127.0.0.1:4712/show"

try:
    urllib.request.urlopen(urllib.request.Request(URL, data=b"{}", headers={"content-type": "application/json"}),
                           timeout=3).read()
except OSError:
    here = Path(__file__).resolve().parent
    subprocess.Popen([sys.executable, str(here / "voiced.py")], cwd=str(here),
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
