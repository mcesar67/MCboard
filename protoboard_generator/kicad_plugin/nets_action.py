# SPDX-License-Identifier: GPL-3.0-or-later
"""The nets button. It gives the copper the nets of what is on the board already, with no plan worked out: each strip
takes the net of the schematic of the pins on it, and the chevrons and wires drawn as tracks on the MCboard inner
layers join strips, so that KiCad's ratsnest shows only what is still to join. The tracks an earlier apply added count
like the user's and stay. It is quick, as it looks for no routes, so it suits drawing chevrons and wires by hand.

It shares the apply button's changes() and commit, as one step in the editor's undo history, but removes and adds no
tracks, and writes no PDF.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# button puts the plugin's packages first
from button import read_current, run_button  # noqa: E402
from apply_action import changes, commit_changes  # noqa: E402

from protoboard.apply import CannotApply, pieces, strip_nets, work_out  # noqa: E402
from protoboard.kicad_file import NotAPlacedBoard  # noqa: E402
from protoboard.planner import solve  # noqa: E402

TITLE = "MCboard nets"
REPORT = "_nets.html"  # the report page


def run_nets(board):
    """All the work except showing the report: (lines of the report, no paths)."""
    placed, _ = read_current(board, locked=True)
    plan = solve(placed, search=False)
    applied = work_out(plan)
    _, update, footprints, _ = changes(board, placed, applied, keep_locked=True)
    commit_changes(board, TITLE, [], update, footprints, [])
    left = pieces(plan)
    lines = [
        f"Strips given a net of the schematic: {len(strip_nets(plan))}",
        f"Chevrons and wires on the inner layers, yours and an earlier apply's: {len(plan.manual)}",
        f"Tracks given a new net: {len(update)}",
        f"Footprints given new nets: {len(footprints)}",
        f"Nets still in pieces: {len(left)}" + (": " + ", ".join(f"{net} ({count})" for net, count in left.items())
                                                 if left else ""),
    ]
    if plan.unplaced:
        lines += ["", "Not on the board, so left out: " + ", ".join(plan.unplaced) + "."]
    if applied.notes or plan.warnings:
        lines += [""] + applied.notes + plan.warnings
    return lines + [
        "",
        "The ratsnest now shows what is still to join." if left else "Every net is joined.",
        "Edit > Undo puts the board back.",
        "",
        "Press this button again after drawing more chevrons or wires, or changing the parts.",
        "MCboard plan and MCboard apply work out the rest.",
    ], []


def main():
    """Give the board the nets of what is on it and show what was done."""
    run_button(TITLE, run_nets, (NotAPlacedBoard, CannotApply), "The nets cannot be given", REPORT)


if __name__ == "__main__":
    main()
