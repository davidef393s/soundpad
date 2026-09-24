# soundpad

[English](README.md) · **Italiano**

Launchpad originale (NOVLPD01) come spia delle sessioni di Claude Code, alimentata dagli hook.
Un pad = una sessione. Funziona su Windows e macOS (su macOS il Launchpad va via USB diretto, senza
driver). C'è anche un'app con finestra e icona: `soundpad.exe` su Windows, `soundpad.app` su macOS. Stato del lavoro in [HANDOFF.md](HANDOFF.md).

## Legenda

| Pad | Significato |
|---|---|
| verde tenue | sessione aperta, oppure turno finito e già visto |
| ambra tenue | Claude sta lavorando |
| **rosso lampeggiante** | aspetta che approvi uno strumento |
| **verde pieno** | ha finito il turno |
| verde lampeggiante | ha finito da più di un minuto e non hai risposto |
| giallo lampeggiante | compattazione del contesto |
| rosso fisso | turno interrotto da un errore API |

Tasti di servizio:
- **tondo in alto a sinistra**, verde tenue: il demone è in ascolto
- **tondo in alto a destra**, rosso lampeggiante: almeno una sessione aspetta un permesso
- **scene in basso a destra**: toglie dalla griglia tutte le sessioni ferme

Sui pad della griglia:
- **pressione breve**: apre nell'app Claude la chat di quella sessione e segna il turno come visto
- **pressione lunga** (1 s): toglie la sessione dalla griglia

Tondi in alto 5, 6 e 7, accesi solo quando una sessione chiede un permesso:
- **verde**: accetta una volta
- **ambra**: accetta sempre (salva la regola che Claude Code propone; spento se non ne propone)
- **rosso tenue**: rifiuta

Rispondono alla richiesta del pad che hai premuto per ultimo, altrimenti alla più vecchia. Puoi anche
rispondere nell'app come sempre: il demone se ne accorge e lascia perdere. Se non rispondi entro
`permission_wait_seconds` (90 s), decide l'app come se soundpad non ci fosse.

Quando Claude fa una domanda a scelta multipla (`AskUserQuestion`), la **riga in basso** mostra le opzioni da
sinistra, in ambra. Premi quella che vuoi; se le domande sono più d'una, la riga passa alla successiva e i pad
5-8 mostrano a che punto sei (verde = risposta, verde lampeggiante = corrente, verde tenue = da fare). Il
riquadro dell'app resta sulla prima domanda finché il pad non ha risposto a tutte: segui il pad o la finestra
di soundpad. Nelle
domande a scelta multipla ogni pressione accende o spegne un'opzione (verde = scelta) e il tondo 5 conferma.
Il tondo 7 rifiuta. "Altro" con testo libero resta nell'app, e rispondere nell'app funziona come sempre.

Colonna dei tondi a destra (righe 1-7, dal basso): un tondo per sessione. Rosso = ti aspetta
(permesso o errore), verde = ha finito, ambra tenue = sta lavorando.

## Animazioni

- **avvio**: una fascia rosso, ambra, verde attraversa il Launchpad quando si collega
- **sessione nuova**: il pad lampeggia e manda una piccola onda ambra
- **al lavoro**: il pad "respira" invece di stare fisso
- **turno finito**: onda verde verso i pad vicini
- **permesso**: onda rossa su tutta la griglia, poi il pad lampeggia
- **sessione tolta**: il pad si spegne sfumando
- **tasto premuto**: si illumina subito
- **griglia vuota da 30 s**: screensaver con pioggia verde tenue

Si spengono con `animations = false` in `config.toml`.

I colori si cambiano in `config.toml`.

## App per macOS (soundpad.app)

`uv run --extra app --group build python build.py` costruisce `dist/soundpad.app`: icona nella barra dei menu
(niente Dock) e finestra con la griglia. libusb è inclusa, per usarla non serve Homebrew.

- chiudere la finestra la nasconde; **Esci** dall'icona nella barra dei menu ferma tutto
- config facoltativo in `~/Library/Application Support/soundpad/config.toml`
- per aprire la chat giusta concedi **Accessibilità** a `soundpad.app`. La firma è ad hoc: dopo ogni
  ricostruzione il permesso va ridato
