# soundpad: stato del lavoro

Aggiornato il 24 settembre 2026, sessione sul Mac (Apple Silicon, macOS 26) con il Launchpad collegato via USB.
Il lavoro precedente (23 settembre) è stato fatto sul PC Windows.

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
- 52 test verdi (`python -m unittest`).

## Mac: Launchpad via USB diretto (24 settembre)

- Il Launchpad MK1 su macOS **non ha porte CoreMIDI**: classe USB `0xff` vendor-specific, nessun driver Novation
  per Apple Silicon. Il sistema vede il dispositivo USB (`1235:000e`), mido no.
- `UsbLaunchpad` (in `launchpad.py`) gli parla con pyusb + libusb (`brew install libusb`). `make_launchpad()` lo
  sceglie su macOS, `MidiLaunchpad` resta per Windows e Linux.
- Protocollo verificato dal vivo: un'interfaccia con endpoint interrupt `0x02` (uscita) e `0x81` (ingresso),
  8 byte a pacchetto, `bInterval` 10. Nei due sensi passano byte MIDI grezzi con **running status**, e un
  messaggio può stare a cavallo di due pacchetti.
- Misurato: 80 LED in 0,168 s (~475 LED/s). Pressioni, rilasci e pressioni sovrapposte arrivano tutti.
- Scoperto a caro prezzo: un secondo `set_configuration()` blocca gli endpoint (`Errno 5`), e `dev.reset()` fa
  sparire il Launchpad dal bus fino a quando si stacca il cavo. Il driver non fa né l'uno né l'altro.
- Il primo pacchetto dopo il collegamento contiene rilasci finti (`90 00 00 b0 68 00 69 00`): innocui, perché
  `handle_press` ignora i rilasci senza pressione.
- Chrome può tenere aperto il dispositivo (una pagina WebUSB): se `claim_interface` fallisce, controllare lì.
- Un processo ucciso a metà trasferimento lascia il Launchpad bloccato allo stesso modo. Per questo il demone
  gestisce SIGTERM (launchd) passando da `service.stop()` → `UsbLaunchpad.close()`: verificato con
  `launchctl kickstart -k`, il Launchpad resta sano.

## Mac: apertura della chat e avvio automatico (24 settembre)

- **Apri la chat** su macOS: `macax.py` usa le API di accessibilità native via ctypes. Nella barra laterale la
  chat è un `AXButton` chiamato `"<stato> <titolo>"`, con lo stato nella lingua dell'app ("Inattivo",
  "In esecuzione"). Serve `AXManualAccessibility = true` sull'app perché Chromium costruisca l'albero.
- Verificato con il pad vero: da un'altra chat la pressione breve riporta alla chat del pad, e viceversa.
- Misurato: 15-24 ms per trovare e premere il pulsante, contro ~3,4 s con JXA/osascript (un Apple Event per
  ogni proprietà letta).
- Il permesso Accessibilità va al binario Python reale (`.venv/bin/python` risolto), perché sotto launchd il
  "processo responsabile" è lui. Per esplorare l'albero senza dare il permesso all'app Claude: un lavoro
  launchd una tantum che lancia lo stesso Python.
- **Avvio automatico**: LaunchAgent `io.github.davidef393s.soundpad`, senza KeepAlive (con la porta occupata
  il demone esce e launchd lo rilancerebbe in loop). Log in `~/Library/Logs/soundpad/`.
- `focus_command` non ha più un default per macOS: se è impostato nel config vince e soundpad non apre la chat.

## Scoperte da ricordare

- Il link `claude://code/continue?session=local_...` esiste nell'app ma è dietro un'impostazione lato server
 , spenta per questo account: l'app lo ignora in silenzio. Non aggirarlo modificando l'app.
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

## Domande di Claude dal pad (24 settembre)

- `AskUserQuestion` passa da **PermissionRequest** (dopo un PreToolUse). Mentre l'hook aspetta, l'app mostra
  la domanda: pad e app rispondono in parallelo e vince il primo. Questo risponde anche al vecchio dubbio sul
  riquadro dei permessi: almeno per le domande compare subito.
- Riga in basso = opzioni (max 4 per lo schema dello strumento), scelta multipla confermata dal tondo 5.
  Risposta: `allow` + `updatedInput` = input originale + `answers` (`{testo domanda: etichetta}`, più etichette
  separate da ", ").
- **Verificato fino a Claude Code** rispondendo dalla pagina (`/action` con `"option"`) e, con `soundpad.app` e il
  cavo appena ricollegato, **dal pad fisico** (24 settembre, 17:40).

## Guasto aperto: su macOS i tasti del Launchpad smettono di arrivare

- I LED funzionano sempre, le pressioni a un certo punto non arrivano più: nessun errore, solo timeout
  sull'endpoint `0x81`. Nel log con `SOUNDPAD_LOG_EVENTS=1` non compaiono righe `[tasto]`.
- Visto funzionare: prima apertura dopo aver ricollegato il cavo (anche sotto launchd, pressione breve che apre
  la chat verificata). Visto guasto: dopo riavvii del demone (`launchctl kickstart -k`).
- Provati senza effetto, con il dispositivo nello stato guasto: `clear_halt` su entrambi gli endpoint,
  `SET_INTERFACE` alt 0, `set_configuration(0)` + `set_configuration(1)`. `dev.reset()` è escluso (lo fa sparire).
- **Non affidabile**: l'ultima prova a fasi (prima apertura / pausa / riapertura) ha dato 0 eventi in tutte e tre,
  anche nella prima apertura dopo il ricollegamento. Non è certo che durante la prova si premessero i tasti.
- Chrome risulta collegato al dispositivo USB (user client IOKit, probabilmente WebUSB), ma la prima apertura
  ha funzionato anche con Chrome collegato.
- Diagnostica ora sempre visibile: la pagina mostra "· tasto HH:MM:SS" accanto a "Launchpad collegato" (campo
  `last_input` di `/state`). Se dice "nessun tasto" dopo aver premuto, l'ingresso è guasto.
- Regola pratica finché non si trova la causa: non riavviare il demone/l'app; se l'ingresso muore, ricollegare il
  cavo (il demone lo riprende da solo).
- Ipotesi ancora da verificare: l'ingresso muore quando il dispositivo viene riaperto da un altro processo.

## Stato per piattaforma

| Funzione | Windows | macOS |
|---|---|---|
| Launchpad | `MidiLaunchpad` (driver Novation) | `UsbLaunchpad` (libusb) |
| Titoli delle chat (`SessionIndex`) | fatto | fatto |
| Aprire la chat giusta | UI Automation (PowerShell) | `macax.py` (API di accessibilità) |
| Avvio automatico | registro, solo exe | LaunchAgent |
| App con finestra e icona | `soundpad.exe` | `soundpad.app` (`build.py`): barra dei menu, niente Dock (`LSUIElement`), libusb inclusa, firma ad hoc. Config in `~/Library/Application Support/soundpad/`. Ogni ricostruzione cambia la firma: il permesso Accessibilità va ridato |

Se il Launchpad resta su un computer e Claude gira su un altro: vedi README, "Claude on one computer,
Launchpad on another". Con il demone in rete, gli endpoint che approvano permessi rispondono solo da
127.0.0.1: dall'altro computer si approva dal pad o dall'app, non dalla pagina.
