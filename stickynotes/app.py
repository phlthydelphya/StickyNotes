# SPDX-License-Identifier: GPL-3.0-or-later
"""Application: owns the store, the notes list and the open note windows."""

import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .hub_window import HubWindow  # noqa: E402
from .note_window import NoteWindow  # noqa: E402
from .palette import build_css  # noqa: E402
from .preferences import Preferences, PreferencesDialog  # noqa: E402
from .store import LoadState, NoteStore  # noqa: E402
from .tray import Tray  # noqa: E402

APP_ID = "org.duskos.StickyNotes"
STYLE = Path(__file__).with_name("style.css")

ACCELS = {
    "app.new-note": ["<Control>n"],
    "app.quit": ["<Control>q"],
    "app.preferences": ["<Control>comma"],
    "window.close": ["<Control>w"],
    "win.bold": ["<Control>b"],
    "win.italic": ["<Control>i"],
    "win.underline": ["<Control>u"],
    "win.strike": ["<Control>t"],
    "win.bullets": ["<Control><Shift>l"],
    "win.delete": ["<Control>d"],
}


class StickyApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("new-note", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Create a new note", None)
        self.add_main_option("restore", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Only reopen the notes left open (used at login)", None)
        self.store = None
        self.hub = None
        self.note_windows = {}
        self._restored = False
        self.exit_status = 0
        self._shutdown_saved = False
        self._save_dialog = None
        self._newer_window = None
        self.prefs = None
        self.tray = None
        self._held = False
        self._prefs_dialog = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Sticky Notes")
        Gtk.Window.set_default_icon_name(APP_ID)
        # Above USER priority: a desktop theme's ~/.config/gtk-4.0/gtk.css must not
        # repaint the notes, since a note's color is its identity.
        display = Gdk.Display.get_default()
        source_icons = Path(__file__).resolve().parent.parent / "data"
        from_checkout = (source_icons / f"{APP_ID}.svg").is_file()
        if from_checkout:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(source_icons))
        priority = Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
        layout = Gtk.CssProvider()
        layout.load_from_path(str(STYLE))
        Gtk.StyleContext.add_provider_for_display(display, layout, priority)
        self._palette_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self._palette_css, priority)
        styles = Adw.StyleManager.get_default()
        styles.connect("notify::dark", self._load_palette)
        self._load_palette(styles)

        self.store = NoteStore()
        # Only collect orphan images against a notes.json that really loaded:
        # after a corrupt or missing file, every image would look orphaned.
        if self.store.load() is LoadState.OK:
            self.store.gc_images()

        self._add_action("new-note", lambda *_: self.new_note())
        self._add_action("new-screenshot-note", lambda *_: self.new_screenshot_note())
        self._add_action("show-list", lambda *_: self.show_list())
        self._add_action("quit", lambda *_: self.quit_saving())
        self._add_action("about", lambda *_: self.show_about())
        self._add_action("preferences", lambda *_: self.show_preferences())
        self._add_action("open-note", lambda _a, p: self.open_note(p.get_string()), "s")
        self._add_action("delete-note", lambda _a, p: self.delete_note(p.get_string()), "s")
        for action, accels in ACCELS.items():
            self.set_accels_for_action(action, accels)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_sigterm)

        self.prefs = Preferences().load()
        self.tray = Tray(self, APP_ID, "Sticky Notes",
                         icon_theme_path=str(source_icons) if from_checkout else "",
                         on_availability=self._on_tray_availability)
        # Decide the hold now: with --restore and no open notes, an async check
        # would come too late and the app would exit before the icon appears.
        self._update_hold(self.tray.start())

    # --- background / tray -----------------------------------------------------
    def _update_hold(self, tray_ok=None):
        """Stay alive with no windows only when close-to-tray is on and a tray exists."""
        if tray_ok is None:
            tray_ok = self.tray is not None and self.tray.available
        want = self.prefs.close_to_tray and tray_ok
        if want and not self._held:
            self.hold()
            self._held = True
        elif not want and self._held:
            self._held = False
            self.release()

    def _on_tray_availability(self, _available):
        self._update_hold()
        if self._prefs_dialog is not None:
            self._prefs_dialog.refresh_tray()

    def set_close_to_tray(self, enabled):
        """Apply now; return False when the choice could not be saved for next time."""
        self.prefs.values["close_to_tray"] = enabled
        self._update_hold()
        try:
            self.prefs.save()
        except OSError as err:
            print(f"dusk-sticky-notes: preferences not saved: {err}", file=sys.stderr)
            return False
        return True

    def toggle_list(self):
        if self.hub is not None and self.hub.is_active():
            self.hub.close()
        else:
            self.show_list()

    def show_preferences(self):
        if self._prefs_dialog is not None:
            root = self._prefs_dialog.get_root()
            if root is not None:
                root.present()
            return
        # From the tray there may be no window: give the dialog the notes list
        # as its parent, which also keeps the app alive while it is open.
        parent = self.get_active_window()
        if parent is None:
            self.show_list()
            parent = self.hub
        dialog = PreferencesDialog(self)
        dialog.connect("closed", self._on_prefs_closed)
        self._prefs_dialog = dialog
        dialog.present(parent)

    def _on_prefs_closed(self, _dialog):
        self._prefs_dialog = None

    def _load_palette(self, styles, *_):
        self._palette_css.load_from_string(build_css(styles.get_dark()))

    def _add_action(self, name, callback, param=None):
        act = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
        act.connect("activate", callback)
        self.add_action(act)

    def _restore(self):
        if not self._restored:
            self._restored = True
            for note in self.store.sorted():
                if note.open and note.is_empty():
                    self.store.delete(note.id)  # left open untouched at last quit
                elif note.open:
                    self.open_note(note.id)

    def do_command_line(self, command_line):
        if self.store.read_only:
            self._show_newer()
            return 0
        options = command_line.get_options_dict()
        if options.contains("new-note"):
            self._restore()
            self.new_note()
        elif options.contains("restore"):
            # With no open notes this opens nothing: the app then waits in the
            # tray, or exits when there is no tray.
            self._restore()
        else:
            self.activate()
        return 0

    def do_activate(self):
        if self.store.read_only:
            self._show_newer()
            return
        self._restore()
        self.show_list()

    def do_shutdown(self):
        try:
            self._save_for_shutdown()
        finally:
            tray = getattr(self, "tray", None)
            if tray is not None:
                tray.stop()
            Adw.Application.do_shutdown(self)

    # --- windows -------------------------------------------------------------
    def _show_newer(self):
        if self._newer_window is not None:
            self._newer_window.present()
            return
        self._newer_window = Adw.ApplicationWindow(application=self, title="Sticky Notes")
        self._newer_window.present()
        dialog = Adw.AlertDialog(heading="Notes were saved by a newer version",
                                 body="Your notes have not been changed. Open them with a newer version of Sticky Notes.")
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")
        dialog.connect("response", lambda *_: self.quit())
        dialog.present(self._newer_window)

    def show_about(self):
        about = Adw.AboutDialog(
            application_name="Sticky Notes",
            application_icon=APP_ID,
            developer_name="phlthydelphya",
            version=__version__,
            comments="Quick colored notes for the Dusk desktop.",
            developers=["phlthydelphya"],
            designers=["phlthydelphya"],
            copyright="© 2026 phlthydelphya",
            license_type=Gtk.License.GPL_3_0,
        )
        about.add_credit_section("Built with", ["GTK https://gtk.org",
                                                "libadwaita https://gnome.pages.gitlab.gnome.org/libadwaita/",
                                                "PyGObject https://pygobject.gnome.org"])
        about.present(self.get_active_window())

    def show_list(self):
        if self.store.read_only:
            self._show_newer()
            return
        if self.hub is None:
            self.hub = HubWindow(self)
            self.hub.connect("close-request", self._on_hub_closed)
        self.hub.present()

    def _on_hub_closed(self, *_):
        self.hub = None
        return False

    def new_note(self):
        if self.store.read_only:
            self._show_newer()
            return
        note = self.store.create()
        self.open_note(note.id)

    def new_screenshot_note(self):
        """New note holding a screenshot; dropped again if the capture is cancelled."""
        if self.store.read_only:
            self._show_newer()
            return
        note = self.store.create()
        self.open_note(note.id)
        win = self.note_windows.get(note.id)
        if win is not None:
            win.take_screenshot(discard_if_empty=True)

    def open_note(self, note_id):
        win = self.note_windows.get(note_id)
        if win is None:
            note = self.store.get(note_id)
            if note is None:
                return
            win = NoteWindow(self, note)
            self.note_windows[note_id] = win
            if not note.open:
                note.open = True
                self.store.update(note, bump=False)
        win.present()

    def forget_note_window(self, note_id):
        self.note_windows.pop(note_id, None)

    def delete_note(self, note_id):
        win = self.note_windows.get(note_id)
        if win is not None:
            win.present()
            win.confirm_delete()
            return
        note = self.store.get(note_id)
        if note is None or self.hub is None:
            return
        dialog = Adw.AlertDialog(heading="Delete this note?",
                                 body="The note will be removed permanently.")
        dialog.add_response("cancel", "Keep")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", lambda _d, r: r == "delete" and self.store.delete(note_id))
        dialog.present(self.hub)

    # --- shutdown --------------------------------------------------------------
    def _flush_all(self):
        """Save every open note; propagate failure to the caller."""
        if self.store is None or self.store.read_only:
            return True
        for win in list(self.note_windows.values()):
            win.flush()
        return self.store.try_save()

    def quit_saving(self):
        if self._flush_all():
            self._shutdown_saved = True
            self.quit()
            return
        if self._save_dialog is not None:
            return
        dialog = Adw.AlertDialog(heading=f"Notes could not be saved: {self.store.last_error}")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("retry", "Retry")
        dialog.add_response("quit", "Quit Anyway")
        dialog.set_response_appearance("quit", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("retry")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_save_response)
        self._save_dialog = dialog
        if self.get_active_window() is None:
            self.show_list()
        dialog.present(self.get_active_window())

    def _on_save_response(self, _dialog, response):
        self._save_dialog = None
        if response == "retry":
            self.quit_saving()
        elif response == "quit":
            self._save_for_shutdown()
            self.quit()

    def _save_for_shutdown(self):
        if self._shutdown_saved:
            return
        self._shutdown_saved = True
        if self._flush_all():
            return
        original_error = self.store.last_error
        try:
            path = self.store.save_emergency()
            print(f"dusk-sticky-notes: normal save failed ({original_error}); recovery saved to {path}",
                  file=sys.stderr)
        except OSError as err:
            print(f"dusk-sticky-notes: save failed: {original_error}; recovery save failed: {err}",
                  file=sys.stderr)
            self.exit_status = 1

    def _on_sigterm(self):
        self._save_for_shutdown()
        self.quit()
        return GLib.SOURCE_REMOVE


def main():
    app = StickyApp()
    result = app.run(sys.argv)
    return app.exit_status or result
