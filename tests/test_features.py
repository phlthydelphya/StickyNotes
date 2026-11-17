# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin, undo delete, reminders, typing shortcuts, checklists, links, image text,
desktop search and selection capture."""
import datetime
import json
import os
import stat
import uuid
from types import SimpleNamespace

import pytest

from stickynotes.app import Adw, Gdk, Gio, GLib, Gtk, StickyApp
from stickynotes import reminders, search_provider, selection
from stickynotes.hub_window import HubWindow, REBUILD_DELAY_MS
from stickynotes.note_editor import NoteEditor
from stickynotes.ocr import OcrIndex, tidy
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
def test_pin_and_reminder_round_trip_and_validate(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    old = note_with(store, "old", pinned=True, remind_at=1900000000.5)
    new = note_with(store, "new")
    store.save_now()
    data = json.loads((tmp_path / "notes.json").read_text())
    saved = {n["id"]: n for n in data["notes"]}
    assert saved[old.id]["pinned"] is True and saved[old.id]["remind_at"] == 1900000000.5
    assert "pinned" not in saved[new.id] and "remind_at" not in saved[new.id]
    again = NoteStore(tmp_path)
    again.load()
    assert [n.id for n in again.sorted()] == [old.id, new.id]  # pinned first
    assert again.get(old.id).remind_at == 1900000000.5
    for bad in ({"pinned": "yes"}, {"remind_at": "soon"}, {"remind_at": float("inf")}):
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


def test_search_matches_every_word_in_text_or_images(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    note = store.create()
    note.content = [{"t": "Groceries list"}, {"img": "shot.png"}]
    store.image_text["shot.png"] = "INVOICE 42 total"
    assert store.matches(note, ["groceries"]) and store.matches(note, ["invoice", "list"])
    assert not store.matches(note, ["invoice", "missing"])


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


# --- reminders ---------------------------------------------------------------------------
def test_reminder_presets_and_labels():
    monday_noon = datetime.datetime(2026, 11, 16, 12, 0)  # a Monday
    keys = [key for key, _text, _ts in reminders.presets(monday_noon)]
    assert keys == ["hour", "evening", "tomorrow", "week"]
    late = datetime.datetime(2026, 11, 16, 17, 30)
    assert "evening" not in [key for key, *_ in reminders.presets(late)]
    week = reminders.preset_time("week", monday_noon)
    assert datetime.datetime.fromtimestamp(week) == datetime.datetime(2026, 11, 23, 9, 0)
    tomorrow = reminders.preset_time("tomorrow", monday_noon)
    assert reminders.describe(tomorrow, monday_noon) == "Tomorrow 09:00"
    assert reminders.describe(monday_noon.timestamp() + 3600, monday_noon) == "13:00"
    assert reminders.describe(week, monday_noon) == "Mon 23 Nov 09:00"


def reminder_app(store):
    events = []
    obj = SimpleNamespace(store=store, _remind_source=0, events=events,
                          open_note=lambda nid: events.append(("open", nid)),
                          send_notification=lambda nid, n: events.append(("notify", nid, n)),
                          withdraw_notification=lambda nid: events.append(("withdraw", nid)),
                          _message=lambda text: events.append(("message", text)))
    for name in ("_schedule_reminders", "_on_reminder_timeout", "fire_reminder", "snooze",
                 "set_reminder", "reminder_done"):
        setattr(obj, name, lambda *a, name=name: getattr(StickyApp, name)(obj, *a))
    return obj


def test_due_reminder_pops_note_out_and_notifies(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    due = note_with(store, "Call the bank\nabout the card", remind_at=1.0)
    later = note_with(store, "later", remind_at=4102444800.0)
    obj = reminder_app(store)
    obj._on_reminder_timeout()
    assert ("open", due.id) in obj.events and due.remind_at is None
    notify = [e for e in obj.events if e[0] == "notify"]
    assert len(notify) == 1 and notify[0][1] == f"reminder-{due.id}"
    assert later.remind_at == 4102444800.0 and obj._remind_source  # next one waits
    obj.snooze(due.id)
    assert due.remind_at > 1.0
    obj.set_reminder(later.id, 5.0)  # in the past
    assert ("message", "That time has already passed") in obj.events
    assert later.remind_at == 4102444800.0
    GLib.source_remove(obj._remind_source)


# --- image text ------------------------------------------------------------------------------
def fake_tesseract(tmp_path, output, code=0):
    program = tmp_path / "tesseract"
    program.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\nexit {code}\n")
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    return str(program)


def test_image_text_is_read_once_cached_and_pruned(tmp_path):
    store = NoteStore(tmp_path / "data")
    store.load()
    note = store.create()
    note.content = [{"img": "a.png"}]
    store.image_path("a.png").write_bytes(b"x")
    cache = tmp_path / "cache" / "image-text.json"
    updates = []
    index = OcrIndex(store, cache, fake_tesseract(tmp_path, "INVOICE\n  42  "),
                     on_update=lambda: updates.append(1))
    index.scan()
    assert run_until(lambda: "a.png" in store.image_text)
    assert store.image_text["a.png"] == "INVOICE 42" and updates == [1]
    assert store.matches(note, ["invoice"])
    index.stop()  # writes the cache now
    assert json.loads(cache.read_text()) == {"a.png": "INVOICE 42"}
    store.delete(note.id)
    again = OcrIndex(store, cache, program="")
    assert not again.available and again.texts == {"a.png": "INVOICE 42"}
    again._save()
    assert json.loads(cache.read_text()) == {}  # the image is gone


def test_unreadable_image_is_not_retried(tmp_path):
    store = NoteStore(tmp_path / "data")
    store.load()
    note = store.create()
    note.content = [{"img": "a.png"}]
    index = OcrIndex(store, tmp_path / "c.json", fake_tesseract(tmp_path, "", code=1))
    index.scan()
    assert run_until(lambda: index._running is None and index._failed)
    assert index.pending() == [] and "a.png" not in store.image_text


def test_tidy_keeps_lines_for_a_note():
    assert tidy("\n\nTotal  \n\n\n\n42\n\f") == "Total\n\n42"
    assert tidy("\f\n  \n") == ""


def image_editor(tmp_path, program, errors):
    store = NoteStore(tmp_path / "data")
    store.load()
    note = store.create()
    note.content = [{"t": "Before\n"}, {"img": "a.png"}, {"t": "\nAfter"}]
    store.image_path("a.png").write_bytes(b"x")
    ed = NoteEditor(store, note, on_error=errors.append, on_screenshot_added=lambda: None,
                    on_screenshot_discarded=lambda: None)
    ed.ocr_program = program
    return ed


@needs_display
def test_insert_text_from_image_goes_below_it(tmp_path):
    errors = []
    ed = image_editor(tmp_path, fake_tesseract(tmp_path, "INVOICE\n\n\n42\n"), errors)
    ed._menu_anchor = next(iter(ed.rich.anchor_files))
    ed.actions.activate_action("image-text-insert", None)
    assert ed._menu_anchor is None
    assert run_until(lambda: "INVOICE" in text_of(ed.rich))
    assert errors == []
    ed.flush()
    assert ed.note.content == [{"t": "Before\n", "s": []}, {"img": "a.png"},
                               {"t": "\nINVOICE\n\n42\nAfter", "s": []}]


@needs_display
def test_image_text_reports_no_text_and_missing_tesseract(tmp_path):
    errors = []
    ed = image_editor(tmp_path, fake_tesseract(tmp_path, "  \n"), errors)
    ed._menu_anchor = next(iter(ed.rich.anchor_files))
    ed.actions.activate_action("image-text-copy", None)
    assert run_until(lambda: errors)
    assert errors == ["No text found in the image"]
    ed.ocr_program = None
    ed.take_screenshot_text()
    assert ed._screenshot is None and errors[-1] == "Install tesseract-ocr to read text in images"


@needs_display
def test_text_after_image_at_the_end_of_a_note():
    ed = editor("", [{"t": "x\n"}, {"img": "a.png"}])
    anchor = next(iter(ed.anchor_files))
    assert ed.insert_text_after_image(anchor, "hello")
    assert ed.dump() == [{"t": "x\n", "s": []}, {"img": "a.png"}, {"t": "\nhello", "s": []}]
    ed.anchor_files.clear()
    ed.buf.set_text("")
    assert anchor.get_deleted() and not ed.insert_text_after_image(anchor, "hello")


@needs_display
def test_screenshot_to_text_inserts_lines_not_the_picture(tmp_path):
    errors = []
    ed = image_editor(tmp_path, fake_tesseract(tmp_path, "line one\nline two\n"), errors)
    buf = ed.rich.buf
    buf.place_cursor(buf.get_end_iter())  # after "After"
    shot = tmp_path / "Screenshot.png"
    shot.write_bytes(b"x")
    ed._on_screenshot_text("ok", shot.as_uri())
    assert run_until(lambda: "line two" in text_of(ed.rich))
    assert text_of(ed.rich).endswith("After\nline one\nline two")
    assert len(ed.rich.anchor_files) == 1 and errors == []
    ed._on_screenshot_text("cancelled", None)
    ed._on_screenshot_text("failed", None)
    assert errors == ["Screenshot could not be taken"]


# --- desktop search ----------------------------------------------------------------------------
def test_search_provider_results_and_metas(tmp_path):
    store = NoteStore(tmp_path)
    store.load()
    shop = note_with(store, "Shopping\n☐ milk\n☐ bread")
    pic = store.create()
    pic.content = [{"img": "x.png"}]
    store.image_text["x.png"] = "boarding pass gate 12"
    store.update(pic)
    note_with(store, "unrelated")
    assert search_provider.find(store, ["MILK"]) == [shop.id]
    assert search_provider.find(store, ["gate", "pass"]) == [pic.id]
    assert search_provider.find(store, ["milk"], among=[pic.id]) == []
    assert search_provider.find(store, ["  "]) == []
    meta = search_provider.meta(store, shop, "icon")
    assert meta["name"].unpack() == "Shopping" and meta["description"].unpack() == "☐ milk · ☐ bread"
    meta = search_provider.meta(store, pic, "icon")
    assert meta["name"].unpack() == "Image note"
    assert meta["description"].unpack() == "boarding pass gate 12"


def test_search_provider_over_dbus(tmp_path):
    try:
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        pytest.skip("needs a session bus; run under dbus-run-session")
    store = NoteStore(tmp_path)
    store.load()
    note = note_with(store, "Dentist Tuesday")
    activated = []
    app = SimpleNamespace(store=store,
                          activate_search_result=lambda nid, ts: activated.append(nid),
                          launch_search=lambda terms, ts: activated.append(terms))
    flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
             | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
    server = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    client = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    provider = search_provider.SearchProvider(app, "org.duskos.StickyNotes")
    provider.register(server)
    replies = []

    def call(method, args, reply_type):
        client.call(server.get_unique_name(), search_provider.OBJECT_PATH, search_provider.IFACE,
                    method, args, GLib.VariantType(reply_type) if reply_type else None,
                    Gio.DBusCallFlags.NONE, 2000, None,
                    lambda c, r: replies.append(c.call_finish(r).unpack()))
        assert run_until(lambda: replies)
        return replies.pop()

    try:
        assert call("GetInitialResultSet", GLib.Variant("(as)", (["dentist"],)), "(as)") == ([note.id],)
        assert call("GetSubsearchResultSet", GLib.Variant("(asas)", ([note.id], ["tues"])),
                    "(as)") == ([note.id],)
        (metas,) = call("GetResultMetas", GLib.Variant("(as)", ([note.id, "gone"],)), "(aa{sv})")
        assert [m["name"] for m in metas] == ["Dentist Tuesday"]
        call("ActivateResult", GLib.Variant("(sasu)", (note.id, ["dentist"], 0)), None)
        call("LaunchSearch", GLib.Variant("(asu)", (["den"], 0)), None)
        assert activated == [note.id, ["den"]]
    finally:
        provider.unregister()
        server.close_sync(None)
        client.close_sync(None)


# --- selection capture -----------------------------------------------------------------------
def test_selection_source_and_capture_text():
    assert selection.source_url(b"https://example.org/page\n") == "https://example.org/page"
    utf16 = "https://example.org/a\nTitle".encode("utf-16-le")
    assert selection.source_url(utf16) == "https://example.org/a"
    assert selection.source_url(b"javascript:alert(1)") is None
    assert selection.source_url(b"") is None
    assert selection.capture_text("  quote  ", None) == "quote"
    assert selection.capture_text("quote", "https://x.org") == "quote\n\nFrom https://x.org"


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
    old.remind_at = 4102444800.0
    store.update(old, bump=False)
    advance(REBUILD_DELAY_MS * 2)
    first = rows(hub)[0]
    assert first.note_id == old.id and first.pin.get_visible()
    assert first.progress.get_visible() and first.progress.text.get_label() == "1/2"
    assert first.alarm.get_visible() and first.alarm.text.get_label()
    assert not rows(hub)[1].alarm.get_visible() and not rows(hub)[1].progress.get_visible()

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


def test_card_menu_has_colors_pin_and_reminders(hub_app):
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
    assert labels[1:] == [GLib.Variant("s", "Pin to Top"), GLib.Variant("s", "Remind Me"),
                          GLib.Variant("s", "Pop Out"), GLib.Variant("s", "Delete Note")]
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


def test_captured_selection_fills_the_new_note(hub_app):
    hub = HubWindow(hub_app)
    hub.present()
    advance()
    note = hub_app.store.create()
    hub.edit_note(note.id)
    hub._insert_capture(note.id, "  a quote  ", "https://example.org")
    hub._editing.editor.flush()
    assert note.plain_text() == "a quote\n\nFrom https://example.org"
    hub._insert_capture("someone-else", "ignored", None)
    hub._insert_capture(note.id, None, None)  # nothing selected: a message only
    hub._editing.editor.flush()
    assert note.plain_text() == "a quote\n\nFrom https://example.org"
    hub.close()
