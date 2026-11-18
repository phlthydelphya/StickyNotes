# SPDX-License-Identifier: GPL-3.0-or-later
"""App style (follow system, light or dark) and the desktop theme's light variant.

A desktop theme such as Dusk installs one variant into ~/.config/gtk-4.0/gtk.css,
which GTK applies to every app at USER priority whatever the app's color scheme.
When the app shows light while that file imports the theme's gtk-dark.css, the
app loads the theme's own light gtk.css from the same folder above it, so the
chrome uses the theme's light tokens instead of dark ones on a light base.
"""

import re
from pathlib import Path

STYLES = ("system", "light", "dark")
LABELS = ("Follow system", "Light", "Dark")

_IMPORT = re.compile(r"""@import\s+url\(\s*["']?file://([^"')]+?)["']?\s*\)""")


def color_scheme(style):
    """The Adw.ColorScheme for a stored style name."""
    from gi.repository import Adw
    return {
        "light": Adw.ColorScheme.FORCE_LIGHT,
        "dark": Adw.ColorScheme.FORCE_DARK,
    }.get(style, Adw.ColorScheme.DEFAULT)


def light_variant(config_dir):
    """Path of the light gtk.css next to a gtk-dark.css that the user's gtk.css
    imports, or None when the user's theme is not a dark-only import."""
    try:
        text = (Path(config_dir) / "gtk-4.0" / "gtk.css").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for match in _IMPORT.finditer(text):
        dark = Path(match.group(1))
        if dark.name == "gtk-dark.css":
            light = dark.with_name("gtk.css")
            if light.is_file():
                return light
    return None
