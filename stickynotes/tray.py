# SPDX-License-Identifier: GPL-3.0-or-later
"""Tray icon: a StatusNotifierItem with a com.canonical.dbusmenu menu, over Gio D-Bus.

GTK 4 has no tray API and libappindicator is GTK 3 only, so the two small D-Bus
interfaces are served directly. Works with GNOME's AppIndicator extension and KDE.
"""

from gi.repository import Gio, GLib

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
  </interface>
</node>
"""

# id -> (label, callback name on the app) ; None label = separator
MENU_ITEMS = (
    (1, "New Note", "new_note"),
    (2, "Show Notes List", "toggle_list"),
    (3, "Preferences", "show_preferences"),
    (4, None, None),
    (5, "Quit", "quit_saving"),
)


def item_properties(item_id):
    if item_id == 0:
        return {"children-display": GLib.Variant("s", "submenu")}
    for iid, label, _cb in MENU_ITEMS:
        if iid == item_id:
            if label is None:
                return {"type": GLib.Variant("s", "separator")}
            return {"label": GLib.Variant("s", label), "enabled": GLib.Variant("b", True),
                    "visible": GLib.Variant("b", True)}
    return None


def build_layout():
    children = [GLib.Variant("(ia{sv}av)", (iid, item_properties(iid), [])) for iid, *_ in MENU_ITEMS]
    return GLib.Variant("(u(ia{sv}av))", (1, (0, item_properties(0), children)))


class Tray:
    """Shows the icon while a StatusNotifierWatcher exists; reports availability changes."""

    def __init__(self, app, app_id, title, icon_theme_path="", on_availability=None):
        self.app = app
        self.app_id = app_id
        self.title = title
        self.icon_theme_path = icon_theme_path
        self.on_availability = on_availability
        self.available = False
        self.conn = None
        self._ids = []
        self._watch = 0

    @staticmethod
    def watcher_present(conn):
        try:
            reply = conn.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                   "org.freedesktop.DBus", "NameHasOwner",
                                   GLib.Variant("(s)", (WATCHER,)), GLib.VariantType("(b)"),
                                   Gio.DBusCallFlags.NONE, 1000, None)
        except GLib.Error:
            return False
        return reply.unpack()[0]

    def start(self):
        try:
            self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False
        item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._ids = [
            self.conn.register_object(ITEM_PATH, item, self._item_method, self._item_property, None),
            self.conn.register_object(MENU_PATH, menu, self._menu_method, self._menu_property, None),
        ]
        self._watch = Gio.bus_watch_name_on_connection(
            self.conn, WATCHER, Gio.BusNameWatcherFlags.NONE, self._on_watcher_appeared,
            self._on_watcher_vanished)
        return self.watcher_present(self.conn)

    def stop(self):
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        for reg in self._ids:
            self.conn.unregister_object(reg)
        self._ids = []
        self._set_available(False)

    # --- watcher -------------------------------------------------------------------
    def _on_watcher_appeared(self, conn, _name, _owner):
        conn.call(WATCHER, WATCHER_PATH, WATCHER, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (conn.get_unique_name(),)), None,
                  Gio.DBusCallFlags.NONE, 5000, None, self._on_registered)

    def _on_registered(self, conn, result):
        try:
            conn.call_finish(result)
        except GLib.Error:
            self._set_available(False)
            return
        self._set_available(True)

    def _on_watcher_vanished(self, _conn, _name):
        self._set_available(False)

    def _set_available(self, available):
        if available != self.available:
            self.available = available
            if self.on_availability:
                self.on_availability(available)

    # --- StatusNotifierItem -----------------------------------------------------------
    def _item_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self.app_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            # Symbolic: one color the shell repaints white or black to suit the panel.
            "IconName": GLib.Variant("s", f"{self.app_id}-symbolic"),
            "IconThemePath": GLib.Variant("s", self.icon_theme_path),
            "IconPixmap": GLib.Variant("a(iiay)", []),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], self.title, "")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(name)

    def _item_method(self, _conn, _sender, _path, _iface, method, _params, invocation):
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self._run, "new_note" if method == "SecondaryActivate" else "toggle_list")
        invocation.return_value(None)

    def _run(self, name):
        getattr(self.app, name)()
        return GLib.SOURCE_REMOVE

    # --- dbusmenu -----------------------------------------------------------------------
    def _menu_property(self, _conn, _sender, _path, _iface, name):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"),
                "IconThemePath": GLib.Variant("as", [])}.get(name)

    def _menu_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "GetLayout":
            invocation.return_value(build_layout())
        elif method == "GetGroupProperties":
            ids = params.unpack()[0] or [iid for iid, *_ in MENU_ITEMS]
            props = [(i, item_properties(i)) for i in ids if item_properties(i) is not None]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (props,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            value = (item_properties(item_id) or {}).get(name)
            if value is None:
                invocation.return_dbus_error("com.canonical.dbusmenu.Error", "No such property")
            else:
                invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item_id, event, _data, _ts = params.unpack()
            if event == "clicked":
                self._activate_item(item_id)
            invocation.return_value(None)
        elif method == "EventGroup":
            for item_id, event, _data, _ts in params.unpack()[0]:
                if event == "clicked":
                    self._activate_item(item_id)
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _activate_item(self, item_id):
        for iid, _label, callback in MENU_ITEMS:
            if iid == item_id and callback:
                GLib.idle_add(self._run, callback)
