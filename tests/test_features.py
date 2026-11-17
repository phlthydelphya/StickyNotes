# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin, undo delete, typing shortcuts, checklists and links."""
import json
import os
import uuid
from types import SimpleNamespace

import pytest

from stickynotes.app import Adw, Gdk, Gio, GLib, Gtk, StickyApp
from stickynotes.hub_window import HubWindow, REBUILD_DELAY_MS
from stickynotes.richtext import RichEditor, checklist_progress, link_target
from stickynotes.store import NoteStore, validate

needs_display = pytest.mark.skipif(Gdk.Display.get_default() is None,
                                   reason="needs a display; run under xvfb-run")


def advance(milliseconds=20):
    loop = GLib.MainLoop()
    GLib.timeout_add(milliseconds, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()


def run_until(condition, timeout_ms=5000):
    waited = 0
    while not condition() and waited < timeout_ms:
        advance(20)
        waited += 20
    return condition()


def note_with(store, text, **fields):
    note = store.create()
    note.content = [{"t": text}]
    for key, value in fields.items():
        setattr(note, key, value)
    store.update(note)
    return note


# --- store ---------------------------------------------------------------------------
def test_pin_round_trip_and_validate(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    old = note_with(store, "old", pinned=True)
    new = note_with(store, "new")
    store.save_now()
    data = json.loads((tmp_path / "notes.json").read_text())
    saved = {n["id"]: n for n in data["notes"]}
    assert saved[old.id]["pinned"] is True and "pinned" not in saved[new.id]
    again = NoteStore(tmp_path)
    again.load()
    assert [n.id for n in again.sorted()] == [old.id, new.id]  # pinned first
    for bad in ({"pinned": "yes"}, {"pinned": 1}):
        with pytest.raises(ValueError):
            validate({"version": 1, "notes": [dict({"id": "x", "content": []}, **bad)]})


def test_deleted_note_keeps_images_until_undo_window_ends(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    note = store.create()
    note.content = [{"t": "pic"}, {"img": "a.png"}]
    store.image_path("a.png").write_bytes(b"x")
    assert store.delete(note.id, keep_images=True) is note
    assert store.get(note.id) is None and store.image_path("a.png").exists()
    assert note.id not in json.loads((tmp_path / "notes.json").read_text())["notes"]
    store.restore(note)
    store.save_now()
    assert store.get(note.id) is note
    store.drop_images(note)  # still a note: kept
    assert store.image_path("a.png").exists()
    store.delete(note.id, keep_images=True)
    store.drop_images(note)
    assert not store.image_path("a.png").exists()


def test_search_matches_every_word(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    note = store.create()
    note.content = [{"t": "Groceries list"}]
    assert store.matches(note, ["groceries"]) and store.matches(note, ["list", "groceries"])
    assert not store.matches(note, ["list", "missing"])


# --- rich text ---------------------------------------------------------------------------
def editor(text, segs=None):
    ed = RichEditor(Gtk.TextView(), lambda name: os.path.join("/nonexistent", name))
    ed.load(segs or [{"t": text, "s": []}])
    return ed


def text_of(ed):
    return ed.buf.get_text(ed.buf.get_start_iter(), ed.buf.get_end_iter(), False)


def type_text(ed, text):
    for char in text:
        ed.buf.insert_interactive_at_cursor(char, -1, True)


@needs_display
def test_typed_markers_start_lists():
    for typed, expected in (("- a", "• a"), ("* a", "• a"), ("[] a", "☐ a"),
                            ("[ ] a", "☐ a"), ("[x] a", "☑ a"), ("1. a", "1. a")):
        ed = editor("")
        type_text(ed, typed)
        assert text_of(ed) == expected, typed
        assert ed.list_style() is not None
    ed = editor("x")
    type_text(ed, " - b")  # not at a line start: stays text
    assert text_of(ed) == "x - b"
    ed = editor("")
    type_text(ed, "- a\n")  # Enter is the editor's key handler, not tested here
    assert text_of(ed).startswith("• a")


@needs_display
def test_ticked_items_sink_and_unticked_rise():
    ed = editor("", [{"t": "Shop\n☐ ", "s": []}, {"t": "milk", "s": ["bold"]},
                     {"t": "\n☐ eggs\n☐ tea\nafter", "s": []}])
    ed.toggle_check(1)
    assert text_of(ed) == "Shop\n☐ eggs\n☐ tea\n☑ milk\nafter"
    assert {"t": "milk", "s": ["bold"]} in ed.dump()  # formatting moves with it
    ed.toggle_check(1)
    assert text_of(ed) == "Shop\n☐ tea\n☑ eggs\n☑ milk\nafter"
    ed.toggle_check(3)  # untick milk: back above the ticked items
    assert text_of(ed) == "Shop\n☐ tea\n☐ milk\n☑ eggs\nafter"
    ed = editor("☐ a\n☐ b")  # checklist at the end of the note
    ed.toggle_check(0)
    assert text_of(ed) == "☐ b\n☑ a"
    ed.toggle_check(1)
    assert text_of(ed) == "☐ b\n☐ a"  # no ticked item above it: stays
    assert checklist_progress("☐ a\n☑ b\n☑ c\nplain") == (2, 3)
    assert checklist_progress("no list") == (0, 0)


@needs_display
def test_links_are_found_and_ctrl_click_targets():
    ed = editor("see https://gnome.org, mail me@example.com or open ~/notes.txt and/or 3/4")
    found = []
    it = ed.buf.get_start_iter()
    while True:
        link = ed.link_at(it)
        if link and link not in found:
            found.append(link)
        if not it.forward_char():
            break
    assert found == ["https://gnome.org", "me@example.com", "~/notes.txt"]
    assert ed.dump() == [{"t": text_of(ed), "s": []}]  # links are never saved
    assert link_target("me@example.com") == "mailto:me@example.com"
    assert link_target("www.gnome.org") == "https://www.gnome.org"
    assert link_target("~/a") == "~/a"
    type_text(ed, " www.example.org")
    advance()  # links are marked on idle after a change
    end = ed.buf.get_end_iter()
    end.backward_char()
    assert ed.link_at(end) == "www.example.org"


# --- the notes list ----------------------------------------------------------------------------
@pytest.fixture
def hub_app(tmp_path):
    if Gdk.Display.get_default() is None:
        pytest.skip("needs a display; run under xvfb-run")
    application = Adw.Application(application_id="org.duskos.StickyNotes.Test" + uuid.uuid4().hex,
                                  flags=Gio.ApplicationFlags.NON_UNIQUE)
    application.register(None)
    application.store = NoteStore(tmp_path)
    application.store.load()
    yield application
    for window in application.get_windows():
        window.close()
    advance()


def rows(hub):
    found, index = [], 0
    while (row := hub.list.get_row_at_index(index)) is not None:
        found.append(row)
        index += 1
    return found


def visible_ids(hub):
    return [row.note_id for row in rows(hub) if row.get_child_visible()]


def test_pinned_first_color_chips_and_card_chips(hub_app):
    store = hub_app.store
    old = note_with(store, "old pinned\n☐ a\n☑ b", color="blue")
    new = note_with(store, "newer note", color="pink")
    hub = HubWindow(hub_app)
    hub.present()
    advance()
    assert [r.note_id for r in rows(hub)] == [new.id, old.id]
    old.pinned = True
    store.update(old, bump=False)
    advance(REBUILD_DELAY_MS * 2)
    first = rows(hub)[0]
    assert first.note_id == old.id and first.pin.get_visible()
    assert first.progress.get_visible() and first.progress.text.get_label() == "1/2"
    assert not rows(hub)[1].pin.get_visible() and not rows(hub)[1].progress.get_visible()

    hub.search_bar.set_search_mode(True)
    hub.color_chips["pink"].set_active(True)
    assert visible_ids(hub) == [new.id]
    hub.color_chips["blue"].set_active(True)
    assert sorted(visible_ids(hub)) == sorted([new.id, old.id])
    hub.search.set_text("note newer")  # every word, any order
    hub._rebuild()
    assert visible_ids(hub) == [new.id]
    hub.search_bar.set_search_mode(False)  # closing clears words and chips
    hub._rebuild()
    assert sorted(visible_ids(hub)) == sorted([new.id, old.id])
    assert not any(chip.get_active() for chip in hub.color_chips.values())
    hub.close()


def test_card_menu_has_colors_and_pin(hub_app):
    note = note_with(hub_app.store, "menu note")
    hub = HubWindow(hub_app)
    hub.present()
    advance()
    row = rows(hub)[0]
    hub._open_menu(row, row.compute_bounds(row)[1])
    menu = hub.menu_popover
    model = menu.get_menu_model()
    labels = []
    for section in range(model.get_n_items()):
        link = model.get_item_link(section, "section")
        for i in range(link.get_n_items()):
            labels.append(link.get_item_attribute_value(i, "label", None))
    assert labels[1:] == [GLib.Variant("s", "Pin to Top"), GLib.Variant("s", "Pop Out"),
                          GLib.Variant("s", "Delete Note")]
    menu.popdown()
    advance()
    hub.close()
    assert note.id


def test_undo_brings_a_deleted_note_back(hub_app, monkeypatch):
    store = hub_app.store
    note = note_with(store, "keep me")
    hub = HubWindow(hub_app)
    hub.present()
    advance()
    toasts = []
    monkeypatch.setattr(hub, "add_toast", toasts.append)
    obj = SimpleNamespace(store=store, note_windows={}, hub=hub, _pending_deletes={},
                          show_list=lambda: None, opened=[])
    obj.open_note = obj.opened.append
    for name in ("delete_note", "undo_delete", "_forget_delete"):
        setattr(obj, name, lambda *a, name=name: getattr(StickyApp, name)(obj, *a))
    hub.edit_note(note.id)
    rows(hub)[0].editor.view.get_buffer().insert_at_cursor(" edited", -1)
    obj.delete_note(note.id)
    assert store.get(note.id) is None and hub._editing is None and len(toasts) == 1
    advance(REBUILD_DELAY_MS * 2)
    assert rows(hub) == []
    toasts[0].emit("button-clicked")
    toasts[0].emit("dismissed")
    assert store.get(note.id) is note and note.plain_text() == "keep me edited"
    advance(REBUILD_DELAY_MS * 2)
    assert [r.note_id for r in rows(hub)] == [note.id]
    assert obj._pending_deletes == {} and obj.opened == []

    empty = store.create()  # nothing to bring back: no toast
    obj.delete_note(empty.id)
    assert store.get(empty.id) is None and len(toasts) == 1
    hub.close()
