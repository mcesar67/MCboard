# SPDX-License-Identifier: GPL-3.0-or-later
"""The printable drawing: the top side and the bottom side next to it.

A PDF is also plain text, apart from the compressed drawing. write_pdf() builds the file itself:
a few numbered objects (catalog, page tree, info, font, and a page and a drawing per page) and at the
end a table (xref) with the byte offset of each object, which is why the bytes are counted as they
are added.

The drawing is a list of operators that follow their arguments:

  x y m             move to a point              x y l       line to a point
  ... c             Bezier curve to a point      h           close the path
  S                 stroke the path              f           fill the path
  w                 line width                   1 J 1 j     round line ends and joins
  r g b rg / RG     fill / stroke colour         q ... Q     save / restore the graphics state
  a b c d e f cm    change coordinates           BT ... ET   a block of text
"""

import datetime
import math
import re
import textwrap
import zlib
from pathlib import Path

from .constants import (PDF_CAPTION_ASCENT, PDF_CAPTION_GAP, PDF_CAPTION_SIZE, PDF_COMMAND_ASCENT,
                        PDF_COMMAND_BOTTOM, PDF_COMMAND_GAP, PDF_COMMAND_LEADING, PDF_COMMAND_LINES, PDF_COMMAND_SIZE,
                        PDF_COPPER_RGB, PDF_DRILL_LINE,
                        PDF_HELVETICA_WIDTHS, PDF_LOCKED_TRIES, PDF_MARGIN, PDF_NOTE_DESCENT, PDF_NOTE_EDGE,
                        PDF_NOTE_LEADING, PDF_NOTE_SIZE, PDF_PAGE, PDF_SCALES, PDF_SIDE_GAP, LAYER_REPORT_TITLE)
from .geometry import circumcentre, is_counterclockwise


def pdf_number(v):
    """Format a number for a PDF content stream, with no trailing zeros."""
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def pdf_circle(x, y, r):
    """Path of a circle, as four Bezier curves."""
    k = 0.5522847 * r
    n = pdf_number
    return (f"{n(x + r)} {n(y)} m "
            f"{n(x + r)} {n(y + k)} {n(x + k)} {n(y + r)} {n(x)} {n(y + r)} c "
            f"{n(x - k)} {n(y + r)} {n(x - r)} {n(y + k)} {n(x - r)} {n(y)} c "
            f"{n(x - r)} {n(y - k)} {n(x - k)} {n(y - r)} {n(x)} {n(y - r)} c "
            f"{n(x + k)} {n(y - r)} {n(x + r)} {n(y - k)} {n(x + r)} {n(y)} c h")


def pdf_layer(board, primitives):
    """Page operators of the primitives of a layer, in fab coordinates."""
    n = pdf_number
    out = []
    width = None

    def stroke_width(w):
        """Set the line width, if it is not the current one."""
        nonlocal width
        if w != width:
            width = w
            out.append(f"{n(w)} w")

    for p in primitives:
        if p[0] == "flash":
            x, y = board.from_corner(p[1], p[2])
            out.append(pdf_circle(x, y, p[3] / 2) + " f")
        elif p[0] == "line":
            stroke_width(p[5])
            x1, y1 = board.from_corner(p[1], p[2])
            x2, y2 = board.from_corner(p[3], p[4])
            out.append(f"{n(x1)} {n(y1)} m {n(x2)} {n(y2)} l S")
        elif p[0] == "arc":
            stroke_width(p[4])
            start, mid, end = (board.from_corner(*q) for q in p[1:4])
            cx, cy = circumcentre(start, mid, end)
            r = math.hypot(start[0] - cx, start[1] - cy)
            a0 = math.atan2(start[1] - cy, start[0] - cx)
            a1 = math.atan2(end[1] - cy, end[0] - cx)
            if is_counterclockwise(start, mid, end):
                a1 += 2 * math.pi if a1 <= a0 else 0
            else:
                a1 -= 2 * math.pi if a1 >= a0 else 0
            steps = 24
            points = [(cx + r * math.cos(a0 + (a1 - a0) * i / steps),
                       cy + r * math.sin(a0 + (a1 - a0) * i / steps)) for i in range(steps + 1)]
            out.append(f"{n(points[0][0])} {n(points[0][1])} m "
                       + " ".join(f"{n(x)} {n(y)} l" for x, y in points[1:]) + " S")
        else:  # region
            points = [board.from_corner(*q) for q in p[1]]
            out.append(f"{n(points[0][0])} {n(points[0][1])} m "
                       + " ".join(f"{n(x)} {n(y)} l" for x, y in points[1:]) + " h f")
    return out


