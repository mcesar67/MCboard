# SPDX-License-Identifier: GPL-3.0-or-later
"""The chevrons and wires the user draws as tracks, taken into the plan.

A track on MCboard.Chevrons closes the chevron it crosses. The lines and arcs on each MCboard.Wires layer are
joined into chains that meet at Bends, and each chain is one Wire, with an end on a hole at each side
(manual_links()). Links that share strips form a group, which must belong to one net at most; add_manual() gives
their strips to that net and takes their holes from the free ones, so that the search has only to link them up.
A wire of the user's need not keep clear of the parts: it may be what they want, so check_manual_wire() warns and
does not refuse it.
"""

import math
from dataclasses import dataclass, replace

from .constants import CHEVRON_LAYER_NAME, PLAN_MANUAL_MEET, PLAN_PIN_TOLERANCE, WIRE_LAYER_NAME
from .kicad_file import Chevron
from .plan import BLOCKED, MANUAL_NO_NET, Bend, Route, Wire, nearest_hole, strip_label


@dataclass(frozen=True)
class ManualPiece:
    """A straight or curved track on a wire layer, between holes or Bends."""
    first: object
    second: object
    inner: tuple = ()     # the points of an arc between its ends
    middle: object = None  # the Bend that names an arc


def arc_points(start, middle, end, steps=8):
    """The points of an arc between its two ends."""
    ax, ay, bx, by, cx, cy = *start, *middle, *end
    det = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(det) < 1e-9:
        return (middle,)
    ux = ((ax ** 2 + ay ** 2) * (by - cy) + (bx ** 2 + by ** 2) * (cy - ay) + (cx ** 2 + cy ** 2) * (ay - by)) / det
    uy = ((ax ** 2 + ay ** 2) * (cx - bx) + (bx ** 2 + by ** 2) * (ax - cx) + (cx ** 2 + cy ** 2) * (bx - ax)) / det
    radius = math.hypot(ax - ux, ay - uy)
    a0, a1, a2 = (math.atan2(y - uy, x - ux) for x, y in (start, middle, end))
    full = 2 * math.pi
    sweep = (a2 - a0) % full if (a1 - a0) % full < (a2 - a0) % full else -((a0 - a2) % full)
    return tuple((ux + radius * math.cos(a0 + sweep * i / steps), uy + radius * math.sin(a0 + sweep * i / steps))
                 for i in range(1, steps))


def manual_pieces(board, plan, lines, arcs):
    """The lines and arcs of one wire layer as ManualPieces, without repeats. Where an arc meets something the
    point is a corner, even over a hole. Lines alone that meet at a hole both end on it, and anywhere else
    they make a corner."""
    spots, shapes = [], []

    def spot(point):
        """Return the index of the place a point is at, counting points that are all but the same."""
        for i, known in enumerate(spots):
            if math.hypot(known[0] - point[0], known[1] - point[1]) <= PLAN_MANUAL_MEET:
                return i
        spots.append(point)
        return len(spots) - 1

    for start, end in lines:
        shapes.append((spot(start), spot(end), None))
    for start, middle, end in arcs:
        shapes.append((spot(start), spot(end), (start, middle, end)))
    touching = [sum((a == i) + (b == i) for a, b, _ in shapes) for i in range(len(spots))]
    has_arc = [any(arc and i in (a, b) for a, b, arc in shapes) for i in range(len(spots))]

    def node(i):
        """Return what is at a place: the hole, unless it is a corner with an arc."""
        hole, gap = nearest_hole(board, *spots[i])
        if gap <= PLAN_PIN_TOLERANCE and not (has_arc[i] and touching[i] > 1):
            return hole
        return Bend(round(spots[i][0], 2), round(spots[i][1], 2), f"near {hole.name}")

    pieces = []
    for a, b, arc in shapes:
        first, second = node(a), node(b)
        if first == second and not arc:
            plan.warnings.append(f"A manual wire track starts and ends at {first.name}. It is left out.")
            continue
        if arc:
            near = nearest_hole(board, *arc[1])[0]
            piece = ManualPiece(first, second, arc_points(*arc), Bend(arc[1][0], arc[1][1], f"near {near.name}"))
        else:
            piece = ManualPiece(first, second)
        if piece not in pieces and replace(piece, first=second, second=first) not in pieces:
            pieces.append(piece)
    return pieces


def point_order(point):
    """Sort key for holes and Bends: by column, then row."""
    return point.x, point.y


def manual_chains(pieces):
    """The pieces split into chains that meet at Bends: at holes they do not chain, so that each is a wire."""
    chains = []
    for piece in pieces:
        touching = [c for c in chains if any(isinstance(p, Bend) and p in (o.first, o.second)
                                             for p in (piece.first, piece.second) for o in c)]
        for chain in touching:
            chains.remove(chain)
        chains.append([other for chain in touching for other in chain] + [piece])
    return chains


