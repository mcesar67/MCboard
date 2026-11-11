# SPDX-License-Identifier: GPL-3.0-or-later
"""The apply button. It plans the board as the plan button does, then puts the plan into the copper, so that KiCad's
ratsnest follows it: each strip takes the net of the schematic it carries, each chevron to close becomes a track on
the inner layer MCboard.Chevrons, each wire becomes tracks on a layer named MCboard.Wires<n>, and the user's own
tracks on those layers take the net of the holes they join. protoboard/apply.py works out what to change; this file
only changes it, through KiCad's IPC API, as one step in the editor's undo history.

The tracks it adds are locked. Pressing it again first removes the locked tracks on those layers, so it can follow a
changed schematic, placement or set of the user's tracks; unlocked ones are the user's and stay. The marks of the plan
button are left alone. It writes the same <board>_plan.pdf as the plan button, so that what is printed follows what was
applied.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# button puts the plugin's packages first
from button import board_path, read_current, run_button, write_pdf  # noqa: E402

from protoboard.apply import CannotApply, is_applied, project_track_width, track_key, work_out  # noqa: E402
from protoboard.kicad_file import CHEVRON, HOLE, ISLAND, NotAPlacedBoard  # noqa: E402
from protoboard.planner import solve  # noqa: E402

TITLE = "MCboard apply"
REPORT = "_apply.html"  # the report page
NM = 1e6  # nanometres in a mm, the API's unit


def mm(vector):
    """A kipy Vector2 in board mm."""
    return vector.x / NM, vector.y / NM


def layer_name(layer):
    """KiCad's own name of a layer, such as "In1.Cu", from the API's BoardLayer value."""
    from kipy.proto.board.board_types_pb2 import BoardLayer
    return BoardLayer.Name(layer)[3:].replace("_", ".")


def layer_value(name):
    """The API's BoardLayer value of a layer named like "In1.Cu"."""
    from kipy.proto.board.board_types_pb2 import BoardLayer
    return BoardLayer.Value("BL_" + name.replace(".", "_"))


def changes(board, placed, applied, keep_locked=False):
    """(tracks to remove, tracks to update, footprints to update, tracks to add) as kipy objects. The tracks to
    update are the generator's, on F.Cu and B.Cu, and the user's on the MCboard inner layers. The locked tracks there,
    an earlier apply's, are removed, or with keep_locked updated like the user's."""
    from kipy.board_types import Net, Track
    from kipy.geometry import Vector2
    from kipy.util.units import from_mm

    remove, update = [], []
    for track in board.get_tracks():
        name = layer_name(track.layer)
        start, end = mm(track.start), mm(track.end)
        if is_applied(placed.layers, name, track.locked) and not keep_locked:
            remove.append(track)
            continue
        if name in placed.layers:
            net = applied.manual_nets.get(track_key(name, start, end))
        elif name in ("F.Cu", "B.Cu") and isinstance(track, Track):
            net = applied.track_net(start, end)
        else:
            net = None
        if net and track.net.name != net:
            track.net = Net(name=net)
            update.append(track)

    pad_place = {pad.id.value: mm(pad.position) for pad in board.get_pads()}
    islands = {(round(i.x, 3), round(i.y, 3)): i.hole for i in placed.islands}
    footprints = []
    for fp in board.get_footprints():
        kind = fp.definition.id.name
        if kind not in (HOLE, CHEVRON, ISLAND):
            continue
        changed = False
        for pad in fp.definition.pads:
            x, y = pad_place.get(pad.id.value, mm(pad.position))
            if kind == ISLAND:
                hole = islands[round(mm(fp.position)[0], 3), round(mm(fp.position)[1], 3)]
                net = applied.nets[hole.col, hole.row]
            else:
                net = applied.net_at(x, y)
            if pad.net.name != net:
                pad.net = Net(name=net)
                changed = True
        if kind == HOLE:
            col_row, _ = applied.nearest(*mm(fp.position))
            strip = placed.holes[col_row].strip
            if fp.value_field.text.value != strip:
                fp.value_field.text.value = strip
                changed = True
        if changed:
            footprints.append(fp)

    add = []
    for wanted in applied.tracks:
        track = Track()
        track.start = Vector2.from_xy_mm(*wanted.start)
        track.end = Vector2.from_xy_mm(*wanted.end)
        track.width = from_mm(wanted.width)
        track.layer = layer_value(wanted.layer)
        track.net = Net(name=wanted.net)
        track.locked = True
        add.append(track)
    return remove, update, footprints, add


def commit_changes(board, title, remove, update, footprints, add):
    """Make the changes as one step in the editor's undo history."""
    commit = board.begin_commit()
    try:
        if remove:
            board.remove_items(remove)
        if update:
            board.update_items(update)
        if footprints:
            board.update_items(footprints)
        if add:
            board.create_items(add)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, title)


def run_apply(board):
    """All the work except showing the report: (lines of the report, [path of the PDF])."""
    placed, _ = read_current(board)
    plan = solve(placed)
    path = board_path(board)
    applied = work_out(plan, project_track_width(path) if path else None)
    remove, update, footprints, add = changes(board, placed, applied)
    commit_changes(board, TITLE, remove, update, footprints, add)
    where, pdf = write_pdf(board, plan)
    lines = applied.counts + [
        f"Tracks of an earlier apply removed: {len(remove)}",
        f"Footprints given new nets: {len(footprints)}",
    ]
    if applied.notes:
        lines += [""] + applied.notes
    return lines + [""] + where + [
        "",
        "The ratsnest now follows the plan: what it still shows is left to do.",
        "Edit > Undo puts the board back.",
        "",
        "Press this button again after changing the schematic, the parts or your own tracks.",
        "The tracks it adds are locked: unlock one to keep it as your own.",
        "",
        "A placed board is a working file: order from the generator's zip.",
    ], [pdf]


def main():
    """Apply the plan to the board open in the editor and show what was done."""
    run_button(TITLE, run_apply, (NotAPlacedBoard, CannotApply), "The plan cannot be applied", REPORT)


if __name__ == "__main__":
    main()
