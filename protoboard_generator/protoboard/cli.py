# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line: parse and check the options, then call everything else."""

import argparse
import datetime
import os
import shlex
import sys
from pathlib import Path

from .board import Board
from .constants import MARGIN_DEFAULT, MARGIN_PLAIN, MOUNT_MIN_SPACING, PDF_SCALES
from .font import check_user_text
from .gerber import write_fab_zip
from .kicad_project import write_project
from .mounting import mount_min_distance, parse_side
from .pattern import has_bus, ideal_rows, pattern_option
from .pdf import write_pdf


def default_output(cols, rows):
    """<cols>x<rows>_YYYYMMDDTHHMM/MCboard_<cols>x<rows>.kicad_pcb"""
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M")
    return str(Path(f"{cols}x{rows}_{stamp}") / default_name(cols, rows))


def default_name(cols, rows):
    """MCboard_<cols>x<rows>.kicad_pcb"""
    return f"MCboard_{cols}x{rows}.kicad_pcb"


def command_line(argv):
    """The command as typed, for the PDF, without -o and --working-copy, which do not change the board: one
    says where the files went, and its path may tell more about the computer than whoever gets the PDF needs,
    and the other only adds a file."""
    kept, skip = [], False
    for arg in argv[1:]:
        if skip:
            skip = False
        elif arg in ("-o", "--out"):
            skip = True
        elif arg != "--working-copy" and not arg.startswith(("--out=", "-o")):
            kept.append(arg)
    return shlex.join([Path(argv[0]).name] + kept)


