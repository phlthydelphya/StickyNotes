# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the text selected in another app (the primary selection) for a new note.

Wayland only hands the selection to the focused app, so read it once the note's
window is active. Browsers may say which page the text came from."""

from gi.repository import GLib

# Page address offered next to selected text: Firefox, then Chromium browsers.
SOURCE_TYPES = ("text/x-moz-url-priv", "chromium/x-source-url")
SOURCE_BYTES = 4096


def source_url(data):
    """The page address in a browser's source-URL clipboard data, or None."""
    if not data:
        return None
    if b"\x00" in data:
        text = data.decode("utf-16-le" if data[1:2] == b"\x00" else "utf-16-be", "ignore")
    else:
        text = data.decode("utf-8", "ignore")
    url = text.lstrip("﻿").split("\n", 1)[0].strip().strip("\x00")
    return url if url.startswith(("https://", "http://")) else None


def capture_text(text, source):
    """The note text for a capture: the selection, then where it came from."""
    text = text.strip()
    return f"{text}\n\nFrom {source}" if source else text


def read_selection(clipboard, on_done):
    """on_done(text or None, source URL or None), once."""
    formats = clipboard.get_formats()
    source_type = next((t for t in SOURCE_TYPES if formats.contain_mime_type(t)), None)

    def got_text(cb, result):
        try:
            text = cb.read_text_finish(result)
        except GLib.Error:
            text = None
        if not text or not text.strip():
            on_done(None, None)
        elif source_type is None:
            on_done(text, None)
        else:
            cb.read_async([source_type], GLib.PRIORITY_DEFAULT, None,
                          lambda c, r: got_stream(c, r, text))

    def got_stream(cb, result, text):
        try:
            stream, _mime = cb.read_finish(result)
        except GLib.Error:
            on_done(text, None)
            return
        stream.read_bytes_async(SOURCE_BYTES, GLib.PRIORITY_DEFAULT, None,
                                lambda s, r: got_bytes(s, r, text))

    def got_bytes(stream, result, text):
        try:
            data = stream.read_bytes_finish(result).get_data()
        except GLib.Error:
            data = b""
        stream.close_async(GLib.PRIORITY_DEFAULT, None, None)
        on_done(text, source_url(data))

    clipboard.read_text_async(None, got_text)