def manual_wire(board, plan, chain):
    """The Wire a chain of pieces draws, net still to be given, or None if it cannot be one, which is said. Only
    its two ends join anything, so they must be on holes, and its corners are Bends."""
    meetings = {}
    for piece in chain:
        for end in (piece.first, piece.second):
            meetings.setdefault(end, []).append(piece)
    bends = [p for p in meetings if isinstance(p, Bend)]
    ends = sorted((p for p in meetings if not isinstance(p, Bend)), key=point_order)
    loose = [p for p in bends if len(meetings[p]) == 1]
    for bend in loose:
        hole, gap = nearest_hole(board, bend.x, bend.y)
        plan.errors.append(f"A manual wire track ends {gap:.2f} mm from the nearest hole, {hole.name}. It is left out.")
    if loose:
        return None
    if any(len(meetings[p]) > 2 for p in bends) or len(ends) != 2:
        names = ", ".join(p.name for p in sorted(meetings, key=point_order))
        plan.errors.append(f"Manual wire tracks through {names} are not one wire with two ends. They are left out.")
        return None
    path, corners, here, piece, curved = [(ends[0].x, ends[0].y)], [], ends[0], meetings[ends[0]][0], False
    while True:
        there = piece.second if piece.first == here else piece.first
        inner = piece.inner if piece.first == here else piece.inner[::-1]
        path += inner
        if piece.middle:
            corners.append(piece.middle)
            curved = True
        path.append((there.x, there.y))
        if there == ends[1]:
            break
        following = next(p for p in meetings[there] if p != piece)
        if not piece.inner and not following.inner:
            corners.append(there)
        here, piece = there, following
    corners = [c for c in corners if c not in meetings or not in_line(path, c)]
    return Wire("", ends[0], ends[1], tuple(corners), tuple(path) if curved else ())


def in_line(path, bend):
    """Say whether a sharp corner is in fact in line with the points on either side."""
    i = path.index((bend.x, bend.y))
    (ax, ay), (bx, by), (cx, cy) = path[i - 1], path[i], path[i + 1]
    return abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) < 0.001


def manual_links(board, plan):
    """What the user's tracks ask for, as (strip, strip, Chevron or Wire). A track on the chevron layer between two
    holes with a chevron between them closes it, the bottom one if there are two. Every piece on a wire layer
    is part of a wire, even one across a chevron. Pieces on different wire layers never meet."""
    between = {}
    for chevron in sorted(board.chevrons, key=lambda c: c.side):
        between.setdefault(frozenset((chevron.first, chevron.second)), chevron)
    links, closed = [], set()
    for line in board.manual_chevrons:
        ends = [nearest_hole(board, *end) for end in line]
        far = [(hole, gap) for hole, gap in ends if gap > PLAN_PIN_TOLERANCE]
        chevron = between.get(frozenset(hole for hole, _ in ends))
        if far:
            plan.errors.append(f"A track on {CHEVRON_LAYER_NAME} ends {far[0][1]:.2f} mm from the nearest hole, "
                               f"{far[0][0].name}. It is left out.")
        elif not chevron:
            plan.errors.append(f"The track on {CHEVRON_LAYER_NAME} from {ends[0][0].name} to {ends[1][0].name} "
                               f"is not across one chevron. It is left out.")
        elif chevron not in closed:
            closed.add(chevron)
            links.append((chevron.first.strip, chevron.second.strip, chevron))
    prefix = WIRE_LAYER_NAME.format("")
    chains = [(name, chain) for name, (lines, arcs) in board.manual.items()
              for chain in manual_chains(manual_pieces(board, plan, lines, arcs))]
    for name, chain in chains:
        wire = manual_wire(board, plan, chain)
        if not wire:
            continue
        if name[len(prefix):].isdigit():
            wire = replace(wire, layer=int(name[len(prefix):]))
        if wire.first.strip == wire.second.strip:
            plan.warnings.append(f"The manual wire {wire.first.name} to {wire.second.name} joins two holes of "
                                 f"{strip_label(board, wire.first.strip)}, already joined. It is left out.")
        else:
            links.append((wire.first.strip, wire.second.strip, wire))
            chevron = between.get(frozenset((wire.first, wire.second)))
            if chevron and not wire.corners:
                plan.warnings.append(f"The manual wire {wire.first.name} to {wire.second.name} is across chevron "
                                     f"{chevron.name}. To close the chevron instead, draw the track on "
                                     f"{CHEVRON_LAYER_NAME}.")
    return links


def manual_groups(links):
    """The links split into groups that share strips, each as (strips, links)."""
    groups = []
    for link in links:
        touching = [g for g in groups if link[0] in g[0] or link[1] in g[0]]
        for group in touching:
            groups.remove(group)
        groups.append(({link[0], link[1]}.union(*(g[0] for g in touching)),
                       [link] + [other for g in touching for other in g[1]]))
    return sorted(groups, key=lambda g: sorted(g[0]))


