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
import re
import sys
import threading
import time
from pathlib import Path

from voiced import SKIPPED, combined_answer, decisions_of, thread_title

_HERE = Path(__file__).resolve().parent

KEY = "#020203"  # window transparency key: never used for anything drawn
CARD, CARD_HI, HOVER, HAIR = "#121214", "#1a1a1d", "#1d1d21", "#29292e"
FG, BODY, MUTED, FAINT = "#f7f7f7", "#e2e2e6", "#8e8f92", "#7c7d83"
AMBER, EMBER, BRONZE, COOLED = "#F59E0B", "#FBBF24", "#D97706", "#EA580C"
OBSIDIAN = "#0b0b0d"
INK = (255, 255, 255)                          # faint outlines and hover washes are this colour at low alpha
GLOW_WARM, GLOW_COOL = (245, 158, 11), (20, 184, 166)  # the card's ambient light
ACC, ACC_HI, ACC_INK, ACC_RGB = ("#F59E0B", "#EA580C"), ("#FBBF24", "#F59E0B"), "#0b0b0d", (245, 158, 11)  # filled buttons
SW_KNOB = "#fffaf0"
ORB_GLASS = (32, 27, 22)                       # the idle orb's dark glass

# Themes set the card, text, hairline and ambient light. The pulse colour is its own setting.
# EMBER doubles as accent text (values, eyebrows), so a light theme darkens it. Faint text is >= 4.5:1.
THEMES = {
    "ember": dict(CARD="#121214", CARD_HI="#1a1a1d", HOVER="#1d1d21", HAIR="#29292e", FG="#f7f7f7",
                  BODY="#e2e2e6", MUTED="#8e8f92", FAINT="#7c7d83", EMBER="#FBBF24", INK=(255, 255, 255),
                  GLOW_WARM=(245, 158, 11), GLOW_COOL=(20, 184, 166), ORB_GLASS=(32, 27, 22),
                  ACC=("#F59E0B", "#EA580C"), ACC_HI=("#FBBF24", "#F59E0B"), ACC_INK="#0b0b0d", ACC_RGB=(245, 158, 11), SW_KNOB="#fffaf0"),
    "graphite": dict(CARD="#161616", CARD_HI="#1f1f1f", HOVER="#242424", HAIR="#2e2e2e", FG="#fafafa",
                     BODY="#e5e5e5", MUTED="#a3a3a3", FAINT="#8a8a8a", EMBER="#f5f5f5", INK=(255, 255, 255),
                     GLOW_WARM=(255, 255, 255), GLOW_COOL=(160, 160, 160), ORB_GLASS=(30, 30, 30),
                     ACC=("#f5f5f5", "#cfcfcf"), ACC_HI=("#ffffff", "#e5e5e5"), ACC_INK="#111111", ACC_RGB=(229, 229, 229), SW_KNOB="#141414"),
    "midnight": dict(CARD="#0d1424", CARD_HI="#131c30", HOVER="#18223a", HAIR="#23304a", FG="#f1f5f9",
                     BODY="#dbe4f0", MUTED="#94a3b8", FAINT="#7d8aa0", EMBER="#93c5fd", INK=(255, 255, 255),
                     GLOW_WARM=(59, 130, 246), GLOW_COOL=(14, 165, 233), ORB_GLASS=(17, 26, 46),
                     ACC=("#60a5fa", "#3b82f6"), ACC_HI=("#93c5fd", "#60a5fa"), ACC_INK="#0b1220", ACC_RGB=(96, 165, 250), SW_KNOB="#f8fafc"),
    "daylight": dict(CARD="#f9f5ea", CARD_HI="#fffdf7", HOVER="#f1ebdc", HAIR="#e3dccb", FG="#1c1917",
                     BODY="#36302a", MUTED="#5c564f", FAINT="#6b645c", EMBER="#a1520a", INK=(60, 40, 10),
                     GLOW_WARM=(245, 158, 11), GLOW_COOL=(20, 184, 166), ORB_GLASS=(255, 250, 238),
                     ACC=("#F59E0B", "#F97316"), ACC_HI=("#FBBF24", "#F59E0B"), ACC_INK="#1c1917", ACC_RGB=(217, 119, 6), SW_KNOB="#fffdf7"),
}
THEME_FONTS = {  # bundled OFL fonts first (fonts/), Windows fonts as fallback if a file is missing
    "ember": (("VQ Outfit Bold",), ("VQ Outfit Semibold",), ("VQ DM Sans",), ("VQ DM Sans Medium",),
              ("VQ JetBrains Mono",)),
    "graphite": (("VQ Inter Bold", "Segoe UI Variable Display Semib", "Segoe UI Semibold"),
                 ("VQ Inter Semibold", "Segoe UI Variable Display Semib", "Segoe UI Semibold"),
                 ("VQ Inter", "Segoe UI Variable Text", "Segoe UI"),
                 ("VQ Inter Medium", "Segoe UI Variable Text Semibold", "Segoe UI Semibold"),
                 ("VQ JetBrains Mono", "Cascadia Mono", "Consolas")),
    "midnight": (("VQ Space Grotesk Bold", "Bahnschrift SemiBold"), ("VQ Space Grotesk Medium", "Bahnschrift SemiBold"),
                 ("VQ Inter", "Segoe UI Variable Text", "Segoe UI"),
                 ("VQ Inter Medium", "Segoe UI Variable Text Semibold", "Segoe UI Semibold"),
                 ("VQ JetBrains Mono", "Cascadia Mono", "Consolas")),
    "daylight": (("VQ Fraunces Semibold", "Sitka Heading Semibold", "Georgia"),
                 ("VQ Fraunces Medium", "Sitka Subheading Semibold", "Georgia"),
                 ("VQ DM Sans",), ("VQ DM Sans Medium",), ("VQ JetBrains Mono",)),
}
# (headings, body): Inter, Space Grotesk and Fraunces run wider than Outfit/DM Sans at the same size, so a step
# smaller keeps the layout's metrics (no new truncation at Narrow + L)
THEME_FONT_SCALE = {"graphite": (0.95, 0.95), "midnight": (0.95, 0.95), "daylight": (0.94, 1.0)}
THEME_NAMES = {"ember": "Ember", "graphite": "Graphite", "midnight": "Midnight", "daylight": "Daylight"}


def apply_theme(name: str) -> None:
    """Point the colour names at a theme. Everything reads them when it draws, so a rebuild repaints all."""
    globals().update(THEMES.get(name) or THEMES["ember"])
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
    "gear": ("\ue713", "\u2699"), "mini": ("\ue921", "\u2013"),
}
STATUS_TAGS = {"not spoken: headless run": "background", "not spoken: muted": "muted",
               "not spoken: project muted": "project muted", "not spoken: do not disturb": "do not disturb",
               "not spoken: nothing to say": "no update", "not spoken: grouped": "grouped", "not spoken: found waiting": "waiting",
               "skipped": "skipped", "cleared": "cleared", "removed": "removed"}
BACKGROUND = ("not spoken: headless run", "not spoken: grouped", "not spoken: nothing to say")


# ---------------------------------------------------------------- small helpers
def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def mix(a: str, b: str, t: float) -> str:
    t = min(1.0, max(0.0, t))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(_rgb(a), _rgb(b)))


def bezier(x1: float, y1: float, x2: float, y2: float):
    """A CSS cubic-bezier easing as a function of t in [0, 1]."""
    def at(a, b, u):
        return 3 * a * u * (1 - u) ** 2 + 3 * b * u * u * (1 - u) + u ** 3

    def f(t: float) -> float:
        if t <= 0 or t >= 1:
            return min(1.0, max(0.0, t))
        lo, hi = 0.0, 1.0
        for _ in range(22):                           # solve x(u) = t by bisection
            mid = (lo + hi) / 2
            if at(x1, x2, mid) < t:
                lo = mid
            else:
                hi = mid
        return at(y1, y2, (lo + hi) / 2)
    return f


ease_out = bezier(0.23, 1, 0.32, 1)        # enters, presses, anything that answers the user
ease_in_out = bezier(0.77, 0, 0.175, 1)    # things moving on screen


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
        return self.get(("orb", round(level, 3), dim), lambda: self.orb_pil(level, dim))

    def orb_pil(self, level: float, dim: bool = False):
        level = round(level, 2)                       # bounded cache: 101 levels at most
        key = ("orb_pil", level, dim)
        if key in self._cache:
            return self._cache[key]

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
                dr.ellipse((k, k, n - 1 - k, n - 1 - k), fill=(*ORB_GLASS, 255))
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
        self._cache[key] = make()
        return self._cache[key]

    def knob(self):
        col, glow = ACC_HI[0], ACC_RGB

        def make():
            I, D, F = self.I, self.D, self.F
            d, g = self.p(11), self.p(8)
            size = d + 2 * g
            out = I.new("RGBA", (size, size), (0, 0, 0, 0))
            halo = I.new("RGBA", (size, size), (0, 0, 0, 0))
            D.Draw(halo).ellipse((g, g, g + d, g + d), fill=(*glow, 200))
            out.alpha_composite(halo.filter(F.GaussianBlur(g / 2)))
            n = d * self.SS
            ball = I.new("RGBA", (n, n), (0, 0, 0, 0))
            D.Draw(ball).ellipse((0, 0, n - 1, n - 1), fill=self._rgba(col))
            out.alpha_composite(self._down(ball, d, d), (g, g))
            return out
        return self.get(("knob", col), make)

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

    def corners(self, r: int, edge: str, inset: float = 0.0, ring: bool = True):
        """Four corner masks (device px radius r): key colour outside the curve, the rim on it, clear inside.
        The window can only hide pixels by colour key, all or nothing, so the key is binary: a pixel is key
        when under half of it lies inside the curve (inset by `inset` px). A blended key pixel would show as
        a dark fringe. On Windows CornerGlass draws the smooth curve and rim on top, so there is no ring here."""
        def make_all():
            I, D, SS = self.I, self.D, self.SS
            n, m, i = 2 * r * SS, 2 * r, int(round(inset * SS))
            out = I.new("L", (n, n), 255)
            D.Draw(out).ellipse((i, i, n - 1 - i, n - 1 - i), fill=0)
            hole = out.resize((m, m), I.BOX).point(lambda v: 255 if v >= 128 else 0)
            im = I.new("RGBA", (n, n), (0, 0, 0, 0))
            if ring:
                dr = D.Draw(im)
                dr.ellipse((i, i, n - 1 - i, n - 1 - i), fill=self._rgba(edge))
                dr.ellipse((i + SS, i + SS, n - 1 - i - SS, n - 1 - i - SS), fill=(0, 0, 0, 0))
            full = self._down(im, m, m)
            full.paste(I.new("RGBA", (m, m), self._rgba(KEY)), (0, 0), hole)
            return [full.crop(b) for b in ((0, 0, r, r), (r, 0, m, r), (0, r, r, m), (r, r, m, m))]
        key = ("corners", r, edge if ring else None, inset)
        if key not in self._cache:
            self._cache[key] = [self.TK.PhotoImage(c) for c in make_all()]
        return self._cache[key]

    def theme_card(self, w: int, h: int, t: dict, chosen: bool):
        """A theme swatch: its card colour lit by its ambient light, a text line, a ring when chosen."""
        def make():
            I, D, F, SS = self.I, self.D, self.F, self.SS
            n_w, n_h, r = w * SS, h * SS, self.p(10) * SS
            body = I.new("RGBA", (n_w, n_h), self._rgba(t["CARD"]))
            light = I.new("RGBA", (n_w, n_h), (0, 0, 0, 0))
            D.Draw(light).ellipse((n_w * 0.45, -n_h * 0.9, n_w * 1.4, n_h * 0.9), fill=(*t["GLOW_WARM"], 90))
            body.alpha_composite(light.filter(F.GaussianBlur(n_h / 3)))
            D.Draw(body).rounded_rectangle((self.p(10) * SS, self.p(10) * SS, n_w * 0.55, self.p(14) * SS),
                                           radius=self.p(2) * SS, fill=self._rgba(t["MUTED"]))
            mask = I.new("L", (n_w, n_h), 0)
            D.Draw(mask).rounded_rectangle((0, 0, n_w - 1, n_h - 1), radius=r, fill=255)
            im = I.new("RGBA", (n_w, n_h), (0, 0, 0, 0))
            im.paste(body, (0, 0), mask)
            ring = I.new("L", (n_w, n_h), 0)
            k = SS * (2 if chosen else 1)
            D.Draw(ring).rounded_rectangle((0, 0, n_w - 1, n_h - 1), radius=r, fill=255)
            D.Draw(ring).rounded_rectangle((k, k, n_w - 1 - k, n_h - 1 - k), radius=r - k, fill=0)
            im.paste(I.new("RGBA", (n_w, n_h), self._rgba(ACC[0] if chosen else t["HAIR"])), (0, 0), ring)
            return self._down(im, w, h)
        return self.get(("theme_card", w, h, tuple(sorted((k, str(v)) for k, v in t.items())), chosen), make)

    def glow_corners(self, s: int, r: int, step: int, cols: tuple):
        """Four corner images (s x s device px) of the inner glow rings: ring i fills the band between the
        rounded rectangles inset 1 + i*step and 1 + (i+1)*step (radius r minus the inset); None = no ring."""
        key = ("glowc", s, r, step, cols)
        if key not in self._cache:
            old = [k for k in self._cache if isinstance(k, tuple) and k[0] == "glowc"]
            if len(old) > 120:
                for k in old:
                    del self._cache[k]
            I, D, SS = self.I, self.D, self.SS
            n, big = s * SS, 4 * s * SS
            im = I.new("RGBA", (n, n), (0, 0, 0, 0))
            dr = D.Draw(im)
            last = max((i for i, col in enumerate(cols) if col), default=-1)
            for i in range(last + 2):
                ins = (1 + i * step) * SS
                rad = max(0, r * SS - ins)
                fill = self._rgba(cols[i]) if i <= last and cols[i] else (0, 0, 0, 0)
                dr.rounded_rectangle((ins, ins, big, big), radius=rad, fill=fill)
            tl = self._down(im, s, s)
            T = I.Transpose
            self._cache[key] = [self.TK.PhotoImage(x) for x in
                                (tl, tl.transpose(T.FLIP_LEFT_RIGHT), tl.transpose(T.FLIP_TOP_BOTTOM),
                                 tl.transpose(T.ROTATE_180))]
        return self._cache[key]

    def glow(self, w: int, h: int, kind: str):
        """Ambient light inside the card: amber from one side, a faint teal backlight, the Forge hairline on top."""
        def make():
            I, D, F = self.I, self.D, self.F
            im = I.new("RGBA", (w, h), (0, 0, 0, 0))
            lights = ([(w - self.p(70), h + self.p(10), self.p(150), (*GLOW_WARM, 46)),
                       (self.p(20), -self.p(20), self.p(120), (*GLOW_COOL, 16))] if kind == "cap" else
                      [(w - self.p(40), -self.p(50), self.p(190), (*GLOW_WARM, 44)),
                       (self.p(10), self.p(40), self.p(150), (*GLOW_COOL, 12))])
            for x, y, r, col in lights:
                layer = I.new("RGBA", (w, h), (0, 0, 0, 0))
                D.Draw(layer).ellipse((x - r, y - r, x + r, y + r), fill=col)
                im.alpha_composite(layer.filter(F.GaussianBlur(r / 2.2)))
            line = I.new("RGBA", (w, 1), (0, 0, 0, 0))
            px = line.load()
            for x in range(w):
                t = x / max(1, w - 1)
                a = max(0.0, 1 - abs(t - 0.58) / 0.42) ** 1.6
                px[x, 0] = (*GLOW_WARM, int(185 * a))
            im.alpha_composite(line, (0, 0))
            return im
        self._cache[("glow_make", w, h, kind)] = make
        return self.get(("glow", w, h, kind), lambda: self.glow_pil(w, h, kind, make))

    def glow_pil(self, w: int, h: int, kind: str, make=None):
        """The ambient light as a Pillow image (CornerGlass samples it)."""
        key = ("glow_pil", w, h, kind)
        if key not in self._cache:
            if make is None:
                self.glow(w, h, kind)  # makes and caches it on first use
                make = self._cache.get(("glow_make", w, h, kind))
            if key not in self._cache:
                self._cache[key] = make()
        return self._cache[key]


