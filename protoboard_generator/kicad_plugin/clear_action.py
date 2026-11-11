# SPDX-License-Identifier: GPL-3.0-or-later
"""The clear button. It takes the board open in the editor back to before the plan and apply buttons, as one step in
the editor's undo history: the locked tracks on the MCboard inner layers go, every strip takes its own name as its
net again, the user's tracks take the net of the hole at one of their ends, and the marks on MCboard.Plan go.

It does not plan. The user's own tracks, which are unlocked, stay where they are, and so do the parts. On a board
whose User.5 is not named MCboard.Plan that layer is left alone, as it is not the plan button's.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# button puts the plugin's packages first
from button import check_marks_layer, read_current, run_button  # noqa: E402
from apply_action import changes  # noqa: E402

from protoboard.apply import cleared  # noqa: E402
from protoboard.constants import PLAN_MARKS_LAYER  # noqa: E402
from protoboard.kicad_file import NotAPlacedBoard  # noqa: E402

TITLE = "MCboard clear"
REPORT = "_clear.html"  # the report page


def plan_marks(board, text):
    """The items on the marks' layer, or None if User.5 is not MCboard.Plan on this board."""
    from kipy.proto.board.board_types_pb2 import BoardLayer
    try:
        check_marks_layer(text)
    except NotAPlacedBoard:
        return None
    return [item for item in list(board.get_shapes()) + list(board.get_text()) if item.layer == BoardLayer.BL_User_5]


def run_clear(board):
    """All the work except showing the report: (lines of the report, no paths to link)."""
    placed, text = read_current(board)
    remove, update, footprints, _ = changes(board, placed, cleared(placed))
    marks = plan_marks(board, text)
    commit = board.begin_commit()
    try:
        if remove or marks:
            board.remove_items(remove + (marks or []))
        if update:
            board.update_items(update)
        if footprints:
            board.update_items(footprints)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, TITLE)
    lines = [
        f"Tracks of an earlier apply removed: {len(remove)}",
        f"Your own tracks given back the net of their hole: {len(update)}",
        f"Footprints given back their strip's net: {len(footprints)}",
    ]
    if marks is None:
        lines.append(f"Plan marks: none removed, as {PLAN_MARKS_LAYER[0]} is not named {PLAN_MARKS_LAYER[1]}")
    else:
        lines.append(f"Plan marks removed from {PLAN_MARKS_LAYER[1]}: {len(marks)}")
    return lines + [
        "",
        "Your own tracks and the parts are where they were.",
        "Edit > Undo puts the board back.",
        "",
        "Press MCboard plan, then MCboard apply, to plan the board again.",
    ], []


def main():
    """Clear the board open in the editor and show what was done."""
    run_button(TITLE, run_clear, NotAPlacedBoard, "This board cannot be cleared", REPORT)


if __name__ == "__main__":
    main()
