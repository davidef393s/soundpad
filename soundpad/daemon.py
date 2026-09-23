"""Demone soundpad: riceve gli eventi degli hook di Claude Code via HTTP e accende i pad.

Un pad della griglia 8x8 = una sessione di Claude Code, assegnato in ordine di lettura.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .launchpad import COLORS, Key, Led, MidiLaunchpad, SimLaunchpad

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 47800,
    "long_press_seconds": 1.0,
    "session_ttl_hours": 12,
    # stato -> [colore, lampeggio]
    "colors": {
        "starting": ["green_low", False],
        "working": ["amber_low", False],
        "compacting": ["yellow", True],
        "needs_permission": ["red", True],
        "done": ["green", False],
        "idle": ["green", True],
        "seen": ["green_low", False],
        "error": ["red", False],
    },
    "focus_command": {
        "Darwin": ["open", "-a", "Claude"],
        "Windows": [
            "powershell", "-NoProfile", "-Command",
            "(New-Object -ComObject WScript.Shell).AppActivate('Claude') | Out-Null",
        ],
    },
}

# Tasto scene in basso a destra: dimentica tutte le sessioni ferme (done/idle/seen/error)
CLEAR_KEY: Key = ("grid", 7, 8)
# Tasto tondo in alto a sinistra: spia del demone (verde = in ascolto)
HEARTBEAT_KEY: Key = ("top", 0, 0)
# Tasto tondo in alto a destra: lampeggia rosso se almeno una sessione aspetta un permesso
ALERT_KEY: Key = ("top", 0, 7)


@dataclass
class Session:
    session_id: str
    slot: int
    state: str = "starting"
    cwd: str = ""
    last_seen: float = field(default_factory=time.time)


def next_state(event: dict, current: str | None) -> str | None:
    """Traduce un evento hook nel nuovo stato della sessione. None = sessione terminata."""
    name = event.get("hook_event_name", "")
    if name == "SessionEnd":
        return None
    if name == "SessionStart":
        return "starting"
    if name in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied"):
        return "working"
    if name == "PermissionRequest":
        return "needs_permission"
    if name == "PreCompact":
        return "compacting"
    if name == "Stop":
        return "done"
    if name == "StopFailure":
        return "error"
    if name == "Notification":
        kind = event.get("notification_type", "")
        if kind == "permission_prompt":
            return "needs_permission"
        if kind == "idle_prompt":
            # l'utente non ha ancora risposto: da "fatto" passa a "fatto e ti sto aspettando".
            # "seen" resta tale: il pad è già stato premuto, non deve tornare a lampeggiare.
            return "idle" if current in ("done", "idle", None) else current
        return current or "starting"
    return current or "starting"


class Board:
    """Stato delle sessioni e disegno sul Launchpad. Tutti i metodi pubblici sono thread-safe."""

    def __init__(self, pad, config: dict) -> None:
        self.pad = pad
        self.cfg = config
        self.sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._drawn: dict[Key, Led] = {}
        self._pressed_at: dict[Key, float] = {}
        pad.on_press(self.handle_press)

    # --- eventi hook -------------------------------------------------------
    def handle_event(self, event: dict) -> None:
        sid = event.get("session_id")
        if not sid or not isinstance(sid, str):
            return
        with self._lock:
            sess = self.sessions.get(sid)
            state = next_state(event, sess.state if sess else None)
            if state is None:
                self.sessions.pop(sid, None)
            elif sess is None:
                slot = self._free_slot()
                if slot is None:
                    print("[soundpad] 64 sessioni attive: griglia piena, evento ignorato", file=sys.stderr)
                    return
                self.sessions[sid] = Session(sid, slot, state, event.get("cwd", ""))
            else:
                sess.state = state
                sess.last_seen = time.time()
                sess.cwd = event.get("cwd", sess.cwd)
            self.redraw()

    def _free_slot(self) -> int | None:
        used = {s.slot for s in self.sessions.values()}
        return next((i for i in range(64) if i not in used), None)

    # --- pressione dei pad -------------------------------------------------
    def handle_press(self, key: Key, pressed: bool) -> None:
        with self._lock:
            if pressed:
                self._pressed_at[key] = time.time()
                return
            started = self._pressed_at.pop(key, None)
            if started is None:
                return
            long_press = time.time() - started >= self.cfg["long_press_seconds"]
            if key == CLEAR_KEY:
                for sid in [s.session_id for s in self.sessions.values() if s.state in ("done", "idle", "seen", "error")]:
                    del self.sessions[sid]
            elif key[0] == "grid" and key[2] < 8:
                slot = key[1] * 8 + key[2]
                sess = next((s for s in self.sessions.values() if s.slot == slot), None)
                if sess is not None:
                    if long_press:
                        del self.sessions[sess.session_id]
                    else:
                        if sess.state in ("done", "idle"):
                            sess.state = "seen"
                        self.focus_app()
            self.redraw()

    def focus_app(self) -> None:
        cmd = self.cfg["focus_command"].get(platform.system())
        if cmd:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                print(f"[soundpad] focus fallito: {exc}", file=sys.stderr)

    # --- disegno -----------------------------------------------------------
    def expire_stale(self) -> None:
        cutoff = time.time() - self.cfg["session_ttl_hours"] * 3600
        with self._lock:
            stale = [sid for sid, s in self.sessions.items() if s.last_seen < cutoff]
            for sid in stale:
                del self.sessions[sid]
            if stale:
                self.redraw()

    def target(self) -> dict[Key, Led]:
        leds: dict[Key, Led] = {}
        colors = self.cfg["colors"]
        for s in self.sessions.values():
            color, flash = colors[s.state]
            leds[("grid", s.slot // 8, s.slot % 8)] = Led(color, bool(flash))
        leds[HEARTBEAT_KEY] = Led("green_low")
        if any(s.state == "needs_permission" for s in self.sessions.values()):
            leds[ALERT_KEY] = Led("red", True)
        if any(s.state in ("done", "idle", "seen", "error") for s in self.sessions.values()):
            leds[CLEAR_KEY] = Led("amber_low")
        return leds

    def redraw(self, full: bool = False) -> None:
        """Invia solo i LED cambiati (il MIDI è lento: 64 messaggi per ogni evento si notano)."""
        with self._lock:
            new = self.target()
            if full:
                self.pad.clear()
                self._drawn = {}
            for key in set(self._drawn) | set(new):
                led = new.get(key, Led())
                if self._drawn.get(key, Led()) != led:
                    self.pad.set(key, led)
            self._drawn = new
            if hasattr(self.pad, "flush"):
                self.pad.flush()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self.pad.connected,
                "sessions": [
                    {"session_id": s.session_id, "slot": s.slot, "state": s.state, "cwd": s.cwd}
                    for s in sorted(self.sessions.values(), key=lambda s: s.slot)
                ],
            }


def make_handler(board: Board, sim: SimLaunchpad | None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("il corpo deve essere un oggetto JSON")
            return body

        def do_GET(self) -> None:
            if self.path == "/state":
                self._json(200, board.snapshot())
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                body = self._body()
            except ValueError:  # comprende json.JSONDecodeError e Content-Length non numerico
                self._json(400, {"error": "json non valido"})
                return
            if self.path == "/event":
                board.handle_event(body)
                # corpo vuoto: un JSON qualsiasi verrebbe letto da Claude Code come output dell'hook
                self.send_response(204)
                self.end_headers()
            elif self.path == "/press" and sim is not None:
                # solo simulatore: {"row": 0, "col": 0, "long": false}
                try:
                    row, col = int(body["row"]), int(body["col"])
                except (KeyError, TypeError, ValueError):
                    self._json(400, {"error": "servono row e col numerici"})
                    return
                if not (0 <= row < 8 and 0 <= col <= 8):
                    self._json(400, {"error": "row 0..7, col 0..8"})
                    return
                key: Key = ("grid", row, col)
                sim.press(key, True)
                if body.get("long"):
                    with board._lock:
                        board._pressed_at[key] -= board.cfg["long_press_seconds"]
                sim.press(key, False)
                self._json(200, board.snapshot())
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, *args) -> None:  # silenzia il log di ogni richiesta
            pass

    return Handler


def load_config(path: Path | None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and path.exists():
        with path.open("rb") as fh:
            user = tomllib.load(fh)
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    for state, (color, _) in cfg["colors"].items():
        if color not in COLORS:
            raise SystemExit(f"colore sconosciuto per '{state}': {color}. Validi: {', '.join(COLORS)}")
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description="Launchpad come spia degli hook di Claude Code")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent / "config.toml")
    parser.add_argument("--sim", action="store_true", help="usa il Launchpad simulato nel terminale")
    parser.add_argument("--quiet", action="store_true", help="con --sim, non disegnare la griglia")
    args = parser.parse_args()

    cfg = load_config(args.config)
    sim = SimLaunchpad(quiet=args.quiet) if args.sim else None
    pad = sim or MidiLaunchpad()
    board = Board(pad, cfg)

    server = ThreadingHTTPServer((cfg["host"], cfg["port"]), make_handler(board, sim))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[soundpad] in ascolto su http://{cfg['host']}:{cfg['port']}", file=sys.stderr)
    board.redraw(full=True)

    try:
        while True:
            if pad.try_connect():
                board.redraw(full=True)  # appena collegato: i LED sono spenti, ridisegna tutto
            board.expire_stale()
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        pad.clear()


if __name__ == "__main__":
    main()
