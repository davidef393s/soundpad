# soundpad: contesto per Claude

Novation Launchpad originale (NOVLPD01, 2009, solo LED rosso/verde) come spia e telecomando delle
sessioni di Claude Code, alimentato dagli hook HTTP di Claude Code. Un pad = una sessione.

- L'utente scrive in **italiano**: rispondi in italiano. Commenti, docstring, log e UI sono in italiano.
- Stato del lavoro, decisioni e cose da verificare: **HANDOFF.md** (leggilo prima di cambiare comportamento).
- Uso e installazione: README.md.

## Struttura

| File | Cosa fa |
|---|---|
| `soundpad/daemon.py` | Cuore: `Board` (sessioni, stati, disegno, effetti, permessi), server HTTP, `Service`, `main` (`uv run soundpad`) |
| `soundpad/launchpad.py` | Driver MIDI del Launchpad (mido + python-rtmidi) e `SimLaunchpad` per `--sim`; `Led.of((rosso, verde))` |
| `soundpad/effects.py` | Animazioni: `Ripple`, `Spark`, `FadeOut`, `Boot`, `Rain`, `breathe`. Colori come coppie (rosso, verde) 0..3 |
| `soundpad/permissions.py` | Richieste di permesso tenute in sospeso e risposte (accetta una volta / sempre / rifiuta) |
| `soundpad/install_hooks.py` | Registra gli hook HTTP in `~/.claude/settings.json` (`uv run soundpad-hooks`) |
| `soundpad/claudeapp.py` | Solo Windows: legge le sessioni dell'app desktop (titolo, id `local_...`) e apre la chat giusta |
| `soundpad/winfocus.py` | Solo Windows: porta in primo piano una finestra (ctypes) |
| `soundpad/app.py` | App con finestra (pywebview) e icona nell'area di notifica (pystray); `soundpad.exe` |
| `soundpad/autostart.py` | Solo Windows + exe: avvio con Windows (registro HKCU\...\Run) |
| `soundpad/paths.py` | Percorsi diversi tra `uv run` e exe PyInstaller |
| `soundpad/dashboard.html` | Pagina servita dal demone su `/`: griglia, sessioni, permessi, impostazioni |
| `build.py` | Costruisce `dist/soundpad.exe` con PyInstaller |
| `tests/test_soundpad.py` | Test, solo libreria standard |

## Comandi

```
uv run soundpad                 # demone da terminale (Launchpad vero)
uv run soundpad --sim           # senza Launchpad: griglia disegnata nel terminale
uv run soundpad-hooks           # installa/aggiorna gli hook (backup di settings.json)
uv run --extra app soundpad-app # app con finestra, senza costruire l'exe
uv run --extra app --group build python build.py   # costruisce l'exe
python -m unittest              # test (anche senza uv: non servono dipendenze)
```

## Regole del progetto

- Python 3.12 (`.python-version`): `python-rtmidi` non ha wheel Windows per 3.13.
- I test non devono toccare il Launchpad né `~/.claude/settings.json` (usa `SimLaunchpad`, `TemporaryDirectory`,
  `static_config()` per test senza animazioni, `board.clock` finto per quelli con animazioni).
- MIDI del Launchpad MK1 è lento: `Board.redraw` manda solo i LED cambiati; tenere le animazioni sotto ~600 msg/s.
- La risposta a un hook diverso da PermissionRequest deve essere **vuota** (204): qualsiasi JSON verrebbe letto
  da Claude Code come output dell'hook.
- Endpoint che cambiano il computer (`/app/*`, risposte ai permessi) accettano solo richieste da 127.0.0.1 e con
  `Content-Type: application/json` (blocca altri siti aperti nel browser).
- Per provare i permessi dal vivo servono comandi che chiedono davvero il permesso (non `echo`/`whoami`, che
  passano da soli) e "accetta sempre" va provato **per ultimo**, o approva in automatico le prove successive.
