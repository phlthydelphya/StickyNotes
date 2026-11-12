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
