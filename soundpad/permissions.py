"""Richieste di permesso tenute in sospeso finché non rispondi dal Launchpad o dalla pagina.

L'hook PermissionRequest di Claude Code aspetta la risposta HTTP: il demone la trattiene e, quando
premi un tasto, risponde con una decisione (formato dalla documentazione degli hook):

    {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                            "decision": {"behavior": "allow" | "deny", ...}}}

"Accetta sempre" rimanda indietro le `permission_suggestions` ricevute come `updatedPermissions`:
è quello che fa l'opzione "sì, e non chiedere più" del riquadro. Una risposta vuota lascia
decidere l'app come se soundpad non ci fosse.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

CHOICES = ("once", "always", "deny")


@dataclass
class Pending:
    session_id: str
    tool_name: str
    summary: str
    suggestions: list
    created: float = field(default_factory=time.monotonic)
    answered: threading.Event = field(default_factory=threading.Event)
    decision: dict | None = None

    @property
    def can_always(self) -> bool:
        return bool(self.suggestions)

    def resolve(self, decision: dict | None) -> None:
        """Sblocca la risposta HTTP in attesa: decision None = lascia fare all'app."""
        if not self.answered.is_set():
            self.decision = decision
            self.answered.set()


def summarize(tool_name: str, tool_input) -> str:
    """Una riga leggibile su cosa chiede lo strumento (comando, file, indirizzo...)."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "path", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            return text if len(text) <= 160 else text[:157] + "..."
    text = json.dumps(tool_input, ensure_ascii=False)
    return text if len(text) <= 160 else text[:157] + "..."


def from_event(event: dict) -> Pending:
    tool = event.get("tool_name") if isinstance(event.get("tool_name"), str) else "?"
    suggestions = event.get("permission_suggestions")
    return Pending(
        session_id=event["session_id"],
        tool_name=tool,
        summary=summarize(tool, event.get("tool_input")),
        suggestions=suggestions if isinstance(suggestions, list) else [],
    )


def decision_for(choice: str, pending: Pending) -> dict:
    if choice == "deny":
        decision = {"behavior": "deny", "message": "Rifiutato dall'utente con soundpad"}
    elif choice == "always" and pending.can_always:
        decision = {"behavior": "allow", "updatedPermissions": pending.suggestions}
    else:
        decision = {"behavior": "allow"}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
