"""Costruisce l'app: dist/soundpad.exe su Windows (un solo file), dist/soundpad.app su macOS.

    uv run --extra app --group build python build.py
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

from soundpad.app import icon_image
from soundpad.autostart import LABEL

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
# libusb per il Launchpad via USB (macOS): si mette dentro l'app, così non serve Homebrew per usarla
LIBUSB = ("/opt/homebrew/lib/libusb-1.0.0.dylib", "/usr/local/lib/libusb-1.0.0.dylib")


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    entry = BUILD / "soundpad_app.py"  # PyInstaller vuole uno script, non un modulo con import relativi
    entry.write_text("from soundpad.app import main\nmain()\n", encoding="utf-8")

    sep = os.pathsep  # separatore di --add-data: ";" su Windows, ":" su macOS
    args = [
        str(entry),
        "--name", "soundpad",
        "--windowed",  # niente terminale; su macOS produce soundpad.app
        "--noconfirm",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'soundpad' / 'dashboard.html'}{sep}soundpad",
        "--hidden-import", "mido.backends.rtmidi",  # mido carica il backend per nome, PyInstaller non lo vede
        "--paths", str(ROOT),
    ]
    if sys.platform == "darwin":
        icns = BUILD / "soundpad.icns"
        icon_image(1024).save(icns)
        libusb = next((p for p in LIBUSB if Path(p).exists()), None)
        if libusb is None:
            raise SystemExit("libusb non trovata: brew install libusb")
        args += [
            "--icon", str(icns),
            "--osx-bundle-identifier", LABEL,
            "--add-binary", f"{libusb}{sep}.",
            "--hidden-import", "pystray._darwin",  # pystray sceglie il backend a runtime
        ]
    else:
        ico = BUILD / "soundpad.ico"
        icon_image(256).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
        args += ["--onefile", "--icon", str(ico)]

    PyInstaller.__main__.run(args)

    if sys.platform == "darwin":
        app = DIST / "soundpad.app"
        info = app / "Contents" / "Info.plist"
        with info.open("rb") as fh:
            plist = plistlib.load(fh)
        # Solo icona nella barra dei menu, niente icona nel Dock: come l'icona vicino all'orologio su Windows
        plist["LSUIElement"] = True
        plist["NSHumanReadableCopyright"] = "MIT License"
        with info.open("wb") as fh:
            plistlib.dump(plist, fh)
        # Info.plist cambiato: la firma ad hoc di PyInstaller non vale più, va rifatta
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
        print(f"\npronto: {app}\nconfig facoltativo: ~/Library/Application Support/soundpad/config.toml")
    else:
        # config.toml accanto all'exe: è lì che l'app lo cerca
        shutil.copy2(ROOT / "config.toml", DIST / "config.toml")
        print(f"\npronto: {DIST / 'soundpad.exe'}")


if __name__ == "__main__":
    main()
