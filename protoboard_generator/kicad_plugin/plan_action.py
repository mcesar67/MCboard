# SPDX-License-Identifier: GPL-3.0-or-later
"""The button. It only reads the board, runs the planner and draws marks on a layer of their own,
MCboard.Plan (User.5). It never regenerates anything, and leaves copper, nets and footprints alone.

KiCad starts this script in a process of its own and the script talks to the open PCB editor through KiCad's IPC
API (the kicad-python package, which KiCad installs from requirements.txt). It is kept thin on purpose: this is
the only file that uses the API. The planner and PDF work from a file.

What the API cannot do in KiCad 9, and so the button does not do either: add or rename a layer (the board must
already have MCboard.Plan, as working copies do), and make groups (the marks are known by their layer).
"""

import os
import re
import subprocess
import sys
import tempfile
import traceback


def own_packages_first():
    """Put the plugin's environment ahead of anything KiCad adds to the path. On Linux KiCad can pass on the system's
    dist-packages, whose older protobuf would then hide the one kicad-python needs."""
    prefix = os.path.realpath(sys.prefix)
    own = [p for p in sys.path if os.path.realpath(p or ".").startswith(prefix + os.sep) and "packages" in p]
    sys.path[:] = [sys.path[0]] + own + [p for p in sys.path[1:] if p not in own]


own_packages_first()

from protoboard.constants import PLAN_MARKS_LAYER, PLAN_RING_LINE, PLAN_RING_RADIUS, PLAN_WIRE_LINE
from protoboard.kicad_file import NotAPlacedBoard, read_board
from protoboard.plan_pdf import write_plan_pdf
from protoboard.planner import report, solve

TITLE = "MCboard plan"
WINDOW_SIZE = "720x560"
TIMEOUT_MS = 30000  # sending a large board as text takes a while


def read_current(board):
    """(the PlacedBoard of what the editor has now, saved or not; the board's text), through a temporary copy."""
    text = board.get_as_string()
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "plan.kicad_pcb")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return read_board(path), text


def check_marks_layer(text):
    """Make sure User.5 is the marks' layer, so that whatever is on it is an earlier run's and can go. The layer
    table of the board file lists only enabled layers, as (<number> "User.5" user ["<name>"])."""
    found = re.search(r'\(\s*\d+\s+"%s"\s+\w+(?:\s+"([^"]*)")?\s*\)' % re.escape(PLAN_MARKS_LAYER[0]), text)
    if found and found.group(1) == PLAN_MARKS_LAYER[1]:
        return
    how = (f"In Board Setup > Board Layers, enable {PLAN_MARKS_LAYER[0]} and name it {PLAN_MARKS_LAYER[1]}. "
           "Working copies made by the generator have it already.")
    if found:
        raise NotAPlacedBoard(f"the plan draws its marks on {PLAN_MARKS_LAYER[0]}, which this board uses as "
                              f"{found.group(1) or PLAN_MARKS_LAYER[0]}. Rename that layer to {PLAN_MARKS_LAYER[1]} "
                              "in Board Setup > Board Layers if its contents can go, or move them to another layer")
    raise NotAPlacedBoard(f"it has no {PLAN_MARKS_LAYER[1]} layer for the marks. {how}")


def add_marks(board, plan):
    """Replace whatever is on the marks' layer with a ring around each chevron to close and the path of each wire,
    as one step in the editor's undo history."""
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
        ring = BoardCircle()
        ring.center = Vector2.from_xy_mm(chevron.x, chevron.y)
        ring.radius_point = Vector2.from_xy_mm(chevron.x + PLAN_RING_RADIUS, chevron.y)
        marks.append(shape(ring, PLAN_RING_LINE))
    for wire in plan.wires:
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


def board_path(board):
    """The board's file, or None if it has never been saved."""
    document = board.document
    name = document.board_filename
    if not name:
        return None
    return name if os.path.isabs(name) else os.path.join(document.project.path, name)


def output_path(board, suffix):
    """Next to the board, or in the temporary folder if it has never been saved."""
    path = board_path(board)
    if not path:
        return os.path.join(tempfile.gettempdir(), "mcboard" + suffix)
    return os.path.splitext(path)[0] + suffix


def marks_hidden(board):
    """Whether the marks' layer is hidden in the editor, or None if KiCad cannot say."""
    from kipy.proto.board.board_types_pb2 import BoardLayer
    try:
        return BoardLayer.BL_User_5 not in board.get_visible_layers()
    except Exception:
        return None


def run_plan(board):
    """All the work except the window: (lines of the report, path of the PDF)."""
    placed, text = read_current(board)
    check_marks_layer(text)
    plan = solve(placed)
    add_marks(board, plan)
    path = output_path(board, "_plan.pdf")
    written, scale = write_plan_pdf(plan, path)
    # A PDF viewer that locks the file sends the PDF to <name>-2.pdf and so on: say so in the report.
    locked = [f"{path} is open in another program, so the PDF went to {written}."] if str(written) != path else []
    path = str(written)
    where = f"Drawing: {path}" if scale else f"The board does not fit on A4. Report: {path}"
    lines = report(plan) + [""] + locked + [where, f"Marks are on the {PLAN_MARKS_LAYER[1]} layer "
                                                   f"({PLAN_MARKS_LAYER[0]}). A placed board is a working file: "
                                                   "order from the generator's zip."]
    if marks_hidden(board):
        lines.append(f"The {PLAN_MARKS_LAYER[1]} layer is hidden: turn it on in the Layers panel to see the marks.")
    return lines, path


def show_in_window(title, text):
    """Show text in a resizable read-only window with a fixed-width font, so the report lines up. It comes up in
    front of the editor, and the script ends when it is closed. Return False if there is no tkinter."""
    try:
        import tkinter
        from tkinter import font, scrolledtext
    except ImportError:
        return False
    root = tkinter.Tk()
    root.title(title)
    root.geometry(WINDOW_SIZE)
    field = scrolledtext.ScrolledText(root, wrap="none", font=font.nametofont("TkFixedFont"))
    field.insert("1.0", text)
    field.configure(state="disabled")
    field.pack(fill="both", expand=True, padx=8, pady=(8, 0))
    tkinter.Button(root, text="OK", width=10, command=root.destroy).pack(pady=8)
    root.bind("<Escape>", lambda event: root.destroy())
    root.attributes("-topmost", True)  # KiCad has the focus when the script starts
    root.after(500, lambda: root.attributes("-topmost", False))
    root.mainloop()
    return True


def open_file(path):
    """Open a file with the system's program for it."""
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def show(title, text, board=None):
    """Show the report in a window or, if this Python has no tkinter, in a text file next to the board."""
    print(text)  # KiCad 10 shows what a plugin prints among the editor's messages
    if show_in_window(title, text):
        return
    path = output_path(board, "_plan.txt") if board else os.path.join(tempfile.gettempdir(), "mcboard_plan.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(title + "\n\n" + text + "\n")
    open_file(path)


def main():
    """Plan the board open in the editor and show the report. Errors go to the same window, as KiCad 9 would show
    nothing."""
    board = None
    try:
        from kipy import KiCad
        board = KiCad(timeout_ms=TIMEOUT_MS).get_board()
        lines, _ = run_plan(board)
    except NotAPlacedBoard as error:
        show(TITLE, f"This board cannot be used: {error}", board)
        return
    except (Exception, SystemExit):  # font.py can exit
        show(TITLE + ": error", traceback.format_exc(), board)
        return
    show(TITLE, "\n".join(lines), board)


if __name__ == "__main__":
    main()
