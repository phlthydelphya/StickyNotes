# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop integration: login autostart file and the GNOME global new-note shortcut.

State lives in the desktop itself (the autostart file, GNOME's custom-keybindings
list), so the preferences switches, the installer and the uninstaller agree.
Needs Gio only, no display, so install.sh can call it.
"""

import itertools
import os
import shlex
import shutil
import sys
from pathlib import Path

from gi.repository import Gio, GLib

APP_ID = "io.github.phlthydelphya.StickyNotes"
# Releases before 1.0.1 used this ID; their autostart entry carries its name.
OLD_APP_ID = "org.duskos.StickyNotes"
LAUNCHER = "dusk-sticky-notes"
SHORTCUT = "<Control><Alt>n"
SHORTCUT_LABEL = "Ctrl+Alt+N"
SELECTION_SHORTCUT_LABEL = "Ctrl+Alt+Shift+N"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
_KEYS = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
SHORTCUT_PATH = _KEYS + "dusk-sticky-notes/"
SELECTION_PATH = _KEYS + "dusk-sticky-notes-selection/"
# path -> (name, launcher option, binding); the preferences switch sets both.
SHORTCUTS = {
    SHORTCUT_PATH: ("New sticky note", "--new-note", SHORTCUT),
    SELECTION_PATH: ("New sticky note from selected text", "--new-from-selection",
                     "<Control><Alt><Shift>n"),
}


PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
BACKGROUND = "org.freedesktop.portal.Background"
REQUEST = "org.freedesktop.portal.Request"
_tokens = itertools.count(1)


def in_flatpak():
    return os.path.exists("/.flatpak-info")


def host_config_dir():
    """The desktop's config dir. In a Flatpak, GLib's points into the sandbox."""
    if in_flatpak():
        return os.environ.get("HOST_XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return GLib.get_user_config_dir()


def launcher_path():
    """Command that starts this app: the installed launcher, else this checkout's."""
    found = shutil.which(LAUNCHER)
    if found:
        return found
    argv0 = os.path.abspath(sys.argv[0])
    if os.path.basename(argv0) == LAUNCHER:
        return argv0
    return str(Path(__file__).resolve().parent.parent / LAUNCHER)


def _desktop_exec_quote(arg):
    arg = arg.replace("%", "%%")  # a bare % starts a field code such as %f
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


# --- autostart ----------------------------------------------------------------

def autostart_file():
    return Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"


def _old_autostart_file():
    return autostart_file().with_name(f"{OLD_APP_ID}.desktop")


def autostart_enabled():
    old = _old_autostart_file()
    if old.is_file():
        # An entry from before the ID change: replace it with one under the new name.
        old.unlink()
        set_autostart(True)
    return autostart_file().is_file()


def set_autostart(enabled, launcher=None):
    path = autostart_file()
    if not enabled:
        path.unlink(missing_ok=True)
        _old_autostart_file().unlink(missing_ok=True)
        if in_flatpak():
            request_background(False)
        return
    command = _desktop_exec_quote(launcher or launcher_path())
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Sticky Notes\n"
        "Comment=Reopen your sticky notes at login\n"
        f"Exec={command} --restore\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".desktop.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    if in_flatpak():
        # The file above stays in the sandbox and only records the choice;
        # the Background portal writes the entry the desktop reads.
        request_background(True)


def request_background(autostart, conn=None):
    """Ask the Background portal to add or remove the login entry. When the
    portal or the user refuses, the sandbox's record is removed too."""
    def refused(message):
        print(f"dusk-sticky-notes: start at login not changed: {message}", file=sys.stderr)
        if autostart:
            autostart_file().unlink(missing_ok=True)

    try:
        conn = conn or Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as err:
        refused(err.message)
        return
    token = f"stickynotes{os.getpid()}_{next(_tokens)}"
    sender = conn.get_unique_name().lstrip(":").replace(".", "_")
    handle = f"{PORTAL_PATH}/request/{sender}/{token}"
    sub = []

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        conn.signal_unsubscribe(sub[0])
        code, results = params.unpack()
        if code != 0 or results.get("autostart", autostart) != autostart:
            refused("the request was declined")

    sub.append(conn.signal_subscribe(PORTAL, REQUEST, "Response", handle, None,
                                     Gio.DBusSignalFlags.NONE, on_response))
    options = {
        "handle_token": GLib.Variant("s", token),
        "reason": GLib.Variant("s", "Reopen your sticky notes at login"),
        "autostart": GLib.Variant("b", autostart),
        "commandline": GLib.Variant("as", [LAUNCHER, "--restore"]),
    }
    try:
        conn.call_sync(PORTAL, PORTAL_PATH, BACKGROUND, "RequestBackground",
                       GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                       Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as err:
        conn.signal_unsubscribe(sub[0])
        refused(err.message)


# --- global shortcut -------------------------------------------------------------

def shortcut_supported():
    if in_flatpak():
        return False  # the desktop's GNOME settings are out of the sandbox's reach
    source = Gio.SettingsSchemaSource.get_default()
    return bool(source and source.lookup(MEDIA_KEYS, True) and source.lookup(CUSTOM_KEY, True))


def shortcut_enabled():
    if not shortcut_supported():
        return False
    return SHORTCUT_PATH in Gio.Settings.new(MEDIA_KEYS).get_strv("custom-keybindings")


def set_shortcut(enabled, launcher=None):
    if not shortcut_supported():
        return False
    media = Gio.Settings.new(MEDIA_KEYS)
    paths = list(media.get_strv("custom-keybindings"))
    command = shlex.quote(launcher or launcher_path())
    for path, (name, option, binding) in SHORTCUTS.items():
        custom = Gio.Settings.new_with_path(CUSTOM_KEY, path)
        if enabled:
            custom.set_string("name", name)
            custom.set_string("command", f"{command} {option}")
            custom.set_string("binding", binding)
            if path not in paths:
                paths.append(path)
        else:
            if path in paths:
                paths.remove(path)
            for key in ("name", "command", "binding"):
                custom.reset(key)
    if paths != list(media.get_strv("custom-keybindings")):
        media.set_strv("custom-keybindings", paths)
    Gio.Settings.sync()
    return True
