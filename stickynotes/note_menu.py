# SPDX-License-Identifier: GPL-3.0-or-later
"""The per-note menu, shared by the list cards and popped-out note windows:
colors, pin and the host's own items. Built fresh on every open."""

from gi.repository import Gio, GLib, Gtk

from .palette import COLORS, label

UNTARGETED = {"app.show-list", "app.about", "app.new-note"}


def _item(text, action, target):
    item = Gio.MenuItem.new(text, None)
    item.set_action_and_target_value(action, target)
    return item


def build_menu(note, extra):
    """Gio.Menu for note. extra: [(label, action)] host items; an "app." action
    gets the note id as its target, any other is used as it is.
    The colors row is a custom item, "colors"; fill it with color_row()."""
    nid = GLib.Variant("s", note.id)
    menu = Gio.Menu()
    colors = Gio.MenuItem.new(None, None)
    colors.set_attribute_value("custom", GLib.Variant("s", "colors"))
    section = Gio.Menu()
    section.append_item(colors)
    menu.append_section(None, section)

    section = Gio.Menu()
    section.append_item(_item("Unpin" if note.pinned else "Pin to Top", "app.toggle-pin", nid))
    menu.append_section(None, section)

    section = Gio.Menu()
    for text, action in extra:
        if action.startswith("app.") and action not in UNTARGETED:
            section.append_item(_item(text, action, nid))
        else:
            section.append(text, action)
    menu.append_section(None, section)
    return menu


def color_row(note, on_color):
    """Round color buttons; on_color(color) runs on a click."""
    box = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=2, margin_bottom=2,
                  halign=Gtk.Align.CENTER)
    for color in COLORS:
        mark = Gtk.Image(icon_name="object-select-symbolic" if color == note.color else None)
        btn = Gtk.Button(child=mark, tooltip_text=label(color))
        btn.add_css_class("swatch")
        btn.add_css_class(color)
        btn.update_property([Gtk.AccessibleProperty.LABEL], [label(color)])
        btn.connect("clicked", lambda _b, c=color: on_color(c))
        box.append(btn)
    return box


def popover(note, extra, on_color):
    """A PopoverMenu with the color row in place; it closes after a color is picked."""
    menu = Gtk.PopoverMenu(menu_model=build_menu(note, extra), has_arrow=False)
    menu.add_css_class("note-menu")

    def picked(color):
        menu.popdown()
        on_color(color)
    menu.add_child(color_row(note, picked), "colors")
    return menu
