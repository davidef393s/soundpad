"""soundpad come app: finestra con la griglia + icona nell'area di notifica.

Chiudere la finestra la nasconde e basta: il demone resta acceso nell'icona vicino all'orologio.
"Esci" dal menu dell'icona ferma tutto. Una seconda copia dell'app mostra la finestra della prima.
"""

from __future__ import annotations

import argparse
import sys
import threading
import urllib.error
import urllib.request

from .daemon import Service, load_config
from .paths import config_path, data_dir

TITLE = "soundpad"


def icon_image(size: int = 64):
    """Icona disegnata al volo: un mini Launchpad 2x2 (rosso, ambra, verde, verde)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 64
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=12 * s, fill=(35, 35, 34, 255))
    colors = [(255, 59, 47), (255, 178, 30), (62, 224, 106), (62, 224, 106)]
    for i, color in enumerate(colors):
        x, y = 9 + (i % 2) * 25, 9 + (i // 2) * 25
        draw.rounded_rectangle((x * s, y * s, (x + 21) * s, (y + 21) * s), radius=4 * s, fill=color)
    return img


def _redirect_output() -> None:
    """Con --noconsole PyInstaller lascia stdout/stderr a None: i print finirebbero in errore."""
    if sys.stderr is None or sys.stdout is None:
        log = open(data_dir() / "soundpad.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log


def _message(text: str) -> None:
    """Finestra di avviso: l'app non ha un terminale dove scrivere gli errori."""
    print(f"[soundpad] {text}", file=sys.stderr)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x30)  # MB_ICONWARNING


def _already_running(port: int) -> str | None:
    """Se sulla porta risponde già soundpad: "app" (e le chiede di mostrarsi) o "daemon"."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/app/show", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=2):
            return "app"
    except urllib.error.HTTPError:
        return "daemon"  # risponde, ma è il demone da terminale: non ha una finestra
    except OSError:
        return None


def main() -> None:
    _redirect_output()
    parser = argparse.ArgumentParser(description="soundpad con finestra e icona")
    parser.add_argument("--hidden", action="store_true", help="parti solo con l'icona (avvio con Windows)")
    parser.add_argument("--sim", action="store_true", help="Launchpad simulato")
    args = parser.parse_args()

    import pystray
    import webview

    cfg = load_config(config_path())
    running = _already_running(cfg["port"])
    if running == "app":
        return  # l'altra copia ha già mostrato la sua finestra
    if running == "daemon":
        _message("soundpad è già acceso in un terminale (uv run soundpad).\n"
                 "Chiudilo con Ctrl+C e riapri l'app.")
        return

    window: webview.Window | None = None
    quitting = threading.Event()

    def show() -> None:
        if window is not None:
            window.show()
            window.restore()

    try:
        service = Service(cfg, sim=args.sim, quiet=True, on_show=show)
    except OSError as exc:
        _message(f"Non riesco ad aprire la porta {cfg['port']}: {exc}\n"
                 "Un altro programma la sta usando. Cambia \"port\" in config.toml.")
        return
    service.start()
    threading.Thread(target=service.run, daemon=True).start()

    window = webview.create_window(
        TITLE, service.url, width=1120, height=780, min_size=(420, 560), hidden=args.hidden,
        background_color="#151514",
    )

    def on_closing() -> bool:
        if quitting.is_set():
            return True
        window.hide()  # chiudere = nascondere: il Launchpad continua a funzionare
        return False

    window.events.closing += on_closing

    def quit_app(icon, _item=None) -> None:
        quitting.set()
        icon.stop()
        window.destroy()

    tray = pystray.Icon(
        TITLE, icon_image(), TITLE,
        menu=pystray.Menu(
            pystray.MenuItem("Apri soundpad", lambda icon, item: show(), default=True),
            pystray.MenuItem("Esci", quit_app),
        ),
    )
    tray.run_detached()

    try:
        webview.start()  # blocca il thread principale finché la finestra non viene distrutta
    finally:
        quitting.set()
        tray.stop()
        service.stop()


if __name__ == "__main__":
    main()
