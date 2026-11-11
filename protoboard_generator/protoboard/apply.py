# SPDX-License-Identifier: GPL-3.0-or-later
"""What applying a plan puts on a working copy, so that KiCad's ratsnest follows the plan.

KiCad counts two pads as connected only through copper of their own net, so applying gives:

  every hole           the net of the schematic that its strip carries, or else the strip's own name
  every chevron pad,   the net of the hole it belongs to
  island and track
  a closed chevron     a track between its two holes on the inner layer MCboard.Chevrons
  a wire               tracks along its path on an inner layer named MCboard.Wires<n>
  the user's tracks    the net of the holes they join

The strip's own name (C<col>_R<row> or BUS_R<n>) goes into the Value of each Hole footprint, so that the board can
still be read once its pads carry other nets: kicad_file.py takes the strip from there. The tracks added here are
locked, and the user's own on those layers are not: that is how the two are told apart. So applying again first
removes the locked ones, a track the user unlocks becomes theirs, and an undo in KiCad puts the whole step back. On
F.Cu and B.Cu only nets change.

The nets button gives the nets of a plan with no search, planner.solve(search=False), which has only the user's
tracks and those an earlier apply added, and it adds no tracks;
cleared() gives the nets of the clear button, which takes the board back to its strips.

This module only works out what to do; the buttons in kicad_plugin/ do it through KiCad's IPC API.
"""

import bisect
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .constants import APPLY_CHEVRON_TRACK, APPLY_WIRE_TRACK, CHEVRON_LAYER_NAME, PLAN_PIN_TOLERANCE, WIRE_LAYER_NAME
from .plan import BLOCKED, MANUAL_NO_NET, strip_label


class CannotApply(Exception):
    """The plan cannot be applied: it has errors, or the board lacks a layer for the tracks."""


@dataclass
class Track:
    """A straight track to add, on an inner copper layer like "In1.Cu"."""
    layer: str
    start: tuple
    end: tuple
    width: float
    net: str


@dataclass
class Applied:
    """What applying a plan does to the board."""
    nets: dict                                   # (col, row): the net of each hole
    tracks: list = field(default_factory=list)   # Track
    counts: list = field(default_factory=list)   # report lines, one count each
    notes: list = field(default_factory=list)    # report lines, one sentence each
    # The net for each of the user's tracks: {(layer like "In1.Cu", (x, y), (x, y)): net}, ends rounded by end_key().
    manual_nets: dict = field(default_factory=dict)

    columns: list = field(default_factory=list)  # (x, col) of the holes, sorted
    rows: list = field(default_factory=list)     # (y, row)

    def nearest(self, x, y):
        """((col, row), distance) of the hole nearest a point. The holes form a grid, so this takes the nearest
        column and the nearest row."""
        def closest(places, value):
            i = bisect.bisect_left(places, (value, -1))
            return min(places[max(i - 1, 0):i + 1], key=lambda p: abs(p[0] - value))

        (hx, col), (hy, row) = closest(self.columns, x), closest(self.rows, y)
        return (col, row), math.hypot(hx - x, hy - y)

    def net_at(self, x, y):
        """The net of the hole nearest a point: for a pad of the generator's."""
        return self.nets[self.nearest(x, y)[0]]

    def track_net(self, start, end):
        """The net for a track of the generator's: that of the hole at one of its ends. An island's tie ends on the
        island and in its hole's pad, nearer the hole."""
        here, there = self.nearest(*start), self.nearest(*end)
        return self.nets[(here if here[1] <= there[1] else there)[0]]


def end_key(point):
    """A track end, rounded so that the ends of tracks that meet are one key."""
    return round(point[0], 3), round(point[1], 3)


def track_key(layer, start, end):
    """The key of a track in Applied.manual_nets, the same whichever way round it runs."""
    return (layer,) + tuple(sorted((end_key(start), end_key(end))))


def wire_layers(layers):
    """[(n, "In<k>.Cu")] of the layers named MCboard.Wires<n> in PlacedBoard.layers, in order of n."""
    prefix = WIRE_LAYER_NAME.format("")
    return sorted((int(name[len(prefix):]), layer) for layer, name in layers.items()
                  if name.startswith(prefix) and name[len(prefix):].isdigit())


