# SPDX-License-Identifier: GPL-3.0-or-later
"""Rich text editing on a GtkTextView: inline formatting, lists, images, (de)serialization."""

import re

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

TAGS = ("bold", "italic", "underline", "strike")
BULLET = "• "
CHECK_OPEN = "☐ "
CHECK_DONE = "☑ "
NUMBER = re.compile(r"(\d{1,4})\. ")
LIST_STYLES = ("bullet", "number", "check")
IMAGE_MAX_WIDTH = 260
# Typed at the start of a line, then a space: becomes that list marker.
TYPED_MARKERS = {"- ": BULLET, "* ": BULLET, "[] ": CHECK_OPEN, "[ ] ": CHECK_OPEN,
                 "[x] ": CHECK_DONE, "[X] ": CHECK_DONE}
# Web and mail addresses, and absolute or home-relative file paths.
LINK = re.compile(
    r"(?:https?://|www\.)[^\s<>\"']*[^\s<>\"'.,;:!?)\]}]"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}"
    r"|(?<![\w/~.:])~?/[\w.-]+(?:/[\w.-]+)*/?(?<![.,;:])")


def line_prefix(text):
    """List marker at the start of a line: (style or None, marker length, number).

    Lists are plain text markers, so notes stay readable anywhere and the
    saved format does not change."""
    if text.startswith(BULLET):
        return "bullet", len(BULLET), 0
    if text.startswith((CHECK_OPEN, CHECK_DONE)):
        return "check", len(CHECK_OPEN), 0
    match = NUMBER.match(text)
    if match:
        return "number", match.end(), int(match.group(1))
    return None, 0, 0


def marker(style, number=1):
    if style == "number":
        return f"{number}. "
    return CHECK_OPEN if style == "check" else BULLET


def checklist_progress(text):
    """(ticked, total) checklist items in plain text."""
    lines = text.splitlines()
    total = sum(line.startswith((CHECK_OPEN, CHECK_DONE)) for line in lines)
    return sum(line.startswith(CHECK_DONE) for line in lines), total


def link_target(text):
    """What opening a detected link opens: a URI, or a local path."""
    if text.startswith(("/", "~")):
        return text
    if "://" in text:
        return text
    if text.startswith("www."):
        return "https://" + text
    return "mailto:" + text


def install_tags(buffer):
    table = buffer.get_tag_table()
    if table.lookup("bold"):
        return
    buffer.create_tag("bold", weight=Pango.Weight.BOLD)
    buffer.create_tag("italic", style=Pango.Style.ITALIC)
    buffer.create_tag("underline", underline=Pango.Underline.SINGLE)
    buffer.create_tag("strike", strikethrough=True)
    # Not in TAGS: links are found again from the text, never saved.
    buffer.create_tag("link", underline=Pango.Underline.SINGLE)


def serialize(buffer, anchor_files):
    """Buffer -> list of segments. anchor_files maps TextChildAnchor -> image filename."""
    segs, text, cur = [], [], None

    def flush():
        if text:
            segs.append({"t": "".join(text), "s": list(cur)})
            text.clear()

    it = buffer.get_start_iter()
    while not it.is_end():
        anchor = it.get_child_anchor()
        if anchor is not None:
            flush()
            name = anchor_files.get(anchor)
            if name:
                segs.append({"img": name})
        else:
            tags = sorted(t.props.name for t in it.get_tags() if t.props.name in TAGS)
            if tags != cur:
                flush()
                cur = tags
            text.append(it.get_char())
        it.forward_char()
    flush()
    return segs


def deserialize(buffer, segs, insert_image):
    """Fill buffer from segments. insert_image(iter, filename) handles image segments."""
    buffer.set_text("")
    for seg in segs:
        end = buffer.get_end_iter()
        if "img" in seg:
            insert_image(end, seg["img"])
        elif seg.get("t"):
            tags = [s for s in seg.get("s", []) if s in TAGS]
            buffer.insert_with_tags_by_name(end, seg["t"], *tags)


