# soundpad

**English** · [Italiano](README.it.md)

Turns an original 2009 Novation Launchpad (NOVLPD01, red/green LEDs only) into a status light and remote
control for your Claude Code sessions, driven by Claude Code's HTTP hooks. One pad = one session.

- See at a glance which sessions are working, finished, or waiting for you
- Approve or deny tool permissions from the pad
- Press a pad to jump to that chat in the Claude desktop app

Runs on Windows and macOS. On macOS the Launchpad is driven directly over USB, no driver needed. There is
also a windowed app with a tray / menu bar icon: `soundpad.exe` on Windows, `soundpad.app` on macOS.

> Code comments, logs and the web page are in Italian. Only the original Launchpad is supported: later
> models (S, Mini, MK2, X, Pro) speak a different protocol.

## Legend

| Pad | Meaning |
|---|---|
| dim green | session open, or turn finished and already seen |
| dim amber | Claude is working |
| **flashing red** | waiting for you to approve a tool |
| **bright green** | turn finished |
| flashing green | finished more than a minute ago and you haven't replied |
| flashing yellow | compacting the context |
| steady red | turn stopped by an API error |

Service keys:
- **top-left round key**, dim green: the daemon is listening
- **top-right round key**, flashing red: at least one session is waiting for a permission
- **bottom-right scene key**: removes every stopped session from the grid

Grid pads:
- **short press**: opens that session's chat in the Claude app and marks the turn as seen
- **long press** (1 s): removes the session from the grid

Top round keys 5, 6 and 7, lit only when a session asks for a permission:
- **green**: allow once
- **amber**: always allow (saves the rule Claude Code suggests; off when it suggests none)
- **dim red**: deny

They answer the request of the pad you pressed last, otherwise the oldest one. You can still answer in the
app as usual: the daemon notices and lets go. If you don't answer within `permission_wait_seconds` (90 s),
the app decides as if soundpad weren't there.

When Claude asks a multiple-choice question (`AskUserQuestion`), the **bottom row** shows its options from
the left, in amber. Press one to answer; with several questions the row moves to the next one, and pads 5-8
show where you are (green = answered, flashing green = current, dim green = to do). The app's own dialog
stays on the first question until the pad has answered them all: follow the pad, or the soundpad window. For
multiple-choice questions each press toggles an option (green = picked) and round key 5 confirms. Round
key 7 declines. "Other" with free text stays in the app, and answering in the app works as usual.

Right-hand column of round keys (rows 1-7, from the bottom): one key per session. Red = waiting for you
(permission or error), green = finished, dim amber = working.

## Animations

- **boot**: a red, amber, green band sweeps across the Launchpad when it connects
- **new session**: the pad flashes and sends out a small amber ripple
- **working**: the pad "breathes" instead of staying steady
- **turn finished**: green ripple towards neighbouring pads
- **permission**: red waves across the whole grid, then the pad flashes
- **session removed**: the pad fades out
- **key pressed**: lights up immediately
- **grid empty for 30 s**: screensaver with dim green rain

Turn them off with `animations = false` in `config.toml`. Colours are set in `config.toml` too.

## Install on macOS

The original Launchpad has no macOS driver: Novation no longer updates it and the device is not a standard
USB MIDI device, so it never shows up in Audio MIDI Setup. soundpad talks to it directly over USB with
libusb.

1. Install **uv** and **libusb**, then get the code:
   ```
   brew install uv libusb
   git clone https://github.com/davidef393s/soundpad
   ```
2. Plug in the Launchpad and close any browser tab using it through WebUSB/WebMIDI: only one program at a
   time can open the device.
3. From the `soundpad` folder:
   ```
   uv run soundpad
   ```
   The top-left round key turns green and the terminal prints `[launchpad] collegato via USB`.
4. Register the hooks (backs up `~/.claude/settings.json` first):
   ```
   uv run soundpad-hooks
   ```
5. To open the right chat on a short press: System Settings → Privacy & Security → **Accessibility** →
   **+**, press ⌘⇧G and paste the path of the project's Python, which you get with
   `uv run python -c "import os,sys; print(os.path.realpath(sys.executable))"`. Without this permission a
   press only brings the app to the front. If uv moves to a different Python version, grant it again.
