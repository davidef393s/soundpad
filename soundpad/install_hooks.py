"""Registra (o rimuove) gli hook HTTP di soundpad in ~/.claude/settings.json.

Idempotente: riconosce i propri hook dall'URL e non li duplica. Salva una copia di backup
prima di scrivere.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import tomllib
from pathlib import Path

from .paths import config_path

CONFIG_PATH = config_path()
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

EVENTS = [
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "PermissionDenied",
    "Notification",
    "PreCompact",
    "Stop",
    "StopFailure",
]
# Eventi che accettano un matcher sul nome dello strumento: "*" = tutti
MATCHER_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied"}
# Secondi che Claude Code aspetta la risposta. PermissionRequest resta aperto mentre il demone
# attende che tu risponda dal Launchpad (permission_wait_seconds, max 110): gli altri rispondono subito.
TIMEOUTS = {"PermissionRequest": 120}
DEFAULT_TIMEOUT = 2


def is_ours(group: dict, url: str) -> bool:
    return any(h.get("type") == "http" and h.get("url") == url for h in group.get("hooks", []))


def our_group(event: str, url: str) -> dict:
    group = {"hooks": [{"type": "http", "url": url, "timeout": TIMEOUTS.get(event, DEFAULT_TIMEOUT)}]}
    return {"matcher": "*", **group} if event in MATCHER_EVENTS else group


def install(settings: dict, url: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event in EVENTS:
        groups = [g for g in hooks.get(event, []) if not is_ours(g, url)]
        groups.append(our_group(event, url))
        hooks[event] = groups
    return settings


def uninstall(settings: dict, url: str) -> dict:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_ours(g, url)]
        if not hooks[event]:
            del hooks[event]
    if not hooks:
        settings.pop("hooks", None)
    return settings


def default_url(config_path: Path = CONFIG_PATH) -> str:
    """URL del demone locale, con la porta letta da config.toml (47800 se manca)."""
    port = 47800
    if config_path.exists():
        with config_path.open("rb") as fh:
            port = tomllib.load(fh).get("port", port)
    return f"http://127.0.0.1:{port}/event"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def is_installed(url: str, path: Path | None = None) -> bool:
    """True se tutti gli eventi hanno l'hook verso `url`, nella versione attuale (timeout compresi)."""
    try:
        hooks = _read(path or SETTINGS_PATH).get("hooks", {})
    except (OSError, ValueError):
        return False
    return all(our_group(event, url) in hooks.get(event, []) for event in EVENTS)


def apply(url: str, path: Path | None = None, remove: bool = False) -> Path | None:
    """Installa (o rimuove) gli hook scrivendo `path`. Ritorna il file di backup, se c'era un file."""
    path = path or SETTINGS_PATH
    settings = _read(path)
    result = uninstall(settings, url) if remove else install(settings, url)
    backup = None
    if path.exists():
        backup = path.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=default_url(), help="default: porta presa da config.toml")
    parser.add_argument("--settings", type=Path, default=SETTINGS_PATH)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="mostra il risultato senza scrivere")
    args = parser.parse_args()

    if args.dry_run:
        settings = _read(args.settings)
        result = uninstall(settings, args.url) if args.uninstall else install(settings, args.url)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    backup = apply(args.url, args.settings, remove=args.uninstall)
    if backup:
        print(f"backup: {backup}")
    print(("rimossi" if args.uninstall else "installati") + f" gli hook soundpad in {args.settings}")


if __name__ == "__main__":
    main()