PDF_EDGE = 0.1  # the board edge line, which also counts for the fit


def pdf_fits(across, along):
    """Whether a drawing of this size on paper fits, across and along the long side of the page."""
    return across <= PDF_PAGE[0] - 2 * PDF_MARGIN and along <= PDF_PAGE[1] - 2 * PDF_MARGIN


# The long side of the board always goes along the long side of the page: at any scale, that is
# the orientation in which the board fits best.

def pdf_placement(width, height):
    """(scale, rotated) on the A4 page with the long side of the board along that of the page,
    or None when the board does not fit even at the smallest scale."""
    rotated = width > height
    across, along = (height, width) if rotated else (width, height)
    for scale in PDF_SCALES:
        if pdf_fits((across + PDF_EDGE) * scale, (along + PDF_EDGE) * scale):
            return scale, rotated
    return None


def pdf_joint_placement(width, height):
    """(scale, rotated, beside) for both sides on one page, or None when they do not fit together.
    The largest scale wins, and at the same scale a drawing that is not rotated."""
    w, h = (width + PDF_EDGE), (height + PDF_EDGE)
    for scale in PDF_SCALES:
        for rotated, beside, across, along in (
                (False, True, 2 * w * scale + PDF_SIDE_GAP, h * scale),
                (False, False, w * scale, 2 * h * scale + PDF_SIDE_GAP),
                (True, True, h * scale, 2 * w * scale + PDF_SIDE_GAP),
                (True, False, 2 * h * scale + PDF_SIDE_GAP, w * scale)):
            if pdf_fits(across, along):
                return scale, rotated, beside
    return None


def pdf_face(board, side, x, y, marks=None):
    """Page operators of one side of the board, with its lower left corner on the page at x, y. Each side
    is seen from its own side, so the text of the bottom one reads normally and its column 1 is on the
    right. marks holds more operators per side, in fab coordinates, to paint on top of everything."""
    a = board.args
    # Painted from the bottom up: copper, the silkscreen, the white drill, then the outline and the marks.
    if side == "B":
        ops = ["q", f"-1 0 0 1 {x + board.board_size()[0]:.6f} {y:.6f} cm"]
    else:
        ops = ["q", f"1 0 0 1 {x:.6f} {y:.6f} cm"]
    r, g, b = PDF_COPPER_RGB
    ops += [f"{r} {g} {b} rg", f"{r} {g} {b} RG"]
    ops += pdf_layer(board, board.art[f"{side}.Cu"])
    ops += ["0 0 0 RG 0 0 0 rg"]
    ops += pdf_layer(board, board.art[f"{side}.SilkS"])
    # The real copper is a ring: the Gerber's disc loses its centre when the board is drilled.
    # After the silkscreen, because the lines of a working copy go through the holes.
    ops += ["1 1 1 rg"]
    for hx, hy in board.pth:
        ops.append(pdf_circle(*board.from_corner(hx, hy), a.drill / 2) + " f")
    ops += ["0 0 0 RG 0 0 0 rg"]
    ops += pdf_layer(board, board.art["Edge.Cuts"])
    ops += [f"{pdf_number(PDF_DRILL_LINE)} w"]
    radius = a.mount_drill / 2
    for hx, hy in board.npth:
        px, py = board.from_corner(hx, hy)
        ops.append(pdf_circle(px, py, radius) + " S")
        n = pdf_number
        ops.append(f"{n(px - radius / 2)} {n(py)} m {n(px + radius / 2)} {n(py)} l S")
        ops.append(f"{n(px)} {n(py - radius / 2)} m {n(px)} {n(py + radius / 2)} l S")
    ops += (marks or {}).get(side, [])
    ops.append("Q")
    return ops