class CornerGlass:
    """Windows only. Four tiny click-through windows with per-pixel alpha, one over each corner of the card.
    Tk hides pixels by colour key only, so its own corners are binary and stepped; these draw the smooth
    outer curve, the rim and a band of card colour that fades out inwards over the stepped edge.
    They are owned by the Tk window, so they stay just above it in the z-order."""
    INSET = 1.0   # px the Tk window's binary curve sits inside the true one (so the glass covers it)
    FADE = 1.0    # px over which the card band fades out, after staying solid to INSET + 0.5 px

    def __init__(self, root, art, count: int = 4):
        import ctypes
        from ctypes import wintypes as W
        self.ct, self.art = ctypes, art
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        self.u, self.g = u, g
        H = ctypes.c_void_p
        u.CreateWindowExW.restype = H
        u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, H, H, H, H]
        u.GetDC.restype, u.GetDC.argtypes = H, [H]
        u.ReleaseDC.argtypes = [H, H]
        u.ShowWindow.argtypes = [H, ctypes.c_int]
        u.DestroyWindow.argtypes = [H]
        u.GetWindowRect.argtypes = [H, ctypes.POINTER(W.RECT)]
        u.IsWindow.argtypes = [H]
        u.UpdateLayeredWindow.argtypes = [H, H, H, H, H, H, W.DWORD, H, W.DWORD]
        g.CreateCompatibleDC.restype, g.CreateCompatibleDC.argtypes = H, [H]
        g.CreateDIBSection.restype = H
        g.CreateDIBSection.argtypes = [H, H, ctypes.c_uint, ctypes.POINTER(H), H, W.DWORD]
        g.SelectObject.restype, g.SelectObject.argtypes = H, [H, H]
        g.DeleteObject.argtypes = [H]
        g.DeleteDC.argtypes = [H]

        class BLEND(ctypes.Structure):
            _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte),
                        ("fmt", ctypes.c_ubyte)]
        self.BLEND, self.POINT, self.SIZE, self.RECT = BLEND, W.POINT, W.SIZE, W.RECT
        root.update_idletasks()
        self.owner = int(root.wm_frame(), 16)
        # LAYERED | TRANSPARENT (click-through) | TOOLWINDOW | NOACTIVATE | TOPMOST
        ex = 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8
        self.hw = [u.CreateWindowExW(ex, "Static", None, 0x80000000, 0, 0, 1, 1, self.owner, None, None, None)
                   for _ in range(count)]
        if not all(self.hw):
            raise OSError("CreateWindowExW failed")
        self.r, self.quads, self.alpha, self.shown = 0, None, 0.0, False
        self._cache: dict = {}

    def _render(self, r: int, edge: str, band, backdrop):
        key = (r, edge, band or backdrop[0])
        if key not in self._cache:
            if len(self._cache) > 300:   # the rim pulse makes a new colour every frame
                self._cache.clear()
            I, D, SS = self.art.I, self.art.D, self.art.SS
            n, m = 2 * r * SS, 2 * r
            # the band is what the card shows there: a flat glow-ring colour, or the card under its ambient light
            col = I.new("RGB", (n, n), _rgb(band)) if band else backdrop[1].resize((n, n), I.NEAREST)
            rim = I.new("L", (n, n), 0)
            D.Draw(rim).ellipse((0, 0, n - 1, n - 1), fill=255)
            D.Draw(rim).ellipse((SS, SS, n - 1 - SS, n - 1 - SS), fill=0)
            col.paste(_rgb(edge), (0, 0, n, n), rim)
            a = I.new("L", (n, n), 0)
            da = D.Draw(a)
            da.ellipse((0, 0, n - 1, n - 1), fill=255)
            steps = 8
            for k in range(1, steps + 1):  # card band: solid to the inset curve, then fading out
                i = int(round((self.INSET + 0.5 + self.FADE * k / steps) * SS))
                if 2 * i < n - 2:
                    da.ellipse((i, i, n - 1 - i, n - 1 - i), fill=round(255 * (1 - k / steps)))
            col.putalpha(a)
            full = col.convert("RGBa").resize((m, m), I.BOX)   # premultiplied, as UpdateLayeredWindow wants
            rr, gg, bb, aa = full.split()
            full = I.merge("RGBa", (bb, gg, rr, aa))           # BGRA byte order
            self._cache[key] = [full.crop(b).tobytes() for b in
                                ((0, 0, r, r), (r, 0, m, r), (0, r, r, m), (r, r, m, m))]
        return self._cache[key]

    def _spots(self):
        rc = self.RECT()
        self.u.GetWindowRect(self.owner, self.ct.byref(rc))
        r = self.r
        return [(rc.left, rc.top), (rc.right - r, rc.top), (rc.left, rc.bottom - r), (rc.right - r, rc.bottom - r)]

    def _push(self, content: bool):
        if not self.r or not self.quads:
            return
        ct, u, g, r = self.ct, self.u, self.g, self.r
        blend = self.BLEND(0, 0, max(0, min(255, round(255 * self.alpha))), 1)  # AC_SRC_ALPHA
        if not content:
            for hw, (x, y) in zip(self.hw, self._spots()):
                u.UpdateLayeredWindow(hw, None, ct.byref(self.POINT(x, y)), None, None, None, 0,
                                      ct.byref(blend), 2)
            return
        self._blit([(hw, x, y, r, r, data) for hw, (x, y), data in zip(self.hw, self._spots(), self.quads)])

    def blit(self, i: int, x: int, y: int, w: int, h: int, data: bytes, alpha: float = 1.0):
        """Show premultiplied BGRA pixels (w x h) in window i at screen x, y."""
        self.alpha = alpha
        self._blit([(self.hw[i], x, y, w, h, data)])

    def _blit(self, items):
        ct, u, g = self.ct, self.u, self.g
        blend = self.BLEND(0, 0, max(0, min(255, round(255 * self.alpha))), 1)  # AC_SRC_ALPHA
        screen = u.GetDC(None)
        mem = g.CreateCompatibleDC(screen)
        try:
            for hw, x, y, w, h, data in items:
                hdr = (ct.c_uint32 * 11)(40, w, (-h) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0, 0)
                bits = ct.c_void_p()
                bmp = g.CreateDIBSection(mem, ct.addressof(hdr), 0, ct.byref(bits), None, 0)
                if not bmp:
                    continue
                ct.memmove(bits, data, len(data))
                old = g.SelectObject(mem, bmp)
                u.UpdateLayeredWindow(hw, screen, ct.byref(self.POINT(x, y)), ct.byref(self.SIZE(w, h)), mem,
                                      ct.byref(self.POINT(0, 0)), 0, ct.byref(blend), 2)
                g.SelectObject(mem, old)
                g.DeleteObject(bmp)
        finally:
            g.DeleteDC(mem)
            u.ReleaseDC(None, screen)

    def shape(self, r: int, edge: str, band, backdrop):
        """band: flat colour just inside the rim (the inner glow while it pulses), or None to use
        backdrop = (key, 2r x 2r RGB image): the card and its ambient light under the four corners."""
        r = int(r or 0)
        if r < 1:
            return
        self.r, self.quads = r, self._render(r, edge, band, backdrop)
        self._push(True)

    def sync(self):
        """Follow the Tk window (moved, resized, faded)."""
        self._push(False)

    def set_alpha(self, a: float):
        self.alpha = float(a)
        self._push(False)

    def show(self):
        if not self.shown:
            self.shown = True
            self._push(True)
            for hw in self.hw:
                self.u.ShowWindow(hw, 4)  # SW_SHOWNOACTIVATE

    def hide(self):
        if self.shown:
            self.shown = False
            for hw in self.hw:
                self.u.ShowWindow(hw, 0)

    def destroy(self):
        for hw in self.hw:
            if self.u.IsWindow(hw):
                self.u.DestroyWindow(hw)
        self.hw = []


class MiniOrb:
    """The minimized player: a glossy ember orb docked at the nearest screen edge. It pulses while speaking,
    shows waiting calls as a small pill, and grows a little on hover. Click restores the player where it was;
    drag moves the orb, and it settles on the nearest edge again.
    On Windows the Tk window only gives the click area (a binary disk); a layered window draws the
    antialiased orb, its shadow, rim and badge on top, so every edge is smooth."""
    BALL = 40    # logical px
    MARGIN = 10  # gap to the screen edge

    def __init__(self, w, cx: int, cy: int, dock: bool = True):
        self.w, tk = w, w.tk
        self.ball = w.p(self.BALL)
        self.size = round(self.ball * 2.1)          # halo, shadow, hover growth and the badge fit inside
        self.cx, self.cy = cx, cy
        self.state = None
        self.hover = 0.0
        self._cache = {}
        self._tip_job = None
        t = self.top = tk.Toplevel(w.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        try:
            t.attributes("-transparentcolor", KEY)
        except tk.TclError:
            pass
        t.configure(bg=KEY)
        c = self.c = tk.Canvas(t, width=self.size, height=self.size, bg=KEY, highlightthickness=0, bd=0)
        c.place(x=0, y=0)
        m = (self.size - self.ball) // 2
        self.disk = c.create_oval(m + 1, m + 1, m + self.ball - 1, m + self.ball - 1, fill=_hex(ORB_GLASS), width=0)
        self.img = c.create_image(0, 0, anchor="nw")
        c.configure(cursor="hand2")
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._move)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Button-3>", w._menu)
        c.bind("<Enter>", lambda e: self._hover(True))
        c.bind("<Leave>", lambda e: self._hover(False))
        self.glass = None
        if sys.platform == "win32":
            try:
                t.update_idletasks()
                self.glass = CornerGlass(t, w.art, count=1)
            except Exception as e:
                w.api.log(f"mini orb glass off: {e!r}")
        self._place()
        t.deiconify()
        if self.glass:
            self.glass.u.ShowWindow(self.glass.hw[0], 4)
        if dock:
            self.dock()

    # ---------------------------------------------------------- where it sits
    def _target(self, cx, cy):
        """The docked spot for a centre point: the nearer side edge of that monitor, height kept."""
        l, t, r, b = self.w._work_area(cx, cy)
        half = self.ball // 2 + self.w.p(self.MARGIN)
        x = l + half if cx < (l + r) / 2 else r - half
        return x, max(t + half, min(b - half, cy))

    def dock(self):
        """Slide to the nearest edge (ease-out, ~240 ms)."""
        x0, y0 = self.cx, self.cy
        x1, y1 = self._target(x0, y0)
        if (x1, y1) == (x0, y0):
            return

        def step(t):
            self.cx, self.cy = round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)
            self._place()
        self.w._anim("minidock", 0.24, step, ease=ease_out)

    def _xy(self):
        return self.cx - self.size // 2, self.cy - self.size // 2

    def _place(self):
        x, y = self._xy()
        self.top.geometry(f"{self.size}x{self.size}+{x}+{y}")
        self._paint()

    # ---------------------------------------------------------- look
    def render(self, k: float, dim: bool, calls: int):
        state = (round(0.0 if dim else k, 2), calls)
        if state != self.state:
            self.state = state
            self._paint()

    def _image(self):
        k, calls = self.state or (0.0, 0)
        h = round(self.hover, 2)
        rim = self.w.cfg.get("rim_color") or AMBER
        key = (k, calls, h, rim)
        if key in self._cache:
            return self._cache[key]
        if len(self._cache) > 160:
            self._cache.clear()
        art, p = self.w.art, self.w.p
        I, D, F, SS = art.I, art.D, art.F, 4
        n = self.size
        out = I.new("RGBA", (n, n), (0, 0, 0, 0))
        d = self.ball * (1 + 0.07 * h)               # grows a little on hover
        c0 = n / 2
        # soft shadow under the orb
        sh = I.new("RGBA", (n, n), (0, 0, 0, 0))
        so = p(2) + p(1) * h
        D.Draw(sh).ellipse((c0 - d / 2 + p(1), c0 - d / 2 + so, c0 + d / 2 - p(1), c0 + d / 2 + so),
                           fill=(0, 0, 0, 110))
        out.alpha_composite(sh.filter(F.GaussianBlur(p(3) + p(1) * h)))
        # the glossy ember orb (with its halo, which breathes with the voice)
        full = art.orb_pil(k, dim=False)
        sc = d / self.w.p(ORB)
        o = full.resize((max(1, round(full.width * sc)), max(1, round(full.height * sc))), I.LANCZOS)
        out.alpha_composite(o, (round(c0 - o.width / 2), round(c0 - o.height / 2)))
        # gloss and a thin rim in the pulse colour, drawn large and scaled down (antialiased)
        m = round(d * SS)
        top = I.new("RGBA", (m, m), (0, 0, 0, 0))
        dr = D.Draw(top)
        dr.ellipse((m * 0.2, m * 0.08, m * 0.62, m * 0.4), fill=(255, 255, 255, 70))
        top = top.filter(F.GaussianBlur(m * 0.05))
        dr = D.Draw(top)
        rr, rg, rb, _ = Art._rgba(rim)
        dr.ellipse((0, 0, m - 1, m - 1), outline=(rr, rg, rb, int(150 + 100 * k)), width=max(SS, round(1.4 * SS)))
        top = top.resize((round(d), round(d)), I.LANCZOS)
        out.alpha_composite(top, (round(c0 - d / 2), round(c0 - d / 2)))
        if calls:
            label = str(calls) if calls < 10 else "9+"
            try:
                from PIL import ImageFont
                font = ImageFont.truetype(str(_HERE / "fonts" / "VQJetBrainsMono.ttf"), round(p(10) * SS))
            except Exception:
                font = None
            bh = p(16) * SS
            tw = D.Draw(I.new("RGBA", (1, 1))).textlength(label, font=font) if font else bh * 0.5
            bw = max(bh, round(tw + p(10) * SS))
            pill = I.new("RGBA", (bw, bh), (0, 0, 0, 0))
            dp = D.Draw(pill)
            dp.rounded_rectangle((0, 0, bw - 1, bh - 1), radius=bh // 2, fill=Art._rgba(OBSIDIAN),
                                 outline=(rr, rg, rb, 230), width=max(SS, round(1.2 * SS)))
            dp.text((bw / 2, bh / 2), label, fill=(255, 255, 255, 255), font=font, anchor="mm")
            pill = pill.resize((round(bw / SS), round(bh / SS)), I.LANCZOS)
            px = round(c0 + d * 0.34 - pill.width / 2)
            py = round(c0 - d * 0.5 - pill.height * 0.15)
            out.alpha_composite(pill, (min(n - pill.width, px), max(0, py)))
        self._cache[key] = out
        return out

    def _paint(self):
        if not self.state:
            self.state = (0.0, 0)
        im = self._image()
        if self.glass:
            pm = im.convert("RGBa")
            rr, gg, bb, aa = pm.split()
            x, y = self._xy()
            self.glass.blit(0, x, y, self.size, self.size, self.w.art.I.merge("RGBa", (bb, gg, rr, aa)).tobytes(),
                            alpha=1.0)
            self.c.itemconfigure(self.img, state="hidden")
        else:
            self._photo = self.w.art.TK.PhotoImage(im)
            self.c.itemconfigure(self.img, image=self._photo)

    # ---------------------------------------------------------- hover, click, drag
    def _hover(self, on: bool):
        h0, h1 = self.hover, (1.0 if on else 0.0)

        def step(t):
            self.hover = h0 + (h1 - h0) * t
            self._paint()
        self.w._anim("minihover", 0.16 if on else 0.12, step, ease=ease_out)
        if self._tip_job:
            self.w.root.after_cancel(self._tip_job)
            self._tip_job = None
        self.w._tip_hide()
        if on:
            calls = (self.state or (0, 0))[1]
            text = "Ember Voice Player" + (f" · {calls} call{'s' if calls != 1 else ''}" if calls else "")
            first = (self.w._calls or [None])[0] if calls else None
            if first:  # which thread is first in line
                text += "\n" + self.w._fit(self.w._where(first, "Next: {}"), self.w.F["meta"], self.w.p(300))
            self._tip_job = self.w.root.after(450, lambda: (setattr(self, "_tip_job", None), self.w._tip_show(text)))

    def _press(self, e):
        self._down = (e.x_root, e.y_root, self.cx, self.cy)
        self._moved = False
        self.w._anims.pop("minidock", None)

    def _move(self, e):
        x0, y0, cx, cy = self._down
        if abs(e.x_root - x0) + abs(e.y_root - y0) > 4:
            self._moved = True
            self.w._tip_hide()
        if self._moved:
            self.cx, self.cy = cx + e.x_root - x0, cy + e.y_root - y0
            self._place()

    def _release(self, e):
        if getattr(self, "_moved", False):
            self.dock()
        else:
            self.w.restore()

    def destroy(self):
        if self._tip_job:
            self.w.root.after_cancel(self._tip_job)
        self.w._anims.pop("minidock", None)
        self.w._anims.pop("minihover", None)
        self.w._tip_hide()
        if self.glass:
            self.glass.destroy()
        try:
            self.top.destroy()
        except Exception:
            pass


