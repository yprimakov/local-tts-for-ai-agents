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
#  KokoroController  —  neumorphic dark player window
# ════════════════════════════════════════════════════════════
class KokoroController:
    W          = 320
    H_TITLE    = 38
    H_LOADING  = 82
    H_PLAYBACK = 118

    # ── Neumorphic dark palette (iMadeFire brand) ─────────────
    C_BASE  = "#1a1a2a"   # main surface
    C_DARK  = "#111120"   # dark shadow
    C_LIGHT = "#27274a"   # light shadow / highlight
    C_FIRE1 = "#ec7200"   # brand orange
    C_FIRE2 = "#e5c84a"   # brand amber
    C_TEXT  = "#e8eaf6"   # near white
    C_DIM   = "#5e6a9a"   # muted label
    SPIN    = ["◐", "◓", "◑", "◒"]

    def __init__(self):
        import tkinter as tk
        self.tk   = tk
        self.root = tk.Tk()
        self.player:   AudioPlayer | None = None
        self._tmp_wav: str | None         = None
        self._spin_idx    = 0
        self._drag_ref    = None
        self._end_handled = False
        self._logo_img    = None   # PhotoImage (SVG rendered via svglib)
        self._grad_img    = None   # progress gradient PhotoImage
        self._btn_boxes:  dict[str, tuple] = {}

        self._setup_window()
        self._load_logo()
        self._build_title_bar()
        self._build_loading()
        self._build_playback()
        self._show_loading()

    # ── window ────────────────────────────────────────────────
    def _setup_window(self):
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=self.C_BASE)
        r.resizable(False, False)

        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        h  = self.H_TITLE + self.H_LOADING
        r.geometry(f"{self.W}x{h}+{sw - self.W - 20}+{sh - h - 70}")

        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(r.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4
            )
        except Exception:
            pass

    # ── optional SVG logo (requires svglib + reportlab) ───────
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
            target_h = 22
            target_w = int(target_h * drawing.width / drawing.height)
            img = renderPM.drawToPIL(drawing, dpi=96)
            img = img.resize((target_w, target_h), Image.LANCZOS)
            self._logo_img = ImageTk.PhotoImage(img)
        except Exception:
            pass

    # ── drawing helpers ───────────────────────────────────────
    def _rrect(self, canvas, x1, y1, x2, y2, r=8, **kw):
        """Smooth rounded rectangle via spline polygon."""
        pts = [
            x1 + r, y1,     x2 - r, y1,
            x2,     y1,     x2,     y1 + r,
            x2,     y2 - r, x2,     y2,
            x2 - r, y2,     x1 + r, y2,
            x1,     y2,     x1,     y2 - r,
            x1,     y1 + r, x1,     y1,
        ]
        return canvas.create_polygon(pts, smooth=True, **kw)

    def _neu_raised(self, canvas, x1, y1, x2, y2, r=9, s=3):
        """Draw a raised neumorphic surface (dark shadow BR, light shadow TL)."""
        self._rrect(canvas, x1+s, y1+s, x2+s, y2+s, r, fill=self.C_DARK,  outline="")
        self._rrect(canvas, x1-s, y1-s, x2-s, y2-s, r, fill=self.C_LIGHT, outline="")
        self._rrect(canvas, x1,   y1,   x2,   y2,   r, fill=self.C_BASE,  outline="")

    def _neu_inset(self, canvas, x1, y1, x2, y2, r=5):
        """Draw an inset neumorphic channel (dark shadow TL, light shadow BR)."""
        self._rrect(canvas, x1,   y1,   x2,   y2,   r, fill=self.C_DARK,  outline="")
        canvas.create_line(x1+r, y1+1, x2-r, y1+1, fill=self.C_DARK,  width=1)
        canvas.create_line(x1+1, y1+r, x1+1, y2-r, fill=self.C_DARK,  width=1)
        canvas.create_line(x1+r, y2-1, x2-r, y2-1, fill=self.C_LIGHT, width=1)
        canvas.create_line(x2-1, y1+r, x2-1, y2-r, fill=self.C_LIGHT, width=1)

    def _neu_button(self, canvas, cx, cy, w, h, label, name, fsize=10):
        """Draw a full neumorphic button and register its hit box."""
        x1, y1, x2, y2 = cx - w//2, cy - h//2, cx + w//2, cy + h//2
        self._neu_raised(canvas, x1, y1, x2, y2)
        is_play = (name == "play")
        color   = self.C_FIRE1 if is_play else self.C_TEXT
        canvas.create_text(cx, cy, text=label, fill=color,
                           font=("Segoe UI", fsize), tags=f"btn_{name}_txt")
        self._btn_boxes[name] = (x1, y1, x2, y2)
        for part in (f"btn_{name}_txt",):
            canvas.tag_bind(part, "<Enter>", lambda e, n=name: self._btn_hover(n, True))
            canvas.tag_bind(part, "<Leave>", lambda e, n=name: self._btn_hover(n, False))

    def _btn_hover(self, name, entering):
        # Re-draw surface polygon on hover by tagging isn't trivial — instead
        # just change the text color to indicate hover.
        c = self._play_cv
        if name == "play":
            c.itemconfig(f"btn_{name}_txt", fill=self.C_FIRE2 if entering else self.C_FIRE1)
        else:
            c.itemconfig(f"btn_{name}_txt", fill=self.C_TEXT if entering else self.C_DIM)

    # ── title bar ─────────────────────────────────────────────
    def _build_title_bar(self):
        tk = self.tk
        c  = tk.Canvas(self.root, width=self.W, height=self.H_TITLE,
                       bg=self.C_DARK, highlightthickness=0)
        c.pack(fill="x")
        self._title_cv = c

        # Subtle bottom separator
        c.create_line(0, self.H_TITLE - 1, self.W, self.H_TITLE - 1,
                      fill=self.C_LIGHT)

        my = self.H_TITLE // 2

        if self._logo_img:
            lw = self._logo_img.width()
            c.create_image(14, my, anchor="w", image=self._logo_img, tags="drag")
            c.create_text(14 + lw + 7, my, text="TTS", anchor="w",
                          fill=self.C_DIM, font=("Segoe UI", 9), tags="drag")
        else:
            # Text fallback — brand name in fire orange + "TTS" dimmed
            c.create_text(14, my, text="iMadeFire", anchor="w",
                          fill=self.C_FIRE1, font=("Segoe UI", 11, "bold"), tags="drag")
            c.create_text(100, my, text="TTS", anchor="w",
                          fill=self.C_DIM, font=("Segoe UI", 9), tags="drag")

        # Neumorphic close button
        bx, by, br = self.W - 18, my, 9
        c.create_oval(bx-br+2, by-br+2, bx+br+2, by+br+2,
                      fill=self.C_DARK, outline="", tags="cls_sd")
        c.create_oval(bx-br-1, by-br-1, bx+br-1, by+br-1,
                      fill=self.C_LIGHT, outline="", tags="cls_sl")
        c.create_oval(bx-br,   by-br,   bx+br,   by+br,
                      fill=self.C_BASE,  outline="", tags="cls_bg")
        c.create_text(bx, by, text="✕", fill=self.C_DIM,
                      font=("Segoe UI", 8), tags="cls_x")

        for tag in ("cls_bg", "cls_x", "cls_sd", "cls_sl"):
            c.tag_bind(tag, "<Button-1>", self._close)
        c.tag_bind("cls_bg", "<Enter>", lambda e: c.itemconfig("cls_x", fill=self.C_TEXT))
        c.tag_bind("cls_x",  "<Enter>", lambda e: c.itemconfig("cls_x", fill=self.C_TEXT))
        c.tag_bind("cls_bg", "<Leave>", lambda e: c.itemconfig("cls_x", fill=self.C_DIM))
        c.tag_bind("cls_x",  "<Leave>", lambda e: c.itemconfig("cls_x", fill=self.C_DIM))

        c.bind("<ButtonPress-1>", self._drag_start)
        c.bind("<B1-Motion>",     self._drag_move)

    # ── loading frame ─────────────────────────────────────────
    def _build_loading(self):
        tk = self.tk
        c  = tk.Canvas(self.root, width=self.W, height=self.H_LOADING,
                       bg=self.C_BASE, highlightthickness=0)
        self._load_cv = c

        mid_x, mid_y = self.W // 2, self.H_LOADING // 2
        sx = mid_x - 44

        # Inset circle background for spinner
        sr = 18
        self._neu_inset(c, sx - sr, mid_y - sr, sx + sr, mid_y + sr, r=sr)
        # Inner surface
        ir = sr - 3
        c.create_oval(sx - ir, mid_y - ir, sx + ir, mid_y + ir,
                      fill=self.C_DARK, outline="")

        self._spin_item = c.create_text(sx, mid_y, text="◐",
                                        fill=self.C_FIRE1,
                                        font=("Segoe UI", 13, "bold"))
        self._gen_item  = c.create_text(mid_x - 8, mid_y, text="Generating...",
                                        fill=self.C_TEXT,
                                        font=("Segoe UI", 11), anchor="w")

    # ── playback frame ────────────────────────────────────────
    def _build_playback(self):
        tk = self.tk
        c  = tk.Canvas(self.root, width=self.W, height=self.H_PLAYBACK,
                       bg=self.C_BASE, highlightthickness=0)
        self._play_cv = c

        # ── progress track (inset neumorphic channel) ─────────
        px, py, pw, ph = 18, 20, self.W - 36, 8
        self._px, self._py, self._pw, self._ph = px, py, pw, ph

        self._neu_inset(c, px, py, px + pw, py + ph, r=4)

        # Gradient fill image (orange → amber, Pillow)
        try:
            from PIL import Image, ImageTk
            r1, g1, b1 = 0xec, 0x72, 0x00
            r2, g2, b2 = 0xe5, 0xc8, 0x4a
            img = Image.new("RGB", (pw - 2, ph - 2))
            pix = img.load()
            for x in range(pw - 2):
                t = x / max(pw - 3, 1)
                col = (int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t))
                for y in range(ph - 2):
                    pix[x, y] = col
            self._grad_img = ImageTk.PhotoImage(img)
            c.create_image(px + 1, py + 1, anchor="nw", image=self._grad_img)
        except Exception:
            self._grad_img = None
            c.create_rectangle(px+1, py+1, px+pw-1, py+ph-1,
                               fill=self.C_FIRE1, outline="")

        # Cover rect — slides right to reveal gradient as audio plays
        self._prog_cover = c.create_rectangle(
            px + 1, py + 1, px + pw - 1, py + ph - 1,
            fill=self.C_DARK, outline="",
        )

        # ── time labels ───────────────────────────────────────
        ty = py + ph + 6
        self._pos_item = c.create_text(px, ty, text="0:00",
                                       fill=self.C_DIM, font=("Segoe UI", 8),
                                       anchor="nw")
        self._dur_item = c.create_text(px + pw, ty, text="0:00",
                                       fill=self.C_DIM, font=("Segoe UI", 8),
                                       anchor="ne")

        # ── control buttons ───────────────────────────────────
        mid   = self.W // 2
        btn_y = self.H_PLAYBACK - 30
        self._neu_button(c, mid - 88, btn_y, 58, 34, "« 10", "rew",  fsize=10)
        self._neu_button(c, mid,      btn_y, 46, 34, "▶",    "play", fsize=16)
        self._neu_button(c, mid + 88, btn_y, 58, 34, "10 »", "fwd",  fsize=10)

        # Default label brightness
        c.itemconfig("btn_rew_txt",  fill=self.C_DIM)
        c.itemconfig("btn_fwd_txt",  fill=self.C_DIM)

        c.bind("<Button-1>",  self._on_play_click)
        c.bind("<B1-Motion>", self._on_prog_drag)

    # ── event dispatching ─────────────────────────────────────
    def _on_play_click(self, event):
        x, y = event.x, event.y
        # Progress bar seek
        if (self._px <= x <= self._px + self._pw and
                self._py - 6 <= y <= self._py + self._ph + 6):
            if self.player:
                self.player.seek_to(max(0.0, min(1.0, (x - self._px) / self._pw)))
            return
        # Button hit test
        actions = {"rew": self._rewind, "play": self._toggle_play, "fwd": self._forward}
        for name, (x1, y1, x2, y2) in self._btn_boxes.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                actions[name]()
                return

    def _on_prog_drag(self, event):
        if self.player and self._px <= event.x <= self._px + self._pw:
            self.player.seek_to(max(0.0, min(1.0, (event.x - self._px) / self._pw)))

    # ── state transitions ─────────────────────────────────────
    def _show_loading(self):
        self._play_cv.pack_forget()
        self._load_cv.pack(fill="both", expand=True)
        self.root.geometry(f"{self.W}x{self.H_TITLE + self.H_LOADING}")
        self._animate_spinner()

    def _show_playback(self):
        self._load_cv.pack_forget()
        self._play_cv.pack(fill="both", expand=True)
        self.root.geometry(f"{self.W}x{self.H_TITLE + self.H_PLAYBACK}")
        self.root.update_idletasks()
        self.root.after(50, self._update_progress)

    # ── spinner ───────────────────────────────────────────────
    def _animate_spinner(self):
        if not self._load_cv.winfo_ismapped():
            return
        self._spin_idx = (self._spin_idx + 1) % len(self.SPIN)
        self._load_cv.itemconfig(self._spin_item, text=self.SPIN[self._spin_idx])
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

        c = self._play_cv
        c.itemconfig(self._pos_item, text=fmt(pos))
        c.itemconfig(self._dur_item, text=fmt(dur))

        # Slide cover to reveal gradient
        frac   = min(pos / dur, 1.0) if dur > 0 else 0.0
        filled = int(self._pw * frac)
        c.coords(self._prog_cover,
                 self._px + 1 + filled, self._py + 1,
                 self._px + self._pw - 1, self._py + self._ph - 1)

        # Play / pause icon
        icon = "⏸" if self.player.is_playing else "▶"
        c.itemconfig("btn_play_txt", text=icon)

        self.root.after(100, self._update_progress)

    # ── controls ──────────────────────────────────────────────
    def _toggle_play(self):
        if self.player:
            playing = self.player.toggle()
            self._play_cv.itemconfig("btn_play_txt", text="⏸" if playing else "▶")

    def _rewind(self):
        if self.player:
            self.player.seek(-10)

    def _forward(self):
        if self.player:
            self.player.seek(10)

    def _handle_end(self):
        c = self._play_cv
        c.itemconfig("btn_play_txt", text="▶")
        c.itemconfig(self._pos_item, text="0:00")
        # Reset cover to full width (hide gradient)
        c.coords(self._prog_cover,
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

    # ── dragging ──────────────────────────────────────────────
    def _drag_start(self, event):
        # Don't start drag if clicking the close button
        item = self._title_cv.find_closest(event.x, event.y)
        if item and any(t.startswith("cls_") for t in self._title_cv.gettags(item[0])):
            return
        self._drag_ref = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _drag_move(self, event):
        if self._drag_ref:
            x = event.x_root - self._drag_ref[0]
            y = event.y_root - self._drag_ref[1]
            self.root.geometry(f"+{x}+{y}")

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
        self._show_playback()


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
