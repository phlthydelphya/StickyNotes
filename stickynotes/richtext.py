"""Rich text editing on a GtkTextView: inline formatting, bullets, images, (de)serialization."""

from gi.repository import Gdk, GObject, Gtk, Pango

TAGS = ("bold", "italic", "underline", "strike")
BULLET = "• "
IMAGE_MAX_WIDTH = 260


def install_tags(buffer):
    table = buffer.get_tag_table()
    if table.lookup("bold"):
        return
    buffer.create_tag("bold", weight=Pango.Weight.BOLD)
    buffer.create_tag("italic", style=Pango.Style.ITALIC)
    buffer.create_tag("underline", underline=Pango.Underline.SINGLE)
    buffer.create_tag("strike", strikethrough=True)


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
    }

    def __init__(self, view, resolve_image_path):
        super().__init__()
        self.view = view
        self.buf = view.get_buffer()
        self._resolve = resolve_image_path
        self.anchor_files = {}
        self.typing = set()
        self._loading = False
        install_tags(self.buf)
        self.buf.connect_after("insert-text", self._on_insert_text)
        self.buf.connect("changed", self._on_changed)
        self.buf.connect("mark-set", self._on_mark_set)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        view.add_controller(keys)

    # --- load / save ---------------------------------------------------------
    def load(self, segs):
        self._loading = True
        self.anchor_files.clear()
        deserialize(self.buf, segs, self.insert_image)
        self._loading = False
        self.buf.place_cursor(self.buf.get_end_iter())

    def dump(self):
        return serialize(self.buf, self.anchor_files)

    def _on_changed(self, _buf):
        if not self._loading:
            self.emit("content-changed")

    # --- typing formatting -----------------------------------------------------
    def _on_insert_text(self, buf, location, text, _length):
        if self._loading or not self.typing:
            return
        mark = buf.create_mark(None, location, False)
        start = location.copy()
        start.backward_chars(len(text))
        for name in self.typing:
            buf.apply_tag_by_name(name, start, location)
        # Tag changes invalidate iterators; hand GTK back a valid one.
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

    # --- bullets ---------------------------------------------------------------
    def _line_start(self, line):
        _ok, it = self.buf.get_iter_at_line(line)
        return it

    def _line_has_bullet(self, line):
        start = self._line_start(line)
        end = start.copy()
        end.forward_chars(len(BULLET))
        return self.buf.get_text(start, end, False) == BULLET

    def _selected_lines(self):
        bounds = self.buf.get_selection_bounds()
        if bounds:
            start, end = bounds
        else:
            start = end = self.buf.get_iter_at_mark(self.buf.get_insert())
        return range(start.get_line(), end.get_line() + 1)

    def bullets_active(self):
        return all(self._line_has_bullet(n) for n in self._selected_lines())

    def toggle_bullets(self):
        lines = self._selected_lines()
        remove = all(self._line_has_bullet(n) for n in lines)
        self.buf.begin_user_action()
        for n in lines:
            start = self._line_start(n)
            if remove:
                end = start.copy()
                end.forward_chars(len(BULLET))
                self.buf.delete(start, end)
            elif not self._line_has_bullet(n):
                self.buf.insert(start, BULLET)
        self.buf.end_user_action()
        self.emit("state-changed")

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
        if not self._line_has_bullet(line):
            return False
        start = self._line_start(line)
        end = start.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        if self.buf.get_text(start, end, False).strip() == BULLET.strip():
            # Enter on an empty bullet ends the list.
            self.buf.delete(start, end)
        else:
            self.buf.insert_at_cursor("\n" + BULLET)
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
