"""Keeps the player's calls and Ember's Warden cards as one queue. The Warden on the Mac is the store of record.

Every sweep: a call from this PC becomes a card; an answer here closes the card; an answer in Ember counts as an answer
here and resumes the session; Ember's own decision cards show in the player. When the Mac cannot be reached (Tailscale
off, Mac asleep) the player keeps working alone and the next sweep catches up.
Set the bridge address in VOICED_EMBER_URL or in ember_url.txt beside this file. Without one, sync stays off.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from voiced import answer_call, open_calls

_URL_FILE = Path(__file__).with_name("ember_url.txt")  # gitignored: this machine's private bridge address
URL = os.environ.get("VOICED_EMBER_URL", _URL_FILE.read_text().strip() if _URL_FILE.exists() else "").rstrip("/")
EVERY = 30  # seconds


def http(method: str, url: str, body: dict | None = None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=8, context=ssl.create_default_context()) as r:
        return json.loads(r.read() or b"{}")


class EmberSync:
    def __init__(self, vq, resume, log=print, url: str = URL, call=http):
        self.vq, self.resume, self.log, self.url, self.call = vq, resume, log, url, call
        self.remote_calls: list[dict] = []
        self.up: bool | None = None

    def run(self) -> None:
        while self.url and not self.vq._stop:
            self.safe_sweep()
            time.sleep(EVERY)

    def safe_sweep(self) -> None:
        try:
            self.sweep()
            up = True
        except (OSError, ValueError) as e:  # URLError and HTTPError are OSError
            up = False
            if self.up is not False:
                self.log(f"ember sync: {e!r}")
        if up != self.up:
            self.log(f"ember sync: {'connected' if up else 'offline'}")
        self.up = up

    def _decide(self, card: dict, text: str | None) -> None:
        """Close a card with Yury's answer, or as handled here when text is None."""
        opt = next((o["id"] for o in card["detail"].get("options", []) if o.get("label") == text), None)
        body = {"decision": "approve", "choice": opt} if opt else {"decision": "deny", "note": text or "Closed on the PC"}
        try:
            self.call("POST", f"{self.url}/needs/{card['id']}/decide", body)
        except urllib.error.HTTPError as e:
            if e.code != 409:  # 409: decided in Ember meanwhile; the next sweep reads it
                raise

    def sweep(self) -> None:
        rows = self.vq.history.recent(150)
        still_open = {r["id"] for r in open_calls(rows)}
        cards = {c["detail"]["source_id"]: c for c in self.call("GET", f"{self.url}/needs/external")["cards"]}
        for row in reversed(rows):
            for ix, d in enumerate(row.get("decisions") or ([row["decision"]] if row.get("decision") else [])):
                self._sync_one(row, ix, d, cards.get(f"{row['id']}#{ix}"), row["id"] in still_open)
        self.remote_calls = [self._as_call(a) for a in self.call("GET", f"{self.url}/needs")["approvals"]
                             if a["kind"] == "decision" and a["detail"].get("source") != "pc" and a.get("summary")]

    def _sync_one(self, row: dict, ix: int, d: dict, card: dict | None, is_open: bool) -> None:
        answer = d.get("answer") or (row.get("answer") if not row.get("decisions") else None)
        if card is None:
            if is_open and not answer:
                self.call("POST", f"{self.url}/needs/external", {
                    "source_id": f"{row['id']}#{ix}", "question": d["question"], "options": d["options"],
                    "recommended": d.get("recommended"), "project": row.get("project") or "",
                    "session_id": row.get("session_id") or ""})
        elif card["state"] == "pending":
            if answer or not is_open:
                self._decide(card, answer)
        elif is_open and not answer:  # answered in Ember
            if card["state"] == "denied":
                self.vq.history.update(row["id"], dismissed=True)
                return
            label = next((o["label"] for o in card["detail"]["options"] if o["id"] == card["choice"]), card["choice"])
            full = answer_call(self.vq.history, row["id"], label, ix)
            if full is not None:
                self.resume(self.vq, row, full)

    @staticmethod
    def _as_call(a: dict) -> dict:
        opts = a["detail"].get("options") or []
        rec = next((i for i, o in enumerate(opts) if o.get("id") == a["detail"].get("recommended")), None)
        return {"id": f"ember-{a['id']}", "remote": True, "card": a, "ts": a["ts"],
                "project": a["detail"].get("project") or "Ember", "text": a["summary"],
                "decision": {"question": a["summary"], "recommended": rec,
                             "options": [o.get("label") or o["id"] for o in opts]}}

    def answer_remote(self, item: dict, text: str) -> None:
        card = item["card"]
        opts = {o.get("label") or o["id"]: o["id"] for o in card["detail"].get("options", [])}
        try:
            self.call("POST", f"{self.url}/needs/{card['id']}/decide", {"decision": "approve", "choice": opts[text]})
        except (OSError, ValueError, KeyError) as e:
            self.log(f"ember sync: answer failed: {e!r}")
