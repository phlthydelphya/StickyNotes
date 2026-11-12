"""Note model and on-disk persistence (JSON + image files in the user data dir)."""

import json
import os
import sys
import time
import uuid
from pathlib import Path

from gi.repository import GLib

from .palette import COLORS, DEFAULT_COLOR

SAVE_DELAY_MS = 400


def default_root():
    return Path(GLib.get_user_data_dir()) / "dusk-sticky-notes"


class Note:
    def __init__(self, id=None, color=DEFAULT_COLOR, content=None, created=None,
                 modified=None, open=False, width=320, height=320):
        now = time.time()
        self.id = id or uuid.uuid4().hex
        self.color = color if color in COLORS else DEFAULT_COLOR
        # content: list of {"t": text, "s": [tag names]} or {"img": filename}
        self.content = content or []
        self.created = created or now
        self.modified = modified or now
        self.open = open
        self.width = width
        self.height = height

    @classmethod
    def from_dict(cls, d):
        return cls(id=d.get("id"), color=d.get("color", DEFAULT_COLOR),
                   content=d.get("content", []), created=d.get("created"),
                   modified=d.get("modified"), open=d.get("open", False),
                   width=d.get("width", 320), height=d.get("height", 320))

    def to_dict(self):
        return {"id": self.id, "color": self.color, "content": self.content,
                "created": self.created, "modified": self.modified, "open": self.open,
                "width": self.width, "height": self.height}

    def plain_text(self):
        return "".join(seg.get("t", "") for seg in self.content)

    def images(self):
        return [seg["img"] for seg in self.content if "img" in seg]

    def is_empty(self):
        return not self.plain_text().strip() and not self.images()


class NoteStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else default_root()
        self.images_dir = self.root / "images"
        self.file = self.root / "notes.json"
        self.notes = {}
        self._listeners = []
        self._save_source = 0

    # --- persistence -------------------------------------------------------
    def load(self):
        """Read notes.json. Returns True only when the file existed and parsed."""
        self.images_dir.mkdir(parents=True, exist_ok=True)
        if not self.file.exists():
            return False
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            notes = [Note.from_dict(d) for d in data.get("notes", [])]
        except (OSError, ValueError, AttributeError, TypeError):
            # Keep the unreadable file for recovery instead of overwriting it.
            self.file.rename(self.file.with_name(f"notes.json.corrupt-{int(time.time())}"))
            return False
        self.notes = {n.id: n for n in notes}
        return True

    def save_now(self):
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "notes": [n.to_dict() for n in self.notes.values()]},
                             ensure_ascii=False, indent=1)
        tmp = self.file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.file)

    def schedule_save(self):
        if not self._save_source:
            self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timeout)

    def try_save(self):
        """save_now() that reports a failed write instead of raising; retries later."""
        try:
            self.save_now()
            return True
        except OSError as err:
            print(f"dusk-sticky-notes: could not save notes: {err}", file=sys.stderr)
            self._save_source = GLib.timeout_add_seconds(5, self._on_save_timeout)
            return False

    def _on_save_timeout(self):
        self._save_source = 0
        self.try_save()
        return GLib.SOURCE_REMOVE

    # --- change notification ----------------------------------------------
    def connect(self, callback):
        self._listeners.append(callback)

    def _notify(self, note_id):
        for cb in list(self._listeners):
            cb(note_id)

    # --- operations --------------------------------------------------------
    def get(self, note_id):
        return self.notes.get(note_id)

    def sorted(self):
        return sorted(self.notes.values(), key=lambda n: n.modified, reverse=True)

    def create(self, color=DEFAULT_COLOR):
        note = Note(color=color, open=True)
        self.notes[note.id] = note
        self._notify(note.id)
        self.schedule_save()
        return note

    def update(self, note, bump=True):
        if bump:
            note.modified = time.time()
        self._notify(note.id)
        self.schedule_save()

    def delete(self, note_id):
        note = self.notes.pop(note_id, None)
        if note is None:
            return
        self._notify(note_id)
        # Commit the note's removal before its images go, so a crash cannot
        # leave a note that points at deleted files.
        if self.try_save():
            for name in note.images():
                self._remove_image(name)

    # --- images -------------------------------------------------------------
    def image_path(self, name):
        return self.images_dir / name

    def new_image_name(self, ext):
        self.images_dir.mkdir(parents=True, exist_ok=True)
        return f"{uuid.uuid4().hex}{ext.lower()}"

    def _remove_image(self, name):
        path = self.image_path(Path(name).name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def gc_images(self):
        """Remove image files that no note references (deleted from a note's text)."""
        used = {name for n in self.notes.values() for name in n.images()}
        if not self.images_dir.is_dir():
            return
        for path in self.images_dir.iterdir():
            if path.is_file() and path.name not in used:
                path.unlink()