def link_name(link):
    """Return the name of a manual chevron or wire in a message."""
    item = link[2]
    return item.name if isinstance(item, Chevron) else f"{item.first.name} to {item.second.name}"


def segment_hits_box(x0, y0, x1, y1, box):
    """Say whether a straight piece touches a box, clipping it against each side."""
    start, end = 0.0, 1.0
    for delta, low, high, origin in ((x1 - x0, box[0], box[2], x0), (y1 - y0, box[1], box[3], y0)):
        if delta == 0:
            if not low <= origin <= high:
                return False
            continue
        enter, leave = sorted(((low - origin) / delta, (high - origin) / delta))
        start, end = max(start, enter), min(end, leave)
    return start <= end


def check_manual_wire(board, plan, wire):
    """Add a warning for each part or pin a wire of the user's meets, which may well be what they want."""
    label = f"Manual wire {wire.first.name} to {wire.second.name} ({wire.net})"
    pieces = [(*a, *b) for a, b in zip(wire.path, wire.path[1:])]
    for part in board.parts:
        if part.ref not in plan.placed:
            continue
        if part.box and any(segment_hits_box(*piece, part.box) for piece in pieces):
            plan.warnings.append(f"{label} runs under {part.ref}: check that it can be fitted.")
        for pin in part.pins:
            if pin.smd:
                continue
            hole, gap = nearest_hole(board, pin.x, pin.y)
            if gap > PLAN_PIN_TOLERANCE:
                continue
            if hole in (wire.first, wire.second):
                plan.warnings.append(f"{label} ends on hole {hole.name}, which holds pin {pin.name}.")
            elif not part.box and any(segment_hits_box(*piece, (hole.x, hole.y, hole.x, hole.y)) for piece in pieces):
                plan.warnings.append(f"{label} runs over pin {pin.name}, in hole {hole.name}.")


def check_shared_pads(plan, wires):
    """Add a warning for each hole where two or more of the user's wires end, which may be what they want if
    the wires fit in it. A planned wire never ends where another wire does: see planner.State.lay() and
    planner.check()."""
    ends = {}
    for wire in wires:
        for hole in (wire.first, wire.second):
            ends.setdefault(hole, []).append(wire)
    for hole, sharing in sorted(ends.items(), key=lambda item: item[0].name):
        if len(sharing) > 1:
            names = ", ".join(sorted(f"{w.first.name} to {w.second.name}" for w in sharing))
            plan.warnings.append(f"Manual wires {names} ({sharing[0].net}) end on hole {hole.name}: check that "
                                 f"they fit in it together.")


def add_manual(problem, plan):
    """Take in the user's tracks: their strips go to their nets and their holes stop being free, so the search
    has only to link them up. Returns {net: Route} with what they add to each net."""
    board = problem.board
    if board.foreign:
        names = ", ".join(f"{layer} ({name})" for layer, name in board.foreign)
        plan.errors.append(f"Inner layers with names of their own: {names}. A working copy's inner layers are for "
                           "MCboard alone, so this board's are left out and the plan cannot be applied.")
    for layer, name in sorted(board.renamed.items()):
        plan.warnings.append(f"{layer} is named {name} on this board. MCboard takes the inner layers by position, "
                             f"so it is {board.layers[layer]}.")
    for layer, count in sorted(board.manual_other.items()):
        kinds = "straight tracks" if layer == CHEVRON_LAYER_NAME else "tracks"
        plan.warnings.append(f"Items on {layer} that are not {kinds} are left out: {count}.")
    manual = {}
    for strips, links in manual_groups(manual_links(board, plan)):
        names = ", ".join(sorted(link_name(link) for link in links))
        nets = sorted({problem.owner[s] for s in strips if s in problem.owner} - {BLOCKED})
        if len(nets) > 1:
            plan.errors.append(f"Manual tracks join {' and '.join(nets)}: {names}. They are left out.")
            continue
        if not nets:
            plan.warnings.append(f"Manual tracks {names} join no net, so the plan keeps off their strips.")
        net = nets[0] if nets else MANUAL_NO_NET
        for strip in sorted(strips):
            if problem.owner.get(strip) == BLOCKED and nets:
                held = ", ".join(p.name for p in plan.pins[strip])
                plan.warnings.append(f"Manual tracks {names} join {held}, with no net, to {net}.")
            problem.owner[strip] = net
        route = manual.setdefault(net, Route(net))
        route.strips |= strips
        for first, second, item in links:
            problem.manual_links.setdefault(first, []).append(second)
            problem.manual_links.setdefault(second, []).append(first)
            if isinstance(item, Chevron):
                route.chevrons.append(item)
                continue
            wire = replace(item, net=net)
            route.wires.append(wire)
            problem.manual_wires.append(wire)
            for hole in (wire.first, wire.second):  # a wire only passes over the holes on its way
                if hole in problem.free.get(hole.strip, ()):
                    problem.free[hole.strip].remove(hole)
    return manual
