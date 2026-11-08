# SPDX-License-Identifier: GPL-3.0-or-later
"""Stroke glyphs and everything measured or drawn with them.

A .kicad_pcb only says 'write this text' and KiCad draws it, but a Gerber needs the actual lines.
So the Gerbers and the PDF are drawn from fonts/newstroke.txt, the table compiled into KiCad, and they
carry the glyphs that KiCad draws, and that its DRC checks, for the .kicad_pcb.

The file has a comment on its first line and then one glyph per line, in code point order from
U+0020. In a glyph every two characters are a pair: the first pair holds the left and right limits,
which give the advance, and after it each pair is a point, or ' R' to lift the pen. A coordinate is
the character's distance from 'R', in units of 1/21 of the text size, with y down.
"""

import sys
from functools import lru_cache
from pathlib import Path

FONT_PATH = Path(__file__).resolve().parent.parent / "fonts" / "newstroke.txt"
FONT_UNIT = 1 / 21
# Text is vertically centred on its origin. KiCad's centring depends on the line width, so this is
# measured for size 1 and line width 0.15, within 0.2 um of KiCad. Other sizes scale it.
FONT_Y_SHIFT = 0.026248
# What the table holds where it has no glyph: an empty entry, or a box as a visible placeholder.
NO_GLYPH = {"RR", "F^K[KFYFY[K["}


@lru_cache(maxsize=1)
def table():
    """All the glyph strings. The file is only read when text is written."""
    try:
        lines = FONT_PATH.read_text(encoding="ascii").rstrip("\n").split("\n")
    except OSError:
        sys.exit(f"Cannot read {FONT_PATH}. Keep the 'fonts' folder next to the script.")
    return lines[1:]


def has_glyph(char):
    """Say whether the font can draw a character."""
    index = ord(char) - 0x20
    return 0 <= index < len(table()) and table()[index] not in NO_GLYPH


@lru_cache(maxsize=None)
def glyph(char):
    """(advance, polylines) of a character for a text size of 1, relative to the pen. A polyline is
    a list of (x, y)."""
    data = table()[ord(char) - 0x20]
    left, right = ord(data[0]) - ord("R"), ord(data[1]) - ord("R")
    polylines = []
    current = []
    for i in range(2, len(data), 2):
        if data[i:i + 2] == " R":
            polylines.append(current)
            current = []
        else:
            current.append(((ord(data[i]) - ord("R") - left) * FONT_UNIT,
                            (ord(data[i + 1]) - ord("R")) * FONT_UNIT + FONT_Y_SHIFT))
    polylines.append(current)
    return (right - left) * FONT_UNIT, [p for p in polylines if p]


def check_user_text(text):
    """The reason why a text cannot be written, or None. KiCad reads these four sequences as markup
    and would draw something other than the Gerbers."""
    unknown = sorted({c for c in text if not has_glyph(c)})
    if unknown:
        return "has characters the font has no glyph for: " + " ".join(f"{c!r} (U+{ord(c):04X})" for c in unknown)
    for markup in ("~{", "_{", "^{", "${"):
        if markup in text:
            return f"contains {markup}, which KiCad would read as formatting"
    return None


def text_width(text, size):
    """Advance of a text, as KiCad computes it to centre the text on its origin."""
    return sum(glyph(c)[0] for c in text) * size


def text_strokes(text, size):
    """Polylines of a text centred on the origin, as lists of (x, y) with y down."""
    pen = -text_width(text, size) / 2
    polylines = []
    for c in text:
        advance, strokes = glyph(c)
        for stroke in strokes:
            polylines.append([(pen + x * size, y * size) for x, y in stroke])
        pen += advance * size
    return polylines


def text_extent(text, size):
    """How far the ink of a text reaches above and below its origin, without the line width."""
    ys = [y for polyline in text_strokes(text, size) for _, y in polyline]
    return (-min(ys), max(ys)) if ys else (0.0, 0.0)
