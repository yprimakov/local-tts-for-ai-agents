"""Voice queue tests: stdlib only, no model, no audio device.  python -m unittest test_voiced"""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

os.environ.setdefault("VOICED_DATA", tempfile.mkdtemp())
import voiced  # noqa: E402


class FakePlayer:
    log: list = []
    live = 0
    max_live = 0
    lock = threading.Lock()

    def __init__(self, wav, secs=0.15):
        self.text = Path(wav).read_text(encoding="utf-8")
        self.secs, self.elapsed, self.playing, self.ended = secs, 0.0, False, False
        with FakePlayer.lock:
            FakePlayer.live += 1
            FakePlayer.max_live = max(FakePlayer.max_live, FakePlayer.live)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.ended and self.elapsed < 5:
            time.sleep(0.01)
            if self.playing:
                self.elapsed += 0.01
                if self.elapsed >= self.secs:
                    self.ended = True
                    FakePlayer.log.append(self.text)

    def play(self): self.playing = True
    def pause(self): self.playing = False
    position = property(lambda s: s.elapsed)
    duration = property(lambda s: s.secs)

    def close(self):
        self.ended = True
        with FakePlayer.lock:
            FakePlayer.live -= 1


def fake_synth(text):
    fd, p = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    Path(p).write_text(text, encoding="utf-8")
    return p


def wait(pred, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class QueueTests(unittest.TestCase):
    def setUp(self):
        FakePlayer.log, FakePlayer.live, FakePlayer.max_live = [], 0, 0
        self.dir = Path(tempfile.mkdtemp())
        self.h = voiced.History(self.dir / "history.jsonl")
        self.vq = voiced.VoiceQueue(self.h, fake_synth, FakePlayer)
        threading.Thread(target=self.vq.run, daemon=True).start()

    def tearDown(self):
        self.vq.stop()

    def test_fifo_one_at_a_time_with_source(self):
        for i in range(3):
            self.vq.say(f"reply {i}", cwd=str(self.dir), entrypoint="cli")
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 3))
        self.assertEqual(FakePlayer.max_live, 1)  # never two voices at once
        self.assertEqual([t.split(". ", 1)[1] for t in FakePlayer.log], ["reply 0", "reply 1", "reply 2"])
        self.assertTrue(FakePlayer.log[0].startswith("From "))
        self.assertTrue(wait(lambda: all(r["status"] == "spoken" for r in self.h.recent())))

    def test_headless_is_logged_not_spoken(self):
        it = self.vq.say("Can't open settings. Headless run.", cwd="C:/x/Temp/pytest-of-y/pytest-1/v",
                         session_id="abc", entrypoint="sdk-cli")
        self.assertEqual(it["status"], "not spoken: headless run")
        self.assertEqual(it["project"], "pytest run")
        time.sleep(0.2)
        self.assertEqual(FakePlayer.log, [])
        self.assertEqual(self.h.recent()[0]["session_id"], "abc")

    def test_pause_holds_queue_and_skip_moves_on(self):
        self.vq.pause()
        self.vq.say("first one", entrypoint="cli")
        self.vq.say("second one", entrypoint="cli")
        time.sleep(0.3)
        self.assertEqual(FakePlayer.log, [])
        self.assertEqual(len(self.vq.state()["queue"]), 2)
        self.vq.resume()
        self.assertTrue(wait(lambda: self.vq.state()["current"] is not None))
        self.vq.skip()
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 1))
        self.assertIn("second one", FakePlayer.log[0])
        statuses = {r["text"]: r["status"] for r in self.h.recent()}
        self.assertTrue(wait(lambda: {r["text"]: r["status"] for r in self.h.recent()}["first one"] == "skipped"))

    def test_mute_drops_queue_into_history(self):
        self.vq.pause()
        self.vq.say("a", entrypoint="cli")
        self.vq.set_muted(True)
        b = self.vq.say("b", entrypoint="cli")
        self.assertEqual(b["status"], "not spoken: muted")
        self.assertEqual(self.vq.state()["queue"], [])
        self.vq.set_muted(False)
        self.vq.resume()
        self.vq.say("c", entrypoint="cli")
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 1))
        self.assertIn("c", FakePlayer.log[0])

    def test_replay_jumps_the_queue(self):
        first = self.vq.say("old news", entrypoint="cli")
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 1))
        self.vq.pause()
        self.vq.say("newer", entrypoint="cli")
        self.vq.replay(first["id"])
        self.vq.resume()
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 3))
        self.assertIn("old news", FakePlayer.log[1])
        self.assertEqual(len(self.h.recent()), 2)  # a replay is not a new history row

    def test_history_keeps_seven_days(self):
        p = self.dir / "old.jsonl"
        now = time.time()
        rows = [{"id": "a", "ts": now - 8 * 86400, "text": "old"}, {"id": "b", "ts": now - 86400, "text": "new"},
                {"id": "b", "status": "spoken"}]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        h = voiced.History(p)
        self.assertEqual([(r["id"], r.get("status")) for r in h.recent()], [("b", "spoken")])
        self.assertEqual(len(p.read_text(encoding="utf-8").splitlines()), 1)


class SourceTests(unittest.TestCase):
    def test_git_root_name(self):
        d = Path(tempfile.mkdtemp()) / "agentic-os-v2"
        (d / ".git").mkdir(parents=True)
        (d / "bridge").mkdir()
        s = voiced.source_of(str(d / "bridge"), "cli")
        self.assertEqual((s["project"], s["headless"]), ("agentic-os-v2", False))
        self.assertEqual(voiced.spoken_text({"spoken": s["spoken"], "text": "Done."}), "From agentic os v2. Done.")

    def test_headless_and_temp(self):
        self.assertTrue(voiced.source_of("C:/a", "sdk-cli")["headless"])
        self.assertEqual(voiced.source_of(r"C:\Users\y\AppData\Local\Temp\x")["project"], "temp folder")


class ApiTests(unittest.TestCase):
    def test_local_api(self):
        h = voiced.History(Path(tempfile.mkdtemp()) / "h.jsonl")
        vq = voiced.VoiceQueue(h, fake_synth, FakePlayer)
        srv = voiced.make_server(vq, 0)
        self.assertEqual(srv.server_address[0], "127.0.0.1")
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def post(path, body, headers=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                         headers={"content-type": "application/json", **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=2) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, None
        try:
            self.assertEqual(post("/say", {"text": "hi there", "entrypoint": "cli"})[0], 200)
            self.assertEqual(post("/say", {"text": "x"}, {"origin": "https://evil.example"})[0], 403)
            self.assertEqual(post("/control", {"action": "pause"})[1]["paused"], True)
            self.assertEqual(post("/control", {"action": "bogus"})[0], 400)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/history", timeout=2) as r:
                self.assertEqual(json.loads(r.read())[0]["text"], "hi there")
        finally:
            srv.shutdown()
            vq.stop()


if __name__ == "__main__":
    unittest.main()
