# SPDX-License-Identifier: GPL-3.0-or-later
"""Editing one note: rich text, format bar, autosave, images, screenshots and the
text in them.

Shared by the floating note window and the expanded card in the notes list."""

import os
import shutil
import sys
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from . import ocr
from .capture import CANCELLED, OK, ScreenshotRequest
from .richtext import TAGS, RichEditor

CONTENT_SAVE_DELAY_MS = 300

FORMAT_BUTTONS = (
    ("bold", "format-text-bold-symbolic", "Bold (Ctrl+B)"),
    ("italic", "format-text-italic-symbolic", "Italic (Ctrl+I)"),
    ("underline", "format-text-underline-symbolic", "Underline (Ctrl+U)"),
    ("strike", "format-text-strikethrough-symbolic", "Strikethrough (Ctrl+T)"),
)

# style -> (menu label, button icon)
LIST_MENU = (
    ("bullet", "Bullets", "view-list-bullet-symbolic"),
    ("number", "Numbered list", "view-list-ordered-symbolic"),
    ("check", "Checklist", "checkbox-checked-symbolic"),
)


class NoteEditor:
    """The text view, format bar and "win" editing actions for one note.

    The host packs `view` and `format_bar`, inserts `actions` as "win" on a widget
    that holds both, and may add its own actions (such as "delete") to that group.
    Only one editor may be open per note: each one writes its whole buffer back."""

    def __init__(self, store, note, *, on_error, on_screenshot_added,
                 on_screenshot_discarded, before_save=None):
        self.store = store
        self.note = note
        self.closed = False
        self._on_error = on_error
        self._on_screenshot_added = on_screenshot_added
        self._on_screenshot_discarded = on_screenshot_discarded
        self._before_save = before_save
        self._content_source = 0
        self._screenshot = None
        self._menu_anchor = None  # the image a right-click menu is open for
        self.ocr_program = ocr.find_program()

        self.view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=6, bottom_margin=12,
                                 left_margin=14, right_margin=14, vexpand=True)
        self.view.add_css_class("note-text")
        self.view.connect("paste-clipboard", self._on_paste)
        self.rich = RichEditor(self.view, self.store.image_path)
        self.rich.load(note.content)

        self.format_bar = self._build_format_bar()
        self.actions = self._build_actions()
        self.rich.connect("content-changed", self._on_content_changed)
        self.rich.connect("state-changed", lambda *_: self._sync_format_state())
        self.rich.connect("link-activated", self._on_link)
        self.rich.connect("image-menu", self._on_image_menu)
        self._sync_format_state()

    # --- UI ------------------------------------------------------------------
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
        menu = Gio.Menu()
        for style, text, _icon in LIST_MENU:
            menu.append(text, f"win.list-style::{style}")
        self.list_button = Gtk.MenuButton(icon_name=LIST_MENU[0][2], menu_model=menu,
                                          tooltip_text="Lists: bullets (Ctrl+Shift+L), "
                                                       "numbers, checklist")
        self.list_button.add_css_class("flat")
        self.list_button.add_css_class("list-button")
        bar.append(self.list_button)
        image = Gtk.Button(icon_name="insert-image-symbolic", tooltip_text="Add image",
                           focus_on_click=False, action_name="win.insert-image")
        image.add_css_class("flat")
        bar.append(image)
        shot = Gtk.Button(icon_name="camera-photo-symbolic", tooltip_text="Add screenshot",
                          focus_on_click=False, action_name="win.screenshot")
        shot.add_css_class("flat")
        bar.append(shot)
        shot_text = Gtk.Button(icon_name="scanner-symbolic", tooltip_text="Screenshot to text",
                               focus_on_click=False, action_name="win.screenshot-text")
        shot_text.add_css_class("flat")
        bar.append(shot_text)
        return bar

    def _build_actions(self):
        group = Gio.SimpleActionGroup()
        for name in TAGS:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, n=name: self.rich.toggle(n))
            group.add_action(act)
        # Radio state shows the current lines' style; choosing it again unlists them.
        self.list_action = Gio.SimpleAction.new_stateful("list-style", GLib.VariantType("s"),
                                                         GLib.Variant("s", ""))
        self.list_action.connect("activate", self._on_list_style)
        group.add_action(self.list_action)
        for name, cb in (("bullets", lambda *_: self.rich.toggle_bullets()),
                         ("insert-image", lambda *_: self._choose_image()),
                         ("screenshot", lambda *_: self.take_screenshot()),
                         ("screenshot-text", lambda *_: self.take_screenshot_text()),
                         ("image-text-insert", lambda *_: self._image_text(insert=True)),
                         ("image-text-copy", lambda *_: self._image_text(insert=False))):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            group.add_action(act)
        return group

    # --- state sync ------------------------------------------------------------
    def _sync_format_state(self):
        for name, (btn, handler) in self.format_buttons.items():
            active = self.rich.is_active(name)
            if btn.get_active() != active:
                btn.handler_block(handler)
                btn.set_active(active)
                btn.handler_unblock(handler)
        style = self.rich.list_style()
        self.list_action.set_state(GLib.Variant("s", style or ""))
        icons = {s: icon for s, _text, icon in LIST_MENU}
        self.list_button.set_icon_name(icons.get(style, LIST_MENU[0][2]))
        if style:
            self.list_button.add_css_class("in-list")
        else:
            self.list_button.remove_css_class("in-list")

    def _on_format_toggled(self, _btn, name):
        self.rich.toggle(name)
        self.view.grab_focus()

    def _on_list_style(self, _action, param):
        self.rich.set_list(param.get_string())
        # After the menu closes, which moves focus back to its button.
        GLib.idle_add(lambda: self.view.grab_focus() and GLib.SOURCE_REMOVE)

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
        """Write the buffer into the note."""
        if self.closed:
            return
        if self._content_source:
            GLib.source_remove(self._content_source)
            self._content_source = 0
        content = self.rich.dump()
        changed = content != self.note.content
        self.note.content = content
        if self._before_save is not None:
            self._before_save()
        self.store.update(self.note, bump=changed)

    def shutdown(self):
        """Stop writing to the note; a pending autosave is dropped, so flush first."""
        self.closed = True
        if self._content_source:
            GLib.source_remove(self._content_source)
            self._content_source = 0

    def is_blank(self):
        return not any(seg.get("t", "").strip() or "img" in seg for seg in self.rich.dump())

    def insert_text(self, text):
        """Add plain text at the cursor (a captured selection) and save it."""
        self.rich.buf.insert_at_cursor(text)
        self.view.scroll_mark_onscreen(self.rich.buf.get_insert())

    def insert_block(self, text):
        """Add text at the cursor, starting on a line of its own."""
        buf = self.rich.buf
        buf.begin_user_action()
        if not buf.get_iter_at_mark(buf.get_insert()).starts_line():
            buf.insert_at_cursor("\n")
        buf.insert_at_cursor(text)
        buf.end_user_action()
        self.view.scroll_mark_onscreen(buf.get_insert())

    # --- links -------------------------------------------------------------------
    def _on_link(self, _rich, target):
        root = self.view.get_root()
        if target.startswith(("/", "~")):
            path = os.path.expanduser(target)
            if not os.path.exists(path):
                self._on_error(f"{target} does not exist")
                return
            launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(path))
        else:
            launcher = Gtk.UriLauncher(uri=target)
        launcher.launch(root if isinstance(root, Gtk.Window) else None, None,
                        self._on_link_launched)

    def _on_link_launched(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error as err:
            if not err.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                self._on_error("Could not open the link")

    # --- images ------------------------------------------------------------------
    def _choose_image(self):
        filt = Gtk.FileFilter(name="Images")
        filt.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog = Gtk.FileDialog(title="Add image", filters=filters, default_filter=filt)
        dialog.open(self.view.get_root(), None, self._on_image_chosen)

    def _on_image_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        src = gfile.get_path()
        if not src or self.closed:
            return
        name = self.store.new_image_name(Path(src).suffix or ".png")
        shutil.copyfile(src, self.store.image_path(name))
        self.rich.insert_image_at_cursor(name)

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
        if texture is None or self.closed:
            return
        name = self.store.new_image_name(".png")
        if not texture.save_to_png(str(self.store.image_path(name))):
            return
        self.rich.insert_image_at_cursor(name)

    # --- screenshots ----------------------------------------------------------------
    def take_screenshot(self, discard_if_empty=False):
        """Open the desktop's screenshot UI and insert the result at the cursor.

        discard_if_empty: report a capture that gives nothing while the note is
        still empty (a note made just for this screenshot) to on_screenshot_discarded."""
        if self._screenshot is not None:
            return
        self._screenshot = ScreenshotRequest(
            lambda status, uri: self._on_screenshot(status, uri, discard_if_empty))
        self._screenshot.start()

    def _on_screenshot(self, status, uri, discard_if_empty):
        self._screenshot = None
        if self.closed:
            return  # note closed while the screenshot UI was open
        if status == OK and self._add_screenshot(uri):
            self._on_screenshot_added()
            return
        if status != CANCELLED:
            self._on_error("Screenshot could not be added")
        elif discard_if_empty and self.is_blank():
            self._on_screenshot_discarded()

    def _add_screenshot(self, uri):
        """Copy the portal's file into the note; the user's own copy stays where it is."""
        src = Gio.File.new_for_uri(uri)
        path = src.get_path()
        name = self.store.new_image_name(Path(path).suffix if path else ".png")
        try:
            src.copy(Gio.File.new_for_path(str(self.store.image_path(name))),
                     Gio.FileCopyFlags.NONE, None, None, None)
        except GLib.Error as err:
            print(f"dusk-sticky-notes: screenshot copy failed: {err.message}", file=sys.stderr)
            return False
        self.rich.insert_image_at_cursor(name)
        return True

    # --- text in images -------------------------------------------------------------
    def read_text(self, path, on_text):
        """Run Tesseract on path; on_text(text) gets its lines when it found any."""
        if not self.ocr_program:
            self._on_error("Install tesseract-ocr to read text in images")
            return

        def done(out):
            if self.closed:
                return
            text = ocr.tidy(out) if out is not None else None
            if text is None:
                self._on_error("Could not read the image")
            elif not text:
                self._on_error("No text found in the image")
            else:
                on_text(text)

        if not ocr.read_image(self.ocr_program, path, done):
            self._on_error("Could not read the image")

    def _on_image_menu(self, _rich, picture, anchor):
        self._menu_anchor = anchor
        menu = Gio.Menu()
        menu.append("Insert Text from Image", "win.image-text-insert")
        menu.append("Copy Text from Image", "win.image-text-copy")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_has_arrow(False)
        popover.set_parent(picture)
        # Unparent after the chosen item's action has run.
        popover.connect("closed", lambda p: GLib.idle_add(
            lambda: p.unparent() or GLib.SOURCE_REMOVE))
        popover.popup()

    def _image_text(self, insert):
        anchor, self._menu_anchor = self._menu_anchor, None
        name = self.rich.anchor_files.get(anchor) if anchor is not None else None
        if name is None or anchor.get_deleted():
            return
        try:
            path = self.store.image_path(name)
        except ValueError:
            self._on_error("Could not read the image")
            return
        if insert:
            self.read_text(path, lambda text: self.rich.insert_text_after_image(anchor, text))
        else:
            self.read_text(path, lambda text: self.view.get_clipboard().set(text))

    def take_screenshot_text(self):
        """Open the desktop's screenshot UI and put the text in the picture at the cursor;
        the picture itself is not added to the note."""
        if self._screenshot is not None:
            return
        if not self.ocr_program:
            self._on_error("Install tesseract-ocr to read text in images")
            return
        self._screenshot = ScreenshotRequest(self._on_screenshot_text)
        self._screenshot.start()

    def _on_screenshot_text(self, status, uri):
        self._screenshot = None
        if self.closed or status == CANCELLED:
            return
        path = Gio.File.new_for_uri(uri).get_path() if status == OK else None
        if not path:
            self._on_error("Screenshot could not be taken")
            return
        self.read_text(path, self.insert_block)
