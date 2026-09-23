"""Test di soundpad. Solo libreria standard: `python -m unittest` dalla cartella del progetto."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from soundpad import install_hooks
from soundpad.claudeapp import AppSession, SessionIndex
from soundpad.daemon import ALERT_KEY, CLEAR_KEY, PERMISSION_KEYS, Board, load_config, make_handler, next_state
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


def static_config() -> dict:
    """Configurazione senza animazioni: i LED dipendono solo dallo stato, non dal tempo."""
    cfg = load_config(None)
    cfg["animations"] = False
    return cfg


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.pad = SimLaunchpad(quiet=True)
        self.board = Board(self.pad, static_config())

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
        self.board.focus_app = lambda *a: None
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


class AnimationTest(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.pad = SimLaunchpad(quiet=True)
        self.board = Board(self.pad, load_config(None))
        self.board.clock = lambda: self.now

    def at(self, t: float, key) -> Led:
        return self.board.frame(t).get(key, Led())

    def test_new_session_sparks_then_settles(self):
        self.board.handle_event(ev("SessionStart"))
        self.assertEqual(self.at(100.0, ("grid", 0, 0)), Led("amber"))  # lampo pieno
        self.assertEqual(self.at(102.0, ("grid", 0, 0)), Led("green_low"))  # poi il colore di "aperta"

    def test_permission_wave_crosses_the_grid(self):
        self.board.handle_event(ev("UserPromptSubmit"))
        self.now = 110.0
        self.board.handle_event(ev("PermissionRequest"))
        far = ("grid", 7, 7)  # ~9.9 pad di distanza dall'origine
        self.assertEqual(self.at(110.0, far), Led())
        self.assertGreater(self.at(110.85, far).rg[0], 0)  # l'onda rossa ci arriva
        self.assertEqual(self.at(112.0, far), Led())  # e se ne va
        self.assertEqual(self.at(112.0, ("grid", 0, 0)), Led("red", True))

    def waves_reaching_far_corner(self, start: float, until: float) -> int:
        """Quante volte un'onda arriva all'angolo lontano (7, 7) tra `start` e `until`,
        facendo avanzare l'orologio (così gli eventi nel frattempo contano da lì in poi)."""
        far, hits, lit_before = ("grid", 7, 7), 0, False
        self.now = start
        while self.now < until:
            lit = self.at(self.now, far).rg[0] > 0
            hits += lit and not lit_before
            lit_before = lit
            self.now += 0.05
        return hits

    def test_permission_wave_repeats_five_times(self):
        self.board.handle_event(ev("UserPromptSubmit"))
        self.now = 110.0
        self.board.handle_event(ev("PermissionRequest"))
        self.assertEqual(self.waves_reaching_far_corner(110.0, 125.0), 5)

    def test_permission_waves_stop_when_answered(self):
        self.board.handle_event(ev("UserPromptSubmit"))
        self.now = 110.0
        self.board.handle_event(ev("PermissionRequest"))
        first = self.waves_reaching_far_corner(110.0, 111.5)
        self.board.handle_event(ev("PostToolUse"))  # risposto nell'app dopo la prima onda
        later = self.waves_reaching_far_corner(111.5, 125.0)
        self.assertEqual((first, later), (1, 0))

    def test_first_event_can_be_a_permission(self):
        self.board.handle_event(ev("PermissionRequest"))  # sessione mai vista prima, istante 100
        self.assertEqual(self.waves_reaching_far_corner(100.0, 115.0), 5)

    def test_done_ripple_reaches_neighbours(self):
        self.board.handle_event(ev("UserPromptSubmit"))
        self.now = 110.0
        self.board.handle_event(ev("Stop"))
        lit = [self.at(t, ("grid", 0, 2)).rg[1] for t in (110.2, 110.3, 110.4, 110.5)]
        self.assertTrue(any(lit))

    def test_working_breathes(self):
        self.board.handle_event(ev("UserPromptSubmit"))
        levels = {self.at(200 + i * 0.2, ("grid", 0, 0)).color for i in range(12)}
        self.assertTrue({"amber_low", "amber"} <= levels)

    def test_meter_counts_from_the_bottom(self):
        cfg = static_config()
        board = Board(SimLaunchpad(quiet=True), cfg)
        board.handle_event(ev("PermissionRequest", "A"))
        board.handle_event(ev("Stop", "B"))
        board.handle_event(ev("UserPromptSubmit", "C"))
        frame = board.frame()
        self.assertEqual(frame[("grid", 6, 8)], Led("red"))
        self.assertEqual(frame[("grid", 5, 8)], Led("green"))
        self.assertEqual(frame[("grid", 4, 8)], Led("amber_low"))
        self.assertNotIn(("grid", 3, 8), frame)

    def test_held_key_lights_up(self):
        self.pad.press(("grid", 3, 3), True)
        self.assertEqual(self.at(100.0, ("grid", 3, 3)), Led("amber"))
        self.pad.press(("grid", 3, 3), False)
        self.assertEqual(self.at(100.0, ("grid", 3, 3)), Led())

    def test_forget_fades_out(self):
        self.board.handle_event(ev("Stop"))
        self.now = 110.0
        self.board.forget(0)
        self.assertNotEqual(self.at(110.1, ("grid", 0, 0)), Led())
        self.assertEqual(self.at(111.0, ("grid", 0, 0)), Led())

    def test_screensaver_only_when_empty(self):
        self.board.boot()
        lit = lambda t: [k for k, led in self.board.frame(t).items() if k[0] == "grid" and k[2] < 8 and led != Led()]
        self.board.frame(100.0)  # primo fotogramma: da qui parte l'attesa
        self.assertEqual(lit(110.0), [])  # griglia vuota da poco: niente
        self.assertTrue(any(lit(100.0 + 35 + i * 0.3) for i in range(20)))  # dopo 30 s piove
        self.board.handle_event(ev("SessionStart"))
        self.assertEqual(lit(200.0), [("grid", 0, 0)])  # una sessione ferma la pioggia

    def test_boot_sweeps_everything(self):
        self.board.boot()
        seen = set()
        for i in range(40):
            seen |= {k for k, led in self.board.frame(100 + i * 0.04).items() if led != Led()}
        self.assertTrue({("grid", 0, 0), ("grid", 7, 8), ("top", 0, 7)} <= seen)
        self.assertEqual(self.board.frame(102.0).get(("grid", 7, 7), Led()), Led())

    def test_led_of_uses_canonical_names(self):
        self.assertEqual(Led.of((3, 0)), Led("red"))
        self.assertEqual(Led.of((2, 1)).rg, (2, 1))
        self.assertEqual(Led.of((0, 0), flash=True), Led())


