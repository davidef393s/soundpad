# soundpad: stato del lavoro

Aggiornato il 23 settembre 2026, a fine sessione su Windows (PC "DavidePC", Launchpad collegato lì).

## Cosa funziona (provato sul PC Windows con il Launchpad vero)

- **Spia delle sessioni**: un pad per sessione, colori per stato (vedi README), hook HTTP installati in
  `~/.claude/settings.json` sulla porta 47800.
- **App `dist\soundpad.exe`** (PyInstaller, un file): finestra con la pagina della griglia (pywebview/Edge
  WebView2), icona vicino all'orologio, chiudere la finestra la nasconde, una seconda copia mostra la prima.
  `config.toml` va accanto all'exe. Log in `%LOCALAPPDATA%\soundpad\soundpad.log`.
- **Apri la chat giusta** (pressione breve o "Apri"): il demone legge
  `%APPDATA%\Claude\claude-code-sessions\<account>\<org>\local_<id>.json` (campi `sessionId`, `cliSessionId`,
  `title`), poi:
  1. prova `claude://code/continue?session=local_...` (link ufficiale dell'app);
  2. porta in primo piano l'app e "clicca" la chat nella barra laterale con UI Automation (script PowerShell
     in `claudeapp.py`: pulsante chiamato `"<stato> <titolo>"`, es. `"Idle Visual synth whiteboard webapp"`).
  Verificato: cambia chat in meno di 1 s.
- **Permessi dal pad** (tondi in alto 5 = accetta una volta, 6 = accetta sempre, 7 = rifiuta): verificato dal vivo
  su una sessione in modalità manuale. L'app desktop rispetta le decisioni dell'hook. "Sempre" salva la regola
  proposta da Claude Code (`permission_suggestions` rimandate come `updatedPermissions`).
- **Effetti**: onde (5 onde rosse per un permesso, si fermano alla risposta), respiro per "al lavoro", avvio,
  screensaver, colonna di stato sui tondi a destra, tasto premuto illuminato.
- 48 test verdi (`python -m unittest`).

## Scoperte da ricordare

- Il link `claude://code/continue?session=local_...` esiste nell'app ma è dietro un'impostazione lato server
  (gate `4217215889`), spenta per questo account: l'app lo ignora in silenzio. Non aggirarlo modificando l'app.
  Se Anthropic lo attiva, soundpad lo usa già.
- Le sessioni dell'app desktop hanno id `local_...` diverso dal `session_id` degli hook: la corrispondenza sta
  nel campo `cliSessionId` dei file sopra.
- UI Automation su Chromium: l'albero di accessibilità nasce alla prima richiesta, serve qualche tentativo.
- Formato risposta PermissionRequest (dalla doc degli hook, `code.claude.com/docs/en/hooks`):
  `{"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}`.
  Timeout dell'hook PermissionRequest portato a 120 s (`install_hooks.TIMEOUTS`), il demone aspetta al massimo
  `permission_wait_seconds` (90, tetto 110).
- `claude.exe` (app desktop) è un pacchetto MSIX; `AppActivate` fa solo lampeggiare l'icona, per questo esiste
  `winfocus.py` (AttachThreadInput + trucco del tasto Alt).

## Da verificare

1. **Riquadro del permesso nell'app mentre soundpad aspetta**: non ancora confermato se compare subito o solo
   quando l'hook risponde. Se compare solo dopo, chi non usa il pad aspetta fino a 90 s: in quel caso ridurre
   `permission_wait_seconds` o cambiare approccio.
2. Resa delle animazioni sul Launchpad vero (fluidità dei ~570 msg/s di picco).
3. "Esci" dall'icona e "Avvia con Windows" non provati.

## Continuare sul Mac

Il demone, gli hook, la pagina, gli effetti e i permessi sono multipiattaforma. Solo Windows:

| Funzione | Su macOS oggi | Idea |
|---|---|---|
| Aprire la chat giusta (`claudeapp.open_chat`, UI Automation) | porta in primo piano l'app con `open -a Claude` | Accessibility API (AppleScript/`osascript` o pyobjc) sul pulsante della barra laterale; oppure il link `claude://` se il gate si accende |
| Titoli delle chat (`SessionIndex`) | attivo, percorso `~/Library/Application Support/Claude/claude-code-sessions` **non verificato** | controllare che esista e abbia gli stessi campi |
| `winfocus.py` | non usato | non serve: `open -a Claude` basta |
| Avvio automatico (`autostart.py`) | non disponibile | LaunchAgent in `~/Library/LaunchAgents` |
| Build (`build.py`) | non provata | PyInstaller su macOS fa un `.app` con `--windowed`; l'icona `.ico` va convertita in `.icns` |

Se il Launchpad resta sul PC e Claude gira sul Mac: sul PC `host = "0.0.0.0"`, sul Mac
`uv run soundpad-hooks --url http://<IP-DEL-PC>:47800/event` (vedi README). Con il demone in rete, gli endpoint
che approvano permessi rispondono solo da 127.0.0.1: dal Mac si approva dal pad o dall'app, non dalla pagina.

Primi passi sul Mac:
```
curl -LsSf https://astral.sh/uv/install.sh | sh
cd soundpad
uv run python -m unittest
uv run soundpad --sim
```
