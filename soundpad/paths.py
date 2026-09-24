"""Percorsi che cambiano tra `uv run` (cartella del progetto) e l'eseguibile PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen() -> bool:
    """True se gira come soundpad.exe costruito con PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def config_path() -> Path:
    """config.toml accanto all'exe (Windows), in ~/Library/Application Support/soundpad per soundpad.app
    (dentro il pacchetto .app non si scrive), oppure nella cartella del progetto. Senza file valgono i default."""
    if frozen() and sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "soundpad" / "config.toml"
    if frozen():
        return Path(sys.executable).resolve().parent / "config.toml"
    return Path(__file__).resolve().parent.parent / "config.toml"


def data_dir() -> Path:
    """Cartella per il log (%LOCALAPPDATA%\\soundpad su Windows, ~/Library/Logs/soundpad su macOS)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state"
    path = Path(base) / "soundpad"
    path.mkdir(parents=True, exist_ok=True)
    return path
