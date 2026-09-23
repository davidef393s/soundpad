"""Avvio automatico con Windows: una voce in HKCU\\...\\Run che lancia soundpad.exe nascosto.

Funziona solo con l'eseguibile: con `uv run` non c'è un programma stabile da registrare.
"""

from __future__ import annotations

import sys

from .paths import frozen

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "soundpad"


def supported() -> bool:
    return sys.platform == "win32" and frozen()


def command() -> str:
    return f'"{sys.executable}" --hidden'


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE)
    except OSError:
        return False
    return value == command()


def set_enabled(enabled: bool) -> None:
    if not supported():
        raise RuntimeError("l'avvio automatico funziona solo con soundpad.exe")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass
