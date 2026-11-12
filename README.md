# Dusk Sticky Notes

Sticky notes for DuskOS, modeled on the Sticky Notes in Microsoft OneNote / Windows.
Python + PyGObject, GTK 4 + libadwaita.

## Run

    ./dusk-sticky-notes             # notes list + reopen the notes left open
    ./dusk-sticky-notes --new-note  # straight to a new note
    ./install.sh                    # install to ~/.local (./install.sh --uninstall to remove)
    ./install.sh --autostart        # also reopen open notes at login (runs --restore)
    ./install.sh --no-shortcut      # skip the global Ctrl+Alt+N shortcut on a fresh install

## Features

- One window per note, plus a notes list with search (type to search, Ctrl+F).
- Seven colors (yellow, green, pink, purple, blue, gray, charcoal), light and dark palettes.
- Bold, italic, underline, strikethrough, images (file picker or pasted).
- Lists from the format bar: bullets, numbered (renumbers itself), checklist (click a box to tick).
- Screenshots: the camera button opens GNOME's screenshot UI (area, window or screen) through
  the xdg-desktop-portal and adds the shot to the note; the tray's "New Note from Screenshot"
  does the same in a new note. GNOME keeps its own copy in ~/Pictures/Screenshots.
- Autosave; open notes reopen on next launch; an empty note is discarded when closed.
- An inactive note hides its controls and shows only the colored sheet.
- Tray icon (monochrome, follows the panel color): New Note, New Note from Screenshot,
  Show Notes List, Preferences, Quit.
  Left click toggles the notes list, middle click makes a new note.
- Preferences (Ctrl+,): keep running in the tray when closed (on by default; ignored when the
  session has no tray, so the app never runs invisibly), start at login, and a global
  Ctrl+Alt+N new-note shortcut (a GNOME custom shortcut running `dusk-sticky-notes --new-note`).

## Shortcuts

| Keys | Action |
|---|---|
| Ctrl+N | New note (in the app) |
| Ctrl+Alt+N | New note from anywhere (if enabled) |
| Ctrl+, | Preferences |
| Ctrl+B / I / U / T | Bold / italic / underline / strikethrough |
| Ctrl+Shift+L | Bullets |
| Ctrl+D | Delete note |
| Ctrl+W | Close note |
| Ctrl+Q | Quit (open notes reopen next time) |

## Data

`$XDG_DATA_HOME/dusk-sticky-notes/notes.json` plus `images/`. Writes are atomic.
Invalid version-1 data is preserved as `notes.json.corrupt-<time>`; its images
are left untouched. Newer formats open a warning and exit without modifying notes.

Pending or failed saves show **Not saved** in the notes-list subtitle. A failed
Quit offers Retry, Cancel, or Quit Anyway. On shutdown, failed normal saves are
written to `notes.json.unsaved-<time>` when possible. The notes list shows a recovery notice with their paths, and image cleanup stays
disabled while recovery files exist. These are recovery files;
keep a backup of both files before manually recovering their contents. If both
writes fail, the process logs both errors and exits with status 1.

## Tests

    python3 -m pytest -q tests

## License

Dusk Sticky Notes is licensed under the GNU General Public License,
version 3 or (at your option) any later version (`GPL-3.0-or-later`).
See [LICENSE](LICENSE) for the full license text.
