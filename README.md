# Dusk Sticky Notes

Sticky notes for GNOME, modeled on the Sticky Notes in Microsoft OneNote / Windows.
It is being made for Dusk OS and the Dusk desktop, both still in development and not
released; until then it runs on any GNOME session.
Python + PyGObject, GTK 4 + libadwaita. App ID: `io.github.phlthydelphya.StickyNotes`
(`org.duskos.StickyNotes` before 1.0.1; a start-at-login entry under the old ID moves
over by itself, and notes and settings never depended on the ID).

## Install

- Debian/Ubuntu package `dusk-sticky-notes`, from the Dusk apt repository
  (https://phlthydelphya.github.io/dusk-apt/), or built here with `dpkg-buildpackage -b`.
  It includes the Activities search provider; no extra root step.
- Flatpak: `build-aux/flatpak/io.github.phlthydelphya.StickyNotes.yml` (build command at the
  top of the file). It bundles tesseract for the text in images. In the sandbox the global
  shortcuts are not available, since GNOME's custom shortcuts are outside its reach, and
  "Start at login" asks the desktop through the Background portal.
- The AppStream screenshots in `data/screenshots` come from sample notes:
  `xvfb-run -a -s "-screen 0 1600x1000x24" build-aux/screenshots.py`.

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
- Search matches every word you type, in the note and in the text of its images. The color
  chips under the search entry show only notes of those colors.
- Each card's menu (the ⋯ button or a right click) has the note colors, Pin to Top,
  Remind Me, Pop Out and Delete Note. A card shows its pin, its reminder time and how many
  checklist items are ticked ("2/5").
- Deleting a note shows "Note deleted" with Undo for a few seconds, instead of asking first.
- Reminders: pick In 1 hour, This evening, Tomorrow morning, Next week or any time. When it
  comes, the note pops out and a notification offers Snooze (10 min) and Done. Reminders
  need Sticky Notes running (it stays in the tray); one missed while it was closed shows
  at the next start.
- The pop-out button on a card floats that note in its own window; its card stays in the
  list and the button brings the window forward. Closing the window puts it back.
  New note (+ or Ctrl+N) in a popped-out note pops the new note out too.
- Each new note gets a random note color.
- Seven colors (yellow, green, pink, purple, blue, gray, charcoal), light and dark palettes.
- Style: follow the system, or always light or always dark (Preferences). See
  [Appearance](#appearance).
- Bold, italic, underline, strikethrough, images (file picker or pasted).
- Lists from the format bar: bullets, numbered (renumbers itself), checklist (click a box to tick).
  Or type them: `- ` or `* ` for bullets, `[] ` for a checklist, `1. ` for numbers.
  A ticked item moves below the open ones; unticked, it moves back up.
- Web addresses, email addresses and file paths are underlined; Ctrl+click opens them.
- Text in images: with `tesseract` installed (`tesseract-ocr` package), screenshots and
  pictures are read in the background so a search finds the words in them. Right-click an
  image for Insert Text from Image (the text goes on the lines below it) or Copy Text from
  Image. The scanner button (Screenshot to text) opens GNOME's screenshot UI and puts only the
  text it finds at the cursor; the picture is not added to the note.
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
- Preferences (Ctrl+,): style (follow system, light or dark), keep running in the tray when closed (on by default; ignored when the
  session has no tray, so the app never runs invisibly), start at login, and the global
  shortcuts (GNOME custom shortcuts): Ctrl+Alt+N runs `dusk-sticky-notes --new-note`, and
  Ctrl+Alt+Shift+N runs `--new-from-selection`, a new note holding the text selected in any
  app. Firefox and Chromium also give the page address, added as "From <address>".

## Appearance

Preferences > Appearance > Style picks **Follow system** (the default), **Light** or
**Dark**. The choice applies at once, to the notes list, every popped-out note and the
dialogs, and is kept for the next start. Follow system uses GNOME's Style setting
(`org.gnome.desktop.interface color-scheme`) and changes with it.

Note colors never come from the desktop theme: each color has its own light and dark
shade, and the notes list cards use the same ones, so a yellow note looks like a yellow
note in any theme.

**Desktop themes with one variant in gtk.css.** Some theme installers (theme-unifier,
for example, and the Dusk desktop, still in development) put a single variant of a theme into
`~/.config/gtk-4.0/gtk.css`, as an `@import` of the theme's `gtk.css` (light) or
`gtk-dark.css` (dark). GTK applies that file to every app whatever the app's own style,
so an app set to Light on a dark desktop would get light libadwaita under dark theme
colors. Sticky Notes reads that import and, when its style is the other one, loads the
theme's other variant from the same folder:

| Desktop gtk.css imports | App style shown | Sticky Notes also loads |
|---|---|---|
| `…/gtk-dark.css` | Light | `…/gtk.css` (the theme's light colors) |
| `…/gtk.css` | Dark | `…/gtk-dark.css` (the theme's dark colors) |
| either | same as the import | nothing |
| no theme import, or no sibling file | any | nothing (plain libadwaita colors) |

So the window, header, cards' surroundings, popovers and dialogs use the theme's own
colors for the style on screen (for a theme built like that, its window, view, card and
accent colors). Details:

- The other variant sits just above the user's gtk.css (`GTK_STYLE_PROVIDER_PRIORITY_USER
  + 1`); the notes' own layout and colors sit above that (`+ 2`), so no theme repaints a
  note.
- Only `file://` imports are followed, escapes such as `%20` included; imports inside
  `/* … */` comments are ignored.
- Each variant file is read once while it stays current; switching style only adds or
  removes it.
- Your own rules in `~/.config/gtk-4.0/gtk.css` (anything besides the theme import) are
  applied again on top of the other variant, so they still win over the theme as they do
  on the desktop. Relative `url()`s in them are resolved against `~/.config/gtk-4.0/`.
- Sticky Notes watches `~/.config/gtk-4.0/gtk.css` and rereads it shortly after the
  desktop theme changes (once per change, even when the file is replaced). A theme that
  rewrites its own files without touching gtk.css shows its new colors after Sticky
  Notes restarts.
- The variant is told by file name only: `gtk-dark.css` is the dark one, `gtk.css` the
  light one. A dark-only theme whose `gtk.css` is dark still shows its dark colors when
  Sticky Notes is set to Light.

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

Preferences: `$XDG_CONFIG_HOME/dusk-sticky-notes/settings.json` (`close_to_tray`, and
`style`: `"system"`, `"light"` or `"dark"`; an unknown value falls back to `"system"`).


`$XDG_DATA_HOME/dusk-sticky-notes/notes.json` plus `images/`. Writes are atomic.
Pins and reminders are optional keys in the same version-1 format. Text read from
images is a cache in `$XDG_CACHE_HOME/dusk-sticky-notes/image-text.json`.
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
