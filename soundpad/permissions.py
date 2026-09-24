"""Richieste di permesso tenute in sospeso finché non rispondi dal Launchpad o dalla pagina.

L'hook PermissionRequest di Claude Code aspetta la risposta HTTP: il demone la trattiene e, quando
premi un tasto, risponde con una decisione (formato dalla documentazione degli hook):

    {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                            "decision": {"behavior": "allow" | "deny", ...}}}

"Accetta sempre" rimanda indietro le `permission_suggestions` ricevute come `updatedPermissions`:
è quello che fa l'opzione "sì, e non chiedere più" del riquadro. Una risposta vuota lascia
decidere l'app come se soundpad non ci fosse.

Anche le domande a scelta multipla di Claude (strumento AskUserQuestion) passano da PermissionRequest.
Per rispondere si rimanda `allow` con `updatedInput`: le `questions` ricevute più `answers`, che associa il
testo di ogni domanda all'etichetta scelta (più etichette separate da virgola se la scelta è multipla).
Mentre l'hook aspetta, l'app mostra la domanda: vince chi risponde prima.
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
    # Solo per AskUserQuestion: input originale da rimandare, domande valide e risposte date finora
    tool_input: dict = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    picked: set[int] = field(default_factory=set)  # opzioni accese nella domanda a scelta multipla

    @property
    def can_always(self) -> bool:
        return bool(self.suggestions)

    @property
    def question(self) -> dict | None:
        """La domanda a cui si sta rispondendo, None se non è una domanda o sono finite."""
        return self.questions[len(self.answers)] if len(self.answers) < len(self.questions) else None

    def pick(self, index: int) -> bool:
        """Opzione `index` della domanda corrente. Scelta singola: risponde e passa alla successiva.
        Scelta multipla: accende o spegne l'opzione. True se tutte le domande hanno una risposta."""
        q = self.question
        if q is None or not 0 <= index < len(q["options"]):
            return False
        if q.get("multiSelect"):
            self.picked ^= {index}
            return False
        return self._answer(q, q["options"][index]["label"])

    def confirm(self) -> bool:
        """Chiude la domanda a scelta multipla corrente con le opzioni accese (almeno una).
        True se tutte le domande hanno una risposta."""
        q = self.question
        if q is None or not q.get("multiSelect") or not self.picked:
            return False
        return self._answer(q, ", ".join(q["options"][i]["label"] for i in sorted(self.picked)))

    def _answer(self, q: dict, value: str) -> bool:
        self.answers[q["question"]] = value
        self.picked = set()
        nxt = self.question
        if nxt is not None:
            self.summary = summarize_question(nxt)
        return nxt is None

    def answers_decision(self) -> dict:
        updated = {**self.tool_input, "answers": dict(self.answers)}
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                       "decision": {"behavior": "allow", "updatedInput": updated}}}

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


def summarize_question(q: dict) -> str:
    text = " ".join(q["question"].split())
    return text if len(text) <= 160 else text[:157] + "..."


def valid_questions(tool_input) -> list[dict]:
    """Domande di AskUserQuestion utilizzabili dal pad: testo e almeno un'opzione con etichetta.
    Lista vuota se anche una sola non lo è: meglio lasciare tutto all'app che rispondere a metà."""
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions:
        return []
    for q in questions:
        if not (isinstance(q, dict) and isinstance(q.get("question"), str) and isinstance(q.get("options"), list)
                and q["options"] and all(isinstance(o, dict) and isinstance(o.get("label"), str) for o in q["options"])):
            return []
    return questions


def from_event(event: dict) -> Pending:
    tool = event.get("tool_name") if isinstance(event.get("tool_name"), str) else "?"
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    suggestions = event.get("permission_suggestions")
    questions = valid_questions(tool_input) if tool == "AskUserQuestion" else []
    return Pending(
        session_id=event["session_id"],
        tool_name=tool,
        summary=summarize_question(questions[0]) if questions else summarize(tool, tool_input),
        suggestions=suggestions if isinstance(suggestions, list) else [],
        tool_input=tool_input,
        questions=questions,
    )


def decision_for(choice: str, pending: Pending) -> dict:
    if choice == "deny":
        decision = {"behavior": "deny", "message": "Rifiutato dall'utente con soundpad"}
    elif choice == "always" and pending.can_always:
        decision = {"behavior": "allow", "updatedPermissions": pending.suggestions}
    else:
        decision = {"behavior": "allow"}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