def pdf_page(board, faces, drawing_size, scale, rotated, caption, marks=None, command=None):
    """Page operators of the given faces, (side, x, y), which take up drawing_size in board mm, and of the
    command line, if given."""
    width, height = drawing_size
    mm = 72 / 25.4
    s = scale * mm
    page_w, page_h = PDF_PAGE[0] * mm, PDF_PAGE[1] * mm
    # The cm matrix (a, b, c, d, e, f) maps x, y to a x + c y + e, b x + d y + f. So from here on
    # the drawing can use board mm in fab coordinates: this handles scale, rotation and centring.
    if rotated:  # 90 degrees counterclockwise
        matrix = (0, s, -s, 0, page_w / 2 + s * height / 2, page_h / 2 - s * width / 2)
    else:
        matrix = (s, 0, 0, s, page_w / 2 - s * width / 2, page_h / 2 - s * height / 2)
    ops = ["q", " ".join(f"{v:.6f}" for v in matrix) + " cm", "1 J 1 j"]
    for side, x, y in faces:
        ops += pdf_face(board, side, x, y, marks)
    ops += ["0 0 0 rg"]
    # Below the drawing, so it reads along with it and never covers it. Board units, so dividing by
    # the scale gives the same size on paper at every scale.
    baseline = -(PDF_CAPTION_GAP + PDF_CAPTION_ASCENT * PDF_CAPTION_SIZE) / scale
    ops.append(f"BT /F1 {PDF_CAPTION_SIZE / scale:.4f} Tf 0 {baseline:.4f} Td "
               f"({caption}Scale: {scale:.0%}) Tj ET")
    # Above the drawing, for the same reasons. The last line is the one closest to it.
    across, along = (PDF_PAGE[0], PDF_PAGE[1]) if rotated else (PDF_PAGE[1], PDF_PAGE[0])
    lines = pdf_notes(board, across / 2 - scale * height / 2)
    # From the drawing's left edge, or further left if a line would pass the right margin.
    left = along / 2 - scale * width / 2
    start = max(PDF_MARGIN, min(left, along - PDF_MARGIN - max([note_width(x) for x in lines], default=0)))
    for i, line in enumerate(reversed(lines)):
        baseline = height + (PDF_CAPTION_GAP + PDF_NOTE_DESCENT * PDF_NOTE_SIZE
                             + i * PDF_NOTE_LEADING) / scale
        ops.append(f"BT /F1 {PDF_NOTE_SIZE / scale:.4f} Tf {pdf_number((start - left) / scale)} {baseline:.4f} Td "
                   f"({line}) Tj ET")
    ops.append("Q")
    if command:
        # Rotated, the bottom of the page is the drawing's left edge, where the caption and the notes start.
        # Otherwise the caption is the lowest thing on the page.
        if rotated:
            free = PDF_PAGE[1] / 2 - scale * width / 2
        else:
            free = (PDF_PAGE[1] / 2 - scale * height / 2 - PDF_CAPTION_GAP
                    - (PDF_CAPTION_ASCENT + PDF_NOTE_DESCENT) * PDF_CAPTION_SIZE)
        ops += pdf_command(command, free)
    return ops


def note_width(text):
    """Width of a line of notes on paper, in mm."""
    return sum(PDF_HELVETICA_WIDTHS[ord(c) - 32] if " " <= c <= "~" else 1000 for c in text) * PDF_NOTE_SIZE / 1000


def pdf_notes(board, room):
    """The lines above the drawing: the mounting holes, then the working copy's layers, if any, with room mm
    between the drawing and the page edge. The layers take three lines where there is room, else two, else one;
    the mounting holes never give way, as before the layers were added."""
    notes = board.mount_report()
    layers = board.layer_report()
    if not layers:
        return notes
    copper, user = (", ".join(part) for part in layers)
    layouts = [[LAYER_REPORT_TITLE, copper, user], [copper + ",", user], [copper + ", " + user]]
    for layout in layouts:
        n = len(notes) + len(layout)
        top = PDF_CAPTION_GAP + PDF_NOTE_DESCENT * PDF_NOTE_SIZE + (n - 1) * PDF_NOTE_LEADING
        if top + PDF_CAPTION_ASCENT * PDF_NOTE_SIZE <= room - PDF_NOTE_EDGE:
            return notes + layout
    return notes + layouts[-1]


def pdf_pages(board, marks=None, command=None):
    """(list of page operators, scale), or None when the board does not fit. Both sides share a page, very
    close together, or take one page each if they do not fit on one."""
    size = board.board_size()
    width, height = size
    single = pdf_placement(width, height)
    if single is None:
        return None
    joint = pdf_joint_placement(width, height)
    if joint is None:
        scale, rotated = single
        bottom = "Bottom side, seen from below. "
        return [pdf_page(board, [("F", 0, 0)], size, scale, rotated, "Top side. ", marks, command),
                pdf_page(board, [("B", 0, 0)], size, scale, rotated, bottom, marks, command)], scale
    scale, rotated, beside = joint
    gap = PDF_SIDE_GAP / scale
    if beside:
        faces = [("F", 0, 0), ("B", width + gap, 0)]
        drawing = 2 * width + gap, height
        caption = "Left: top side. Right: bottom side, seen from below. "
    else:
        faces = [("F", 0, height + gap), ("B", 0, 0)]
        drawing = width, 2 * height + gap
        caption = "Above: top side. Below: bottom side, seen from below. "
    return [pdf_page(board, faces, drawing, scale, rotated, caption, marks, command)], scale


