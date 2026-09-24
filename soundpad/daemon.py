"""Demone soundpad: riceve gli eventi degli hook di Claude Code via HTTP e accende i pad.

Un pad della griglia 8x8 = una sessione di Claude Code, assegnato in ordine di lettura.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import autostart, effects, install_hooks, permissions
from .claudeapp import SessionIndex
from .launchpad import COLORS, Key, Led, SimLaunchpad, make_launchpad
from .paths import config_path

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 47800,
    "long_press_seconds": 1.0,
    "session_ttl_hours": 12,
    # "Apri" apre la chat della sessione nell'app desktop (Windows e macOS), non solo l'app
    "open_chat": True,
    # Animazioni sul Launchpad (onde, respiro, avvio, screensaver). false = luci fisse come prima
    "animations": True,
    # Screensaver a griglia vuota dopo questi secondi (0 = mai)
    "screensaver_after_seconds": 30,
    # Rispondere ai permessi dal Launchpad: quanti secondi il demone tiene in sospeso la richiesta
    # (0 = funzione spenta: i permessi si danno solo nell'app)
    "permission_wait_seconds": 90,
    # Quante onde rosse attraversano la griglia quando una sessione chiede un permesso
    "permission_waves": 5,
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
    # Comando per portare in primo piano l'app al posto di quello predefinito, per sistema ("Darwin",
    # "Windows", "Linux"). Se c'è, soundpad non apre la chat della sessione.
    "focus_command": {},
}

# SOUNDPAD_LOG_EVENTS=1: scrive nel log ogni evento ricevuto (nome, strumento, sessione), per capire cosa manda
# Claude Code. Sotto launchd: `launchctl setenv SOUNDPAD_LOG_EVENTS 1` e poi riavvio del demone.
LOG_EVENTS = bool(os.environ.get("SOUNDPAD_LOG_EVENTS"))

# Tasto scene in basso a destra: dimentica tutte le sessioni ferme (done/idle/seen/error)
CLEAR_KEY: Key = ("grid", 7, 8)
# Tasto tondo in alto a sinistra: spia del demone (verde = in ascolto)
HEARTBEAT_KEY: Key = ("top", 0, 0)
# Tasto tondo in alto a destra: lampeggia rosso se almeno una sessione aspetta un permesso
ALERT_KEY: Key = ("top", 0, 7)
# Tasti tondi per rispondere a una richiesta di permesso (accesi solo quando ce n'è una)
PERMISSION_KEYS: dict[str, Key] = {"once": ("top", 0, 4), "always": ("top", 0, 5), "deny": ("top", 0, 6)}
# Riga in basso della griglia: le opzioni di una domanda di Claude (AskUserQuestion), da sinistra.
# Mentre c'è una domanda coprono le sessioni di quella riga. Il tondo "once" conferma la scelta multipla.
OPTION_KEYS: list[Key] = [("grid", 7, c) for c in range(8)]
# Eventi che dicono che la richiesta in sospeso ha già avuto risposta (nell'app)
ANSWERED_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied",
                   "UserPromptSubmit", "Stop", "StopFailure", "SessionEnd"}
# Tasti scene delle righe 0-6, dal basso verso l'alto: colonna di stato (vedi Board.meter)
METER_KEYS: list[Key] = [("grid", r, 8) for r in range(6, -1, -1)]
# Secondi tra una onda del permesso e la successiva (ogni onda dura 1,1 s)
PERMISSION_WAVE_EVERY = 1.4
# Stati in cui Claude non sta facendo niente: li toglie il tasto CLEAR_KEY
STOPPED = ("done", "idle", "seen", "error")
# Pagina web servita su "/"
DASHBOARD = Path(__file__).with_name("dashboard.html")


@dataclass
class Session:
    session_id: str
    slot: int
    state: str = "starting"
    cwd: str = ""
    last_seen: float = field(default_factory=time.time)
    # Dall'app desktop Claude, se la sessione è sua: titolo della chat e id "local_..."
    title: str = ""
    local_id: str = ""


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

    def __init__(self, pad, config: dict, index: SessionIndex | None = None) -> None:
        self.pad = pad
        self.cfg = config
        self.index = index  # sessioni dell'app desktop, per titolo e apertura della chat
        self.sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._drawn: dict[Key, Led] = {}
        self._pressed_at: dict[Key, float] = {}
        # Animazioni: orologio monotono (sostituibile nei test), effetti in corso, tasti tenuti premuti
        self.clock: Callable[[], float] = time.monotonic
        self.effects: list[effects.Effect] = []
        self._held: set[Key] = set()
        self._empty_since: float | None = None
        self._rain: effects.Rain | None = None
        # Richieste di permesso in attesa di risposta, per sessione; _selected = scelta col pad
        self.pending: dict[str, permissions.Pending] = {}
        self._selected: str | None = None
        pad.on_press(self.handle_press)

    # --- eventi hook -------------------------------------------------------
    def handle_event(self, event: dict) -> None:
        sid = event.get("session_id")
        if not sid or not isinstance(sid, str):
            return
        with self._lock:
            if event.get("hook_event_name") in ANSWERED_EVENTS and sid in self.pending:
                self.pending.pop(sid).resolve(None)  # hai risposto nell'app: la risposta HTTP si chiude vuota
            sess = self.sessions.get(sid)
            old = sess.state if sess else None
            state = next_state(event, old)
            if state is None:
                if sess is not None:
                    self._fade_out(sess)
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
            if state is not None and self.index is not None:
                app = self.index.lookup(sid)
                if app is not None:
                    self.sessions[sid].title, self.sessions[sid].local_id = app.title, app.local_id
            if state is not None and state != old:
                self._on_transition(self.sessions[sid].slot, old, state)
            self.redraw()

    def _free_slot(self) -> int | None:
        used = {s.slot for s in self.sessions.values()}
        return next((i for i in range(64) if i not in used), None)

    # --- effetti -----------------------------------------------------------
    @property
    def animated(self) -> bool:
        return bool(self.cfg["animations"])

    def _add(self, effect: effects.Effect) -> None:
        if self.animated:
            self.effects = self.effects[-15:] + [effect]  # tetto: una raffica di eventi non intasa il MIDI

    def _on_transition(self, slot: int, old: str | None, new: str) -> None:
        now = self.clock()
        if old == "needs_permission":
            self._stop_waves(slot)  # risposta arrivata: le onde rimaste non servono più
        if new == "needs_permission":
            # onde rosse su tutta la griglia, una dopo l'altra: difficile non vederle
            for i in range(self.cfg["permission_waves"]):
                wave = effects.Ripple(now + i * PERMISSION_WAVE_EVERY, slot, effects.RED,
                                      radius=12, duration=1.1, width=1.3)
                wave.tag = ("permission", slot)
                self._add(wave)
        elif old is None:  # sessione nuova: lampo e piccola onda ambra
            self._add(effects.Spark(now, slot))
            self._add(effects.Ripple(now, slot, effects.AMBER, radius=2.2, duration=0.6))
        elif new == "done" and old != "idle":  # turno finito: onda verde verso i vicini
            self._add(effects.Ripple(now, slot, effects.GREEN, radius=4.5, duration=0.9))
        elif new == "error":
            self._add(effects.Ripple(now, slot, effects.RED, radius=2.5, duration=0.7))

    def _stop_waves(self, slot: int) -> None:
        self.effects = [e for e in self.effects if e.tag != ("permission", slot)]

    def _fade_out(self, sess: Session) -> None:
        self._stop_waves(sess.slot)
        color, _ = self.cfg["colors"][sess.state]
        self._add(effects.FadeOut(self.clock(), sess.slot, COLORS[color]))

    def boot(self) -> None:
        """Animazione di benvenuto (all'avvio o quando il Launchpad viene collegato)."""
        with self._lock:
            self._add(effects.Boot(self.clock()))

    # --- permessi ----------------------------------------------------------
    def request_permission(self, event: dict) -> dict | None:
        """Chiamato dal server HTTP per un PermissionRequest: aggiorna la griglia, poi aspetta
        (bloccando solo questa richiesta) che tu risponda. None = nessuna risposta, decide l'app."""
        self.handle_event(event)
        # sotto il timeout dell'hook (install_hooks.TIMEOUTS), o Claude Code chiuderebbe prima lui
        wait = min(self.cfg["permission_wait_seconds"], 110)
        sid = event.get("session_id")
        if not wait or not isinstance(sid, str) or sid not in self.sessions:
            return None
        pending = permissions.from_event(event)
        with self._lock:
            if sid in self.pending:
                self.pending[sid].resolve(None)
            self.pending[sid] = pending
            self.redraw()
        pending.answered.wait(wait)
        with self._lock:
            if self.pending.get(sid) is pending:
                del self.pending[sid]
                self.redraw()
        if LOG_EVENTS:
            print(f"[risposta] {time.strftime('%H:%M:%S')} {sid[:8]} {json.dumps(pending.decision, ensure_ascii=False)}",
                  file=sys.stderr)
        return pending.decision

    def permission_target(self) -> permissions.Pending | None:
        """La richiesta a cui rispondono i tasti: quella del pad premuto, altrimenti la più vecchia."""
        if self._selected in self.pending:
            return self.pending[self._selected]
        return min(self.pending.values(), key=lambda p: p.created, default=None)

    def answer(self, choice: str, session_id: str | None = None) -> bool:
        """Risponde alla richiesta di `session_id` (o a permission_target) con once/always/deny.
        Su una domanda: "once" conferma la scelta multipla, "deny" rifiuta, "always" non fa niente."""
        with self._lock:
            pending = self.pending.get(session_id) if session_id else self.permission_target()
            if pending is None or choice not in permissions.CHOICES:
                return False
            if pending.questions and choice != "deny":
                q = pending.question
                if choice != "once" or q is None or not q.get("multiSelect") or not pending.picked:
                    return False
                if pending.confirm():
                    self._finish(pending, pending.answers_decision(), effects.GREEN)
                else:
                    self.redraw()  # restano altre domande
                return True
            decision = permissions.decision_for(choice, pending)
            self._finish(pending, decision, effects.RED if choice == "deny" else effects.GREEN)
            return True

    def choose(self, index: int, session_id: str | None = None) -> bool:
        """Sceglie l'opzione `index` della domanda in sospeso (di `session_id` o di permission_target)."""
        with self._lock:
            pending = self.pending.get(session_id) if session_id else self.permission_target()
            q = pending.question if pending is not None else None
            if q is None or not 0 <= index < min(len(q["options"]), len(OPTION_KEYS)):
                return False
            if pending.pick(index):
                self._finish(pending, pending.answers_decision(), effects.GREEN)
            else:
                self.redraw()
            return True

    def _finish(self, pending: permissions.Pending, decision: dict, hue) -> None:
        """Chiude la richiesta con `decision` e lo mostra sul pad della sessione. Con il lock preso."""
        del self.pending[pending.session_id]
        pending.resolve(decision)
        sess = self.sessions.get(pending.session_id)
        if sess is not None:
            sess.state = "working"  # Claude riparte (o riceve il rifiuto) subito
            self._stop_waves(sess.slot)
            self._add(effects.Ripple(self.clock(), sess.slot, hue, radius=3, duration=0.6))
        self.redraw()

    def release_all(self) -> None:
        """Chiude le richieste in sospeso (all'uscita): l'app mostra il suo riquadro come sempre."""
        with self._lock:
            for pending in self.pending.values():
                pending.resolve(None)
            self.pending.clear()

    # --- pressione dei pad -------------------------------------------------
    def handle_press(self, key: Key, pressed: bool) -> None:
        if LOG_EVENTS:
            print(f"[tasto] {time.strftime('%H:%M:%S')} {key} {'giù' if pressed else 'su'}", file=sys.stderr)
        with self._lock:
            if pressed:
                self._pressed_at[key] = time.time()
                self._held.add(key)  # riscontro immediato: il tasto si illumina finché è premuto
                self.redraw()
                return
            self._held.discard(key)
            self.redraw()
            started = self._pressed_at.pop(key, None)
            if started is None:
                return
            long_press = time.time() - started >= self.cfg["long_press_seconds"]
            choice = next((c for c, k in PERMISSION_KEYS.items() if k == key), None)
            if key in self.option_leds():
                self.choose(OPTION_KEYS.index(key))
            elif choice is not None:
                self.answer(choice)
            elif key == CLEAR_KEY:
                self.clear_stopped()
            elif key[0] == "grid" and key[2] < 8:
                slot = key[1] * 8 + key[2]
                if long_press:
                    self.forget(slot)
                else:
                    self.open(slot)

    # --- azioni (dal Launchpad o dalla pagina web) -------------------------
    def _at(self, slot: int) -> Session | None:
        return next((s for s in self.sessions.values() if s.slot == slot), None)

    def open(self, slot: int) -> bool:
        """Segna il turno come visto e porta in primo piano l'app. False se il pad è vuoto."""
        with self._lock:
            sess = self._at(slot)
            if sess is None:
                return False
            if sess.session_id in self.pending:
                self._selected = sess.session_id  # i tondi rispondono a questa richiesta
            if sess.state in ("done", "idle"):
                sess.state = "seen"
            self.redraw()
        self.focus_app(sess)
        return True

    def forget(self, slot: int) -> bool:
        with self._lock:
            sess = self._at(slot)
            if sess is None:
                return False
            self._fade_out(sess)
            del self.sessions[sess.session_id]
            self.redraw()
            return True

    def clear_stopped(self) -> None:
        """Toglie dalla griglia tutte le sessioni ferme (done/idle/seen/error)."""
        with self._lock:
            for sess in [s for s in self.sessions.values() if s.state in STOPPED]:
                self._fade_out(sess)
                del self.sessions[sess.session_id]
            self.redraw()

    def focus_app(self, sess: Session | None = None) -> None:
        """Porta in primo piano l'app Claude e, se possibile, apre la chat della sessione."""
        system = platform.system()
        cmd = self.cfg["focus_command"].get(system)
        if cmd:  # comando scelto nel config: vince su tutto
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                print(f"[soundpad] focus fallito: {exc}", file=sys.stderr)
            return
        if system not in ("Windows", "Darwin"):
            return
        from . import claudeapp

        if sess is not None and sess.local_id and self.cfg["open_chat"]:
            app = claudeapp.AppSession(sess.local_id, sess.title)
        else:
            app = None  # porta in primo piano l'app e basta
        # Link, focus e ricerca nella barra laterale possono richiedere 1-2 s: fuori dal thread del
        # Launchpad o dell'HTTP
        threading.Thread(target=claudeapp.open_chat, args=(app,), daemon=True).start()

    # --- disegno -----------------------------------------------------------
    def expire_stale(self) -> None:
        cutoff = time.time() - self.cfg["session_ttl_hours"] * 3600
        with self._lock:
            stale = [sid for sid, s in self.sessions.items() if s.last_seen < cutoff]
            for sid in stale:
                del self.sessions[sid]
            if stale:
                self.redraw()

    def target(self, now: float | None = None) -> dict[Key, Led]:
        """Griglia di base, senza effetti: un pad per sessione più i tasti di servizio."""
        now = self.clock() if now is None else now
        leds: dict[Key, Led] = {}
        colors = self.cfg["colors"]
        for s in self.sessions.values():
            color, flash = colors[s.state]
            led = Led(color, bool(flash))
            if self.animated and s.state == "working" and not flash:
                # "al lavoro" respira: stesso tono del colore configurato, livello che sale e scende
                led = Led.of(effects.breathe(now, effects.hue_of(led.rg)))
            leds[("grid", s.slot // 8, s.slot % 8)] = led
        leds[HEARTBEAT_KEY] = Led("green_low")
        if any(s.state == "needs_permission" for s in self.sessions.values()):
            leds[ALERT_KEY] = Led("red", True)
        if any(s.state in STOPPED for s in self.sessions.values()):
            leds[CLEAR_KEY] = Led("amber_low")
        leds.update(self.meter())
        target = self.permission_target()
        if target is not None and target.questions:
            if target.question is not None and target.question.get("multiSelect"):
                leds[PERMISSION_KEYS["once"]] = Led("green" if target.picked else "green_low")
            leds[PERMISSION_KEYS["deny"]] = Led("red_low")
        elif target is not None:
            leds[PERMISSION_KEYS["once"]] = Led("green")
            if target.can_always:
                leds[PERMISSION_KEYS["always"]] = Led("amber")
            leds[PERMISSION_KEYS["deny"]] = Led("red_low")
        leds.update(self.option_leds())
        return leds

    def option_leds(self) -> dict[Key, Led]:
        """Riga in basso durante una domanda: opzioni in ambra (verde se scelte), il resto spento."""
        target = self.permission_target()
        q = target.question if target is not None else None
        if q is None:
            return {}
        count = min(len(q["options"]), len(OPTION_KEYS))
        return {key: (Led("green") if i in target.picked else Led("amber")) if i < count else Led()
                for i, key in enumerate(OPTION_KEYS)}

    def meter(self) -> dict[Key, Led]:
        """Colonna di stato (tasti scene, righe 0-6, dal basso): rosso = ti aspetta una risposta
        o c'è un errore, verde = ha finito, ambra tenue = sta lavorando."""
        waiting = sum(s.state in ("needs_permission", "error") for s in self.sessions.values())
        finished = sum(s.state in ("done", "idle") for s in self.sessions.values())
        working = sum(s.state in ("working", "compacting") for s in self.sessions.values())
        cells = ["red"] * waiting + ["green"] * finished + ["amber_low"] * working
        return {METER_KEYS[i]: Led(color) for i, color in enumerate(cells[: len(METER_KEYS)])}

    def frame(self, now: float | None = None) -> dict[Key, Led]:
        """Quello che il Launchpad deve mostrare adesso: base + effetti + screensaver + tasti premuti."""
        now = self.clock() if now is None else now
        leds = self.target(now)
        if not self.animated:
            return leds
        overlay: dict[Key, effects.RG] = {}
        self.effects = [e for e in self.effects if not e.finished(now)]
        for effect in self.effects:
            for key, rg in effect.render(now).items():
                overlay[key] = effects.blend(overlay.get(key), rg)

        # Screensaver: solo a griglia vuota da un po' e senza altri effetti in corso
        if self.sessions:
            self._empty_since, self._rain = None, None
        elif self._empty_since is None:
            self._empty_since = now
        delay = self.cfg["screensaver_after_seconds"]
        if not self.sessions and delay and now - self._empty_since >= delay and not self.effects:
            if self._rain is None:
                self._rain = effects.Rain(now)
            for key, rg in self._rain.render(now).items():
                overlay.setdefault(key, rg)

        for key in self._held:
            overlay[key] = (3, 3)
        options = self.option_leds()
        for key, rg in overlay.items():
            if key in options and key not in self._held:
                continue  # le onde non coprono le opzioni di una domanda: devono restare leggibili
            base = leds.get(key, Led())
            leds[key] = Led.of(effects.blend(base.rg, rg))  # durante un effetto la luce è fissa
        return leds

    def redraw(self, full: bool = False) -> None:
        """Invia solo i LED cambiati: il MIDI del Launchpad originale è lento."""
        with self._lock:
            new = {k: led for k, led in self.frame().items() if led != Led()}
            if full:
                self.pad.clear()
                self._drawn = {}
            changed = full
            for key in set(self._drawn) | set(new):
                led = new.get(key, Led())
                if self._drawn.get(key, Led()) != led:
                    self.pad.set(key, led)
                    changed = True
            self._drawn = new
            if changed and hasattr(self.pad, "flush"):
                self.pad.flush()

    def _permission_info(self, session_id: str) -> dict | None:
        pending = self.pending.get(session_id)
        if pending is None:
            return None
        target = self.permission_target()
        info = {"tool": pending.tool_name, "summary": pending.summary, "can_always": pending.can_always,
                "target": target is pending}
        q = pending.question
        if q is not None:
            info["question"] = {
                "header": q.get("header") if isinstance(q.get("header"), str) else "",
                "options": [o["label"] for o in q["options"][: len(OPTION_KEYS)]],
                "multi": bool(q.get("multiSelect")),
                "picked": sorted(pending.picked),
                "number": len(pending.answers) + 1,
                "count": len(pending.questions),
            }
        return info

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self.pad.connected,
                "last_input": getattr(self.pad, "last_input", None),  # ultimo tasto ricevuto dal Launchpad
                "now": time.time(),
                "colors": self.cfg["colors"],
                "sessions": [
                    {
                        "session_id": s.session_id,
                        "slot": s.slot,
                        "state": s.state,
                        "cwd": s.cwd,
                        "last_seen": s.last_seen,
                        "title": s.title,
                        "permission": self._permission_info(s.session_id),
                    }
                    for s in sorted(self.sessions.values(), key=lambda s: s.slot)
                ],
            }


def app_settings(board: Board, window: bool) -> dict:
    return {
        "window": window,
        "hooks_url": hook_url(board.cfg),
        "hooks_installed": install_hooks.is_installed(hook_url(board.cfg)),
        "autostart": autostart.is_enabled() if autostart.supported() else None,
    }


def hook_url(cfg: dict) -> str:
    return f"http://127.0.0.1:{cfg['port']}/event"


def make_handler(board: Board, sim: SimLaunchpad | None, on_show: Callable[[], None] | None = None):
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

        def _local(self) -> bool:
            # Le impostazioni cambiano questo computer: mai dalla rete, anche con host = "0.0.0.0"
            return self.client_address[0] in ("127.0.0.1", "::1")

        def _is_json(self) -> bool:
            # Solo JSON dichiarato: un altro sito aperto nel browser non può inviarlo senza
            # una richiesta preliminare CORS, che qui non viene mai accettata.
            if self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json":
                return True
            self._json(415, {"error": "serve Content-Type: application/json"})
            return False

        def _app(self, body: dict) -> None:
            if not self._local():
                self._json(403, {"error": "solo da questo computer"})
                return
            if not self._is_json():
                return
            try:
                if self.path == "/app/hooks":
                    install_hooks.apply(hook_url(board.cfg), remove=not body.get("installed", True))
                elif self.path == "/app/autostart":
                    autostart.set_enabled(bool(body.get("enabled")))
                elif self.path == "/app/show" and on_show is not None:
                    on_show()
                else:
                    self._json(404, {"error": "not found"})
                    return
            except (OSError, ValueError, RuntimeError) as exc:
                self._json(500, {"error": str(exc)})
                return
            self._json(200, app_settings(board, on_show is not None))

        def do_GET(self) -> None:
            if self.path == "/state":
                self._json(200, board.snapshot())
            elif self.path == "/app/settings":
                if self._local():
                    self._json(200, app_settings(board, on_show is not None))
                else:
                    self._json(403, {"error": "solo da questo computer"})
            elif self.path in ("/", "/index.html"):
                data = DASHBOARD.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json(404, {"error": "not found"})

        def _action(self, body: dict) -> None:
            if not self._is_json():
                return
            action = body.get("action")
            if action in permissions.CHOICES:
                # approvare comandi su questo computer: mai dalla rete, anche con host = "0.0.0.0"
                if not self._local():
                    self._json(403, {"error": "solo da questo computer"})
                    return
                sid = body.get("session_id")
                done = board.answer(action, sid if isinstance(sid, str) else None)
                self._json(200 if done else 404, board.snapshot())
                return
            if action == "option":
                index, sid = body.get("index"), body.get("session_id")
                if not self._local():
                    self._json(403, {"error": "solo da questo computer"})
                    return
                if not isinstance(index, int) or isinstance(index, bool):
                    self._json(400, {"error": 'usa {"action": "option", "index": 0..7, "session_id": "..."}'})
                    return
                done = board.choose(index, sid if isinstance(sid, str) else None)
                self._json(200 if done else 404, board.snapshot())
                return
            if action == "clear":
                board.clear_stopped()
                self._json(200, board.snapshot())
                return
            slot = body.get("slot")
            if action not in ("open", "forget") or not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < 64:
                self._json(400, {"error": 'usa {"action": "open"|"forget", "slot": 0..63} o {"action": "clear"}'})
                return
            done = board.open(slot) if action == "open" else board.forget(slot)
            self._json(200 if done else 404, board.snapshot())

        def do_POST(self) -> None:
            try:
                body = self._body()
            except ValueError:  # comprende json.JSONDecodeError e Content-Length non numerico
                self._json(400, {"error": "json non valido"})
                return
            if self.path == "/event":
                if LOG_EVENTS:
                    print(f"[evento] {time.strftime('%H:%M:%S')} {body.get('hook_event_name')} "
                          f"{body.get('tool_name') or ''} {str(body.get('session_id'))[:8]}", file=sys.stderr)
                decision = None
                if body.get("hook_event_name") == "PermissionRequest":
                    decision = board.request_permission(body)  # può aspettare che tu risponda dal pad
                else:
                    board.handle_event(body)
                if decision is not None:
                    self._json(200, decision)
                else:
                    # corpo vuoto: qualsiasi JSON verrebbe letto da Claude Code come output dell'hook
                    self.send_response(204)
                    self.end_headers()
            elif self.path == "/action":
                self._action(body)
            elif self.path.startswith("/app/"):
                self._app(body)
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


class Service:
    """Launchpad + server HTTP + ciclo di manutenzione. Usato dal comando `soundpad` e dall'app."""

    def __init__(self, cfg: dict, sim: bool = False, quiet: bool = False,
                 on_show: Callable[[], None] | None = None) -> None:
        self.cfg = cfg
        self.sim = SimLaunchpad(quiet=quiet) if sim else None
        self.pad = self.sim or make_launchpad()
        # Titoli delle chat dall'app desktop, per mostrarli e per aprire la chat giusta (focus_app)
        index = SessionIndex() if sys.platform in ("win32", "darwin") else None
        self.board = Board(self.pad, cfg, index)
        # Può sollevare OSError se la porta è occupata (demone già acceso)
        self.server = ThreadingHTTPServer((cfg["host"], cfg["port"]), make_handler(self.board, self.sim, on_show))
        self._stop = threading.Event()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def start(self) -> None:
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"[soundpad] in ascolto su http://{self.cfg['host']}:{self.cfg['port']}", file=sys.stderr)
        if self.sim is not None:
            self.board.boot()
        self.board.redraw(full=True)
        if self.cfg["animations"]:
            threading.Thread(target=self._animate, daemon=True).start()

    def _animate(self) -> None:
        """Ridisegna ~15 volte al secondo: redraw manda al Launchpad solo i LED cambiati."""
        while not self._stop.wait(1 / 15):
            try:
                self.board.redraw()
            except Exception as exc:  # un errore MIDI non deve fermare le animazioni per sempre
                print(f"[soundpad] animazione: {exc}", file=sys.stderr)
                self._stop.wait(1)

    def run(self) -> None:
        """Ciclo bloccante fino a stop(): ricollega il Launchpad e fa scadere le sessioni vecchie."""
        while not self._stop.is_set():
            if self.pad.try_connect():
                self.board.boot()
                self.board.redraw(full=True)  # appena collegato: i LED sono spenti, ridisegna tutto
            self.board.expire_stale()
            self._stop.wait(2)

    def stop(self) -> None:
        self._stop.set()
        self.board.release_all()
        self.server.shutdown()
        self.server.server_close()
        self.pad.clear()
        if hasattr(self.pad, "close"):
            self.pad.close()  # USB: rilascia il dispositivo, o il prossimo avvio lo trova bloccato


def _interrupt(*_) -> None:
    raise KeyboardInterrupt


def main() -> None:
    parser = argparse.ArgumentParser(description="Launchpad come spia degli hook di Claude Code")
    parser.add_argument("--config", type=Path, default=config_path())
    parser.add_argument("--sim", action="store_true", help="usa il Launchpad simulato nel terminale")
    parser.add_argument("--quiet", action="store_true", help="con --sim, non disegnare la griglia")
    args = parser.parse_args()

    service = Service(load_config(args.config), sim=args.sim, quiet=args.quiet)
    # launchd (e `kill`) fermano il demone con SIGTERM: come Ctrl+C, per passare da service.stop()
    signal.signal(signal.SIGTERM, _interrupt)
    service.start()
    try:
        service.run()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()


if __name__ == "__main__":
    main()
