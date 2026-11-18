# SPDX-License-Identifier: GPL-3.0-or-later
"""GNOME Shell search provider: notes show up when you type in the Activities overview.

The app exports org.gnome.Shell.SearchProvider2 on its own bus name. GNOME Shell
finds it through a .search-provider.ini file in a system data dir (see
install-search-provider.sh) and starts the app through its D-Bus service file."""

from gi.repository import Gio, GLib

OBJECT_PATH = "/io/github/phlthydelphya/StickyNotes/SearchProvider"
IFACE = "org.gnome.Shell.SearchProvider2"
XML = f"""
<node>
  <interface name="{IFACE}">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{{sv}}" name="metas" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
  </interface>
</node>
"""
MAX_RESULTS = 20
DESCRIPTION_CHARS = 120


def lowered(terms):
    return [t.lower() for t in terms if t.strip()]


def find(store, terms, among=None):
    """Ids of the notes matching every term, pinned and newest first."""
    terms = lowered(terms)
    if not terms:
        return []
    notes = store.sorted()
    if among is not None:
        allowed = set(among)
        notes = [n for n in notes if n.id in allowed]
    return [n.id for n in notes if not n.is_empty() and store.matches(n, terms)][:MAX_RESULTS]


def meta(store, note, icon):
    """Name is the first line; the description is the rest, or the image text."""
    lines = [line.strip() for line in note.plain_text().replace("￼", "").splitlines()
             if line.strip()]
    if lines:
        name, rest = lines[0], " · ".join(lines[1:])
    else:
        name = "Image note"
        rest = " ".join(store.image_text.get(img, "") for img in note.images()).strip()
    result = {"id": GLib.Variant("s", note.id), "name": GLib.Variant("s", name[:80]),
              "gicon": GLib.Variant("s", icon)}
    if rest:
        result["description"] = GLib.Variant("s", rest[:DESCRIPTION_CHARS])
    return result


class SearchProvider:
    """Serves the interface for an app with .store and the two activation methods."""

    def __init__(self, app, icon_name):
        self.app = app
        self.icon = Gio.ThemedIcon.new(icon_name).to_string()
        self._info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self._conn = None
        self._reg = 0

    def register(self, conn, path=OBJECT_PATH):
        self._conn = conn
        self._reg = conn.register_object(path, self._info, self._on_call, None, None)
        return self._reg

    def unregister(self):
        if self._reg and self._conn is not None:
            self._conn.unregister_object(self._reg)
        self._reg, self._conn = 0, None

    def _on_call(self, *args):
        # Holding across each call restarts a service-mode app's idle timer, so
        # it is still running when the user picks a result.
        hold, release = getattr(self.app, "hold", None), getattr(self.app, "release", None)
        if hold is not None:
            hold()
        try:
            self._answer(*args)
        finally:
            if release is not None:
                release()

    def _answer(self, _conn, _sender, _path, _iface, method, params, invocation):
        store = self.app.store
        args = params.unpack()
        if store is None or store.read_only:
            reply = {"GetResultMetas": GLib.Variant("(aa{sv})", ([],))}.get(
                method, GLib.Variant("(as)", ([],)))
            invocation.return_value(None if method in ("ActivateResult", "LaunchSearch") else reply)
            return
        if method == "GetInitialResultSet":
            invocation.return_value(GLib.Variant("(as)", (find(store, args[0]),)))
        elif method == "GetSubsearchResultSet":
            invocation.return_value(GLib.Variant("(as)", (find(store, args[1], args[0]),)))
        elif method == "GetResultMetas":
            metas = [meta(store, store.get(i), self.icon) for i in args[0] if store.get(i)]
            invocation.return_value(GLib.Variant("(aa{sv})", (metas,)))
        elif method == "ActivateResult":
            self.app.activate_search_result(args[0], args[2])
            invocation.return_value(None)
        elif method == "LaunchSearch":
            self.app.launch_search(args[0], args[1])
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
