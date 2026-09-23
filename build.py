"""Costruisce dist/soundpad.exe (un solo file, senza terminale).

    uv run --extra app --group build python build.py
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import PyInstaller.__main__

from soundpad.app import icon_image

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "soundpad.ico"
    icon_image(256).save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])

    entry = BUILD / "soundpad_app.py"  # PyInstaller vuole uno script, non un modulo con import relativi
    entry.write_text("from soundpad.app import main\nmain()\n", encoding="utf-8")

    sep = os.pathsep  # separatore di --add-data: ";" su Windows, ":" su macOS
    PyInstaller.__main__.run([
        str(entry),
        "--name", "soundpad",
        "--onefile",
        "--noconsole",
        "--noconfirm",
        "--icon", str(ico),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'soundpad' / 'dashboard.html'}{sep}soundpad",
        "--hidden-import", "mido.backends.rtmidi",  # mido carica il backend per nome, PyInstaller non lo vede
        "--paths", str(ROOT),
    ])

    # config.toml accanto all'exe: è lì che l'app lo cerca
    shutil.copy2(ROOT / "config.toml", ROOT / "dist" / "config.toml")
    print(f"\npronto: {ROOT / 'dist' / 'soundpad.exe'}")


if __name__ == "__main__":
    main()
