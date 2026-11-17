# SPDX-License-Identifier: GPL-3.0-or-later
"""Note model and on-disk persistence (JSON + image files in the user data dir)."""

import math
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path
from enum import Enum

from gi.repository import GLib

from .palette import COLORS, DEFAULT_COLOR

SAVE_DELAY_MS = 400


class LoadState(Enum):
    MISSING = "missing"
    OK = "ok"
    CORRUPT = "corrupt"
    NEWER = "newer"


def valid_image_name(name):
    return isinstance(name, str) and bool(name) and name != "." and "/" not in name and ".." not in name


def validate(data):
    if not isinstance(data, dict) or type(data.get("version")) is not int:
        raise ValueError("Invalid version")
    if data["version"] != 1 or not isinstance(data.get("notes"), list):
        raise ValueError("Invalid notes schema")
    ids = set()
    for note in data["notes"]:
        if not isinstance(note, dict) or not isinstance(note.get("id"), str) or not note["id"]:
            raise ValueError("Invalid note id")
        if note["id"] in ids:
            raise ValueError("Duplicate note id")
        ids.add(note["id"])
        if not isinstance(note.get("content"), list):
            raise ValueError("Invalid content")
        for seg in note["content"]:
            if not isinstance(seg, dict) or (("t" in seg) == ("img" in seg)):
                raise ValueError("Invalid segment")
            if "img" in seg:
                if not valid_image_name(seg["img"]):
                    raise ValueError("Invalid image name")
            elif not isinstance(seg["t"], str) or not isinstance(seg.get("s", []), list):
                raise ValueError("Invalid text segment")
        for key in ("created", "modified"):
            if key in note and (type(note[key]) not in (int, float) or not math.isfinite(note[key])):
                raise ValueError("Invalid timestamp")
        for key in ("width", "height"):
            if key in note and (type(note[key]) is not int or not 1 <= note[key] <= 32767):
                raise ValueError("Invalid size")
        if "open" in note and type(note["open"]) is not bool:
            raise ValueError("Invalid open state")


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
        self._listeners = {}
        self._next_listener_id = 0
        self._save_source = 0
        self.read_only = False
        self.load_state = LoadState.MISSING
        self.last_error = None
        self.unsaved = False
        self._save_listeners = {}

    # --- persistence -------------------------------------------------------
    def load(self):
        """Validate before exposing notes or permitting image collection."""
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.notes = {}
        self.read_only = False
        self.load_state = LoadState.MISSING
        if not self.file.exists():
            return self.load_state
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and type(data.get("version")) is int and data["version"] > 1:
                self.read_only = True
                self.load_state = LoadState.NEWER
                return self.load_state
            validate(data)
            notes = [Note.from_dict(d) for d in data["notes"]]
        except (OSError, ValueError, AttributeError, TypeError):
            self.file.rename(self.file.with_name(f"notes.json.corrupt-{time.time_ns()}"))
            self.load_state = LoadState.CORRUPT
            return self.load_state
        self.notes = {n.id: n for n in notes}
        self.load_state = LoadState.OK
        return self.load_state

    def recovery_files(self):
        """Recovery payloads are never imported or deleted automatically."""
        return sorted(self.root.glob("notes.json.unsaved-*"))

    def payload(self):
        return json.dumps({"version": 1, "notes": [n.to_dict() for n in self.notes.values()]},
                          ensure_ascii=False, indent=1)

    def save_emergency(self):
        if self.read_only:
            raise OSError("Notes were saved by a newer version")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.file.with_name(f"notes.json.unsaved-{time.time_ns()}")
        with path.open("x", encoding="utf-8") as f:
            f.write(self.payload())
            f.flush()
            os.fsync(f.fileno())
        return path

    def connect_save_state(self, callback):
        return self._subscribe(self._save_listeners, callback)

    def _save_state(self, unsaved, error=None):
        self.unsaved, self.last_error = unsaved, error
        for listener_id, callback in list(self._save_listeners.items()):
            if listener_id in self._save_listeners:
                callback()

    def save_now(self):
        if self.read_only:
            raise OSError("Notes were saved by a newer version")
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        self.root.mkdir(parents=True, exist_ok=True)
        payload = self.payload()
        tmp = self.file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.file)
        self._save_state(False)

    def schedule_save(self):
        if self.read_only:
            return
        self._save_state(True, self.last_error)
        if not self._save_source:
            self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timeout)

    def try_save(self):
        """save_now() that reports a failed write instead of raising; retries later."""
        try:
            self.save_now()
            return True
        except OSError as err:
            print(f"dusk-sticky-notes: could not save notes: {err}", file=sys.stderr)
            self._save_state(True, str(err))
            if not self.read_only and not self._save_source:
                self._save_source = GLib.timeout_add_seconds(5, self._on_save_timeout)
            return False

    def _on_save_timeout(self):
        self._save_source = 0
        self.try_save()
        return GLib.SOURCE_REMOVE

    # --- change notification ----------------------------------------------
    def _subscribe(self, listeners, callback):
        self._next_listener_id += 1
        listeners[self._next_listener_id] = callback
        return self._next_listener_id

    def connect(self, callback):
        return self._subscribe(self._listeners, callback)

    def disconnect(self, listener_id):
        """Disconnect either subscription kind; repeated/unknown IDs are harmless."""
        self._listeners.pop(listener_id, None)
        self._save_listeners.pop(listener_id, None)

    def _notify(self, note_id):
        for listener_id, cb in list(self._listeners.items()):
            if listener_id in self._listeners:
                cb(note_id)

    # --- operations --------------------------------------------------------
    def get(self, note_id):
        return self.notes.get(note_id)

    def sorted(self):
        return sorted(self.notes.values(), key=lambda n: n.modified, reverse=True)

    def matches(self, note, terms):
        """True when every (lowercase) term appears in the note."""
        text = note.plain_text().lower()
        return all(term in text for term in terms)

    def create(self, color=None):
        """A new note in the notes list, in a random note color unless one is given."""
        note = Note(color=color or random.choice(COLORS))
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
        if not valid_image_name(name):
            raise ValueError("Invalid image name")
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
        if self.load_state is not LoadState.OK or self.read_only or self.unsaved or self.recovery_files():
            return
        used = {name for n in self.notes.values() for name in n.images()}
        if not self.images_dir.is_dir():
            return
        for path in self.images_dir.iterdir():
            if path.is_file() and path.name not in used:
                path.unlink()
