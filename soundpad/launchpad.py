"""Driver per il Novation Launchpad originale (NOVLPD01, 2009).

Protocollo (dal Launchpad Programmer's Reference):
- griglia 8x8 + colonna destra (tasti "scene"): Note On canale 1, nota = 16*riga + colonna
  (colonna 8 = tasto scene della riga)
- riga superiore (tasti tondi): Control Change 104-111
- colore: velocity = 16*verde + rosso + flag, con verde/rosso in 0..3
  flag 12 = luce fissa, flag 8 = lampeggio (serve il lampeggio automatico, B0 00 28)
- pressione: velocity 127, rilascio: velocity 0

Due modi di parlargli:
- `MidiLaunchpad`: porte MIDI del sistema. Su Windows c'è il driver Novation, su Linux il kernel.
- `UsbLaunchpad`: USB diretto con libusb. Serve su macOS, dove il driver Novation non esiste più per
  Apple Silicon e il dispositivo (classe USB vendor-specific, non MIDI standard) non ha porte CoreMIDI.
"""

from __future__ import annotations

import sys
import threading
import time
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


def key_event(status: int, data1: int, data2: int) -> tuple[Key, bool] | None:
    """Messaggio MIDI in arrivo dal Launchpad -> (tasto, premuto?). None se non è un tasto."""
    kind = status & 0xF0
    if kind in (0x80, 0x90):
        key = key_for_note(data1)
        return (key, kind == 0x90 and data2 > 0) if key is not None else None
    if kind == 0xB0 and data1 in TOP_ROW_CC:
        return ("top", 0, data1 - 104), data2 > 0
    return None


class MidiStream:
    """Ricompone i messaggi MIDI da un flusso di byte grezzi (ingresso USB del Launchpad).

    Il Launchpad usa il running status (lo status si ripete solo quando cambia) e i messaggi possono stare
    a cavallo di due pacchetti USB, o più di uno nello stesso pacchetto.
    """

    def __init__(self) -> None:
        self._status = 0
        self._data: list[int] = []

    def feed(self, chunk: bytes) -> list[tuple[int, int, int]]:
        out = []
        for byte in chunk:
            if byte >= 0xF8:  # real time: non interrompe il messaggio in corso
                continue
            if byte >= 0x80:
                self._status = byte if byte < 0xF0 else 0  # sysex e simili: ignorati
                self._data = []
                continue
            if not self._status:
                continue
            self._data.append(byte)
            if len(self._data) == 2:  # note on/off e control change hanno due byte di dati
                out.append((self._status, *self._data))
                self._data = []
        return out


def encode_stream(messages: list[tuple[int, int, int]]) -> bytes:
    """Messaggi MIDI -> byte con running status: 2 byte a LED invece di 3 sul canale USB lento."""
    out = bytearray()
    last = None
    for status, data1, data2 in messages:
        if status != last:
            out.append(status)
            last = status
        out += bytes((data1, data2))
    return bytes(out)


def led_message(key: Key, led: Led) -> tuple[int, int, int]:
    kind, row, col = key
    if kind == "top":
        return (0xB0, 104 + col, led.velocity())
    return (0x90, note_for(row, col), led.velocity())


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
        self.last_input: float | None = None  # ora dell'ultimo messaggio ricevuto dal dispositivo

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
        self.last_input = time.time()
        if self._on_press is None or msg.type not in ("note_on", "note_off", "control_change"):
            return
        event = key_event(*msg.bytes()[:3])
        if event is not None:
            self._on_press(*event)

    # --- output ------------------------------------------------------------
    def _send_cc(self, control: int, value: int) -> None:
        with self._lock:
            if self._outport is not None:
                self._outport.send(self._mido.Message("control_change", control=control, value=value))

    def set(self, key: Key, led: Led) -> None:
        with self._lock:
            if self._outport is not None:
                self._outport.send(self._mido.Message.from_bytes(led_message(key, led)))

    def clear(self) -> None:
        self._send_cc(0, 0)
        self._send_cc(0, 0x28)


