"""Driver per il Novation Launchpad originale (NOVLPD01, 2009).

Protocollo (dal Launchpad Programmer's Reference):
- griglia 8x8 + colonna destra (tasti "scene"): Note On canale 1, nota = 16*riga + colonna
  (colonna 8 = tasto scene della riga)
- riga superiore (tasti tondi): Control Change 104-111
- colore: velocity = 16*verde + rosso + flag, con verde/rosso in 0..3
  flag 12 = luce fissa, flag 8 = lampeggio (serve il lampeggio automatico, B0 00 28)
- pressione: velocity 127, rilascio: velocity 0
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Callable

FLAG_STEADY = 12
FLAG_FLASH = 8

# (rosso, verde), ciascuno 0..3
COLORS: dict[str, tuple[int, int]] = {
    "off": (0, 0),
    "red_low": (1, 0),
    "red": (3, 0),
    "green_low": (0, 1),
    "green": (0, 3),
    "amber_low": (1, 1),
    "amber": (3, 3),
    "orange": (3, 2),
    "yellow": (2, 3),
}
# Tutte le altre combinazioni rosso/verde, usate dalle animazioni: "r2g1" = rosso 2, verde 1
for _r in range(4):
    for _g in range(4):
        if (_r, _g) not in COLORS.values():
            COLORS[f"r{_r}g{_g}"] = (_r, _g)
# (rosso, verde) -> nome; i nomi "belli" vincono perché inseriti prima
_NAME_OF: dict[tuple[int, int], str] = {}
for _name, _rg in COLORS.items():
    _NAME_OF.setdefault(_rg, _name)

TOP_ROW_CC = range(104, 112)


@dataclass(frozen=True)
class Led:
    color: str = "off"
    flash: bool = False

    def velocity(self) -> int:
        red, green = COLORS[self.color]
        if red == 0 and green == 0:
            return FLAG_STEADY
        return 16 * green + red + (FLAG_FLASH if self.flash else FLAG_STEADY)

    @property
    def rg(self) -> tuple[int, int]:
        return COLORS[self.color]

    @staticmethod
    def of(rg: tuple[int, int], flash: bool = False) -> Led:
        """Led da (rosso, verde) 0..3, con il nome canonico: così Led.of((3, 0)) == Led("red")."""
        name = _NAME_OF[(max(0, min(3, rg[0])), max(0, min(3, rg[1])))]
        return Led(name, flash and name != "off")


# Coordinate di un tasto: ("grid", riga, colonna) con colonna 8 = tasto scene,
# oppure ("top", 0, indice) per la riga di tasti tondi in alto.
Key = tuple[str, int, int]
PressHandler = Callable[[Key, bool], None]  # (tasto, premuto?)


def note_for(row: int, col: int) -> int:
    return 16 * row + col


def key_for_note(note: int) -> Key | None:
    row, col = divmod(note, 16)
    if 0 <= row < 8 and 0 <= col <= 8:
        return ("grid", row, col)
    return None


class MidiLaunchpad:
    """Launchpad fisico via mido + python-rtmidi. Si riconnette se il cavo viene staccato."""

    def __init__(self, port_hint: str = "launchpad") -> None:
        import mido  # import locale: il simulatore non richiede le librerie MIDI

        self._mido = mido
        self._hint = port_hint.lower()
        self._inport = None
        self._outport = None
        self._on_press: PressHandler | None = None
        self._lock = threading.Lock()

    # --- connessione -------------------------------------------------------
    def _find(self, names: list[str]) -> str | None:
        return next((n for n in names if self._hint in n.lower()), None)

    @property
    def connected(self) -> bool:
        return self._outport is not None

    def try_connect(self) -> bool:
        """Apre le porte se il Launchpad è presente. Ritorna True se è appena stato collegato."""
        in_name = self._find(self._mido.get_input_names())
        out_name = self._find(self._mido.get_output_names())
        if self.connected:
            if in_name and out_name:
                return False
            self._close()  # il dispositivo è sparito
            print("[launchpad] scollegato", file=sys.stderr)
            return False
        if not (in_name and out_name):
            return False
        try:
            self._outport = self._mido.open_output(out_name)
            self._inport = self._mido.open_input(in_name, callback=self._on_message)
        except OSError as exc:
            # Su Windows capita se un altro programma (Ableton, Components) tiene aperta la porta
            print(f"[launchpad] porta occupata o non apribile: {exc}", file=sys.stderr)
            self._close()
            return False
        print(f"[launchpad] collegato: {out_name}", file=sys.stderr)
        self._send_cc(0, 0)  # reset
        self._send_cc(0, 0x28)  # lampeggio automatico gestito dal dispositivo
        return True

    def _close(self) -> None:
        for port in (self._inport, self._outport):
            try:
                if port is not None:
                    port.close()
            except Exception:
                pass
        self._inport = self._outport = None

    # --- input -------------------------------------------------------------
    def on_press(self, handler: PressHandler) -> None:
        self._on_press = handler

    def _on_message(self, msg) -> None:
        if self._on_press is None:
            return
        if msg.type in ("note_on", "note_off"):
            key = key_for_note(msg.note)
            pressed = msg.type == "note_on" and msg.velocity > 0
        elif msg.type == "control_change" and msg.control in TOP_ROW_CC:
            key = ("top", 0, msg.control - 104)
            pressed = msg.value > 0
        else:
            return
        if key is not None:
            self._on_press(key, pressed)

    # --- output ------------------------------------------------------------
    def _send_cc(self, control: int, value: int) -> None:
        with self._lock:
            if self._outport is not None:
                self._outport.send(self._mido.Message("control_change", control=control, value=value))

    def set(self, key: Key, led: Led) -> None:
        kind, row, col = key
        with self._lock:
            if self._outport is None:
                return
            if kind == "top":
                msg = self._mido.Message("control_change", control=104 + col, value=led.velocity())
            else:
                msg = self._mido.Message("note_on", note=note_for(row, col), velocity=led.velocity())
            self._outport.send(msg)

    def clear(self) -> None:
        self._send_cc(0, 0)
        self._send_cc(0, 0x28)


class SimLaunchpad:
    """Launchpad finto: disegna la griglia nel terminale. Serve a provare tutto senza dispositivo."""

    _ANSI = {
        "off": "\033[90m·\033[0m",
        "red_low": "\033[31m●\033[0m",
        "red": "\033[91m●\033[0m",
        "green_low": "\033[32m●\033[0m",
        "green": "\033[92m●\033[0m",
        "amber_low": "\033[33m●\033[0m",
        "amber": "\033[93m●\033[0m",
        "orange": "\033[38;5;208m●\033[0m",
        "yellow": "\033[38;5;226m●\033[0m",
    }

    def __init__(self, quiet: bool = False) -> None:
        self.leds: dict[Key, Led] = {}
        self._on_press: PressHandler | None = None
        self._quiet = quiet

    connected = True

    def try_connect(self) -> bool:
        return False

    def on_press(self, handler: PressHandler) -> None:
        self._on_press = handler

    def press(self, key: Key, pressed: bool) -> None:
        if self._on_press:
            self._on_press(key, pressed)

    def set(self, key: Key, led: Led) -> None:
        self.leds[key] = led

    def clear(self) -> None:
        self.leds.clear()

    def render(self) -> str:
        def cell(key: Key) -> str:
            led = self.leds.get(key, Led())
            red, green = led.rg
            # colori delle animazioni senza nome: colore "vero" del terminale
            glyph = self._ANSI.get(led.color) or f"\033[38;2;{red * 85};{green * 85};0m●\033[0m"
            return glyph.replace("●", "◉") if led.flash else glyph

        lines = [" ".join(cell(("top", 0, c)) for c in range(8))]
        for r in range(8):
            lines.append(" ".join(cell(("grid", r, c)) for c in range(8)) + "  " + cell(("grid", r, 8)))
        return "\n".join(lines)

    def flush(self) -> None:
        if not self._quiet:
            print(self.render() + "\n", flush=True)
