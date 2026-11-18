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


def test_style_preference_values(tmp_path):
    assert Preferences(tmp_path).load().style == "system"
    (tmp_path / "settings.json").write_text(json.dumps({"style": "light"}))
    assert Preferences(tmp_path).load().style == "light"
    (tmp_path / "settings.json").write_text(json.dumps({"style": "sepia"}))
    assert Preferences(tmp_path).load().style == "system"  # unknown style ignored


def _theme_dirs(tmp_path, imported, quote='"'):
    theme = tmp_path / "my theme"
    theme.mkdir(exist_ok=True)
    user = tmp_path / "gtk-4.0"
    user.mkdir(exist_ok=True)
    uri = (theme / imported).as_uri()  # "my%20theme": escapes must be decoded
    (user / "gtk.css").write_text(f"@import url({quote}{uri}{quote});\n")
    return theme, user


def test_imported_variant_finds_the_other_variant_both_ways(tmp_path):
    from stickynotes.theme import imported_variant
    assert imported_variant(tmp_path) is None  # no gtk.css
    theme, user = _theme_dirs(tmp_path, "gtk-dark.css")
    (theme / "gtk-dark.css").write_text("")
    assert imported_variant(tmp_path) is None  # no light sibling
    (theme / "gtk.css").write_text("")
    assert imported_variant(tmp_path)[:2] == (True, theme / "gtk.css")
    _theme_dirs(tmp_path, "gtk.css", quote="'")
    assert imported_variant(tmp_path)[:2] == (False, theme / "gtk-dark.css")
    _theme_dirs(tmp_path, "gtk.css", quote="")
    assert imported_variant(tmp_path)[:2] == (False, theme / "gtk-dark.css")
    uri = (theme / "gtk-dark.css").as_uri()
    (user / "gtk.css").write_text(f'/* @import url("{uri}"); */\n')
    assert imported_variant(tmp_path) is None  # commented out


def test_variant_css_keeps_the_users_own_rules_on_top(tmp_path):
    from stickynotes.theme import imported_variant
    theme, user = _theme_dirs(tmp_path, "gtk-dark.css")
    (theme / "gtk-dark.css").write_text("")
    (theme / "gtk.css").write_text("")
    dark_uri = (theme / "gtk-dark.css").as_uri()
    (user / "gtk.css").write_text(
        f'@import url("{dark_uri}");\n'
        'window.maximized { border-radius: 20px; }\n'
        '.logo { background-image: url("img/logo.png"); }\n'
        '.web { background-image: url("https://example.org/a.png"); }\n'
        '.abs { background-image: url("/usr/share/pixmaps/a.png"); }\n')
    css = imported_variant(tmp_path).css()
    light = css.index(f'@import url("{(theme / "gtk.css").as_uri()}");')
    assert light == 0 and dark_uri not in css  # variant first, dark import gone
    assert css.index("border-radius: 20px") > light  # user rule after it, so it wins
    assert f'url("{(user / "img" / "logo.png").as_uri()}")' in css  # relative url rebased
    assert 'url("https://example.org/a.png")' in css
    assert 'url("file:///usr/share/pixmaps/a.png")' in css  # absolute path made a URI


def test_theme_variant_attaches_only_the_mismatched_variant(tmp_path):
    from stickynotes.theme import ThemeVariant
    theme, user = _theme_dirs(tmp_path, "gtk-dark.css")
    (theme / "gtk-dark.css").write_text("")
    (theme / "gtk.css").write_text("")
    attached, loads = [], []

    def load(css):
        loads.append(css)
        return css.splitlines()[0]
    tv = ThemeVariant(tmp_path, attached.append, attached.remove, load=load, monitor=False)
    light = f'@import url("{(theme / "gtk.css").as_uri()}");'
    dark = f'@import url("{(theme / "gtk-dark.css").as_uri()}");'
    tv.update(True)
    assert attached == []  # desktop already dark
    tv.update(False)
    assert attached == [light]
    tv.update(True)
    tv.update(False)
    assert attached == [light] and len(loads) == 1  # parsed once
    _theme_dirs(tmp_path, "gtk.css")  # desktop switched to its light variant
    tv.reload()
    assert attached == []
    tv.update(True)
    assert attached == [dark]
    (user / "gtk.css").write_text("")
    tv.reload()
    assert attached == []


def test_theme_variant_reloads_once_after_gtk_css_changes(tmp_path):
    from gi.repository import Gio, GLib
    from stickynotes.theme import ThemeVariant
    theme, user = _theme_dirs(tmp_path, "gtk-dark.css")
    (theme / "gtk-dark.css").write_text("")
    (theme / "gtk.css").write_text("")
    attached, loads = [], []
    tv = ThemeVariant(tmp_path, attached.append, attached.remove,
                      load=lambda css: loads.append(css) or css, monitor=False)
    tv.RELOAD_DELAY_MS = 0
    tv.update(False)
    assert len(attached) == 1
    _theme_dirs(tmp_path, "gtk.css")  # atomic rewrite: CREATED, then CHANGES_DONE_HINT
    E = Gio.FileMonitorEvent
    tv._on_changed(None, None, None, E.CHANGED)  # ignored: not finished yet
    assert not tv._reload_source
    tv._on_changed(None, None, None, E.CREATED)
    tv._on_changed(None, None, None, E.CHANGES_DONE_HINT)
    while tv._reload_source:
        GLib.MainContext.default().iteration(True)
    assert attached == [] and len(loads) == 1  # one reload; light desktop, light app
    tv.update(True)
    assert len(loads) == 2


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


