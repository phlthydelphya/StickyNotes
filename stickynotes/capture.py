# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshots through the xdg-desktop-portal Screenshot interface, over Gio D-Bus.

On GNOME an interactive request opens the Shell's own screenshot UI (area,
window or screen). The portal saves the picture and replies with its URI.
"""

import itertools
import os
import sys

from gi.repository import Gio, GLib

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENSHOT = "org.freedesktop.portal.Screenshot"
REQUEST = "org.freedesktop.portal.Request"

OK, CANCELLED, FAILED = "ok", "cancelled", "failed"
_counter = itertools.count(1)


def request_path(unique_name, token):
    """Object path the portal will use for a request made with handle_token=token."""
    sender = unique_name.lstrip(":").replace(".", "_")
    return f"{PORTAL_PATH}/request/{sender}/{token}"


def response_result(code, results):
    """Portal Response (code, results) -> (OK|CANCELLED|FAILED, uri or None)."""
    if code == 0 and results.get("uri"):
        return OK, results["uri"]
    if code == 1:
        return CANCELLED, None
    return FAILED, None


class ScreenshotRequest:
    """One interactive screenshot. on_done(status, uri) runs once, on the main loop."""

    def __init__(self, on_done, conn=None):
        self.on_done = on_done
        self.conn = conn
        self._sub = 0
        self._path = None
        self._done = False

    def start(self):
        try:
            if self.conn is None:
                self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self._finish(FAILED, None)
            return
        token = f"dusk_sticky_{os.getpid()}_{next(_counter)}"
        # Subscribe before calling, so a fast reply cannot be missed.
        self._subscribe(request_path(self.conn.get_unique_name(), token))
        options = {"handle_token": GLib.Variant("s", token),
                   "interactive": GLib.Variant("b", True)}
        self.conn.call(PORTAL, PORTAL_PATH, SCREENSHOT, "Screenshot",
                       GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                       Gio.DBusCallFlags.NONE, -1, None, self._on_called)

    def _subscribe(self, path):
        self._unsubscribe()
        self._path = path
        self._sub = self.conn.signal_subscribe(None, REQUEST, "Response", path, None,
                                               Gio.DBusSignalFlags.NONE, self._on_response)

    def _unsubscribe(self):
        if self._sub:
            self.conn.signal_unsubscribe(self._sub)
            self._sub = 0

    def _on_called(self, conn, result):
        try:
            handle = conn.call_finish(result).unpack()[0]
        except GLib.Error as err:
            print(f"dusk-sticky-notes: screenshot portal unavailable: {err.message}", file=sys.stderr)
            self._finish(FAILED, None)
            return
        if handle != self._path and not self._done:
            self._subscribe(handle)  # very old portals ignore handle_token

    def _on_response(self, _conn, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        self._finish(*response_result(code, results))

    def _finish(self, status, uri):
        if self._done:
            return
        self._done = True
        self._unsubscribe()
        self.on_done(status, uri)
