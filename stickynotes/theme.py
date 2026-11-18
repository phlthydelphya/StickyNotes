# SPDX-License-Identifier: GPL-3.0-or-later
"""App style (follow system, light or dark) and the desktop theme's other variant.

A desktop theme installer (theme-unifier, for example, and the Dusk desktop,
still in development) puts one variant of a theme into ~/.config/gtk-4.0/gtk.css,
usually as an @import of the theme's gtk.css or gtk-dark.css. GTK applies that
file to every app at USER priority, whatever the app's color scheme.
When the app shows the other scheme than the one that file imports (light while
it imports gtk-dark.css, or dark while it imports gtk.css), the app loads the
theme's other variant from the same folder above USER priority, so the chrome
uses the theme's own tokens for the scheme on screen.
"""

import re
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

STYLES = ("system", "light", "dark")
LABELS = ("Follow system", "Light", "Dark")
VARIANTS = {"gtk.css": "gtk-dark.css", "gtk-dark.css": "gtk.css"}

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_IMPORT = re.compile(r"""@import\s+url\(\s*(["']?)(file://[^"')]+?)\1\s*\)\s*;?""")
_URL = re.compile(r"""url\(\s*(["']?)([^"')]+?)\1\s*\)""")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^/")


def color_scheme(style):
    """The Adw.ColorScheme for a stored style name."""
    return {
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(style, Adw.ColorScheme.DEFAULT)


def user_css(config_dir):
    return Path(config_dir) / "gtk-4.0" / "gtk.css"


def imported_variant(config_dir):
    """Variant for a theme variant that the user's gtk.css imports and whose
    sibling exists; None otherwise."""
    css = user_css(config_dir)
    try:
        text = css.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for match in _IMPORT.finditer(_COMMENT.sub("", text)):
        try:
            path = Path(GLib.filename_from_uri(match.group(2))[0])
        except GLib.Error:
            continue
        other = VARIANTS.get(path.name)
        if other is None or path.parent == css.parent:
            continue
        sibling = path.with_name(other)
        if sibling.is_file():
            rules = _rebase(text.replace(match.group(0), ""), css.parent)
            return Variant(path.name == "gtk-dark.css", sibling, rules)
    return None


class Variant(tuple):
    """(imported_is_dark, other variant's path, the user's own rules)."""

    def __new__(cls, imported_is_dark, other, user_rules):
        return super().__new__(cls, (imported_is_dark, other, user_rules))

    imported_is_dark = property(lambda self: self[0])
    other = property(lambda self: self[1])
    user_rules = property(lambda self: self[2])

    def css(self):
        """The other variant, then the user's own rules from gtk.css on top of it,
        as they are over the imported variant."""
        uri = GLib.filename_to_uri(str(self.other), None)
        return f'@import url("{uri}");\n{self.user_rules}'


def _rebase(text, base):
    """Make relative url()s absolute, since the rules no longer load from base."""
    def absolute(match):
        quote, ref = match.group(1), match.group(2)
        if _SCHEME.match(ref):
            return match.group(0)
        return f"url({quote}{GLib.filename_to_uri(str(base / ref), None)}{quote})"
    return _URL.sub(absolute, text)


def _css_provider(css):
    provider = Gtk.CssProvider()
    provider.load_from_string(css)
    return provider


class ThemeVariant:
    """Keeps the theme variant that matches the app's scheme attached.

    attach/detach add and remove a provider (for the display, at a priority
    between USER and the app's own CSS). The user's gtk.css is read again shortly
    after it changes, and each variant is parsed once while it stays current."""

    RELOAD_DELAY_MS = 250

    def __init__(self, config_dir, attach, detach, load=_css_provider, monitor=True):
        self.config_dir = config_dir
        self._attach, self._detach, self._load = attach, detach, load
        self._variant = imported_variant(config_dir)
        self._providers = {}
        self._current = None  # path of the attached variant
        self._dark = None
        self._monitor = None
        self._reload_source = 0
        if monitor:
            self._monitor = Gio.File.new_for_path(str(user_css(config_dir))).monitor_file(
                Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_changed)

    def update(self, dark):
        self._dark = dark
        want = None
        if self._variant is not None and self._variant.imported_is_dark != dark:
            want = self._variant.other
        if want == self._current:
            return
        if self._current is not None:
            self._detach(self._providers[self._current])
        self._current = want
        if want is not None:
            if want not in self._providers:
                self._providers[want] = self._load(self._variant.css())
            self._attach(self._providers[want])

    def reload(self):
        """Read the user's gtk.css again, e.g. after the desktop theme changed."""
        self._variant = imported_variant(self.config_dir)
        if self._current is not None:
            self._detach(self._providers[self._current])
            self._current = None
        self._providers.clear()  # the variant files or user rules may have changed
        if self._dark is not None:
            self.update(self._dark)

    def _on_changed(self, _monitor, _file, _other, event):
        # An atomic rewrite sends CREATED then CHANGES_DONE_HINT: reload once.
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.DELETED,
                     Gio.FileMonitorEvent.CREATED) and not self._reload_source:
            self._reload_source = GLib.timeout_add(self.RELOAD_DELAY_MS, self._on_reload_due)

    def _on_reload_due(self):
        self._reload_source = 0
        self.reload()
        return GLib.SOURCE_REMOVE
