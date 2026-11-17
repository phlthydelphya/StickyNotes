# Dusk Sticky Notes

Sticky notes for DuskOS, modeled on the Sticky Notes in Microsoft OneNote / Windows.
Python + PyGObject, GTK 4 + libadwaita.

## Run

    ./dusk-sticky-notes             # notes list + reopen popped-out notes
    ./dusk-sticky-notes --new-note  # straight to a new note
    ./install.sh                    # install to ~/.local (./install.sh --uninstall to remove)
    ./install.sh --autostart        # also reopen popped-out notes at login (runs --restore)
    ./install.sh --no-shortcut      # skip the global shortcuts on a fresh install
    sudo ./install-search-provider.sh   # once: notes in GNOME's Activities search

## Features

- Notes live in the notes list, with search (type to search, Ctrl+F). Cards are compact;
  click one (or press Enter) to edit it in place, Escape to close it again.
- Search matches every word you type. The color chips under the search entry show only
  notes of those colors.
- Each card's menu (the ⋯ button or a right click) has the note colors, Pin to Top,
  Pop Out and Delete Note. A card shows its pin and how many checklist items are
  ticked ("2/5").
- Deleting a note shows "Note deleted" with Undo for a few seconds, instead of asking first.
- The pop-out button on a card floats that note in its own window; its card stays in the
  list and the button brings the window forward. Closing the window puts it back.
- Each new note gets a random note color.
- Seven colors (yellow, green, pink, purple, blue, gray, charcoal), light and dark palettes.
- Bold, italic, underline, strikethrough, images (file picker or pasted).
- Lists from the format bar: bullets, numbered (renumbers itself), checklist (click a box to tick).
  Or type them: `- ` or `* ` for bullets, `[] ` for a checklist, `1. ` for numbers.
  A ticked item moves below the open ones; unticked, it moves back up.
- Web addresses, email addresses and file paths are underlined; Ctrl+click opens them.
- Activities search: after `sudo ./install-search-provider.sh` and a new login, typing in
  GNOME's overview lists matching notes; choosing one opens it.
- Screenshots: the camera button opens GNOME's screenshot UI (area, window or screen) through
  the xdg-desktop-portal and adds the shot to the note; the tray's "New Note from Screenshot"
  does the same in a new note. GNOME keeps its own copy in ~/Pictures/Screenshots.
- Autosave; popped-out notes reopen on next launch; an empty note is discarded when closed.
- An inactive popped-out note hides its controls and shows only the colored sheet.
- Tray icon (monochrome, follows the panel color): New Note, New Note from Screenshot,
  Show Notes List, Preferences, Quit.
  Left click toggles the notes list, middle click makes a new note.
- Preferences (Ctrl+,): keep running in the tray when closed (on by default; ignored when the
  session has no tray, so the app never runs invisibly), start at login, and the global
  shortcuts (GNOME custom shortcuts): Ctrl+Alt+N runs `dusk-sticky-notes --new-note`, and
  Ctrl+Alt+Shift+N runs `--new-from-selection`, a new note holding the text selected in any
  app. Firefox and Chromium also give the page address, added as "From <address>".

## Shortcuts

| Keys | Action |
|---|---|
| Ctrl+N | New note in the list (in the app) |
| Ctrl+Alt+N | New note from anywhere (if enabled) |
| Ctrl+Alt+Shift+N | New note from the selected text, from anywhere (if enabled) |
| Ctrl+, | Preferences |
| Ctrl+B / I / U / T | Bold / italic / underline / strikethrough |
| Ctrl+Shift+L | Bullets |
| Ctrl+D | Delete note (Undo in the notes list) |
| Ctrl+click | Open a link or path |
| Escape | Close the note being edited in the list |
| Ctrl+W | Close the window |
| Ctrl+Q | Quit (popped-out notes reopen next time) |

## Data

`$XDG_DATA_HOME/dusk-sticky-notes/notes.json` plus `images/`. Writes are atomic.
Pins are an optional key in the same version-1 format.
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
