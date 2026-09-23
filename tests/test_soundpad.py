"""Test di soundpad. Solo libreria standard: `python -m unittest` dalla cartella del progetto."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from soundpad.daemon import ALERT_KEY, CLEAR_KEY, Board, load_config, make_handler, next_state
from soundpad.install_hooks import EVENTS, default_url, install, uninstall
from soundpad.launchpad import Led, SimLaunchpad, key_for_note, note_for

URL = "http://127.0.0.1:47800/event"


def ev(name: str, sid: str = "A", **extra) -> dict:
    return {"session_id": sid, "hook_event_name": name, **extra}


class NextStateTest(unittest.TestCase):
    def test_basic_transitions(self):
        self.assertEqual(next_state(ev("SessionStart"), None), "starting")
        self.assertEqual(next_state(ev("UserPromptSubmit"), "starting"), "working")
        self.assertEqual(next_state(ev("PermissionRequest"), "working"), "needs_permission")
        self.assertEqual(next_state(ev("PreCompact"), "working"), "compacting")
        self.assertEqual(next_state(ev("Stop"), "working"), "done")
        self.assertEqual(next_state(ev("StopFailure"), "working"), "error")
        self.assertIsNone(next_state(ev("SessionEnd"), "done"))

    def test_idle_prompt(self):
        idle = ev("Notification", notification_type="idle_prompt")
        self.assertEqual(next_state(idle, "done"), "idle")
        self.assertEqual(next_state(idle, None), "idle")
        self.assertEqual(next_state(idle, "seen"), "seen")  # già visto: non torna a lampeggiare
        self.assertEqual(next_state(idle, "working"), "working")

    def test_permission_prompt_notification(self):
        n = ev("Notification", notification_type="permission_prompt")
        self.assertEqual(next_state(n, "working"), "needs_permission")

    def test_unknown_event_keeps_state(self):
        self.assertEqual(next_state(ev("Boh"), "working"), "working")
        self.assertEqual(next_state(ev("Boh"), None), "starting")


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.pad = SimLaunchpad(quiet=True)
        self.board = Board(self.pad, load_config(None))

    def press(self, row: int, col: int, long: bool = False):
        key = ("grid", row, col)
        self.pad.press(key, True)
        if long:
            self.board._pressed_at[key] -= self.board.cfg["long_press_seconds"]
        self.pad.press(key, False)

    def test_slots_assigned_in_order_and_reused(self):
        for sid in "ABC":
            self.board.handle_event(ev("SessionStart", sid))
        self.assertEqual([self.board.sessions[s].slot for s in "ABC"], [0, 1, 2])
        self.board.handle_event(ev("SessionEnd", "B"))
        self.board.handle_event(ev("SessionStart", "D"))
        self.assertEqual(self.board.sessions["D"].slot, 1)

    def test_leds_and_alert(self):
        self.board.handle_event(ev("PermissionRequest"))
        self.assertEqual(self.pad.leds[("grid", 0, 0)], Led("red", True))
        self.assertEqual(self.pad.leds[ALERT_KEY], Led("red", True))
        self.board.handle_event(ev("PostToolUse"))
        self.assertEqual(self.pad.leds[ALERT_KEY], Led())

    def test_short_press_marks_seen(self):
        self.board.focus_app = lambda: None
        self.board.handle_event(ev("Stop"))
        self.press(0, 0)
        self.assertEqual(self.board.sessions["A"].state, "seen")

    def test_long_press_removes(self):
        self.board.handle_event(ev("Stop"))
        self.press(0, 0, long=True)
        self.assertNotIn("A", self.board.sessions)

    def test_clear_key_removes_only_stopped(self):
        self.board.handle_event(ev("Stop", "A"))
        self.board.handle_event(ev("UserPromptSubmit", "B"))
        self.assertEqual(self.pad.leds[CLEAR_KEY], Led("amber_low"))
        self.press(*CLEAR_KEY[1:])
        self.assertEqual(list(self.board.sessions), ["B"])

    def test_invalid_session_id_ignored(self):
        self.board.handle_event({"session_id": ["x"], "hook_event_name": "Stop"})
        self.board.handle_event({"hook_event_name": "Stop"})
        self.assertEqual(self.board.sessions, {})


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.pad = SimLaunchpad(quiet=True)
        self.board = Board(self.pad, load_config(None))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board, self.pad))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def post(self, path: str, data: bytes) -> int:
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_event_returns_empty_204(self):
        self.assertEqual(self.post("/event", json.dumps(ev("Stop")).encode()), 204)
        self.assertEqual(self.board.sessions["A"].state, "done")

    def test_bad_bodies_return_400(self):
        self.assertEqual(self.post("/event", b"{nope"), 400)
        self.assertEqual(self.post("/event", b"[1, 2]"), 400)
        self.assertEqual(self.post("/press", b'{"row": "x", "col": 0}'), 400)
        self.assertEqual(self.post("/press", b'{"row": 9, "col": 0}'), 400)

    def test_state(self):
        self.post("/event", json.dumps(ev("SessionStart")).encode())
        with urllib.request.urlopen(self.base + "/state") as resp:
            state = json.load(resp)
        self.assertEqual(state["sessions"][0]["state"], "starting")


class InstallHooksTest(unittest.TestCase):
    def test_install_is_idempotent_and_keeps_other_hooks(self):
        other = {"hooks": [{"type": "command", "command": "echo hi"}]}
        settings = {"hooks": {"Stop": [other]}, "model": "x"}
        install(settings, URL)
        install(settings, URL)
        self.assertEqual(set(settings["hooks"]), set(EVENTS))
        self.assertEqual(len(settings["hooks"]["Stop"]), 2)
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], "*")

    def test_uninstall_restores(self):
        other = {"hooks": [{"type": "command", "command": "echo hi"}]}
        settings = uninstall(install({"hooks": {"Stop": [other]}}, URL), URL)
        self.assertEqual(settings, {"hooks": {"Stop": [other]}})
        self.assertEqual(uninstall(install({}, URL), URL), {})

    def test_default_url_reads_port(self):
        with TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            self.assertEqual(default_url(cfg), "http://127.0.0.1:47800/event")
            cfg.write_text("port = 5000\n", encoding="utf-8")
            self.assertEqual(default_url(cfg), "http://127.0.0.1:5000/event")


class LaunchpadTest(unittest.TestCase):
    def test_note_mapping_roundtrip(self):
        for row in range(8):
            for col in range(9):
                self.assertEqual(key_for_note(note_for(row, col)), ("grid", row, col))
        self.assertIsNone(key_for_note(9))

    def test_velocity(self):
        self.assertEqual(Led().velocity(), 12)
        self.assertEqual(Led("red").velocity(), 3 + 12)
        self.assertEqual(Led("green", True).velocity(), 48 + 8)


if __name__ == "__main__":
    unittest.main()