class UsbLaunchpad:
    """Launchpad fisico via libusb (pyusb). Stessa interfaccia di MidiLaunchpad.

    USB del Launchpad: un'interfaccia vendor-specific con due endpoint interrupt da 8 byte, 0x02 in uscita e
    0x81 in ingresso, che portano MIDI grezzo (come il quirk QUIRK_MIDI_RAW_BYTES del driver Linux).
    Il dispositivo è low-speed: un pacchetto ogni ~8 ms, quindi `set` accumula e `flush` spedisce tutto
    insieme con il running status (4 LED per pacchetto).
    """

    VENDOR, PRODUCT = 0x1235, 0x000E
    EP_OUT, EP_IN = 0x02, 0x81
    PACKET = 8
    # Dove cercare libusb se ctypes non la trova da sé (su macOS non guarda nelle cartelle di Homebrew)
    LIBUSB_PATHS = ("/opt/homebrew/lib/libusb-1.0.0.dylib", "/usr/local/lib/libusb-1.0.0.dylib")

    def __init__(self) -> None:
        import usb.backend.libusb1  # import locale: serve solo con questo driver
        import usb.core
        import usb.util

        self._usb = usb
        self._backend = usb.backend.libusb1.get_backend()
        bundled = getattr(sys, "_MEIPASS", None)  # soundpad.app porta con sé libusb
        paths = ((f"{bundled}/libusb-1.0.0.dylib",) if bundled else ()) + self.LIBUSB_PATHS
        for path in paths:
            if self._backend is not None:
                break
            self._backend = usb.backend.libusb1.get_backend(find_library=lambda _name, p=path: p)
        if self._backend is None:
            raise RuntimeError("libusb non trovata: installala con `brew install libusb`")
        self._dev = None
        self._reader: threading.Thread | None = None
        self._pending: list[tuple[int, int, int]] = []
        self._on_press: PressHandler | None = None
        self._lock = threading.Lock()
        self._stream = MidiStream()
        # (bus, indirizzo) di un Launchpad che non risponde: si riprova solo quando ricompare con un
        # indirizzo nuovo, cioè dopo aver staccato e riattaccato il cavo
        self._stuck: tuple[int, int] | None = None
        self._fresh = False  # appena collegato: il primo errore di scrittura vuol dire "bloccato"
        # Diagnostica per la pagina: su macOS l'ingresso può smettere di funzionare senza errori (HANDOFF.md)
        self.last_input: float | None = None

    # --- connessione -------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._dev is not None

    def try_connect(self) -> bool:
        """Apre il dispositivo se è collegato. Ritorna True se è appena stato collegato."""
        if self.connected:
            return False  # uno scollegamento lo scopre il thread di lettura (o una scrittura che fallisce)
        dev = self._usb.core.find(idVendor=self.VENDOR, idProduct=self.PRODUCT, backend=self._backend)
        if dev is None:
            self._stuck = None
            return False
        if (dev.bus, dev.address) == self._stuck:
            return False
        try:
            # Solo se serve: riconfigurare un dispositivo già configurato blocca gli endpoint di questo
            # firmware, e poi solo staccare il cavo lo rimette in sesto. Mai usare dev.reset() per lo stesso
            # motivo: il Launchpad sparisce dal bus.
            try:
                dev.get_active_configuration()
            except self._usb.core.USBError:
                dev.set_configuration()
            self._usb.util.claim_interface(dev, 0)
        except self._usb.core.USBError as exc:
            print(f"[launchpad] USB non apribile (un altro programma lo usa?): {exc}", file=sys.stderr)
            self._usb.util.dispose_resources(dev)
            return False
        with self._lock:
            self._dev = dev
            self._stream = MidiStream()
            self._pending = []
            self._fresh = True
        self._reader = threading.Thread(target=self._read_loop, args=(dev,), daemon=True)
        self._reader.start()
        self.clear()
        if not self.connected:
            return False  # la prima scrittura è fallita: _write ha già spiegato cosa fare
        print("[launchpad] collegato via USB", file=sys.stderr)
        return True

    def close(self) -> None:
        """Chiusura ordinata: ferma la lettura e rilascia l'interfaccia prima di uscire.

        Un processo che muore con un trasferimento in corso lascia il Launchpad bloccato (Errno 5 alla prima
        scrittura) fino a quando si stacca il cavo.
        """
        with self._lock:
            dev, self._dev = self._dev, None
        if dev is None:
            return
        if self._reader is not None:
            self._reader.join(timeout=1)  # la lettura ha un timeout di 500 ms
        try:
            self._usb.util.release_interface(dev, 0)
        except Exception:
            pass
        self._usb.util.dispose_resources(dev)

    def _close(self, dev, quiet: bool = False) -> None:
        """Chiude `dev` se è ancora quello in uso. Da chiamare con il lock preso."""
        if self._dev is not dev:
            return
        self._dev = None
        self._pending = []
        try:
            self._usb.util.release_interface(dev, 0)
        except Exception:
            pass
        self._usb.util.dispose_resources(dev)
        if not quiet:
            print("[launchpad] scollegato", file=sys.stderr)

    # --- input -------------------------------------------------------------
    def on_press(self, handler: PressHandler) -> None:
        self._on_press = handler

    def _read_loop(self, dev) -> None:
        errors = 0
        while self._dev is dev:
            try:
                chunk = dev.read(self.EP_IN, self.PACKET, timeout=500).tobytes()
            except self._usb.core.USBTimeoutError:
                continue
            except self._usb.core.USBError:
                errors += 1  # un errore isolato capita; tre di fila = cavo staccato
                if errors >= 3:
                    with self._lock:
                        self._close(dev)
                    return
                time.sleep(0.1)
                continue
            errors = 0
            self.feed(chunk)

    def feed(self, chunk: bytes) -> None:
        """Byte arrivati dal dispositivo -> pressioni. Separato dal thread di lettura per i test."""
        if chunk:
            self.last_input = time.time()
        for message in self._stream.feed(chunk):
            event = key_event(*message)
            if event is not None and self._on_press is not None:
                self._on_press(*event)

    # --- output ------------------------------------------------------------
    def _write(self, data: bytes) -> None:
        """Spedisce `data` a pacchetti da 8 byte. Da chiamare con il lock preso."""
        dev = self._dev
        if dev is None:
            return
        try:
            for i in range(0, len(data), self.PACKET):
                dev.write(self.EP_OUT, data[i:i + self.PACKET], timeout=1000)
        except self._usb.core.USBError as exc:
            if self._fresh:
                self._stuck = (dev.bus, dev.address)
                print(f"[launchpad] il Launchpad non risponde ({exc}): stacca e riattacca il cavo USB",
                      file=sys.stderr)
            else:
                print(f"[launchpad] scrittura USB fallita: {exc}", file=sys.stderr)
            self._close(dev, quiet=self._fresh)
            return
        self._fresh = False

    def set(self, key: Key, led: Led) -> None:
        with self._lock:
            if self._dev is not None:
                self._pending.append(led_message(key, led))

    def flush(self) -> None:
        with self._lock:
            pending, self._pending = self._pending, []
            if pending:
                self._write(encode_stream(pending))

    def clear(self) -> None:
        with self._lock:
            self._pending = []
            self._write(bytes((0xB0, 0, 0, 0, 0x28)))  # reset, poi lampeggio automatico del dispositivo


def make_launchpad() -> MidiLaunchpad | UsbLaunchpad:
    """Il driver giusto per il sistema: USB diretto su macOS, porte MIDI altrove."""
    return UsbLaunchpad() if sys.platform == "darwin" else MidiLaunchpad()


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
