"""
On-screen player for the voice queue (voiced.py): a floating capsule that opens into a panel.

Look: MadeFire "Ember Capsule" (obsidian glass pill, ember orb) with the glowing edges of "Forge"
(amber hairline, a breathing amber rim while a reply is speaking). Fonts: Outfit, DM Sans and
JetBrains Mono, bundled in fonts/ and loaded privately for this process only.

Speed: everything is drawn on one Canvas (no widget per row), the 4 Hz state poll only touches items
whose values changed, and the frame timer runs only while something moves. The old Tk player rebuilt
every history row whenever a background run was logged, which froze it for a second or two each time.

Three sizes:
  capsule  one line: who is speaking, what about, how far; badge for calls waiting on you
  panel    the full summary, scrubbing, and your calls (pick an option, or type another / ask)
  library  panel plus up next and the last 7 days, each replayable
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent

KEY = "#020203"  # window transparency key: never used for anything drawn
CARD, CARD_HI, HOVER, HAIR = "#121214", "#1a1a1d", "#1d1d21", "#29292e"
FG, BODY, MUTED, FAINT = "#f7f7f7", "#e2e2e6", "#8e8f92", "#5f6066"
AMBER, EMBER, BRONZE, COOLED = "#F59E0B", "#FBBF24", "#D97706", "#EA580C"
OBSIDIAN = "#0b0b0d"
EDGE_HOT = "#8a5410"  # rim colour at the top of a breath

W = 384           # logical px
CAP_H = 64
PAD = 18
R_PANEL = 22
ORB = 46
LIST_MAX = 300
BREATH = 2.6      # seconds per breath while speaking
LEVELS = 12       # pre-rendered glow levels

GLYPHS = {  # Segoe Fluent Icons on Windows, plain symbols elsewhere
    "play": ("", "▶"), "pause": ("", "❚❚"), "next": ("", "⏭"),
    "volume": ("", "♪"), "mute": ("", "✕"), "down": ("", "⌄"),
    "up": ("", "⌃"), "close": ("", "✕"), "send": ("", "➤"),
    "left": ("", "‹"), "right": ("", "›"),
}
STATUS_TAGS = {"not spoken: headless run": "background", "not spoken: muted": "muted",
               "not spoken: project muted": "project muted", "not spoken: do not disturb": "do not disturb",
               "not spoken: nothing to say": "no update", "not spoken: grouped": "grouped",
               "skipped": "skipped", "cleared": "cleared", "removed": "removed"}
BACKGROUND = ("not spoken: headless run", "not spoken: grouped", "not spoken: nothing to say")


# ---------------------------------------------------------------- small helpers
def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    t = min(1.0, max(0.0, t))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def when(ts: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0")


def day_label(ts: float) -> str:
    d, today = time.localtime(ts), time.localtime()
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(d[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    if days < 0.5:
        return "Today"
    if days < 1.5:
        return "Yesterday"
    return time.strftime("%A, %b %d", d).replace(" 0", " ")


def clock(sec: float) -> str:
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def load_fonts() -> None:
    """Bundled fonts, visible to this process only (nothing is installed)."""
    if sys.platform != "win32":
        return
    import ctypes
    for f in sorted((_HERE / "fonts").glob("*.ttf")):
        try:
            ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)  # FR_PRIVATE
        except Exception:
            pass


# ---------------------------------------------------------------- pre-rendered art (Pillow)
class Art:
    SS = 4  # supersampling for smooth edges

    def __init__(self, S: float):
        from PIL import Image, ImageDraw, ImageFilter, ImageTk
        self.I, self.D, self.F, self.TK = Image, ImageDraw, ImageFilter, ImageTk
        self.S = S
        self._cache: dict = {}

    def p(self, n: float) -> int:
        return max(1, int(round(n * self.S)))

    def _down(self, im, w, h):
        return im.convert("RGBa").resize((w, h), self.I.LANCZOS).convert("RGBA")

    def get(self, key, make):
        ph = self._cache.get(key)
        if ph is None:
            ph = self._cache[key] = self.TK.PhotoImage(make())
        return ph

    @staticmethod
    def _rgba(c: str, a: int = 255):
        return (*_rgb(c), a)

    # the ember orb, at a glow level 0..1; dim = idle
    def orb(self, level: float, dim: bool = False):
        def make():
            I, D, F, SS = self.I, self.D, self.F, self.SS
            d, g = self.p(ORB), self.p(18)
            size = d + 2 * g
            out = I.new("RGBA", (size, size), (0, 0, 0, 0))
            halo = I.new("RGBA", (size, size), (0, 0, 0, 0))
            if dim:  # idle: dark glass with an amber rim; the glyph carries the colour
                n = d * SS
                ball = I.new("RGBA", (n, n), (0, 0, 0, 0))
                dr = D.Draw(ball)
                dr.ellipse((0, 0, n - 1, n - 1), fill=(245, 158, 11, 130))
                k = int(SS * 1.5)
                dr.ellipse((k, k, n - 1 - k, n - 1 - k), fill=(32, 27, 22, 255))
                out.alpha_composite(self._down(ball, d, d), (g, g))
                return out
            a = int(70 + 120 * level)
            D.Draw(halo).ellipse((g - self.p(3), g - self.p(3), g + d + self.p(3), g + d + self.p(3)),
                                 fill=(245, 158, 11, a))
            out.alpha_composite(halo.filter(F.GaussianBlur(g / 2.4)))
            n = d * SS
            ball = I.new("RGBA", (n, n), (0, 0, 0, 0))
            dr = D.Draw(ball)
            stops = ([(0, (92, 52, 14)), (0.6, (138, 84, 16)), (1, (176, 116, 30))] if dim else
                     [(0, (234, 88, 12)), (0.55, (245, 158, 11)), (1, (253, 214, 110))])
            hx, hy = n * 0.36, n * 0.30
            steps = 56
            for i in range(steps):
                t = i / (steps - 1)
                r = (n / 2) * (1 - t * 0.92)
                x, y = n / 2 + (hx - n / 2) * t, n / 2 + (hy - n / 2) * t
                for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
                    if t0 <= t <= t1:
                        k = (t - t0) / (t1 - t0)
                        col = tuple(round(u + (v - u) * k) for u, v in zip(c0, c1))
                        break
                dr.ellipse((x - r, y - r, x + r, y + r), fill=(*col, 255))
            out.alpha_composite(self._down(ball, d, d), (g, g))
            return out
        return self.get(("orb", round(level, 3), dim), make)

    def knob(self):
        def make():
            I, D, F = self.I, self.D, self.F
            d, g = self.p(11), self.p(8)
            size = d + 2 * g
            out = I.new("RGBA", (size, size), (0, 0, 0, 0))
            halo = I.new("RGBA", (size, size), (0, 0, 0, 0))
            D.Draw(halo).ellipse((g, g, g + d, g + d), fill=(245, 158, 11, 200))
            out.alpha_composite(halo.filter(F.GaussianBlur(g / 2)))
            n = d * self.SS
            ball = I.new("RGBA", (n, n), (0, 0, 0, 0))
            D.Draw(ball).ellipse((0, 0, n - 1, n - 1), fill=self._rgba(EMBER))
            out.alpha_composite(self._down(ball, d, d), (g, g))
            return out
        return self.get("knob", make)

    def circle(self, d: float, fill=None, outline=None):
        def make():
            I, D, SS = self.I, self.D, self.SS
            dp = self.p(d)
            n = dp * SS
            im = I.new("RGBA", (n, n), (0, 0, 0, 0))
            dr = D.Draw(im)
            if outline:
                dr.ellipse((0, 0, n - 1, n - 1), fill=outline)
                dr.ellipse((SS, SS, n - 1 - SS, n - 1 - SS), fill=fill or (0, 0, 0, 0))
            else:
                dr.ellipse((0, 0, n - 1, n - 1), fill=fill)
            return self._down(im, dp, dp)
        return self.get(("circle", d, fill, outline), make)

    def pill(self, w: int, h: int, fill=None, outline=None, grad=None):
        """w, h in device px. grad=(c0, c1) paints a diagonal gradient instead of fill."""
        def make():
            I, D, SS = self.I, self.D, self.SS
            n_w, n_h = w * SS, h * SS
            mask = I.new("L", (n_w, n_h), 0)
            D.Draw(mask).rounded_rectangle((0, 0, n_w - 1, n_h - 1), radius=n_h // 2, fill=255)
            if grad:
                c0, c1 = _rgb(grad[0]), _rgb(grad[1])
                g = I.new("RGBA", (256, 1))
                g.putdata([tuple(round(a + (b - a) * x / 255) for a, b in zip(c0, c1)) + (255,) for x in range(256)])
                body = g.resize((n_w, n_h), I.BILINEAR)
            else:
                body = I.new("RGBA", (n_w, n_h), fill or (0, 0, 0, 0))
            im = I.new("RGBA", (n_w, n_h), (0, 0, 0, 0))
            if outline:
                im.paste(I.new("RGBA", (n_w, n_h), outline), (0, 0), mask)
                inner = I.new("L", (n_w, n_h), 0)
                D.Draw(inner).rounded_rectangle((SS, SS, n_w - 1 - SS, n_h - 1 - SS), radius=n_h // 2 - SS, fill=255)
                im.paste(I.new("RGBA", (n_w, n_h), (0, 0, 0, 0)), (0, 0), inner)
                if fill or grad:
                    im.paste(body, (0, 0), inner)
            else:
                im.paste(body, (0, 0), mask)
            return self._down(im, w, h)
        return self.get(("pill", w, h, fill, outline, grad), make)

    def corners(self, r: int, edge: str):
        """Four corner masks (device px radius r): key colour outside the curve, the rim on it, clear inside."""
        def make_all():
            I, D, SS = self.I, self.D, self.SS
            n = 2 * r * SS
            im = I.new("RGBA", (n, n), self._rgba(KEY))
            dr = D.Draw(im)
            dr.ellipse((0, 0, n - 1, n - 1), fill=self._rgba(edge))
            dr.ellipse((SS, SS, n - 1 - SS, n - 1 - SS), fill=(0, 0, 0, 0))
            full = self._down(im, 2 * r, 2 * r)
            return [full.crop(b) for b in ((0, 0, r, r), (r, 0, 2 * r, r), (0, r, r, 2 * r), (r, r, 2 * r, 2 * r))]
        key = ("corners", r, edge)
        if key not in self._cache:
            self._cache[key] = [self.TK.PhotoImage(c) for c in make_all()]
        return self._cache[key]

    def glow(self, w: int, h: int, kind: str):
        """Ambient light inside the card: amber from one side, a faint teal backlight, the Forge hairline on top."""
        def make():
            I, D, F = self.I, self.D, self.F
            im = I.new("RGBA", (w, h), (0, 0, 0, 0))
            lights = ([(w - self.p(70), h + self.p(10), self.p(150), (245, 158, 11, 46)),
                       (self.p(20), -self.p(20), self.p(120), (20, 184, 166, 16))] if kind == "cap" else
                      [(w - self.p(40), -self.p(50), self.p(190), (245, 158, 11, 44)),
                       (self.p(10), self.p(40), self.p(150), (20, 184, 166, 12))])
            for x, y, r, col in lights:
                layer = I.new("RGBA", (w, h), (0, 0, 0, 0))
                D.Draw(layer).ellipse((x - r, y - r, x + r, y + r), fill=col)
                im.alpha_composite(layer.filter(F.GaussianBlur(r / 2.2)))
            line = I.new("RGBA", (w, 1), (0, 0, 0, 0))
            px = line.load()
            for x in range(w):
                t = x / max(1, w - 1)
                a = max(0.0, 1 - abs(t - 0.58) / 0.42) ** 1.6
                px[x, 0] = (245, 158, 11, int(185 * a))
            im.alpha_composite(line, (0, 0))
            return im
        return self.get(("glow", w, h, kind), make)


# ---------------------------------------------------------------- the player
class Widget:
    def __init__(self, vq, api):
        """api: log, open_session(item) -> str, resume_session(vq, item, answer) -> bool."""
        import tkinter as tk
        import tkinter.font as tkfont
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
        load_fonts()
        self.tk, self.vq, self.api = tk, vq, api
        self.root = root = tk.Tk()
        root.report_callback_exception = lambda *exc: api.log(
            "player error: " + "".join(__import__("traceback").format_exception(*exc))[-1500:])
        root.title("Voice queue")
        self.S = max(1.0, root.winfo_fpixels("1i") / 96)
        self.Wp = self.p(W)
        self.art = Art(self.S)
        fams = set(tkfont.families(root))

        def fam(*names):
            return next((n for n in names if n in fams), "TkDefaultFont")
        outfit_b = fam("VQ Outfit Bold", "Segoe UI Semibold", "DejaVu Sans")
        outfit = fam("VQ Outfit Semibold", "Segoe UI Semibold", "DejaVu Sans")
        dm = fam("VQ DM Sans", "Segoe UI", "DejaVu Sans")
        dm_m = fam("VQ DM Sans Medium", "Segoe UI Semibold", "DejaVu Sans")
        mono = fam("VQ JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono")
        icon = next((n for n in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if n in fams), None)
        self._g = 0 if icon else 1

        def font(family, px, bold=False):
            return tkfont.Font(root=root, family=family, size=-self.p(px), weight="bold" if bold else "normal")
        bold_fallback = outfit_b.startswith("Segoe") or outfit_b.startswith("DejaVu")
        self.F = {
            "title": font(outfit_b, 17, bold_fallback), "name": font(outfit, 14.5),
            "q": font(outfit, 15), "body": font(dm, 14), "line": font(dm, 12.5), "meta": font(dm, 11.5),
            "chip": font(dm_m, 13), "row": font(dm_m, 13), "snip": font(dm, 12), "mono": font(mono, 10),
            "mono_s": font(mono, 9), "icon": font(icon or dm, 12 if icon else 13), "icon_s": font(icon or dm, 10),
            "icon_orb": font(icon or dm, 14 if icon else 15), "link": font(dm_m, 12), "entry": font(dm, 13),
        }

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        for attr, val in (("-transparentcolor", KEY), ("-alpha", 0.0)):
            try:
                root.attributes(attr, val)
            except tk.TclError:
                pass
        root.configure(bg=KEY)

        # state
        self.mode = "cap"           # cap | panel
        self.lib = False            # library open (panel only)
        self.h = self.p(CAP_H)      # current drawn height (device px)
        self._target_h = self.h
        self.hidden_until = 0.0
        self.hidden_for_next = None
        self.answered: set[str] = set()
        self._call_ix = 0
        self._auto_open = None      # call id that opened the panel by itself
        self._calls: list[dict] = []
        self._cur = None
        self._q: list[dict] = []
        self._paused = False
        self._shown = None
        self._prog = (0.0, 0.0, 0.0, False)  # pos, dur, wall clock, running
        self._scrub = None
        self._level = -1
        self._anims: dict = {}
        self._frame_job = None
        self._texts: dict = {}
        self._fills: dict = {}
        self._layout_key = None
        self._lib_sig = None
        self._open_row = None
        self._toast_until = 0.0
        self._tip = None
        self._tip_job = None
        self._visible = False
        self._badge_left = self.Wp
        self._cap_track_end = self.Wp
        self._bar_y = 0

        self._build()
        self._place()
        self._apply_mode(instant=True)
        self._tick()

    # ---------------------------------------------------------- tiny utils
    def p(self, n: float) -> int:
        return int(round(n * self.S))

    def glyph(self, name: str) -> str:
        return GLYPHS[name][self._g]

    def _text(self, item, text: str) -> bool:
        if self._texts.get(item) != text:
            self._texts[item] = text
            self.c.itemconfigure(item, text=text)
            return True
        return False

    def _fill(self, item, color: str) -> None:
        if self._fills.get(item) != color:
            self._fills[item] = color
            self.c.itemconfigure(item, fill=color)

    def _ellipsize(self, text: str, font, maxw: int) -> str:
        text = " ".join((text or "").split())
        if font.measure(text) <= maxw:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid].rstrip() + "…") <= maxw:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + "…"

    def _anim(self, name, dur, fn, ease=ease_out, done=None):
        self._anims[name] = {"t0": time.perf_counter(), "dur": max(0.001, dur), "fn": fn, "ease": ease, "done": done}
        self._kick()

    def _kick(self):
        if self._frame_job is None:
            self._frame_job = self.root.after(0, self._frame)

    def _frame(self):
        self._frame_job = None
        now = time.perf_counter()
        for name, a in list(self._anims.items()):
            t = min(1.0, (now - a["t0"]) / a["dur"])
            try:
                a["fn"](a["ease"](t))
            except Exception as e:
                self.api.log(f"anim {name}: {e!r}")
                t = 1.0
            if t >= 1.0 and self._anims.get(name) is a:
                del self._anims[name]
                if a["done"]:
                    a["done"]()
        live = self._live(now)
        if self._anims or live:
            self._frame_job = self.root.after(16 if self._anims else 40, self._frame)

    # ---------------------------------------------------------- build
    def _build(self):
        tk, p, c0 = self.tk, self.p, None
        self.c = c = tk.Canvas(self.root, width=self.Wp, height=p(900), bg=KEY, highlightthickness=0, bd=0)
        c.place(x=0, y=0)
        # a borderless window never takes the keyboard by itself: clicking it does, so Space, M, 1-9 and Esc work
        c.bind("<ButtonPress-1>", lambda e: self.root.focus_force(), add="+")
        Wp = self.Wp

        # card: fill, rim, ambient light, corners on top (disabled: clicks fall through)
        self.card = c.create_rectangle(0, 0, Wp, self.h, fill=CARD, width=0, tags=("bg",))
        self.rim_top = c.create_line(0, 0, Wp, 0, fill=HAIR, tags=("bg",))
        self.glow_cap = c.create_image(0, 0, anchor="nw", image=self.art.glow(Wp, p(CAP_H), "cap"),
                                       tags=("bg", "glowcap"))
        self.glow_pan = c.create_image(0, 0, anchor="nw", image=self.art.glow(Wp, p(240), "pan"),
                                       tags=("bg", "glowpan"), state="hidden")
        self.rim_l = c.create_line(0, 0, 0, self.h, fill=HAIR, tags=("bg",))
        self.rim_r = c.create_line(Wp - 1, 0, Wp - 1, self.h, fill=HAIR, tags=("bg",))
        self.rim_b = c.create_line(0, self.h - 1, Wp, self.h - 1, fill=HAIR, tags=("bg",))
        for ev, fn in (("<ButtonPress-1>", self._drag_start), ("<B1-Motion>", self._drag), ("<Button-3>", self._menu)):
            c.tag_bind("bg", ev, fn)
            c.tag_bind("drag", ev, fn)

        # orb (shared by both sizes; it glides between them)
        self.orb = c.create_image(0, 0, image=self.art.orb(0, dim=True), tags=("orb",))
        self.orb_g = c.create_text(0, 0, text=self.glyph("play"), font=self.F["icon_orb"], fill=OBSIDIAN, tags=("orb",))
        c.tag_bind("orb", "<Button-1>", lambda e: self._orb_click())
        c.tag_bind("orb", "<Enter>", lambda e: self._orb_hover(True))
        c.tag_bind("orb", "<Leave>", lambda e: self._orb_hover(False))
        self._tipify("orb", lambda: ("Replay the last one" if not (self._cur or self._q) else
                                     "Resume (Space)" if self._paused else "Pause (Space)"))

        # ---- capsule
        self.cap_name = c.create_text(p(66), p(22), anchor="w", text="", font=self.F["name"], fill=FG,
                                      tags=("cap", "capname"))
        self.cap_line = c.create_text(p(66), p(41), anchor="w", text="", font=self.F["line"], fill=MUTED,
                                      tags=("cap", "drag"))
        self.cap_track = c.create_line(p(66), p(55), p(200), p(55), fill=HAIR, width=max(1, p(2)), tags=("cap",))
        self.cap_fill = c.create_line(p(66), p(55), p(66), p(55), fill=AMBER, width=max(1, p(2)), tags=("cap",))
        c.tag_bind("capname", "<Button-1>", lambda e: self._open(self._shown))
        c.tag_bind("capname", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._fill(self.cap_name, EMBER)))
        c.tag_bind("capname", "<Leave>", lambda e: (c.configure(cursor=""), self._fill(self.cap_name, FG)))
        self._tipify("capname", lambda: "Open this session")
        self.badge = c.create_image(0, p(32), anchor="e", tags=("cap", "badge"), state="hidden")
        self.badge_t = c.create_text(0, p(32), text="", font=self.F["mono_s"], fill=OBSIDIAN,
                                     tags=("cap", "badge"), state="hidden")
        c.tag_bind("badge", "<Button-1>", lambda e: self._set_mode("panel"))
        self._tipify("badge", lambda: "Open the panel")

        # icon buttons (both sizes)
        self.btn = {}
        for name, glyph, cmd, tip in (
                ("close", "close", self.hide_until_next, "Hide until the next voiceover (Esc)"),
                ("size", "down", self._toggle_mode, lambda: "Smaller" if self.mode == "panel" else "Open the panel"),
                ("mute", "volume", self.toggle_mute, lambda: "Unmute" if self.vq.muted else
                 "Mute: new replies go to history only (M)")):
            self.btn[name] = self._icon_button(name, glyph, cmd, tip)

        # ---- panel
        self.pan_name = c.create_text(p(78), p(31), anchor="w", text="", font=self.F["title"], fill=FG,
                                      tags=("pan", "panname"))
        self.pan_arrow = c.create_text(0, p(31), anchor="w", text="↗", font=self.F["name"], fill=AMBER,
                                       tags=("pan", "panname"))
        self.pan_meta = c.create_text(p(78), p(53), anchor="w", text="", font=self.F["meta"], fill=MUTED,
                                      tags=("pan", "drag"))
        c.tag_bind("panname", "<Button-1>", lambda e: self._open(self._shown))
        c.tag_bind("panname", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._fill(self.pan_name, EMBER)))
        c.tag_bind("panname", "<Leave>", lambda e: (c.configure(cursor=""), self._fill(self.pan_name, FG)))
        self._tipify("panname", lambda: "Open this session")
        self.body = c.create_text(p(PAD), 0, anchor="nw", text="", font=self.F["body"], fill=BODY,
                                  width=self.Wp - 2 * p(PAD), tags=("pan", "play", "drag"))
        self.bar_hit = c.create_rectangle(p(PAD) - p(4), 0, Wp - p(PAD) + p(4), 0, fill=CARD, width=0,
                                          tags=("pan", "play", "bar"))
        self.bar_track = c.create_line(p(PAD), 0, Wp - p(PAD), 0, fill=HAIR, width=max(1, p(3)), capstyle="round",
                                       tags=("pan", "play", "bar"))
        self.bar_fill = c.create_line(p(PAD), 0, p(PAD), 0, fill=AMBER, width=max(1, p(3)), capstyle="round",
                                      tags=("pan", "play", "bar"))
        self.bar_tip = c.create_line(p(PAD), 0, p(PAD), 0, fill=EMBER, width=max(1, p(3)), capstyle="round",
                                     tags=("pan", "play", "bar"))
        self.knob = c.create_image(p(PAD), 0, image=self.art.knob(), tags=("pan", "play", "bar"))
        c.tag_bind("bar", "<ButtonPress-1>", self._scrub_move)
        c.tag_bind("bar", "<B1-Motion>", self._scrub_move)
        c.tag_bind("bar", "<ButtonRelease-1>", self._scrub_end)
        c.tag_bind("bar", "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind("bar", "<Leave>", lambda e: c.configure(cursor=""))
        self.skip_bg = c.create_image(0, 0, image=self.art.circle(34, outline=(255, 255, 255, 36)),
                                      tags=("pan", "play", "skip"))
        self.skip_g = c.create_text(0, 0, text=self.glyph("next"), font=self.F["icon"], fill=FG,
                                    tags=("pan", "play", "skip"))
        c.tag_bind("skip", "<Button-1>", lambda e: self.vq.skip())
        c.tag_bind("skip", "<Enter>", lambda e: (c.itemconfigure(self.skip_bg, image=self.art.circle(
            34, fill=(255, 255, 255, 16), outline=(255, 255, 255, 60))), c.configure(cursor="hand2")))
        c.tag_bind("skip", "<Leave>", lambda e: (c.itemconfigure(self.skip_bg, image=self.art.circle(
            34, outline=(255, 255, 255, 36))), c.configure(cursor="")))
        self._tipify("skip", lambda: "Skip to the next one (Right arrow)")
        self.times = c.create_text(0, 0, anchor="w", text="", font=self.F["mono"], fill=FAINT, tags=("pan", "play"))
        self.next_t = c.create_text(0, 0, anchor="e", text="", font=self.F["meta"], fill=MUTED, tags=("pan", "play"))

        # calls waiting on you
        self.call_rule = c.create_line(0, 0, 0, 0, fill=AMBER, tags=("pan", "ask"))
        self.call_eyebrow = c.create_text(0, 0, anchor="w", text="YOUR CALL", font=self.F["mono"], fill=EMBER,
                                          tags=("pan", "ask"))
        self.call_from = c.create_text(0, 0, anchor="w", text="", font=self.F["mono"], fill=MUTED,
                                       tags=("pan", "ask", "callfrom"))
        c.tag_bind("callfrom", "<Button-1>", lambda e: self._open(self._call()))
        c.tag_bind("callfrom", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._fill(self.call_from, FG)))
        c.tag_bind("callfrom", "<Leave>", lambda e: (c.configure(cursor=""), self._fill(self.call_from, MUTED)))
        self._tipify("callfrom", lambda: "Open this session")
        self.call_nav = c.create_text(0, 0, anchor="e", text="", font=self.F["mono"], fill=FAINT, tags=("pan", "ask"))
        self.call_prev = c.create_text(0, 0, anchor="e", text=self.glyph("left"), font=self.F["icon_s"], fill=MUTED,
                                       tags=("pan", "ask", "callprev"))
        self.call_next = c.create_text(0, 0, anchor="e", text=self.glyph("right"), font=self.F["icon_s"], fill=MUTED,
                                       tags=("pan", "ask", "callnext"))
        for tag, d in (("callprev", -1), ("callnext", 1)):
            c.tag_bind(tag, "<Button-1>", lambda e, d=d: self._step_call(d))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
        self.call_dismiss = c.create_text(0, 0, anchor="e", text="Dismiss", font=self.F["mono"], fill=FAINT,
                                          tags=("pan", "ask", "dismiss"))
        c.tag_bind("dismiss", "<Button-1>", lambda e: self._dismiss())
        c.tag_bind("dismiss", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._fill(self.call_dismiss, FG)))
        c.tag_bind("dismiss", "<Leave>", lambda e: (c.configure(cursor=""), self._fill(self.call_dismiss, FAINT)))
        self.question = c.create_text(p(PAD), 0, anchor="nw", text="", font=self.F["q"], fill=FG,
                                      width=self.Wp - 2 * p(PAD), tags=("pan", "ask", "drag"))
        self.chips: list[tuple] = []
        self.entry_bg = c.create_image(p(PAD), 0, anchor="nw", tags=("pan", "ask"))
        self.entry = tk.Entry(c, bg=CARD_HI, fg=FAINT, insertbackground=FG, relief="flat", bd=0,
                              highlightthickness=0, font=self.F["entry"])
        self.entry_win = c.create_window(p(PAD) + p(16), 0, anchor="w", window=self.entry,
                                         width=self.Wp - 2 * p(PAD) - p(16) - p(48), tags=("pan", "ask"))
        self._hint = "Another option, or ask a follow-up"
        self.entry.insert(0, self._hint)
        self.entry.bind("<FocusIn>", self._entry_focus)
        self.entry.bind("<FocusOut>", self._entry_blur)
        self.entry.bind("<Return>", lambda e: self._send_typed())
        self.entry.bind("<Escape>", lambda e: (self.root.focus_set(), "break")[1])
        self.send_bg = c.create_image(0, 0, image=self.art.circle(32, fill=Art._rgba(AMBER)), tags=("pan", "ask", "send"))
        self.send_g = c.create_text(0, 0, text=self.glyph("send"), font=self.F["icon_s"], fill=OBSIDIAN,
                                    tags=("pan", "ask", "send"))
        c.tag_bind("send", "<Button-1>", lambda e: self._send_typed())
        c.tag_bind("send", "<Enter>", lambda e: (c.itemconfigure(self.send_bg, image=self.art.circle(
            32, fill=Art._rgba(EMBER))), c.configure(cursor="hand2")))
        c.tag_bind("send", "<Leave>", lambda e: (c.itemconfigure(self.send_bg, image=self.art.circle(
            32, fill=Art._rgba(AMBER))), c.configure(cursor="")))
        self._tipify("send", lambda: "Send to the session (Enter)")

        # library toggle + list
        self.lib_t = c.create_text(p(PAD), 0, anchor="w", text="QUEUE & HISTORY", font=self.F["mono"], fill=MUTED,
                                   tags=("pan", "libtoggle"))
        self.lib_g = c.create_text(0, 0, anchor="w", text=self.glyph("down"), font=self.F["icon_s"], fill=MUTED,
                                   tags=("pan", "libtoggle"))
        self.lib_count = c.create_text(self.Wp - p(PAD), 0, anchor="e", text="", font=self.F["mono"], fill=FAINT,
                                       tags=("pan", "libtoggle"))
        c.tag_bind("libtoggle", "<Button-1>", lambda e: self._toggle_lib())
        c.tag_bind("libtoggle", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._fill(self.lib_t, FG),
                                                      self._fill(self.lib_g, FG)))
        c.tag_bind("libtoggle", "<Leave>", lambda e: (c.configure(cursor=""), self._fill(self.lib_t, MUTED),
                                                      self._fill(self.lib_g, MUTED)))
        self.lst = tk.Canvas(c, bg=CARD, highlightthickness=0, bd=0, width=self.Wp - 2 * p(PAD), height=p(10))
        self.lst_win = c.create_window(p(PAD), 0, anchor="nw", window=self.lst, width=self.Wp - 2 * p(PAD),
                                       height=p(10), tags=("lib",), state="hidden")
        self.lst.bind("<MouseWheel>", lambda e: self.lst.yview_scroll(int(-e.delta / 40) or (-1 if e.delta > 0 else 1),
                                                                      "units"))
        self.lst.bind("<Button-4>", lambda e: self.lst.yview_scroll(-3, "units"))
        self.lst.bind("<Button-5>", lambda e: self.lst.yview_scroll(3, "units"))
        self.lst.configure(yscrollincrement=self.p(8))

        # corner masks last: on top of everything, insensitive to the mouse
        self.corner = [c.create_image(0, 0, anchor="nw", state="disabled") for _ in range(4)]
        self._radius = None
        self._edge = None

        root = self.root
        root.bind("<Escape>", lambda e: self.hide_until_next())
        root.bind("<space>", lambda e: self._key(self.toggle_pause))
        root.bind("<Right>", lambda e: self._key(self.vq.skip))
        root.bind("<m>", lambda e: self._key(self.toggle_mute))
        for i in range(1, 10):
            root.bind(str(i), lambda e, i=i: self._key(lambda: self._pick_number(i)))

    def _key(self, fn):
        if self.root.focus_get() is self.entry:
            return
        fn()

    def _icon_button(self, name, glyph, cmd, tip):
        c, tag = self.c, f"btn_{name}"
        bg = c.create_image(0, 0, image=self.art.circle(30, fill=(255, 255, 255, 16)), state="hidden", tags=(tag,))
        g = c.create_text(0, 0, text=self.glyph(glyph), font=self.F["icon"], fill=MUTED, tags=(tag,))
        c.tag_bind(tag, "<Button-1>", lambda e: cmd())

        def hover(on):
            c.itemconfigure(bg, state="normal" if on else "hidden")
            self._fill(g, FG if on else self._btn_color(name))
            c.configure(cursor="hand2" if on else "")
        c.tag_bind(tag, "<Enter>", lambda e: hover(True))
        c.tag_bind(tag, "<Leave>", lambda e: hover(False))
        self._tipify(tag, tip if callable(tip) else (lambda: tip))
        return {"bg": bg, "g": g, "tag": tag, "glyph": glyph}

    def _btn_color(self, name):
        return AMBER if name == "mute" and self.vq.muted else MUTED

    def _set_glyph(self, name, glyph):
        b = self.btn[name]
        if b["glyph"] != glyph:
            b["glyph"] = glyph
            self.c.itemconfigure(b["g"], text=self.glyph(glyph))

    # ---------------------------------------------------------- tooltips
    def _tipify(self, tag, text_fn):
        self.c.tag_bind(tag, "<Enter>", lambda e: self._tip_arm(text_fn), add="+")
        self.c.tag_bind(tag, "<Leave>", lambda e: self._tip_hide(), add="+")
        self.c.tag_bind(tag, "<ButtonPress>", lambda e: self._tip_hide(), add="+")

    def _tip_arm(self, text_fn):
        self._tip_hide()
        self._tip_job = self.root.after(550, lambda: self._tip_show(text_fn()))

    def _tip_show(self, text):
        tk = self.tk
        self._tip_job = None
        x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        t = self._tip = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.configure(bg=HAIR)
        tk.Label(t, text=text, bg="#1c1c20", fg=BODY, font=self.F["meta"], padx=self.p(8), pady=self.p(4)).pack(
            padx=1, pady=1)
        t.update_idletasks()
        tx = min(x - t.winfo_reqwidth() // 2, self.root.winfo_screenwidth() - t.winfo_reqwidth() - self.p(8))
        t.geometry(f"+{max(self.p(8), tx)}+{y + self.p(18)}")

    def _tip_hide(self):
        if self._tip_job:
            self.root.after_cancel(self._tip_job)
            self._tip_job = None
        if self._tip:
            self._tip.destroy()
            self._tip = None

    # ---------------------------------------------------------- window
    def _place(self):
        x = self.root.winfo_screenwidth() - self.Wp - self.p(24)
        self.root.geometry(f"{self.Wp}x{self.h}+{x}+{self.p(24)}")
        self._xy = (x, self.p(24))

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag(self, e):
        x, y = e.x_root - self._dx, e.y_root - self._dy
        self._xy = (x, y)
        self.root.geometry(f"+{x}+{y}")

    def _set_height(self, h: int):
        h = max(self.p(CAP_H), int(h))
        self.h = h
        c, Wp = self.c, self.Wp
        c.coords(self.card, 0, 0, Wp, h)
        c.coords(self.rim_l, 0, 0, 0, h)
        c.coords(self.rim_r, Wp - 1, 0, Wp - 1, h)
        c.coords(self.rim_b, 0, h - 1, Wp, h - 1)
        r = self._radius or 0
        for i, (x, y) in enumerate(((0, 0), (Wp - r, 0), (0, h - r), (Wp - r, h - r))):
            c.coords(self.corner[i], x, y)
        x, y = getattr(self, "_xy", (self.root.winfo_x(), self.root.winfo_y()))
        self.root.geometry(f"{Wp}x{h}+{x}+{y}")

    def _set_shape(self, radius: int, edge: str):
        if (radius, edge) == (self._radius, self._edge):
            return
        self._radius, self._edge = radius, edge
        for item, img in zip(self.corner, self.art.corners(radius, edge)):
            self.c.itemconfigure(item, image=img)
        for line in (self.rim_l, self.rim_r, self.rim_b, self.rim_top):
            self._fill(line, edge)
        self._set_height(self.h)

    def _resize(self, target: int, instant=False):
        target = int(target)
        if target == self._target_h and not instant:
            return
        self._target_h = target
        start = self.h
        if instant or not self._visible or abs(target - start) < 2:
            self._anims.pop("height", None)
            self._set_height(target)
            return
        self._anim("height", 0.26, lambda t: self._set_height(start + (target - start) * t), ease=ease_out)

    def _fade_window(self, to: float, done=None, dur=0.18):
        try:
            start = float(self.root.attributes("-alpha"))
        except (self.tk.TclError, ValueError):
            start = 1.0

        def step(t):
            try:
                self.root.attributes("-alpha", start + (to - start) * t)
            except self.tk.TclError:
                pass
        self._anim("alpha", dur, step, ease=ease_out, done=done)

    def show(self):
        self.hidden_until, self.hidden_for_next = 0.0, None
        if self._visible:
            return
        self._visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        x, y = self._xy
        self.root.geometry(f"+{x}+{y - self.p(10)}")
        self._anim("slide", 0.28, lambda t: self.root.geometry(f"+{x}+{round(y - self.p(10) * (1 - t))}"))
        self._fade_window(1.0)
        self._level = -1

    def _withdraw(self):
        self._visible = False

        def gone():
            if not self._visible:
                self.root.withdraw()
        self._fade_window(0.0, done=gone, dur=0.14)

    def _hide(self, secs):
        self.hidden_for_next = None
        self.hidden_until = time.time() + secs
        self._withdraw()

    def hide_until_next(self):
        cur = self.vq.state()["current"]
        self.hidden_until = 0.0
        self.hidden_for_next = (cur or {}).get("id", "")
        self._withdraw()

    def quit(self):
        self.vq.stop()
        self.root.destroy()

    def toggle_mute(self):
        self.vq.set_muted(not self.vq.muted)
        self._tick_once()

    def toggle_pause(self):
        self.vq.resume() if self.vq.paused else self.vq.pause()
        self._tick_once()

    def _menu(self, e):
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label="Hide until the next voiceover", command=self.hide_until_next)
        m.add_command(label="Hide for 1 hour", command=lambda: self._hide(3600))
        proj = (self._shown or {}).get("project")
        if proj and hasattr(self.vq, "quiet_project"):
            m.add_command(label=f"Mute {proj} for 1 hour", command=lambda: self.vq.quiet_project(proj, 3600))
        m.add_separator()
        m.add_command(label="Clear the queue", command=self.vq.clear)
        m.add_command(label="Quit voice queue", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    # ---------------------------------------------------------- sizes
    def _toggle_mode(self):
        self._set_mode("cap" if self.mode == "panel" else "panel")

    def _set_mode(self, mode, auto=False):
        if mode == self.mode:
            return
        self.mode = mode
        if mode == "cap":
            self.lib = False
            self._auto_open = None
            if self.root.focus_get() is self.entry:
                self.root.focus_set()
        self._apply_mode()

    def _toggle_lib(self):
        self.lib = not self.lib
        self._lib_sig = None
        self._layout_key = None
        self._tick_once()

    def _apply_mode(self, instant=False):
        c, p = self.c, self.p
        cap = self.mode == "cap"
        self._layout_key = None
        if cap:
            c.itemconfigure("pan", state="hidden")
            c.itemconfigure("lib", state="hidden")
            c.itemconfigure("glowpan", state="hidden")
            c.itemconfigure("cap", state="normal")
            c.itemconfigure("glowcap", state="normal")
        else:
            c.itemconfigure("cap", state="hidden")
            c.itemconfigure("glowcap", state="hidden")
        self._set_shape(p(CAP_H) // 2 if cap else p(R_PANEL), self._edge or HAIR)
        self._set_glyph("size", "down" if cap else "up")
        # orb glides to its spot; buttons move with it
        ox0, oy0 = c.coords(self.orb) or (0, 0)
        ox1, oy1 = (p(9 + ORB / 2), p(CAP_H / 2)) if cap else (p(PAD + ORB / 2), p(PAD + ORB / 2))

        def orb_to(t):
            x, y = ox0 + (ox1 - ox0) * t, oy0 + (oy1 - oy0) * t
            c.coords(self.orb, x, y)
            c.coords(self.orb_g, x, y)
        if instant or not self._visible:
            orb_to(1.0)
        else:
            self._anim("orb", 0.3, orb_to, ease=ease_in_out)
        self._place_buttons()
        if cap:
            self._resize(p(CAP_H), instant=instant)
        self._tick_once(force=True)
        if cap:
            self._reveal("cap")
        else:
            self._reveal("pan")

    def _place_buttons(self):
        p, Wp, c = self.p, self.Wp, self.c
        y = p(CAP_H / 2) if self.mode == "cap" else p(PAD + ORB / 2)
        names = ("close", "size") if self.mode == "cap" else ("close", "size", "mute")
        for i, name in enumerate(("close", "size", "mute")):
            b = self.btn[name]
            if name in names:
                x = Wp - p(12 if self.mode == "cap" else 10) - p(15) - i * p(31)
                c.coords(b["bg"], x, y)
                c.coords(b["g"], x, y)
                c.itemconfigure(b["g"], state="normal")
            else:
                c.itemconfigure(b["g"], state="hidden")
                c.itemconfigure(b["bg"], state="hidden")

    def _reveal(self, group):
        """Fade a group's text in after a size change (images just appear)."""
        c = self.c
        items = [i for i in c.find_withtag(group) if c.type(i) == "text" and c.itemcget(i, "state") != "hidden"]
        targets = {i: self._fills.get(i) or c.itemcget(i, "fill") for i in items}
        if not self._visible:
            return

        def step(t):
            for i, col in targets.items():
                if col and col.startswith("#") and len(col) == 7:
                    c.itemconfigure(i, fill=mix(CARD, col, t))

        def done():
            for i, col in targets.items():
                c.itemconfigure(i, fill=self._fills.get(i, col))
        self._anim("reveal", 0.22, step, done=done)

    # ---------------------------------------------------------- orb, progress, breathing
    def _orb_click(self):
        if self._cur or self._q:
            self.toggle_pause()
        else:
            self._replay_last()

    def _orb_hover(self, on):
        self.c.configure(cursor="hand2" if on else "")

    def _live(self, now: float) -> bool:
        if not self._visible:
            return False
        pos, dur, wall, running = self._prog
        speaking = running and self._cur is not None
        if dur:
            cur_pos = pos + ((time.time() - wall) if running else 0)
            frac = min(1.0, max(0.0, cur_pos / dur))
            if self._scrub is not None:
                frac = self._scrub
            self._draw_progress(frac, dur)
        # breathing ember: orb glow and the card's rim
        if speaking:
            phase = (now % BREATH) / BREATH
            lvl = round((0.5 - 0.5 * math.cos(2 * math.pi * phase)) * (LEVELS - 1))
        else:
            lvl = -2 if not (self._cur or self._q) else LEVELS // 2
        if lvl != self._level:
            self._level = lvl
            dim = lvl == -2
            k = 0 if dim else lvl / (LEVELS - 1)
            self.c.itemconfigure(self.orb, image=self.art.orb(k, dim=dim))
            edge = mix(HAIR, EDGE_HOT, k * 0.9) if speaking else HAIR
            self._set_shape(self._radius, edge)
        return speaking or self._scrub is not None

    def _draw_progress(self, frac, dur):
        c, p, Wp = self.c, self.p, self.Wp
        if self.mode == "cap":
            x0, x1 = p(66), self._cap_track_end
            y = p(55)
            c.coords(self.cap_track, x0, y, x1, y)
            c.coords(self.cap_fill, x0, y, x0 + (x1 - x0) * frac, y)
        else:
            y = self._bar_y
            x0, x1 = p(PAD), Wp - p(PAD)
            x = x0 + (x1 - x0) * frac
            c.coords(self.bar_track, x0, y, x1, y)
            c.coords(self.bar_fill, x0, y, x, y)
            c.coords(self.bar_tip, max(x0, x - p(28)), y, x, y)
            c.coords(self.knob, x, y)
            self._text(self.times, f"{clock(frac * dur)} / {clock(dur)}")

    def _scrub_move(self, e):
        x0, x1 = self.p(PAD), self.Wp - self.p(PAD)
        self._scrub = min(1.0, max(0.0, (e.x - x0) / max(1, x1 - x0)))
        self._kick()

    def _scrub_end(self, e):
        if self._scrub is not None and self._cur:
            self.vq.seek(self._scrub)
            pos, dur, _, running = self._prog
            self._prog = (self._scrub * dur, dur, time.time(), running)
        self._scrub = None

    # ---------------------------------------------------------- calls (decisions waiting on Yury)
    def _collect_calls(self, cur) -> list[dict]:
        now = time.time()
        calls, seen = [], set()
        rows = self.vq.history.recent(150)
        newest: dict[str, float] = {}  # a later reply from the same session means it was answered there
        for r in rows:
            sid = r.get("session_id")
            if sid and r.get("status") not in BACKGROUND:
                newest[sid] = max(newest.get(sid, 0), r.get("ts", 0))
        if cur and cur.get("decision") and (cur.get("replay_of") or cur["id"]) not in self.answered:
            calls.append(cur)
            seen.add(cur.get("replay_of") or cur["id"])
        for r in rows:
            stale = newest.get(r.get("session_id") or "", 0) > r.get("ts", 0) + 1
            if (r.get("decision") and not r.get("answer") and not r.get("dismissed") and not stale
                    and r["id"] not in self.answered and r["id"] not in seen and now - r.get("ts", 0) < 86400):
                calls.append(r)
                seen.add(r["id"])
        return calls

    def _call(self):
        if not self._calls:
            return None
        self._call_ix = min(self._call_ix, len(self._calls) - 1)
        return self._calls[self._call_ix]

    def _step_call(self, d):
        if self._calls:
            self._call_ix = (self._call_ix + d) % len(self._calls)
            self._layout_key = None
            self._tick_once()

    def _call_id(self, item):
        return item.get("replay_of") or item["id"]

    def _dismiss(self):
        item = self._call()
        if not item:
            return
        cid = self._call_id(item)
        self.answered.add(cid)
        self.vq.history.update(cid, dismissed=True)
        self._after_call()

    def _after_call(self):
        self._layout_key = None
        self._tick_once()
        if not self._calls and self._auto_open:
            self.root.after(1600, lambda: not self._calls and self._auto_open and self._set_mode("cap"))

    def _pick_number(self, i):
        item = self._call()
        if item and self.mode == "panel" and i <= len(item["decision"]["options"]):
            self._answer(item, item["decision"]["options"][i - 1])

    def _send_typed(self):
        text = self.entry.get().strip()
        item = self._call()
        if item and text and text != self._hint:
            self.entry.delete(0, "end")
            self.root.focus_set()
            self._answer(item, text)
        return "break"

    def _entry_focus(self, _e):
        if self.entry.get() == self._hint:
            self.entry.delete(0, "end")
        self.entry.configure(fg=FG)
        self.c.itemconfigure(self.entry_bg, image=self._entry_img(True))

    def _entry_blur(self, _e):
        if not self.entry.get().strip():
            self.entry.delete(0, "end")
            self.entry.insert(0, self._hint)
            self.entry.configure(fg=FAINT)
        self.c.itemconfigure(self.entry_bg, image=self._entry_img(False))

    def _entry_img(self, focus):
        return self.art.pill(self.Wp - 2 * self.p(PAD), self.p(42), fill=Art._rgba(CARD_HI),
                             outline=(245, 158, 11, 150) if focus else (255, 255, 255, 30))

    def _answer(self, item, text):
        cid = self._call_id(item)
        self.answered.add(cid)
        self.vq.history.update(cid, answer=text, answered_at=time.time())
        if self.api.resume_session(self.vq, item, text):
            self._toast(f"Sent to {item.get('project')}. Its reply will play here")
        else:
            self._copy(text)
            msg = self.api.open_session(item)
            self._toast((msg + ". " if msg else "") + "No session to resume: answer copied")
        self._after_call()

    # ---------------------------------------------------------- open, copy, replay, toast
    def _open(self, item):
        if not item:
            return
        msg = self.api.open_session(item)
        if not msg and item.get("session_id"):
            self._copy(self._resume_cmd(item))
            msg = "Resume command copied"
        self._toast(msg or "Can't tell where this session runs")

    @staticmethod
    def _resume_cmd(item) -> str:
        return f'cd "{item.get("cwd")}"; claude --resume {item["session_id"]}'

    def _copy(self, s, note=""):
        self.root.clipboard_clear()
        self.root.clipboard_append(s)
        if note:
            self._toast(note)

    def _toast(self, text):
        self._toast_until = time.time() + 2.6
        self._text(self.cap_line, self._ellipsize(text, self.F["line"], self._cap_line_w()))
        self._fill(self.cap_line, EMBER)
        self._text(self.pan_meta, self._ellipsize(text, self.F["meta"], self.Wp - self.p(78) - self.p(106)))
        self._fill(self.pan_meta, EMBER)

    def _replay(self, item_id):
        if self.vq.muted:
            self.vq.set_muted(False)
        self.vq.replay(item_id)
        self._tick_once()

    def _replay_last(self):
        rows = [r for r in self.vq.history.recent(40) if r.get("status") == "spoken"] or self.vq.history.recent(1)
        if rows:
            self._replay(rows[0]["id"])

    # ---------------------------------------------------------- the 4 Hz poll
    def _tick(self):
        try:
            self._tick_once()
        except Exception as e:
            self.api.log(f"tick error: {e!r}")
        if not self.vq._stop:
            self.root.after(250, self._tick)

    def _cap_line_w(self):
        return self._badge_left - self.p(66) - self.p(10)

    def _tick_once(self, force=False):
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
        first = not hasattr(self, "_started")
        if first:
            self._started = True
            self.show()
        if self.hidden_until and time.time() >= self.hidden_until:
            self.show()
        if self.hidden_for_next is not None and cur and cur.get("id") != self.hidden_for_next:
            self.api.log(f"showing again for {cur.get('id')}")
            self.show()
        if not self._visible and self.hidden_for_next is None and not self.hidden_until and (cur or q):
            self.show()
        self.vq.window["hidden"] = ("until next voiceover" if self.hidden_for_next is not None else
                                    time.strftime("until %H:%M", time.localtime(self.hidden_until))
                                    if self.hidden_until else None)
        self._cur, self._q, self._paused = cur, q, st["paused"]

        # calls: newest first; a new one opens the panel by itself
        calls = self._collect_calls(cur)
        new_ids = [self._call_id(i) for i in calls]
        old_ids = [self._call_id(i) for i in self._calls]
        if new_ids != old_ids:
            fresh = [i for i in new_ids if i not in old_ids]
            self._calls = calls
            if fresh and not first:
                self._call_ix = new_ids.index(fresh[0])
                if self.mode == "cap" and self._visible:
                    self._auto_open = fresh[0]
                    self._set_mode("panel")
            self._layout_key = None
        else:
            self._calls = calls

        show = cur or (q[0] if q else None)
        self._shown = show
        p = self.p

        # progress model (interpolated by the frame timer)
        if cur:
            running = not st["paused"] and cur.get("status") == "playing"
            self._prog = (cur.get("position", 0.0), cur.get("duration", 0.0), time.time(), running)
        else:
            self._prog = (0.0, 0.0, 0.0, False)

        toast = time.time() < self._toast_until
        # badge (capsule)
        if self.vq.muted:
            badge, filled = "MUTED", False
        elif self._calls:
            badge, filled = (f"{len(self._calls)} CALL" + ("S" if len(self._calls) > 1 else "")), True
        elif q:
            badge, filled = f"{len(q)} NEXT", False
        else:
            badge, filled = "", False
        right = self.Wp - p(12) - p(15) - p(31) - p(17)
        if badge:
            tw = self.F["mono_s"].measure(badge)
            bw, bh = tw + p(16), p(20)
            img = self.art.pill(bw, bh, grad=(EMBER, AMBER)) if filled else self.art.pill(
                bw, bh, outline=(255, 255, 255, 40))
            self.c.itemconfigure(self.badge, image=img)
            self.c.coords(self.badge, right, p(CAP_H / 2))
            self.c.coords(self.badge_t, right - bw / 2, p(CAP_H / 2))
            self._text(self.badge_t, badge)
            self._fill(self.badge_t, OBSIDIAN if filled else MUTED)
            self._badge_left = right - bw
        else:
            self._badge_left = right + p(17)
        if self.mode == "cap":
            self.c.itemconfigure("badge", state="normal" if badge else "hidden")
        self._cap_track_end = self._badge_left - p(10) if badge else right - p(4)

        # orb glyph
        self.c.itemconfigure(self.orb_g, text=self.glyph("pause" if cur and not st["paused"] else "play"))
        self._fill(self.orb_g, OBSIDIAN if (cur or q) else EMBER)
        self._fill(self.btn["mute"]["g"], self._btn_color("mute"))
        self._set_glyph("mute", "mute" if self.vq.muted else "volume")

        # capsule text
        if show:
            name = show.get("project") or "?"
            line = show.get("text", "") if show.get("ready", True) else "Summarizing the reply…"
            if st["paused"] and cur:
                line = "Paused · " + line
        elif self._calls:
            n = len(self._calls)
            name = f"{n} call{'s' if n > 1 else ''} waiting on you"
            line = f"{self._calls[0].get('project')}: {self._calls[0]['decision']['question']}"
        else:
            last = self.vq.history.recent(1)
            name = "All caught up"
            line = (f"Last: {last[0].get('project')} · {when(last[0]['ts'])}" if last
                    else "Waiting for the next reply")
        nmax = self._badge_left - p(66) - p(10)
        self._text(self.cap_name, self._ellipsize(name, self.F["name"], nmax))
        if not toast:
            self._text(self.cap_line, self._ellipsize(line, self.F["line"], self._cap_line_w()))
            self._fill(self.cap_line, MUTED)
        if self.mode == "cap":
            st_play = "normal" if cur else "hidden"
            self.c.itemconfigure(self.cap_track, state=st_play)
            self.c.itemconfigure(self.cap_fill, state=st_play)
            if not cur:
                pass
            elif not self._prog[3]:
                pos, dur = self._prog[0], self._prog[1]
                self._draw_progress(pos / dur if dur else 0, dur)
        else:
            self._panel(show, cur, q, st, toast, force)

        self._live(time.perf_counter())
        if self._prog[3] or self._anims:
            self._kick()
        try:
            self.vq.window["geometry"] = [self.root.winfo_x(), self.root.winfo_y(), self.Wp, self.h]
        except self.tk.TclError:
            pass

    # ---------------------------------------------------------- panel layout
    def _panel(self, show, cur, q, st, toast, force):
        c, p, Wp = self.c, self.p, self.Wp
        call = self._call()
        playing = bool(show)
        body_text = ""
        if show:
            body_text = show.get("text", "") if show.get("ready", True) else "Summarizing the reply…"
            if len(body_text) > 420:
                body_text = body_text[:417].rstrip() + "…"
        lib_rows, lib_sig, bg_count = self._library_rows(q) if self.lib else ([], None, 0)
        key = (playing, body_text, show.get("id") if show else None,
               self._call_id(call) if call else None, self._call_ix, len(self._calls),
               self.lib, lib_sig, self.mode)
        # header (cheap, every tick)
        if show:
            name = show.get("project") or "?"
            sid = (show.get("session_id") or "")[:8]
            host = (show.get("host") or {}).get("kind")
            where = {"desktop": "Claude desktop", "vscode": "VS Code", "cursor": "Cursor",
                     "terminal": "Terminal"}.get(host, "")
            meta = " · ".join(x for x in (when(show["ts"]), where,
                                               "replay" if show.get("replay_of") else "",
                                               "paused" if st["paused"] and cur else "") if x)
        else:
            last = self.vq.history.recent(1)
            n = len(self._calls)
            name = f"{n} call{'s' if n > 1 else ''} waiting on you" if n else "All caught up"
            meta = (f"Last: {last[0].get('project')} · {when(last[0]['ts'])}" if last
                    else "Waiting for the next reply")
        nmax = Wp - p(78) - p(110)
        shown_name = self._ellipsize(name, self.F["title"], nmax)
        if self._text(self.pan_name, shown_name) or force:
            c.coords(self.pan_arrow, p(78) + self.F["title"].measure(shown_name) + p(6), p(31))
        if not toast:
            self._text(self.pan_meta, self._ellipsize(meta, self.F["meta"], Wp - p(78) - p(106)))
            self._fill(self.pan_meta, MUTED)
        self._text(self.next_t, self._ellipsize(f"Next: {q[0].get('project')}" if q else "Nothing after this",
                                                self.F["meta"], p(150)))
        if key == self._layout_key and not force:
            c.itemconfigure(self.pan_arrow, state="normal" if show else "hidden")
            return
        self._layout_key = key

        # full layout: positions for everything below the header
        y = p(PAD + ORB + 16)
        state = lambda on: "normal" if on else "hidden"
        c.itemconfigure("pan", state="normal")
        if playing:
            self._text(self.body, body_text)
            c.coords(self.body, p(PAD), y)
            bb = c.bbox(self.body)
            y = (bb[3] if bb else y) + p(16)
            self._bar_y = y
            c.coords(self.bar_hit, p(PAD) - p(4), y - p(10), Wp - p(PAD) + p(4), y + p(10))
            y += p(22)
            cy = y + p(17)
            c.coords(self.skip_bg, p(PAD) + p(17), cy)
            c.coords(self.skip_g, p(PAD) + p(17), cy)
            c.coords(self.times, p(PAD) + p(34) + p(12), cy)
            c.coords(self.next_t, Wp - p(PAD), cy)
            y += p(34) + p(4)
            pos, dur = self._prog[0], self._prog[1]
            self._draw_progress(pos / dur if dur else 0, dur)
            if not cur:
                self._text(self.times, "up next")
        else:
            self._bar_y = 0
        c.itemconfigure("play", state=state(playing))

        # calls
        for ids in self.chips:
            for i in ids:
                c.delete(i)
        self.chips = []
        if call:
            y += p(14)
            c.coords(self.call_rule, p(PAD), y, p(PAD) + p(20), y)
            c.coords(self.call_eyebrow, p(PAD) + p(28), y)
            ew = self.F["mono"].measure("YOUR CALL")
            frm = self._ellipsize(f"· {call.get('project') or '?'}", self.F["mono"], p(150))
            self._text(self.call_from, frm)
            c.coords(self.call_from, p(PAD) + p(28) + ew + p(8), y)
            right = Wp - p(PAD)
            c.coords(self.call_dismiss, right, y)
            right -= self.F["mono"].measure("Dismiss") + p(20)
            many = len(self._calls) > 1
            if many:
                c.coords(self.call_next, right, y)
                right -= p(16)
                self._text(self.call_nav, f"{self._call_ix + 1}/{len(self._calls)}")
                c.coords(self.call_nav, right, y)
                right -= self.F["mono"].measure(f"{self._call_ix + 1}/{len(self._calls)}") + p(6)
                c.coords(self.call_prev, right, y)
            for it in (self.call_nav, self.call_prev, self.call_next):
                c.itemconfigure(it, state=state(many))
            y += p(16)
            self._text(self.question, call["decision"]["question"])
            c.coords(self.question, p(PAD), y)
            bb = c.bbox(self.question)
            y = (bb[3] if bb else y) + p(12)
            x = p(PAD)
            ch = p(36)
            maxw = Wp - 2 * p(PAD)
            rec = call["decision"].get("recommended")
            for n, opt in enumerate(call["decision"]["options"]):
                is_rec = rec == n
                label = ("★ " if is_rec else "") + opt
                font = self.F["chip"]
                tw = min(font.measure(label), maxw - p(32))
                w = tw + p(30)
                if x > p(PAD) and x + w > Wp - p(PAD):
                    x, y = p(PAD), y + ch + p(8)
                label = self._ellipsize(label, font, tw + 1)
                base = self.art.pill(w, ch, grad=(AMBER, COOLED)) if is_rec else self.art.pill(
                    w, ch, outline=(255, 255, 255, 36))
                hover = self.art.pill(w, ch, grad=(EMBER, AMBER)) if is_rec else self.art.pill(
                    w, ch, fill=(255, 255, 255, 14), outline=(245, 158, 11, 120))
                tag = f"chip{n}"
                img = c.create_image(x, y, anchor="nw", image=base, tags=("pan", "ask", tag))
                txt = c.create_text(x + w / 2, y + ch / 2, text=label, font=font,
                                    fill=OBSIDIAN if is_rec else FG, tags=("pan", "ask", tag))
                c.tag_bind(tag, "<Button-1>", lambda e, o=opt, it=call: self._answer(it, o))
                c.tag_bind(tag, "<Enter>", lambda e, i=img, h=hover: (c.itemconfigure(i, image=h),
                                                                      c.configure(cursor="hand2")))
                c.tag_bind(tag, "<Leave>", lambda e, i=img, b=base: (c.itemconfigure(i, image=b),
                                                                     c.configure(cursor="")))
                self._tipify(tag, lambda n=n, o=opt: (f"Press {n + 1} to pick" if n < 9 else o))
                self.chips.append((img, txt))
                x += w + p(8)
            y += ch + p(12)
            c.itemconfigure(self.entry_bg, image=self._entry_img(self.root.focus_get() is self.entry))
            c.coords(self.entry_bg, p(PAD), y)
            c.coords(self.entry_win, p(PAD) + p(18), y + p(21))
            c.coords(self.send_bg, Wp - p(PAD) - p(5) - p(16), y + p(21))
            c.coords(self.send_g, Wp - p(PAD) - p(5) - p(16), y + p(21))
            y += p(42) + p(4)
            c.tag_raise("ask")
        c.itemconfigure("ask", state=state(bool(call)))
        if call and len(self._calls) < 2:
            for it in (self.call_nav, self.call_prev, self.call_next):
                c.itemconfigure(it, state="hidden")
        if not call and self.root.focus_get() is self.entry:
            self.root.focus_set()

        # library
        y += p(16)
        ly = y + p(7)
        c.coords(self.lib_t, p(PAD), ly)
        c.coords(self.lib_g, p(PAD) + self.F["mono"].measure("QUEUE & HISTORY") + p(6), ly + p(1))
        self._set_lib_glyph()
        c.coords(self.lib_count, Wp - p(PAD), ly)
        today = sum(1 for r in self.vq.history.recent(150)
                    if r.get("status") == "spoken" and day_label(r["ts"]) == "Today")
        self._text(self.lib_count, " · ".join(x for x in (f"{len(q)} up next" if q else "",
                                                                f"{today} today") if x))
        y += p(14)
        if self.lib:
            y += p(10)
            hl = self._render_library(lib_rows, bg_count, q)
            room = self.root.winfo_screenheight() - self._xy[1] - p(48) - (y + p(R_PANEL))
            h = max(p(120), min(p(LIST_MAX), hl, room))
            c.coords(self.lst_win, p(PAD), y)
            c.itemconfigure(self.lst_win, height=h, state="normal")
            y += h
        else:
            c.itemconfigure(self.lst_win, state="hidden")
        y += p(R_PANEL)
        c.tag_raise("orb")
        for b in self.btn.values():
            c.tag_raise(b["tag"])
        for i in self.corner:
            c.tag_raise(i)
        c.itemconfigure("glowpan", state="normal")
        self._place_buttons()
        c.itemconfigure(self.pan_arrow, state="normal" if show else "hidden")
        self._resize(y)

    def _set_lib_glyph(self):
        self._text(self.lib_g, self.glyph("up" if self.lib else "down"))

    # ---------------------------------------------------------- library (one canvas, drawn items only)
    def _library_rows(self, q):
        rows = self.vq.history.recent(150)
        keep, bg_count = [], 0
        for r in rows:
            if r.get("status") in BACKGROUND:
                bg_count += 1
                continue
            keep.append(r)
        keep = keep[:60]
        sig = (tuple((r["id"], r.get("status"), bool(r.get("answer"))) for r in keep),
               tuple(i["id"] for i in q), self._open_row, bg_count // 25)
        return keep, sig, bg_count

    def _render_library(self, rows, bg_count, q) -> int:
        l, p = self.lst, self.p
        w = self.Wp - 2 * p(PAD)
        l.delete("all")
        y = 0

        def eyebrow(text, y):
            l.create_text(0, y + p(8), anchor="w", text=text, font=self.F["mono"], fill=MUTED)
            return y + p(22)
        if q:
            y = eyebrow("UP NEXT", y)
            for it in q[:6]:
                y = self._list_row(it, "queue", y, w)
            y += p(8)
        day = None
        for r in rows:
            d = day_label(r["ts"])
            if d != day:
                if day is not None:
                    y += p(6)
                day = d
                y = eyebrow(d.upper(), y)
            y = self._list_row(r, "history", y, w)
        if not rows and not q:
            l.create_text(0, y + p(12), anchor="w", text="Nothing yet. Voiceovers from the last 7 days show up here.",
                          font=self.F["snip"], fill=FAINT)
            y += p(28)
        if bg_count:
            l.create_text(0, y + p(14), anchor="w", font=self.F["mono_s"], fill=FAINT,
                          text=f"{bg_count} background runs logged, not spoken")
            y += p(28)
        l.configure(scrollregion=(0, 0, w, y))
        return y

    def _list_row(self, it, kind, y, w) -> int:
        l, p = self.lst, self.p
        tag = f"r{it['id']}"
        h = p(48)
        rect = l.create_rectangle(0, y, w, y + h, fill=CARD, width=0, tags=(tag,))
        if kind == "history":
            l.create_text(p(8), y + p(16), anchor="w", text=when(it["ts"]), font=self.F["mono_s"], fill=FAINT,
                          tags=(tag,))
        nx = p(70) if kind == "history" else p(8)
        name = it.get("project") or "?"
        st = it.get("status", "")
        label = STATUS_TAGS.get(st, "" if st in ("spoken", "queued", "") else st.split(":")[0][:14])
        if it.get("answer"):
            label = "answered"
        elif it.get("decision") and not it.get("dismissed") and kind == "history":
            label = "your call"
        l.create_text(nx, y + p(16), anchor="w", text=name, font=self.F["row"], fill=FG, tags=(tag,))
        if label:
            lx = nx + self.F["row"].measure(name) + p(8)
            l.create_text(lx, y + p(16), anchor="w", text=label.upper(), font=self.F["mono_s"],
                          fill=EMBER if label == "your call" else FAINT, tags=(tag,))
        snip = self._ellipsize(it.get("text", ""), self.F["snip"], w - nx - p(32))
        l.create_text(nx, y + p(34), anchor="w", text=snip, font=self.F["snip"], fill=MUTED, tags=(tag,))
        act = l.create_text(w - p(14), y + h / 2, text=self.glyph("play" if kind == "history" else "close"),
                            font=self.F["icon_s"], fill=CARD, tags=(tag, "act"))

        def hover(on):
            l.itemconfigure(rect, fill=HOVER if on else CARD)
            l.itemconfigure(act, fill=FG if on else CARD)
            l.configure(cursor="hand2" if on else "")

        def click(_e):
            cur = l.find_withtag("current")
            if cur and "act" in l.gettags(cur[0]):
                if kind == "history":
                    self._replay(it["id"])
                else:
                    self.vq.remove(it["id"])
                    self._tick_once()
            elif kind == "history":
                self._open_row = None if self._open_row == it["id"] else it["id"]
                self._layout_key = None
                self._tick_once()
        l.tag_bind(tag, "<Enter>", lambda e: hover(True))
        l.tag_bind(tag, "<Leave>", lambda e: hover(False))
        l.tag_bind(tag, "<Button-1>", click)
        l.tag_bind(tag, "<Double-Button-1>", lambda e: kind == "history" and self._replay(it["id"]))
        l.tag_bind(tag, "<Button-3>", lambda e: self._row_menu(e, it))
        y += h
        if kind == "history" and self._open_row == it["id"]:
            y = self._row_detail(it, y, w)
        return y

    def _row_detail(self, it, y, w) -> int:
        l, p = self.lst, self.p
        top = y
        bg = l.create_rectangle(0, y, w, y, fill=CARD_HI, width=0)
        y += p(10)
        t = l.create_text(p(12), y, anchor="nw", text=it.get("text", ""), font=self.F["snip"], fill=BODY,
                          width=w - p(24))
        bb = l.bbox(t)
        y = (bb[3] if bb else y) + p(6)
        sid = (it.get("session_id") or "")[:8]
        where = (it.get("cwd") or "unknown folder") + (f"  ·  session {sid}" if sid else "")
        t2 = l.create_text(p(12), y, anchor="nw", text=where, font=self.F["mono_s"], fill=FAINT, width=w - p(24))
        bb = l.bbox(t2)
        y = (bb[3] if bb else y) + p(10)
        x = p(12)
        links = [("Replay", lambda: self._replay(it["id"])), ("Open session", lambda: self._open(it))]
        if it.get("decision") and not it.get("answer"):
            links.append(("Answer", lambda: self._reopen_call(it)))
        if it.get("session_id"):
            links.append(("Copy resume", lambda: self._copy(self._resume_cmd(it), "Resume command copied")))
        if it.get("answer"):
            l.create_text(p(12), y, anchor="nw", text=f"You answered: {it['answer']}", font=self.F["snip"],
                          fill=EMBER, width=w - p(24))
            y += p(20)
        for i, (text, fn) in enumerate(links):
            tag = f"lnk{it['id']}{i}"
            item = l.create_text(x, y, anchor="nw", text=text, font=self.F["link"], fill=AMBER, tags=(tag,))
            l.tag_bind(tag, "<Button-1>", lambda e, fn=fn: fn())
            l.tag_bind(tag, "<Enter>", lambda e, i=item: (l.itemconfigure(i, fill=EMBER), l.configure(cursor="hand2")))
            l.tag_bind(tag, "<Leave>", lambda e, i=item: (l.itemconfigure(i, fill=AMBER), l.configure(cursor="")))
            x += self.F["link"].measure(text) + p(16)
        y += p(28)
        l.coords(bg, 0, top, w, y)
        l.tag_lower(bg)
        return y + p(4)

    def _reopen_call(self, it):
        self.answered.discard(it["id"])
        self.vq.history.update(it["id"], dismissed=False)
        self._tick_once()
        ids = [self._call_id(i) for i in self._calls]
        if it["id"] in ids:
            self._call_ix = ids.index(it["id"])
            self._layout_key = None
            self._tick_once()

    def _row_menu(self, e, it):
        m = self.tk.Menu(self.root, tearoff=0)
        m.add_command(label="Replay", command=lambda: self._replay(it["id"]))
        m.add_command(label="Open session", command=lambda: self._open(it))
        if it.get("session_id"):
            m.add_command(label="Copy resume command", command=lambda: self._copy(self._resume_cmd(it)))
        if it.get("cwd"):
            m.add_command(label="Copy folder path", command=lambda: self._copy(it["cwd"]))
        if it.get("project") and hasattr(self.vq, "quiet_project"):
            m.add_command(label=f"Mute {it['project']} for 1 hour",
                          command=lambda: self.vq.quiet_project(it["project"], 3600))
        m.tk_popup(e.x_root, e.y_root)

    def run(self):
        self.root.mainloop()
