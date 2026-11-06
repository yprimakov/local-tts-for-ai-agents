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


class SpeechTests(unittest.TestCase):
    def test_machine_output_detected(self):
        self.assertTrue(voiced.looks_machine('{"messages":[{"sender":"Boris","to":"Matt"}]}'))
        self.assertTrue(voiced.looks_machine("Traceback (most recent call last):"))
        self.assertFalse(voiced.looks_machine("I sent the draft to Gmail. You only need to review it."))

    def test_plain_speech_strips_code(self):
        t = voiced.plain_speech("Done. Ran `pytest -q` in C:\\x\\y.py.\n```json\n{\"a\":1}\n```\nYou need to approve the PR.")
        self.assertNotIn("{", t)
        self.assertNotIn("pytest", t)
        self.assertIn("approve the PR", t)

    def test_parse_summary(self):
        raw = 'Here: {"speak": true, "summary": "I fixed the login. Pick a backup option.", "decision": ' \
              '{"question": "Which backup?", "options": ["Nightly to Mac", "NAS only"], "recommended": 0}}'
        d = voiced.parse_summary(raw)
        self.assertEqual(d["decision"]["options"], ["Nightly to Mac", "NAS only"])
        self.assertEqual(d["decision"]["recommended"], 0)
        self.assertIsNone(voiced.parse_summary("no json"))
        bad = voiced.parse_summary('{"summary": "ok", "decision": {"question": "q", "options": ["one"], "recommended": 5}}')
        self.assertIsNone(bad["decision"])


class SummaryQueueTests(unittest.TestCase):
    def setUp(self):
        FakePlayer.log, FakePlayer.live, FakePlayer.max_live = [], 0, 0
        self.h = voiced.History(Path(tempfile.mkdtemp()) / "h.jsonl")
        self.calls = []

        def summarize(item):
            self.calls.append(item["message"])
            time.sleep(0.05)
            if item["message"].startswith("{"):
                return {"speak": False, "summary": "Raw email data.", "decision": None}
            return {"speak": True, "summary": "Summary of " + item["message"][:10], "decision":
                    {"question": "Go?", "options": ["Yes", "No"], "recommended": 0} if "?" in item["message"] else None}
        self.vq = voiced.VoiceQueue(self.h, fake_synth, FakePlayer, summarizer=summarize)
        threading.Thread(target=self.vq.run, daemon=True).start()
        threading.Thread(target=self.vq.summarize_loop, daemon=True).start()

    def tearDown(self):
        self.vq.stop()

    def test_speaks_summary_not_raw(self):
        self.vq.say("raw blurb", cwd="C:/a", message="First reply with lots of detail", entrypoint="cli")
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 1))
        self.assertIn("Summary of First repl", FakePlayer.log[0])
        self.assertNotIn("raw blurb", FakePlayer.log[0])

    def test_machine_output_is_not_spoken(self):
        it = self.vq.say("x", cwd="C:/a", message='{"messages": []}', entrypoint="cli")
        self.vq.say("y", cwd="C:/b", message="A normal reply", entrypoint="cli")
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 1))
        self.assertIn("A normal r", FakePlayer.log[0])
        self.assertTrue(wait(lambda: self.h.get(it["id"])["status"] == "not spoken: nothing to say"))

    def test_decision_kept(self):
        it = self.vq.say("x", cwd="C:/a", message="Should I merge?", entrypoint="cli")
        self.assertTrue(wait(lambda: (self.h.get(it["id"]) or {}).get("decision") is not None))
        self.assertEqual(self.h.get(it["id"])["decision"]["options"], ["Yes", "No"])

    def test_burst_from_one_folder_is_grouped(self):
        self.vq.pause()
        for i in range(8):
            self.vq.say("x", cwd="C:/p/_timelog", session_id=f"s{i}", message=f"Run {i} done", entrypoint="cli")
        self.assertEqual(len(self.vq.state()["queue"]), 3)
        last = self.vq.state()["queue"][-1]
        self.assertEqual(last["group_count"], 6)
        self.vq.resume()
        self.assertTrue(wait(lambda: len(FakePlayer.log) == 3, 5))
        self.assertIn("6 replies", FakePlayer.log[-1])

    def test_headless_flag_from_hook(self):
        it = self.vq.say("x", cwd="C:/a", message="hi there friend", entrypoint="claude-desktop", headless=True)
        self.assertEqual(it["status"], "not spoken: headless run")


if __name__ == "__main__":
    unittest.main()