6. Start at login (a LaunchAgent in `~/Library/LaunchAgents`), from the web page (Settings → "Avvia
   all'accesso") or with:
   ```
   uv run python -c "from soundpad import autostart; autostart.set_enabled(True)"
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.github.davidef393s.soundpad.plist
   ```
   The second command starts it now instead of at the next login. Logs go to `~/Library/Logs/soundpad/`.
   Restart it after changing the code with `launchctl kickstart -k gui/$(id -u)/io.github.davidef393s.soundpad`.

If the Launchpad stops responding (LEDs frozen, USB write errors in the log), unplug it and plug it back in:
the daemon reconnects within 2 seconds.

## Install on Windows

1. Install **uv** from PowerShell:
   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
2. Get the code: `git clone https://github.com/davidef393s/soundpad` (or download the zip from GitHub).
3. Plug in the Launchpad. If Device Manager doesn't list it as "Launchpad", install the
   **Novation USB Driver** from downloads.novationmusic.com (Launchpad MK1 section).
4. Close Ableton Live and Novation Components: on Windows only one program at a time can open a MIDI port.
5. From the `soundpad` folder:
   ```
   uv run soundpad
   ```
   The top-left round key turns green.
6. Register the hooks (backs up `settings.json` first):
   ```
   uv run soundpad-hooks
   ```
7. Open a session in the Code tab of the desktop app: the first pad lights up.

To remove the hooks: `uv run soundpad-hooks --uninstall`.

The hooks use the port set in `config.toml`. If you change it, run `uv run soundpad-hooks` again (removing
the old ones first with `--uninstall --url http://127.0.0.1:<OLD-PORT>/event`).

## macOS app (soundpad.app)

`uv run --extra app --group build python build.py` builds `dist/soundpad.app`: a menu bar icon (no Dock icon)
and a window with the grid. libusb is bundled, so Homebrew isn't needed to run it.

- closing the window hides it; **Esci** (quit) in the menu bar icon stops everything
- optional config in `~/Library/Application Support/soundpad/config.toml`
- grant **Accessibility** to `soundpad.app` to open the right chat. The build is ad-hoc signed, so every
  rebuild needs the permission again
- turn on start at login from the window's Settings: the LaunchAgent then points to the app
- don't run it together with `uv run soundpad` or the `uv`-based LaunchAgent: they use the same port

If the pads stop reacting (the status pill in the window keeps saying "nessun tasto" after a press), unplug
and replug the Launchpad: see HANDOFF.md for the open USB input issue.

## Windows app (soundpad.exe)

`dist\soundpad.exe` does everything without a terminal: it starts the daemon, opens a window with the grid
and puts an icon next to the clock.

- **closing the window hides it**: the Launchpad keeps working. Double-click the icon or launch
  `soundpad.exe` again to bring it back
- **Esci** (quit) in the icon's right-click menu stops everything
- at the bottom of the window, **Settings**: install or remove the hooks, and start with Windows (hidden,
  icon only)
- `config.toml` goes next to `soundpad.exe` (the build copies it there)
- don't run it together with `uv run soundpad`: they use the same port. The app tells you

To rebuild it after changing the code:
```
uv run --extra app --group build python build.py
```
Without building the exe: `uv run --extra app soundpad-app`. The app log is in
`%LOCALAPPDATA%\soundpad\soundpad.log`.

## Web page

With the daemon running, open **http://127.0.0.1:47800/**. It shows the grid as it is on the Launchpad and,
for each pad, the project, folder, state and time since the last event. It refreshes every second and works
without a Launchpad too.

- click a pad: highlights its session in the list
- **Apri** (open): same as a short press
- **Accetta / Sempre / Rifiuta** (allow / always / deny): appear when the session asks for a permission,
  with the requested command or file
- **Rimuovi** (remove): same as a long press
- **Togli le ferme** (clear stopped): same as the bottom-right scene key

The tab title shows how many sessions are waiting for a permission, e.g. `(2) soundpad`.

## Claude on one computer, Launchpad on another

1. On the computer with the Launchpad, in `config.toml`: `host = "0.0.0.0"`. On Windows, allow access in
   the firewall prompt for private networks only.
2. On the computer running Claude: `uv run soundpad-hooks --url http://<LAUNCHPAD-COMPUTER-IP>:47800/event`.

If the router changes that IP, the hooks stop arriving: reserve a fixed IP for it in the router.

Warning: the daemon has no password. With `host = "0.0.0.0"` anyone on the same network can light pads or
bring the Claude app to the front. Only use it on networks you trust. Endpoints that approve permissions
only answer requests from 127.0.0.1.

## Try it without a Launchpad

```
uv run soundpad --sim
```
draws the grid in the terminal. To simulate events and presses:
```
curl -X POST localhost:47800/event -d '{"session_id":"A","hook_event_name":"PermissionRequest"}'
curl -X POST localhost:47800/press -d '{"row":0,"col":0}'
curl localhost:47800/state
```

## Tests

Standard library only, no Launchpad needed:
```
uv run python -m unittest -v
```

## Python version

The project pins Python 3.12 (`.python-version`): `python-rtmidi` has no prebuilt wheels for Windows on
Python 3.13, and without a C++ compiler the install fails. uv downloads Python 3.12 if it's missing.

## How it works

- Claude Code sends every hook event (`SessionStart`, `UserPromptSubmit`, `PermissionRequest`, `Stop`, …)
  as an HTTP POST to the daemon on `127.0.0.1:47800`. The daemon answers non-permission events with an empty
  `204`, so Claude Code ignores the reply.
- A `PermissionRequest` is held open until you press a key (or the timeout): the answer goes back in the
  same HTTP response, in the format described in the
  [hooks documentation](https://code.claude.com/docs/en/hooks).
- To find the chat of a hook's `session_id`, the daemon reads the desktop app's session files
  (`claude-code-sessions/<account>/<org>/local_*.json`, field `cliSessionId`) and then presses that chat's
  button in the app's sidebar through the OS accessibility APIs.
- On macOS the Launchpad's USB interface has two 8-byte interrupt endpoints carrying raw MIDI bytes with
  running status. At low speed that's one packet every ~8 ms, about 475 LED updates per second.

## Known limits

- If the daemon is off, the hooks fail without blocking Claude. The app may show a failed-hook notice.
- Opening the right chat "clicks" it in the app's sidebar: with two chats sharing a title it opens the
  first, it can't find a chat whose folder group is collapsed, and an app update can break it. Sessions
  started from a terminal only bring the app to the front.
- Cloud sessions don't read `~/.claude/settings.json`, so they don't light pads.
- Restarting the daemon empties the grid. Each session comes back on its next event, possibly on a
  different pad.

## License

[MIT](LICENSE)
