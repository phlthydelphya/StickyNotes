# SPDX-License-Identifier: GPL-3.0-or-later
"""App preferences: stored ones (settings.json) and the Preferences dialog."""

import json
import os
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
        self._tray_subtitle()
        self.tray_row.connect("notify::active",
                              lambda row, _p: app.set_close_to_tray(row.get_active()))
        background.add(self.tray_row)

        autostart = Adw.SwitchRow(title="Start at login",
                                  subtitle="Reopen your notes when you sign in",
                                  active=integration.autostart_enabled())
        autostart.connect("notify::active",
                          lambda row, _p: integration.set_autostart(row.get_active()))
        background.add(autostart)
        page.add(background)

        keys = Adw.PreferencesGroup(title="Keyboard")
        shortcut = Adw.SwitchRow(title="New note from anywhere",
                                 subtitle=f"{integration.SHORTCUT_LABEL} creates a note, even when "
                                          "Sticky Notes is not focused",
                                 active=integration.shortcut_enabled(),
                                 sensitive=integration.shortcut_supported())
        shortcut.connect("notify::active",
                         lambda row, _p: integration.set_shortcut(row.get_active()))
        keys.add(shortcut)
        page.add(keys)
        self.add(page)

    def _tray_subtitle(self):
        if self.app.tray is None or not self.app.tray.available:
            self.tray_row.set_subtitle("No tray in this session: closing the last window quits")
        else:
            self.tray_row.set_subtitle("Closing windows leaves Sticky Notes in the top bar")
