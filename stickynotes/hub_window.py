# SPDX-License-Identifier: GPL-3.0-or-later
"""The notes list: search and browse every note."""

import datetime

import gi

gi.require_version("Graphene", "1.0")

from gi.repository import Graphene  # noqa: E402
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

REBUILD_DELAY_MS = 120
PREVIEW_LINES = 6


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
        self.search_bar = Gtk.SearchBar(child=self.search, show_close_button=False)
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

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("note-list")
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

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.search_bar)
        view.set_content(body)
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
        return False

    def _update_save_state(self):
        if self._closed:
            return
        self.window_title.set_subtitle("Not saved" if self.store.unsaved else "")

    def _on_search_mode(self, bar, _pspec):
        if not bar.get_search_mode():
            self.search.set_text("")

    def _focus_search(self, *_):
        self.search_bar.set_search_mode(not self.search_bar.get_search_mode())
        return True

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
        self.list.remove_all()
        query = self.search.get_text().strip().lower()
        notes = [n for n in self.store.sorted() if not n.is_empty()]
        shown = 0
        for note in notes:
            if query and query not in note.plain_text().lower():
                continue
            self.list.append(self._build_row(note))
            shown += 1
        if not notes:
            self.stack.set_visible_child_name("empty")
        elif not shown:
            self.stack.set_visible_child_name("no-results")
        else:
            self.stack.set_visible_child_name("list")

    def _build_row(self, note):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("note-card")
        card.add_css_class(note.color)
        stamp = Gtk.Label(label=format_time(note.modified), xalign=1)
        stamp.add_css_class("caption")
        stamp.add_css_class("dim-label")
        card.append(stamp)

        images = note.images()
        if images:
            pic = Gtk.Picture.new_for_filename(str(self.store.image_path(images[0])))
            pic.set_content_fit(Gtk.ContentFit.COVER)
            pic.set_size_request(-1, 96)
            pic.add_css_class("card-image")
            card.append(pic)
        text = "\n".join(line for line in note.plain_text().replace("￼", "").splitlines()
                         if line.strip())
        if text:
            preview = Gtk.Label(label=text, xalign=0, wrap=True, lines=PREVIEW_LINES,
                                ellipsize=Pango.EllipsizeMode.END,
                                wrap_mode=Pango.WrapMode.WORD_CHAR)
            card.append(preview)

        row = Gtk.ListBoxRow(child=card, activatable=True)
        row.note_id = note.id
        row.update_property([Gtk.AccessibleProperty.LABEL], [text[:80] or "Image note"])
        return row

    def _on_right_click(self, _gesture, _n, x, y):
        row = self.list.get_row_at_y(int(y))
        if row is None:
            return
        menu = Gio.Menu()
        for title, action in (("Open note", "app.open-note"), ("Delete note", "app.delete-note")):
            item = Gio.MenuItem.new(title, None)
            item.set_action_and_target_value(action, GLib.Variant("s", row.note_id))
            menu.append_item(item)
        ok, point = self.list.compute_point(row, Graphene.Point().init(x, y))
        if not ok:
            return
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(point.x), int(point.y), 1, 1
        popover = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, pointing_to=rect)
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
        self.app.open_note(row.note_id)
