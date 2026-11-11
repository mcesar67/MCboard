# SPDX-License-Identifier: GPL-3.0-or-later
"""The plan button. It only reads the board, runs the planner and draws marks on a layer of their own,
MCboard.Plan (User.5). It never regenerates anything, and leaves copper, nets and footprints alone.

What the buttons share, and how they reach KiCad, is in button.py. The API cannot make groups, so the marks are known
by their layer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# button puts the plugin's packages first
from button import check_marks_layer, read_current, run_button, write_pdf  # noqa: E402

from protoboard.constants import (PLAN_MARKS_LAYER, PLAN_RING_LINE, PLAN_RING_RADIUS,  # noqa: E402
                                  PLAN_WIRE_LINE)
from protoboard.kicad_file import NotAPlacedBoard  # noqa: E402
from protoboard.plan import report  # noqa: E402
from protoboard.planner import solve  # noqa: E402

TITLE = "MCboard plan"


def add_marks(board, plan):
    """Replace whatever is on the marks' layer with a ring around each chevron to close and the path of each wire,
    as one step in the editor's undo history. The chevrons and wires drawn as the user's own tracks get no mark, as
    the tracks already show them; the report and the PDF still list them."""
    from kipy.board_types import BoardCircle, BoardSegment
    from kipy.geometry import Vector2
    from kipy.proto.board.board_types_pb2 import BoardLayer
    from kipy.util.units import from_mm

    layer = BoardLayer.BL_User_5

    def shape(item, width):
        item.layer = layer
        item.attributes.stroke.width = from_mm(width)
        return item

    marks = []
    for _, chevron in plan.chevrons:
        if chevron in plan.manual:
            continue
        ring = BoardCircle()
        ring.center = Vector2.from_xy_mm(chevron.x, chevron.y)
        ring.radius_point = Vector2.from_xy_mm(chevron.x + PLAN_RING_RADIUS, chevron.y)
        marks.append(shape(ring, PLAN_RING_LINE))
    for wire in plan.wires:
        if wire in plan.manual:
            continue
        for start, end in zip(wire.drawn, wire.drawn[1:]):
            line = BoardSegment()
            line.start = Vector2.from_xy_mm(*start)
            line.end = Vector2.from_xy_mm(*end)
            marks.append(shape(line, PLAN_WIRE_LINE))

    old = [item for item in list(board.get_shapes()) + list(board.get_text()) if item.layer == layer]
    commit = board.begin_commit()
    try:
        board.remove_items(old)
        board.create_items(marks)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, TITLE)


def marks_hidden(board):
    """Whether the marks' layer is hidden in the editor, or None if KiCad cannot say."""
    from kipy.proto.board.board_types_pb2 import BoardLayer
    try:
        return BoardLayer.BL_User_5 not in board.get_visible_layers()
    except Exception:
        return None


def run_plan(board):
    """All the work except showing the report: (lines of the report, [path of the PDF])."""
    placed, text = read_current(board)
    check_marks_layer(text)
    plan = solve(placed)
    add_marks(board, plan)
    where, path = write_pdf(board, plan)
    lines = report(plan) + [""] + where + [f"Marks are on the {PLAN_MARKS_LAYER[1]} layer "
                                                   f"({PLAN_MARKS_LAYER[0]}). A placed board is a working file: "
                                                   "order from the generator's zip."]
    if marks_hidden(board):
        lines.append(f"The {PLAN_MARKS_LAYER[1]} layer is hidden: turn it on in the Layers panel to see the marks.")
    return lines, [path]


def main():
    """Plan the board open in the editor and show the report."""
    run_button(TITLE, run_plan, NotAPlacedBoard, "This board cannot be used", "_plan.html")


if __name__ == "__main__":
    main()