def pdf_text(text):
    """Text as a PDF string body: ASCII, with its brackets and backslashes escaped."""
    text = text.encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_command(command, free):
    """Page operators of the command line, in Courier across the foot of the page, below everything else, which
    leaves free mm above the page's bottom edge. One line always fits there, and as many more as there is room
    for, up to PDF_COMMAND_LINES; a command that needs more is cut short with ...."""
    mm = 72 / 25.4
    columns = int((PDF_PAGE[0] - 2 * PDF_MARGIN) / (0.6 * PDF_COMMAND_SIZE))  # Courier advances 0.6 of its size
    room = free - PDF_COMMAND_GAP - PDF_COMMAND_BOTTOM - PDF_COMMAND_ASCENT * PDF_COMMAND_SIZE
    most = max(1, min(PDF_COMMAND_LINES, 1 + int(room // PDF_COMMAND_LEADING)))
    lines = []
    for unit in re.split(r" (?=-)", command):  # an option stays on one line with its value
        if lines and len(lines[-1]) + 1 + len(unit) <= columns:
            lines[-1] += " " + unit
        else:
            lines += textwrap.wrap(unit, columns, break_on_hyphens=False)
    if len(lines) > most:
        lines = lines[:most]
        lines[-1] = lines[-1][:-3] + "..."
    ops = ["0 0 0 rg"]
    for i, line in enumerate(reversed(lines)):
        y = (PDF_COMMAND_BOTTOM + i * PDF_COMMAND_LEADING) * mm
        ops.append(f"BT /F2 {PDF_COMMAND_SIZE * mm:.4f} Tf {PDF_MARGIN * mm:.4f} {y:.4f} Td ({pdf_text(line)}) Tj ET")
    return ops


def write_pdf(board, base, command=None):
    """Write <base>.pdf with the copper, silkscreen, mounting holes and board edge of both sides, and at the
    foot of each page the command that made it, if given. Returns (path, scale), or None when the board does
    not fit."""
    pages = pdf_pages(board, command=command)
    if pages is None:
        return None
    pages, scale = pages
    pdf_path = base.with_name(base.name + ".pdf")
    pdf_path = write_pdf_file(pdf_path, f"{board.name()}, top and bottom sides, {scale:.0%}", pages,
                              mono_font=bool(command))
    return pdf_path, scale


def write_pdf_file(pdf_path, title, pages, mono_font=False):
    """Write the file for pages of finished operators. Their text is in /F1, Helvetica, and with mono_font
    also in /F2, Courier. Returns the path written, which is another one if pdf_path is open in a program
    that locks it: see write_unlocked()."""
    mm = 72 / 25.4
    page_w, page_h = PDF_PAGE[0] * mm, PDF_PAGE[1] * mm
    offset = datetime.datetime.now().astimezone().strftime("%z")
    now = datetime.datetime.now().strftime("D:%Y%m%d%H%M%S") + f"{offset[:3]}'{offset[3:5]}'"
    # Objects 1 to 4 are fixed, and then each page and its drawing take two numbers.
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii"),
        (f"<< /Title ({title}) "
         f"/Producer (generate_protoboard) /CreationDate ({now}) >>").encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    fonts = "/F1 4 0 R"
    if mono_font:
        fonts += f" /F2 {5 + 2 * len(pages)} 0 R"
    for i, ops in enumerate(pages):
        stream = zlib.compress("\n".join(ops).encode("ascii"), 9)
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.3f} {page_h:.3f}] "
                        f"/Resources << /Font << {fonts} >> >> "
                        f"/Contents {6 + 2 * i} 0 R >>").encode("ascii"))
        objects.append(f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
                       + stream + b"\nendstream")
    if mono_font:
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    for offset in offsets:
        data += f"{offset:010d} 00000 n \n".encode("ascii")
    data += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 3 0 R >>\n"
             f"startxref\n{xref}\n%%EOF\n").encode("ascii")
    return write_unlocked(pdf_path, bytes(data))


def write_unlocked(path, data):
    """Write data to path, or, if that file is open in a program that will not let it be replaced, as Windows
    PDF viewers do, to <name>-2.pdf, <name>-3.pdf, ... Returns the path written. Any other error is raised, and
    so is the last refusal if every name is taken."""
    path = Path(path)
    for n in range(1, PDF_LOCKED_TRIES + 1):
        target = path if n == 1 else path.with_name(f"{path.stem}-{n}{path.suffix}")
        try:
            target.write_bytes(data)
            return target
        except PermissionError:
            if n == PDF_LOCKED_TRIES:
                raise
