# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line of the planner. Exit status: 0 for a complete plan, 1 if it has problems, 2 if the file
cannot be used."""

import argparse
import sys
from pathlib import Path

from .kicad_file import NotAPlacedBoard, read_board
from .plan_pdf import write_plan_pdf
from .planner import report, solve


def main():
    """Read the board, print the plan and write its PDF. Exit status: see the module docstring."""
    ap = argparse.ArgumentParser(
        description="Read a generated board with parts placed on it in KiCad, and work out which chevrons "
                    "to close and which wires to add. KiCad is not needed, and the board is not changed.")
    ap.add_argument("board", help="the .kicad_pcb, saved after placing the parts")
    ap.add_argument("-o", "--out", help="the PDF to write (<board>_plan.pdf)")
    ap.add_argument("--no-pdf", action="store_true", help="only print the report")
    args = ap.parse_args()
    try:
        board = read_board(args.board)
    except NotAPlacedBoard as error:
        print(f"{args.board}: {error}", file=sys.stderr)
        return 2
    plan = solve(board)
    print("\n".join(report(plan)))
    if not args.no_pdf:
        source = Path(args.board)
        out = Path(args.out) if args.out else source.with_name(source.stem + "_plan.pdf")
        written, scale = write_plan_pdf(plan, out)
        print()
        if written != out:
            print(f"Warning: {out} is open in another program, so the plan went to {written}.", file=sys.stderr)
            out = written
        if scale is None:
            print(f"Warning: the board does not fit on A4; {out} has only the report.", file=sys.stderr)
        else:
            print(f"{out} (scale {scale:.0%}; print it at actual size)")
    return 0 if plan.complete else 1
