"""Every theme through the real player: panel, settings (and its scroll), capsule hover, minimize, restore.
Run: venv\\Scripts\\python.exe -m unittest test_player_themes"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("VOICED_DATA", tempfile.mkdtemp())
import player  # noqa: E402
import voiced  # noqa: E402


class FakeAudio:
    def __init__(self, wav, secs=60):
        self.secs, self.elapsed, self.playing, self.ended = secs, 0.0, False, False

    def play(self): self.playing = True
    def pause(self): self.playing = False
    position = property(lambda s: s.elapsed)
    duration = property(lambda s: s.secs)
    def close(self): self.ended = True


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(x, y):
    a, b = sorted((lum(x), lum(y)))
    return (b + 0.05) / (a + 0.05)


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = Path(tempfile.mkdtemp())
        h = voiced.History(d / "h.jsonl")
        cls.st = voiced.Settings(d / "settings.json")
        vq = voiced.VoiceQueue(h, lambda t: str(d / "x.wav"), FakeAudio, settings=cls.st)
        h.add({"id": "c1", "ts": time.time() - 60, "cwd": "C:/x/demo", "project": "demo", "session_id": "s",
               "entrypoint": "claude-desktop", "status": "spoken", "text": "Two choices are left.",
               "decisions": [{"question": "Which address?", "options": ["Studio", "Support"], "recommended": 0},
                             {"question": "Launch today?", "options": ["Yes", "Wait"], "recommended": 1}],
               "decision": {"question": "Which address?", "options": ["Studio", "Support"], "recommended": 0}})
        api = SimpleNamespace(log=lambda m: None, open_session=lambda *a, **k: "", resume_session=lambda *a, **k: False,
                              desktop_app_id=lambda *a, **k: None, type_into_desktop=lambda *a, **k: 0,
                              desktop_result={})
        cls.w = player.Widget(vq, api)

    @classmethod
    def tearDownClass(cls):
        cls.w.quit()

    def pump(self, secs=0.35):
        end = time.time() + secs
        while time.time() < end:
            self.w.root.update()
            time.sleep(0.01)

    def test_every_theme_draws_every_view(self):
        w = self.w
        for name in player.THEMES:
            with self.subTest(theme=name):
                self.st.update(theme=name)
                w._rebuild()
                self.assertEqual(player.CARD, player.THEMES[name]["CARD"])
                w._set_mode("panel")
                w._tick_once(force=True)
                self.pump()
                self.assertGreater(len(w.chips), 0)
                # the title font is the theme's (or its fallback), never Tk's default
                self.assertNotEqual(w.F["title"].actual("family"), "TkDefaultFont")
                w._toggle_settings()
                self.pump()
                self.assertEqual(w.view, "settings")
                # a short screen: the body scrolls, the panel stays inside the room
                w._room = lambda: w.p(520)
                w._layout_key = None
                w._tick_once(force=True)
                self.pump()
                self.assertGreater(w._set_max, 0)
                self.assertLessEqual(w._target_h, w.p(520))
                w._set_scroll(10 ** 6)
                self.assertEqual(w._set_off, w._set_max)
                del w._room
                w._toggle_settings()
                w._set_mode("cap")
                w._set_cap_hover(True)
                self.assertEqual(w.c.itemcget(w.btn["mini"]["g"], "state"), "normal")
                w._set_cap_hover(False)
                self.pump(0.1)
                w.minimize()
                self.pump()
                self.assertIsNotNone(w._mini)
                l, t, r, b = w._work_area(w._mini.cx, w._mini.cy)
                edge = min(w._mini.cx - l, r - w._mini.cx)
                self.assertLessEqual(edge, w._mini.ball)          # docked at a side edge
                xy = w._xy
                w.restore()
                self.pump()
                self.assertIsNone(w._mini)
                self.assertEqual(w._xy, xy)                       # back exactly where it was

    def test_hover_tint_eases_and_press_dips(self):
        w = self.w
        w._set_mode("panel")
        w._tick_once(force=True)
        self.pump()
        item = w.call_dismiss
        w.c.itemconfigure(item, fill=player.FAINT)
        w._tint(item, player.FG)
        self.assertNotEqual(w.c.itemcget(item, "fill"), player.FG)   # not a jump
        self.pump(0.3)
        self.assertEqual(w.c.itemcget(item, "fill").lower(), player.FG.lower())
        x, y = w.c.coords(item)
        w.c.configure(cursor="hand2")
        w.c.addtag_withtag("current", item)
        w._press_fx(None)
        self.assertEqual(w.c.coords(item)[1], y + 1)
        w._release_fx(None)
        self.pump(0.3)
        self.assertAlmostEqual(w.c.coords(item)[1], y, places=1)
        w.c.dtag(item, "current")
        w.c.configure(cursor="")

    def test_theme_colours_are_readable(self):
        for name, t in player.THEMES.items():
            with self.subTest(theme=name):
                for fg in ("FG", "BODY", "MUTED", "FAINT", "EMBER"):
                    self.assertGreaterEqual(ratio(t[fg], t["CARD"]), 4.5, fg)
                for end in t["ACC"] + t["ACC_HI"]:
                    self.assertGreaterEqual(ratio(t["ACC_INK"], end), 4.5)
                    self.assertGreaterEqual(ratio(t["SW_KNOB"], end), 1.3)  # the switch knob stands out
                self.assertEqual(len(player.THEME_FONTS[name]), 5)


if __name__ == "__main__":
    unittest.main()
