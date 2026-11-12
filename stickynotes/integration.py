# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop integration: login autostart file and the GNOME global new-note shortcut.

State lives in the desktop itself (the autostart file, GNOME's custom-keybindings
list), so the preferences switches, the installer and the uninstaller agree.
Needs Gio only, no display, so install.sh can call it.
"""

import os
import shlex
import shutil
import sys
from pathlib import Path

from gi.repository import Gio, GLib

APP_ID = "org.duskos.StickyNotes"
LAUNCHER = "dusk-sticky-notes"
SHORTCUT = "<Control><Alt>n"
SHORTCUT_LABEL = "Ctrl+Alt+N"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
SHORTCUT_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dusk-sticky-notes/"


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
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


# --- autostart ----------------------------------------------------------------

def autostart_file():
    return Path(GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"


def autostart_enabled():
    return autostart_file().is_file()


def set_autostart(enabled, launcher=None):
    path = autostart_file()
    if not enabled:
        path.unlink(missing_ok=True)
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


# --- global shortcut -------------------------------------------------------------

def shortcut_supported():
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
    custom = Gio.Settings.new_with_path(CUSTOM_KEY, SHORTCUT_PATH)
    if enabled:
        custom.set_string("name", "New sticky note")
        custom.set_string("command", f"{shlex.quote(launcher or launcher_path())} --new-note")
        custom.set_string("binding", SHORTCUT)
        if SHORTCUT_PATH not in paths:
            media.set_strv("custom-keybindings", paths + [SHORTCUT_PATH])
    else:
        if SHORTCUT_PATH in paths:
            media.set_strv("custom-keybindings", [p for p in paths if p != SHORTCUT_PATH])
        for key in ("name", "command", "binding"):
            custom.reset(key)
    Gio.Settings.sync()
    return True
