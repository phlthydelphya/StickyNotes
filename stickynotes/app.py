# SPDX-License-Identifier: GPL-3.0-or-later
"""Application: owns the store, the notes list and the open note windows."""

import signal
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .hub_window import HubWindow  # noqa: E402
from .note_window import NoteWindow  # noqa: E402
from .ocr import OcrIndex  # noqa: E402
from .palette import COLORS, build_css  # noqa: E402
from .preferences import Preferences, PreferencesDialog  # noqa: E402
from .reminders import SNOOZE_MINUTES, ReminderDialog, preset_time  # noqa: E402
from .search_provider import SearchProvider  # noqa: E402
from .store import LoadState, NoteStore  # noqa: E402
from .theme import ThemeVariant, color_scheme  # noqa: E402
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
# Started by GNOME Shell for a search (--gapplication-service): stay this long
# after the last call, unless a window or the tray keeps the app running.
SERVICE_TIMEOUT_MS = 15000
# Reminders are checked against the wall clock at least this often, so a
# suspended laptop still shows them soon after it wakes.
REMINDER_CHECK_S = 60
UNDO_TIMEOUT_S = 8


class StickyApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option("new-note", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Create a new note", None)
        self.add_main_option("restore", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Only reopen the notes left open (used at login)", None)
        self.add_main_option("new-from-selection", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Create a new note holding the selected text", None)
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
        self._background = False  # tray started and hold decided
        self._pending_deletes = {}  # note id -> deleted note, while Undo is offered
        self._remind_source = 0
        self.ocr = None
        self.search_provider = SearchProvider(self, APP_ID)

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
        # The desktop theme's other variant sits between USER and the notes' own CSS.
        user = Gtk.STYLE_PROVIDER_PRIORITY_USER
        self._theme = ThemeVariant(
            GLib.get_user_config_dir(),
            attach=lambda p: Gtk.StyleContext.add_provider_for_display(display, p, user + 1),
            detach=lambda p: Gtk.StyleContext.remove_provider_for_display(display, p))
        priority = user + 2
        layout = Gtk.CssProvider()
        layout.load_from_path(str(STYLE))
        Gtk.StyleContext.add_provider_for_display(display, layout, priority)
        self._palette_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self._palette_css, priority)
        self.prefs = Preferences().load()
        styles = Adw.StyleManager.get_default()
        styles.connect("notify::dark", self._load_palette)
        styles.set_color_scheme(color_scheme(self.prefs.style))
        self._load_palette(styles)

        self.store = NoteStore()
        # Only collect orphan images against a notes.json that really loaded:
        # after a corrupt or missing file, every image would look orphaned.
        if self.store.load() is LoadState.OK:
            self.store.gc_images()

        self._add_action("new-note", lambda *_: self._on_new_note_action())
        self._add_action("new-screenshot-note", lambda *_: self.new_screenshot_note())
        self._add_action("show-list", lambda *_: self.show_list())
        self._add_action("quit", lambda *_: self.quit_saving())
        self._add_action("about", lambda *_: self.show_about())
        self._add_action("preferences", lambda *_: self.show_preferences())
        self._add_action("open-note", lambda _a, p: self.open_note(p.get_string()), "s")
        self._add_action("delete-note", lambda _a, p: self.delete_note(p.get_string()), "s")
        self._add_action("toggle-pin", lambda _a, p: self.toggle_pin(p.get_string()), "s")
        self._add_action("set-color", lambda _a, p: self.set_note_color(*p.unpack()), "(ss)")
        self._add_action("remind", lambda _a, p: self.remind_preset(*p.unpack()), "(ss)")
        self._add_action("remind-pick", lambda _a, p: self.pick_reminder(p.get_string()), "s")
        self._add_action("remind-clear", lambda _a, p: self.set_reminder(p.get_string(), None), "s")
        self._add_action("snooze-reminder", lambda _a, p: self.snooze(p.get_string()), "s")
        self._add_action("reminder-done", lambda _a, p: self.reminder_done(p.get_string()), "s")
        for action, accels in ACCELS.items():
            self.set_accels_for_action(action, accels)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_sigterm)

        self.tray = Tray(self, APP_ID, "Sticky Notes",
                         icon_theme_path=str(source_icons) if from_checkout else "",
                         on_availability=self._on_tray_availability)
        if not self.store.read_only:
            self.ocr = OcrIndex(self.store, on_update=self._on_image_text)
            self.ocr.schedule_scan()
            self.store.connect(self._on_note_changed)
            self._schedule_reminders()
        if self.get_flags() & Gio.ApplicationFlags.IS_SERVICE:
            # Started for a desktop search: no tray icon unless a note is shown.
            self.set_inactivity_timeout(SERVICE_TIMEOUT_MS)
        else:
            self._start_background()

    def do_dbus_register(self, connection, object_path):
        if not Adw.Application.do_dbus_register(self, connection, object_path):
            return False
        try:
            self.search_provider.register(connection)
        except GLib.Error as err:
            print(f"dusk-sticky-notes: search provider not exported: {err.message}", file=sys.stderr)
        return True

    def do_dbus_unregister(self, connection, object_path):
        self.search_provider.unregister()
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def _on_note_changed(self, _note_id):
        self._schedule_reminders()
        if self.ocr is not None:
            self.ocr.schedule_scan()

    def _on_image_text(self):
        if self.hub is not None:
            self.hub._schedule_rebuild()  # a search may now match the new text

    # --- background / tray -----------------------------------------------------
    def _start_background(self):
        """Show the tray icon and decide whether the app outlives its windows."""
        if self._background:
            return
        self._background = True
        # Decide the hold now: with --restore and no open notes, an async check
        # would come too late and the app would exit before the icon appears.
        self._update_hold(self.tray.start())

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
        return self._save_prefs()

    def set_style(self, style):
        """Apply now; return False when the choice could not be saved for next time."""
        self.prefs.values["style"] = style
        Adw.StyleManager.get_default().set_color_scheme(color_scheme(style))
        return self._save_prefs()

    def _save_prefs(self):
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
        dark = styles.get_dark()
        self._palette_css.load_from_string(build_css(dark))
        self._theme.update(dark)

    def _add_action(self, name, callback, param=None):
        act = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
        act.connect("activate", callback)
        self.add_action(act)

    def _restore(self):
        if not self._restored:
            self._restored = True
            for note in self.store.sorted():
                if note.is_empty():
                    self.store.delete(note.id)  # made but never written to
                elif note.open:
                    self.open_note(note.id)  # popped out at last quit

    def do_command_line(self, command_line):
        if self.store.read_only:
            self._show_newer()
            return 0
        options = command_line.get_options_dict()
        if options.contains("new-note"):
            self._restore()
            self.new_note()
        elif options.contains("new-from-selection"):
            self._restore()
            self.new_note(from_selection=True)
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
            store = getattr(self, "store", None)
            for note in getattr(self, "_pending_deletes", {}).values():
                store.drop_images(note)  # Undo is gone with the app
        finally:
            for name in ("tray", "ocr"):
                part = getattr(self, name, None)
                if part is not None:
                    part.stop()
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
        self._start_background()
        if self.hub is None:
            self.hub = HubWindow(self)
            self.hub.connect("close-request", self._on_hub_closed)
        self.hub.present()

    def _on_hub_closed(self, *_):
        self.hub = None
        return False

    def _on_new_note_action(self):
        # Ctrl+N or the + button in a popped-out note: the new note pops out too.
        self.new_note(pop_out=isinstance(self.get_active_window(), NoteWindow))

    def new_note(self, from_selection=False, pop_out=False):
        if self.store.read_only:
            self._show_newer()
            return
        note = self.store.create()
        if pop_out:
            self.open_note(note.id)
            return
        # Otherwise new notes are written in the notes list; the pop-out button floats one.
        self.show_list()
        self.hub.edit_note(note.id)
        if from_selection:
            self.hub.capture_selection(note.id)

    def new_screenshot_note(self):
        """New note holding a screenshot; dropped again if the capture is cancelled."""
        if self.store.read_only:
            self._show_newer()
            return
        note = self.store.create()
        self.show_list()
        self.hub.edit_note(note.id)
        self.hub.take_screenshot(note.id)

    def open_note(self, note_id):
        """Pop a note out into its own window, or bring that window forward."""
        win = self.note_windows.get(note_id)
        if win is None:
            note = self.store.get(note_id)
            if note is None:
                return
            if self.hub is not None:
                self.hub.release_note(note_id)  # one editor per note
            self._start_background()
            win = NoteWindow(self, note)
            self.note_windows[note_id] = win
            if not note.open:
                note.open = True
                self.store.update(note, bump=False)
        win.present()

    def forget_note_window(self, note_id):
        self.note_windows.pop(note_id, None)

    def delete_note(self, note_id):
        """Delete at once; the notes list offers Undo for a few seconds."""
        note = self.store.get(note_id)
        if note is None:
            return
        win = self.note_windows.get(note_id)
        if win is not None:
            win.close_for_delete()  # saves its last edit, then closes
        elif self.hub is not None:
            self.hub.release_note(note_id)  # saves an edit open in the list
        if note.is_empty():
            self.store.delete(note_id)  # nothing to bring back
            return
        self.store.delete(note_id, keep_images=True)
        self._pending_deletes[note_id] = note
        self.show_list()
        toast = Adw.Toast(title="Note deleted", button_label="Undo", timeout=UNDO_TIMEOUT_S,
                          priority=Adw.ToastPriority.HIGH)
        toast.connect("button-clicked", lambda *_: self.undo_delete(note_id))
        toast.connect("dismissed", lambda *_: self._forget_delete(note_id))
        self.hub.add_toast(toast)

    def undo_delete(self, note_id):
        note = self._pending_deletes.pop(note_id, None)
        if note is None:
            return
        self.store.restore(note)
        if note.open:
            self.open_note(note_id)  # it was popped out: float it again

    def _forget_delete(self, note_id):
        note = self._pending_deletes.pop(note_id, None)
        if note is not None:
            self.store.drop_images(note)

    # --- note settings from the menus --------------------------------------------
    def _message(self, text):
        """A short message in the active window."""
        show = getattr(self.get_active_window(), "_show_error", None)
        if show is not None:
            show(text)

    def toggle_pin(self, note_id):
        note = self.store.get(note_id)
        if note is not None:
            note.pinned = not note.pinned
            self.store.update(note, bump=False)

    def set_note_color(self, note_id, color):
        note = self.store.get(note_id)
        if note is None or color not in COLORS:
            return
        win = self.note_windows.get(note_id)
        if win is not None:
            win.set_color(color)
        elif note.color != color:
            note.color = color
            self.store.update(note, bump=False)

    # --- reminders -----------------------------------------------------------------
    def remind_preset(self, note_id, key):
        when = preset_time(key)
        if when is not None:
            self.set_reminder(note_id, when)

    def pick_reminder(self, note_id):
        note = self.store.get(note_id)
        if note is None:
            return
        dialog = ReminderDialog(note.remind_at, lambda when: self.set_reminder(note_id, when))
        dialog.present(self.get_active_window())

    def set_reminder(self, note_id, when):
        note = self.store.get(note_id)
        if note is None:
            return
        if when is not None and when <= time.time():
            self._message("That time has already passed")
            return
        note.remind_at = when
        self.store.update(note, bump=False)
        self.withdraw_notification(f"reminder-{note_id}")

    def snooze(self, note_id):
        note = self.store.get(note_id)
        if note is not None:
            note.remind_at = time.time() + SNOOZE_MINUTES * 60
            self.store.update(note, bump=False)

    def reminder_done(self, note_id):
        self.withdraw_notification(f"reminder-{note_id}")

    def _schedule_reminders(self):
        if self._remind_source:
            GLib.source_remove(self._remind_source)
            self._remind_source = 0
        if self.store is None or self.store.read_only:
            return
        times = [n.remind_at for n in self.store.notes.values() if n.remind_at is not None]
        if times:
            wait = max(0.0, min(min(times) - time.time(), REMINDER_CHECK_S))
            self._remind_source = GLib.timeout_add(int(wait * 1000), self._on_reminder_timeout)

    def _on_reminder_timeout(self):
        self._remind_source = 0
        now = time.time()
        for note in self.store.sorted():
            if note.remind_at is not None and note.remind_at <= now:
                self.fire_reminder(note)
        self._schedule_reminders()
        return GLib.SOURCE_REMOVE

    def fire_reminder(self, note):
        """Bring the note back: float it on top and send a notification."""
        note.remind_at = None
        self.store.update(note, bump=False)
        self.open_note(note.id)
        lines = [line for line in note.plain_text().replace("\ufffc", "").splitlines()
                 if line.strip()]
        notification = Gio.Notification.new("Sticky note reminder")
        notification.set_body("\n".join(lines[:2]) or "Image note")
        notification.set_priority(Gio.NotificationPriority.HIGH)
        target = GLib.Variant("s", note.id)
        notification.set_default_action_and_target("app.open-note", target)
        notification.add_button_with_target(f"Snooze {SNOOZE_MINUTES} min",
                                            "app.snooze-reminder", target)
        notification.add_button_with_target("Done", "app.reminder-done", target)
        self.send_notification(f"reminder-{note.id}", notification)

    # --- desktop search (search_provider.py) ------------------------------------------
    def activate_search_result(self, note_id, _timestamp=0):
        if self.store.read_only:
            self._show_newer()
            return
        self._restore()
        note = self.store.get(note_id)
        if note is not None and note.open:
            self.open_note(note_id)
            return
        self.show_list()
        if note is not None:
            self.hub.edit_note(note_id)

    def launch_search(self, terms, _timestamp=0):
        if self.store.read_only:
            self._show_newer()
            return
        self._restore()
        self.show_list()
        self.hub.set_search(" ".join(terms))

    # --- shutdown --------------------------------------------------------------
    def _flush_all(self):
        """Save every open note; propagate failure to the caller."""
        if self.store is None or self.store.read_only:
            return True
        for win in list(self.note_windows.values()):
            win.flush()
        hub = getattr(self, "hub", None)
        if hub is not None:
            hub.flush_editor()
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
