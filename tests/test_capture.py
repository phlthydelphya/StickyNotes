# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshot portal request: path, reply parsing and the one-shot callback."""
from gi.repository import GLib

from stickynotes import capture
from stickynotes.capture import CANCELLED, FAILED, OK, ScreenshotRequest, request_path


def test_request_path_from_unique_name():
    assert request_path(":1.42", "tok") == "/org/freedesktop/portal/desktop/request/1_42/tok"


def test_response_result():
    assert capture.response_result(0, {"uri": "file:///a.png"}) == (OK, "file:///a.png")
    assert capture.response_result(0, {}) == (FAILED, None)
    assert capture.response_result(1, {}) == (CANCELLED, None)
    assert capture.response_result(2, {}) == (FAILED, None)


class FakeResult:
    def __init__(self, value=None, error=None):
        self.value, self.error = value, error


class FakeConn:
    def __init__(self):
        self.subscribed, self.unsubscribed, self.calls = [], [], []

    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, sender, iface, member, path, arg0, flags, callback):
        self.subscribed.append((iface, member, path))
        return len(self.subscribed)

    def signal_unsubscribe(self, sub):
        self.unsubscribed.append(sub)

    def call(self, name, path, iface, method, params, reply, flags, timeout, cancel, callback):
        self.calls.append((iface, method, params.unpack()))

    def call_finish(self, result):
        if result.error:
            raise result.error
        return GLib.Variant("(o)", (result.value,))


def start(conn):
    done = []
    req = ScreenshotRequest(lambda status, uri: done.append((status, uri)), conn)
    req.start()
    return req, done


def test_subscribes_before_calling_and_reports_once():
    conn = FakeConn()
    req, done = start(conn)
    iface, member, path = conn.subscribed[0]
    assert (iface, member) == ("org.freedesktop.portal.Request", "Response")
    assert path.startswith("/org/freedesktop/portal/desktop/request/1_42/dusk_sticky_")
    _iface, method, (parent, options) = conn.calls[0]
    assert method == "Screenshot" and parent == "" and options["interactive"] is True
    assert path.endswith("/" + options["handle_token"])
    req._on_called(conn, FakeResult(path))
    assert len(conn.subscribed) == 1  # portal honoured the token
    response = GLib.Variant("(ua{sv})", (0, {"uri": GLib.Variant("s", "file:///s.png")}))
    req._on_response(conn, ":1.9", path, "", "Response", response)
    req._on_response(conn, ":1.9", path, "", "Response", response)
    assert done == [(OK, "file:///s.png")]
    assert conn.unsubscribed == [1]


def test_follows_handle_from_old_portal():
    conn = FakeConn()
    req, done = start(conn)
    req._on_called(conn, FakeResult("/org/freedesktop/portal/desktop/request/1_42/t1"))
    assert conn.subscribed[-1][2].endswith("/t1") and conn.unsubscribed == [1]
    cancel = GLib.Variant("(ua{sv})", (1, {}))
    req._on_response(conn, ":1.9", "", "", "Response", cancel)
    assert done == [(CANCELLED, None)]


def test_call_error_fails():
    conn = FakeConn()
    req, done = start(conn)
    req._on_called(conn, FakeResult(error=GLib.Error("no portal")))
    assert done == [(FAILED, None)] and conn.unsubscribed == [1]
