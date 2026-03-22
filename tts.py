"""
Kokoro-82M TTS
Usage:
    python tts.py "Your text here"
    python tts.py "Your text here" --voice am_adam --speed 1.1 --out speech.wav
    python tts.py --file input.txt --play
    python tts.py --list-voices
"""

import argparse
import os
import sys
import tempfile
import threading
import time
import wave

import numpy as np
import onnxruntime as rt

MODEL_PATH  = os.path.join(os.path.dirname(__file__), "models", "kokoro-v1.0.onnx")
VOICES_PATH = os.path.join(os.path.dirname(__file__), "models", "voices-v1.0.bin")
SAMPLE_RATE = 24000
LOGO_PATH   = os.path.join(
    os.path.dirname(__file__), "brand", "logo",
    "iMadeFire-simple-white-on-black-v2.svg",
)

VOICES = {
    "af_heart":    "American Female — warm, natural (default)",
    "af_bella":    "American Female — clear, expressive",
    "af_nicole":   "American Female — smooth, professional",
    "af_aoede":    "American Female — bright, energetic",
    "af_kore":     "American Female — calm, authoritative",
    "af_sarah":    "American Female — friendly, conversational",
    "af_sky":      "American Female — gentle, airy",
    "am_adam":     "American Male — strong, clear",
    "am_michael":  "American Male — warm, measured",
    "am_echo":     "American Male — resonant",
    "am_eric":     "American Male — steady, confident",
    "am_fenrir":   "American Male — deep, powerful",
    "am_liam":     "American Male — youthful, upbeat",
    "am_onyx":     "American Male — rich, baritone",
    "am_puck":     "American Male — nimble, lively",
    "bf_emma":     "British Female — refined, articulate",
    "bf_isabella": "British Female — warm, elegant",
    "bm_george":   "British Male — formal, distinguished",
    "bm_lewis":    "British Male — casual, approachable",
}


