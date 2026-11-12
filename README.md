# Dusk Sticky Notes

Sticky notes for DuskOS, modeled on the Sticky Notes in Microsoft OneNote / Windows.
Python + PyGObject, GTK 4 + libadwaita.

## Run

    ./dusk-sticky-notes             # notes list + reopen the notes left open
    ./dusk-sticky-notes --new-note  # straight to a new note
    ./install.sh                    # install to ~/.local (./install.sh --uninstall to remove)
    ./install.sh --autostart        # also reopen open notes at login (runs --restore)

## Features

- One window per note, plus a notes list with search (type to search, Ctrl+F).
- Seven colors (yellow, green, pink, purple, blue, gray, charcoal), light and dark palettes.
- Bold, italic, underline, strikethrough, bullet lists, images (file picker or pasted screenshot).
- Autosave; open notes reopen on next launch; an empty note is discarded when closed.
- An inactive note hides its controls and shows only the colored sheet.

## Shortcuts

| Keys | Action |
|---|---|
| Ctrl+N | New note |
| Ctrl+B / I / U / T | Bold / italic / underline / strikethrough |
| Ctrl+Shift+L | Bullets |
| Ctrl+D | Delete note |
| Ctrl+W | Close note |
| Ctrl+Q | Quit (open notes reopen next time) |

## Data

`$XDG_DATA_HOME/dusk-sticky-notes/notes.json` plus `images/`. Writes are atomic.
An unreadable `notes.json` is renamed to `notes.json.corrupt-<time>`, never overwritten.

## Tests

    python3 -m pytest -q tests