- l'avvio all'accesso si accende dalle Impostazioni della finestra: il LaunchAgent punta all'app
- non tenerla accesa insieme a `uv run soundpad` o al LaunchAgent basato su `uv`: usano la stessa porta

Se i pad smettono di rispondere (nella finestra resta "nessun tasto" dopo una pressione), stacca e riattacca il
Launchpad: il guasto aperto è descritto in HANDOFF.md.

## App per Windows (soundpad.exe)

`dist\soundpad.exe` fa tutto da solo, senza terminale: avvia il demone, apre una finestra con la griglia e
mette un'icona vicino all'orologio.

- **chiudere la finestra la nasconde**: il Launchpad continua a funzionare. Per riaprirla, doppio clic
  sull'icona oppure rilancia `soundpad.exe`
- **Esci** dal menu dell'icona (tasto destro) ferma tutto
- in fondo alla finestra, **Impostazioni**: installa o togli gli hook, e "Avvia con Windows"
  (parte nascosto, con la sola icona)
- `config.toml` va messo accanto a `soundpad.exe` (la build ce lo copia)
- non tenerlo acceso insieme a `uv run soundpad`: usano la stessa porta. L'app te lo segnala

Per ricostruirlo dopo una modifica al codice:
```
uv run --extra app --group build python build.py
```
Senza costruire l'exe: `uv run --extra app soundpad-app`. Il log dell'app è in `%LOCALAPPDATA%\soundpad\soundpad.log`.

## Pagina web

Con il demone acceso, apri **http://127.0.0.1:47800/** nel browser. La pagina mostra la griglia com'è sul
Launchpad e, per ogni pad, il progetto, la cartella, lo stato e da quanto tempo non succede niente. Si aggiorna
da sola ogni secondo e funziona anche senza Launchpad collegato.

