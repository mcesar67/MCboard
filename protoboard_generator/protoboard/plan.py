# SPDX-License-Identifier: GPL-3.0-or-later
"""What a plan is made of, and the report of it as text.

A Plan holds a Route for each net: the strips it uses, the chevrons to close and the Wires to add. A Wire joins two
holes; a manual one, drawn by the user, may turn at Bends, which are not holes. planner.solve() makes the Plan, and
plan_pdf, apply and the buttons read it. This module only holds the data and the lookups they share.
"""

import math
from dataclasses import dataclass, field

from .constants import PLAN_WIRE_BEND, WIRE_LAYER_NAME

BLOCKED = "\0"  # owner of a strip that holds only pins connected to nothing
MANUAL_NO_NET = "(no net)"  # owner of strips joined by manual tracks where no pin has a net


def is_no_connect(net):
    """Say whether a pad's net needs no connection: none, or KiCad's unconnected- nets."""
    return not net or net.startswith("unconnected-")


@dataclass(frozen=True)
class Bend:
    """A corner of a manual wire, which connects to nothing, so it is not a hole even if it is over one."""
    x: float
    y: float
    name: str


@dataclass(frozen=True)
class Wire:
    """A wire on the top side. A planned one joins two free holes along rows and columns; a manual one may
    end on any hole and run anywhere."""
    net: str
    first: object   # Hole
    second: object  # Hole
    corners: tuple = ()  # the Holes where it turns, in order, or Bends on a manual wire
    curve: tuple = ()    # its points on the sheet, if it has drawn arcs
    layer: int = field(default=0, compare=False)  # n of the MCboard.Wires<n> it is on, for a manual one

    @property
    def points(self):
        """Return its holes in order: both ends and the corners between them."""
        return (self.first, *self.corners, self.second)

    @property
    def length(self):
        """Along its path, not straight from end to end."""
        path = self.path
        return sum(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(path, path[1:]))

    @property
    def path(self):
        """The points it runs through on the sheet."""
        return list(self.curve) or [(hole.x, hole.y) for hole in self.points]

    @property
    def drawn(self):
        """The same with its corners rounded, to show that they connect to nothing."""
        return rounded_corners(self.path)

    @property
    def stretches(self):
        """Return its straight pieces, as path_stretches() gives them."""
        return path_stretches(self.points)


def rounded_corners(path, steps=6):
    """A path of points with a curve at each sharp turn. The small turns of an arc already drawn are left."""
    found = path[:1]
    for before, here, after in zip(path, path[1:], path[2:]):
        in_x, in_y = here[0] - before[0], here[1] - before[1]
        out_x, out_y = after[0] - here[0], after[1] - here[1]
        into, out = math.hypot(in_x, in_y), math.hypot(out_x, out_y)
        if in_x * out_x + in_y * out_y >= 0.85 * into * out:
            found.append(here)
            continue
        reach = min(PLAN_WIRE_BEND, into / 2, out / 2)
        start = (here[0] - in_x / into * reach, here[1] - in_y / into * reach)
        end = (here[0] + out_x / out * reach, here[1] + out_y / out * reach)
        for i in range(steps + 1):
            t = i / steps
            found.append(((1 - t) ** 2 * start[0] + 2 * t * (1 - t) * here[0] + t ** 2 * end[0],
                          (1 - t) ** 2 * start[1] + 2 * t * (1 - t) * here[1] + t ** 2 * end[1]))
    return found + path[-1:]


def path_stretches(points):
    """The straight pieces between Holes, as (line, from, to), where the line is a row or a column and the
    other two are places along it. A diagonal or a Bend, which only a manual wire can have, follows no line."""
    found = []
    for a, b in zip(points, points[1:]):
        if isinstance(a, Bend) or isinstance(b, Bend):
            continue
        if a.row == b.row:
            found.append((("row", a.row), min(a.col, b.col), max(a.col, b.col)))
        elif a.col == b.col:
            found.append((("col", a.col), min(a.row, b.row), max(a.row, b.row)))
    return found


def overlap(start, end, others):
    """How many pitches a piece of a line runs along others. Touching at a hole is not running along."""
    return sum(max(0, min(end, b) - max(start, a)) for a, b in others)


@dataclass
class Route:
    """What one net uses."""
    net: str
    strips: set = field(default_factory=set)
    chevrons: list = field(default_factory=list)
    wires: list = field(default_factory=list)
    missing: set = field(default_factory=set)  # strips it could not reach