def wire_layer_numbers(plan, spare=1):
    """{Wire: n} of the MCboard.Wires<n> each wire of the plan goes on. A manual wire stays on its own. The planned
    wires of one net go on one layer, the nets taking the layers in turn, so that a colour per layer tells nets apart.
    A board with no wire layers takes 1 to spare in turn instead."""
    numbers = [n for n, _ in wire_layers(plan.board.layers)] or list(range(1, spare + 1))
    nets = sorted({w.net for w in plan.wires if w not in plan.manual})
    of_net = {net: numbers[i % len(numbers)] for i, net in enumerate(nets)}
    return {w: (w.layer or numbers[0]) if w in plan.manual else of_net[w.net] for w in plan.wires}


def chevron_layer(layers):
    """The layer named MCboard.Chevrons, like "In2.Cu", or None."""
    return next((layer for layer, name in layers.items() if name == CHEVRON_LAYER_NAME), None)


def strip_nets(plan):
    """{strip: net} for the strips the plan gives a net: the strips of its routes and those with its pins. Strips
    joined by manual tracks where no pin has a net share the name of the first of them."""
    nets = {}
    for net, strips in plan.terminals.items():
        for strip in strips:
            nets[strip] = net
    for net, route in plan.routes.items():
        strips = route.strips | plan.terminals.get(net, set())
        if net in (MANUAL_NO_NET, BLOCKED):
            net = min(strips)
        for strip in strips:
            nets[strip] = net
    return nets


def pieces(plan):
    """{net: pieces} of the nets of the schematic whose strips the plan's chevrons and wires leave in more than one
    piece, which the ratsnest then joins."""
    parent = {}

    def root(strip):
        parent.setdefault(strip, strip)
        while parent[strip] != strip:
            parent[strip] = parent[parent[strip]]
            strip = parent[strip]
        return strip

    for route in plan.routes.values():
        for link in route.chevrons + route.wires:
            parent[root(link.first.strip)] = root(link.second.strip)
    found = {net: len({root(s) for s in strips}) for net, strips in plan.terminals.items()}
    return {net: count for net, count in sorted(found.items()) if count > 1}


def manual_nets(applied, board, layers):
    """Fill in Applied.manual_nets. The tracks on a wire layer that meet end to end are one wire, and take the net of
    the holes it ends on; a track on MCboard.Chevrons takes that of the holes it joins. The plan has no errors, so
    they agree."""
    by_name = {name: layer for layer, name in layers.items()}

    def hole_net(point):
        place, gap = applied.nearest(*point)
        return applied.nets[place] if gap <= PLAN_PIN_TOLERANCE else None

    for name, (lines, arcs) in board.manual.items():
        ends = [(start, end) for start, end in lines] + [(start, end) for start, _, end in arcs]
        group = {}  # end_key: the set of end keys it is joined to, shared

        for start, end in ends:
            joined = group.get(end_key(start), {end_key(start)}) | group.get(end_key(end), {end_key(end)})
            for key in joined:
                group[key] = joined
        for start, end in ends:
            nets = {hole_net(point) for point in group[end_key(start)]} - {None}
            if len(nets) == 1:
                applied.manual_nets[track_key(by_name[name], start, end)] = nets.pop()
    for start, end in board.manual_chevrons:
        net = hole_net(start) or hole_net(end)
        if net:
            applied.manual_nets[track_key(by_name[CHEVRON_LAYER_NAME], start, end)] = net


def on_grid(board, nets):
    """An Applied with these hole nets, and the grid of the board's holes to find them by."""
    return Applied(nets, columns=sorted((h.x, h.col) for h in board.holes.values() if h.row == 0),
                   rows=sorted((h.y, h.row) for h in board.holes.values() if h.col == 0))


def cleared(board):
    """The Applied that takes a PlacedBoard back to before any apply: each hole, chevron pad, island and track of the
    generator's has its strip's own name as its net again, as the generator made it, and no tracks are added. Each of
    the user's tracks takes the net of the hole at one of its ends, as the router gives it; the button that uses this
    also removes the locked tracks on the MCboard inner layers."""
    applied = on_grid(board, {key: h.strip for key, h in board.holes.items()})
    by_name = {name: layer for layer, name in board.layers.items()}
    ends = [(name, start, end) for name, (lines, arcs) in board.manual.items()
            for start, end in list(lines) + [(start, end) for start, _, end in arcs]]
    ends += [(CHEVRON_LAYER_NAME, start, end) for start, end in board.manual_chevrons]
    for name, start, end in ends:
        if name not in by_name:
            continue
        for point in (start, end):
            place, gap = applied.nearest(*point)
            if gap <= PLAN_PIN_TOLERANCE:
                applied.manual_nets[track_key(by_name[name], start, end)] = applied.nets[place]
                break
    return applied


