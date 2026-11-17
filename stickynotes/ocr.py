# SPDX-License-Identifier: GPL-3.0-or-later
"""Text in images: Tesseract reads each note image once, in the background, for search,
and read_image() reads one image on request, for text the user puts in a note.

The search text is derived data, so it lives in the user cache dir, not in notes.json.
Without the tesseract program nothing runs and searches see note text only."""

import json
import os
import sys
from pathlib import Path

from gi.repository import Gio, GLib

SCAN_DELAY_MS = 1500
SAVE_DELAY_MS = 2000


def default_cache_file():
    return Path(GLib.get_user_cache_dir()) / "dusk-sticky-notes" / "image-text.json"


def find_program():
    return GLib.find_program_in_path("tesseract")


def clean(text):
    """Tesseract output as one line of words."""
    return " ".join(text.split())


def tidy(text):
    """Tesseract output as lines for a note: no trailing spaces or form feed, at most
    one blank line in a row, none at the start or end."""
    lines = []
    for line in text.replace("\f", "").splitlines():
        line = line.rstrip()
        if line or (lines and lines[-1]):
            lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def read_image(program, path, on_done):
    """Run Tesseract on path in the background; on_done(text) gets its raw output,
    or None when it did not start or failed. Returns False when it did not start."""
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE
                                          | Gio.SubprocessFlags.STDERR_SILENCE)
    launcher.setenv("OMP_THREAD_LIMIT", "1", True)  # stay in the background
    try:
        proc = launcher.spawnv([program, str(path), "stdout"])
    except GLib.Error as err:
        print(f"dusk-sticky-notes: tesseract did not start: {err.message}", file=sys.stderr)
        return False

    def finished(proc, result):
        try:
            _ok, out, _err = proc.communicate_utf8_finish(result)
        except GLib.Error:
            out = None
        ok = proc.get_if_exited() and proc.get_exit_status() == 0
        on_done(out if ok else None)

    proc.communicate_utf8_async(None, None, finished)
    return True


class OcrIndex:
    """Fills store.image_text {image name: text}; on_update() runs after each new text."""

    def __init__(self, store, cache_file=None, program=None, on_update=None):
        self.store = store
        self.cache_file = Path(cache_file) if cache_file else default_cache_file()
        self.program = program if program is not None else find_program()
        self.on_update = on_update
        self._running = None  # image name being read
        self._failed = set()  # unreadable this session; not retried until restart
        self._scan_source = 0
        self._save_source = 0
        self._load()
        self.store.image_text = self.texts

    @property
    def available(self):
        return bool(self.program)

    def _load(self):
        self.texts = {}
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.texts = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def _save(self):
        self._save_source = 0
        used = {name for note in self.store.notes.values() for name in note.images()}
        for name in [n for n in self.texts if n not in used]:
            del self.texts[name]  # images that are gone
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.texts, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_file)
        except OSError as err:
            print(f"dusk-sticky-notes: image text not cached: {err}", file=sys.stderr)
        return GLib.SOURCE_REMOVE

    def stop(self):
        """Cancel pending work; text read but not yet cached is written now."""
        if self._scan_source:
            GLib.source_remove(self._scan_source)
            self._scan_source = 0
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save()

    # --- scanning -----------------------------------------------------------------
    def schedule_scan(self, delay_ms=SCAN_DELAY_MS):
        if self.available and not self._scan_source:
            self._scan_source = GLib.timeout_add(delay_ms, self._on_scan_timeout)

    def _on_scan_timeout(self):
        self._scan_source = 0
        self.scan()
        return GLib.SOURCE_REMOVE

    def pending(self):
        """Images that notes use and that have not been read yet."""
        return [name for note in self.store.sorted() for name in note.images()
                if name not in self.texts and name not in self._failed]

    def scan(self):
        """Read the next unread image; each result starts the next one."""
        if not self.available or self._running is not None:
            return
        todo = self.pending()
        if not todo:
            return
        name = todo[0]
        try:
            path = self.store.image_path(name)
        except ValueError:
            self._failed.add(name)
            self.scan()
            return
        self._running = name
        if not read_image(self.program, path, lambda out: self._on_read(name, out)):
            self._running = None
            self.program = None

    def _on_read(self, name, out):
        self._running = None
        if out is not None:
            self.texts[name] = clean(out)
            if not self._save_source:
                self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._save)
            if self.on_update is not None:
                self.on_update()
        else:
            self._failed.add(name)
        self.scan()