@dataclass
class Plan:
    """The result of planning a board: the route of each net, and everything to report about them."""
    board: object
    routes: dict                                  # net: Route
    errors: list = field(default_factory=list)    # the board cannot work like this
    warnings: list = field(default_factory=list)  # it can, but look at it
    placed: list = field(default_factory=list)    # parts on the board
    unplaced: list = field(default_factory=list)  # parts with no pin on a hole
    terminals: dict = field(default_factory=dict)  # net: set of strips
    pins: dict = field(default_factory=dict)      # strip: list of Pin
    attempts: int = 0
    no_way_out: set = field(default_factory=set)  # strips of incomplete nets shut in: see planner.shut_in_strips()
    wire_room: set = field(default_factory=set)   # those of them with a hole, under a part, for a wire of the user's
    manual: set = field(default_factory=set)      # the chevrons and wires that come from manual tracks

    @property
    def chevrons(self):
        """(net, Chevron), sorted."""
        found = [(net, c) for net, r in self.routes.items() for c in r.chevrons]
        return sorted(found, key=lambda e: (e[0], e[1].first.col, e[1].first.row, e[1].side))

    @property
    def wires(self):
        """Return all the wires, in the order the report numbers them."""
        found = [w for r in self.routes.values() for w in r.wires]
        return sorted(found, key=lambda w: (w.net, w.first.col, w.first.row))

    @property
    def missing(self):
        """Return the strips left unconnected, per net."""
        return {net: r.missing for net, r in self.routes.items() if r.missing}

    @property
    def complete(self):
        """Say whether the board can be built as planned, with nothing to fix."""
        return not self.errors and not self.missing and not self.unplaced


# ---------- places on the board ----------

def strip_label(board, strip):
    """A strip as the silkscreen names it, by its end holes: 'strip D9 to F9', or 'bus row A'."""
    holes = sorted((h for h in board.holes.values() if h.strip == strip), key=lambda h: (h.row, h.col))
    first, last = holes[0], holes[-1]
    if first.row == last.row and first.col != last.col:
        return "bus row " + first.name.rstrip("0123456789")
    return f"strip {first.name} to {last.name}"


def nearest_hole(board, x, y):
    """(Hole, distance) of the hole closest to a point."""
    hole = min(board.holes.values(), key=lambda h: (h.x - x) ** 2 + (h.y - y) ** 2)
    return hole, math.hypot(hole.x - x, hole.y - y)


# ---------- the report ----------

def report(plan):
    """The plan as lines of text, for the console, the PDF and the plugin."""
    board = plan.board
    lines = [f"Board: {board.cols} x {board.rows} holes. Parts on it: {len(plan.placed)}. "
             f"Nets to join: {len(set(plan.routes) - {MANUAL_NO_NET})}."]
    if plan.errors or plan.unplaced or plan.missing:
        lines += ["", "PROBLEMS"]
        if plan.unplaced:
            lines.append("Not on the board, so not in this plan: " + ", ".join(plan.unplaced) + ".")
        lines += plan.errors
        for net, strips in sorted(plan.missing.items()):
            def named(group):
                return ", ".join(f"{strip_label(board, s)} ({', '.join(p.name for p in plan.pins[s])})"
                                 if plan.pins.get(s) else strip_label(board, s) for s in sorted(group))

            lines.append(f"{net} cannot reach {named(strips)}: no path through chevrons and wires clear of the parts.")
            boxed = plan.routes[net].strips & plan.no_way_out
            if boxed:
                fix = "Move a part."
                if boxed & plan.wire_room:
                    fix = f"Move a part, or draw a wire as a track on {WIRE_LAYER_NAME.format(1)} from a hole under it."
                lines.append(f"  {named(boxed)}: every hole has a pin or is under a part, and its chevrons lead "
                             f"to other nets. {fix}")
    chevrons = plan.chevrons
    if not chevrons and not plan.wires:
        lines += ["", "No chevrons to close and no wires to add."]
    if chevrons:
        lines += ["", f"Close {len(chevrons)} chevron{'' if len(chevrons) == 1 else 's'}:"]
        width = max(len(chevron.name) for _, chevron in chevrons)
    net_now = None
    for net, chevron in chevrons:
        if net != net_now:
            lines.append(f"  {net}:")
            net_now = net
        side = "top" if chevron.side == "F" else "bottom"
        mine = "  manual" if chevron in plan.manual else ""
        lines.append(f"    {chevron.name:<{width}}  {side:<6}  ({chevron.ref}){mine}")
    wires = plan.wires
    if wires:
        lines += ["", f"Add {len(wires)} wire{'' if len(wires) == 1 else 's'}:"]
        tag = len(f"W{len(wires)}")
        net = max(len(w.net) for w in wires) + 1
        first = max(len(w.first.name) for w in wires)
        second = max(len(w.second.name) for w in wires)
    for i, wire in enumerate(wires, 1):
        mine = "  manual" if wire in plan.manual else ""
        # A Bend's name already says "near"; a planned wire turns over a hole without being soldered to it
        corners = ", ".join(c.name if isinstance(c, Bend) else f"at {c.name}" for c in wire.corners)
        corners = (f"  corner{'s' if wire.corners[1:] else ''} {corners}"
                   if wire.corners[1:] or (mine and wire.corners) else "")
        lines.append(f"  {f'W{i}':<{tag}}  {wire.net + ':':<{net}}  {wire.first.name:<{first}} to "
                     f"{wire.second.name:<{second}}  {wire.length:5.1f} mm{corners}{mine}")
    if plan.warnings:
        lines += ["", "Check:"] + [f"  {w}" for w in plan.warnings]
    return lines