def project_track_width(board_file):
    """The track width of the Default net class in the .kicad_pro beside a .kicad_pcb, which is what KiCad's router
    gives the user's tracks, or None if there is no such file or width."""
    try:
        project = json.loads(Path(board_file).with_suffix(".kicad_pro").read_text(encoding="utf-8"))
        width = next(c["track_width"] for c in project["net_settings"]["classes"] if c.get("name") == "Default")
        return float(width) if float(width) > 0 else None
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


def work_out(plan, width=None):
    """The Applied for a Plan. The plan's chevrons and wires from the user's tracks are there already, and get only
    their nets. The tracks added are width wide, so that they match the user's (project_track_width()), or else
    APPLY_CHEVRON_TRACK and APPLY_WIRE_TRACK."""
    if plan.errors:
        raise CannotApply("the plan has errors. Fix them and press the button again:\n"
                          + "\n".join("  " + e for e in plan.errors))
    board = plan.board
    wires, chevrons = wire_layers(board.layers), chevron_layer(board.layers)
    if not wires or not chevrons:
        raise CannotApply("the board has no inner copper layers for the tracks. Working copies made with "
                          "--working-copy have them; for this board, make a new working copy and swap it in")
    by_strip = strip_nets(plan)
    applied = on_grid(board, {key: by_strip.get(h.strip, h.strip) for key, h in board.holes.items()})
    planned_chevrons = [c for _, c in plan.chevrons if c not in plan.manual]
    for chevron in planned_chevrons:
        applied.tracks.append(Track(chevrons, (chevron.first.x, chevron.first.y), (chevron.second.x, chevron.second.y),
                                    width or APPLY_CHEVRON_TRACK, applied.nets[chevron.first.col, chevron.first.row]))
    planned_wires = [w for w in plan.wires if w not in plan.manual]
    numbers, layer_name = wire_layer_numbers(plan), dict(wires)
    for wire in planned_wires:
        net = by_strip.get(wire.first.strip, wire.first.strip)
        for start, end in zip(wire.path, wire.path[1:]):
            applied.tracks.append(Track(layer_name[numbers[wire]], start, end, width or APPLY_WIRE_TRACK, net))
    manual_nets(applied, board, board.layers)
    used = sorted({(numbers[w], layer_name[numbers[w]]) for w in planned_wires})
    applied.counts.append(f"Strips given a net of the schematic: {len(by_strip)}")
    applied.counts.append(f"Chevrons added on {CHEVRON_LAYER_NAME}: {len(planned_chevrons)}")
    applied.counts.append(f"Wires added: {len(planned_wires)}"
                          + (", on " + ", ".join(WIRE_LAYER_NAME.format(n) for n, _ in used) if used else ""))
    mine = len(plan.manual)
    applied.counts.append(f"Your own chevrons and wires kept: {mine}")
    if applied.tracks:
        applied.notes.append(f"The tracks added are {width:g} mm wide, as the Default net class of the project draws "
                             "yours." if width else f"The tracks added are {APPLY_WIRE_TRACK:g} mm wide: the board's "
                             "project was not found to take the width of its Default net class.")
    for layer, name in sorted(board.renamed.items()):
        applied.notes.append(f"{layer} is named {name} on this board, and is taken as {board.layers[layer]}.")
    if mine:
        applied.notes.append("Your own tracks keep their place and take the net of the holes they join.")
    for net, strips in sorted(plan.missing.items()):
        names = ", ".join(strip_label(board, s) for s in sorted(strips))
        applied.notes.append(f"{net} is not complete: the ratsnest shows what is left to {names}.")
    return applied


def is_applied(layers, layer, locked):
    """Whether a track was added by applying a plan: it is locked and on one of the MCboard inner layers."""
    return locked and layer in layers