class RichEditor(GObject.Object):
    """Owns formatting state for one text view."""

    __gsignals__ = {
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "content-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "link-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, view, resolve_image_path):
        super().__init__()
        self.view = view
        self.buf = view.get_buffer()
        self._resolve = resolve_image_path
        self.anchor_files = {}
        self.typing = set()
        self._loading = False
        self._links_source = 0
        install_tags(self.buf)
        self.link_tag = self.buf.get_tag_table().lookup("link")
        self.buf.connect_after("insert-text", self._on_insert_text)
        self.buf.connect("changed", self._on_changed)
        self.buf.connect("mark-set", self._on_mark_set)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        view.add_controller(keys)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_click)
        view.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        view.add_controller(motion)
        view.connect("destroy", lambda *_: self._cancel_links())

    # --- load / save ---------------------------------------------------------
    def load(self, segs):
        self._loading = True
        self.anchor_files.clear()
        deserialize(self.buf, segs, self.insert_image)
        self._loading = False
        self.buf.place_cursor(self.buf.get_end_iter())
        self.mark_links()

    def dump(self):
        return serialize(self.buf, self.anchor_files)

    def _on_changed(self, _buf):
        if not self._loading:
            self.emit("content-changed")
            if not self._links_source:
                self._links_source = GLib.idle_add(self._links_idle)

    # --- links -------------------------------------------------------------------
    def _links_idle(self):
        self._links_source = 0
        self.mark_links()
        return GLib.SOURCE_REMOVE

    def _cancel_links(self):
        if self._links_source:
            GLib.source_remove(self._links_source)
            self._links_source = 0

    def mark_links(self):
        """Underline every address and path in the text (notes are short: rescan all)."""
        start, end = self.buf.get_bounds()
        self.buf.remove_tag(self.link_tag, start, end)
        text = self.buf.get_slice(start, end, True)  # offsets match the buffer
        for match in LINK.finditer(text):
            self.buf.apply_tag(self.link_tag, self.buf.get_iter_at_offset(match.start()),
                               self.buf.get_iter_at_offset(match.end()))

    def link_at(self, it):
        """The link text around it, or None."""
        if not it.has_tag(self.link_tag):
            return None
        start, end = it.copy(), it.copy()
        if not start.starts_tag(self.link_tag):
            start.backward_to_tag_toggle(self.link_tag)
        end.forward_to_tag_toggle(self.link_tag)
        return self.buf.get_text(start, end, False)

    def _iter_at_point(self, x, y):
        bx, by = self.view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        found, it = self.view.get_iter_at_location(bx, by)
        return it if found else None

    def _on_motion(self, _controller, x, y):
        it = self._iter_at_point(x, y)
        over = it is not None and it.has_tag(self.link_tag)
        self.view.set_cursor_from_name("pointer" if over else "text")
        self.view.set_tooltip_text("Ctrl+click to open" if over else None)

    # --- typing formatting -----------------------------------------------------
    def _on_insert_text(self, buf, location, text, _length):
        if self._loading:
            return
        if self.typing:
            mark = buf.create_mark(None, location, False)
            start = location.copy()
            start.backward_chars(len(text))
            for name in self.typing:
                buf.apply_tag_by_name(name, start, location)
            # Tag changes invalidate iterators; hand GTK back a valid one.
            location.assign(buf.get_iter_at_mark(mark))
            buf.delete_mark(mark)
        if text == " ":
            self._typed_marker(buf, location)

    def _typed_marker(self, buf, location):
        """"- ", "* ", "[] " or "[x] " typed at a line start becomes a list marker."""
        start = location.copy()
        start.set_line_offset(0)
        marker_text = TYPED_MARKERS.get(buf.get_slice(start, location, True))
        if marker_text is None:
            return
        mark = buf.create_mark(None, location, False)
        buf.delete(start, location)
        buf.insert(start, marker_text)
        location.assign(buf.get_iter_at_mark(mark))
        buf.delete_mark(mark)

    def _on_mark_set(self, buf, it, mark):
        if self._loading or mark != buf.get_insert():
            return
        if not buf.get_has_selection():
            prev = it.copy()
            if prev.backward_char():
                self.typing = {t.props.name for t in prev.get_tags() if t.props.name in TAGS}
        self.emit("state-changed")

    def is_active(self, name):
        bounds = self.buf.get_selection_bounds()
        if not bounds:
            return name in self.typing
        start, end = bounds
        tag = self.buf.get_tag_table().lookup(name)
        if not start.has_tag(tag):
            return False
        it = start.copy()
        it.forward_to_tag_toggle(tag)
        return it.compare(end) >= 0

    def toggle(self, name):
        bounds = self.buf.get_selection_bounds()
        if bounds:
            start, end = bounds
            if self.is_active(name):
                self.buf.remove_tag_by_name(name, start, end)
            else:
                self.buf.apply_tag_by_name(name, start, end)
            self.emit("content-changed")  # tag changes do not emit "changed"
        else:
            self.typing ^= {name}
        self.emit("state-changed")

    # --- lists -----------------------------------------------------------------
    def _line_start(self, line):
        _ok, it = self.buf.get_iter_at_line(line)
        return it

    def _prefix(self, line):
        start = self._line_start(line)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        return line_prefix(self.buf.get_text(start, end, False))

    def _replace_prefix(self, line, length, text):
        start = self._line_start(line)
        if length:
            end = start.copy()
            end.forward_chars(length)
            self.buf.delete(start, end)
            start = self._line_start(line)
        if text:
            self.buf.insert(start, text)

    def _selected_lines(self):
        bounds = self.buf.get_selection_bounds()
        if bounds:
            start, end = bounds
        else:
            start = end = self.buf.get_iter_at_mark(self.buf.get_insert())
        return range(start.get_line(), end.get_line() + 1)

    def list_style(self):
        """The list style every selected line has, else None."""
        styles = {self._prefix(n)[0] for n in self._selected_lines()}
        return styles.pop() if len(styles) == 1 else None

    def bullets_active(self):
        return self.list_style() == "bullet"

    def set_list(self, style):
        """Make the selected lines a list of style; if they already are one, unlist them."""
        lines = self._selected_lines()
        remove = self.list_style() == style
        number = 1
        if lines.start > 0:
            before, _len, num = self._prefix(lines.start - 1)
            if before == "number":
                number = num + 1  # continue the list above
        self.buf.begin_user_action()
        for n in lines:
            _style, length, _num = self._prefix(n)
            self._replace_prefix(n, length, "" if remove else marker(style, number))
            number += 1
        self._renumber(lines.stop)
        self.buf.end_user_action()
        self.emit("state-changed")

    def toggle_bullets(self):
        self.set_list("bullet")

    def _renumber(self, line):
        """Number the numbered lines from line on, following the line above."""
        prev_style, _len, prev = self._prefix(line - 1) if line > 0 else (None, 0, 0)
        expected = prev + 1 if prev_style == "number" else 1
        while line < self.buf.get_line_count():
            style, length, num = self._prefix(line)
            if style != "number":
                break
            if num != expected:
                self._replace_prefix(line, length, marker("number", expected))
            expected += 1
            line += 1

    def _is_done(self, line):
        start = self._line_start(line)
        return start.get_char() == CHECK_DONE[0] and self._prefix(line)[0] == "check"

    def toggle_check(self, line):
        """Tick or untick an item. A ticked item sinks below the open ones in its
        checklist; an unticked one rises back above the ticked ones."""
        style, length, _num = self._prefix(line)
        if style != "check":
            return
        done = self._is_done(line)
        self.buf.begin_user_action()
        self._replace_prefix(line, length, CHECK_OPEN if done else CHECK_DONE)
        first = last = line
        while first > 0 and self._prefix(first - 1)[0] == "check":
            first -= 1
        while last + 1 < self.buf.get_line_count() and self._prefix(last + 1)[0] == "check":
            last += 1
        others = [n for n in range(first, last + 1) if n != line]
        if not done:  # now ticked: just below the last open item
            open_below = [n for n in others if n > line and not self._is_done(n)]
            if open_below:
                self._move_line(line, open_below[-1])
        else:  # now open: above every ticked item
            ticked = [n for n in others if self._is_done(n)]
            if ticked and ticked[0] < line:
                self._move_line(line, ticked[0])
        self.buf.end_user_action()

    def _move_line(self, src, dst):
        """Move line src, formatting included, so it becomes line dst."""
        start = self._line_start(src)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        if "\ufffc" in self.buf.get_slice(start, end, True):
            return  # holds an image: leave it where it is
        first = self.buf.create_mark(None, start, True)
        last = self.buf.create_mark(None, end, False)
        at = self._line_start(dst)
        if dst > src:  # after line dst: "\n" + copy
            if not at.ends_line():
                at.forward_to_line_end()
            spot = self.buf.create_mark(None, at, False)
            self.buf.insert(at, "\n")
            self.buf.insert_range(self.buf.get_iter_at_mark(spot),
                                  self.buf.get_iter_at_mark(first), self.buf.get_iter_at_mark(last))
        else:  # before line dst: copy + "\n"
            spot = self.buf.create_mark(None, at, False)
            self.buf.insert_range(at, self.buf.get_iter_at_mark(first), self.buf.get_iter_at_mark(last))
            self.buf.insert(self.buf.get_iter_at_mark(spot), "\n")
        self.buf.delete_mark(spot)
        cut_start = self.buf.get_iter_at_mark(first)
        cut_end = self.buf.get_iter_at_mark(last)
        if cut_end.is_end():
            cut_start.backward_char()  # the last line: take the newline before it
        else:
            cut_end.forward_char()  # and its own newline
        self.buf.delete(cut_start, cut_end)
        self.buf.delete_mark(first)
        self.buf.delete_mark(last)

    def _on_click(self, gesture, _n_press, x, y):
        it = self._iter_at_point(x, y)
        if it is None:
            return
        if gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            link = self.link_at(it)
            if link:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.emit("link-activated", link_target(link))
                return
        if it.get_line_offset() == 0 and it.get_char() in (CHECK_OPEN[0], CHECK_DONE[0]):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.toggle_check(it.get_line())

    def _on_key_pressed(self, _ctrl, keyval, _code, state):
        if keyval not in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
                    | Gdk.ModifierType.ALT_MASK):
            return False
        if self.buf.get_has_selection():
            return False
        cursor = self.buf.get_iter_at_mark(self.buf.get_insert())
        line = cursor.get_line()
        style, length, number = self._prefix(line)
        if style is None or cursor.get_line_offset() < length:
            return False  # not a list, or before the marker: a plain new line
        start = self._line_start(line)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        self.buf.begin_user_action()
        if not self.buf.get_text(start, end, False)[length:].strip():
            # Enter on an empty item ends the list.
            self._replace_prefix(line, length, "")
            self._renumber(line + 1)
        else:
            self.buf.insert_at_cursor("\n" + marker(style, number + 1))
            if style == "number":
                self._renumber(line + 2)
        self.buf.end_user_action()
        self.view.scroll_mark_onscreen(self.buf.get_insert())
        return True

    # --- images ----------------------------------------------------------------
    def insert_image(self, it, name):
        path = self._resolve(name)
        try:
            texture = Gdk.Texture.new_from_filename(str(path))
        except Exception:
            # Unreadable now: keep a placeholder so the note still references
            # the file and saving does not drop it for good.
            picture = Gtk.Image(icon_name="image-missing-symbolic", pixel_size=48)
        else:
            width, height = texture.get_width(), texture.get_height()
            if width > IMAGE_MAX_WIDTH:
                height = max(1, round(height * IMAGE_MAX_WIDTH / width))
                width = IMAGE_MAX_WIDTH
            picture = Gtk.Picture.new_for_paintable(texture)
            picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            picture.set_size_request(width, height)
            picture.add_css_class("note-image")
        anchor = self.buf.create_child_anchor(it)
        self.anchor_files[anchor] = name
        self.view.add_child_at_anchor(picture, anchor)

    def insert_image_at_cursor(self, name):
        it = self.buf.get_iter_at_mark(self.buf.get_insert())
        self.buf.begin_user_action()
        if not it.starts_line():
            self.buf.insert(it, "\n")
        self.insert_image(it, name)
        self.buf.insert(it, "\n")
        self.buf.end_user_action()
