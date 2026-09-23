# soundpad

Launchpad originale (NOVLPD01) come spia delle sessioni di Claude Code, alimentata dagli hook.
Un pad = una sessione. Funziona su Windows e macOS.

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
- **pressione breve**: porta in primo piano l'app Claude e segna il turno come visto
- **pressione lunga** (1 s): toglie la sessione dalla griglia

I colori si cambiano in `config.toml`.

## Installazione su Windows

1. Installa **uv** da PowerShell:
   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. Copia la cartella `soundpad` sul PC.
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

## Limiti noti

- Se il demone è spento, gli hook falliscono senza bloccare Claude. L'app può mostrare un avviso di hook non riuscito.
- La pressione breve porta in primo piano l'app Claude, ma non apre la sessione specifica.
- Le sessioni cloud non leggono `~/.claude/settings.json`: non accendono pad.
- Riavviando il demone la griglia riparte vuota. Ogni sessione ricompare al suo evento successivo, anche in una posizione diversa.
