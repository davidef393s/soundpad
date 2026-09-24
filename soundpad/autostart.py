"""Avvio automatico all'accesso.

- Windows: una voce in HKCU\\...\\Run che lancia soundpad.exe nascosto. Solo con l'eseguibile: con `uv run`
  non c'è un programma stabile da registrare.
- macOS: un LaunchAgent in ~/Library/LaunchAgents che lancia il demone con il Python del progetto
  (`.venv` di uv) o l'app costruita. Vale dal prossimo accesso: accenderlo non avvia un secondo demone
  accanto a quello già acceso, che fallirebbe sulla porta occupata.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from .paths import data_dir, frozen

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "soundpad"

LABEL = "io.github.davidef393s.soundpad"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"  # i test lo spostano in una cartella temporanea


def supported() -> bool:
    return (sys.platform == "win32" and frozen()) or sys.platform == "darwin"


def command() -> str:
    return f'"{sys.executable}" --hidden'


def plist_path() -> Path:
    return LAUNCH_AGENTS / f"{LABEL}.plist"


def launch_agent() -> dict:
    """Contenuto del LaunchAgent: stesso interprete e stessa cartella di chi lo registra."""
    if frozen():
        args = [sys.executable, "--hidden"]
    else:
        args = [sys.executable, "-m", "soundpad.daemon"]
    log = str(data_dir() / "soundpad.log")
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": str(Path(__file__).resolve().parent.parent),
        "RunAtLoad": True,
        # Niente KeepAlive: se la porta è occupata il demone esce, e launchd lo rilancerebbe in loop
        "StandardOutPath": log,
        "StandardErrorPath": log,
        # launchd parte con un PATH minimo: serve a `open` e `osascript` usati per portare avanti l'app
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"},
    }


def is_enabled() -> bool:
    if not supported():
        return False
    if sys.platform == "darwin":
        try:
            with plist_path().open("rb") as fh:
                return plistlib.load(fh).get("ProgramArguments") == launch_agent()["ProgramArguments"]
        except (OSError, plistlib.InvalidFileException):
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
    if sys.platform == "darwin":
        if enabled:
            LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
            with plist_path().open("wb") as fh:
                plistlib.dump(launch_agent(), fh)
        else:
            plist_path().unlink(missing_ok=True)
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE)
            except FileNotFoundError:
                pass
