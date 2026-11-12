# SPDX-License-Identifier: GPL-3.0-or-later
"""Tray menu, preferences file and desktop integration (autostart, global shortcut)."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from stickynotes.preferences import Preferences
from stickynotes.tray import MENU_ITEMS, build_layout, item_properties

ROOT = Path(__file__).resolve().parent.parent


def run_isolated(tmp_path, code):
    """Run integration code with an in-memory GSettings backend and a temp config dir,
    so the real GNOME shortcuts and autostart folder are never touched."""
    env = dict(os.environ, GSETTINGS_BACKEND="memory", XDG_CONFIG_HOME=str(tmp_path / "config"),
               PYTHONDONTWRITEBYTECODE="1")
    script = "import sys; sys.path.insert(0, %r)\n" % str(ROOT) + textwrap.dedent(code)
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_menu_layout_shape_and_items():
    layout = build_layout()
    assert layout.get_type_string() == "(u(ia{sv}av))"
    _rev, (root_id, root_props, children) = layout.unpack()
    assert root_id == 0 and root_props["children-display"] == "submenu"
    assert [c[0] for c in children] == [iid for iid, *_ in MENU_ITEMS]
    labels = [label for _iid, label, _cb in MENU_ITEMS if label]
    assert labels == ["New Note", "Show Notes List", "Preferences", "Quit"]
    assert item_properties(4)["type"].get_string() == "separator"
    assert item_properties(99) is None


def test_preferences_defaults_and_bad_values(tmp_path):
    prefs = Preferences(tmp_path).load()
    assert prefs.close_to_tray is True
    prefs.values["close_to_tray"] = False
    prefs.save()
    assert Preferences(tmp_path).load().close_to_tray is False
    (tmp_path / "settings.json").write_text(json.dumps({"close_to_tray": "yes"}))
    assert Preferences(tmp_path).load().close_to_tray is True  # wrong type ignored
    (tmp_path / "settings.json").write_text("{broken")
    assert Preferences(tmp_path).load().close_to_tray is True


def test_autostart_file_round_trip(tmp_path):
    out = run_isolated(tmp_path, """
        from stickynotes import integration as i
        assert not i.autostart_enabled()
        i.set_autostart(True, "/opt/my apps/dusk-sticky-notes")
        print(i.autostart_file().read_text())
        i.set_autostart(False)
        assert not i.autostart_enabled()
        i.set_autostart(False)  # idempotent
    """)
    assert 'Exec="/opt/my apps/dusk-sticky-notes" --restore' in out
    assert "X-GNOME-Autostart-enabled=true" in out


def test_shortcut_add_remove_keeps_other_bindings(tmp_path):
    out = run_isolated(tmp_path, """
        from gi.repository import Gio
        from stickynotes import integration as i
        if not i.shortcut_supported():
            print("unsupported"); raise SystemExit(0)
        media = Gio.Settings.new(i.MEDIA_KEYS)
        other = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/other/"
        media.set_strv("custom-keybindings", [other])
        assert i.set_shortcut(True, "/home/u/.local/bin/dusk-sticky-notes")
        assert i.set_shortcut(True, "/home/u/.local/bin/dusk-sticky-notes")  # no duplicate
        custom = Gio.Settings.new_with_path(i.CUSTOM_KEY, i.SHORTCUT_PATH)
        print(media.get_strv("custom-keybindings"))
        print(custom.get_string("binding"), custom.get_string("command"))
        assert i.shortcut_enabled()
        i.set_shortcut(False)
        print(media.get_strv("custom-keybindings"), repr(custom.get_string("binding")))
    """)
    if out.strip() == "unsupported":
        return
    lines = out.strip().splitlines()
    assert lines[0].count("dusk-sticky-notes/") == 1 and "other/" in lines[0]
    assert lines[1] == "<Control><Alt>n /home/u/.local/bin/dusk-sticky-notes --new-note"
    assert "dusk-sticky-notes/" not in lines[2] and "other/" in lines[2] and "''" in lines[2]
