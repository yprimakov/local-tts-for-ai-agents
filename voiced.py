"""
Voice queue daemon: one Kokoro load, one FIFO queue, one on-screen player, 7-day history.

Every Claude Code reply used to start its own tts.py process, so replies that finished at the
same time talked over each other and nothing said which session was speaking. Now the Stop hook
(tts_hook.py) only posts the blurb here, and this single process:

  - speaks one item at a time, oldest first, each prefixed with where it came from
    ("From agentic-os-v2." / "From a pytest run.")
  - shows a small always-on-top player: now playing, the queue, pause / skip / mute,
    and the last 7 days of voiceovers, each replayable (right-click: copy the resume command)
  - keeps history as text in history.jsonl (replays are synthesized again, so no audio piles up)

Local only: the control API binds 127.0.0.1 and nothing else.

    venv\\Scripts\\pythonw.exe voiced.py            # the hook starts it on demand
    venv\\Scripts\\python.exe voiced.py --no-window # queue and history without the player
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"  # never 0.0.0.0
PORT = int(os.environ.get("VOICED_PORT", "4712"))
HISTORY_DAYS = 7
VOICE = os.environ.get("VOICED_VOICE", "af_heart")
SPEED = float(os.environ.get("VOICED_SPEED", "1.0"))

_HERE = Path(__file__).resolve().parent


def data_dir() -> Path:
    base = os.environ.get("VOICED_DATA") or (
        Path(os.environ["LOCALAPPDATA"]) / "local-tts" if os.environ.get("LOCALAPPDATA")
        else Path.home() / ".local" / "share" / "local-tts")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def log(msg: str) -> None:
    try:
        with (data_dir() / "voiced.log").open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- where a reply came from
_TEMP_HINTS = ("\\temp\\", "/tmp/", "/var/folders/", "\\appdata\\local\\temp")


def source_of(cwd: str | None, entrypoint: str | None = None) -> dict:
    """Project label for a session's working folder: the git root's name, or a plain description."""
    cwd = (cwd or "").strip()
    low = cwd.replace("/", "\\").lower() + "\\"
    name = ""
    p = Path(cwd) if cwd else None
    try:
        q = p
        while q and q != q.parent:
            if (q / ".git").exists():
                name = q.name
                break
            q = q.parent
    except OSError:
        pass
    if "pytest-of-" in low or "\\pytest-" in low:
        project, spoken = "pytest run", "a pytest run"
    elif not name and any(h.replace("/", "\\") in low for h in _TEMP_HINTS):
        project, spoken = "temp folder", "a temporary folder"
    else:
        name = name or (p.name if p else "") or "unknown session"
        project, spoken = name, name
    headless = (entrypoint or "").lower().startswith("sdk")
    return {"project": project, "spoken": spoken, "headless": headless}


def spoken_text(item: dict) -> str:
    who = item.get("spoken") or item.get("project") or "a session"
    who = re.sub(r"[-_.]+", " ", who).strip()
    return f"From {who}. {item.get('text', '')}"


# ---------------------------------------------------------------- history (text only, 7 days)
class History:
    def __init__(self, path: Path, days: int = HISTORY_DAYS):
        self.path, self.days = path, days
        self._lock = threading.Lock()
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        cutoff = time.time() - self.days * 86400
        try:
            with self.path.open(encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
        except (OSError, ValueError):
            rows = []
        by_id: dict[str, dict] = {}
        for r in rows:  # later lines update earlier ones (status changes)
            if r.get("id"):
                by_id.setdefault(r["id"], {}).update(r)
        self.items = sorted((r for r in by_id.values() if r.get("ts", 0) >= cutoff), key=lambda r: r["ts"])
        self._rewrite()

    def _rewrite(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in self.items:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def add(self, item: dict) -> None:
        with self._lock:
            self.items.append(dict(item))
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def update(self, item_id: str, **fields) -> None:
        with self._lock:
            for r in self.items:
                if r["id"] == item_id:
                    r.update(fields)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"id": item_id, **fields}, ensure_ascii=False) + "\n")

    def get(self, item_id: str) -> dict | None:
        with self._lock:
            return next((dict(r) for r in self.items if r["id"] == item_id), None)

    def prune(self) -> None:
        with self._lock:
            cutoff = time.time() - self.days * 86400
            keep = [r for r in self.items if r.get("ts", 0) >= cutoff]
            if len(keep) != len(self.items):
                self.items = keep
                self._rewrite()

    def recent(self, limit: int = 200) -> list[dict]:
        with self._lock:
            return [dict(r) for r in reversed(self.items[-limit:])]


# ---------------------------------------------------------------- the queue
class VoiceQueue:
    """FIFO: exactly one item is spoken at a time. synth(text) -> wav path; player(wav) -> AudioPlayer-like."""

    def __init__(self, history: History, synth, player, speak_headless: bool = False):
        self.history, self.synth, self.make_player = history, synth, player
        self.speak_headless = speak_headless
        self.queue: deque[dict] = deque()
        self.current: dict | None = None
        self.paused = False
        self.muted = False
        self._player = None
        self._skip = threading.Event()
        self._cv = threading.Condition()
        self._stop = False
        self.window = {"hidden": None}  # set by the player: None, "until next voiceover", "until HH:MM"
        self.show_requested = False
        self.hide_requested = False

    # -- intake
    def say(self, text: str, cwd: str = "", session_id: str = "", entrypoint: str = "",
            silent: str = "") -> dict:
        src = source_of(cwd, entrypoint)
        item = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "text": text.strip(), "cwd": cwd,
                "session_id": session_id, "entrypoint": entrypoint, **src, "status": "queued"}
        if not silent and src["headless"] and not self.speak_headless:
            silent = "headless run"
        if not silent and self.muted:
            silent = "muted"
        if silent:
            item["status"] = f"not spoken: {silent}"
            self.history.add(item)
            return item
        self.history.add(item)
        with self._cv:
            self.queue.append(item)
            self._cv.notify_all()
        return item

    def replay(self, item_id: str) -> dict | None:
        old = self.history.get(item_id)
        if not old:
            return None
        item = {**old, "replay_of": item_id, "status": "queued", "id": uuid.uuid4().hex[:12], "ts": time.time()}
        with self._cv:
            self.queue.appendleft(item)  # a replay is asked for now: it plays next, not last
            self._cv.notify_all()
        return item

    # -- controls
    def pause(self) -> None:
        with self._cv:
            self.paused = True
            if self._player:
                self._player.pause()

    def resume(self) -> None:
        with self._cv:
            self.paused = False
            if self._player:
                self._player.play()
            self._cv.notify_all()

    def skip(self) -> None:
        self._skip.set()
        with self._cv:
            self._cv.notify_all()

    def set_muted(self, muted: bool) -> None:
        """Mute stops the current item and lets new replies through to history without speaking them."""
        with self._cv:
            self.muted = muted
            if muted:
                for it in self.queue:
                    if not it.get("replay_of"):
                        self.history.update(it["id"], status="not spoken: muted")
                self.queue.clear()
        if muted:
            self.skip()

    def clear(self) -> None:
        with self._cv:
            for it in self.queue:
                if not it.get("replay_of"):
                    self.history.update(it["id"], status="cleared")
            self.queue.clear()

    def remove(self, item_id: str) -> bool:
        with self._cv:
            for it in list(self.queue):
                if it["id"] == item_id:
                    self.queue.remove(it)
                    if not it.get("replay_of"):
                        self.history.update(item_id, status="removed")
                    return True
        return False

    def state(self) -> dict:
        with self._cv:
            p = self._player
            cur = dict(self.current) if self.current else None
            if cur and p is not None:
                cur["position"], cur["duration"] = round(p.position, 1), round(p.duration, 1)
            return {"paused": self.paused, "muted": self.muted, "current": cur, "window": dict(self.window),
                    "queue": [dict(i) for i in self.queue]}

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._skip.set()

    # -- the one speaker
    def run(self) -> None:
        while True:
            with self._cv:
                while not self._stop and (self.paused or not self.queue):
                    self._cv.wait(1.0)
                if self._stop:
                    return
                item = self.queue.popleft()
                self.current = {**item, "status": "preparing"}
            self._skip.clear()
            status = "spoken"
            try:
                wav = self.synth(spoken_text(item))
                if self._skip.is_set():
                    status = "skipped"
                else:
                    player = self.make_player(wav)
                    with self._cv:
                        self._player = player
                        self.current["status"] = "playing"
                        if not self.paused:
                            player.play()
                    while not self._stop:
                        if self._skip.wait(0.05):
                            status = "skipped"
                            break
                        if player.ended:
                            break
                    player.close()
                    try:
                        os.remove(wav)
                    except OSError:
                        pass
            except Exception as e:  # a bad item never stops the queue
                status = f"failed: {e}"[:200]
            with self._cv:
                self._player, self.current = None, None
            if not item.get("replay_of"):
                self.history.update(item["id"], status=status, spoken_at=time.time())


# ---------------------------------------------------------------- Kokoro (loaded once)
class Kokoro:
    def __init__(self):
        self._k = None
        self._lock = threading.Lock()

    def _load(self):
        if self._k is None:
            import onnxruntime as rt
            from kokoro_onnx import Kokoro as K
            sess = rt.InferenceSession(str(_HERE / "models" / "kokoro-v1.0.onnx"), providers=["CPUExecutionProvider"])
            self._k = K.from_session(sess, str(_HERE / "models" / "voices-v1.0.bin"))
        return self._k

    def __call__(self, text: str) -> str:
        import numpy as np
        import wave
        with self._lock:
            samples, sr = self._load().create(text, voice=VOICE, speed=SPEED, lang="en-us")
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="voiced_")
        os.close(fd)
        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
        return path


def default_player(wav: str):
    from tts import AudioPlayer
    return AudioPlayer(wav)


# ---------------------------------------------------------------- local control API (127.0.0.1)
def make_server(vq: VoiceQueue | None, port: int = PORT) -> ThreadingHTTPServer:
    """Bind first (vq may be attached later as server.vq): a second copy fails here and exits."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code: int, obj) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("content-length") or 0)
            try:
                d = json.loads(self.rfile.read(n) or b"{}")
                return d if isinstance(d, dict) else {}
            except ValueError:
                return {}

        def do_GET(self):
            vq = self.server.vq
            if self.path == "/state":
                return self._json(200, {"app": "voiced", **vq.state()})
            if self.path.startswith("/history"):
                return self._json(200, vq.history.recent())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            # Browsers can post here too; refuse anything that is not this machine's own tools.
            if self.headers.get("origin"):
                return self._json(403, {"error": "no browser requests"})
            vq = self.server.vq
            d = self._body()
            if self.path == "/say":
                text = str(d.get("text") or "").strip()
                if not text:
                    return self._json(400, {"error": "text required"})
                it = vq.say(text[:2000], str(d.get("cwd") or ""), str(d.get("session_id") or ""),
                            str(d.get("entrypoint") or ""), str(d.get("silent") or ""))
                return self._json(200, it)
            if self.path == "/control":
                a, iid = d.get("action"), str(d.get("id") or "")
                if a == "pause": vq.pause()
                elif a == "resume": vq.resume()
                elif a == "skip": vq.skip()
                elif a == "mute": vq.set_muted(True)
                elif a == "unmute": vq.set_muted(False)
                elif a == "clear": vq.clear()
                elif a == "remove": vq.remove(iid)
                elif a == "show": vq.show_requested = True
                elif a == "hide": vq.hide_requested = True
                elif a == "replay":
                    if not vq.replay(iid):
                        return self._json(404, {"error": "no such item"})
                else:
                    return self._json(400, {"error": "unknown action"})
                return self._json(200, vq.state())
            self._json(404, {"error": "not found"})

    srv = ThreadingHTTPServer((HOST, port), H)
    srv.vq = vq
    return srv


