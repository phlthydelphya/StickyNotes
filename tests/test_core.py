# SPDX-License-Identifier: GPL-3.0-or-later
import tempfile
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from stickynotes.richtext import RichEditor, install_tags, serialize, deserialize  # noqa: E402
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


@unittest.skipIf(Gdk.Display.get_default() is None, "needs a display")
class EditorTest(unittest.TestCase):
    def test_unreadable_image_keeps_its_segment(self):
        with tempfile.TemporaryDirectory() as d:
            editor = RichEditor(Gtk.TextView(), lambda name: Path(d, name))
            segs = [{"t": "x", "s": []}, {"img": "missing.png"}]
            editor.load(segs)
            self.assertEqual(editor.dump(), segs)


if __name__ == "__main__":
    unittest.main()