- clic su un pad: evidenzia la sua sessione nell'elenco
- **Apri**: come la pressione breve (apre la chat nell'app Claude e segna il turno come visto)
- **Accetta / Sempre / Rifiuta**: compaiono quando la sessione chiede un permesso, con il comando o il file richiesto
- **Rimuovi**: come la pressione lunga
- **Togli le ferme**: come il tasto scene in basso a destra

Il titolo della scheda mostra quante sessioni aspettano un permesso, per esempio `(2) soundpad`.

## Installazione su Windows

1. Installa **uv** da PowerShell:
   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. Scarica il progetto: `git clone https://github.com/davidef393s/soundpad` (o lo zip da GitHub).
3. Collega il Launchpad. Se in Gestione dispositivi non compare come "Launchpad", installa il
   **Novation USB Driver** da downloads.novationmusic.com (sezione Launchpad MK1).
4. Chiudi Ableton Live e Novation Components: su Windows una porta MIDI la apre un solo programma alla volta.
5. Dalla cartella `soundpad`:
   ```
   uv run soundpad
   ```
   Il tasto tondo in alto a sinistra si accende di verde.
6. Registra gli hook (fa una copia di backup di `settings.json`):
   ```
   uv run soundpad-hooks
   ```
7. Apri una sessione nella scheda Code dell'app desktop: si accende il primo pad.

Per togliere gli hook: `uv run soundpad-hooks --uninstall`.

Gli hook usano la porta scritta in `config.toml`. Se la cambi, rilancia `uv run soundpad-hooks`
(e togli prima quelli vecchi con `--uninstall --url http://127.0.0.1:<VECCHIA-PORTA>/event`).

## Installazione su macOS

Su macOS il Launchpad originale non ha driver: Novation non lo aggiorna più e il dispositivo non è un MIDI
standard, quindi non compare in "Configurazione MIDI Audio". soundpad gli parla direttamente via USB con
libusb, senza installare driver.

1. Installa **uv** e **libusb** e scarica il progetto:
   ```
   brew install uv libusb
   git clone https://github.com/davidef393s/soundpad
   ```
2. Collega il Launchpad e chiudi le pagine del browser che usano WebUSB/WebMIDI con il Launchpad: il
   dispositivo lo apre un solo programma alla volta.
3. Dalla cartella `soundpad`:
   ```
   uv run soundpad
   ```
   Il tasto tondo in alto a sinistra si accende di verde e nel terminale compare
   `[launchpad] collegato via USB`.
4. Registra gli hook: `uv run soundpad-hooks`.
5. Per aprire la chat giusta con la pressione breve: Impostazioni di Sistema → Privacy e sicurezza →
   **Accessibilità** → **+**, premi ⌘⇧G e incolla il percorso del Python del progetto, che si ricava con
   `uv run python -c "import os,sys; print(os.path.realpath(sys.executable))"`. Senza questo permesso la
   pressione porta in primo piano l'app e basta. Se uv passa a un'altra versione di Python, il permesso va dato
   di nuovo.
6. Avvio automatico all'accesso (LaunchAgent in `~/Library/LaunchAgents`), dalla pagina web
   (Impostazioni → "Avvia all'accesso") oppure:
   ```
   uv run python -c "from soundpad import autostart; autostart.set_enabled(True)"
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.davidef393s.soundpad.plist
   ```
   Il secondo comando lo avvia subito invece che al prossimo accesso. Log in `~/Library/Logs/soundpad/`.
   Per riavviarlo dopo una modifica al codice: `launchctl kickstart -k gui/$(id -u)/io.github.davidef393s.soundpad`.

Se il Launchpad smette di rispondere (LED fermi, errori di scrittura USB nel terminale), stacca e riattacca
il cavo: il demone lo ricollega da solo entro 2 secondi.

## Claude su un computer, Launchpad su un altro

Serve se usi Claude sul Mac e il Launchpad è collegato al PC Windows.

1. Sul PC, in `config.toml`: `host = "0.0.0.0"`. Al primo avvio consenti l'accesso nel firewall di Windows,
   solo per le reti private.
2. Sul Mac: `uv run soundpad-hooks --url http://<IP-DEL-PC>:47800/event`.

Se il router cambia l'IP del PC, gli hook smettono di arrivare. Conviene riservargli un IP fisso dal router.

Attenzione: il demone non ha password. Con `host = "0.0.0.0"` chiunque sulla stessa rete può accendere pad
o portare in primo piano l'app Claude. Usalo solo su reti di cui ti fidi.

## Prova senza Launchpad

```
uv run soundpad --sim
```
disegna la griglia nel terminale. Per simulare eventi e pressioni:
```
curl -X POST localhost:47800/event -d '{"session_id":"A","hook_event_name":"PermissionRequest"}'
curl -X POST localhost:47800/press -d '{"row":0,"col":0}'
curl localhost:47800/state
```
In PowerShell `curl` è un'altra cosa e le virgolette funzionano diversamente. Usa:
```
Invoke-RestMethod -Method Post http://127.0.0.1:47800/event -Body '{"session_id":"A","hook_event_name":"PermissionRequest"}'
Invoke-RestMethod http://127.0.0.1:47800/state
```

## Test

Solo libreria standard, non serve il Launchpad:
```
uv run python -m unittest -v
```

## Versione di Python

Il progetto usa Python 3.12 (file `.python-version`): `python-rtmidi` non ha pacchetti già compilati
per Windows con Python 3.13, e senza un compilatore C++ l'installazione fallisce. uv scarica da solo
Python 3.12 se non è installato.

## Limiti noti

- Se il demone è spento, gli hook falliscono senza bloccare Claude. L'app può mostrare un avviso di hook non riuscito.
- Per aprire la chat giusta soundpad "clicca" la chat nella barra laterale dell'app (UI Automation su
  Windows, API di accessibilità su macOS): se due chat hanno lo stesso titolo apre la prima, se il gruppo
  della cartella è chiuso nella barra laterale non la trova, e un aggiornamento dell'app può romperlo.
  Le sessioni avviate da un terminale portano in primo piano solo l'app.
- Le sessioni cloud non leggono `~/.claude/settings.json`: non accendono pad.
- Riavviando il demone la griglia riparte vuota. Ogni sessione ricompare al suo evento successivo, anche in una posizione diversa.