def pattern_value(text):
    """--pattern, checked by argparse."""
    try:
        return pattern_option(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def mount_sides(args):
    """{side: SideSpec} from the --mount-* options. A single side beats the pair it belongs to."""
    given = (("x", ("top", "bottom")), ("y", ("left", "right")), ("top", ("top",)), ("bottom", ("bottom",)),
             ("left", ("left",)), ("right", ("right",)))
    least = mount_min_distance(args.mount_drill)
    sides = {}
    for name, targets in given:
        text = getattr(args, f"mount_{name}")
        if text is None:
            continue
        if args.no_mount_holes:
            sys.exit(f"--mount-{name} cannot be used with --no-mount-holes.")
        for side in targets:
            try:
                spec = parse_side(text, side)
            except ValueError as error:
                sys.exit(f"--mount-{name}: {error}.")
            if spec.spacing is not None and spec.spacing < least:
                sys.exit(f"--mount-{name}: the spacing must be at least {least:g} mm.")
            sides[side] = spec
    return sides


def main():
    """Parse the options, build the board and write all the output files."""
    ap = argparse.ArgumentParser(
        description="Generate a protoboard of buses and vertical strips with chevron jumpers between them, as a "
                    "KiCad project.")
    ap.add_argument("--cols", type=int, required=True, help="number of hole columns")
    ap.add_argument("--rows", type=int, required=True,
                    help="number of rows, spaces of --pattern included, ideally ending on a bus: 6k+1 with the "
                         "default --pattern")
    ap.add_argument("-o", "--out",
                    help="output .kicad_pcb file, whose folder and name are used for all the other files, or a "
                         "folder, given with a final slash or already there, for the default name "
                         "(<cols>x<rows>_YYYYMMDDTHHMM/MCboard_<cols>x<rows>.kicad_pcb)")
    ap.add_argument("--pattern", type=pattern_value, default="b+2+3",
                    help="the rows from the top, joined by + and repeated down the board: a number is a group of "
                         "that many rows, a vertical strip per column, and b a bus row, 2b two and so on, each "
                         "row a rail of its own. Neighbouring strips have a chevron between them, except two bus "
                         "rows, and nothing crosses s, a row with no holes. Such as b+3+2, b+5, b+3+2+2b+5, "
                         "2b+2s+5+2s+5+2s, which can be written classic (b+2+3)")
    ap.add_argument("--pitch", type=float, default=2.54, help="hole pitch in mm (2.54)")
    ap.add_argument("--drill", type=float, default=1.0, help="drill diameter in mm (1.0)")
    ap.add_argument("--pad", type=float, default=1.6, help="pad diameter in mm (1.6)")
    ap.add_argument("--track", type=float, default=1.0, help="strip track width in mm (1.0)")
    ap.add_argument("--bus", type=float, default=1.0, help="horizontal bus width in mm (1.0)")
    ap.add_argument("--gap", type=float, default=0.25, help="chevron gap, perpendicular, in mm (0.25)")
    ap.add_argument("--chev-width", type=float, default=1.0, help="chevron width in mm (1.0)")
    ap.add_argument("--chev-depth", type=float, default=0.3, help="depth of the chevron V in mm (0.3)")
    ap.add_argument("--mask-margin", type=float, default=0.05, help="solder mask expansion in mm (0.05)")
    ap.add_argument("--margin", type=float,
                    help="distance from board edge to the first hole centre on sides with "
                         f"mounting holes, in mm ({MARGIN_DEFAULT}); with --no-mount-holes, "
                         "on all sides")
    ap.add_argument("--margin-plain", type=float, default=MARGIN_PLAIN,
                    help=f"the same on sides without mounting holes, in mm ({MARGIN_PLAIN})")
    ap.add_argument("--origin-x", type=float, default=50.0, help="X position of the board corner on the sheet (50)")
    ap.add_argument("--origin-y", type=float, default=50.0, help="Y position of the board corner on the sheet (50)")
    ap.add_argument("--top", choices=["plain", "smt"], default="plain",
                    help="top side: only the rings of the holes (plain), or also exposed chevrons between "
                         "holes and small square pads on some rows, where SMT parts can be soldered (smt)")
    ap.add_argument("--silk", choices=["bars", "outline", "lines", "none"], default="bars",
                    help="top silkscreen showing the connections: a bar over each track and a line "
                         "between groups (bars), a capsule around each group and bus (outline, not "
                         "with --top smt), short lines between holes (lines) or nothing (none)")
    ap.add_argument("--silk-width", type=float, default=0.15, help="silkscreen line width in mm (0.15)")
    ap.add_argument("--label-size", type=float, default=1.0,
                    help="height of the column and row labels in mm (1.0)")
    ap.add_argument("--no-labels", action="store_true", help="do not add column and row labels")
    ap.add_argument("--text", help="your own text on the top silkscreen, at the label size; placed "
                                   "automatically, in a margin other than the name's if it fits")
    ap.add_argument("--mount-spacing", type=int, default=40,
                    help="largest spacing between mounting holes in a strip, in mm; "
                         "a multiple of 5 (40)")
    for flag, where in (("--mount-x", "the top and bottom sides"), ("--mount-y", "the left and right sides"),
                        ("--mount-top", "the top side"), ("--mount-bottom", "the bottom side"),
                        ("--mount-left", "the left side"), ("--mount-right", "the right side")):
        ap.add_argument(flag, metavar="SPEC",
                        help=f"mounting holes on {where}, as holes x spacing : alignment, each part optional: "
                             "0 for none, 3, x40, 3x40, 3x40:left. Number and spacing together make the board "
                             "grow if needed. Alignment is left, right or centre (top, bottom or centre on "
                             "the left and right sides). A side not given is automatic")
    ap.add_argument("--array-x", choices=["centre", "left", "right"], default="centre",
                    help="where the grid of holes goes when the mounting holes make the board wider (centre)")
    ap.add_argument("--array-y", choices=["centre", "top", "bottom"], default="centre",
                    help="the same when they make it taller (centre)")
    ap.add_argument("--mount-drill", type=float, default=3.2,
                    help="mounting hole diameter in mm (3.2, for M3)")
    ap.add_argument("--no-mount-holes", action="store_true", help="do not add mounting holes")
    ap.add_argument("--working-copy", action="store_true",
                    help="also write <name>_working.kicad_pcb, for placing parts and planning: instead of its top silkscreen "
                         "it has a thin dotted line through the connected holes and a thin circle around each drill on User.1 and the labels on User.2, and "
                         "lines and arcs drawn on User.3 are wires of your own for the plan, and lines on User.4 chevrons to "
                         "close; the plan button draws on User.5. "
                         "Never order from it")
    ap.add_argument("--fab", choices=["jlcpcb", "pcbway", "generic", "none"], default="generic",
                    help="fab design rules to include (generic, which satisfies jlcpcb and pcbway)")
    args = ap.parse_args()

    # Fill in the defaults that depend on other options, and reject the impossible ones
    if args.out is None:
        args.out = default_output(args.cols, args.rows)
    elif args.out.endswith(("/", os.sep)) or Path(args.out).is_dir():
        args.out = str(Path(args.out) / default_name(args.cols, args.rows))
    if not args.out.endswith(".kicad_pcb"):
        args.out += ".kicad_pcb"
    if args.margin is None:
        args.margin = args.margin_plain if args.no_mount_holes else MARGIN_DEFAULT

    if args.text is not None:
        args.text = args.text.strip()
        problem = check_user_text(args.text) if args.text else "is empty"
        if problem:
            sys.exit(f"--text {problem}.")
    if args.top == "smt" and args.silk == "outline":
        sys.exit("--silk outline would cross the chevrons of --top smt; use bars, lines or none.")
    if args.cols < 1 or args.rows < 2:
        sys.exit("At least 1 column and 2 rows are required.")
    if min(args.margin, args.margin_plain) < args.pad / 2 + 0.3:
        sys.exit("Margin is too small for the edge pads.")
    if args.mount_spacing < MOUNT_MIN_SPACING or args.mount_spacing % 5:
        sys.exit(f"--mount-spacing must be a multiple of 5 mm, at least {MOUNT_MIN_SPACING}.")
    args.mount_sides = mount_sides(args)
    lower, upper = ideal_rows(args.rows, args.pattern)
    if args.rows != lower:
        tries = f"{lower} or {upper}" if lower else f"{upper}"
        cut = "bus; the last group or bus" if has_bus(args.pattern) else "group; the last one"
        print(f"Warning: with {args.rows} rows the board does not end with a whole {cut} is cut short. "
              f"Try {tries}.", file=sys.stderr)

    # Do the work: first decide everything, then write the files
    board = Board(args)
    n_holes, n_jumpers, (width, height) = board.build()
    Path(args.out).resolve().parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(board.text())
    extra = write_project(args.out, args)
    if args.working_copy:
        working = Path(args.out).with_name(Path(args.out).stem + "_working.kicad_pcb")
        working.write_text(board.text(working=True), encoding="utf-8")
        extra += [str(working)] + write_project(working, args, working=True)
    base = Path(args.out).with_suffix("")
    extra.append(write_fab_zip(board, base))
    pdf = write_pdf(board, base, command_line(sys.argv))

    # Report what came out
    hole_rows = len(board.pattern.hole_rows)
    grid = f" on a {args.cols}x{args.rows} grid" if hole_rows != args.rows else ""
    print(f"{args.out}: {args.cols}x{hole_rows} holes ({n_holes}){grid}, {n_jumpers} chevrons, "
          + (f"{board.n_top_chevrons} on top, {board.n_islands} islands, " if board.n_top_chevrons else "") +
          f"{len(board.nets) - 1} nets, board {width:.2f} x {height:.2f} mm")
    if board.mount_info:
        print(f"Mounting holes: {board.mount_info[0]}")
        for line in board.mount_report() + board.growth_report():
            print(f"  {line}")
    if board.name_placement:
        print(f'Name "{board.name()}": {board.name_placement[0]} margin.')
    if board.text_placement:
        print(f'Text "{args.text}": {board.text_placement[0]} margin.')
    for path in extra:
        print(path)
    if pdf:
        print(f"{pdf[0]} (scale {pdf[1]:.0%}; print it at actual size)")
        if pdf[0] != base.with_name(base.name + ".pdf"):
            print(f"Warning: {base.name}.pdf is open in another program, so the PDF went to {pdf[0].name}.",
                  file=sys.stderr)
    else:
        print(f"Warning: the board does not fit on A4 at {PDF_SCALES[-1]:.0%}; no PDF written.",
              file=sys.stderr)