class MemSettings:
    """Stand-in when the queue has no settings store (tests, harness)."""
    def __init__(self):
        self.version = 0
        self._v = {"text_size": 1.0, "opacity": 1.0, "voice": "af_heart", "speed": 1.0, "summaries": True,
                   "say_source": True, "speak_background": False, "auto_open_calls": True,
                   "hide_when_idle": False, "calls_hours": 72, "desktop_answers": "send",
                   "pulse_with_voice": True, "rim_color": "#F59E0B", "glow_size": 12,
                   "player_width": 1.0, "theme": "ember", "minimized": False}

    def get(self, k):
        return self._v[k]

    def update(self, **kw):
        self._v.update(kw)
        self.version += 1
        return kw

    def reset(self):
        v = self.version
        self.__init__()
        self.version = v + 1


RIM_NAMES = {"#F59E0B": "Promethean Amber", "#EA580C": "Cooled Ember", "#FBBF24": "Ember Light",
             "#F43F5E": "Rose", "#8B5CF6": "Violet", "#3B82F6": "Blue", "#14B8A6": "Teal", "#E5E7EB": "White"}
WIDTH_NAMES = {0.85: "Narrow", 1.0: "Default", 1.2: "Wide", 1.4: "Wider"}
SIZE_NAMES = {0.85: "Small", 1.0: "Default", 1.15: "Large", 1.3: "Larger", 1.5: "Largest"}
VOICE_NAMES = {"af_heart": "Heart", "af_bella": "Bella", "af_nicole": "Nicole", "af_sarah": "Sarah",
               "am_michael": "Michael", "am_adam": "Adam", "bf_emma": "Emma \u00b7 UK", "bm_george": "George \u00b7 UK"}


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
        root.title("Ember Voice Player")
        try:
            root.iconbitmap(default=str(Path(__file__).with_name("assets") / "ember-voice.ico"))
        except Exception:
            pass
        self.cfg = getattr(vq, "settings", None) or MemSettings()
        self.S0 = max(1.0, root.winfo_fpixels("1i") / 96)
        self._fams = set(tkfont.families(root))
        self._tkfont = tkfont
        self._scale_setup()

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
        self._drafts: dict[str, dict] = {}  # call id -> answers so far, sent together once all are in
        self._typed: list[tuple] = []        # results from typing answers into the Claude app (worker thread)
        self._thumbs: dict = {}              # thumbnails for call context, by (path, w, h)
        self._call_ix = 0
        self._auto_open = None      # call id that opened the panel by itself
        self._calls: list[dict] = []
        self._cur = None
        self._q: list[dict] = []
        self._paused = False
        self._shown = None
        self._prog = (0.0, 0.0, 0.0, False)  # pos, dur, wall clock, running
        self._env_k = 0.0                       # smoothed voice loudness for the pulse
        self._rings: list[list[int]] = []       # inner glow rings: four straight lines each
        self._ring_corner: list[int] = []       # and four antialiased corner images
        self._ring_k = 0.0
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
        self._slides, self._last_key = {}, 0.0
        self._tip = None
        self._tip_job = None
        self._visible = False
        self._badge_left = self.Wp
        self._cap_hover = False
        self._cap_track_end = self.Wp
        self._bar_y = 0
        self.view = "now"           # now | settings (panel)
        self._idle_since = None
        self._sliders: dict = {}
        self._glass = None                  # CornerGlass (Windows), made on first show
        self._mini = None                   # MiniOrb while minimized
        self._use_glass = sys.platform == "win32"
        root.bind("<Configure>", lambda e: e.widget is root and self._glass and self._glass.sync(), add="+")

        self._build()
        self._place()
        self._apply_mode(instant=True)
        self._tick()

    def _look(self):
        """The settings that need a full redraw when they change."""
        return (self.cfg.get("text_size"), self.cfg.get("player_width"), self.cfg.get("theme"))

    def _scale_setup(self):
        """Sizes and fonts for the current text size setting (all of the player scales with it)."""
        self._built_size = self._look()
        apply_theme(self.cfg.get("theme"))
        self.S = self.S0 * float(self.cfg.get("text_size"))
        self.Wp = self.p(W * float(self.cfg.get("player_width") or 1.0))
        self.art = Art(self.S)
        fams, tkfont, root = self._fams, self._tkfont, self.root

        def fam(*names):
            return next((n for n in names if n in fams), "TkDefaultFont")
        tf = THEME_FONTS.get(self.cfg.get("theme")) or THEME_FONTS["ember"]
        outfit_b = fam(*tf[0], "VQ Outfit Bold", "Segoe UI Semibold", "DejaVu Sans")
        outfit = fam(*tf[1], "VQ Outfit Semibold", "Segoe UI Semibold", "DejaVu Sans")
        dm = fam(*tf[2], "VQ DM Sans", "Segoe UI", "DejaVu Sans")
        dm_m = fam(*tf[3], "VQ DM Sans Medium", "Segoe UI Semibold", "DejaVu Sans")
        mono = fam(*tf[4], "VQ JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono")
        icon = next((n for n in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if n in fams), None)
        self._g = 0 if icon else 1

        fh, fb = THEME_FONT_SCALE.get(self.cfg.get("theme"), (1.0, 1.0))

        def font(family, px, bold=False):
            k = 1.0 if family == mono else fh if family in (outfit_b, outfit) else fb
            return tkfont.Font(root=root, family=family, size=-self.p(px * k), weight="bold" if bold else "normal")
        bold_fallback = outfit_b in ("Segoe UI Semibold", "DejaVu Sans")
        self.F = {
            "title": font(outfit_b, 17, bold_fallback), "name": font(outfit, 14.5),
            "q": font(outfit, 15), "body": font(dm, 14), "line": font(dm, 12.5), "meta": font(dm, 11.5),
            "chip": font(dm_m, 13), "row": font(dm_m, 13), "snip": font(dm, 12), "mono": font(mono, 10),
            "mono_s": font(mono, 9), "icon": font(icon or dm, 12 if icon else 13), "icon_s": font(icon or dm, 10),
            "icon_orb": font(icon or dm, 14 if icon else 15), "link": font(dm_m, 12), "entry": font(dm, 13),
            "label": font(dm, 13),
        }

    def _rebuild(self):
        """Redraw everything at a new size (text size setting). Keeps position (right edge), mode and state."""
        self._tip_hide()
        if self._frame_job:
            self.root.after_cancel(self._frame_job)
            self._frame_job = None
        self._anims.clear()
        old_w = self.Wp
        self.c.destroy()
        self._scale_setup()
        x, y = self._xy
        self._xy = (x + old_w - self.Wp, y)
        self._texts, self._fills, self.chips = {}, {}, []
        self._rings, self._ring_corner = [], []
        self._layout_key, self._radius, self._edge, self._level = None, None, None, -1
        self.h = self._target_h = self.p(CAP_H)
        self._build()
        self._apply_mode(instant=True)
        if not self._on_screen():
            self._reset_position()
        if self._mini:
            cx, cy = self._mini.cx, self._mini.cy
            self._mini.destroy()
            self._mini = MiniOrb(self, cx, cy, dock=False)
        if self._glass:
            self._glass._cache.clear()
            self._glass_key = None
            self._glass_shape()

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

    def _tint(self, item, color: str) -> None:
        """Ease a hover colour change (120 ms, ease-out). A new target starts from wherever the colour is now."""
        self._fills[item] = color
        try:
            start = self.c.itemcget(item, "fill")
        except self.tk.TclError:
            return
        if start == color or not (start.startswith("#") and len(start) == 7 and color.startswith("#")):
            self.c.itemconfigure(item, fill=color)
            return

        def step(t):
            try:
                self.c.itemconfigure(item, fill=mix(start, color, t))
            except self.tk.TclError:
                pass
        self._anim(f"tint:{item}", 0.12, step)

    _GENERIC_TAGS = {"pan", "ask", "set", "play", "cap", "current", "bg", "sethit", "askhit", "drag", "lib"}

    def _press_fx(self, e):
        """Press feedback without scaling: the pressed control dips 1 px and its text dims a step."""
        c = self.c
        if c.cget("cursor") != "hand2":
            return
        cur = c.find_withtag("current")
        if not cur:
            return
        own = [t for t in c.gettags(cur[0]) if t not in self._GENERIC_TAGS]
        items = c.find_withtag(own[-1]) if own else cur
        self._pressed = {}
        for i in items:
            if c.itemcget(i, "state") == "hidden" or c.type(i) not in ("text", "image"):
                continue
            xy = c.coords(i)
            if len(xy) != 2:
                continue
            fill = c.itemcget(i, "fill") if c.type(i) == "text" else ""
            dim = mix(fill, CARD, 0.22) if fill.startswith("#") and len(fill) == 7 else ""
            self._pressed[i] = (xy[0], xy[1], fill, dim)
            c.coords(i, xy[0], xy[1] + 1)
            if dim:
                c.itemconfigure(i, fill=dim)

    def _release_fx(self, e):
        c, pressed = self.c, getattr(self, "_pressed", None) or {}
        self._pressed = {}
        for i, (x, y, fill, dim) in pressed.items():
            try:
                xy = c.coords(i)
            except self.tk.TclError:
                continue
            if len(xy) == 2 and abs(xy[1] - (y + 1)) < 0.01:   # not moved by a new layout meanwhile
                self._anim(f"press:{i}", 0.1, lambda t, i=i, x=x, y=y: c.coords(i, x, y + 1 - t))
            if dim and c.itemcget(i, "fill") == dim:
                self._tint(i, self._fills.get(i) or fill)

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
        cut = text[:lo]
        b = max(cut.rfind(ch) for ch in " -_/")       # end at a word boundary when one is near
        if b >= lo * 0.4:
            cut = cut[:b]
        return cut.rstrip(" ·:,;-_/") + "…"

    def _fit(self, opts, font, maxw: int) -> str:
        """The first of several phrasings that fits, else the last one cut at a word."""
        opts = opts if isinstance(opts, tuple) else (opts,)
        for o in opts:
            if font.measure(o) <= maxw:
                return o
        return self._ellipsize(opts[-1], font, maxw)

    @staticmethod
    def _calls_names(n: int):
        s = "s" if n > 1 else ""
        return (f"{n} call{s} waiting on you", f"{n} call{s} waiting", f"{n} call{s}")

    @staticmethod
    def _thread(item) -> str:
        """The thread name for a row (re-read at display time, cached), unless it just repeats the project."""
        if not item:
            return ""
        try:
            t = thread_title(item.get("session_id") or "", item.get("cwd") or "")
        except Exception:
            t = ""
        t = t or item.get("thread") or ""
        proj = (item.get("project") or "").lower()
        return "" if not t or t.lower() == proj or t.lower() == proj.rsplit(".", 1)[0] else t

    def _where(self, item, fmt="{}") -> tuple:
        """"project · thread" phrasings, longest first: the thread is cut at a word (with an ellipsis) before
        the project is touched, and the last phrasing is the project alone. For _fit."""
        proj = (item or {}).get("project") or "?"
        thread = self._thread(item)
        if not thread:
            return (fmt.format(proj),)
        words, opts = thread.split(), []
        for n in range(len(words), 0, -1):
            t = " ".join(words[:n]) + ("" if n == len(words) else "…")
            opts.append(fmt.format(f"{proj} · {t}"))
        return tuple(opts) + (fmt.format(proj),)

    def _last_lines(self, last):
        if not last:
            return ("Waiting for the next reply", "Waiting")
        t = when(last[0]["ts"])
        full, bare = self._where(last[0], "Last: {} · " + t), self._where(last[0], "{} · " + t)
        # thread phrasings with "Last:", then without it, then the project alone
        return full[:-1] + bare[:-1] + full[-1:] + bare[-1:] + (f"Last: {t}",)

    def _reduced(self) -> bool:
        if self.cfg.get("reduce_motion"):
            return True
        if getattr(self, "_win_anim", None) is None:
            self._win_anim = True
            if sys.platform == "win32":
                try:
                    import ctypes
                    v = ctypes.c_int(1)
                    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0)  # client-area animation
                    self._win_anim = bool(v.value)
                except Exception:
                    pass
        return not self._win_anim

    def _anim(self, name, dur, fn, ease=ease_out, done=None, delay=0.0):
        if self._reduced() and name not in ("alpha", "reveal"):  # reduced motion: fades stay, movement jumps
            fn(1.0)
            self._anims.pop(name, None)
            if done:
                done()
            return
        self._anims[name] = {"t0": time.perf_counter() + delay, "dur": max(0.001, dur), "fn": fn, "ease": ease,
                             "done": done}
        self._kick()

    def _glide(self, tag, dx, dy, dur=0.2, delay=0.0):
        """Offset `tag` by (dx, dy) and glide it back to where the layout put it (ease-out). A new slide of the
        same group first snaps the old one home, so nothing drifts. Skipped right after a key press."""
        if time.time() - self._last_key < 0.4 or not self._visible:
            return
        name = f"slide:{tag}"
        left = self._slides.pop(name, None)
        if left:
            self.c.move(tag, -left[0], -left[1])
            self._anims.pop(name, None)
        self.c.move(tag, dx, dy)
        self._slides[name] = [dx, dy]

        def step(t):
            cur = self._slides.get(name)
            if not cur:
                return
            tx, ty = dx * (1 - t), dy * (1 - t)
            self.c.move(tag, tx - cur[0], ty - cur[1])
            cur[0], cur[1] = tx, ty
        self._anim(name, dur, step, ease=ease_out, delay=delay,
                   done=lambda: self._slides.pop(name, None))

    def _end_slides(self):
        """A fresh layout put everything in place: forget glides that are still running. Header lines are not
        laid out again (they stay where they were created), so their glides snap home instead: forgetting them
        left each toast's leftover offset behind, and the subtitle drifted down over the call row."""
        fixed = {f"slide:{self.cap_line}", f"slide:{self.pan_meta}"}
        for name in list(self._slides):
            self._anims.pop(name, None)
            left = self._slides.pop(name)
            if name in fixed:
                self.c.move(name.split(":", 1)[1], -left[0], -left[1])

    def _stagger_chips(self):
        p = self.p
        for n in range(len(self.chips)):
            self._glide(f"chip{n}", 0, p(5), dur=0.22, delay=0.025 * n)

    def _kick(self):
        if self._frame_job is None:
            self._frame_job = self.root.after(0, self._frame)

    def _frame(self):
        self._frame_job = None
        now = time.perf_counter()
        for name, a in list(self._anims.items()):
            t = (now - a["t0"]) / a["dur"]
            if t < 0:                                  # staggered: not started yet
                continue
            t = min(1.0, t)
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
        c.bind("<Enter>", lambda e: self._set_cap_hover(True), add="+")
        c.bind("<Leave>", lambda e: self._set_cap_hover(False), add="+")
        self._cap_hover = False
        self._set_off, self._set_max, self._set_bar_on, self._bar_job = 0, 0, False, None
        c.bind("<MouseWheel>", self._on_wheel, add="+")
        c.bind("<ButtonPress-1>", self._press_fx, add="+")
        c.bind("<ButtonRelease-1>", self._release_fx, add="+")
        Wp = self.Wp

        # card: fill, rim, ambient light, corners on top (disabled: clicks fall through)
        self.card = c.create_rectangle(0, 0, Wp, self.h, fill=CARD, width=0, tags=("bg",))
        self.rim_top = c.create_line(0, 0, Wp, 0, fill=HAIR, tags=("bg",))
        self.glow_cap = c.create_image(0, 0, anchor="nw", image=self.art.glow(Wp, p(CAP_H), "cap"),
                                       tags=("bg", "glowcap"), state="disabled")
        self.glow_pan = c.create_image(0, 0, anchor="nw", image=self.art.glow(Wp, p(240), "pan"),
                                       tags=("bg", "glowpan"), state="hidden")
        self.rim_l = c.create_line(0, 0, 0, self.h, fill=HAIR, tags=("bg",))
        self.rim_r = c.create_line(Wp - 1, 0, Wp - 1, self.h, fill=HAIR, tags=("bg",))
        self.rim_b = c.create_line(0, self.h - 1, Wp, self.h - 1, fill=HAIR, tags=("bg",))
        for ev, fn in (("<ButtonPress-1>", self._drag_start), ("<B1-Motion>", self._drag), ("<Button-3>", self._menu),
                       ("<Double-Button-1>", self._double)):
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
        self.cap_fill = c.create_line(p(66), p(55), p(66), p(55), fill=ACC[0], width=max(1, p(2)), tags=("cap",))
        c.tag_bind("capname", "<Button-1>", lambda e: self._open(self._shown or self._call()))
        c.tag_bind("capname", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(self.cap_name, EMBER)))
        c.tag_bind("capname", "<Leave>", lambda e: (c.configure(cursor=""), self._tint(self.cap_name, FG)))
        self._tipify("capname", lambda: "Open this session")
        self.badge = c.create_image(0, p(32), anchor="e", tags=("cap", "badge"), state="hidden")
        self.badge_t = c.create_text(0, p(32), text="", font=self.F["mono_s"], fill=ACC_INK,
                                     tags=("cap", "badge"), state="hidden")
        c.tag_bind("badge", "<Button-1>", lambda e: self._set_mode("panel"))
        self._tipify("badge", lambda: "Open the panel")

        # icon buttons (both sizes)
        self.btn = {}
        for name, glyph, cmd, tip in (
                ("close", "close", self.hide_until_next, "Hide until the next voiceover (Esc)"),
                ("size", "down", self._toggle_mode, lambda: "Smaller" if self.mode == "panel" else "Open the panel"),
                ("mini", "mini", self.minimize, "Minimize to an orb"),
                ("mute", "volume", self.toggle_mute, lambda: "Unmute" if self.vq.muted else
                 "Mute: new replies go to history only (M)"),
                ("gear", "gear", self._toggle_settings, lambda: "Back" if self.view == "settings" else "Settings")):
            self.btn[name] = self._icon_button(name, glyph, cmd, tip)

        # ---- panel
        self.pan_name = c.create_text(p(78), p(31), anchor="w", text="", font=self.F["title"], fill=FG,
                                      tags=("pan", "panname"))
        self.pan_arrow = c.create_text(0, p(31), anchor="w", text="↗", font=self.F["name"], fill=EMBER,
                                       tags=("pan", "panname"))
        self.pan_meta = c.create_text(p(78), p(53), anchor="w", text="", font=self.F["meta"], fill=MUTED,
                                      tags=("pan", "drag"))
        c.tag_bind("panname", "<Button-1>", lambda e: self._open(self._shown or self._call()))
        c.tag_bind("panname", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(self.pan_name, EMBER)))
        c.tag_bind("panname", "<Leave>", lambda e: (c.configure(cursor=""), self._tint(self.pan_name, FG)))
        self._tipify("panname", lambda: "Open this session")
        self.body = c.create_text(p(PAD), 0, anchor="nw", text="", font=self.F["body"], fill=BODY,
                                  width=self.Wp - 2 * p(PAD), tags=("pan", "play", "drag"))
        self.bar_hit = c.create_rectangle(p(PAD) - p(4), 0, Wp - p(PAD) + p(4), 0, fill=CARD, width=0,
                                          tags=("pan", "play", "bar"))
        c.tag_lower(self.bar_hit, self.glow_cap)
        self.bar_track = c.create_line(p(PAD), 0, Wp - p(PAD), 0, fill=HAIR, width=max(1, p(3)), capstyle="round",
                                       tags=("pan", "play", "bar"))
        self.bar_fill = c.create_line(p(PAD), 0, p(PAD), 0, fill=ACC[0], width=max(1, p(3)), capstyle="round",
                                      tags=("pan", "play", "bar"))
        self.bar_tip = c.create_line(p(PAD), 0, p(PAD), 0, fill=EMBER, width=max(1, p(3)), capstyle="round",
                                     tags=("pan", "play", "bar"))
        self.knob = c.create_image(p(PAD), 0, image=self.art.knob(), tags=("pan", "play", "bar"))
        c.tag_bind("bar", "<ButtonPress-1>", self._scrub_move)
        c.tag_bind("bar", "<B1-Motion>", self._scrub_move)
        c.tag_bind("bar", "<ButtonRelease-1>", self._scrub_end)
        c.tag_bind("bar", "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind("bar", "<Leave>", lambda e: c.configure(cursor=""))
        self.skip_bg = c.create_image(0, 0, image=self.art.circle(34, outline=(*INK, 36)),
                                      tags=("pan", "play", "skip"))
        self.skip_g = c.create_text(0, 0, text=self.glyph("next"), font=self.F["icon"], fill=FG,
                                    tags=("pan", "play", "skip"))
        c.tag_bind("skip", "<Button-1>", lambda e: self.vq.skip())
        c.tag_bind("skip", "<Enter>", lambda e: (c.itemconfigure(self.skip_bg, image=self.art.circle(
            34, fill=(*INK, 16), outline=(*INK, 60))), c.configure(cursor="hand2")))
        c.tag_bind("skip", "<Leave>", lambda e: (c.itemconfigure(self.skip_bg, image=self.art.circle(
            34, outline=(*INK, 36))), c.configure(cursor="")))
        self._tipify("skip", lambda: "Skip to the next one (Right arrow)")
        self.times = c.create_text(0, 0, anchor="w", text="", font=self.F["mono"], fill=FAINT, tags=("pan", "play"))
        self.next_t = c.create_text(0, 0, anchor="e", text="", font=self.F["meta"], fill=MUTED, tags=("pan", "play"))

        # calls waiting on you
        self.call_rule = c.create_line(0, 0, 0, 0, fill=ACC[0], tags=("pan", "ask"))
        self.call_eyebrow = c.create_text(0, 0, anchor="w", text="YOUR CALL", font=self.F["mono"], fill=EMBER,
                                          tags=("pan", "ask"))
        self.call_from = c.create_text(0, 0, anchor="w", text="", font=self.F["mono"], fill=MUTED,
                                       tags=("pan", "ask", "callfrom"))
        c.tag_bind("callfrom", "<Button-1>", lambda e: self._open(self._call()))
        c.tag_bind("callfrom", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(self.call_from, FG)))
        c.tag_bind("callfrom", "<Leave>", lambda e: (c.configure(cursor=""), self._tint(self.call_from, MUTED)))
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
        c.tag_bind("dismiss", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(self.call_dismiss, FG)))
        c.tag_bind("dismiss", "<Leave>", lambda e: (c.configure(cursor=""), self._tint(self.call_dismiss, FAINT)))
        self.question = c.create_text(p(PAD), 0, anchor="nw", text="", font=self.F["q"], fill=FG,
                                      width=self.Wp - 2 * p(PAD), tags=("pan", "ask", "drag"))
        self.chips: list[tuple] = []
        self.entry_bg = c.create_image(p(PAD), 0, anchor="nw", tags=("pan", "ask"))
        self.entry = tk.Entry(c, bg=CARD_HI, fg=FAINT, insertbackground=FG, relief="flat", bd=0,
                              highlightthickness=0, font=self.F["entry"])
        self.entry_win = c.create_window(p(PAD) + p(16), 0, anchor="w", window=self.entry,
                                         width=self.Wp - 2 * p(PAD) - p(16) - p(48), tags=("pan", "ask"))
        self.ctx_text = tk.Text(c, bg=CARD_HI, fg=BODY, relief="flat", bd=0, highlightthickness=0, wrap="word",
                                font=self.F["snip"], padx=0, pady=0, cursor="arrow", insertwidth=0,
                                selectbackground=BRONZE, spacing1=0, spacing3=self.p(4))
        self.ctx_win = c.create_window(0, 0, anchor="nw", window=self.ctx_text, tags=("pan", "ask"))
        self._ctx_shown = False
        self._hint = "Another option, or ask a follow-up"
        self.entry.insert(0, self._hint)
        self.entry.bind("<FocusIn>", self._entry_focus)
        self.entry.bind("<FocusOut>", self._entry_blur)
        self.entry.bind("<Return>", lambda e: self._send_typed())
        self.entry.bind("<Escape>", lambda e: (self.root.focus_set(), "break")[1])
        self.send_bg = c.create_image(0, 0, image=self.art.circle(32, fill=Art._rgba(ACC[0])), tags=("pan", "ask", "send"))
        self.send_g = c.create_text(0, 0, text=self.glyph("send"), font=self.F["icon_s"], fill=ACC_INK,
                                    tags=("pan", "ask", "send"))
        c.tag_bind("send", "<Button-1>", lambda e: self._send_typed())
        c.tag_bind("send", "<Enter>", lambda e: (c.itemconfigure(self.send_bg, image=self.art.circle(
            32, fill=Art._rgba(ACC_HI[0]))), c.configure(cursor="hand2")))
        c.tag_bind("send", "<Leave>", lambda e: (c.itemconfigure(self.send_bg, image=self.art.circle(
            32, fill=Art._rgba(ACC[0]))), c.configure(cursor="")))
        self._tipify("send", lambda: "Send to the session (Enter)")

        # library toggle + list
        self.lib_t = c.create_text(p(PAD), 0, anchor="w", text="QUEUE & HISTORY", font=self.F["mono"], fill=MUTED,
                                   tags=("pan", "libtoggle"))
        self.lib_g = c.create_text(0, 0, anchor="w", text=self.glyph("down"), font=self.F["icon_s"], fill=MUTED,
                                   tags=("pan", "libtoggle"))
        self.lib_count = c.create_text(self.Wp - p(PAD), 0, anchor="e", text="", font=self.F["mono"], fill=FAINT,
                                       tags=("pan", "libtoggle"))
        c.tag_bind("libtoggle", "<Button-1>", lambda e: self._toggle_lib())
        c.tag_bind("libtoggle", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(self.lib_t, FG),
                                                      self._tint(self.lib_g, FG)))
        c.tag_bind("libtoggle", "<Leave>", lambda e: (c.configure(cursor=""), self._tint(self.lib_t, MUTED),
                                                      self._tint(self.lib_g, MUTED)))
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
        root.bind("<Return>", lambda e: self._key(self._enter))
        root.bind("<s>", lambda e: self._key(self._skip_question))
        for i in range(1, 10):
            root.bind(str(i), lambda e, i=i: self._key(lambda: self._pick_number(i)))

    def _key(self, fn):
        if self.root.focus_get() is self.entry:
            return
        self._last_key = time.time()                  # keyboard actions never animate
        fn()

    def _icon_button(self, name, glyph, cmd, tip):
        c, tag = self.c, f"btn_{name}"
        bg = c.create_image(0, 0, image=self.art.circle(30, fill=(*INK, 16)), state="hidden", tags=(tag,))
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
        if name == "mute" and self.vq.muted or name == "gear" and self.view == "settings":
            return EMBER
        return MUTED

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
        tk.Label(t, text=text, bg=CARD_HI, fg=BODY, font=self.F["meta"], padx=self.p(8), pady=self.p(4)).pack(
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
        self._press_at, self._dragged = (e.x_root, e.y_root), False

    def _double(self, e):
        """Double-click on empty background opens or collapses the player. Not on controls (hand cursor),
        and not when the first click turned into a drag."""
        if getattr(self, "_dragged", False) or self.c.cget("cursor") == "hand2" or self.view == "settings":
            return
        self._toggle_mode()

    def _drag(self, e):
        px, py = getattr(self, "_press_at", (e.x_root, e.y_root))
        if abs(e.x_root - px) + abs(e.y_root - py) > self.p(4):
            self._dragged = True
        x, y = e.x_root - self._dx, e.y_root - self._dy
        self._xy = (x, y)
        self.root.geometry(f"+{x}+{y}")

    def _glow_rings(self, k: float, recolor=True):
        """Inner glow: rings inside the rim in the pulse colour, fading inwards. Straight parts are Tk lines
        (not jagged); the four corners are antialiased images that end exactly where the lines start."""
        c, p = self.c, self.p
        size = p(int(self.cfg.get("glow_size") or 0))
        step = max(1, p(2))
        n = size // step if size else 0
        while len(self._rings) < n:
            self._rings.append([c.create_line(0, 0, 0, 0, width=step, fill=CARD, state="hidden", tags=("bg", "ring"))
                                for _ in range(4)])
        while len(self._rings) > n:
            for ln in self._rings.pop():
                c.delete(ln)
        if not self._ring_corner:
            self._ring_corner = [c.create_image(0, 0, anchor="nw", state="hidden", tags=("bg", "ring"))
                                 for _ in range(4)]
        self._ring_k = k
        if not n or k <= 0.01:
            c.itemconfigure("ring", state="hidden")
            return
        rim = self.cfg.get("rim_color") or AMBER
        r, w, h = self._radius or 0, self.Wp, self.h
        if recolor or not getattr(self, "_ring_cols", None):
            # a faint ring would be darker than the card's ambient light behind it: leave it out
            self._ring_cols = [mix(CARD, rim, a) if a >= 0.09 else None
                               for a in (k * 0.8 * (1 - i / n) ** 1.7 for i in range(n))]
        cols = self._ring_cols
        s = min(max(r, 1 + n * step), w // 2, h // 2)  # corner square: the whole curve of every ring
        for i, lines in enumerate(self._rings):
            y = 1 + i * step + step / 2
            for ln, xy in zip(lines, ((s, y, w - s, y), (s, h - y, w - s, h - y),     # mirror of the top/left band
                                      (y, s, y, h - s), (w - y, s, w - y, h - s))):
                c.coords(ln, *xy)
                if cols[i]:
                    c.itemconfigure(ln, fill=cols[i], state="disabled")
                else:
                    c.itemconfigure(ln, state="hidden")
        imgs = self.art.glow_corners(s, r, step, tuple(cols))
        for item, img, xy in zip(self._ring_corner, imgs, ((0, 0), (w - s, 0), (0, h - s), (w - s, h - s))):
            c.coords(item, *xy)
            c.itemconfigure(item, image=img, state="disabled")
        c.tag_raise("ring", self.glow_pan)

    def _set_height(self, h: int):
        h = max(self.p(CAP_H), int(h))
        self.h = h
        if getattr(self, "_rings", None) and self._ring_k > 0.01:
            self._glow_rings(self._ring_k, recolor=False)
        c, Wp = self.c, self.Wp
        if h > int(c.cget("height")):                # the canvas must cover the whole window
            c.configure(height=h + self.p(200))
        c.coords(self.card, 0, 0, Wp, h)
        c.coords(self.rim_l, 0, 0, 0, h)
        c.coords(self.rim_r, Wp - 1, 0, Wp - 1, h)
        c.coords(self.rim_b, 0, h - 1, Wp, h - 1)
        r = self._radius or 0
        for i, (x, y) in enumerate(((0, 0), (Wp - r, 0), (0, h - r), (Wp - r, h - r))):
            c.coords(self.corner[i], x, y)
        x, y = getattr(self, "_xy", (self.root.winfo_x(), self.root.winfo_y()))
        self.root.geometry(f"{Wp}x{h}+{x}+{y}")
        self._glass_shape()  # the bottom corners move over the ambient light

    def _set_shape(self, radius: int, edge: str):
        if (radius, edge) == (self._radius, self._edge):
            return
        self._radius, self._edge = radius, edge
        masks = (self.art.corners(radius, edge, CornerGlass.INSET, ring=False) if self._use_glass
                 else self.art.corners(radius, edge))
        for item, img in zip(self.corner, masks):
            self.c.itemconfigure(item, image=img)
        self._glass_shape()
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
        self._anim("height", 0.24 if target > start else 0.17,
                   lambda t: self._set_height(start + (target - start) * t), ease=ease_out)

    def _fade_window(self, to: float, done=None, dur=0.18):
        try:
            start = float(self.root.attributes("-alpha"))
        except (self.tk.TclError, ValueError):
            start = 1.0

        def step(t):
            try:
                self._set_alpha(start + (to - start) * t)
            except self.tk.TclError:
                pass
        self._anim("alpha", dur, step, ease=ease_out, done=done)

    def show(self):
        self.hidden_until, self.hidden_for_next = 0.0, None
        if self.cfg.get("minimized"):
            if not self._mini:
                self.minimize()
            return
        if self._visible:
            return
        self._visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self._glass_show()
        x, y = self._xy
        self.root.geometry(f"+{x}+{y - self.p(10)}")
        self._anim("slide", 0.28, lambda t: self.root.geometry(f"+{x}+{round(y - self.p(10) * (1 - t))}"))
        self._fade_window(self._alpha())
        self._level = -1

    def _band(self):
        """Colour of the outermost inner-glow ring when it shows (CornerGlass's band must match it)."""
        a = (self._ring_k if self._rings else 0.0) * 0.8
        return mix(CARD, self.cfg.get("rim_color") or AMBER, a) if a >= 0.09 else None

    def _backdrop(self):
        """(key, image): the card and its ambient light under the four corners, quadrants of a 2r square."""
        r, Wp, h, p = self._radius, self.Wp, self.h, self.p
        cap = self.mode == "cap"
        gw, gh, kind = Wp, p(CAP_H) if cap else p(240), "cap" if cap else "pan"
        spots = ((0, 0), (Wp - r, 0), (0, h - r), (Wp - r, h - r))
        key = (kind, r, Wp, tuple(min(y, gh) for _, y in spots))
        if getattr(self, "_bd", (None,))[0] != key:
            art = self.art
            ck = ("card_lit", gw, gh, kind, r)
            lit = art._cache.get(ck)
            if lit is None:
                lit = art.I.new("RGBA", (gw, gh + r), art._rgba(CARD))
                lit.alpha_composite(art.glow_pil(gw, gh, kind))
                lit = art._cache[ck] = lit.convert("RGB")
            img = art.I.new("RGB", (2 * r, 2 * r), _rgb(CARD))
            for (x, y), q in zip(spots, ((0, 0), (r, 0), (0, r), (r, r))):
                if y < gh:
                    img.paste(lit.crop((x, y, x + r, y + r)), q)
            self._bd = (key, img)
        return self._bd

    def _glass_shape(self):
        if not self._glass or not self._radius:
            return
        band, bd = self._band(), self._backdrop()
        key = (self._radius, self._edge, band or bd[0])
        if key != getattr(self, "_glass_key", None):
            self._glass_key = key
            self._glass.shape(self._radius, self._edge or HAIR, band, bd)

    def _set_alpha(self, a: float):
        self.root.attributes("-alpha", a)
        if self._glass:
            self._glass.set_alpha(a)

    def _glass_show(self):
        if self._use_glass and not self._glass:
            try:
                self._glass = CornerGlass(self.root, self.art)
                self._glass.alpha = float(self.root.attributes("-alpha"))
                self._glass_key = None
                self._glass_shape()
            except Exception as e:
                self.api.log(f"corner glass off: {e!r}")
                self._glass, self._use_glass = None, False
                r, edge, self._radius = self._radius, self._edge, None
                if r:
                    self._set_shape(r, edge or HAIR)
        if self._glass:
            self._glass.show()

    def _orb_offset(self):
        """Where the orb's centre sits inside the player, in device px."""
        p = self.p
        return (p(9 + ORB / 2), p(CAP_H / 2)) if self.mode == "cap" else (p(PAD + ORB / 2), p(PAD + ORB / 2))

    def minimize(self):
        """Shrink to a small ember orb where the player's orb is (kept across restarts)."""
        self.cfg.update(minimized=True)
        if self._mini:
            return
        ox, oy = self._orb_offset()
        x, y = self._xy
        self._tip_hide()
        if self._visible:
            self._withdraw()
        else:
            self.root.withdraw()
        self._mini = MiniOrb(self, x + ox, y + oy)
        self._mini.render(0, not (self._cur or self._q), len(self._calls))
        self._kick()

    def restore(self):
        """Back from the orb to the player, exactly where it was (same spot, same size) before minimizing."""
        self.cfg.update(minimized=False)
        if self._mini:
            self._mini.destroy()
            self._mini = None
            if not self._on_screen():
                self._reset_position()
        self.show()

    def summon(self):
        """Bring the player back for sure (the "Voice player" shortcut, POST /show): onto a visible screen,
        un-hidden, and redrawn from scratch in case the window lost its picture."""
        if self.cfg.get("minimized") or self._mini:
            self.restore()
            return
        if not self._on_screen():
            self._reset_position()
        if self._visible:
            self.root.after(10, self._rebuild)
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self._set_alpha(self._alpha())
            if self._glass:
                self._glass.show()
        else:
            self.show()

    def _on_screen(self) -> bool:
        """True when the player's top-left area lies on a connected monitor (Windows; elsewhere assume so)."""
        if sys.platform != "win32":
            return True
        try:
            import ctypes
            from ctypes import wintypes as W
            x, y = self._xy
            u = ctypes.windll.user32
            u.MonitorFromRect.restype = ctypes.c_void_p
            rc = W.RECT(x, y, x + self.p(60), y + self.p(40))
            return bool(u.MonitorFromRect(ctypes.byref(rc), 0))  # MONITOR_DEFAULTTONULL
        except Exception:
            return True

    def _work_area(self, x=None, y=None):
        """(left, top, right, bottom) of the work area (no taskbar) of the monitor at x, y (default: the player)."""
        if x is None:
            x, y = self._xy
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes as W

                class MI(ctypes.Structure):
                    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]
                u = ctypes.windll.user32
                u.MonitorFromRect.restype = ctypes.c_void_p
                rc = W.RECT(x, y, x + self.p(60), y + self.p(40))
                mon = u.MonitorFromRect(ctypes.byref(rc), 2)  # MONITOR_DEFAULTTONEAREST
                mi = MI()
                mi.cbSize = ctypes.sizeof(MI)
                if u.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
                    w = mi.rcWork
                    return w.left, w.top, w.right, w.bottom
            except Exception:
                pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _room(self) -> int:
        """The tallest the player may grow from where it is without leaving the work area."""
        return max(self.p(240), self._work_area()[3] - self._xy[1] - self.p(12))

    def _alpha(self) -> float:
        return float(self.cfg.get("opacity"))

    def _withdraw(self):
        self._visible = False

        def gone():
            if not self._visible:
                self.root.withdraw()
                if self._glass:
                    self._glass.hide()
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
        if self._glass:
            self._glass.destroy()
        if self._mini:
            self._mini.destroy()
        self.root.destroy()

    def toggle_mute(self):
        self.vq.set_muted(not self.vq.muted)
        self._tick_once()

    def toggle_pause(self):
        self.vq.resume() if self.vq.paused else self.vq.pause()
        self._tick_once()

    def _menu(self, e):
        m = self.tk.Menu(self.root, tearoff=0)
        if self._mini:
            m.add_command(label="Restore the player", command=self.restore)
        else:
            m.add_command(label="Minimize to an orb", command=self.minimize)
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
            self.view = "now"
            self.c.delete("set")
            self.c.delete("setmask")
            self._set_off = self._set_max = 0
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
            c.itemconfigure("glowcap", state="disabled")
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
            if not instant:
                for tag in ("play", "ask"):
                    self._glide(tag, 0, -self.p(8), dur=0.24)
                self._stagger_chips()

    def _set_cap_hover(self, on: bool):
        """Hovering the capsule shows a minimize button in the badge's place."""
        if on == self._cap_hover:
            return
        self._cap_hover = on
        if self.mode == "cap":
            self._place_buttons()
            self._tick_once(force=True)

    def _place_buttons(self):
        p, Wp, c = self.p, self.Wp, self.c
        y = p(CAP_H / 2) if self.mode == "cap" else p(PAD + ORB / 2)
        if self.mode == "cap":
            names = ("close", "size", "mini") if self._cap_hover else ("close", "size")
        else:
            names = ("close", "size", "mini", "mute", "gear")
        for name in ("close", "size", "mini", "mute", "gear"):
            b = self.btn[name]
            if name in names:
                x = Wp - p(12 if self.mode == "cap" else 10) - p(15) - names.index(name) * p(31)
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
        mini = self._mini if not self._visible else None
        if not self._visible and not mini:
            return False
        pos, dur, wall, running = self._prog
        speaking = running and self._cur is not None
        if dur:
            cur_pos = pos + ((time.time() - wall) if running else 0)
            frac = min(1.0, max(0.0, cur_pos / dur))
            if self._scrub is not None:
                frac = self._scrub
            if not mini:
                self._draw_progress(frac, dur)
        # ember: orb glow and the card's rim pulse with the voice (or breathe slowly)
        env = (self._cur or {}).get("env") if speaking and self.cfg.get("pulse_with_voice") else None
        if env and dur:
            i = int((pos + (time.time() - wall)) * ((self._cur or {}).get("env_fps") or 25))
            target = 0.1 + 0.9 * (env[i] if 0 <= i < len(env) else 0.0)
            # quick attack, gentler release: reads as a pulse on each syllable, not flicker
            self._env_k += (target - self._env_k) * (0.65 if target > self._env_k else 0.2)
            lvl = round(self._env_k * (LEVELS - 1))
        elif speaking:
            phase = (now % BREATH) / BREATH
            lvl = round((0.5 - 0.5 * math.cos(2 * math.pi * phase)) * (LEVELS - 1))
        else:
            lvl = -2 if not (self._cur or self._q) else LEVELS // 2
        if mini:
            dim = lvl == -2
            mini.render(0 if dim else lvl / (LEVELS - 1), dim, len(self._calls))
            return speaking
        if lvl != self._level:
            self._level = lvl
            dim = lvl == -2
            k = 0 if dim else lvl / (LEVELS - 1)
            self.c.itemconfigure(self.orb, image=self.art.orb(k, dim=dim))
            rim = self.cfg.get("rim_color") or AMBER
            edge = mix(HAIR, rim, 0.25 + 0.75 * k) if speaking else HAIR
            self._glow_rings(k if speaking else 0.0)  # first: the corner glass matches the ring colour
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
        calls = self._collect_calls_raw(cur)
        ids = {self._call_id(x) for x in calls}
        for cid in getattr(self, "_prev_call_ids", set()) - ids:
            row = self.vq.history.get(cid) or {}
            if row.get("answered_in") == "thread":
                self._toast(f"{row.get('project') or 'A call'} was answered in its thread")
                self._drafts.pop(cid, None)
        self._prev_call_ids = ids
        return calls

    def _collect_calls_raw(self, cur) -> list[dict]:
        now = time.time()
        calls, seen = [], set()
        # by age, not count: a busy day writes 60+ rows, so the last 150 rows covered under 3 days
        rows = self.vq.history.since(now - 3600 * float(self.cfg.get("calls_hours")) - 86400)
        newest: dict[str, float] = {}  # a later reply from the same session means it was answered there
        for r in rows:
            sid = r.get("session_id")
            if sid and r.get("status") not in BACKGROUND:
                newest[sid] = max(newest.get(sid, 0), r.get("ts", 0))
        cur_row = self.vq.history.get(cur.get("replay_of") or cur["id"]) if cur else None
        if (cur and cur.get("decision") and (cur.get("replay_of") or cur["id"]) not in self.answered
                and not (cur_row or {}).get("answer") and not (cur_row or {}).get("dismissed")):
            calls.append(cur)
            seen.add(cur.get("replay_of") or cur["id"])
        for r in rows:
            stale = newest.get(r.get("session_id") or "", 0) > r.get("ts", 0) + 1
            if (r.get("decision") and not r.get("answer") and not r.get("dismissed") and not stale
                    and r["id"] not in self.answered and r["id"] not in seen
                    and now - r.get("ts", 0) < 3600 * float(self.cfg.get("calls_hours"))):
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
            self._step_motion(d)

    def _step_motion(self, direction):
        """The next question comes in from the side it lives on: a few px and a fade, chips staggered."""
        self._glide("ask", self.p(10) * (1 if direction > 0 else -1), 0, dur=0.2)
        self._reveal("ask")
        self._stagger_chips()

    def _call_id(self, item):
        return item.get("replay_of") or item["id"]

    def _dismiss(self):
        item = self._call()
        if not item:
            return
        cid = self._call_id(item)
        self.answered.add(cid)
        self._drafts.pop(cid, None)
        self.vq.history.update(cid, dismissed=True)
        self._after_call()

    def _after_call(self):
        self._layout_key = None
        self._tick_once()
        if not self._calls and self._auto_open:
            self.root.after(1600, lambda: not self._calls and self._auto_open and self._set_mode("cap"))

    def _pick_number(self, i):
        item = self._call()
        if not item or self.mode != "panel" or self.view != "now":
            return
        decs, d = self._draft(item)
        if d["ix"] < len(decs) and i <= len(decs[d["ix"]]["options"]):
            self._reply(item, decs[d["ix"]]["options"][i - 1])

    def _enter(self):
        """Enter in the review step sends every answer."""
        item = self._call()
        if item and self.mode == "panel" and self.view == "now":
            decs, d = self._draft(item)
            if len(decs) > 1 and d["ix"] >= len(decs):
                self._send_all(item)

    def _skip_question(self):
        item = self._call()
        if item and self.mode == "panel" and self.view == "now":
            decs, d = self._draft(item)
            if len(decs) > 1 and d["ix"] < len(decs):
                self._reply(item, None)

    # a reply can ask several questions: answer them one by one, then send them together
    def _draft(self, call):
        cid = self._call_id(call)
        decs = decisions_of(call)
        d = self._drafts.get(cid)
        if not d or len(d["answers"]) != len(decs):
            d = self._drafts[cid] = {"answers": [None] * len(decs), "done": [False] * len(decs), "ix": 0}
        return decs, d

    def _draft_sig(self, call):
        if not call:
            return None
        _, d = self._draft(call)
        return d["ix"], tuple(d["answers"]), tuple(d["done"]), d.get("ctx", False), d.get("full", False)

    def _reply(self, call, text):
        """Record one answer (None = skip). One question sends at once; several wait for Send answers."""
        if call.get("kind") == "approve":  # approving happens where the session runs: open it
            self._open(call)
            return
        decs, d = self._draft(call)
        if len(decs) <= 1:
            self._answer(call, text or SKIPPED)
            return
        i = min(d["ix"], len(decs) - 1)
        d["answers"][i], d["done"][i] = text, True
        later = [j for j in range(i + 1, len(decs)) if not d["done"][j]]
        rest = [j for j in range(len(decs)) if not d["done"][j]]
        d["ix"] = later[0] if later else (rest[0] if rest else len(decs))
        self._layout_key = None
        self._tick_once()
        self._step_motion(1 if d["ix"] > i else -1)

    def _goto_question(self, call, i):
        _, d = self._draft(call)
        back = i < d["ix"]
        d["ix"] = i
        self._layout_key = None
        self._tick_once()
        self._step_motion(-1 if back else 1)

    def _send_all(self, call):
        decs, d = self._draft(call)
        self._answer(call, combined_answer(decs, d["answers"]), answers=list(d["answers"]))

    def _set_hint(self, hint):
        if hint == self._hint:
            return
        showing = self.entry.get() == self._hint
        self._hint = hint
        if showing and self.root.focus_get() is not self.entry:
            self.entry.delete(0, "end")
            self.entry.insert(0, hint)

    def _link(self, x, y, text, fn, tag, anchor="e", color=None, tip=None):
        """A small mono text button (Back, Skip)."""
        c = self.c
        color = color or FAINT
        t = c.create_text(x, y, anchor=anchor, text=text, font=self.F["mono"], fill=color, tags=("pan", "ask", tag))
        c.tag_bind(tag, "<Button-1>", lambda e: fn())
        c.tag_bind(tag, "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(t, FG)))
        c.tag_bind(tag, "<Leave>", lambda e: (c.configure(cursor=""), c.itemconfigure(t, fill=color)))
        if tip:
            self._tipify(tag, lambda: tip)
        self.chips.append((t,))
        return self.F["mono"].measure(text)

    def _call_steps(self, call, decs, d, y):
        """Step row: QUESTION 2 OF 3 with Back and Skip, then one segment per question."""
        c, p, Wp = self.c, self.p, self.Wp
        n, ix = len(decs), d["ix"]
        review = ix >= n
        label = f"{n} ANSWERS READY" if review else f"QUESTION {ix + 1} OF {n}"
        t = c.create_text(p(PAD), y + p(6), anchor="w", text=label, font=self.F["mono"], fill=MUTED,
                          tags=("pan", "ask"))
        self.chips.append((t,))
        right = Wp - p(PAD)
        if not review:
            right -= self._link(right, y + p(6), "Skip", lambda: self._reply(call, None), "qskip",
                                tip="Leave this one to the agent (S)") + p(16)
        if ix > 0:
            self._link(right, y + p(6), "Back", lambda: self._goto_question(call, ix - 1), "qback")
        y += p(18)
        gap = p(6)
        seg = (Wp - 2 * p(PAD) - gap * (n - 1)) / n
        for j in range(n):
            x = p(PAD) + j * (seg + gap)
            col = AMBER if d["done"][j] else (FG if j == ix else HAIR)
            tag = f"qseg{j}"
            hit = c.create_rectangle(x, y - p(6), x + seg, y + p(8), fill=CARD, width=0, tags=("pan", "ask", "askhit", tag))
            ln = c.create_line(x, y + p(1), x + seg, y + p(1), fill=col, width=max(1, p(3)), capstyle="round",
                               tags=("pan", "ask", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, j=j: self._goto_question(call, j))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
            self._tipify(tag, lambda j=j: decs[j]["question"])
            self.chips.append((hit, ln))
        return y + p(18)

    def _ctx_toggle(self, call, key):
        _, d = self._draft(call)
        d[key] = not d.get(key, False)
        if key == "ctx" and not d["ctx"]:
            d["full"] = False
        self._layout_key = None
        self._tick_once()

    def _thumb(self, path, w, h):
        """A cover-cropped thumbnail of a picture the reply points at (cached)."""
        key = (path, w, h)
        if key not in self._thumbs:
            from PIL import Image, ImageDraw, ImageTk
            im = Image.open(path).convert("RGB")
            s = max(w / im.width, h / im.height)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
            l, t = (im.width - w) // 2, (im.height - h) // 2
            im = im.crop((l, t, l + w, t + h)).convert("RGBA")
            mask = Image.new("L", (w * 4, h * 4), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 4 - 1, h * 4 - 1), radius=self.p(10) * 4, fill=255)
            im.putalpha(mask.resize((w, h), Image.LANCZOS))
            bg = Image.new("RGBA", (w, h), Art._rgba(CARD_HI))
            self._thumbs[key] = ImageTk.PhotoImage(Image.alpha_composite(bg, im))
        return self._thumbs[key]

    @staticmethod
    def _reply_text(message):
        t = re.sub(r"\*\*|__|`", "", message or "")
        t = re.sub(r"!?\[([^\]]*)\]\(([^)]*)\)", r"\1", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()

    def _open_path(self, target):
        try:
            if sys.platform == "win32":
                import os
                os.startfile(target)
            else:
                import webbrowser
                webbrowser.open(target)
        except OSError as e:
            self._toast(f"Couldn't open it: {e.strerror or e}")

    def _call_context(self, call, dec, d, y):
        """What the question is about: a one-line hint that opens to the context, any pictures or previews
        the reply points at, and the full reply, so there's no need to open the session first."""
        c, p, Wp = self.c, self.p, self.Wp
        row = self.vq.history.get(self._call_id(call)) or {}
        ctx = (dec.get("context") or "").strip()
        vis = call.get("visuals") or row.get("visuals") or []
        msg = row.get("message") or call.get("message") or ""
        if not (ctx or vis or msg):
            return y
        x0, x1 = p(PAD), Wp - p(PAD)
        opened = d.get("ctx", False)
        # the toggle row
        g = c.create_text(x0, y + p(8), anchor="w", text=self.glyph("down" if opened else "right"),
                          font=self.F["icon_s"], fill=MUTED, tags=("pan", "ask", "ctxtog"))
        hint = ctx if ctx else ("Pictures and the full reply" if vis else "The full reply")
        lead = "Hide context" if opened else "About: "
        t1 = c.create_text(x0 + p(16), y + p(8), anchor="w", text=lead, font=self.F["mono"], fill=EMBER,
                           tags=("pan", "ask", "ctxtog"))
        items = [g, t1]
        if not opened:
            lw = self.F["mono"].measure(lead)
            t2 = c.create_text(x0 + p(16) + lw, y + p(8), anchor="w",
                               text=self._ellipsize(hint, self.F["meta"], x1 - x0 - p(16) - lw),
                               font=self.F["meta"], fill=MUTED, tags=("pan", "ask", "ctxtog"))
            items.append(t2)
        hit = c.create_rectangle(x0 - p(4), y - p(2), x1, y + p(18), fill=CARD, width=0,
                                 tags=("pan", "ask", "askhit", "ctxtog"))
        items.append(hit)
        c.tag_bind("ctxtog", "<Button-1>", lambda e: self._ctx_toggle(call, "ctx"))
        c.tag_bind("ctxtog", "<Enter>", lambda e: (c.configure(cursor="hand2"), self._tint(g, FG)))
        c.tag_bind("ctxtog", "<Leave>", lambda e: (c.configure(cursor=""), self._tint(g, MUTED)))
        self.chips.append(tuple(items))
        y += p(26)
        if not opened:
            return y + p(2)
        # the panel
        top = y
        pad = p(14)
        ix0, ix1 = x0 + pad, x1 - pad
        y += pad
        if ctx:
            t = c.create_text(ix0, y, anchor="nw", text=ctx, font=self.F["snip"], fill=BODY, width=ix1 - ix0,
                              tags=("pan", "ask"))
            self.chips.append((t,))
            bb = c.bbox(t)
            y = (bb[3] if bb else y) + p(12)
        imgs = [v for v in vis if v.get("kind") == "image"][:2]
        if imgs:
            gap = p(8)
            w = int(((ix1 - ix0) - gap * (len(imgs) - 1)) / len(imgs))
            h = p(110) if len(imgs) == 1 else p(84)
            for n, v in enumerate(imgs):
                try:
                    ph = self._thumb(v["path"], w, h)
                except Exception as e:  # a missing or odd file never breaks the panel
                    self.api.log(f"thumb {v.get('path')}: {e!r}")
                    continue
                tag = f"ctximg{n}"
                im = c.create_image(ix0 + n * (w + gap), y, anchor="nw", image=ph, tags=("pan", "ask", tag))
                c.tag_bind(tag, "<Button-1>", lambda e, pth=v["path"]: self._open_path(pth))
                c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
                c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
                self._tipify(tag, lambda v=v: f"Open {v.get('label') or 'the picture'}")
                self.chips.append((im,))
            y += h + p(12)
        links = [v for v in vis if v.get("kind") == "link"][:3]
        lx = ix0
        actions = [(v.get("label") or "Open", lambda u=v["url"]: self._open_path(u)) for v in links]
        if msg:
            actions.append(("Hide the full reply" if d.get("full") else "Read the full reply",
                            lambda: self._ctx_toggle(call, "full")))
        for n, (label, fn) in enumerate(actions):
            text = label + (" \u2197" if n < len(links) else "")
            tw = self.F["link"].measure(text)
            if lx > ix0 and lx + tw > ix1:
                lx, y = ix0, y + p(22)
            tag = f"ctxlnk{n}"
            t = c.create_text(lx, y, anchor="nw", text=text, font=self.F["link"], fill=EMBER, tags=("pan", "ask", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, fn=fn: fn())
            c.tag_bind(tag, "<Enter>", lambda e, t=t: (self._tint(t, FG), c.configure(cursor="hand2")))
            c.tag_bind(tag, "<Leave>", lambda e, t=t: (self._tint(t, EMBER), c.configure(cursor="")))
            self.chips.append((t,))
            lx += tw + p(18)
        if actions:
            y += p(22)
        if d.get("full") and msg:
            self.ctx_text.configure(state="normal")
            self.ctx_text.delete("1.0", "end")
            self.ctx_text.insert("1.0", self._reply_text(msg))
            self.ctx_text.configure(state="disabled")
            self.ctx_text.yview_moveto(0)
            h = p(190)
            c.coords(self.ctx_win, ix0, y + p(2))
            c.itemconfigure(self.ctx_win, width=ix1 - ix0, height=h)
            self._ctx_shown = True
            y += h + p(10)
        y += pad - p(6)
        box = c.create_rectangle(x0, top, x1, y, fill=CARD_HI, outline=HAIR, width=1,
                                 tags=("pan", "ask", "askhit"))
        self.chips.append((box,))
        return y + p(14)

    def _call_chips(self, call, dec, chosen, y):
        """Option pills. The chosen answer (or else the recommendation) is filled; the recommendation has a star."""
        c, p, Wp = self.c, self.p, self.Wp
        x, ch, maxw = p(PAD), p(36), Wp - 2 * p(PAD)
        rec = dec.get("recommended")
        opts = dec["options"]
        filled = opts.index(chosen) if chosen in opts else (rec if chosen is None else None)
        for n, opt in enumerate(opts):
            is_fill = filled == n
            label = ("\u2605 " if rec == n else "") + opt
            font = self.F["chip"]
            tw = min(font.measure(label), maxw - p(32))
            w = tw + p(30)
            if x > p(PAD) and x + w > Wp - p(PAD):
                x, y = p(PAD), y + ch + p(8)
            label = self._ellipsize(label, font, tw + 1)
            base = self.art.pill(w, ch, grad=ACC) if is_fill else self.art.pill(
                w, ch, outline=(*INK, 36))
            hover = self.art.pill(w, ch, grad=ACC_HI) if is_fill else self.art.pill(
                w, ch, fill=(*INK, 14), outline=(*ACC_RGB, 120))
            tag = f"chip{n}"
            img = c.create_image(x, y, anchor="nw", image=base, tags=("pan", "ask", tag))
            txt = c.create_text(x + w / 2, y + ch / 2, text=label, font=font,
                                fill=ACC_INK if is_fill else FG, tags=("pan", "ask", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, o=opt, it=call: self._reply(it, o))
            c.tag_bind(tag, "<Enter>", lambda e, i=img, h=hover: (c.itemconfigure(i, image=h),
                                                                  c.configure(cursor="hand2")))
            c.tag_bind(tag, "<Leave>", lambda e, i=img, b=base: (c.itemconfigure(i, image=b),
                                                                 c.configure(cursor="")))
            self._tipify(tag, lambda n=n, o=opt: (f"Press {n + 1} to pick" if n < 9 else o))
            self.chips.append((img, txt))
            x += w + p(8)
        if opts:
            y += ch + p(12)
        if chosen and chosen not in opts:
            t = c.create_text(p(PAD), y, anchor="nw", width=maxw, text=f"Your answer: {chosen}",
                              font=self.F["meta"], fill=EMBER, tags=("pan", "ask"))
            self.chips.append((t,))
            bb = c.bbox(t)
            y = (bb[3] if bb else y) + p(10)
        return y

    def _call_review(self, call, decs, d, y):
        """Every question with its answer; click one to change it, then send them all in one message."""
        c, p, Wp = self.c, self.p, self.Wp
        maxw = Wp - 2 * p(PAD)
        tx = p(PAD) + p(24)
        for j, dec in enumerate(decs):
            tag = f"qrev{j}"
            top = y
            num = c.create_text(p(PAD), y + p(1), anchor="nw", text=f"{j + 1}", font=self.F["mono"], fill=EMBER,
                                tags=("pan", "ask", tag))
            q = c.create_text(tx, y, anchor="nw", width=maxw - p(24) - p(18), text=dec["question"],
                              font=self.F["meta"], fill=MUTED, tags=("pan", "ask", tag))
            bb = c.bbox(q)
            y = (bb[3] if bb else y) + p(3)
            ans = d["answers"][j]
            a = c.create_text(tx, y, anchor="nw", width=maxw - p(24) - p(18),
                              text=ans if ans else "Skipped: the agent decides",
                              font=self.F["chip"], fill=FG if ans else FAINT, tags=("pan", "ask", tag))
            bb = c.bbox(a)
            y = (bb[3] if bb else y)
            g = c.create_text(Wp - p(PAD), (top + y) / 2, anchor="e", text=self.glyph("right"), font=self.F["icon_s"],
                              fill=MUTED, tags=("pan", "ask", tag))
            hit = c.create_rectangle(p(PAD) - p(8), top - p(7), Wp - p(PAD) + p(8), y + p(7), fill=CARD, width=0,
                                     tags=("pan", "ask", "askhit", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, j=j: self._goto_question(call, j))
            c.tag_bind(tag, "<Enter>", lambda e, h=hit, g=g: (self._tint(h, CARD_HI),
                                                              self._tint(g, FG), c.configure(cursor="hand2")))
            c.tag_bind(tag, "<Leave>", lambda e, h=hit, g=g: (self._tint(h, CARD),
                                                              self._tint(g, MUTED), c.configure(cursor="")))
            self._tipify(tag, lambda: "Change this answer")
            self.chips.append((num, q, a, g, hit))
            y += p(16)
            if j < len(decs) - 1:
                ln = c.create_line(p(PAD), y - p(8), Wp - p(PAD), y - p(8), fill=HAIR, tags=("pan", "ask"))
                self.chips.append((ln,))
        y += p(2)
        bh = p(42)
        base = self.art.pill(maxw, bh, grad=ACC)
        hover = self.art.pill(maxw, bh, grad=ACC_HI)
        img = c.create_image(p(PAD), y, anchor="nw", image=base, tags=("pan", "ask", "sendall"))
        txt = c.create_text(Wp / 2, y + bh / 2, text=f"Send {len(decs)} answers to {call.get('project') or 'the session'}",
                            font=self.F["chip"], fill=ACC_INK, tags=("pan", "ask", "sendall"))
        c.tag_bind("sendall", "<Button-1>", lambda e: self._send_all(call))
        c.tag_bind("sendall", "<Enter>", lambda e: (c.itemconfigure(img, image=hover), c.configure(cursor="hand2")))
        c.tag_bind("sendall", "<Leave>", lambda e: (c.itemconfigure(img, image=base), c.configure(cursor="")))
        self._tipify("sendall", lambda: "One message with every answer (Enter)")
        self.chips.append((img, txt))
        return y + bh + p(4)

    def _send_typed(self):
        text = self.entry.get().strip()
        item = self._call()
        if item and text and text != self._hint:
            self.entry.delete(0, "end")
            self.root.focus_set()
            self._reply(item, text)
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
                             outline=(*ACC_RGB, 150) if focus else (*INK, 30))

    def _answer(self, item, text, answers=None):
        cid = self._call_id(item)
        self.answered.add(cid)
        self._drafts.pop(cid, None)
        self.vq.history.update(cid, answer=text, answered_at=time.time(),
                               **({"answers": answers} if answers else {}))
        mode = self.cfg.get("desktop_answers")
        app_id = getattr(self.api, "desktop_app_id", None)
        if mode in ("send", "paste") and app_id and app_id(item):
            # the thread is open in the Claude app: type the answer there, so the thread shows it and the
            # app's own agent carries on (a background resume would leave that thread out of date)
            self._toast(f"Typing your answer{'s' if answers else ''} into the {item.get('project')} thread")
            threading.Thread(target=self._type_desktop, args=(item, text, mode == "send"), daemon=True).start()
            self._after_call()
            return
        if self.api.resume_session(self.vq, item, text):
            what = f"{len(answers)} answers" if answers else "Sent"
            self._toast(f"{what} sent to {item.get('project')}. Its reply will play here" if answers
                        else f"Sent to {item.get('project')}. Its reply will play here")
        else:
            self._copy(text)
            msg = self.api.open_session(item)
            self._toast((msg + ". " if msg else "") + "No session to resume: answer copied")
        self._after_call()

    def _type_desktop(self, item, text, send):
        """Worker thread: the UI only learns the result on the next tick (Tk is not thread-safe)."""
        try:
            code = self.api.type_into_desktop(item, text, send)
        except Exception as e:  # never lose an answer to an automation error
            self.api.log(f"type into desktop error: {e!r}")
            code = 5
        self._typed.append((item, text, code))

    def _typed_results(self):
        while self._typed:
            item, text, code = self._typed.pop(0)
            self._toast(self.api.desktop_result.get(code, "Answer copied"))
            if code in (3, 4, 5):  # not delivered: keep the answer on the clipboard and the call open
                self._copy(text)
                cid = self._call_id(item)
                self.answered.discard(cid)
                self.vq.history.update(cid, answer=None, answered_at=None, answers=None)
                self._layout_key = None

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
        self._text(self.pan_meta, self._ellipsize(text, self.F["meta"], self.Wp - self.p(78) - self.p(138)))
        self._fill(self.pan_meta, EMBER)
        for item in (self.cap_line, self.pan_meta):
            self._glide(item, 0, self.p(5), dur=0.2)

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
            self.summon()
        if self.vq.hide_requested:
            self.vq.hide_requested = False
            self.hide_until_next()
        if self._typed:
            self._typed_results()
        # settings changed from outside the player (the HTTP API or settings.json)
        if self._look() != self._built_size:
            self._built_size = self._look()
            self.root.after(10, self._rebuild)
            return
        if self.cfg.get("opacity") != getattr(self, "_built_alpha", None):
            self._built_alpha = self.cfg.get("opacity")
            if getattr(self, "_visible", False):
                try:
                    self._set_alpha(self._alpha())
                except self.tk.TclError:
                    pass
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
                if not self._visible and self.hidden_for_next is not None:
                    self.show()  # a decision is waiting: come back even if hidden until the next voiceover
                if self._mini and self.cfg.get("auto_open_calls"):
                    self.restore()
                if self.mode == "cap" and self._visible and self.cfg.get("auto_open_calls"):
                    self._auto_open = fresh[0]
                    self._set_mode("panel")
            self._layout_key = None
        else:
            self._calls = calls

        # hide when idle (setting): out of the way 8 s after the last reply, back with the next one
        idle = not cur and not q and not self._calls and self.mode == "cap"
        if self.cfg.get("hide_when_idle") and idle and self._visible:
            self._idle_since = self._idle_since or time.time()
            if time.time() - self._idle_since > 8:
                self._idle_since = None
                self.hide_until_next()
        else:
            self._idle_since = None

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
            img = self.art.pill(bw, bh, grad=ACC_HI) if filled else self.art.pill(
                bw, bh, outline=(*INK, 40))
            self.c.itemconfigure(self.badge, image=img)
            self.c.coords(self.badge, right, p(CAP_H / 2))
            self.c.coords(self.badge_t, right - bw / 2, p(CAP_H / 2))
            self._text(self.badge_t, badge)
            self._fill(self.badge_t, ACC_INK if filled else MUTED)
            self._badge_left = right - bw
        else:
            self._badge_left = right + p(17)
        if self._cap_hover:                              # the minimize button takes the badge's place
            self._badge_left = min(self._badge_left, right - p(24))
        if self.mode == "cap":
            self.c.itemconfigure("badge", state="normal" if badge and not self._cap_hover else "hidden")
        self._cap_track_end = self._badge_left - p(10) if badge else right - p(4)

        # orb glyph
        self.c.itemconfigure(self.orb_g, text=self.glyph("pause" if cur and not st["paused"] else "play"))
        self._fill(self.orb_g, OBSIDIAN if (cur or q) else EMBER)
        self._fill(self.btn["mute"]["g"], self._btn_color("mute"))
        self._set_glyph("mute", "mute" if self.vq.muted else "volume")

        # capsule text
        if show:
            name = self._where(show)
            line = show.get("text", "") if show.get("ready", True) else "Summarizing the reply…"
            if st["paused"] and cur:
                line = "Paused · " + line
        elif self._calls:
            n = len(self._calls)
            name = self._calls_names(n)
            decs = decisions_of(self._calls[0])
            many = f" · {len(decs)} questions" if len(decs) > 1 else ""
            line = self._where(self._calls[0], "{}" + f"{many}: {decs[0]['question']}")
        else:
            last = self.vq.history.recent(1)
            name = "All caught up"
            line = self._last_lines(last)
        nmax = self._badge_left - p(66) - p(10)
        self._text(self.cap_name, self._fit(name, self.F["name"], nmax))
        if not toast:
            self._text(self.cap_line, self._fit(line, self.F["line"], self._cap_line_w()))
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
        if self.view == "settings":
            return self._settings_panel(force)
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
               self._draft_sig(call), self.lib, lib_sig, self.mode)
        # header (cheap, every tick)
        if show:
            name = self._where(show)
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
            name = self._calls_names(n) if n else "All caught up"
            meta = self._last_lines(last)
        nmax = Wp - p(78) - p(171)  # five header buttons
        shown_name = self._fit(name, self.F["title"], nmax)
        if self._text(self.pan_name, shown_name) or force:
            c.coords(self.pan_arrow, p(78) + self.F["title"].measure(shown_name) + p(6), p(31))
        if not toast:
            self._text(self.pan_meta, self._fit(meta, self.F["meta"], Wp - p(78) - p(PAD)))  # under the buttons
            self._fill(self.pan_meta, MUTED)
        self._text(self.next_t, self._ellipsize(f"Next: {q[0].get('project')}" if q else "Nothing after this",
                                                self.F["meta"], p(150)))
        if key == self._layout_key and not force:
            c.itemconfigure(self.pan_arrow, state="normal" if show else "hidden")
            return
        self._layout_key = key
        self._end_slides()

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
        self._ctx_shown = False
        for ids in self.chips:
            for i in ids:
                c.delete(i)
        self.chips = []
        if call:
            y += p(14)
            c.coords(self.call_rule, p(PAD), y, p(PAD) + p(20), y)
            c.coords(self.call_eyebrow, p(PAD) + p(28), y)
            ew = self.F["mono"].measure("YOUR CALL")
            x0 = p(PAD) + p(28) + ew + p(8)
            room = (Wp - p(PAD) - x0 - self.F["mono"].measure("Dismiss") - p(20)
                    - (self.F["mono"].measure("‹ 10/10 ›") + p(18) if len(self._calls) > 1 else 0))
            frm = self._fit(self._where(call, "· {}"), self.F["mono"], max(p(60), room))
            self._text(self.call_from, frm)
            c.coords(self.call_from, x0, y)
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
            decs, d = self._draft(call)
            n, ix = len(decs), d["ix"]
            review = n > 1 and ix >= n
            if n > 1:
                y = self._call_steps(call, decs, d, y)
            if review:
                y = self._call_review(call, decs, d, y)
                c.itemconfigure(self.question, state="hidden")
            else:
                dec = decs[ix]
                self._text(self.question, dec["question"])
                c.coords(self.question, p(PAD), y)
                bb = c.bbox(self.question)
                y = (bb[3] if bb else y) + p(10)
                y = self._call_context(call, dec, d, y)
                y = self._call_chips(call, dec, d["answers"][ix] if d["done"][ix] else None, y)
                self._set_hint("Type your answer" if not dec["options"] else
                               ("Another answer, or ask a follow-up" if n > 1 else "Another option, or ask a follow-up"))
                c.itemconfigure(self.entry_bg, image=self._entry_img(self.root.focus_get() is self.entry))
                c.coords(self.entry_bg, p(PAD), y)
                c.coords(self.entry_win, p(PAD) + p(18), y + p(21))
                c.coords(self.send_bg, Wp - p(PAD) - p(5) - p(16), y + p(21))
                c.coords(self.send_g, Wp - p(PAD) - p(5) - p(16), y + p(21))
                y += p(42) + p(4)
            c.tag_raise("ask")
        c.itemconfigure("ask", state=state(bool(call)))
        if call:
            decs, d = self._draft(call)
            in_review = len(decs) > 1 and d["ix"] >= len(decs)
            for i in (self.entry_bg, self.entry_win, self.send_bg, self.send_g):
                c.itemconfigure(i, state="hidden" if in_review else "normal")
            c.itemconfigure(self.question, state="hidden" if in_review else "normal")
            c.tag_lower("askhit", self.glow_cap)
        c.itemconfigure(self.ctx_win, state="normal" if (call and self._ctx_shown) else "hidden")
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
            room = self._room() - (y + p(R_PANEL))
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
        c.itemconfigure("glowpan", state="disabled")
        self._place_buttons()
        c.itemconfigure(self.pan_arrow, state="normal" if show else "hidden")
        self._resize(y)

    # ---------------------------------------------------------- settings view
    def _toggle_settings(self):
        self.view = "now" if self.view == "settings" else "settings"
        self.c.delete("set")
        self.c.delete("setmask")
        self._set_off = self._set_max = 0
        self._layout_key = None
        self._fill(self.btn["gear"]["g"], self._btn_color("gear"))
        if self.mode == "cap":
            self._set_mode("panel")  # resets view to now; open settings after
            self.view = "settings"
            self._layout_key = None
        self._tick_once(force=True)
        self._reveal("set" if self.view == "settings" else "pan")
        if self.view == "settings":
            self._glide("set", self.p(14), 0, dur=0.22)
        else:
            for tag in ("play", "ask"):
                self._glide(tag, -self.p(10), 0, dur=0.18)

    def _set(self, key, value):
        """Change one setting and apply it right away."""
        self.cfg.update(**{key: value})
        if key in ("text_size", "player_width", "theme"):
            self.root.after(10, self._rebuild)
            return
        if key == "opacity" and self._visible:
            try:
                self._set_alpha(float(value))
            except self.tk.TclError:
                pass
        self._layout_key = None
        self._tick_once(force=True)
        if key in ("rim_color", "glow_size", "pulse_with_voice"):
            self._preview_pulse()

    def _preview_pulse(self):
        """Two pulses in the chosen colour and glow size, so a change is visible without a voiceover."""
        rim = self.cfg.get("rim_color") or AMBER

        def step(t):
            k = abs(math.sin(2 * math.pi * t))
            self._glow_rings(k)
            self._set_shape(self._radius, mix(HAIR, rim, 0.25 + 0.75 * k))

        def done():
            self._level = -1  # the live loop redraws the real state
            self._glow_rings(0.0)
            self._set_shape(self._radius, HAIR)
            self._kick()
        self._anim("rimpreview", 1.4, step, ease=lambda t: t, done=done)

    def _settings_panel(self, force):
        c, p, Wp = self.c, self.p, self.Wp
        cfg = self.cfg
        key = ("settings", getattr(cfg, "version", 0), self.mode)
        self._text(self.pan_name, "Settings")
        c.itemconfigure(self.pan_arrow, state="hidden")
        if time.time() >= self._toast_until:
            self._text(self.pan_meta, "Changes save as you go")
            self._fill(self.pan_meta, MUTED)
        if key == self._layout_key and not force:
            return
        self._layout_key = key
        self._end_slides()
        c.itemconfigure("pan", state="normal")
        for tag in ("play", "ask", "libtoggle"):
            c.itemconfigure(tag, state="hidden")
        c.itemconfigure(self.pan_arrow, state="hidden")
        c.itemconfigure(self.lst_win, state="hidden")
        for ids in self.chips:
            for i in ids:
                c.delete(i)
        self.chips = []
        if self.root.focus_get() is self.entry:
            self.root.focus_set()
        c.delete("set")
        self._sliders = {}
        x0, x1 = p(PAD), Wp - p(PAD)
        y = p(PAD + ORB + 18)

        def eyebrow(text, y, link=None):
            c.create_line(x0, y, x0 + p(20), y, fill=ACC[0], tags=("set",))
            c.create_text(x0 + p(28), y, anchor="w", text=text, font=self.F["mono"], fill=EMBER, tags=("set",))
            if link:
                label, fn = link
                tag = f"setlink{text}"
                t = c.create_text(x1, y, anchor="e", text=label, font=self.F["mono"], fill=MUTED, tags=("set", tag))
                c.tag_bind(tag, "<Button-1>", lambda e: fn())
                c.tag_bind(tag, "<Enter>", lambda e: (self._tint(t, FG), c.configure(cursor="hand2")))
                c.tag_bind(tag, "<Leave>", lambda e: (self._tint(t, MUTED), c.configure(cursor="")))
            return y + p(22)

        def slider(key, title, stops, names, y):
            val = cfg.get(key)
            ix = stops.index(val) if val in stops else 0
            c.create_text(x0, y + p(8), anchor="w", text=title, font=self.F["label"], fill=BODY, tags=("set",))
            vt = c.create_text(x1, y + p(8), anchor="e", text=names[ix], font=self.F["mono"], fill=EMBER,
                               tags=("set",))
            ty = y + p(34)
            a, b = x0 + p(10), x1 - p(10)
            xs = [a + (b - a) * i / (len(stops) - 1) for i in range(len(stops))]
            tag = f"sl_{key}"
            c.tag_lower(c.create_rectangle(x0, ty - p(14), x1, ty + p(14), fill=CARD, width=0, tags=("set", "sethit", tag)),
                        self.glow_cap)  # hit area under the (mouse-transparent) ambient light
            c.create_line(a, ty, b, ty, fill=HAIR, width=max(1, p(3)), capstyle="round", tags=("set", tag))
            fill = c.create_line(a, ty, xs[ix], ty, fill=ACC[0], width=max(1, p(3)), capstyle="round",
                                 tags=("set", tag))
            dots = []
            for i, x in enumerate(xs):
                d = c.create_image(x, ty, image=self.art.circle(7, fill=Art._rgba(ACC[0] if i <= ix else HAIR)),
                                   tags=("set", tag))
                dots.append(d)
                c.create_text(x, ty + p(18), text=self._short(key, stops[i]), font=self.F["mono_s"],
                              fill=EMBER if i == ix else FAINT, tags=("set", tag))
            knob = c.create_image(xs[ix], ty, image=self.art.knob(), tags=("set", tag))
            self._sliders[key] = {"xs": xs, "stops": stops, "names": names, "knob": knob, "fill": fill,
                                  "dots": dots, "vt": vt, "a": a, "ty": ty, "ix": ix}
            c.tag_bind(tag, "<ButtonPress-1>", lambda e, k=key: self._slide(k, e.x))
            c.tag_bind(tag, "<B1-Motion>", lambda e, k=key: self._slide(k, e.x))
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key: self._slide_end(k))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
            return ty + p(34)

        def toggle(key, title, hint, y):
            on = bool(cfg.get(key))
            tag = f"tg_{key}"
            h = p(40) if hint else p(32)
            c.tag_lower(c.create_rectangle(x0 - p(6), y, x1 + p(6), y + h, fill=CARD, width=0, tags=("set", "sethit", tag)),
                        self.glow_cap)
            c.create_text(x0, y + p(11), anchor="w", text=title, font=self.F["label"], fill=BODY, tags=("set", tag))
            if hint:
                c.create_text(x0, y + p(29), anchor="w", text=hint, font=self.F["meta"], fill=FAINT, tags=("set", tag))
            sw, sh = p(36), p(20)
            img = self.art.pill(sw, sh, grad=ACC) if on else self.art.pill(sw, sh, outline=(*INK, 50))
            cy = y + p(11) if not hint else y + p(16)
            c.create_image(x1, cy, anchor="e", image=img, tags=("set", tag))
            kx = x1 - p(10) if on else x1 - sw + p(10)
            c.create_image(kx, cy, image=self.art.circle(14, fill=Art._rgba(SW_KNOB if on else MUTED)), tags=("set", tag))
            c.tag_bind(tag, "<Button-1>", lambda e: self._set(key, not on))
            c.tag_bind(tag, "<Enter>", lambda e: c.configure(cursor="hand2"))
            c.tag_bind(tag, "<Leave>", lambda e: c.configure(cursor=""))
            return y + h + p(6)

        def swatches(key, title, y):
            """Colour dots; the chosen one has a ring. Hover shows the colour's name."""
            cur = cfg.get(key)
            vt = c.create_text(x1, y + p(8), anchor="e", text=RIM_NAMES.get(cur, cur), font=self.F["mono"],
                               fill=EMBER, tags=("set",))
            c.create_text(x0, y + p(8), anchor="w", text=title, font=self.F["label"], fill=BODY, tags=("set",))
            y += p(24)
            d, gap = 22, p(10)
            dp = p(d)
            cols = list(RIM_NAMES)
            for n, col in enumerate(cols):
                cx = x0 + dp / 2 + p(3) + n * (dp + gap)
                cy = y + dp / 2 + p(3)
                tag = f"sw_{n}"
                if col == cur:
                    c.create_image(cx, cy, image=self.art.circle(d + 8, fill=(0, 0, 0, 0), outline=Art._rgba(col)),
                                   tags=("set", tag))
                c.create_image(cx, cy, image=self.art.circle(d, fill=Art._rgba(col)), tags=("set", tag))
                c.tag_bind(tag, "<Button-1>", lambda e, col=col: self._set(key, col))
                c.tag_bind(tag, "<Enter>", lambda e, col=col: (c.configure(cursor="hand2"),
                                                               c.itemconfigure(vt, text=RIM_NAMES[col])))
                c.tag_bind(tag, "<Leave>", lambda e: (c.configure(cursor=""),
                                                      c.itemconfigure(vt, text=RIM_NAMES.get(cfg.get(key), ""))))
            return y + dp + p(14)

        def themes(y):
            """Theme cards: a small swatch of each theme's card, text and light; the chosen one has a ring."""
            cur = cfg.get("theme")
            c.create_text(x0, y + p(8), anchor="w", text="Theme", font=self.F["label"], fill=BODY, tags=("set",))
            vt = c.create_text(x1, y + p(8), anchor="e", text=THEME_NAMES.get(cur, ""), font=self.F["mono"],
                               fill=EMBER, tags=("set",))
            y += p(24)
            gap = p(8)
            n = len(THEMES)
            tw = (x1 - x0 - gap * (n - 1)) / n
            th = p(46)
            for i, (name, t) in enumerate(THEMES.items()):
                tx = x0 + i * (tw + gap)
                tag = f"th_{name}"
                img = self.art.theme_card(int(tw), th, t, name == cur)
                c.create_image(tx, y, anchor="nw", image=img, tags=("set", tag))
                c.create_text(tx + p(10), y + th - p(12), anchor="w", text=THEME_NAMES[name], font=self.F["mono_s"],
                              fill=t["FG"], tags=("set", tag))
                c.tag_bind(tag, "<Button-1>", lambda e, v=name: self._set("theme", v))
                c.tag_bind(tag, "<Enter>", lambda e, v=name: (c.configure(cursor="hand2"),
                                                              c.itemconfigure(vt, text=THEME_NAMES[v])))
                c.tag_bind(tag, "<Leave>", lambda e: (c.configure(cursor=""),
                                                      c.itemconfigure(vt, text=THEME_NAMES.get(cfg.get("theme"), ""))))
            return y + th + p(16)

        def choice(key, title, opts, hints, y):
            """A row of pills, one selected (a setting with a few named values)."""
            cur = cfg.get(key)
            c.create_text(x0, y + p(8), anchor="w", text=title, font=self.F["label"], fill=BODY, tags=("set",))
            y += p(22)
            x, ch = x0, p(32)
            for v, label in opts:
                w = self.F["chip"].measure(label) + p(26)
                if x > x0 and x + w > x1:
                    x, y = x0, y + ch + p(8)
                sel = v == cur
                base = self.art.pill(w, ch, grad=ACC) if sel else self.art.pill(w, ch, outline=(*INK, 36))
                hover = self.art.pill(w, ch, grad=ACC_HI) if sel else self.art.pill(
                    w, ch, fill=(*INK, 14), outline=(*ACC_RGB, 120))
                tag = f"ch_{key}_{v}"
                img = c.create_image(x, y, anchor="nw", image=base, tags=("set", tag))
                c.create_text(x + w / 2, y + ch / 2, text=label, font=self.F["chip"], fill=ACC_INK if sel else FG,
                              tags=("set", tag))
                c.tag_bind(tag, "<Button-1>", lambda e, v=v: self._set(key, v))
                c.tag_bind(tag, "<Enter>", lambda e, i=img, h=hover: (c.itemconfigure(i, image=h), c.configure(cursor="hand2")))
                c.tag_bind(tag, "<Leave>", lambda e, i=img, b=base: (c.itemconfigure(i, image=b), c.configure(cursor="")))
                x += w + p(8)
            y += ch + p(8)
            if hints.get(cur):
                c.create_text(x0, y, anchor="nw", text=hints[cur], font=self.F["meta"], fill=FAINT, width=x1 - x0,
                              tags=("set",))
                y += p(18)
            return y + p(6)

        # appearance
        y = eyebrow("APPEARANCE", y)
        y = themes(y)
        y = slider("text_size", "Text size", [0.85, 1.0, 1.15, 1.3, 1.5],
                   [SIZE_NAMES[v] for v in (0.85, 1.0, 1.15, 1.3, 1.5)], y)
        y = slider("player_width", "Player width", list(WIDTH_NAMES), list(WIDTH_NAMES.values()), y)
        y = slider("opacity", "Opacity", [0.7, 0.8, 0.9, 1.0], ["70%", "80%", "90%", "100%"], y)
        y = swatches("rim_color", "Pulse color", y)
        y = slider("glow_size", "Glow size", [0, 6, 12, 18, 24], ["Rim only", "Small", "Medium", "Large", "Largest"], y)
        y = toggle("pulse_with_voice", "Pulse with the voice", "Off: a slow, steady breath while speaking", y)
        y = toggle("reduce_motion", "Reduce motion", "Panels change at once; fades stay", y)
        y += p(14)

        # voice
        y = eyebrow("VOICE", y, ("Play a sample", self._preview))
        voices = list(VOICE_NAMES)
        cur_v = cfg.get("voice")
        x, ch = x0, p(32)
        for v in voices:
            label = VOICE_NAMES[v]
            w = self.F["chip"].measure(label) + p(26)
            if x > x0 and x + w > x1:
                x, y = x0, y + ch + p(8)
            sel = v == cur_v
            base = self.art.pill(w, ch, grad=ACC) if sel else self.art.pill(w, ch, outline=(*INK, 36))
            hover = self.art.pill(w, ch, grad=ACC_HI) if sel else self.art.pill(
                w, ch, fill=(*INK, 14), outline=(*ACC_RGB, 120))
            tag = f"vc_{v}"
            img = c.create_image(x, y, anchor="nw", image=base, tags=("set", tag))
            c.create_text(x + w / 2, y + ch / 2, text=label, font=self.F["chip"], fill=ACC_INK if sel else FG,
                          tags=("set", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, v=v: (self._set("voice", v), self._preview()))
            c.tag_bind(tag, "<Enter>", lambda e, i=img, h=hover: (c.itemconfigure(i, image=h), c.configure(cursor="hand2")))
            c.tag_bind(tag, "<Leave>", lambda e, i=img, b=base: (c.itemconfigure(i, image=b), c.configure(cursor="")))
            x += w + p(8)
        y += ch + p(14)
        y = slider("speed", "Speaking speed", [0.9, 1.0, 1.1, 1.25, 1.4], ["0.9\u00d7", "Normal", "1.1\u00d7",
                                                                          "1.25\u00d7", "1.4\u00d7"], y)
        y = toggle("say_source", "Say which project first", "\u201cFrom agentic os v2.\u201d before each reply", y)
        y = toggle("summaries", "Summarize replies", "Off: read a cleaned-up excerpt instead", y)
        y = toggle("speak_background", "Speak background runs", "Batch and headless runs (always in history)", y)
        y += p(14)

        # behavior
        y = eyebrow("BEHAVIOR", y)
        y = toggle("auto_open_calls", "Open the panel when a call arrives", None, y)
        y = toggle("hide_when_idle", "Hide when nothing is playing", "Comes back with the next reply or call", y)
        y = slider("calls_hours", "Keep calls waiting for", [6, 12, 24, 72, 168], ["6 hours", "12 hours", "1 day", "3 days", "1 week"], y)
        y = choice("desktop_answers", "Answers to Claude app threads",
                   [("send", "Type and send"), ("paste", "Paste only"), ("background", "Background")],
                   {"send": "Typed into the thread and sent, so it shows there",
                    "paste": "Typed into the thread; you press Enter",
                    "background": "Resumed out of sight; the app's thread won't show it"}, y)

        # footer
        y += p(10)
        c.create_line(x0, y, x1, y, fill=HAIR, tags=("set",))
        fy = y + p(20)
        for label, fn, anchor, x in (("Restore defaults", self._restore_defaults, "w", x0),
                                     ("Reset position", self._reset_position, "e", x1)):
            tag = f"ft{label}"
            t = c.create_text(x, fy, anchor=anchor, text=label, font=self.F["link"], fill=EMBER, tags=("set", tag))
            c.tag_bind(tag, "<Button-1>", lambda e, fn=fn: fn())
            c.tag_bind(tag, "<Enter>", lambda e, t=t: (self._tint(t, FG), c.configure(cursor="hand2")))
            c.tag_bind(tag, "<Leave>", lambda e, t=t: (self._tint(t, EMBER), c.configure(cursor="")))
        y = fy + p(16) + p(R_PANEL)
        c.tag_raise("set")
        c.tag_lower("sethit", self.glow_cap)  # hit areas stay under the ambient light
        y = self._set_scroll_setup(y)
        c.tag_raise("orb")
        for b in self.btn.values():
            c.tag_raise(b["tag"])
        for i in self.corner:
            c.tag_raise(i)
        c.itemconfigure("glowpan", state="disabled")
        self._place_buttons()
        self._fill(self.btn["gear"]["g"], self._btn_color("gear"))
        self._resize(y)

    def _set_scroll_setup(self, total: int) -> int:
        """When Settings is taller than the screen allows, the body scrolls under a fixed header and a fixed,
        rounded bottom. Returns the height to show."""
        c, p, Wp = self.c, self.p, self.Wp
        c.delete("setmask")
        room = self._room()
        self._set_max = max(0, total - room)
        if not self._set_max:
            self._set_off = 0
            return total
        top, bot = p(PAD + ORB + 8), room - p(R_PANEL)
        self._set_band = (top, bot)
        off, self._set_off = min(self._set_off, self._set_max), 0
        # header: the card and its ambient light, cropped to the header band
        c.create_rectangle(0, 0, Wp, top, fill=CARD, width=0, tags=("setmask", "bg"))
        src = self.art.glow(Wp, p(240), "pan")
        key = ("glowtop", Wp, top, str(src))
        if key not in self.art._cache:
            im = self.tk.PhotoImage(width=Wp, height=top)
            im.tk.call(im, "copy", str(src), "-from", 0, 0, Wp, top, "-to", 0, 0)
            self.art._cache[key] = im
        c.create_image(0, 0, anchor="nw", image=self.art._cache[key], tags=("setmask", "bg"))
        c.create_line(p(PAD), top - 1, Wp - p(PAD), top - 1, fill=HAIR, tags=("setmask", "setedge"))
        c.create_rectangle(0, bot, Wp, room, fill=CARD, width=0, tags=("setmask", "bg"))
        c.create_line(p(PAD), bot, Wp - p(PAD), bot, fill=HAIR, tags=("setmask", "setedge"))
        self._set_bar = c.create_line(0, 0, 0, 0, fill=MUTED, width=max(2, p(3)), capstyle="round",
                                      tags=("setmask",), state="hidden")
        c.tag_raise("setmask")
        for it in (self.pan_name, self.pan_meta, self.rim_l, self.rim_r, self.rim_b, self.rim_top):
            c.tag_raise(it)
        c.tag_raise("ring")
        self._set_scroll(off)
        return room

    def _set_scroll(self, to: float):
        """Move the settings body to offset `to` (clamped) and redraw the thin scrollbar."""
        to = max(0, min(self._set_max, int(to)))
        d = to - self._set_off
        if d:
            self.c.move("set", 0, -d)
            self._set_off = to
        if self._set_max:
            p, (top, bot) = self.p, self._set_band
            span = bot - top - p(16)
            seen = span * span / (span + self._set_max)
            y0 = top + p(8) + (span - seen) * to / self._set_max
            x = self.Wp - p(7)
            self.c.coords(self._set_bar, x, y0, x, y0 + seen)
            self.c.itemconfigure(self._set_bar, state="disabled" if self._set_bar_on else "hidden")

    def _on_wheel(self, e):
        if self.view != "settings" or not getattr(self, "_set_max", 0):
            return
        self._set_bar_on = True
        step = -e.delta / 120 * self.p(48)
        start, end = self._set_off, max(0, min(self._set_max, self._set_off + step + self._set_goal_left()))
        self._set_goal = end
        self._anim("setscroll", 0.18, lambda t: self._set_scroll(start + (end - start) * t), ease=ease_out,
                   done=self._bar_later)

    def _set_goal_left(self):
        # wheel ticks during a scroll add up instead of restarting from where the motion is now
        if "setscroll" in self._anims and getattr(self, "_set_goal", None) is not None:
            return self._set_goal - self._set_off
        return 0

    def _bar_later(self):
        if self._bar_job:
            self.root.after_cancel(self._bar_job)

        def off():
            self._bar_job = None
            self._set_bar_on = False
            if getattr(self, "_set_bar", None):
                try:
                    self.c.itemconfigure(self._set_bar, state="hidden")
                except self.tk.TclError:
                    pass
        self._bar_job = self.root.after(900, off)

    @staticmethod
    def _short(key, v):
        if key == "text_size":
            return {0.85: "S", 1.0: "M", 1.15: "L", 1.3: "XL", 1.5: "XXL"}[v]
        if key == "glow_size":
            return {0: "off", 6: "S", 12: "M", 18: "L", 24: "XL"}[v]
        if key == "opacity":
            return f"{int(v * 100)}"
        if key == "speed":
            return f"{v:g}\u00d7"
        if key == "calls_hours":
            return "3d" if v == 72 else f"{v}h"
        if key == "player_width":
            return {0.85: "N", 1.0: "M", 1.2: "W", 1.4: "XW"}[v]
        return str(v)

    def _slide(self, key, x):
        sl = self._sliders.get(key)
        if not sl:
            return
        c = self.c
        xs = sl["xs"]
        ix = min(range(len(xs)), key=lambda i: abs(xs[i] - x))
        kx = min(max(x, xs[0]), xs[-1])
        c.coords(sl["knob"], kx, (sl["ty"] - self._set_off))
        c.coords(sl["fill"], sl["a"], (sl["ty"] - self._set_off), kx, (sl["ty"] - self._set_off))
        c.itemconfigure(sl["vt"], text=sl["names"][ix])
        for i, d in enumerate(sl["dots"]):
            c.itemconfigure(d, image=self.art.circle(7, fill=Art._rgba(ACC[0] if xs[i] <= kx + 1 else HAIR)))
        sl["ix"] = ix

    def _slide_end(self, key):
        sl = self._sliders.get(key)
        if not sl:
            return
        ix, xs = sl["ix"], sl["xs"]
        x0 = self.c.coords(sl["knob"])[0]

        def snap(t):
            x = x0 + (xs[ix] - x0) * t
            self.c.coords(sl["knob"], x, (sl["ty"] - self._set_off))
            self.c.coords(sl["fill"], sl["a"], (sl["ty"] - self._set_off), x, (sl["ty"] - self._set_off))
        self._anim("snap", 0.12, snap, done=lambda: (
            self._set(key, sl["stops"][ix]) if sl["stops"][ix] != self.cfg.get(key) else None))

    def _preview(self):
        if hasattr(self.vq, "preview"):
            if self.vq.muted:
                self.vq.set_muted(False)
            self.vq.preview()
            self._toast("Playing a sample")

    def _restore_defaults(self):
        size = self._look()
        self.cfg.reset()
        if self._look() != size:
            self.root.after(10, self._rebuild)
            return
        try:
            self._set_alpha(self._alpha())
        except self.tk.TclError:
            pass
        self._layout_key = None
        self._tick_once(force=True)

    def _reset_position(self):
        x = self.root.winfo_screenwidth() - self.Wp - self.p(24)
        self._xy = (x, self.p(24))
        self.root.geometry(f"+{x}+{self.p(24)}")

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
        st = it.get("status", "")
        label = STATUS_TAGS.get(st, "" if st in ("spoken", "queued", "") else st.split(":")[0][:14])
        if it.get("answer"):
            label = "answered"
        elif it.get("decision") and not it.get("dismissed") and kind == "history":
            label = "your call"
        room = w - nx - p(32) - (self.F["mono_s"].measure(label.upper()) + p(8) if label else 0)
        name = self._fit(self._where(it), self.F["row"], max(p(60), room))
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
            answers = it.get("answers")
            text = ("You answered:\n" + "\n".join(f"{n}. {a or 'Skipped (the agent decides)'}"
                                                   for n, a in enumerate(answers, 1))
                    if answers else f"You answered: {it['answer']}")
            t3 = l.create_text(p(12), y, anchor="nw", text=text, font=self.F["snip"], fill=EMBER, width=w - p(24))
            bb = l.bbox(t3)
            y = (bb[3] if bb else y + p(14)) + p(8)
        for i, (text, fn) in enumerate(links):
            tag = f"lnk{it['id']}{i}"
            item = l.create_text(x, y, anchor="nw", text=text, font=self.F["link"], fill=EMBER, tags=(tag,))
            l.tag_bind(tag, "<Button-1>", lambda e, fn=fn: fn())
            l.tag_bind(tag, "<Enter>", lambda e, i=item: (l.itemconfigure(i, fill=FG), l.configure(cursor="hand2")))
            l.tag_bind(tag, "<Leave>", lambda e, i=item: (l.itemconfigure(i, fill=EMBER), l.configure(cursor="")))
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
