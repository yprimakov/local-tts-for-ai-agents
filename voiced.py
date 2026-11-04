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
            return {"paused": self.paused, "muted": self.muted, "current": cur,
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
BG, CARD, LINE, FG, DIM, ACCENT = "#121214", "#1b1b1f", "#2a2a30", "#ececf0", "#8a8a96", "#ff7a1a"


class Widget:
    """Small always-on-top card. Collapsed: one line. Expanded: queue + 7-day history."""

    def __init__(self, vq: VoiceQueue):
        import tkinter as tk
        if sys.platform == "win32":  # crisp text on high-DPI screens; sizes below scale with it
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        self.tk, self.vq = tk, vq
        self.root = tk.Tk()
        self.W = int(360 * max(1.0, self.root.winfo_fpixels("1i") / 96))
        self.root.title("Voice queue")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        self.root.configure(bg=LINE)
        self.expanded = False
        self.hidden_until = 0.0
        self.hidden_for_next = None  # id playing when hidden ("" if none); a different item brings it back
        self._hist_ids: list[str] = []
        self._queue_ids: list[str] = []
        self._build()
        self._place()
        self._tick()

    def _btn(self, parent, text, cmd, w=6):
        b = self.tk.Label(parent, text=text, bg=CARD, fg=FG, font=("Segoe UI", 9), width=w, cursor="hand2",
                          padx=2, pady=2)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=LINE))
        b.bind("<Leave>", lambda e: b.configure(bg=CARD))
        return b

    def _build(self):
        tk = self.tk
        outer = tk.Frame(self.root, bg=BG, padx=10, pady=8)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        top = tk.Frame(outer, bg=BG)
        top.pack(fill="x")
        self.dot = tk.Label(top, text="●", bg=BG, fg=DIM, font=("Segoe UI", 10))
        self.dot.pack(side="left")
        self.title = tk.Label(top, text="Voice queue", bg=BG, fg=FG, font=("Segoe UI Semibold", 10), anchor="w")
        self.title.pack(side="left", padx=(4, 0), fill="x", expand=True)
        self.count = tk.Label(top, text="", bg=BG, fg=DIM, font=("Segoe UI", 9))
        self.count.pack(side="left", padx=4)
        self.b_more = self._btn(top, "▾", self.toggle_expand, w=2)
        self.b_more.pack(side="left")
        self.b_close = self._btn(top, "\u2715", self.hide_until_next, w=2)
        self.b_close.pack(side="left", padx=(2, 0))
        self.root.bind("<Escape>", lambda e: self.hide_until_next())
        for w in (outer, top, self.title, self.dot, self.count):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<Button-3>", self._menu)

        self.now = tk.Label(outer, text="Nothing playing", bg=BG, fg=DIM, font=("Segoe UI", 9), anchor="w",
                            justify="left", wraplength=self.W - 30)
        self.now.pack(fill="x", pady=(4, 2))
        self.bar = tk.Canvas(outer, height=3, bg=LINE, highlightthickness=0)
        self.bar.pack(fill="x", pady=(0, 6))
        self.bar_fill = self.bar.create_rectangle(0, 0, 0, 3, fill=ACCENT, width=0)

        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x")
        self.b_pause = self._btn(row, "Pause", self.toggle_pause)
        self.b_skip = self._btn(row, "Skip", self.vq.skip)
        self.b_mute = self._btn(row, "Mute", self.toggle_mute)
        for b in (self.b_pause, self.b_skip, self.b_mute):
            b.pack(side="left", padx=(0, 4))

        self.more = tk.Frame(outer, bg=BG)
        tk.Label(self.more, text="UP NEXT", bg=BG, fg=DIM, font=("Segoe UI", 8)).pack(anchor="w", pady=(8, 2))
        self.qlist = tk.Listbox(self.more, height=4, bg=CARD, fg=FG, selectbackground=LINE, borderwidth=0,
                                highlightthickness=0, font=("Segoe UI", 9), activestyle="none")
        self.qlist.pack(fill="x")
        self.qlist.bind("<Delete>", lambda e: self._remove_selected())
        hrow = tk.Frame(self.more, bg=BG)
        hrow.pack(fill="x", pady=(8, 2))
        tk.Label(hrow, text="LAST 7 DAYS  (double-click to replay, right-click for session)", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="left")
        self.hlist = tk.Listbox(self.more, height=12, bg=CARD, fg=FG, selectbackground=LINE, borderwidth=0,
                                highlightthickness=0, font=("Segoe UI", 9), activestyle="none")
        self.hlist.pack(fill="both", expand=True)
        self.hlist.bind("<Double-Button-1>", lambda e: self._replay_selected())
        self.hlist.bind("<Button-3>", self._hist_menu)
        self.detail = tk.Label(self.more, text="", bg=BG, fg=DIM, font=("Segoe UI", 8), anchor="w", justify="left",
                               wraplength=self.W - 30)
        self.detail.pack(fill="x", pady=(4, 0))
        self.hlist.bind("<<ListboxSelect>>", lambda e: self._show_detail())

    def _place(self):
        self.root.update_idletasks()
        x = self.root.winfo_screenwidth() - self.W - 24
        self.root.geometry(f"{self.W}x{self.root.winfo_reqheight()}+{x}+24")

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _menu(self, e):
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label="Hide until next voiceover  (\u2715 / Esc)", command=self.hide_until_next)
        m.add_command(label="Hide for 1 hour, even while speaking", command=lambda: self._hide(3600))
        m.add_command(label="Clear queue", command=self.vq.clear)
        m.add_separator()
        m.add_command(label="Quit voice queue", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _hide(self, secs):
        self.hidden_for_next = None
        self.hidden_until = time.time() + secs
        self.root.withdraw()

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
        if self.expanded:
            self.more.pack(fill="both", expand=True)
            self.b_more.configure(text="▴")
            self._refresh_history()
        else:
            self.more.pack_forget()
            self.b_more.configure(text="▾")
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.update_idletasks()
        self.root.geometry(f"{self.W}x{self.root.winfo_reqheight()}+{x}+{y}")

    def toggle_pause(self):
        self.vq.resume() if self.vq.paused else self.vq.pause()

    def toggle_mute(self):
        self.vq.set_muted(not self.vq.muted)

    @staticmethod
    def _when(ts: float) -> str:
        t = time.localtime(ts)
        return time.strftime("%H:%M" if time.strftime("%Y%m%d", t) == time.strftime("%Y%m%d") else "%a %H:%M", t)

    def _refresh_history(self):
        rows = self.vq.history.recent()
        self._hist_ids = [r["id"] for r in rows]
        self.hlist.delete(0, "end")
        for r in rows:
            mark = "" if r.get("status") in ("spoken", "queued") else f"  [{r.get('status')}]"
            self.hlist.insert("end", f"{self._when(r['ts'])}  {r.get('project')}: {r.get('text', '')[:60]}{mark}")

    def _selected_hist(self) -> dict | None:
        sel = self.hlist.curselection()
        return self.vq.history.get(self._hist_ids[sel[0]]) if sel else None

    def _show_detail(self):
        r = self._selected_hist()
        if r:
            sid = r.get("session_id") or "?"
            self.detail.configure(text=f"{r.get('cwd') or '?'}\nsession {sid}  {r.get('entrypoint') or ''}\n"
                                       f"{r.get('text', '')}")

    def _replay_selected(self):
        r = self._selected_hist()
        if r:
            if self.vq.muted:
                self.vq.set_muted(False)
            self.vq.replay(r["id"])

    def _hist_menu(self, e):
        i = self.hlist.nearest(e.y)
        self.hlist.selection_clear(0, "end")
        self.hlist.selection_set(i)
        self._show_detail()
        r = self._selected_hist()
        if not r:
            return
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label="Replay", command=self._replay_selected)
        if r.get("session_id"):
            cmd = f'cd "{r.get("cwd")}"; claude --resume {r["session_id"]}'
            m.add_command(label="Copy resume command", command=lambda: self._copy(cmd))
        if r.get("cwd"):
            m.add_command(label="Copy folder path", command=lambda: self._copy(r["cwd"]))
        m.tk_popup(e.x_root, e.y_root)

    def _copy(self, s):
        self.root.clipboard_clear()
        self.root.clipboard_append(s)

    def _remove_selected(self):
        sel = self.qlist.curselection()
        if sel:
            self.vq.remove(self._queue_ids[sel[0]])

    def _tick(self):
        if self.vq._stop:
            self.root.destroy()
            return
        st = self.vq.state()
        cur, q = st["current"], st["queue"]
        if self.hidden_until and time.time() >= self.hidden_until:
            self.hidden_until = 0
            self.root.deiconify()
        if self.hidden_for_next is not None and cur and cur.get("id") != self.hidden_for_next:
            self.hidden_for_next = None
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        if cur:
            self.title.configure(text=f"{cur.get('project')}" + ("  (replay)" if cur.get("replay_of") else ""))
            self.now.configure(text=cur.get("text", "")[:240], fg=FG)
            frac = (cur.get("position", 0) / cur["duration"]) if cur.get("duration") else 0
            self.bar.coords(self.bar_fill, 0, 0, int(self.bar.winfo_width() * min(frac, 1)), 3)
        else:
            self.title.configure(text="Voice queue")
            self.now.configure(text="Muted: replies go to history only" if st["muted"] else "Nothing playing",
                               fg=DIM)
            self.bar.coords(self.bar_fill, 0, 0, 0, 3)
        self.dot.configure(fg=DIM if st["muted"] else ("#e8b04a" if st["paused"] else (ACCENT if cur else "#4caf7a")))
        self.count.configure(text=f"{len(q)} waiting" if q else "")
        self.b_pause.configure(text="Resume" if st["paused"] else "Pause")
        self.b_mute.configure(text="Unmute" if st["muted"] else "Mute")
        ids = [i["id"] for i in q]
        if ids != self._queue_ids:
            self._queue_ids = ids
            self.qlist.delete(0, "end")
            for i in q:
                self.qlist.insert("end", f"{i.get('project')}: {i.get('text', '')[:70]}")
            if self.expanded:
                self._refresh_history()
        if self.expanded and (cur or {}).get("id") != getattr(self, "_last_cur", None):
            self._last_cur = (cur or {}).get("id")
            self._refresh_history()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        h = self.root.winfo_reqheight()
        if h != self.root.winfo_height():
            self.root.geometry(f"{self.W}x{h}+{x}+{y}")
        self.root.after(250, self._tick)

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
