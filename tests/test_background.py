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
    assert labels == ["New Note", "New Note from Screenshot", "Show Notes List", "Preferences",
                      "Quit"]
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
        selection = Gio.Settings.new_with_path(i.CUSTOM_KEY, i.SELECTION_PATH)
        print(media.get_strv("custom-keybindings"))
        print(custom.get_string("binding"), custom.get_string("command"))
        print(selection.get_string("binding"), selection.get_string("command"))
        assert i.shortcut_enabled()
        i.set_shortcut(False)
        print(media.get_strv("custom-keybindings"), repr(custom.get_string("binding")),
              repr(selection.get_string("binding")))
    """)
    if out.strip() == "unsupported":
        return
    lines = out.strip().splitlines()
    assert lines[0].count("dusk-sticky-notes/") == 1 and "other/" in lines[0]
    assert lines[0].count("dusk-sticky-notes-selection/") == 1
    assert lines[1] == "<Control><Alt>n /home/u/.local/bin/dusk-sticky-notes --new-note"
    assert lines[2] == ("<Control><Alt><Shift>n /home/u/.local/bin/dusk-sticky-notes "
                        "--new-from-selection")
    assert "dusk-sticky-notes" not in lines[3] and "other/" in lines[3]
    assert lines[3].count("''") == 2


def test_desktop_exec_escapes_percent():
    from stickynotes.integration import _desktop_exec_quote
    assert _desktop_exec_quote("/opt/100%/run") == "/opt/100%%/run"
    assert _desktop_exec_quote("/my 50%/run") == '"/my 50%%/run"'


class FakeConn:
    def __init__(self):
        self.calls = []

    def get_unique_name(self):
        return ":1.42"

    def call(self, *args):
        self.calls.append(args[3])


def run_loop(milliseconds):
    from gi.repository import GLib
    loop = GLib.MainLoop()
    GLib.timeout_add(milliseconds, lambda: loop.quit() or GLib.SOURCE_REMOVE)
    loop.run()


def test_tray_waits_before_reporting_vanished_watcher(monkeypatch):
    from stickynotes import tray as tray_mod
    events = []
    tray = tray_mod.Tray(None, "x", "X", on_availability=events.append)
    tray.available = True
    # Watcher drops and comes back (shell restart): no availability change.
    tray._on_watcher_vanished(None, tray_mod.WATCHER)
    assert tray._grace
    conn = FakeConn()
    tray._on_watcher_appeared(conn, tray_mod.WATCHER, ":1.7")
    assert not tray._grace and conn.calls == ["RegisterStatusNotifierItem"]
    assert events == []
    # Watcher stays gone: reported after the grace period.
    monkeypatch.setattr(tray_mod, "VANISH_GRACE_S", 0)
    tray._on_watcher_vanished(None, tray_mod.WATCHER)
    run_loop(1100)  # second-granularity timer
    assert events == [False] and not tray.available
    # Absent from the start: nothing to wait for, no timer.
    tray._on_watcher_vanished(None, tray_mod.WATCHER)
    assert not tray._grace


def test_preference_switches_revert_when_change_fails(monkeypatch, tmp_path):
    import gi
    gi.require_version("Gdk", "4.0")
    from types import SimpleNamespace
    from gi.repository import Gdk
    if Gdk.Display.get_default() is None:
        import pytest
        pytest.skip("needs a display; run under xvfb-run")
    from gi.repository import Adw
    Adw.init()
    from stickynotes import preferences as p

    def fail(enabled, launcher=None):
        raise OSError("read-only config")
    monkeypatch.setattr(p.integration, "set_autostart", fail)
    monkeypatch.setattr(p.integration, "autostart_enabled", lambda: False)
    monkeypatch.setattr(p.integration, "set_shortcut", lambda enabled, launcher=None: False)
    monkeypatch.setattr(p.integration, "shortcut_enabled", lambda: False)
    monkeypatch.setattr(p.integration, "shortcut_supported", lambda: True)
    app = SimpleNamespace(prefs=Preferences(tmp_path), tray=None,
                          set_close_to_tray=lambda enabled: False)
    dialog = p.PreferencesDialog(app)
    for row in (dialog.autostart_row, dialog.shortcut_row):
        row.set_active(True)
        assert not row.get_active()
    dialog.tray_row.set_active(False)  # app kept True: switch follows the app
    assert dialog.tray_row.get_active()