# ════════════════════════════════════════════════════════════
#  AudioPlayer  —  sounddevice-backed, pause / seek / replay
# ════════════════════════════════════════════════════════════
class AudioPlayer:
    """
    The stream runs continuously for the lifetime of the player.
    When audio ends naturally the callback outputs silence and sets
    self.ended = True.  The controller polls this flag and resets it
    on replay — no stream close/recreate needed, so play always works.
    """

    def __init__(self, wav_path):
        import soundfile as sf
        self.data, self.samplerate = sf.read(wav_path, dtype="float32")
        if self.data.ndim == 1:
            self.data = self.data.reshape(-1, 1)
        self._pos     = 0
        self._lock    = threading.Lock()
        self._playing = False
        self.ended    = False
        import sounddevice as sd
        self._stream = sd.OutputStream(
            samplerate=self.samplerate,
            channels=self.data.shape[1],
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, outdata, frames, _time, _status):
        with self._lock:
            if not self._playing:
                outdata[:] = 0
                return
            remaining = len(self.data) - self._pos
            if remaining <= 0:
                outdata[:] = 0
                self._playing = False
                self.ended    = True
                return
            n = min(frames, remaining)
            outdata[:n] = self.data[self._pos : self._pos + n]
            if n < frames:
                outdata[n:]   = 0
                self._playing = False
                self.ended    = True
            self._pos += n

    def play(self):
        with self._lock:
            if self.ended:
                self._pos  = 0
                self.ended = False
            self._playing = True

    def pause(self):
        with self._lock:
            self._playing = False

    def toggle(self):
        with self._lock:
            if self.ended:
                self._pos     = 0
                self.ended    = False
                self._playing = True
                return True
            self._playing = not self._playing
            return self._playing

    def seek(self, delta_seconds):
        with self._lock:
            new = self._pos + int(delta_seconds * self.samplerate)
            self._pos  = max(0, min(len(self.data) - 1, new))
            self.ended = False

    def seek_to(self, fraction):
        with self._lock:
            self._pos  = int(fraction * max(0, len(self.data) - 1))
            self.ended = False

    @property
    def position(self):
        with self._lock:
            return self._pos / self.samplerate

    @property
    def duration(self):
        return len(self.data) / self.samplerate

    @property
    def is_playing(self):
        with self._lock:
            return self._playing

    def close(self):
        with self._lock:
            self._playing = False
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════
#  KokoroController  —  dark glass card player window
# ════════════════════════════════════════════════════════════
class KokoroController:
    W         = 340
    H_TITLE   = 48
    H_CONTENT = 130
    RADIUS    = 14

    # Transparent key — unique near-black, never used in the card
    TRANS_KEY = "#010101"

    # Dark glass palette (inspired by iMadeFire dashboard style)
    C_CARD    = "#12122a"   # card surface
    C_BORDER  = "#26265a"   # card border
    C_DIVIDER = "#1c1c3e"   # section divider line
    C_ACCENT1 = "#f07020"   # brand orange
    C_ACCENT2 = "#f5c842"   # brand amber
    C_TEXT    = "#e8eaf6"   # near white
    C_DIM     = "#6272a4"   # muted text
    C_BTN     = "#1c1c3e"   # button / track bg
    C_BTN_BD  = "#2c2c56"   # button border
    SPIN      = ["◐", "◓", "◑", "◒"]

    def __init__(self):
        import tkinter as tk
        self.tk            = tk
        self.root          = tk.Tk()
        self.player: AudioPlayer | None = None
        self._tmp_wav: str | None       = None
        self._spin_idx     = 0
        self._drag_ref     = None
        self._end_handled  = False
        self._logo_img     = None
        self._grad_img     = None
        self._glow_img     = None
        self._btn_boxes: dict[str, tuple] = {}
        self._state        = "loading"

        self._setup_window()
        self._load_logo()
        self._build_ui()
        self._set_state("loading")

    # ── window ────────────────────────────────────────────────
    def _setup_window(self):
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=self.TRANS_KEY)
        try:
            r.wm_attributes("-transparentcolor", self.TRANS_KEY)
        except Exception:
            r.configure(bg=self.C_CARD)
        r.resizable(False, False)
        h  = self.H_TITLE + self.H_CONTENT
        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        r.geometry(f"{self.W}x{h}+{sw - self.W - 20}+{sh - h - 70}")

    # ── SVG logo (svglib optional; black bg made transparent) ─
    def _load_logo(self):
        try:
            from svglib.svglib import svg2rlg
            from reportlab.graphics import renderPM
            from PIL import Image, ImageTk
            if not os.path.exists(LOGO_PATH):
                return
            drawing = svg2rlg(LOGO_PATH)
            if not drawing:
                return
            target_h = 20
            scale    = target_h / drawing.height
            target_w = min(int(drawing.width * scale), 180)
            pil_img  = renderPM.drawToPIL(drawing, dpi=max(72, int(96 * scale)))
            pil_img  = pil_img.resize((target_w, target_h), Image.LANCZOS)
            pil_img  = pil_img.convert("RGBA")
            pixels   = pil_img.load()
            for y in range(pil_img.height):
                for x in range(pil_img.width):
                    rv, gv, bv, av = pixels[x, y]
                    if rv < 60 and gv < 60 and bv < 60:
                        pixels[x, y] = (rv, gv, bv, 0)
            self._logo_img = ImageTk.PhotoImage(pil_img)
        except Exception:
            pass

    # ── canvas helper ─────────────────────────────────────────
    def _rrect(self, cv, x1, y1, x2, y2, r=10, **kw):
        r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
        pts = [
            x1+r, y1,   x2-r, y1,
            x2,   y1,   x2,   y1+r,
            x2,   y2-r, x2,   y2,
            x2-r, y2,   x1+r, y2,
            x1,   y2,   x1,   y2-r,
            x1,   y1+r, x1,   y1,
        ]
        return cv.create_polygon(pts, smooth=True, **kw)

    # ── main canvas ───────────────────────────────────────────
    def _build_ui(self):
        tk  = self.tk
        h   = self.H_TITLE + self.H_CONTENT
        R   = self.RADIUS
        cv  = tk.Canvas(self.root, width=self.W, height=h,
                        bg=self.TRANS_KEY, highlightthickness=0)
        cv.pack(fill="both", expand=True)
        self._cv = cv

        # Card border + fill (rounded corners via smooth polygon)
        self._rrect(cv, 0, 0, self.W, h, r=R,
                    fill=self.C_BORDER, outline="")
        self._rrect(cv, 1, 1, self.W - 1, h - 1, r=R - 1,
                    fill=self.C_CARD, outline="")

        # Orange→amber glow at card bottom
        self._build_glow(cv, h)

        # Title bar
        self._build_title(cv)

        # Divider under title
        cv.create_line(14, self.H_TITLE, self.W - 14, self.H_TITLE,
                       fill=self.C_DIVIDER, width=1)

        # Content items (shown/hidden via state tags)
        self._build_loading_items(cv)
        self._build_playback_items(cv)

        cv.bind("<ButtonPress-1>",   self._on_click)
        cv.bind("<B1-Motion>",       self._on_drag)
        cv.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_ref", None))

    def _build_glow(self, cv, total_h):
        try:
            from PIL import Image, ImageTk
            gw, gh = self.W - 2, 40
            img    = Image.new("RGBA", (gw, gh))
            pix    = img.load()
            r1, g1, b1 = 0xf0, 0x70, 0x20
            r2, g2, b2 = 0xf5, 0xc8, 0x42
            for x in range(gw):
                t  = x / max(gw - 1, 1)
                rc = int(r1 + (r2 - r1) * t)
                gc = int(g1 + (g2 - g1) * t)
                bc = int(b1 + (b2 - b1) * t)
                for y in range(gh):
                    alpha = int(48 * (y / gh) ** 1.6)
                    pix[x, y] = (rc, gc, bc, alpha)
            self._glow_img = ImageTk.PhotoImage(img)
            cv.create_image(1, total_h - gh, anchor="nw", image=self._glow_img)
        except Exception:
            pass

    def _build_title(self, cv):
        my = self.H_TITLE // 2
        if self._logo_img:
            lw = self._logo_img.width()
            cv.create_image(16, my, anchor="w", image=self._logo_img)
            cv.create_text(16 + lw + 8, my, text="TTS", anchor="w",
                           fill=self.C_DIM, font=("Segoe UI", 9))
        else:
            cv.create_text(16, my, text="iMadeFire", anchor="w",
                           fill=self.C_ACCENT1, font=("Segoe UI", 12, "bold"))
            cv.create_text(100, my, text="TTS", anchor="w",
                           fill=self.C_DIM, font=("Segoe UI", 9))

        # Close button — small pill
        bx, br = self.W - 20, 9
        cv.create_oval(bx - br, my - br, bx + br, my + br,
                       fill=self.C_BTN, outline=self.C_BTN_BD,
                       tags=("cls_bg",))
        cv.create_text(bx, my, text="✕", fill=self.C_DIM,
                       font=("Segoe UI", 8), tags=("cls_x",))

        def _cls_enter(e):
            cv.itemconfig("cls_bg", fill=self.C_DIVIDER)
            cv.itemconfig("cls_x",  fill=self.C_TEXT)

        def _cls_leave(e):
            cv.itemconfig("cls_bg", fill=self.C_BTN)
            cv.itemconfig("cls_x",  fill=self.C_DIM)

        for tag in ("cls_bg", "cls_x"):
            cv.tag_bind(tag, "<Button-1>", self._close)
            cv.tag_bind(tag, "<Enter>",    _cls_enter)
            cv.tag_bind(tag, "<Leave>",    _cls_leave)

    def _build_loading_items(self, cv):
        cy = self.H_TITLE + self.H_CONTENT // 2
        sx = self.W // 2 - 58
        sr = 20
        cv.create_oval(sx - sr, cy - sr, sx + sr, cy + sr,
                       fill=self.C_BTN, outline=self.C_BTN_BD,
                       tags="loading")
        self._spin_item = cv.create_text(
            sx, cy, text="◐", fill=self.C_ACCENT1,
            font=("Segoe UI", 14, "bold"), tags="loading",
        )
        self._gen_item = cv.create_text(
            sx + sr + 14, cy, text="Generating...",
            fill=self.C_TEXT, font=("Segoe UI", 11),
            anchor="w", tags="loading",
        )

    def _build_playback_items(self, cv):
        ty        = self.H_TITLE
        px, py    = 18, ty + 18
        pw        = self.W - 36
        ph        = 6
        self._px, self._py, self._pw, self._ph = px, py, pw, ph

        # Progress track
        self._rrect(cv, px, py, px + pw, py + ph, r=3,
                    fill=self.C_BTN, outline=self.C_BTN_BD, tags="playback")

        # Gradient fill (orange → amber)
        try:
            from PIL import Image, ImageTk
            r1, g1, b1 = 0xf0, 0x70, 0x20
            r2, g2, b2 = 0xf5, 0xc8, 0x42
            img = Image.new("RGB", (max(1, pw - 2), max(1, ph - 2)))
            pix = img.load()
            for x in range(pw - 2):
                t   = x / max(pw - 3, 1)
                col = (int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t))
                for yy in range(ph - 2):
                    pix[x, yy] = col
            self._grad_img = ImageTk.PhotoImage(img)
            cv.create_image(px + 1, py + 1, anchor="nw",
                            image=self._grad_img, tags="playback")
        except Exception:
            self._grad_img = None
            cv.create_rectangle(px+1, py+1, px+pw-1, py+ph-1,
                                fill=self.C_ACCENT1, outline="", tags="playback")

        # Cover rect slides right as audio plays
        self._prog_cover = cv.create_rectangle(
            px + 1, py + 1, px + pw - 1, py + ph - 1,
            fill=self.C_BTN, outline="", tags="playback",
        )

        # Time labels
        ly = py + ph + 8
        self._pos_item = cv.create_text(
            px, ly, text="0:00", fill=self.C_DIM,
            font=("Segoe UI", 8), anchor="nw", tags="playback",
        )
        self._dur_item = cv.create_text(
            px + pw, ly, text="0:00", fill=self.C_DIM,
            font=("Segoe UI", 8), anchor="ne", tags="playback",
        )

        # Control buttons
        btn_y = ty + self.H_CONTENT - 30
        mid   = self.W // 2
        self._draw_btn(cv, mid - 84, btn_y, 64, 28, "« 10", "rew")
        self._draw_btn(cv, mid,      btn_y, 50, 28, "▶",    "play", accent=True)
        self._draw_btn(cv, mid + 84, btn_y, 64, 28, "10 »", "fwd")

    def _draw_btn(self, cv, cx, cy, w, h, label, name, accent=False):
        r  = h // 2
        x1, y1, x2, y2 = cx - w//2, cy - h//2, cx + w//2, cy + h//2
        fill = self.C_ACCENT1 if accent else self.C_BTN
        bd   = self.C_ACCENT1 if accent else self.C_BTN_BD
        tc   = "#ffffff"      if accent else self.C_DIM
        fs   = 14             if name == "play" else 10
        self._rrect(cv, x1, y1, x2, y2, r=r,
                    fill=fill, outline=bd,
                    tags=(f"btn_{name}", "playback"))
        cv.create_text(cx, cy, text=label, fill=tc,
                       font=("Segoe UI", fs),
                       tags=(f"btn_{name}_txt", "playback"))
        self._btn_boxes[name] = (x1, y1, x2, y2)

        for tag in (f"btn_{name}", f"btn_{name}_txt"):
            cv.tag_bind(tag, "<Enter>", lambda e, n=name, a=accent: self._btn_enter(n, a))
            cv.tag_bind(tag, "<Leave>", lambda e, n=name, a=accent: self._btn_leave(n, a))

    def _btn_enter(self, name, accent):
        cv = self._cv
        if accent:
            cv.itemconfig(f"btn_{name}", fill=self.C_ACCENT2, outline=self.C_ACCENT2)
        else:
            cv.itemconfig(f"btn_{name}", fill=self.C_DIVIDER)
            cv.itemconfig(f"btn_{name}_txt", fill=self.C_TEXT)

    def _btn_leave(self, name, accent):
        cv = self._cv
        if accent:
            cv.itemconfig(f"btn_{name}", fill=self.C_ACCENT1, outline=self.C_ACCENT1)
        else:
            cv.itemconfig(f"btn_{name}", fill=self.C_BTN)
            cv.itemconfig(f"btn_{name}_txt", fill=self.C_DIM)

    # ── state management ──────────────────────────────────────
    def _set_state(self, state):
        self._state = state
        cv = self._cv
        if state == "loading":
            cv.itemconfig("playback", state="hidden")
            cv.itemconfig("loading",  state="normal")
            self._animate_spinner()
        else:
            cv.itemconfig("loading",  state="hidden")
            cv.itemconfig("playback", state="normal")
            self.root.after(50, self._update_progress)

    # ── spinner ───────────────────────────────────────────────
    def _animate_spinner(self):
        if self._state != "loading":
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        self._spin_idx = (self._spin_idx + 1) % len(self.SPIN)
        self._cv.itemconfig(self._spin_item, text=self.SPIN[self._spin_idx])
        self.root.after(130, self._animate_spinner)

    # ── progress polling ──────────────────────────────────────
    def _update_progress(self):
        if not self.player:
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return

        if self.player.ended:
            if not self._end_handled:
                self._end_handled = True
                self._handle_end()
        else:
            self._end_handled = False

        pos = self.player.position
        dur = self.player.duration

        def fmt(s):
            s = int(s)
            return f"{s // 60}:{s % 60:02d}"

        cv = self._cv
        cv.itemconfig(self._pos_item, text=fmt(pos))
        cv.itemconfig(self._dur_item, text=fmt(dur))

        frac   = min(pos / dur, 1.0) if dur > 0 else 0.0
        filled = int(self._pw * frac)
        cv.coords(self._prog_cover,
                  self._px + 1 + filled, self._py + 1,
                  self._px + self._pw - 1, self._py + self._ph - 1)

        icon = "⏸" if self.player.is_playing else "▶"
        cv.itemconfig("btn_play_txt", text=icon)

        self.root.after(100, self._update_progress)

    # ── controls ──────────────────────────────────────────────
    def _toggle_play(self):
        if self.player:
            playing = self.player.toggle()
            self._cv.itemconfig("btn_play_txt", text="⏸" if playing else "▶")

    def _rewind(self):
        if self.player:
            self.player.seek(-10)

    def _forward(self):
        if self.player:
            self.player.seek(10)

    def _handle_end(self):
        cv = self._cv
        cv.itemconfig("btn_play_txt", text="▶")
        cv.itemconfig(self._pos_item, text="0:00")
        cv.coords(self._prog_cover,
                  self._px + 1, self._py + 1,
                  self._px + self._pw - 1, self._py + self._ph - 1)

    def _close(self, _event=None):
        if self.player:
            self.player.close()
        if self._tmp_wav:
            try:
                os.remove(self._tmp_wav)
            except OSError:
                pass
        self.root.destroy()

    # ── drag / click ──────────────────────────────────────────
    def _on_click(self, event):
        x, y = event.x, event.y
        # Title bar → drag (skip close button)
        if y < self.H_TITLE:
            item = self._cv.find_closest(x, y)
            if item and any(t.startswith("cls_") for t in self._cv.gettags(item[0])):
                return
            self._drag_ref = (
                event.x_root - self.root.winfo_x(),
                event.y_root - self.root.winfo_y(),
            )
            return
        # Content → seek or button
        self._drag_ref = None
        if (self._px <= x <= self._px + self._pw and
                self._py - 6 <= y <= self._py + self._ph + 6):
            if self.player:
                self.player.seek_to(max(0.0, min(1.0, (x - self._px) / self._pw)))
            return
        actions = {"rew": self._rewind, "play": self._toggle_play, "fwd": self._forward}
        for name, (x1, y1, x2, y2) in self._btn_boxes.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                actions[name]()
                return

    def _on_drag(self, event):
        if self._drag_ref:
            self.root.geometry(
                f"+{event.x_root - self._drag_ref[0]}+{event.y_root - self._drag_ref[1]}"
            )

    # ── entry point ───────────────────────────────────────────
    def run(self, text, voice, speed):
        threading.Thread(
            target=self._generate_worker,
            args=(text, voice, speed),
            daemon=True,
        ).start()
        self.root.mainloop()

    def _generate_worker(self, text, voice, speed):
        from kokoro_onnx import Kokoro

        sess   = rt.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
        kokoro = Kokoro.from_session(sess, VOICES_PATH)
        samples, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")

        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="kokoro_")
        os.close(fd)
        self._tmp_wav = tmp

        s16 = (samples * 32767).astype(np.int16)
        with wave.open(tmp, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(s16.tobytes())

        self.root.after(0, self._on_generated, tmp)

    def _on_generated(self, wav_path):
        self.player = AudioPlayer(wav_path)
        self.player.play()
        self._set_state("playback")


# ════════════════════════════════════════════════════════════
#  CLI helpers
# ════════════════════════════════════════════════════════════
def list_voices():
    print("\nAvailable voices:\n")
    sections = [
        ("American Female (af_*)", [k for k in VOICES if k.startswith("af_")]),
        ("American Male   (am_*)", [k for k in VOICES if k.startswith("am_")]),
        ("British Female  (bf_*)", [k for k in VOICES if k.startswith("bf_")]),
        ("British Male    (bm_*)", [k for k in VOICES if k.startswith("bm_")]),
    ]
    for section, keys in sections:
        print(f"  {section}")
        for k in keys:
            print(f"    {k:<14}  {VOICES[k]}")
        print()


def save_wav(samples: np.ndarray, path: str, sample_rate: int = SAMPLE_RATE):
    s16 = (samples * 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(s16.tobytes())


def autoplay(text: str, voice: str, speed: float):
    """Generate and play silently — no GUI, no console. Used by the Claude Code hook."""
    import sounddevice as sd
    from kokoro_onnx import Kokoro

    sess   = rt.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    kokoro = Kokoro.from_session(sess, VOICES_PATH)
    samples, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    sd.play(samples.reshape(-1), sr)
    sd.wait()


def generate_to_file(text: str, voice: str, speed: float, out: str):
    from kokoro_onnx import Kokoro

    print(f"Loading model...", end=" ", flush=True)
    sess   = rt.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    kokoro = Kokoro.from_session(sess, VOICES_PATH)
    print("ready")

    print(f"Voice: {voice}  |  Speed: {speed}x")
    print(f"Text:  {text[:80]}{'...' if len(text) > 80 else ''}")
    print("Generating...", end=" ", flush=True)

    t0 = time.perf_counter()
    samples, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    elapsed = time.perf_counter() - t0

    save_wav(samples, out, sr)

    duration = len(samples) / sr
    print(f"done  ({elapsed:.2f}s -> {duration:.1f}s audio, {duration/elapsed:.1f}x realtime)")
    print(f"Saved: {os.path.abspath(out)}")


# ════════════════════════════════════════════════════════════
#  main
# ════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Kokoro-82M TTS — fast, offline text-to-speech",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python tts.py "Hello world"
  python tts.py "Hello world" --voice am_adam --speed 1.1 --out hello.wav
  python tts.py --file input.txt --play
  python tts.py --list-voices
        """,
    )
    parser.add_argument("text",         nargs="?",               help="Text to synthesize")
    parser.add_argument("--file",       metavar="FILE",          help="Read text from a UTF-8 file")
    parser.add_argument("--voice",      default="af_heart",      help="Voice (default: af_heart)")
    parser.add_argument("--speed",      type=float, default=1.0, help="Speed multiplier (default: 1.0)")
    parser.add_argument("--out",        default="output.wav",    help="Output WAV (default: output.wav)")
    parser.add_argument("--play",       action="store_true",     help="Show player controller and play")
    parser.add_argument("--autoplay",   action="store_true",     help="Play silently with no GUI (used by hook)")
    parser.add_argument("--list-voices",action="store_true",     help="List all voices and exit")
    args = parser.parse_args()

    if args.list_voices:
        list_voices()
        return

    text = None
    if args.file:
        if not os.path.exists(args.file):
            print(f"ERROR: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        with open(args.file, encoding="utf-8") as f:
            text = f.read().strip()
    elif args.text:
        text = args.text

    if not text:
        parser.print_help()
        sys.exit(1)

    for path, label in [(MODEL_PATH, "Model"), (VOICES_PATH, "Voices")]:
        if not os.path.exists(path):
            print(f"ERROR: {label} not found: {path}", file=sys.stderr)
            sys.exit(1)

    if args.play:
        ctrl = KokoroController()
        ctrl.run(text, args.voice, args.speed)
    elif args.autoplay:
        autoplay(text, args.voice, args.speed)
    else:
        generate_to_file(text, args.voice, args.speed, args.out)


if __name__ == "__main__":
    main()
