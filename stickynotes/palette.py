"""Note colors. One table drives the note windows, the swatches and the list cards."""

# name: (label, light body, light header, dark body, dark header, accent strip)
PALETTE = {
    "yellow": ("Yellow", "#fff7d1", "#fff2ab", "#3b3722", "#544d2a", "#ffe66e"),
    "green": ("Green", "#e4f9e0", "#cbf1c4", "#22362a", "#2c4a35", "#a1ef9b"),
    "pink": ("Pink", "#ffe4f1", "#ffcce5", "#3f2635", "#59344b", "#ffafdf"),
    "purple": ("Purple", "#f2e6ff", "#e7cfff", "#332b44", "#463b5e", "#d7afff"),
    "blue": ("Blue", "#e2f1ff", "#cde9ff", "#22344a", "#2d4863", "#9edfff"),
    "gray": ("Gray", "#f3f2f1", "#e1dfdd", "#2e2e2e", "#3d3d3d", "#c8c6c4"),
    "charcoal": ("Charcoal", "#3a3a3a", "#494745", "#1f1f1f", "#2a2a2a", "#767676"),
}

COLORS = tuple(PALETTE)
DEFAULT_COLOR = "yellow"

LIGHT_TEXT = "#f3f3f3"
DARK_TEXT = "#1f1f1f"


def label(color):
    return PALETTE[color][0]


def _rules(color, body, header, text):
    return f"""
window.sticky.{color} {{ background-color: {body}; color: {text}; }}
window.sticky.{color} headerbar {{ background-color: {header}; color: {text}; }}
.note-card.{color} {{ background-color: {body}; color: {text}; }}
"""


def build_css(dark):
    out = []
    for color, (_, lbody, lhead, dbody, dhead, strip) in PALETTE.items():
        if dark:
            body, head, text = dbody, dhead, LIGHT_TEXT
        else:
            body, head = lbody, lhead
            text = LIGHT_TEXT if color == "charcoal" else DARK_TEXT
        out.append(_rules(color, body, head, text))
        out.append(f".note-card.{color} {{ border-top-color: {strip}; }}\n")
        out.append(f".swatch.{color} {{ background-color: {head}; }}\n")
    return "".join(out)
