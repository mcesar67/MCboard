# SPDX-License-Identifier: GPL-3.0-or-later
"""The drawing of a plan: the board as pdf.py draws it, with the work to do painted on top.

Both sides are always drawn, each as it looks when you have it in front of you, so the bottom side is
seen from below: its text reads normally and column 1 is on the right. The top side has the parts; the side
of each chevron has its mark; and wires are on both, since their ends are soldered on the bottom. Then the
report follows, on its own page.
"""

import textwrap
from pathlib import Path
from types import SimpleNamespace

from .constants import (PDF_MARGIN, PDF_PAGE, PLAN_BRIDGE_LINE, PLAN_MARK_RGB, PLAN_PART_LINE, PLAN_PART_RGB,
                        PLAN_PIN_RADIUS, PLAN_REPORT_COLUMNS, PLAN_REPORT_LEADING, PLAN_REPORT_SIZE, PLAN_RING_LINE,
                        PLAN_RING_RADIUS, PLAN_TEXT_SIZE, PLAN_WIRE_END, PLAN_WIRE_LINE, PLAN_WIRE_RGB)
from .pdf import pdf_circle, pdf_number as n, pdf_pages, write_pdf_file
from .planner import report

LEGEND = "Red: chevrons to close. Blue: wires to add. Green: parts. Dots: holes with a pin."
TEXT_WIDTH_FACTOR = 0.5  # Helvetica's average character width, as a fraction of its size


class PlanDrawing:
    """What pdf.py needs of a board, taken from a PlacedBoard instead of from a Board."""

    def __init__(self, board):
        """Take the geometry from a placed board."""
        self.board = board
        self.art = board.art
        self.pth = board.pth
        self.npth = board.npth
        self.args = SimpleNamespace(drill=board.drill, mount_drill=board.mount_drill)

    def from_corner(self, x, y):
        """Convert sheet coordinates to mm from the lower left board corner, y up."""
        return x - self.board.corner[0], self.board.size[1] - (y - self.board.corner[1])

    def board_size(self):
        """Return the width and height of the board."""
        return self.board.size

    def mount_report(self):
        """Return no lines, as the distances are not read back from the file."""
        return []

    def name(self):
        """Return the name of the board, built from its size."""
        return f"MCboard {self.board.cols}x{self.board.rows}"


def pdf_string(text):
    """A text as a PDF string for the built-in font: anything else than ASCII becomes a question mark."""
    text = text.encode("ascii", "replace").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def label(x, y, text, size=PLAN_TEXT_SIZE, flipped=False):
    """Text centred on a point, in the colour already set. flipped is for the bottom side, whose coordinates
    pdf.py mirrors: the text is mirrored too, so that the two cancel out and it reads normally."""
    half = TEXT_WIDTH_FACTOR * size * len(text) / 2
    place = f"-1 0 0 1 {n(x + half)} {n(y)} Tm" if flipped else f"{n(x - half)} {n(y)} Td"
    return f"BT /F1 {n(size)} Tf {place} ({pdf_string(text)}) Tj ET"


def chevron_marks(drawing, chevron):
    """Return the PDF operators of the ring and the bridge over a chevron to close."""
    r, g, b = PLAN_MARK_RGB
    x, y = drawing.from_corner(chevron.x, chevron.y)
    x1, y1 = drawing.from_corner(chevron.first.x, chevron.first.y)
    x2, y2 = drawing.from_corner(chevron.second.x, chevron.second.y)
    # The bridge covers only the chevron, not the holes: half way from its centre to each hole.
    return [f"{r} {g} {b} RG {n(PLAN_RING_LINE)} w", pdf_circle(x, y, PLAN_RING_RADIUS) + " S",
            f"{n(PLAN_BRIDGE_LINE)} w",
            f"{n((x + x1) / 2)} {n((y + y1) / 2)} m {n((x + x2) / 2)} {n((y + y2) / 2)} l S"]


def wire_marks(drawing, wire, name, flipped=False):
    """Return the PDF operators of a wire: its path, the circles at its ends and its name."""
    r, g, b = PLAN_WIRE_RGB
    points = [drawing.from_corner(x, y) for x, y in wire.drawn]
    line = " ".join(f"{n(x)} {n(y)} {'l' if i else 'm'}" for i, (x, y) in enumerate(points)) + " S"
    # The name goes on the longer piece.
    (x1, y1), (x2, y2) = max(zip(points, points[1:]), key=lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1]))
    return [f"{r} {g} {b} RG {r} {g} {b} rg {n(PLAN_WIRE_LINE)} w", line,
            pdf_circle(*points[0], PLAN_WIRE_END) + " S", pdf_circle(*points[-1], PLAN_WIRE_END) + " S",
            label((x1 + x2) / 2, (y1 + y2) / 2 + 0.3, name, flipped=flipped)]


def part_marks(drawing, part):
    """The courtyard and name of a part, for the top side."""
    r, g, b = PLAN_PART_RGB
    ops = [f"{r} {g} {b} RG {r} {g} {b} rg {n(PLAN_PART_LINE)} w"]
    if part.box:
        x0, y0 = drawing.from_corner(part.box[0], part.box[3])
        x1, y1 = drawing.from_corner(part.box[2], part.box[1])
        ops.append(f"{n(x0)} {n(y0)} m {n(x1)} {n(y0)} l {n(x1)} {n(y1)} l {n(x0)} {n(y1)} l h S")
        ops.append(label((x0 + x1) / 2, (y0 + y1) / 2 - PLAN_TEXT_SIZE / 3, part.ref))
    return ops


def pin_marks(drawing, plan):
    """Return the PDF operators of a dot on each pin of the parts on the board."""
    ops = ["0 0 0 rg"]
    for part in plan.board.parts:
        if part.ref in plan.placed:
            for pin in part.pins:
                ops.append(pdf_circle(*drawing.from_corner(pin.x, pin.y), PLAN_PIN_RADIUS) + " f")
    return ops


def plan_marks(drawing, plan):
    """{side: operators}"""
    marks = {"F": pin_marks(drawing, plan), "B": pin_marks(drawing, plan)}
    for part in plan.board.parts:
        if part.ref in plan.placed:
            marks["F"] += part_marks(drawing, part)
    for _, chevron in plan.chevrons:
        marks[chevron.side] += chevron_marks(drawing, chevron)
    for i, wire in enumerate(plan.wires, 1):
        marks["F"] += wire_marks(drawing, wire, f"W{i}")
        marks["B"] += wire_marks(drawing, wire, f"W{i}", flipped=True)
    return marks


def text_pages(lines):
    """Pages of plain text, in operators for pdf.py."""
    wrapped = []
    for line in lines:
        indent = line[:len(line) - len(line.lstrip())]
        wrapped += textwrap.wrap(line, PLAN_REPORT_COLUMNS, initial_indent="", subsequent_indent=indent + "    ",
                                 drop_whitespace=True) or [""]
    points = 72 / 25.4
    left = PDF_MARGIN * points
    top = (PDF_PAGE[1] - PDF_MARGIN) * points - PLAN_REPORT_SIZE
    per_page = int((PDF_PAGE[1] - 2 * PDF_MARGIN) * points // PLAN_REPORT_LEADING)
    pages = []
    for start in range(0, len(wrapped), per_page):
        ops = ["0 0 0 rg"]
        for i, line in enumerate(wrapped[start:start + per_page]):
            if line:
                ops.append(f"BT /F2 {n(PLAN_REPORT_SIZE)} Tf {n(left)} {n(top - i * PLAN_REPORT_LEADING)} Td "
                           f"({pdf_string(line)}) Tj ET")
        pages.append(ops)
    return pages


def write_plan_pdf(plan, path):
    """Write the drawing and the report. Returns (path written, scale), the path being another one if the
    file is open in a viewer that locks it, and the scale None if the board does not fit on A4, in which case
    only the report is written."""
    drawing = PlanDrawing(plan.board)
    found = pdf_pages(drawing, plan_marks(drawing, plan))
    pages, scale = found if found else ([], None)
    pages = pages + text_pages([LEGEND, ""] + report(plan))
    return write_pdf_file(Path(path), f"{drawing.name()}, plan", pages, mono_font=True), scale
