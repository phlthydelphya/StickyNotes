# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from stickynotes.richtext import (RichEditor, deserialize, install_tags, line_prefix,  # noqa: E402
                                  serialize)
from stickynotes.palette import COLORS  # noqa: E402
from stickynotes.store import LoadState, Note, NoteStore  # noqa: E402


class StoreTest(unittest.TestCase):
    def test_roundtrip_and_delete_removes_images(self):
        with tempfile.TemporaryDirectory() as d:
            store = NoteStore(d)
            store.load()
            note = store.create("pink")
            name = store.new_image_name(".PNG")
            store.image_path(name).write_bytes(b"x")
            note.content = [{"t": "hi", "s": ["bold"]}, {"img": name}]
            store.save_now()

            again = NoteStore(d)
            again.load()
            loaded = again.get(note.id)
            self.assertEqual(loaded.color, "pink")
            self.assertEqual(loaded.content, note.content)
            again.delete(note.id)
            self.assertFalse(again.image_path(name).exists())

    def test_corrupt_file_is_kept_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "notes.json").write_text("{not json")
            store = NoteStore(d)
            store.load()
            self.assertEqual(store.notes, {})
            self.assertTrue(list(Path(d).glob("notes.json.corrupt-*")))

    def test_load_reports_failure_so_images_are_not_collected(self):
        with tempfile.TemporaryDirectory() as d:
            store = NoteStore(d)
            self.assertEqual(store.load(), LoadState.MISSING)  # missing file
            Path(d, "notes.json").write_text("{not json")
            self.assertEqual(NoteStore(d).load(), LoadState.CORRUPT)  # corrupt file
            store.save_now()
            self.assertEqual(NoteStore(d).load(), LoadState.OK)

    def test_delete_commits_before_removing_images(self):
        with tempfile.TemporaryDirectory() as d:
            store = NoteStore(d)
            store.load()
            note = store.create()
            note.content = [{"img": "a.png"}]
            store.image_path("a.png").write_bytes(b"x")
            store.delete(note.id)
            self.assertFalse(store.image_path("a.png").exists())
            again = NoteStore(d)
            again.load()
            self.assertIsNone(again.get(note.id))

    def test_gc_keeps_referenced_images(self):
        with tempfile.TemporaryDirectory() as d:
            store = NoteStore(d)
            store.load()
            note = store.create()
            note.content = [{"img": "keep.png"}]
            store.image_path("keep.png").write_bytes(b"x")
            store.image_path("orphan.png").write_bytes(b"x")
            store.save_now()
            store.load()
            store.gc_images()
            self.assertTrue(store.image_path("keep.png").exists())
            self.assertFalse(store.image_path("orphan.png").exists())

    def test_new_notes_get_random_note_colors_and_stay_in_the_list(self):
        with tempfile.TemporaryDirectory() as d:
            store = NoteStore(d)
            store.load()
            notes = [store.create() for _ in range(60)]
            colors = {note.color for note in notes}
            self.assertTrue(colors <= set(COLORS))
            self.assertGreater(len(colors), 1)
            self.assertFalse(any(note.open for note in notes))
            self.assertEqual(store.create("blue").color, "blue")

    def test_empty_and_unknown_color(self):
        self.assertTrue(Note(content=[{"t": "  \n", "s": []}]).is_empty())
        self.assertEqual(Note(color="neon").color, "yellow")


class RichTextTest(unittest.TestCase):
    def test_serialize_roundtrip(self):
        segs = [{"t": "plain ", "s": []}, {"t": "bold it", "s": ["bold", "italic"]},
                {"img": "a.png"}, {"t": "\n• item", "s": ["strike"]}]
        buf = Gtk.TextBuffer()
        install_tags(buf)
        anchors = {}

        def insert_image(it, name):
            anchors[buf.create_child_anchor(it)] = name

        deserialize(buf, segs, insert_image)
        self.assertEqual(serialize(buf, anchors), segs)

    def test_line_prefix(self):
        self.assertEqual(line_prefix("• a"), ("bullet", 2, 0))
        self.assertEqual(line_prefix("12. a"), ("number", 4, 12))
        self.assertEqual(line_prefix("☐ a"), ("check", 2, 0))
        self.assertEqual(line_prefix("☑ "), ("check", 2, 0))
        for text in ("", "a", "1.a", "-1. a", "•a"):
            self.assertEqual(line_prefix(text)[0], None, text)


@unittest.skipIf(Gdk.Display.get_default() is None, "needs a display")
class EditorTest(unittest.TestCase):
    def editor(self, text):
        editor = RichEditor(Gtk.TextView(), lambda name: Path("/nonexistent", name))
        editor.load([{"t": text, "s": []}])
        return editor

    def text(self, editor):
        buf = editor.buf
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def select_all(self, editor):
        editor.buf.select_range(editor.buf.get_start_iter(), editor.buf.get_end_iter())

    def enter_at_end_of_line(self, editor, line):
        _ok, it = editor.buf.get_iter_at_line(line)
        if not it.ends_line():
            it.forward_to_line_end()
        editor.buf.place_cursor(it)
        return editor._on_key_pressed(None, Gdk.KEY_Return, 0, 0)

    def test_list_styles_switch_and_toggle_off(self):
        editor = self.editor("a\nb")
        self.select_all(editor)
        editor.set_list("bullet")
        self.assertEqual(self.text(editor), "• a\n• b")
        self.assertEqual(editor.list_style(), "bullet")
        editor.set_list("number")
        self.assertEqual(self.text(editor), "1. a\n2. b")
        editor.set_list("number")
        self.assertEqual(self.text(editor), "a\nb")
        self.assertIsNone(editor.list_style())
        editor.set_list("check")
        self.assertEqual(self.text(editor), "☐ a\n☐ b")
        editor.toggle_check(1)
        self.assertEqual(self.text(editor), "☐ a\n☑ b")
        editor.toggle_check(1)
        self.assertEqual(self.text(editor), "☐ a\n☐ b")

    def test_enter_continues_and_renumbers(self):
        editor = self.editor("1. a\n2. b")
        self.assertTrue(self.enter_at_end_of_line(editor, 0))
        self.assertEqual(self.text(editor), "1. a\n2. \n3. b")
        # Enter on the empty item ends the list; the rest restarts at 1.
        self.assertTrue(self.enter_at_end_of_line(editor, 1))
        self.assertEqual(self.text(editor), "1. a\n\n1. b")
        editor = self.editor("☑ done")
        self.enter_at_end_of_line(editor, 0)
        self.assertEqual(self.text(editor), "☑ done\n☐ ")  # new items start unchecked
        editor = self.editor("plain")
        self.assertFalse(self.enter_at_end_of_line(editor, 0))

    def test_numbering_continues_list_above(self):
        editor = self.editor("1. a\nb\nc")
        _ok, start = editor.buf.get_iter_at_line(1)
        editor.buf.select_range(start, editor.buf.get_end_iter())
        editor.set_list("number")
        self.assertEqual(self.text(editor), "1. a\n2. b\n3. c")

    def test_unreadable_image_keeps_its_segment(self):
        with tempfile.TemporaryDirectory() as d:
            editor = RichEditor(Gtk.TextView(), lambda name: Path(d, name))
            segs = [{"t": "x", "s": []}, {"img": "missing.png"}]
            editor.load(segs)
            self.assertEqual(editor.dump(), segs)


if __name__ == "__main__":
    unittest.main()
