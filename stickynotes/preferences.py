# SPDX-License-Identifier: GPL-3.0-or-later
"""App preferences: stored ones (settings.json) and the Preferences dialog."""

import json
import os
import sys
from pathlib import Path

from gi.repository import Adw, GLib

from . import integration

DEFAULTS = {"close_to_tray": True}


class Preferences:
    def __init__(self, root=None):
        self.file = Path(root or Path(GLib.get_user_config_dir()) / "dusk-sticky-notes") / "settings.json"
        self.values = dict(DEFAULTS)

    def load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self
        if isinstance(data, dict):
            for key, default in DEFAULTS.items():
                if type(data.get(key)) is type(default):
                    self.values[key] = data[key]
        return self

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.values, indent=1), encoding="utf-8")
        os.replace(tmp, self.file)

    @property
    def close_to_tray(self):
        return self.values["close_to_tray"]


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title="Preferences")
        self.app = app
        page = Adw.PreferencesPage()

        background = Adw.PreferencesGroup(title="Background")
        self.tray_row = Adw.SwitchRow(title="Keep running in the tray when closed",
                                      active=app.prefs.close_to_tray)
        self.refresh_tray()
        self._connect(self.tray_row, app.set_close_to_tray, lambda: app.prefs.close_to_tray,
                      "The choice applies now but could not be saved for next time")
        background.add(self.tray_row)

        self.autostart_row = Adw.SwitchRow(title="Start at login",
                                           subtitle="Reopen your notes when you sign in",
                                           active=integration.autostart_enabled())
        self._connect(self.autostart_row, self._set_autostart, integration.autostart_enabled,
                      "Could not change the login item")
        background.add(self.autostart_row)
        page.add(background)

        keys = Adw.PreferencesGroup(title="Keyboard")
        self.shortcut_row = Adw.SwitchRow(
            title="New note from anywhere",
            subtitle=f"{integration.SHORTCUT_LABEL} creates a note, even when "
                     "Sticky Notes is not focused",
            active=integration.shortcut_enabled(),
            sensitive=integration.shortcut_supported())
        self._connect(self.shortcut_row, integration.set_shortcut, integration.shortcut_enabled,
                      "Could not change the keyboard shortcut")
        keys.add(self.shortcut_row)
        page.add(keys)
        self.add(page)

    def _connect(self, row, apply, current, error):
        """Run apply(active) on toggle. When it fails, show error and put the
        switch back to current(), so it never claims a state that is not real."""
        def on_active(row, _pspec):
            try:
                ok = apply(row.get_active()) is not False
            except (OSError, GLib.Error) as err:
                print(f"dusk-sticky-notes: {error}: {err}", file=sys.stderr)
                ok = False
            if ok:
                return
            self.add_toast(Adw.Toast(title=error))
            actual = current()
            if row.get_active() != actual:
                row.handler_block(handler)
                row.set_active(actual)
                row.handler_unblock(handler)
        handler = row.connect("notify::active", on_active)

    @staticmethod
    def _set_autostart(enabled):
        integration.set_autostart(enabled)
        return integration.autostart_enabled() == enabled

    def refresh_tray(self):
        if self.app.tray is None or not self.app.tray.available:
            self.tray_row.set_subtitle("No tray in this session: closing the last window quits")
        else:
            self.tray_row.set_subtitle("Closing windows leaves Sticky Notes in the top bar")