# ---------------------------------------------------------------- the on-screen player
BG, SURFACE, HOVER, BORDER = "#151518", "#1d1d22", "#27272e", "#30303a"
FG, MUTED, FAINT = "#eeeef2", "#a3a3ae", "#6e6e7a"
ACCENT, ACCENT_HI, GREEN, AMBER = "#ff7a1a", "#ff9447", "#4cc38a", "#f0b44c"

# Segoe Fluent Icons / MDL2 glyphs on Windows; plain symbols elsewhere
_ICONS = {
    "play": ("", "▶"), "pause": ("", "❙❙"), "next": ("", "⏭"),
    "volume": ("", "\U0001f50a"), "mute": ("", "\U0001f507"), "down": ("", "▾"),
    "up": ("", "▴"), "close": ("", "✕"), "replay": ("", "▶"),
    "remove": ("", "✕"),
}
_STATUS_TAGS = {"not spoken: headless run": "headless", "not spoken: muted": "muted",
                "not spoken: do not disturb": "do not disturb", "skipped": "skipped", "cleared": "cleared",
                "removed": "removed"}


class Tooltip:
    def __init__(self, w, text_fn, ui):
        self.w, self.text_fn, self.ui, self.tip, self.job = w, text_fn, ui, None, None
        w.bind("<Enter>", self._arm, add="+")
        w.bind("<Leave>", self._hide, add="+")
        w.bind("<ButtonPress>", self._hide, add="+")

    def _arm(self, _e):
        self.job = self.w.after(450, self._show)

    def _show(self):
        tk = self.ui.tk
        self.tip = t = tk.Toplevel(self.w)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        tk.Label(t, text=self.text_fn(), bg="#000000", fg=FG, font=self.ui.f_small, padx=6, pady=3).pack()
        t.geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 4}")

    def _hide(self, _e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


class Widget:
    """Always-on-top card. Idle: a slim bar. Playing: what, from where, how far. Expanded: up next + 7 days."""

    def __init__(self, vq: VoiceQueue):
        import tkinter as tk
        import tkinter.font as tkfont
        if sys.platform == "win32":  # crisp text on high-DPI screens; sizes below scale with it
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        self.tk, self.vq = tk, vq
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *exc: log(
            "player error: " + "".join(__import__("traceback").format_exception(*exc))[-1500:])
        self.root.title("Voice queue")
        self.S = max(1.0, self.root.winfo_fpixels("1i") / 96)
        self.W = self.px(372)
        fams = set(tkfont.families(self.root))
        ui = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans") if f in fams), "TkDefaultFont")
        semi = next((f for f in ("Segoe UI Variable Display Semib", "Segoe UI Semibold") if f in fams), None)
        icon = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        self.f_body = (ui, 10)
        self.f_small = (ui, 8)
        self.f_tag = (ui, 7, "bold")
        self.f_title = (semi, 11) if semi else (ui, 11, "bold")
        self.f_row = (semi, 9) if semi else (ui, 9, "bold")
        self.f_icon = (icon, 11) if icon else (ui, 11)
        self.f_icon_big = (icon, 13) if icon else (ui, 13)
        self._glyph = 0 if icon else 1
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BORDER)
        self.expanded = False
        self.hidden_until = 0.0
        self.hidden_for_next = None  # id playing when hidden ("" if none); a different item brings it back
        self._queue_sig = None
        self._hist_sig = None
        self._open_row = None
        self._mode = None
        self._build()
        self._place()
        self.root.after(50, self._round_corners)
        self._tick()

    # ---------------------------------------------------------- helpers
    def px(self, n: float) -> int:
        return int(round(n * self.S))

    def glyph(self, name: str) -> str:
        return _ICONS[name][self._glyph]

    def _icon_btn(self, parent, name, cmd, tip, big=False, primary=False):
        tk = self.tk
        bg = ACCENT if primary else BG
        hi = ACCENT_HI if primary else HOVER
        b = tk.Label(parent, text=self.glyph(name), bg=bg, fg="#111111" if primary else MUTED,
                     font=self.f_icon_big if big else self.f_icon, cursor="hand2",
                     padx=self.px(12 if big else 6), pady=self.px(6 if big else 3))
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=hi, fg="#111111" if primary else FG))
        b.bind("<Leave>", lambda e: b.configure(bg=bg, fg="#111111" if primary else MUTED))
        b.icon_name = name
        Tooltip(b, tip if callable(tip) else (lambda: tip), self)
        return b

    def _set_icon(self, b, name):
        if b.icon_name != name:
            b.icon_name = name
            b.configure(text=self.glyph(name))

    def _round_corners(self):
        if sys.platform != "win32":
            return
        try:  # Windows 11 rounded corners for a borderless window
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    @staticmethod
    def _when(ts: float) -> str:
        return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")

    @staticmethod
    def _day(ts: float) -> str:
        d = time.localtime(ts)
        today = time.localtime()
        days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(d[:3] + (0, 0, 0, 0, 0, -1))) / 86400
        if days < 0.5:
            return "Today"
        if days < 1.5:
            return "Yesterday"
        return time.strftime("%A, %b %d", d).replace(" 0", " ")

    @staticmethod
    def _clock(sec: float) -> str:
        sec = max(0, int(sec))
        return f"{sec // 60}:{sec % 60:02d}"

    # ---------------------------------------------------------- layout
    def _build(self):
        tk = self.tk
        p = self.px
        self.card = card = tk.Frame(self.root, bg=BG, padx=p(14), pady=p(10))
        card.pack(fill="both", expand=True, padx=1, pady=1)

        # header: status on the left, window actions on the right
        top = tk.Frame(card, bg=BG)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=p(8), height=p(8), bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(0, 0, p(8), p(8), fill=FAINT, width=0)
        self.dot.pack(side="left", pady=p(1))
        self.status = tk.Label(top, text="Idle", bg=BG, fg=MUTED, font=self.f_small, anchor="w")
        self.status.pack(side="left", padx=(p(6), 0))
        self.badge = tk.Label(top, text="", bg=SURFACE, fg=MUTED, font=self.f_small, padx=p(6))
        self.b_close = self._icon_btn(top, "close", self.hide_until_next, "Hide until the next voiceover (Esc)")
        self.b_close.pack(side="right")
        self.b_more = self._icon_btn(top, "down", self.toggle_expand,
                                     lambda: "Hide queue and history" if self.expanded else "Show queue and history")
        self.b_more.pack(side="right")
        self.b_mute = self._icon_btn(top, "volume", self.toggle_mute,
                                     lambda: "Unmute" if self.vq.muted else "Mute: new replies go to history only")
        self.b_mute.pack(side="right")
        for w in (card, top, self.status, self.dot):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<Button-3>", self._menu)

        # idle line: the last voiceover, one click to hear it again
        self.idle = tk.Frame(card, bg=BG)
        self.idle_text = tk.Label(self.idle, text="", bg=BG, fg=FAINT, font=self.f_small, anchor="w")
        self.idle_text.pack(side="left", fill="x", expand=True)
        self.idle_replay = self._icon_btn(self.idle, "replay", self._replay_last, "Replay the last voiceover")
        self.idle_replay.pack(side="right")

        # now playing
        self.now = tk.Frame(card, bg=BG)
        self.project = tk.Label(self.now, text="", bg=BG, fg=ACCENT, font=self.f_title, anchor="w")
        self.project.pack(fill="x", pady=(p(6), 0))
        self.meta = tk.Label(self.now, text="", bg=BG, fg=FAINT, font=self.f_small, anchor="w")
        self.meta.pack(fill="x")
        self.body = tk.Label(self.now, text="", bg=BG, fg=FG, font=self.f_body, anchor="w", justify="left",
                             wraplength=self.W - p(30))
        self.body.pack(fill="x", pady=(p(6), p(8)))
        prog = tk.Frame(self.now, bg=BG)
        prog.pack(fill="x")
        self.t_pos = tk.Label(prog, text="0:00", bg=BG, fg=FAINT, font=self.f_small)
        self.t_pos.pack(side="left")
        self.t_dur = tk.Label(prog, text="0:00", bg=BG, fg=FAINT, font=self.f_small)
        self.t_dur.pack(side="right")
        self.bar = tk.Canvas(prog, height=p(4), bg=HOVER, highlightthickness=0)
        self.bar.pack(side="left", fill="x", expand=True, padx=p(8))
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, p(4), fill=ACCENT, width=0)
        ctl = tk.Frame(self.now, bg=BG)
        ctl.pack(fill="x", pady=(p(8), 0))
        self.b_pause = self._icon_btn(ctl, "pause", self.toggle_pause,
                                      lambda: "Resume (Space)" if self.vq.paused else "Pause (Space)",
                                      big=True, primary=True)
        self.b_pause.pack(side="left")
        self.b_skip = self._icon_btn(ctl, "next", self.vq.skip, "Skip to the next one (Right arrow)", big=True)
        self.b_skip.pack(side="left", padx=(p(6), 0))
        self.next_hint = tk.Label(ctl, text="", bg=BG, fg=FAINT, font=self.f_small, anchor="e")
        self.next_hint.pack(side="right")

        # expanded: up next + history
        self.more = tk.Frame(card, bg=BG)
        tk.Frame(self.more, bg=BORDER, height=1).pack(fill="x", pady=(p(10), p(6)))
        self.q_head = tk.Label(self.more, text="UP NEXT", bg=BG, fg=FAINT, font=self.f_tag, anchor="w")
        self.q_box = tk.Frame(self.more, bg=BG)
        self.h_head = tk.Label(self.more, text="LAST 7 DAYS", bg=BG, fg=FAINT, font=self.f_tag, anchor="w")
        self.h_head.pack(fill="x", pady=(p(4), p(2)))
        wrap = tk.Frame(self.more, bg=BG)
        wrap.pack(fill="both", expand=True)
        self.h_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, height=p(300), width=self.W - p(30))
        self.h_canvas.pack(side="left", fill="both", expand=True)
        self.h_box = tk.Frame(self.h_canvas, bg=BG)
        self.h_win = self.h_canvas.create_window(0, 0, window=self.h_box, anchor="nw")
        self.h_box.bind("<Configure>", lambda e: self.h_canvas.configure(scrollregion=self.h_canvas.bbox("all")))
        self.h_canvas.bind("<Configure>", lambda e: self.h_canvas.itemconfigure(self.h_win, width=e.width))
        self.root.bind_all("<MouseWheel>", self._wheel)
        self.root.bind_all("<Button-4>", lambda e: self.h_canvas.yview_scroll(-2, "units"))
        self.root.bind_all("<Button-5>", lambda e: self.h_canvas.yview_scroll(2, "units"))

        self.root.bind("<Escape>", lambda e: self.hide_until_next())
        self.root.bind("<space>", lambda e: self.toggle_pause())
        self.root.bind("<Right>", lambda e: self.vq.skip())
        self.root.bind("<m>", lambda e: self.toggle_mute())

    def _wheel(self, e):
        if self.expanded:
            self.h_canvas.yview_scroll(int(-e.delta / 40) or (-1 if e.delta > 0 else 1), "units")

    def _place(self):
        self.root.update_idletasks()
        x = self.root.winfo_screenwidth() - self.W - self.px(24)
        self.root.geometry(f"{self.W}x{self.root.winfo_reqheight()}+{x}+{self.px(24)}")

    def _fit(self):
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        if h != self.root.winfo_height():
            self.root.geometry(f"{self.W}x{h}+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---------------------------------------------------------- rows
    def _row(self, parent, item, kind):
        """One queue or history row. Hover shows its action; click opens details (history)."""
        tk, p = self.tk, self.px
        row = tk.Frame(parent, bg=BG, cursor="hand2")
        row.pack(fill="x", pady=(0, 1))
        grid = tk.Frame(row, bg=BG)
        grid.pack(fill="x", padx=p(6), pady=p(4))
        grid.columnconfigure(1, weight=1)
        line = tk.Frame(grid, bg=BG)
        line.grid(row=0, column=1, sticky="w")
        widgets = [row, grid, line]
        if kind == "history":
            t = tk.Label(grid, text=self._when(item["ts"]), bg=BG, fg=FAINT, font=self.f_small, width=8, anchor="nw")
            t.grid(row=0, column=0, rowspan=2, sticky="nw", pady=(p(1), 0))
            widgets.append(t)
        name = tk.Label(line, text=item.get("project") or "?", bg=BG, fg=FG, font=self.f_row, anchor="w")
        name.pack(side="left")
        widgets.append(name)
        tag = _STATUS_TAGS.get(item.get("status", ""), "" if item.get("status") in ("spoken", "queued", None)
                               else item.get("status", "").split(":")[0][:14])
        if tag:
            tl = tk.Label(line, text=tag.upper(), bg=SURFACE, fg=MUTED, font=self.f_tag, padx=p(4))
            tl.pack(side="left", padx=(p(6), 0))
            widgets.append(tl)
        act = self._icon_btn(grid, "replay" if kind == "history" else "remove",
                             (lambda: self._replay(item["id"])) if kind == "history"
                             else (lambda: self.vq.remove(item["id"])),
                             "Replay" if kind == "history" else "Remove from the queue")
        act.configure(fg=BG)
        act.grid(row=0, column=2, rowspan=2, sticky="e")
        snip = tk.Label(grid, text=item.get("text", "")[:80], bg=BG, fg=MUTED, font=self.f_small, anchor="w")
        snip.grid(row=1, column=1, sticky="we")
        widgets.append(snip)

        def paint(on):
            c = HOVER if on else BG
            for w in widgets:
                w.configure(bg=c)
            act.configure(bg=c, fg=FG if on else c)

        def leave(e):
            w = row.winfo_containing(e.x_root, e.y_root)
            while w is not None and w is not row:
                w = getattr(w, "master", None)
            if w is not row:
                paint(False)

        for w in widgets:
            w.bind("<Enter>", lambda e: paint(True), add="+")
            w.bind("<Leave>", leave, add="+")
            if kind == "history" and w is not act:
                w.bind("<Button-1>", lambda e, i=item["id"]: self._toggle_detail(i))
                w.bind("<Double-Button-1>", lambda e, i=item["id"]: self._replay(i))
                w.bind("<Button-3>", lambda e, i=item["id"]: self._row_menu(e, i))
        if kind == "history" and self._open_row == item["id"]:
            self._detail(row, item)
        return row

    def _detail(self, parent, item):
        tk, p = self.tk, self.px
        d = tk.Frame(parent, bg=SURFACE, padx=p(10), pady=p(8))
        d.pack(fill="x", padx=p(6), pady=(0, p(6)))
        tk.Label(d, text=item.get("text", ""), bg=SURFACE, fg=FG, font=self.f_small, anchor="w", justify="left",
                 wraplength=self.W - p(70)).pack(fill="x")
        where = item.get("cwd") or "unknown folder"
        sid = (item.get("session_id") or "")[:8]
        tk.Label(d, text=f"{where}" + (f"  ·  session {sid}" if sid else ""), bg=SURFACE, fg=FAINT,
                 font=self.f_small, anchor="w", justify="left", wraplength=self.W - p(70)).pack(fill="x", pady=(p(4), p(6)))
        bar = tk.Frame(d, bg=SURFACE)
        bar.pack(fill="x")

        def link(text, cmd):
            l = tk.Label(bar, text=text, bg=SURFACE, fg=ACCENT, font=self.f_small, cursor="hand2")
            l.bind("<Button-1>", lambda e: cmd())
            l.bind("<Enter>", lambda e: l.configure(fg=ACCENT_HI))
            l.bind("<Leave>", lambda e: l.configure(fg=ACCENT))
            l.pack(side="left", padx=(0, p(14)))
            return l

        link("Replay", lambda: self._replay(item["id"]))
        if item.get("session_id"):
            link("Copy resume command", lambda: self._copy(self._resume_cmd(item), "Resume command copied"))
        if item.get("cwd"):
            link("Copy folder", lambda: self._copy(item["cwd"], "Folder copied"))
        self.flash = tk.Label(bar, text="", bg=SURFACE, fg=GREEN, font=self.f_small)
        self.flash.pack(side="right")

    @staticmethod
    def _resume_cmd(item) -> str:
        return f'cd "{item.get("cwd")}"; claude --resume {item["session_id"]}'

    def _refresh_queue(self, q):
        sig = tuple(i["id"] for i in q)
        if sig == self._queue_sig:
            return
        self._queue_sig = sig
        for w in self.q_box.winfo_children():
            w.destroy()
        if q:
            self.q_head.pack(fill="x", pady=(0, p2 := self.px(2)), before=self.h_head)
            self.q_box.pack(fill="x", pady=(0, self.px(8)), before=self.h_head)
            for i in q[:6]:
                self._row(self.q_box, i, "queue")
        else:
            self.q_head.pack_forget()
            self.q_box.pack_forget()

    def _refresh_history(self, force=False):
        rows = sorted(self.vq.history.recent(150), key=lambda r: r.get("ts", 0), reverse=True)
        sig = (tuple((r["id"], r.get("status")) for r in rows), self._open_row)
        if sig == self._hist_sig and not force:
            return
        self._hist_sig = sig
        for w in self.h_box.winfo_children():
            w.destroy()
        if not rows:
            self.tk.Label(self.h_box, text="Nothing yet. Voiceovers from the last 7 days show up here.", bg=BG,
                          fg=FAINT, font=self.f_small, anchor="w").pack(fill="x", pady=self.px(8))
        day = None
        for r in rows:
            d = self._day(r["ts"])
            if d != day:
                day = d
                self.tk.Label(self.h_box, text=d, bg=BG, fg=MUTED, font=self.f_row, anchor="w").pack(
                    fill="x", padx=self.px(6), pady=(self.px(8), self.px(2)))
            self._row(self.h_box, r, "history")

    # ---------------------------------------------------------- actions
    def _toggle_detail(self, item_id):
        self._open_row = None if self._open_row == item_id else item_id
        self._refresh_history()

    def _replay(self, item_id):
        if self.vq.muted:
            self.vq.set_muted(False)
        self.vq.replay(item_id)

    def _replay_last(self):
        rows = [r for r in self.vq.history.recent(20) if r.get("status") == "spoken"] or self.vq.history.recent(1)
        if rows:
            self._replay(rows[0]["id"])

    def _row_menu(self, e, item_id):
        r = self.vq.history.get(item_id)
        if not r:
            return
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label="Replay", command=lambda: self._replay(item_id))
        if r.get("session_id"):
            m.add_command(label="Copy resume command", command=lambda: self._copy(self._resume_cmd(r)))
        if r.get("cwd"):
            m.add_command(label="Copy folder path", command=lambda: self._copy(r["cwd"]))
        m.tk_popup(e.x_root, e.y_root)

    def _copy(self, s, note=""):
        self.root.clipboard_clear()
        self.root.clipboard_append(s)
        if note and getattr(self, "flash", None) is not None:
            try:
                self.flash.configure(text=note)
                self.flash.after(1800, lambda: self.flash.winfo_exists() and self.flash.configure(text=""))
            except self.tk.TclError:
                pass

    def _menu(self, e):
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label="Hide until next voiceover   Esc", command=self.hide_until_next)
        m.add_command(label="Hide for 1 hour, even while speaking", command=lambda: self._hide(3600))
        m.add_command(label="Clear the queue", command=self.vq.clear)
        m.add_separator()
        m.add_command(label="Quit voice queue", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _hide(self, secs):
        self.hidden_for_next = None
        self.hidden_until = time.time() + secs
        self.root.withdraw()

    def show(self):
        self.hidden_until, self.hidden_for_next = 0.0, None
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(50, self._round_corners)

    def hide_until_next(self):
        """Out of the way now; back as soon as a different voiceover starts (the current one keeps playing)."""
        cur = self.vq.state()["current"]
        self.hidden_until = 0.0
        self.hidden_for_next = (cur or {}).get("id", "")
        self.root.withdraw()

    def quit(self):
        self.vq.stop()
        self.root.destroy()

    def toggle_expand(self):
        self.expanded = not self.expanded
        self._set_icon(self.b_more, "up" if self.expanded else "down")
        if self.expanded:
            self.more.pack(fill="both", expand=True)
            self._queue_sig = self._hist_sig = None
        else:
            self.more.pack_forget()
        self._tick_once()
        self._fit()

    def toggle_pause(self):
        self.vq.resume() if self.vq.paused else self.vq.pause()

    def toggle_mute(self):
        self.vq.set_muted(not self.vq.muted)

    # ---------------------------------------------------------- refresh
    def _tick(self):
        try:
            self._tick_once()
        except Exception as e:  # one bad frame must not stop the player from ever updating again
            log(f"tick error: {e!r}")
        if not self.vq._stop:
            self.root.after(250, self._tick)

    def _tick_once(self):
        if self.vq._stop:
            self.root.destroy()
            return
        st = self.vq.state()
        cur, q = st["current"], st["queue"]
        if self.vq.show_requested:
            self.vq.show_requested = False
            self.show()
        if self.vq.hide_requested:
            self.vq.hide_requested = False
            self.hide_until_next()
            cur = self.vq.state()["current"]
        if self.hidden_until and time.time() >= self.hidden_until:
            self.show()
        if self.hidden_for_next is not None and cur and cur.get("id") != self.hidden_for_next:
            log(f"showing again for {cur.get('id')}")
            self.show()
        self.vq.window["hidden"] = ("until next voiceover" if self.hidden_for_next is not None else
                                    time.strftime("until %H:%M", time.localtime(self.hidden_until))
                                    if self.hidden_until else None)

        # status
        if st["muted"]:
            status, color = "Muted · replies go to history only", FAINT
        elif st["paused"]:
            status, color = "Paused", AMBER
        elif cur:
            status, color = ("Replaying" if cur.get("replay_of") else "Playing"), ACCENT
        else:
            status, color = "Idle", GREEN
        self.status.configure(text=status)
        self.dot.itemconfigure(self.dot_id, fill=color)
        self._set_icon(self.b_mute, "mute" if st["muted"] else "volume")
        self.b_mute.configure(fg=ACCENT if st["muted"] else self.b_mute.cget("fg"))
        if q:
            self.badge.configure(text=f"{len(q)} up next")
            self.badge.pack(side="left", padx=(self.px(8), 0))
        else:
            self.badge.pack_forget()

        # body
        mode = "now" if (cur or q) else "idle"
        if mode != self._mode:
            self._mode = mode
            self.now.pack_forget()
            self.idle.pack_forget()
            (self.now if mode == "now" else self.idle).pack(fill="x", before=self.more if self.expanded else None)
        if mode == "now":
            show = cur or {**q[0], "status": "preparing"}
            self.project.configure(text=show.get("project") or "?", fg=ACCENT if cur and not st["paused"] else FG)
            sid = (show.get("session_id") or "")[:8]
            self.meta.configure(text=self._when(show["ts"]) + (f"  ·  session {sid}" if sid else "")
                                + ("  ·  replay" if show.get("replay_of") else ""))
            text = show.get("text", "")
            self.body.configure(text=text if len(text) <= 230 else text[:227].rstrip() + "…")
            pos, dur = (cur or {}).get("position", 0), (cur or {}).get("duration", 0)
            self.t_pos.configure(text=self._clock(pos))
            self.t_dur.configure(text=self._clock(dur) if dur else "--:--")
            w = self.bar.winfo_width()
            self.bar.coords(self.bar_fill, 0, 0, int(w * min(pos / dur, 1)) if dur else 0, self.px(4))
            self._set_icon(self.b_pause, "play" if st["paused"] else "pause")
            self.next_hint.configure(text=(f"Next: {q[0].get('project')}" if q else "Nothing after this"))
        else:
            last = self.vq.history.recent(1)
            if last:
                self.idle_text.configure(text=f"Last: {last[0].get('project')} · {self._when(last[0]['ts'])}")
                self.idle_replay.pack(side="right")
            else:
                self.idle_text.configure(text="Waiting for the next reply")
                self.idle_replay.pack_forget()

        if self.expanded:
            self._refresh_queue(q)
            self._refresh_history()
        try:
            self.vq.window["geometry"] = [self.root.winfo_x(), self.root.winfo_y(),
                                          self.root.winfo_width(), self.root.winfo_height()]
        except self.tk.TclError:
            pass
        self._fit()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-window", action="store_true", help="queue and history only, no on-screen player")
    ap.add_argument("--speak-headless", action="store_true",
                    help="also speak replies from headless (claude -p / SDK) runs; default: history only")
    args = ap.parse_args(argv)

    try:
        server = make_server(None, PORT)  # bind first: a second copy exits here, before loading the model
    except OSError:
        return 0
    history = History(data_dir() / "history.jsonl")
    vq = VoiceQueue(history, Kokoro(), default_player,
                    speak_headless=args.speak_headless or os.environ.get("VOICED_SPEAK_HEADLESS") == "1")
    server.vq = vq
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=vq.run, daemon=True).start()

    def prune():
        while True:
            history.prune()
            time.sleep(3600)
    threading.Thread(target=prune, daemon=True).start()

    if args.no_window:
        try:
            while not vq._stop:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        Widget(vq).run()
    vq.stop()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
