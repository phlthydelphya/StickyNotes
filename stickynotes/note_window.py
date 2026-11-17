# SPDX-License-Identifier: GPL-3.0-or-later
"""A note popped out of the notes list into its own floating window."""

from gi.repository import Adw, Gio, Gtk

from . import note_menu
from .note_editor import NoteEditor


class NoteWindow(Adw.Window):
    def __init__(self, app, note):
        super().__init__(application=app, title="Sticky Note",
                         default_width=note.width, default_height=note.height)
        self.app = app
        self.store = app.store
        self.note = note
        self.deleted = False
        self.add_css_class("sticky")
        self.add_css_class(note.color)

        self.editor = NoteEditor(self.store, note, on_error=self._show_error,
                                 on_screenshot_added=self.present,
                                 on_screenshot_discarded=self.close,
                                 before_save=self._save_size)
        self.view = self.editor.view

        toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT,
                                  bottom_bar_style=Adw.ToolbarStyle.FLAT)
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(Gtk.ScrolledWindow(child=self.view,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER))
        toolbar.add_bottom_bar(self.editor.format_bar)
        self.toolbar = toolbar
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)

        delete = Gio.SimpleAction.new("delete", None)
        delete.connect("activate", lambda *_: self.confirm_delete())
        self.editor.actions.add_action(delete)
        self.insert_action_group("win", self.editor.actions)
        self.connect("notify::is-active", self._on_active_changed)
        self.connect("close-request", self._on_close_request)

    # --- UI ------------------------------------------------------------------
    def _build_header(self):
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.set_title_widget(Gtk.Box())
        header.pack_start(Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New note (Ctrl+N)",
                                     action_name="app.new-note"))
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close (Ctrl+W)",
                           action_name="window.close")
        header.pack_end(close)
        # Built on every open, so the color mark shows the current color.
        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Menu")
        self.menu_button.set_create_popup_func(self._build_menu)
        header.pack_end(self.menu_button)
        return header

    def _build_menu(self, button):
        button.set_popover(note_menu.popover(
            self.note, (("Notes List", "app.show-list"), ("Delete Note", "win.delete"),
                        ("About Sticky Notes", "app.about")), self.set_color))

    def _on_active_changed(self, *_):
        self.toolbar.set_reveal_bottom_bars(self.is_active())

    def _show_error(self, text):
        self.toasts.add_toast(Adw.Toast(title=text))

    def set_color(self, color):
        if color == self.note.color:
            return
        self.remove_css_class(self.note.color)
        self.note.color = color
        self.add_css_class(color)
        self.store.update(self.note, bump=False)

    # --- saving and closing ---------------------------------------------------------
    def _save_size(self):
        if self.get_width() > 0:
            self.note.width, self.note.height = self.get_width(), self.get_height()

    def flush(self):
        """Write the buffer and window size into the note."""
        if not self.deleted:
            self.editor.flush()

    def _on_close_request(self, *_):
        if not self.deleted:
            self.flush()
            if self.note.is_empty():
                self.deleted = True
                self.store.delete(self.note.id)
            else:
                self.note.open = False
                self.store.update(self.note, bump=False)
        self.editor.shutdown()
        self.app.forget_note_window(self.note.id)
        return False

    def confirm_delete(self):
        if self.editor.is_blank():
            self.deleted = True
            self.editor.shutdown()
            self.close()
            self.store.delete(self.note.id)
            return
        dialog = Adw.AlertDialog(heading="Delete this note?",
                                 body="The note will be removed permanently.")
        dialog.add_response("cancel", "Keep")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_delete_response)
        dialog.present(self)

    def _on_delete_response(self, _dialog, response):
        if response == "delete":
            self.deleted = True
            self.store.delete(self.note.id)
            self.close()