SUGGESTION = {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "npm test"}],
              "behavior": "allow", "destination": "localSettings"}


class PermissionTest(unittest.TestCase):
    """Un PermissionRequest vero via HTTP: il demone lo tiene in sospeso finché non premi un tondo."""

    def setUp(self):
        cfg = static_config()
        cfg["permission_wait_seconds"] = 5
        self.pad = SimLaunchpad(quiet=True)
        self.board = Board(self.pad, cfg)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board, None))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/event"

    def tearDown(self):
        self.board.release_all()
        self.server.shutdown()
        self.server.server_close()

    def ask(self, sid: str = "A", suggestions=None) -> dict:
        """Invia la richiesta in un thread; result["body"] arriva quando il demone risponde."""
        event = ev("PermissionRequest", sid, tool_name="Bash", tool_input={"command": "npm test"},
                   permission_suggestions=suggestions or [])
        result: dict = {}

        def run():
            req = urllib.request.Request(self.url, data=json.dumps(event).encode(), method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                result["status"], result["body"] = resp.status, resp.read()

        result["thread"] = threading.Thread(target=run)
        result["thread"].start()
        for _ in range(100):  # aspetta che la richiesta sia in sospeso
            if sid in self.board.pending:
                break
            time.sleep(0.02)
        return result

    def decision(self, result: dict) -> dict | None:
        result["thread"].join(10)
        if result["status"] == 204:
            return None
        return json.loads(result["body"])["hookSpecificOutput"]["decision"]

    def press(self, key):
        self.pad.press(key, True)
        self.pad.press(key, False)

    def test_accept_once(self):
        result = self.ask()
        self.assertEqual(self.pad.leds[PERMISSION_KEYS["once"]], Led("green"))
        self.assertEqual(self.board.snapshot()["sessions"][0]["permission"]["summary"], "npm test")
        self.press(PERMISSION_KEYS["once"])
        self.assertEqual(self.decision(result), {"behavior": "allow"})
        self.assertEqual(self.board.sessions["A"].state, "working")
        self.assertEqual(self.pad.leds[PERMISSION_KEYS["once"]], Led())

    def test_accept_always_echoes_suggestions(self):
        result = self.ask(suggestions=[SUGGESTION])
        self.assertEqual(self.pad.leds[PERMISSION_KEYS["always"]], Led("amber"))
        self.press(PERMISSION_KEYS["always"])
        self.assertEqual(self.decision(result), {"behavior": "allow", "updatedPermissions": [SUGGESTION]})

    def test_always_without_suggestions_is_not_offered(self):
        result = self.ask()
        self.assertNotEqual(self.pad.leds.get(PERMISSION_KEYS["always"], Led()), Led("amber"))
        self.press(PERMISSION_KEYS["always"])
        self.assertEqual(self.decision(result), {"behavior": "allow"})  # ripiega su "una volta"

    def test_deny(self):
        result = self.ask()
        self.press(PERMISSION_KEYS["deny"])
        self.assertEqual(self.decision(result)["behavior"], "deny")

    def test_answer_in_app_releases(self):
        result = self.ask()
        self.board.handle_event(ev("PostToolUse", "A"))  # hai risposto nell'app
        self.assertIsNone(self.decision(result))
        self.assertEqual(self.board.pending, {})

    def test_pressed_pad_chooses_the_request(self):
        self.board.focus_app = lambda *a: None
        first, second = self.ask("A"), self.ask("B")
        self.press(("grid", 0, 1))  # pad della sessione B
        self.press(PERMISSION_KEYS["once"])
        self.assertEqual(self.decision(second), {"behavior": "allow"})
        self.assertIn("A", self.board.pending)
        self.press(PERMISSION_KEYS["deny"])
        self.assertEqual(self.decision(first)["behavior"], "deny")

    def test_timeout_leaves_it_to_the_app(self):
        self.board.cfg["permission_wait_seconds"] = 0.2
        self.assertIsNone(self.decision(self.ask()))

    def test_disabled(self):
        self.board.cfg["permission_wait_seconds"] = 0
        result = self.ask()
        self.assertIsNone(self.decision(result))
        self.assertEqual(self.board.sessions["A"].state, "needs_permission")


class ClaudeAppTest(unittest.TestCase):
    def write(self, root: Path, local_id: str, cli_id: str, title: str) -> Path:
        folder = root / "account" / "org"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{local_id}.json"
        path.write_text(json.dumps({"sessionId": local_id, "cliSessionId": cli_id, "title": title}), encoding="utf-8")
        return path

    def test_index_maps_cli_id_to_app_session(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "local_1", "cli-A", "Prima chat")
            index = SessionIndex(root)
            self.assertEqual(index.lookup("cli-A"), AppSession("local_1", "Prima chat"))
            self.assertIsNone(index.lookup("cli-B"))

    def test_index_picks_up_title_changes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write(root, "local_1", "cli-A", "")
            index = SessionIndex(root)
            self.assertEqual(index.lookup("cli-A").title, "")
            self.write(root, "local_1", "cli-A", "Titolo arrivato")
            os.utime(path, (time.time() + 5, time.time() + 5))  # mtime diverso anche su file system lenti
            self.assertEqual(index.lookup("cli-A").title, "Titolo arrivato")

    def test_board_fills_title_and_opens_chat(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, "local_1", "cli-A", "Chat del pad")
            board = Board(SimLaunchpad(quiet=True), load_config(None), SessionIndex(root))
            opened = []
            board.focus_app = lambda sess=None: opened.append(sess)
            board.handle_event(ev("Stop", "cli-A"))
            sess = board.sessions["cli-A"]
            self.assertEqual((sess.title, sess.local_id), ("Chat del pad", "local_1"))
            self.assertEqual(board.snapshot()["sessions"][0]["title"], "Chat del pad")
            board.open(0)
            self.assertIs(opened[0], sess)

    def test_missing_folder_is_harmless(self):
        self.assertIsNone(SessionIndex(Path("non-esiste-davvero")).lookup("x"))


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

    def action(self, body: dict, content_type: str = "application/json") -> int:
        req = urllib.request.Request(
            self.base + "/action", data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_dashboard_served(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertIn("text/html", resp.headers["Content-Type"])
            self.assertIn(b"<title>soundpad</title>", resp.read())

    def test_actions(self):
        self.board.focus_app = lambda *a: None
        for sid in "AB":
            self.post("/event", json.dumps(ev("Stop", sid)).encode())
        self.assertEqual(self.action({"action": "open", "slot": 0}), 200)
        self.assertEqual(self.board.sessions["A"].state, "seen")
        self.assertEqual(self.action({"action": "forget", "slot": 1}), 200)
        self.assertNotIn("B", self.board.sessions)
        self.assertEqual(self.action({"action": "forget", "slot": 5}), 404)
        self.assertEqual(self.action({"action": "clear"}), 200)
        self.assertEqual(self.board.sessions, {})

    def test_action_rejects_bad_input(self):
        self.assertEqual(self.action({"action": "clear"}, content_type="text/plain"), 415)
        self.assertEqual(self.action({"action": "boom", "slot": 0}), 400)
        self.assertEqual(self.action({"action": "open", "slot": 64}), 400)
        self.assertEqual(self.action({"action": "open", "slot": True}), 400)

    def app_post(self, path: str, body: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as exc:
            return exc.code, {}

    def test_app_hooks_toggle(self):
        with TemporaryDirectory() as tmp:
            settings = Path(tmp) / "settings.json"
            old = install_hooks.SETTINGS_PATH
            install_hooks.SETTINGS_PATH = settings
            try:
                with urllib.request.urlopen(self.base + "/app/settings") as resp:
                    self.assertFalse(json.load(resp)["hooks_installed"])
                code, data = self.app_post("/app/hooks", {"installed": True})
                self.assertEqual(code, 200)
                self.assertTrue(data["hooks_installed"])
                self.assertIsNone(data["autostart"])  # non è l'exe
                code, data = self.app_post("/app/hooks", {"installed": False})
                self.assertFalse(data["hooks_installed"])
                self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), {})
            finally:
                install_hooks.SETTINGS_PATH = old

    def test_app_show_needs_window(self):
        self.assertEqual(self.app_post("/app/show", {})[0], 404)  # demone senza finestra
        self.assertEqual(self.app_post("/app/autostart", {"enabled": True})[0], 500)

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
