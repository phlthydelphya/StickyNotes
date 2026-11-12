# SPDX-License-Identifier: GPL-3.0-or-later
"""A single floating sticky note."""

import shutil
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .palette import COLORS, label
from .richtext import TAGS, RichEditor

CONTENT_SAVE_DELAY_MS = 300

FORMAT_BUTTONS = (
    ("bold", "format-text-bold-symbolic", "Bold (Ctrl+B)"),
    ("italic", "format-text-italic-symbolic", "Italic (Ctrl+I)"),
    ("underline", "format-text-underline-symbolic", "Underline (Ctrl+U)"),
    ("strike", "format-text-strikethrough-symbolic", "Strikethrough (Ctrl+T)"),
)


class NoteWindow(Adw.Window):
    def __init__(self, app, note):
        super().__init__(application=app, title="Sticky Note",
                         default_width=note.width, default_height=note.height)
        self.app = app
        self.store = app.store
        self.note = note
        self.deleted = False
        self._content_source = 0
        self.add_css_class("sticky")
        self.add_css_class(note.color)

        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=12,
                                 left_margin=14, right_margin=14, vexpand=True)
        self.view.add_css_class("note-text")
        self.view.connect("paste-clipboard", self._on_paste)
        self.editor = RichEditor(self.view, self.store.image_path)
        self.editor.load(note.content)

        toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT,
                                  bottom_bar_style=Adw.ToolbarStyle.FLAT)
        toolbar.add_top_bar(self._build_header())
        toolbar.set_content(Gtk.ScrolledWindow(child=self.view,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER))
        toolbar.add_bottom_bar(self._build_format_bar())
        self.toolbar = toolbar
        self.set_content(toolbar)

        self._install_actions()
        self.editor.connect("content-changed", self._on_content_changed)
        self.editor.connect("state-changed", lambda *_: self._sync_format_state())
        self.connect("notify::is-active", self._on_active_changed)
        self.connect("close-request", self._on_close_request)
        self._sync_format_state()

    # --- UI ------------------------------------------------------------------
    def _build_header(self):
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.set_title_widget(Gtk.Box())
        header.pack_start(Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New note (Ctrl+N)",
                                     action_name="app.new-note"))
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close (Ctrl+W)",
                           action_name="window.close")
        header.pack_end(close)
        self.menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Menu",
                                          popover=self._build_menu())
        header.pack_end(self.menu_button)
        return header

    def _build_menu(self):
        popover = Gtk.Popover(has_arrow=False)
        popover.add_css_class("note-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        swatches = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=2)
        self.swatch_buttons = {}
        for color in COLORS:
            mark = Gtk.Image()
            btn = Gtk.Button(child=mark, tooltip_text=label(color))
            btn.add_css_class("swatch")
            btn.add_css_class(color)
            btn.update_property([Gtk.AccessibleProperty.LABEL], [label(color)])
            btn.connect("clicked", self._on_swatch_clicked, color, popover)
            swatches.append(btn)
            self.swatch_buttons[color] = mark
        box.append(swatches)
        box.append(Gtk.Separator())
        for text, icon, action in (("Notes list", "view-list-symbolic", "app.show-list"),
                                   ("Delete note", "user-trash-symbolic", "win.delete"),
                                   ("About Sticky Notes", "help-about-symbolic", "app.about")):
            row = Gtk.Box(spacing=10)
            row.append(Gtk.Image(icon_name=icon))
            row.append(Gtk.Label(label=text, xalign=0))
            btn = Gtk.Button(child=row, action_name=action)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: popover.popdown())
            box.append(btn)
        popover.set_child(box)
        self._mark_swatch()
        return popover

    def _build_format_bar(self):
        bar = Gtk.Box(spacing=2, margin_start=6, margin_end=6, margin_bottom=4)
        bar.add_css_class("format-bar")
        self.format_buttons = {}
        for name, icon, tip in FORMAT_BUTTONS:
            btn = Gtk.ToggleButton(icon_name=icon, tooltip_text=tip)
            btn.add_css_class("flat")
            btn.set_focus_on_click(False)
            handler = btn.connect("toggled", self._on_format_toggled, name)
            self.format_buttons[name] = (btn, handler)
            bar.append(btn)
        bullets = Gtk.ToggleButton(icon_name="view-list-bullet-symbolic",
                                   tooltip_text="Bullets (Ctrl+Shift+L)", focus_on_click=False)
        bullets.add_css_class("flat")
        handler = bullets.connect("toggled", self._on_bullets_toggled)
        self.format_buttons["bullets"] = (bullets, handler)
        bar.append(bullets)
        image = Gtk.Button(icon_name="insert-image-symbolic", tooltip_text="Add image",
                           focus_on_click=False, action_name="win.insert-image")
        image.add_css_class("flat")
        bar.append(image)
        return bar

    def _install_actions(self):
        group = Gio.SimpleActionGroup()
        self.insert_action_group("win", group)
        for name in TAGS:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, n=name: self.editor.toggle(n))
            group.add_action(act)
        for name, cb in (("bullets", lambda *_: self.editor.toggle_bullets()),
                         ("delete", lambda *_: self.confirm_delete()),
                         ("insert-image", lambda *_: self._choose_image())):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            group.add_action(act)

    # --- state sync ------------------------------------------------------------
    def _sync_format_state(self):
        for name, (btn, handler) in self.format_buttons.items():
            active = self.editor.bullets_active() if name == "bullets" else self.editor.is_active(name)
            if btn.get_active() != active:
                btn.handler_block(handler)
                btn.set_active(active)
                btn.handler_unblock(handler)

    def _on_format_toggled(self, _btn, name):
        self.editor.toggle(name)
        self.view.grab_focus()

    def _on_bullets_toggled(self, _btn):
        self.editor.toggle_bullets()
        self.view.grab_focus()

    def _on_active_changed(self, *_):
        self.toolbar.set_reveal_bottom_bars(self.is_active())

    def _mark_swatch(self):
        for color, mark in self.swatch_buttons.items():
            mark.set_from_icon_name("object-select-symbolic" if color == self.note.color else None)

    def _on_swatch_clicked(self, _btn, color, popover):
        popover.popdown()
        self.set_color(color)

    def set_color(self, color):
        if color == self.note.color:
            return
        self.remove_css_class(self.note.color)
        self.note.color = color
        self.add_css_class(color)
        self._mark_swatch()
        self.store.update(self.note)

    # --- content -----------------------------------------------------------------
    def _on_content_changed(self, *_):
        if self._content_source:
            GLib.source_remove(self._content_source)
        self._content_source = GLib.timeout_add(CONTENT_SAVE_DELAY_MS, self._content_timeout)

    def _content_timeout(self):
        self._content_source = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self):
        """Write the buffer and window size into the note."""
        if self.deleted:
            return
        if self._content_source:
            GLib.source_remove(self._content_source)
            self._content_source = 0
        content = self.editor.dump()
        changed = content != self.note.content
        self.note.content = content
        if self.get_width() > 0:
            self.note.width, self.note.height = self.get_width(), self.get_height()
        self.store.update(self.note, bump=changed)

    def _on_close_request(self, *_):
        if not self.deleted:
            self.flush()
            if self.note.is_empty():
                self.deleted = True
                self.store.delete(self.note.id)
            else:
                self.note.open = False
                self.store.update(self.note, bump=False)
        self.app.forget_note_window(self.note.id)
        return False

    def close_deleted(self):
        self.deleted = True
        self.close()

    def confirm_delete(self):
        content = self.editor.dump()
        if not any(seg.get("t", "").strip() or "img" in seg for seg in content):
            self.close_deleted()
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

    # --- images ------------------------------------------------------------------
    def _choose_image(self):
        filt = Gtk.FileFilter(name="Images")
        filt.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog = Gtk.FileDialog(title="Add image", filters=filters, default_filter=filt)
        dialog.open(self, None, self._on_image_chosen)

    def _on_image_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        src = gfile.get_path()
        if not src:
            return
        name = self.store.new_image_name(Path(src).suffix or ".png")
        shutil.copyfile(src, self.store.image_path(name))
        self.editor.insert_image_at_cursor(name)

    def _on_paste(self, view):
        clipboard = view.get_clipboard()
        formats = clipboard.get_formats()
        if formats.contain_gtype(Gdk.Texture) and not formats.contain_mime_type("text/plain"):
            view.stop_emission_by_name("paste-clipboard")
            clipboard.read_texture_async(None, self._on_paste_texture)

    def _on_paste_texture(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            return
        if texture is None:
            return
        name = self.store.new_image_name(".png")
        if not texture.save_to_png(str(self.store.image_path(name))):
            return
        self.editor.insert_image_at_cursor(name)