def test_autostart_entry_from_old_app_id_is_renamed(tmp_path):
    out = run_isolated(tmp_path, """
        from stickynotes import integration as i
        old = i.autostart_file().with_name(i.OLD_APP_ID + ".desktop")
        old.parent.mkdir(parents=True)
        old.write_text("[Desktop Entry]\\nExec=dusk-sticky-notes --restore\\n")
        assert i.autostart_enabled()
        assert not old.exists()
        print(i.autostart_file().read_text())
        old.write_text("x")
        i.set_autostart(False)
        assert not old.exists() and not i.autostart_enabled()
    """)
    assert "Icon=io.github.phlthydelphya.StickyNotes" in out
    assert "--restore" in out


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
                          set_close_to_tray=lambda enabled: False,
                          set_style=lambda style: styles.append(style))
    styles = []
    dialog = p.PreferencesDialog(app)
    for row in (dialog.autostart_row, dialog.shortcut_row):
        row.set_active(True)
        assert not row.get_active()
    dialog.tray_row.set_active(False)  # app kept True: switch follows the app
    assert dialog.tray_row.get_active()
    assert dialog.style_row.get_selected() == 0
    dialog.style_row.set_selected(1)
    assert styles == ["light"]


class FakePortalBus:
    """Records the Background request and replays one Response signal."""

    def __init__(self, response=None, fail=False):
        self.response, self.fail = response, fail
        self.calls, self.handlers, self.unsubscribed = [], {}, []

    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, sender, iface, member, path, arg0, flags, callback):
        self.handlers[path] = callback
        return 7

    def signal_unsubscribe(self, sub):
        self.unsubscribed.append(sub)

    def call_sync(self, name, path, iface, method, params, reply_type, flags, timeout, cancel):
        from gi.repository import GLib
        self.calls.append((iface, method, params.unpack()))
        if self.fail:
            raise GLib.Error("no portal")
        if self.response is not None:
            handle = next(iter(self.handlers))
            self.handlers[handle](self, None, handle, None, "Response",
                                  GLib.Variant("(ua{sv})", self.response))


def flatpak_integration(tmp_path, monkeypatch):
    from stickynotes import integration
    marker = tmp_path / "autostart" / f"{integration.APP_ID}.desktop"
    marker.parent.mkdir()
    marker.write_text("x")
    monkeypatch.setattr(integration, "in_flatpak", lambda: True)
    monkeypatch.setattr(integration, "autostart_file", lambda: marker)
    return integration, marker


def test_background_portal_request_and_grant(tmp_path, monkeypatch):
    from gi.repository import GLib
    i, marker = flatpak_integration(tmp_path, monkeypatch)
    bus = FakePortalBus(response=(0, {"autostart": GLib.Variant("b", True)}))
    i.request_background(True, conn=bus)
    (iface, method, (parent, options)), = bus.calls
    assert (iface, method, parent) == (i.BACKGROUND, "RequestBackground", "")
    assert options["autostart"] is True
    assert options["commandline"] == ["dusk-sticky-notes", "--restore"]
    token = options["handle_token"]
    assert list(bus.handlers) == [f"{i.PORTAL_PATH}/request/1_42/{token}"]
    assert bus.unsubscribed == [7] and marker.exists()


def test_background_portal_refusal_clears_the_switch(tmp_path, monkeypatch):
    from gi.repository import GLib
    i, marker = flatpak_integration(tmp_path, monkeypatch)
    i.request_background(True, conn=FakePortalBus(response=(1, {})))
    assert not marker.exists()
    marker.write_text("x")
    i.request_background(True, conn=FakePortalBus(response=(0, {"autostart": GLib.Variant("b", False)})))
    assert not marker.exists()
    marker.write_text("x")
    bus = FakePortalBus(fail=True)
    i.request_background(True, conn=bus)
    assert not marker.exists() and bus.unsubscribed == [7]


def test_flatpak_has_no_shortcut_and_reads_the_host_config(monkeypatch):
    from stickynotes import integration as i
    monkeypatch.setattr(i, "in_flatpak", lambda: True)
    assert not i.shortcut_supported() and not i.shortcut_enabled()
    assert not i.set_shortcut(True)
    monkeypatch.setenv("HOST_XDG_CONFIG_HOME", "/home/u/.config")
    assert i.host_config_dir() == "/home/u/.config"
    monkeypatch.delenv("HOST_XDG_CONFIG_HOME")
    assert i.host_config_dir() == str(Path.home() / ".config")
