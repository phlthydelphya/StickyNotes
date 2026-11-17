# SPDX-License-Identifier: GPL-3.0-or-later
"""The notes list: browse, search and edit every note; pop one out to float it."""

import datetime

import gi

gi.require_version("Graphene", "1.0")

from gi.repository import Graphene  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import note_menu  # noqa: E402
from .note_editor import NoteEditor  # noqa: E402
from .palette import COLORS, label  # noqa: E402
from .richtext import checklist_progress  # noqa: E402
from .selection import capture_text, read_selection  # noqa: E402

REBUILD_DELAY_MS = 120
# Cards are compact; the selected one expands into an editor.
COMPACT_LINES = 2
COMPACT_IMAGE_HEIGHT = 56
EDITOR_MIN_HEIGHT = 80
EXPAND_MS = 200
# A captured selection waits this long for the window to get focus (Wayland
# hands the selection only to the focused app), then is read anyway.
CAPTURE_WAIT_MS = 1500
CAPTURE_SETTLE_MS = 100


def format_time(ts):
    when = datetime.datetime.fromtimestamp(ts)
    now = datetime.datetime.now()
    if when.date() == now.date():
        return when.strftime("%H:%M")
    if when.year == now.year:
        return f"{when:%b} {when.day}"
    return f"{when:%b} {when.day}, {when.year}"


class HubWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Sticky Notes", default_width=360,
                         default_height=600)
        self.app = app
        self.store = app.store
        self._rebuild_source = 0
        self._menu_drop_source = 0
        self._closed = False
        self._terms = []
        self._colors = set()  # color filter chips that are on
        self._rows = {}  # note id -> row, kept across store changes
        self._editing = None  # the row whose card is open in the inline editor
        self.connect("close-request", self._on_close_request)

        self.window_title = Adw.WindowTitle(title="Sticky Notes")
        self._save_listener = self.store.connect_save_state(self._update_save_state)
        self._update_save_state()
        header = Adw.HeaderBar(title_widget=self.window_title)
        header.pack_start(Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New note (Ctrl+N)",
                                     action_name="app.new-note"))

        # Hidden search that drops down under the header: toggle button, Ctrl+F,
        # or just start typing. Escape closes it and clears the query.
        self.search = Gtk.SearchEntry(placeholder_text="Search notes", hexpand=True)
        self.search.connect("search-changed", lambda *_: self._rebuild())
        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search_box.append(self.search)
        search_box.append(self._build_color_chips())
        self.search_bar = Gtk.SearchBar(child=search_box, show_close_button=False)
        self.search_bar.connect_entry(self.search)
        self.search_bar.set_key_capture_widget(self)
        self.search_bar.connect("notify::search-mode-enabled", self._on_search_mode)
        search_toggle = Gtk.ToggleButton(icon_name="system-search-symbolic",
                                         tooltip_text="Search (Ctrl+F)")
        search_toggle.bind_property("active", self.search_bar, "search-mode-enabled",
                                    GObject.BindingFlags.BIDIRECTIONAL
                                    | GObject.BindingFlags.SYNC_CREATE)
        menu = Gio.Menu()
        menu.append("Preferences", "app.preferences")
        menu.append("About Sticky Notes", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Main menu", primary=True))
        header.pack_end(search_toggle)

        # Click (or Enter) opens a compact card's note in place; its pop-out button
        # floats it in its own window. Rows are sorted and filtered in place, so a
        # store change never rebuilds the card being edited.
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("note-list")
        self.list.set_sort_func(self._sort_rows)
        self.list.set_filter_func(self._filter_row)
        self.list.connect("row-activated", self._on_row_activated)
        # Context menu is made per right-click and dropped when it closes;
        # rebuilds wait while it is open so autosave never tears it down.
        self.menu_popover = None
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._on_right_click)
        self.list.add_controller(click)

        self.empty = Adw.StatusPage(icon_name="org.duskos.StickyNotes", title="Start a new note",
                                    description="Press Ctrl+N or the + button to capture a thought.")
        self.no_results = Adw.StatusPage(icon_name="edit-find-symbolic", title="No matching notes")

        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.list,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.stack.add_named(self.empty, "empty")
        self.stack.add_named(self.no_results, "no-results")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        recovery_files = self.store.recovery_files()
        if recovery_files:
            notice = Adw.Banner(title="Recovered notes need your attention", button_label="Details", revealed=True)
            notice.connect("button-clicked", self._show_recovery_notice)
            body.append(notice)
        self.stack.set_vexpand(True)
        body.append(self.stack)
        self.toasts = Adw.ToastOverlay(child=body)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.search_bar)
        view.set_content(self.toasts)
        self.set_content(view)

        find = Gtk.ShortcutController()
        find.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                       action=Gtk.CallbackAction.new(self._focus_search)))
        self.add_controller(find)

        self._note_listener = self.store.connect(lambda _id: self._schedule_rebuild())
        self._rebuild()

    def _show_recovery_notice(self, *_):
        paths = "\n".join(str(path) for path in self.store.recovery_files())
        dialog = Adw.AlertDialog(
            heading="Unsaved notes were preserved",
            body="A previous save failed. Recovery files may contain newer edits. "
                 "They have not been merged into your notes. Keep backups before recovering them.\n\n" + paths)
        dialog.add_response("close", "Close")
        dialog.set_close_response("close")
        dialog.present(self)

    def _on_close_request(self, *_):
        if self._closed:
            return False
        self._closed = True
        self._collapse(discard_empty=True)
        self.store.disconnect(self._note_listener)
        self.store.disconnect(self._save_listener)
        for name in ("_rebuild_source", "_menu_drop_source"):
            source = getattr(self, name)
            if source:
                GLib.source_remove(source)
                setattr(self, name, 0)
        if self.menu_popover is not None:
            popover = self.menu_popover
            self.menu_popover = None
            popover.popdown()
            if popover.get_parent() is not None:
                popover.unparent()
        # GTK destroys the window after this returns but only disposes it once the
        # last reference goes. On X11, IBus may still have an idle queued from the
        # window's last focus-in that reads the search entry's surface, which a
        # destroyed window no longer has: a segfault. Disposing first, at a priority
        # above IBus's G_PRIORITY_DEFAULT_IDLE, clears the entry's input method client.
        GLib.idle_add(self._dispose_after_close, priority=GLib.PRIORITY_HIGH_IDLE)
        return False

    def _dispose_after_close(self):
        self.run_dispose()
        return GLib.SOURCE_REMOVE

    def _update_save_state(self):
        if self._closed:
            return
        self.window_title.set_subtitle("Not saved" if self.store.unsaved else "")

    def _build_color_chips(self):
        """One toggle per note color; with any on, only notes of those colors show."""
        chips = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        chips.add_css_class("color-chips")
        self.color_chips = {}
        for color in COLORS:
            chip = Gtk.ToggleButton(tooltip_text=f"Only {label(color).lower()} notes")
            chip.add_css_class("swatch")
            chip.add_css_class(color)
            chip.update_property([Gtk.AccessibleProperty.LABEL], [f"{label(color)} notes"])
            chip.connect("toggled", self._on_chip_toggled, color)
            chips.append(chip)
            self.color_chips[color] = chip
        return chips

    def _on_chip_toggled(self, chip, color):
        if chip.get_active():
            self._colors.add(color)
        else:
            self._colors.discard(color)
        self._rebuild()

    def _on_search_mode(self, bar, _pspec):
        if not bar.get_search_mode():
            self.search.set_text("")
            for chip in self.color_chips.values():
                chip.set_active(False)

    def _focus_search(self, *_):
        self.search_bar.set_search_mode(not self.search_bar.get_search_mode())
        return True

    # --- syncing rows with the store ------------------------------------------------
    def _schedule_rebuild(self):
        if not self._closed and not self._rebuild_source:
            self._rebuild_source = GLib.timeout_add(REBUILD_DELAY_MS, self._on_rebuild_timeout)

    def _on_rebuild_timeout(self):
        self._rebuild_source = 0
        if not self._closed:
            self._rebuild()
        return GLib.SOURCE_REMOVE

    def _rebuild(self):
        if self._closed:
            return
        if self.menu_popover is not None:
            self._schedule_rebuild()  # try again once the menu closes
            return
        self._terms = self.search.get_text().lower().split()
        editing_id = self._editing.note_id if self._editing is not None else None
        # Empty notes stay hidden, except a new one that is being written.
        wanted = {n.id: n for n in self.store.sorted() if not n.is_empty() or n.id == editing_id}
        for note_id in [i for i in self._rows if i not in wanted]:
            self._remove_row(note_id)
        for note in wanted.values():
            row = self._rows.get(note.id)
            if row is None:
                self._add_row(note)
            elif row is not self._editing:
                self._refresh_row(row, note)
        self.list.invalidate_sort()
        self.list.invalidate_filter()
        self._update_stack()

    def _update_stack(self):
        if not self._rows:
            self.stack.set_visible_child_name("empty")
        elif not any(self._filter_row(row) for row in self._rows.values()):
            self.stack.set_visible_child_name("no-results")
        else:
            self.stack.set_visible_child_name("list")

    def _sort_rows(self, a, b):
        return (b.sort_key > a.sort_key) - (b.sort_key < a.sort_key)  # pinned, then newest

    def _filter_row(self, row):
        if row is self._editing or not (self._terms or self._colors):
            return True
        note = self.store.get(row.note_id)
        if note is None or (self._colors and note.color not in self._colors):
            return False
        return self.store.matches(note, self._terms)  # every word, anywhere in the note

    # --- cards -----------------------------------------------------------------------
    def _add_row(self, note):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("note-card")
        top = Gtk.Box(spacing=4)
        pin = Gtk.Image(icon_name="view-pin-symbolic", tooltip_text="Pinned", visible=False)
        pin.add_css_class("card-chip")
        top.append(pin)
        progress = self._chip("checkbox-checked-symbolic")
        top.append(progress)
        stamp = Gtk.Label(xalign=1, hexpand=True)
        stamp.add_css_class("caption")
        stamp.add_css_class("dim-label")
        top.append(stamp)
        more = Gtk.Button(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Note menu")
        more.add_css_class("flat")
        more.add_css_class("card-button")
        top.append(more)
        popout = Gtk.Button(icon_name="window-new-symbolic", valign=Gtk.Align.CENTER)
        popout.add_css_class("flat")
        popout.add_css_class("card-button")
        top.append(popout)
        card.append(top)

        # The card itself grows; only its contents crossfade from preview to editor.
        previews = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                             transition_duration=EXPAND_MS, interpolate_size=True,
                             vhomogeneous=False)
        compact = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        previews.add_named(compact, "compact")
        card.append(previews)

        row = Gtk.ListBoxRow(child=card, activatable=True)
        row.note_id = note.id
        row.card, row.stamp, row.popout = card, stamp, popout
        row.pin, row.progress = pin, progress
        row.previews, row.compact = previews, compact
        row.color = None
        row.shown = None  # what the card last showed, to skip unchanged refreshes
        row.editor = None
        popout.connect("clicked", lambda _b: self.pop_out(row.note_id))
        more.connect("clicked", lambda b: self._open_menu(row, b.compute_bounds(row)[1]))
        row.update_state([Gtk.AccessibleState.EXPANDED], [0])
        self._refresh_row(row, note)
        self._rows[note.id] = row
        self.list.append(row)
        return row

    @staticmethod
    def _chip(icon):
        """Small icon + caption on a card's top line, hidden until it has text."""
        box = Gtk.Box(spacing=3, visible=False)
        box.add_css_class("card-chip")
        box.append(Gtk.Image(icon_name=icon))
        box.text = Gtk.Label()
        box.text.add_css_class("caption")
        box.append(box.text)
        return box

    @staticmethod
    def _set_chip(chip, text, tooltip=None):
        chip.set_visible(bool(text))
        chip.text.set_label(text or "")
        chip.set_tooltip_text(tooltip)

    def _refresh_row(self, row, note):
        shown = (note.modified, note.color, note.open, row.editor is not None, note.pinned)
        if row.shown == shown:
            return
        row.shown = shown
        row.sort_key = (note.pinned, note.modified)
        row.pin.set_visible(note.pinned)
        done, total = checklist_progress(note.plain_text())
        self._set_chip(row.progress, f"{done}/{total}" if total else None,
                       f"{done} of {total} ticked" if total else None)
        if row.color != note.color:
            if row.color:
                row.card.remove_css_class(row.color)
            row.card.add_css_class(note.color)
            row.color = note.color
        row.stamp.set_label(format_time(note.modified))
        # A popped-out note keeps its card; the button then brings its window forward.
        row.popout.set_tooltip_text("Show note window" if note.open else "Pop out")
        if note.open:
            row.add_css_class("popped-out")
        else:
            row.remove_css_class("popped-out")

        while (child := row.compact.get_first_child()) is not None:
            row.compact.remove(child)
        images = note.images()
        if images:
            try:
                texture = Gdk.Texture.new_from_filename(str(self.store.image_path(images[0])))
            except GLib.Error:
                texture = None
            if texture is not None:
                pic = Gtk.Picture(paintable=texture, content_fit=Gtk.ContentFit.COVER)
                pic.set_size_request(-1, COMPACT_IMAGE_HEIGHT)
                pic.add_css_class("card-image")
                row.compact.append(pic)
        paragraphs = [line for line in note.plain_text().replace("￼", "").splitlines()
                      if line.strip()]
        if paragraphs:
            # A label's line limit applies per paragraph, so run them together.
            row.compact.append(Gtk.Label(label=" · ".join(paragraphs), xalign=0, wrap=True,
                                         lines=COMPACT_LINES, ellipsize=Pango.EllipsizeMode.END,
                                         wrap_mode=Pango.WrapMode.WORD_CHAR))
        text = "\n".join(paragraphs)
        row.update_property([Gtk.AccessibleProperty.LABEL], [text[:80] or "Image note"])

    def _remove_row(self, note_id):
        row = self._rows.pop(note_id, None)
        if row is None:
            return
        if row is self._editing:  # deleted elsewhere while open here
            self._editing = None
            row.editor.shutdown()
            row.editor = None
        self.list.remove(row)

    # --- inline editing ---------------------------------------------------------------
    def edit_note(self, note_id):
        """Open a note in its card, collapsing any other, and focus the text."""
        note = self.store.get(note_id)
        if note is None or self._closed:
            return
        if note.open:
            self.app.open_note(note_id)  # popped out: bring its window forward
            return
        row = self._editing
        if row is None or row.note_id != note_id:
            self._collapse(discard_empty=True)
            row = self._rows.get(note_id)
            if row is None:
                row = self._add_row(note)
            self._expand(row, note)
            self.list.invalidate_filter()
            self._update_stack()
        row.editor.view.grab_focus()

    def _expand(self, row, note):
        editor = NoteEditor(self.store, note, on_error=self._show_error,
                            on_screenshot_added=self.present,
                            on_screenshot_discarded=lambda: self._discard_if_editing(row))
        editor.view.set_left_margin(0)
        editor.view.set_right_margin(0)
        editor.view.set_top_margin(2)
        editor.view.set_bottom_margin(6)
        editor.view.set_size_request(-1, EDITOR_MIN_HEIGHT)
        editor.view.set_vexpand(False)
        editor.format_bar.set_margin_start(0)
        editor.format_bar.set_margin_end(0)
        editor.format_bar.set_margin_bottom(0)
        delete = Gio.SimpleAction.new("delete", None)
        delete.connect("activate", lambda *_: self._delete_editing(row))
        editor.actions.add_action(delete)
        # Formatting buttons and shortcuts (win.bold, ...) resolve here before the window.
        row.insert_action_group("win", editor.actions)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(editor.view)
        page.append(editor.format_bar)
        escape = Gtk.EventControllerKey()
        escape.connect("key-pressed", self._on_editor_key, row)
        page.add_controller(escape)
        row.previews.add_named(page, "editor")
        row.previews.set_visible_child(page)
        row.editor, row.editor_page = editor, page
        row.add_css_class("expanded")
        row.update_state([Gtk.AccessibleState.EXPANDED], [1])
        self._editing = row

    def _collapse(self, discard_empty):
        """Close the inline editor, saving it; an empty note is dropped if asked."""
        row = self._editing
        if row is None:
            return
        self._editing = None
        editor, page = row.editor, row.editor_page
        row.editor = row.editor_page = None
        editor.flush()
        editor.shutdown()
        row.insert_action_group("win", None)
        row.remove_css_class("expanded")
        row.update_state([Gtk.AccessibleState.EXPANDED], [0])
        note = self.store.get(row.note_id)
        if note is None or (discard_empty and note.is_empty()):
            self._remove_row(row.note_id)
            if note is not None:
                self.store.delete(note.id)
            self._update_stack()
            return
        self._refresh_row(row, note)
        row.previews.set_visible_child_name("compact")
        self._drop_after_transition(row.previews, page)
        self.list.invalidate_sort()
        self.list.invalidate_filter()
        self._update_stack()

    @staticmethod
    def _drop_after_transition(stack, page):
        if not stack.get_transition_running():
            stack.remove(page)
            return
        def finished(_stack, _pspec):
            if not stack.get_transition_running():
                stack.disconnect(handler)
                if page.get_parent() is stack:
                    stack.remove(page)
        handler = stack.connect("notify::transition-running", finished)

    def _on_editor_key(self, _controller, keyval, _keycode, _state, row):
        if keyval == Gdk.KEY_Escape and row is self._editing:
            self._collapse(discard_empty=True)
            if row.get_parent() is not None:
                row.grab_focus()
            return True
        return False

    def _discard_if_editing(self, row):
        if row is self._editing:
            self._collapse(discard_empty=True)

    def _delete_editing(self, row):
        if row is not self._editing:
            return
        if row.editor.is_blank():
            self._collapse(discard_empty=True)
        else:
            self.app.delete_note(row.note_id)

    def _show_error(self, text):
        self.toasts.add_toast(Adw.Toast(title=text))

    def add_toast(self, toast):
        self.toasts.add_toast(toast)

    def capture_selection(self, note_id):
        """Fill the note being written with the text selected in another app."""
        state = {"handler": 0, "source": 0}

        def read(*_):
            if state["handler"]:
                self.disconnect(state["handler"])
                state["handler"] = 0
            if state["source"]:
                GLib.source_remove(state["source"])
                state["source"] = 0
            if not self._closed:
                read_selection(self.get_display().get_primary_clipboard(),
                               lambda text, source: self._insert_capture(note_id, text, source))
            return GLib.SOURCE_REMOVE

        def became_active(*_):
            if self.is_active():
                read()  # the selection offer arrives with keyboard focus
        if self.is_active():
            GLib.timeout_add(CAPTURE_SETTLE_MS, read)
            return
        state["handler"] = self.connect("notify::is-active", became_active)
        state["source"] = GLib.timeout_add(CAPTURE_WAIT_MS, read)

    def _insert_capture(self, note_id, text, source):
        row = self._editing
        if self._closed or row is None or row.note_id != note_id:
            return
        if text is None:
            self._show_error("No selected text to capture")
            return
        row.editor.insert_text(capture_text(text, source))

    def release_note(self, note_id):
        """Save and close the inline editor for a note that is about to pop out."""
        if self._editing is not None and self._editing.note_id == note_id:
            self._collapse(discard_empty=False)

    def flush_editor(self):
        if self._editing is not None:
            self._editing.editor.flush()

    def take_screenshot(self, note_id):
        """Screenshot into a note open here; a cancelled capture drops it if still empty."""
        if self._editing is not None and self._editing.note_id == note_id:
            self._editing.editor.take_screenshot(discard_if_empty=True)

    def pop_out(self, note_id):
        self.app.open_note(note_id)

    # --- activation and context menu ---------------------------------------------------
    def _on_right_click(self, _gesture, _n, x, y):
        row = self.list.get_row_at_y(int(y))
        if row is None:
            return
        ok, point = self.list.compute_point(row, Graphene.Point().init(x, y))
        if ok:
            self._open_menu(row, Graphene.Rect().init(point.x, point.y, 1, 1))

    def _open_menu(self, row, bounds):
        """The note menu (colors, pin, pop out, delete) at bounds in row."""
        note = self.store.get(row.note_id)
        if note is None or bounds is None:
            return
        popover = note_menu.popover(
            note, (("Pop Out", "app.open-note"), ("Delete Note", "app.delete-note")),
            lambda color: self.app.set_note_color(note.id, color))
        rect = Gdk.Rectangle()
        rect.x, rect.y = int(bounds.get_x()), int(bounds.get_y())
        rect.width, rect.height = max(1, int(bounds.get_width())), max(1, int(bounds.get_height()))
        popover.set_pointing_to(rect)
        popover.set_parent(row)
        popover.connect("closed", self._on_menu_closed)
        self.menu_popover = popover
        popover.popup()

    def _on_menu_closed(self, popover):
        if self._closed:
            return
        # Unparent after the chosen action has run.
        def drop():
            self._menu_drop_source = 0
            if popover.get_parent() is not None:
                popover.unparent()
            if self.menu_popover is popover:
                self.menu_popover = None
            return GLib.SOURCE_REMOVE
        self._menu_drop_source = GLib.idle_add(drop)

    def _on_row_activated(self, _list, row):
        self.edit_note(row.note_id)
