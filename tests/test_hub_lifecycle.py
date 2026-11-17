# SPDX-License-Identifier: GPL-3.0-or-later
"""Real GTK lifecycle regressions; run under an isolated display and session bus."""
import uuid

import pytest

from stickynotes.app import Adw, Gdk, Gio, GLib
from stickynotes.hub_window import HubWindow, REBUILD_DELAY_MS
from stickynotes.store import NoteStore


def advance(milliseconds=20):
    loop = GLib.MainLoop()
    def stop():
        loop.quit()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(milliseconds, stop)
    loop.run()


class CountingHub(HubWindow):
    def __init__(self, app):
        self.rebuilds = 0
        self.subtitle_updates = 0
        super().__init__(app)

    def _rebuild(self):
        self.rebuilds += 1
        return super()._rebuild()

    def _update_save_state(self):
        self.subtitle_updates += 1
        return super()._update_save_state()


@pytest.fixture
def app(tmp_path):
    if Gdk.Display.get_default() is None:
        pytest.skip('needs a display; run under xvfb-run')
    application = Adw.Application(application_id="org.duskos.StickyNotes.Test" + uuid.uuid4().hex,
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
    application.register(None)
    application.store = NoteStore(tmp_path)
    application.store.load()
    note = application.store.create()
    note.content = [{'t': 'Visible note'}]
    application.store.save_now()
    yield application
    for window in application.get_windows():
        window.close()
    application.store.save_now()
    advance()


def open_hub(app):
    hub = CountingHub(app)
    hub.present()
    advance()
    return hub


def test_disconnect_is_idempotent(tmp_path):
    store = NoteStore(tmp_path)
    calls = []
    note_id = store.connect(lambda _: calls.append('note'))
    save_id = store.connect_save_state(lambda: calls.append('save'))
    assert note_id != save_id
    store.disconnect(note_id)
    store.disconnect(note_id)
    store.disconnect(-1)
    store._notify('x')
    store._save_state(False)
    assert calls == ['save']
    store.disconnect(save_id)
    store.disconnect(save_id)
    store._save_state(False)
    assert calls == ['save']


def test_closed_hub_has_no_callbacks_or_pending_rebuild(app):
    hub = open_hub(app)
    note = app.store.sorted()[0]
    app.store.update(note)
    assert hub._rebuild_source
    hub.close()
    assert hub._closed
    before = hub.rebuilds, hub.subtitle_updates
    app.store.update(note)
    app.store.save_now()
    advance(REBUILD_DELAY_MS * 2)
    assert (hub.rebuilds, hub.subtitle_updates) == before
    assert hub._rebuild_source == 0
    assert not app.store._listeners and not app.store._save_listeners


def test_open_menu_reschedule_stops_when_hub_closes(app):
    hub = open_hub(app)
    hub._on_right_click(None, 1, 1, 1)
    assert hub.menu_popover is not None
    hub._schedule_rebuild()
    # Exercise the self-rescheduling path before closing, not just a fresh timer.
    before = hub.rebuilds
    advance(REBUILD_DELAY_MS + 40)
    assert hub.rebuilds > before
    assert hub.menu_popover is not None and hub._rebuild_source
    hub.close()
    before = hub.rebuilds, hub.subtitle_updates
    app.store.update(app.store.sorted()[0])
    app.store.save_now()
    advance(REBUILD_DELAY_MS * 2)
    assert (hub.rebuilds, hub.subtitle_updates) == before
    assert hub._rebuild_source == 0 and hub._menu_drop_source == 0
    assert hub.menu_popover is None


def test_pending_menu_cleanup_cancelled_on_close(app):
    hub = open_hub(app)
    hub._on_right_click(None, 1, 1, 1)
    popover = hub.menu_popover
    hub._on_menu_closed(popover)
    assert hub._menu_drop_source
    hub.close()
    advance(REBUILD_DELAY_MS * 2)
    assert hub._menu_drop_source == 0
    assert popover.get_parent() is None


def rows(hub):
    found, index = [], 0
    while (row := hub.list.get_row_at_index(index)) is not None:
        found.append(row)
        index += 1
    return found


def editing(hub):
    return [row.note_id for row in rows(hub) if row.previews.get_visible_child_name() == "editor"]


def add_note(app, text):
    note = app.store.create()
    note.content = [{'t': text}]
    app.store.update(note)
    return note


def test_clicked_card_opens_in_place_and_survives_autosave(app):
    add_note(app, 'Second note')
    hub = open_hub(app)
    first_row, second_row = rows(hub)
    assert editing(hub) == []  # the row with initial focus stays compact

    # Activating a card opens its editor in place and collapses any other.
    hub.list.emit("row-activated", first_row)
    assert editing(hub) == [first_row.note_id]
    hub.list.emit("row-activated", second_row)
    assert editing(hub) == [second_row.note_id] and first_row.editor is None
    editor = second_row.editor
    assert editor.view.has_focus()

    # Typing autosaves; the store change does not rebuild the card being edited.
    editor.view.get_buffer().insert_at_cursor(" edited", -1)
    advance(400 + REBUILD_DELAY_MS * 2)
    assert app.store.get(second_row.note_id).plain_text().endswith(" edited")
    assert rows(hub)[0] in (first_row, second_row) and second_row.editor is editor
    assert hub.rebuilds > 1

    # A search keeps the open card visible; Escape closes the editor.
    hub.search.set_text("no such text")
    hub._rebuild()
    assert [r.note_id for r in rows(hub) if r.get_child_visible()] == [second_row.note_id]
    hub.search.set_text("")
    hub._rebuild()
    hub._on_editor_key(None, Gdk.KEY_Escape, 0, 0, second_row)
    assert hub._editing is None and second_row.editor is None
    hub.close()


def test_new_empty_note_is_listed_while_written_and_dropped_if_left_empty(app):
    hub = open_hub(app)
    before = len(rows(hub))
    note = app.store.create()
    hub.edit_note(note.id)
    assert len(rows(hub)) == before + 1 and editing(hub) == [note.id]
    advance(REBUILD_DELAY_MS * 2)  # the create notification rebuilds; the row stays
    assert editing(hub) == [note.id]
    hub.list.emit("row-activated", next(r for r in rows(hub) if r.note_id != note.id))
    assert app.store.get(note.id) is None
    assert note.id not in [r.note_id for r in rows(hub)]
    hub.close()


def test_pop_out_saves_and_closes_the_inline_editor(app):
    hub = open_hub(app)
    popped = []
    def open_note(note_id):  # what StickyApp.open_note does before making the window
        hub.release_note(note_id)
        popped.append(note_id)
    app.open_note = open_note
    row = rows(hub)[0]
    hub.edit_note(row.note_id)
    row.editor.view.get_buffer().insert_at_cursor("popped ", -1)
    row.popout.emit("clicked")
    assert popped == [row.note_id] and hub._editing is None
    assert app.store.get(row.note_id).plain_text().endswith("popped ")

    # While it floats, activating its card brings the window forward instead.
    note = app.store.get(row.note_id)
    note.open = True
    app.store.update(note, bump=False)
    advance(REBUILD_DELAY_MS * 2)
    assert row.has_css_class("popped-out")
    hub.list.emit("row-activated", row)
    assert popped == [row.note_id] * 2 and hub._editing is None
    hub.close()


def test_closing_hub_saves_the_open_editor(app):
    hub = open_hub(app)
    row = rows(hub)[0]
    hub.edit_note(row.note_id)
    row.editor.view.get_buffer().insert_at_cursor("saved on close ", -1)
    hub.close()
    assert app.store.get(row.note_id).plain_text().endswith("saved on close ")


def test_twenty_reopens_have_one_subscription_each(app):
    closed = []
    for _ in range(20):
        hub = open_hub(app)
        assert len(app.store._listeners) == len(app.store._save_listeners) == 1
        hub.close()
        closed.append((hub, hub.rebuilds, hub.subtitle_updates))
        assert len(app.store._listeners) == len(app.store._save_listeners) == 0
    current = open_hub(app)
    app.store.update(app.store.sorted()[0])
    app.store.save_now()
    advance(REBUILD_DELAY_MS * 2)
    assert all((hub.rebuilds, hub.subtitle_updates) == (r, s) for hub, r, s in closed)
    assert len(app.store._listeners) == len(app.store._save_listeners) == 1
    print('After 20 reopen cycles: 1 note-change + 1 save-state listener')
    current.close()
