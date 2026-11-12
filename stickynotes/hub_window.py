"""The notes list: search and browse every note."""

import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

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

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Sticky Notes"))
        header.pack_start(Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New note (Ctrl+N)",
                                     action_name="app.new-note"))

        self.search = Gtk.SearchEntry(placeholder_text="Search…", margin_start=12,
                                      margin_end=12, margin_top=6, margin_bottom=6)
        self.search.set_key_capture_widget(self)
        self.search.connect("search-changed", lambda *_: self._rebuild())

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
        body.append(self.search)
        self.stack.set_vexpand(True)
        body.append(self.stack)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(body)
        self.set_content(view)

        find = Gtk.ShortcutController()
        find.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                       action=Gtk.CallbackAction.new(self._focus_search)))
        self.add_controller(find)

        self.store.connect(lambda _id: self._schedule_rebuild())
        self._rebuild()

    def _focus_search(self, *_):
        self.search.grab_focus()
        return True

    def _schedule_rebuild(self):
        if not self._rebuild_source:
            self._rebuild_source = GLib.timeout_add(REBUILD_DELAY_MS, self._on_rebuild_timeout)

    def _on_rebuild_timeout(self):
        self._rebuild_source = 0
        self._rebuild()
        return GLib.SOURCE_REMOVE

    def _rebuild(self):
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
        px, py = self.list.translate_coordinates(row, x, y)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(px), int(py), 1, 1
        popover = Gtk.PopoverMenu(menu_model=menu, has_arrow=False, pointing_to=rect)
        popover.set_parent(row)
        popover.connect("closed", self._on_menu_closed)
        self.menu_popover = popover
        popover.popup()

    def _on_menu_closed(self, popover):
        # Unparent after the chosen action has run.
        def drop():
            popover.unparent()
            if self.menu_popover is popover:
                self.menu_popover = None
            return GLib.SOURCE_REMOVE
        GLib.idle_add(drop)

    def _on_row_activated(self, _list, row):
        self.app.open_note(row.note_id)
