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

# ── Colour palette (Catppuccin Mocha) ───────────────────────
C_BG      = "#1E1E2E"
C_SURFACE = "#2A2A3D"
C_BORDER  = "#45475A"
C_ACCENT  = "#CBA6F2"
C_TEXT    = "#CDD6F4"
C_SUBTEXT = "#A6ADC8"
C_BTN_HV  = "#CBA6F2"


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
        self.ended    = False   # polled by controller; True after natural end
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

    # ── public API ────────────────────────────────────────
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
        """Toggle play/pause. Returns True if now playing."""
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
#  KokoroController  —  draggable tkinter player window
# ════════════════════════════════════════════════════════════
class KokoroController:
    W          = 320
    H_LOADING  = 72
    H_PLAYBACK = 116
    SPIN       = ["◐", "◓", "◑", "◒"]

    def __init__(self):
        import tkinter as tk
        self.tk   = tk
        self.root = tk.Tk()
        self.player:    AudioPlayer | None = None
        self._tmp_wav:  str | None         = None
        self._spin_idx  = 0
        self._drag_ref  = None
        self._end_handled = False

        self._setup_window()
        self._build_title_bar()
        self._build_loading_frame()
        self._build_playback_frame()
        self._show_loading()

    # ── window setup ──────────────────────────────────────
    def _setup_window(self):
        r = self.root
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=C_BG)
        r.resizable(False, False)

        sw = r.winfo_screenwidth()
        sh = r.winfo_screenheight()
        x  = sw - self.W - 20
        y  = sh - self.H_LOADING - 70
        r.geometry(f"{self.W}x{self.H_LOADING}+{x}+{y}")

        # Rounded corners on Windows 11
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(r.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4
            )
        except Exception:
            pass

    # ── title bar (drag handle) ───────────────────────────
    def _build_title_bar(self):
        tk = self.tk
        bar = tk.Frame(self.root, bg=C_SURFACE, height=30)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._title_lbl = tk.Label(
            bar, text="Kokoro TTS", fg=C_SUBTEXT, bg=C_SURFACE,
            font=("Segoe UI", 9),
        )
        self._title_lbl.pack(side="left", padx=12)

        close = tk.Label(
            bar, text="✕", fg=C_SUBTEXT, bg=C_SURFACE,
            font=("Segoe UI", 10), cursor="hand2",
        )
        close.pack(side="right", padx=10)
        close.bind("<Button-1>", self._close)
        close.bind("<Enter>", lambda e: close.config(fg=C_TEXT))
        close.bind("<Leave>", lambda e: close.config(fg=C_SUBTEXT))

        for w in (bar, self._title_lbl):
            w.bind("<ButtonPress-1>",   self._drag_start)
            w.bind("<B1-Motion>",       self._drag_move)

        self._title_bar = bar

    # ── loading frame ─────────────────────────────────────
    def _build_loading_frame(self):
        tk = self.tk
        f  = tk.Frame(self.root, bg=C_BG)

        row = tk.Frame(f, bg=C_BG)
        row.pack(expand=True)

        self._spin_lbl = tk.Label(
            row, text="◐", fg=C_ACCENT, bg=C_BG,
            font=("Segoe UI", 14, "bold"),
        )
        self._spin_lbl.pack(side="left", padx=(0, 10))

        self._gen_lbl = tk.Label(
            row, text="Generating...", fg=C_TEXT, bg=C_BG,
            font=("Segoe UI", 11),
        )
        self._gen_lbl.pack(side="left")

        self._loading_frame = f

    # ── playback frame ────────────────────────────────────
    def _build_playback_frame(self):
        tk = self.tk
        f  = tk.Frame(self.root, bg=C_BG)

        # Progress bar (canvas for full style control)
        prog_wrap = tk.Frame(f, bg=C_BG)
        prog_wrap.pack(fill="x", padx=16, pady=(10, 2))

        self._prog_canvas = tk.Canvas(
            prog_wrap, height=6, bg=C_BORDER,
            highlightthickness=0, cursor="hand2",
        )
        self._prog_canvas.pack(fill="x")
        self._prog_fill = self._prog_canvas.create_rectangle(
            0, 0, 0, 6, fill=C_ACCENT, outline="",
        )
        self._prog_canvas.bind("<Button-1>",        self._seek_click)
        self._prog_canvas.bind("<B1-Motion>",       self._seek_click)

        # Time labels
        time_row = tk.Frame(f, bg=C_BG)
        time_row.pack(fill="x", padx=16)

        self._pos_lbl = tk.Label(
            time_row, text="0:00", fg=C_SUBTEXT, bg=C_BG,
            font=("Segoe UI", 9),
        )
        self._pos_lbl.pack(side="left")

        self._dur_lbl = tk.Label(
            time_row, text="0:00", fg=C_SUBTEXT, bg=C_BG,
            font=("Segoe UI", 9),
        )
        self._dur_lbl.pack(side="right")

        # Control buttons
        btn_row = tk.Frame(f, bg=C_BG)
        btn_row.pack(expand=True)

        self._btn_rew  = self._make_btn(btn_row, "« 10",  self._rewind)
        self._btn_play = self._make_btn(btn_row, "▶",     self._toggle_play, size=22)
        self._btn_fwd  = self._make_btn(btn_row, "10 »",  self._forward)

        self._btn_rew .pack(side="left", padx=18)
        self._btn_play.pack(side="left", padx=18)
        self._btn_fwd .pack(side="left", padx=18)

        self._playback_frame = f

    def _make_btn(self, parent, text, cmd, size=11):
        btn = self.tk.Label(
            parent, text=text, fg=C_TEXT, bg=C_BG,
            font=("Segoe UI", size), cursor="hand2",
        )
        btn.bind("<Button-1>", lambda e: cmd())
        btn.bind("<Enter>",    lambda e, b=btn: b.config(fg=C_BTN_HV))
        btn.bind("<Leave>",    lambda e, b=btn: b.config(fg=C_TEXT))
        return btn

    # ── state transitions ─────────────────────────────────
    def _show_loading(self):
        self._playback_frame.pack_forget()
        self._loading_frame.pack(fill="both", expand=True)
        self.root.geometry(f"{self.W}x{self.H_LOADING}")
        self._animate_spinner()

    def _show_playback(self):
        self._loading_frame.pack_forget()
        self._playback_frame.pack(fill="both", expand=True)
        self.root.geometry(f"{self.W}x{self.H_PLAYBACK}")
        # Force layout so winfo_ismapped() and winfo_width() are valid
        self.root.update_idletasks()
        self.root.after(50, self._update_progress)

    # ── spinner animation ─────────────────────────────────
    def _animate_spinner(self):
        if not self._loading_frame.winfo_ismapped():
            return
        self._spin_idx = (self._spin_idx + 1) % len(self.SPIN)
        self._spin_lbl.config(text=self.SPIN[self._spin_idx])
        self.root.after(130, self._animate_spinner)

    # ── progress polling ──────────────────────────────────
    def _update_progress(self):
        if not self.player:
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return

        # Detect natural end of playback (polled — no callback needed)
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

        self._pos_lbl.config(text=fmt(pos))
        self._dur_lbl.config(text=fmt(dur))

        # If the canvas hasn't been laid out yet, force it
        w = self._prog_canvas.winfo_width()
        if w <= 1:
            self.root.update_idletasks()
            w = self._prog_canvas.winfo_width()

        if w > 1 and dur > 0:
            filled = int(w * min(pos / dur, 1.0))
            self._prog_canvas.coords(self._prog_fill, 0, 0, filled, 6)

        self._btn_play.config(text="⏸" if self.player.is_playing else "▶")

        self.root.after(100, self._update_progress)

    # ── controls ──────────────────────────────────────────
    def _toggle_play(self):
        if self.player:
            playing = self.player.toggle()
            self._btn_play.config(text="⏸" if playing else "▶")

    def _rewind(self):
        if self.player:
            self.player.seek(-10)

    def _forward(self):
        if self.player:
            self.player.seek(10)

    def _seek_click(self, event):
        if not self.player:
            return
        w = self._prog_canvas.winfo_width()
        if w > 0:
            self.player.seek_to(max(0.0, min(1.0, event.x / w)))

    def _handle_end(self):
        """Audio finished naturally — reset to start, ready to replay."""
        self._btn_play.config(text="▶")
        self._pos_lbl.config(text="0:00")
        self._prog_canvas.coords(self._prog_fill, 0, 0, 0, 6)

    def _close(self, _event=None):
        if self.player:
            self.player.close()
        if self._tmp_wav:
            try:
                os.remove(self._tmp_wav)
            except OSError:
                pass
        self.root.destroy()

    # ── dragging ──────────────────────────────────────────
    def _drag_start(self, event):
        self._drag_ref = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _drag_move(self, event):
        if self._drag_ref:
            x = event.x_root - self._drag_ref[0]
            y = event.y_root - self._drag_ref[1]
            self.root.geometry(f"+{x}+{y}")

    # ── entry point ───────────────────────────────────────
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

        # Save to temp WAV
        fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="kokoro_")
        os.close(fd)
        self._tmp_wav = tmp

        s16 = (samples * 32767).astype(np.int16)
        with wave.open(tmp, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(s16.tobytes())

        # Hand off to main thread
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
    print(f"done  ({elapsed:.2f}s → {duration:.1f}s audio, {duration/elapsed:.1f}x realtime)")
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
    parser.add_argument("text",        nargs="?",              help="Text to synthesize")
    parser.add_argument("--file",      metavar="FILE",         help="Read text from a UTF-8 file")
    parser.add_argument("--voice",     default="af_heart",     help="Voice (default: af_heart)")
    parser.add_argument("--speed",     type=float, default=1.0,help="Speed multiplier (default: 1.0)")
    parser.add_argument("--out",       default="output.wav",   help="Output WAV (default: output.wav)")
    parser.add_argument("--play",      action="store_true",    help="Show player controller and play")
    parser.add_argument("--autoplay",  action="store_true",    help="Play silently with no GUI (used by hook)")
    parser.add_argument("--list-voices",action="store_true",   help="List all voices and exit")
    args = parser.parse_args()

    if args.list_voices:
        list_voices()
        return

    # Resolve text
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
