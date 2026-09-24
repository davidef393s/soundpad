# soundpad: contesto per Claude

Novation Launchpad originale (NOVLPD01, 2009, solo LED rosso/verde) come spia e telecomando delle
sessioni di Claude Code, alimentato dagli hook HTTP di Claude Code. Un pad = una sessione.

- L'utente scrive in **italiano**: rispondi in italiano. Commenti, docstring, log e UI sono in italiano.
- Stato del lavoro, decisioni e cose da verificare: **HANDOFF.md** (leggilo prima di cambiare comportamento).
- Uso e installazione: README.md (inglese) e README.it.md (italiano): tenerli allineati.
- Repository pubblico: https://github.com/davidef393s/soundpad (licenza MIT). Commit firmati con l'identità
  impostata nel repository (`git config user.email`), non con quella globale.

## Struttura

| File | Cosa fa |
|---|---|
| `soundpad/daemon.py` | Cuore: `Board` (sessioni, stati, disegno, effetti, permessi), server HTTP, `Service`, `main` (`uv run soundpad`) |
| `soundpad/launchpad.py` | Driver del Launchpad: `MidiLaunchpad` (mido, Windows/Linux), `UsbLaunchpad` (pyusb + libusb, macOS), `SimLaunchpad` per `--sim`; `Led.of((rosso, verde))` |
| `soundpad/effects.py` | Animazioni: `Ripple`, `Spark`, `FadeOut`, `Boot`, `Rain`, `breathe`. Colori come coppie (rosso, verde) 0..3 |
| `soundpad/permissions.py` | Richieste di permesso tenute in sospeso e risposte (accetta una volta / sempre / rifiuta) |
| `soundpad/install_hooks.py` | Registra gli hook HTTP in `~/.claude/settings.json` (`uv run soundpad-hooks`) |
| `soundpad/claudeapp.py` | Windows e macOS: legge le sessioni dell'app desktop (titolo, id `local_...`) e apre la chat giusta |
| `soundpad/macax.py` | Solo macOS: preme un pulsante dell'app Claude con le API di accessibilità (ctypes) |
| `soundpad/winfocus.py` | Solo Windows: porta in primo piano una finestra (ctypes) |
| `soundpad/app.py` | App con finestra (pywebview) e icona nell'area di notifica (pystray); `soundpad.exe` |
| `soundpad/autostart.py` | Avvio all'accesso: registro HKCU\...\Run (Windows, solo exe) o LaunchAgent (macOS) |
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
- I test non devono toccare il Launchpad, `~/.claude/settings.json` né `~/Library/LaunchAgents` (usa `SimLaunchpad`, `TemporaryDirectory`,
  `static_config()` per test senza animazioni, `board.clock` finto per quelli con animazioni).
- MIDI del Launchpad MK1 è lento: `Board.redraw` manda solo i LED cambiati; tenere le animazioni sotto ~600 msg/s.
  Via USB (macOS) il tetto misurato è ~475 LED/s: un pacchetto da 8 byte ogni ~8 ms, 4 LED a pacchetto.
- `UsbLaunchpad`: mai `dev.reset()` e mai `set_configuration()` su un dispositivo già configurato. Bloccano
  gli endpoint o fanno sparire il Launchpad dal bus finché non si stacca il cavo.
- La risposta a un hook diverso da PermissionRequest deve essere **vuota** (204): qualsiasi JSON verrebbe letto
  da Claude Code come output dell'hook.
- Endpoint che cambiano il computer (`/app/*`, risposte ai permessi) accettano solo richieste da 127.0.0.1 e con
  `Content-Type: application/json` (blocca altri siti aperti nel browser).
- Per provare i permessi dal vivo servono comandi che chiedono davvero il permesso (non `echo`/`whoami`, che
  passano da soli) e "accetta sempre" va provato **per ultimo**, o approva in automatico le prove successive.
