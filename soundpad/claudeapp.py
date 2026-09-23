"""Aprire nell'app desktop Claude la chat di una sessione Claude Code (solo Windows).

Gli hook danno l'id della sessione CLI (`session_id`). L'app desktop salva per ogni sessione un file
%APPDATA%\\Claude\\claude-code-sessions\\<account>\\<org>\\local_<id>.json con `sessionId` (local_...),
`cliSessionId` e `title`: da lì si ricavano titolo e id interno.

Per aprire la chat:
1. link ufficiale claude://code/continue?session=local_...  L'app lo accetta solo se un'impostazione
   lato server è attiva per l'account; altrimenti lo ignora in silenzio.
2. ripiego: UI Automation di Windows. Nella barra laterale ogni chat è un pulsante chiamato
   "<stato> <titolo>" (es. "Idle Visual synth whiteboard webapp"): lo si cerca e lo si "preme".
   Dipende dall'interfaccia dell'app e da titoli diversi tra loro: se due chat hanno lo stesso
   titolo apre la prima della barra laterale.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSession:
    local_id: str  # "local_..." usato dall'app
    title: str


def sessions_dir() -> Path | None:
    if sys.platform == "darwin":  # percorso macOS non ancora verificato su un Mac vero
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Claude" / "claude-code-sessions" if appdata else None


class SessionIndex:
    """cliSessionId -> AppSession, rileggendo solo i file cambiati (il titolo arriva dopo il primo prompt)."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else sessions_dir()
        self._files: dict[Path, tuple[float, str | None, AppSession | None]] = {}
        self._lock = threading.Lock()

    def lookup(self, cli_session_id: str) -> AppSession | None:
        if self.root is None or not self.root.is_dir():
            return None
        with self._lock:
            self._refresh()
            for _, cli, sess in self._files.values():
                if cli == cli_session_id:
                    return sess
        return None

    def _refresh(self) -> None:
        seen = set()
        for path in self.root.glob("*/*/local_*.json"):
            seen.add(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = self._files.get(path)
            if cached and cached[0] == mtime:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            local_id = data.get("sessionId")
            sess = AppSession(local_id, data.get("title") or "") if isinstance(local_id, str) else None
            self._files[path] = (mtime, data.get("cliSessionId"), sess)
        for gone in set(self._files) - seen:
            del self._files[gone]


# Script PowerShell per il ripiego UI Automation. Titolo passato in una variabile d'ambiente:
# niente problemi di virgolette. Stampa l'esito, uscita 0 = chat aperta.
_UIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$title = $env:SOUNDPAD_TITLE
$p = Get-Process claude -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { 'app Claude non trovata'; exit 2 }
$root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
$any = [System.Windows.Automation.Condition]::TrueCondition
$scope = [System.Windows.Automation.TreeScope]::Descendants
function Header($all) { ($all | Where-Object { $_.Current.Name -like '*, rename session' } | Select-Object -First 1).Current.Name }
# Chromium costruisce l'albero di accessibilità alla prima richiesta: qualche tentativo
for ($i = 0; $i -lt 8; $i++) {
  $all = $root.FindAll($scope, $any)
  if ((Header $all) -eq "$title, rename session") { 'già aperta'; exit 0 }
  $btn = $all | Where-Object {
    $n = $_.Current.Name
    $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and
    $n.EndsWith(" $title") -and -not $n.StartsWith('More options')
  } | Select-Object -First 1
  if ($btn) {
    $btn.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
    "aperta: $($btn.Current.Name)"; exit 0
  }
  Start-Sleep -Milliseconds 400
}
'chat non trovata nella barra laterale'; exit 1
"""


def _log(msg: str) -> None:
    print(f"[soundpad] {msg}", file=sys.stderr)


def open_chat(sess: AppSession, use_link: bool = True) -> None:
    """Porta in primo piano l'app e apre la chat. Bloccante (1-2 s): chiamarla da un thread."""
    from . import winfocus

    if use_link:
        try:
            os.startfile(f"claude://code/continue?session={sess.local_id}")  # type: ignore[attr-defined]
        except OSError as exc:
            _log(f"link claude:// non riuscito: {exc}")
    try:
        winfocus.bring_to_front("claude.exe")
    except OSError as exc:
        _log(f"focus fallito: {exc}")
    if not sess.title:
        return  # senza titolo la barra laterale non si può cercare
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _UIA_SCRIPT],
            env={**os.environ, "SOUNDPAD_TITLE": sess.title},
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _log(f"apri '{sess.title}': {result.stdout.strip() or result.stderr.strip()}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log(f"apertura chat non riuscita: {exc}")
