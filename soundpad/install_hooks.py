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

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

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


def is_ours(group: dict, url: str) -> bool:
    return any(h.get("type") == "http" and h.get("url") == url for h in group.get("hooks", []))


def install(settings: dict, url: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event in EVENTS:
        groups = [g for g in hooks.get(event, []) if not is_ours(g, url)]
        group = {"hooks": [{"type": "http", "url": url, "timeout": 2}]}
        if event in MATCHER_EVENTS:
            group = {"matcher": "*", **group}
        groups.append(group)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=default_url(), help="default: porta presa da config.toml")
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude" / "settings.json")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="mostra il risultato senza scrivere")
    args = parser.parse_args()

    settings = json.loads(args.settings.read_text(encoding="utf-8")) if args.settings.exists() else {}
    result = uninstall(settings, args.url) if args.uninstall else install(settings, args.url)
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"

    if args.dry_run:
        print(text)
        return
    if args.settings.exists():
        backup = args.settings.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(args.settings, backup)
        print(f"backup: {backup}")
    args.settings.parent.mkdir(parents=True, exist_ok=True)
    args.settings.write_text(text, encoding="utf-8")
    print(("rimossi" if args.uninstall else "installati") + f" gli hook soundpad in {args.settings}")


if __name__ == "__main__":
    main()
