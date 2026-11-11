# SPDX-License-Identifier: GPL-3.0-or-later
"""Works out what to do on a placed board: which chevrons to close and which wires to add.

The board is a graph: the strips and buses are its nodes, and the chevrons its edges. A pin claims the
strip of its hole for its net. Each net then needs a tree that joins all its strips, through strips
that nobody else claims, and no two nets may share a strip. That is a Steiner forest with disjoint
nodes, which is NP-hard, and this is a heuristic that gives a good answer, not provably the best one:

  route_net()  joins one net: repeatedly the cheapest path from what it has to its next strip, where a
               wire between free holes is one more possible step, at a higher cost, and higher
               still for each mm along a column, so that wires come out mostly horizontal. Wires go on the
               top side, among the parts, so none may end or run under a part or over a pin
  attempt      routes all the nets in some order; a net routed early can block a later one
  solve()      tries many orders, and takes the best result; then it reroutes each net of that one
               in turn, keeping any improvement

The orders come from a seeded generator working on the nets sorted by name, and the work is limited
by a count of steps and not by time, so that the same circuit always gives the same plan, whatever the
order of the file and however fast the computer. check() then verifies the result independently, so
that a bug here gives an error and never a wrong plan.

Tracks the user draws on MCboard.Chevrons are chevrons already closed, and those on the MCboard.Wires layers are
wires already decided (manual.add_manual()). The search takes them as joins that cost nothing, and they are added
to the routes at the end.

What a plan is made of, and its report, are in plan.py; the user's tracks in manual.py.
"""

import heapq
import math
import random

from .constants import (PLAN_CHEVRON_COST, PLAN_EXTRA_BEND_COST, PLAN_MAX_ATTEMPTS, PLAN_MAX_STEPS, PLAN_PIN_EXACT,
                        PLAN_PIN_TOLERANCE, PLAN_SHARED_HOLE_COST, PLAN_SMD_ON_CHEVRON, PLAN_SMD_REACH,
                        PLAN_TOP_CHEVRON_COST, PLAN_VERTICAL_COST_PER_MM, PLAN_WIRE_COST, PLAN_WIRE_COST_PER_MM)
from .manual import add_manual, check_manual_wire, check_shared_pads
from .plan import BLOCKED, Plan, Route, Wire, is_no_connect, nearest_hole, overlap, path_stretches, strip_label

PLAN_DETOUR_STEP = 10  # a place visited by detour() is worth this many steps, as it does much more work


# ---------- from pins to strips ----------

def hole_of_pin(board, pin):
    """(Hole, distance) that gives a pin its strip: for an SMD pad on an island, the hole the island is tied to,
    which is not always the nearest one."""
    if pin.smd:
        for island in board.islands:
            if island.covers(pin.x, pin.y):
                return island.hole, 0.0
    return nearest_hole(board, pin.x, pin.y)


def over_grid(board, pin):
    """Whether a pin is within the area of the holes, even if not on one: a part badly placed rather than
    one still waiting beside the board."""
    xs = [h.x for h in board.holes.values()]
    ys = [h.y for h in board.holes.values()]
    return (min(xs) - PLAN_PIN_TOLERANCE <= pin.x <= max(xs) + PLAN_PIN_TOLERANCE
            and min(ys) - PLAN_PIN_TOLERANCE <= pin.y <= max(ys) + PLAN_PIN_TOLERANCE)


def assign_pins(board, plan):
    """Fill plan.pins and plan.terminals, and return the holes with a pin in them."""
    occupied = {}
    smd_seen = False
    for part in sorted(board.parts, key=lambda p: p.ref):
        on_holes = []
        for pin in part.pins:
            hole, distance = hole_of_pin(board, pin)
            reach = PLAN_SMD_REACH if pin.smd else PLAN_PIN_TOLERANCE
            on_holes.append((pin, hole if distance <= reach else None, distance))
        if not any(hole for _, hole, _ in on_holes) and not any(over_grid(board, pin) for pin in part.pins):
            plan.unplaced.append(part.ref)
            continue
        plan.placed.append(part.ref)
        for pin, hole, distance in on_holes:
            if hole is None:
                plan.errors.append(f"{pin.name} is {distance:.2f} mm from the nearest hole.")
                continue
            plan.pins.setdefault(hole.strip, []).append(pin)
            if pin.smd:
                smd_seen = True
                continue
            occupied.setdefault((hole.col, hole.row), []).append(pin)
            if distance > PLAN_PIN_EXACT:
                plan.warnings.append(f"{pin.name} is {distance:.2f} mm off the centre of hole {hole.name}.")
            if pin.drill > board.drill:
                plan.warnings.append(f"{pin.name} asks for a {pin.drill:g} mm drill and hole {hole.name} "
                                     f"has {board.drill:g} mm: its lead may not fit.")
    for (col, row), pins in sorted(occupied.items()):
        if len(pins) > 1:
            names = ", ".join(p.name for p in pins)
            plan.errors.append(f"Hole {board.holes[col, row].name} has {len(pins)} pins: {names}.")
    if smd_seen:
        plan.warnings.append("SMD pads were taken to connect to their island, or else their nearest hole. "
                             "Check them by eye.")
    return set(occupied)


def claim_strips(plan):
    """Who owns each strip because of its pins: (owner, terminals of each net)."""
    owner, terminals = {}, {}
    for strip, pins in sorted(plan.pins.items()):
        nets = sorted({p.net for p in pins if not is_no_connect(p.net)})
        if len(nets) > 1:
            board = plan.board
            names = " and ".join(f"{p.name} ({p.net}, hole {hole_of_pin(board, p)[0].name})"
                                 for p in pins if not is_no_connect(p.net))
            plan.errors.append(f"Short: {names} are joined by the copper of {strip_label(board, strip)}. "
                               "Move one of them.")
            owner[strip] = BLOCKED
        elif nets:
            owner[strip] = nets[0]
            terminals.setdefault(nets[0], set()).add(strip)
            for pin in pins:
                if is_no_connect(pin.net):
                    plan.warnings.append(f"{pin.name} has no net, but shares {strip_label(plan.board, strip)} "
                                         f"with {nets[0]}.")
        else:
            owner[strip] = BLOCKED
    return owner, terminals


def in_box(box, x, y):
    """Say whether a point is inside a box, which may be None."""
    return box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]


class Problem:
    """What stays the same in every attempt: the graph, what the pins claim and the free holes."""

    def __init__(self, board, plan, occupied):
        """Work out the strips each net needs, the usable chevrons and the holes wires may use."""
        self.board = board
        self.owner, self.terminals = claim_strips(plan)
        self.edges = {}
        smd = [p for part in board.parts for p in part.pins if p.smd]
        for chevron in sorted(board.chevrons, key=lambda c: (c.side, c.ref)):
            if chevron.side == "F" and any(math.hypot(p.x - chevron.x, p.y - chevron.y) < PLAN_SMD_ON_CHEVRON
                                           for p in smd):
                continue  # a part is already sitting on it
            a, b = chevron.first.strip, chevron.second.strip
            self.edges.setdefault(a, []).append((b, chevron))
            self.edges.setdefault(b, []).append((a, chevron))
        boxes = [p.box for p in board.parts if p.ref in plan.placed]
        self.boxes = [b for b in boxes if b is not None]
        self.occupied = occupied
        self.free = {}
        for (col, row), hole in sorted(board.holes.items()):
            if (col, row) not in occupied and not any(in_box(b, hole.x, hole.y) for b in self.boxes):
                self.free.setdefault(hole.strip, []).append(hole)
        self.barred = self.wire_obstacles()
        self.steps = 0
        self.manual_links = {}  # strip: the strips manual tracks join it to
        self.manual_wires = []

    def row_place(self, y):
        """Where a sheet y falls among the rows of holes, as a row number with a fraction. Rows are numbered
        by holes, and a space in the pattern leaves more than a pitch between two of them."""
        ys = self.row_ys
        if len(ys) == 1 or y <= ys[0]:
            return (y - ys[0]) / self.pitch
        for row, (here, there) in enumerate(zip(ys, ys[1:])):
            if y <= there:
                return row + (y - here) / (there - here)
        return len(ys) - 1 + (y - ys[-1]) / self.pitch

    def wire_obstacles(self):
        """{line: [(from, to)]}, what a wire cannot run through along each row and column, in rows and columns
        of holes."""
        holes = self.board.holes
        origin = holes[0, 0]
        neighbour = holes.get((1, 0)) or holes.get((0, 1))
        pitch = self.pitch = math.hypot(neighbour.x - origin.x, neighbour.y - origin.y) if neighbour else 1.0
        self.row_ys = [holes[0, row].y for row in range(self.board.rows)]
        regular = all(abs(b - a - pitch) < 1e-6 for a, b in zip(self.row_ys, self.row_ys[1:]))
        barred = {}
        for box in self.boxes:
            cols = (box[0] - origin.x) / pitch, (box[2] - origin.x) / pitch
            if regular:
                rows = (box[1] - origin.y) / pitch, (box[3] - origin.y) / pitch
            else:
                rows = self.row_place(box[1]), self.row_place(box[3])
            for row in range(self.board.rows):
                if rows[0] <= row <= rows[1]:
                    barred.setdefault(("row", row), []).append(cols)
            for col in range(self.board.cols):
                if cols[0] <= col <= cols[1]:
                    barred.setdefault(("col", col), []).append(rows)
        for col, row in self.occupied:
            barred.setdefault(("row", row), []).append((col, col))
            barred.setdefault(("col", col), []).append((row, row))
        return barred

    def is_open(self, points):
        """Say whether a wire through those holes keeps clear of the parts and pins."""
        return not any(max(start, a) <= min(end, b) for line, start, end in path_stretches(points)
                       for a, b in self.barred.get(line, ()))

    def wire_cost(self, points):
        """Return the cost of a wire: fixed, plus its length, more along columns, plus extra bends."""
        along_row = sum(abs(a.x - b.x) for a, b in zip(points, points[1:]))
        along_col = sum(abs(a.y - b.y) for a, b in zip(points, points[1:]))
        return (PLAN_WIRE_COST + PLAN_WIRE_COST_PER_MM * (along_row + along_col)
                + PLAN_VERTICAL_COST_PER_MM * along_col
                + PLAN_EXTRA_BEND_COST * max(0, len(points) - 3))

    def ways(self, first, second):
        """The corners of the wires with at most one bend between two holes."""
        if first.row == second.row or first.col == second.col:
            return [()]
        holes = self.board.holes
        return [(holes[second.col, first.row],), (holes[first.col, second.row],)]

    def detour(self, state, starts, goals):
        """(cost, first, second, corners) of the cheapest wire from a hole of starts to one of goals, with as
        many bends as it takes to go round the parts. None if the parts close the way completely."""
        goals = set(goals)
        pitch_row = PLAN_WIRE_COST_PER_MM * self.pitch
        pitch_col = pitch_row + PLAN_VERTICAL_COST_PER_MM * self.pitch
        holes = self.board.holes
        dist, before, heap = {}, {}, []
        for hole in starts:
            for along in ("row", "col"):
                dist[hole, along] = 0.0
                heap.append((0.0, hole.col, hole.row, along))
        heapq.heapify(heap)
        while heap:
            d, col, row, along = heapq.heappop(heap)
            here = holes[col, row]
            if d > dist[here, along]:
                continue
            self.steps += PLAN_DETOUR_STEP
            if here in goals:
                cells = [here]
                node = (here, along)
                while node in before:
                    node = before[node]
                    cells.append(node[0])
                cells.reverse()
                corners = tuple(b for a, b, c in zip(cells, cells[1:], cells[2:])
                                if (a.row == b.row) != (b.row == c.row))
                points = (cells[0], *corners, here)
                cost = self.wire_cost(points) + PLAN_SHARED_HOLE_COST * state.shared(path_stretches(points))
                return cost, cells[0], here, corners
            for step_col, step_row in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                there = holes.get((col + step_col, row + step_row))
                if there is None or not self.is_open((here, there)):
                    continue
                way = "row" if step_row == 0 else "col"
                # A step along a column can cross a space of the pattern, and so be longer than a pitch.
                pitches = 1 if way == "row" else max(1, round(abs(there.y - here.y) / self.pitch))
                cost = d + (pitch_row if way == "row" else pitch_col * pitches)
                cost += PLAN_SHARED_HOLE_COST * state.shared(path_stretches((here, there)))
                if way != along:
                    cost += PLAN_EXTRA_BEND_COST
                if cost < dist.get((there, way), math.inf):
                    dist[there, way] = cost
                    before[there, way] = (here, along)
                    heapq.heappush(heap, (cost, there.col, there.row, way))
        return None

    def wires_from(self, strip, state, net, ends):
        """{strip: (cost, hole, hole, corners)}, the cheapest wire to each strip it can reach: of any shape
        to the given strips, and horizontal to any strip nobody else owns, to carry on from there by chevron.
        Wires of any shape to every strip would be too slow to try."""
        best = self.free_wires(strip, state, ends)
        for there, wire in self.horizontal_wires(strip, state, net).items():
            if there not in best or wire[0] < best[there][0]:
                best[there] = wire
        return best

    def free_wires(self, strip, state, ends):
        """Between any two free holes, towards the given strips."""
        best = {}
        mine = state.free.get(strip, ())
        for there in ends:
            if there == strip:
                continue
            self.steps += len(mine) * len(state.free.get(there, ()))
            for first in mine:
                for second in state.free.get(there, ()):
                    cost = self.wire_cost((first, second))
                    way = state.way_for(first, second) if there not in best or cost < best[there][0] else None
                    if way:
                        cost += PLAN_SHARED_HOLE_COST * way[0]
                        if there not in best or cost < best[there][0]:
                            best[there] = (cost, first, second, way[1])
            if there not in best and mine and state.free.get(there) and self.steps < PLAN_MAX_STEPS:
                found = self.detour(state, mine, state.free[there])
                if found:
                    best[there] = found
        return best

    def horizontal_wires(self, strip, state, net):
        """Along the rows of the free holes of the strip. A bus takes up its whole row and so never has a
        wire, and every strip nobody else owns counts, not just those the net needs."""
        best = {}
        for first in state.free.get(strip, ()):
            self.steps += self.board.cols
            for col in range(self.board.cols):
                second = self.board.holes.get((col, first.row))
                if second is None or second.strip == strip or state.owner.get(second.strip, net) != net:
                    continue
                if second in state.free.get(second.strip, ()):
                    cost = self.wire_cost((first, second))
                    way = None
                    if second.strip not in best or cost < best[second.strip][0]:
                        way = state.way_for(first, second)
                    if way:
                        cost += PLAN_SHARED_HOLE_COST * way[0]
                        if second.strip not in best or cost < best[second.strip][0]:
                            best[second.strip] = (cost, first, second, way[1])
        return best

    def chevron_cost(self, chevron):
        """Return the cost of closing a chevron, slightly higher on the top side so the bottom one wins."""
        return PLAN_TOP_CHEVRON_COST if chevron.side == "F" else PLAN_CHEVRON_COST


class State:
    """Who owns what, which holes are free and where the wires run, during one attempt."""

    def __init__(self, problem, routes=()):
        """Start from the problem's free holes, with the given routes already taken."""
        self.problem = problem
        self.owner = dict(problem.owner)
        self.free = {strip: list(holes) for strip, holes in problem.free.items()}
        self.laid = {}  # line: [(from, to)], the stretches of the wires so far
        for wire in problem.manual_wires:  # their holes are not in problem.free
            for line, start, end in wire.stretches:
                self.laid.setdefault(line, []).append((start, end))
        for route in routes:
            self.take(route)

    def take(self, route):
        """Give a route's strips to its net and lay its wires."""
        for strip in route.strips:
            self.owner[strip] = route.net
        for wire in route.wires:
            self.lay(wire)

    def lay(self, wire):
        """Use up a wire's two end holes and record its stretches."""
        self.free[wire.first.strip].remove(wire.first)
        self.free[wire.second.strip].remove(wire.second)
        for line, start, end in wire.stretches:
            self.laid.setdefault(line, []).append((start, end))

    def shared(self, stretches):
        """Return the length of those stretches that runs along wires already laid."""
        return sum(overlap(start, end, self.laid.get(line, ())) for line, start, end in stretches)

    def way_for(self, first, second):
        """(pitches shared with other wires, corners) for the better way of laying a wire with at most one
        bend, or None if parts are in the way of both."""
        problem = self.problem
        ways = [(self.shared(path_stretches((first, *corners, second))), i, corners)
                for i, corners in enumerate(problem.ways(first, second))
                if problem.is_open((first, *corners, second))]
        if not ways:
            return None
        shared, _, corners = min(ways, key=lambda way: way[:2])
        return shared, corners


# ---------- routing ----------

def wire_ends(problem, state, net, pending):
    """Where a free wire may end: a strip still to be reached, or, if that strip has no free hole, a
    neighbour of it that nobody owns, from where a chevron gets there."""
    ends = set()
    for strip in pending:
        if state.free.get(strip):
            ends.add(strip)
        else:
            ends.update(there for there, _ in problem.edges.get(strip, ())
                        if state.owner.get(there, net) == net and state.free.get(there))
    return sorted(ends)


def shortest_step(problem, state, net, group, pending):
    """The cheapest path from the strips of the group to one of the pending ones, as (strip, step) pairs,
    where a step is a Chevron, a (Hole, Hole, corners) wire or None for a manual track, the strip nearest the
    pending one first. None if there is none."""
    ends = wire_ends(problem, state, net, pending)
    dist = {s: 0.0 for s in group}
    before = {}
    heap = [(0.0, s) for s in sorted(group)]
    heapq.heapify(heap)
    while heap:
        d, here = heapq.heappop(heap)
        if d > dist[here]:
            continue
        problem.steps += 1
        if here in pending:
            path = []
            while here in before:
                previous, step = before[here]
                path.append((here, step))
                here = previous
            return path
        options = [(there, problem.chevron_cost(c), c) for there, c in problem.edges.get(here, ())
                   if state.owner.get(there, net) == net]
        options += [(there, 0.0, None) for there in problem.manual_links.get(here, ())]
        for there, wire in problem.wires_from(here, state, net, ends).items():
            options.append((there, wire[0], wire[1:]))
        for there, cost, step in options:
            if d + cost < dist.get(there, math.inf):
                dist[there] = d + cost
                before[there] = (here, step)
                heapq.heappush(heap, (d + cost, there))
    return None


def route_net(problem, state, net):
    """Join all the strips of a net. The route is taken into the state."""
    ordered = sorted(problem.terminals[net])
    route = Route(net, strips=set(ordered))
    group, pending = {ordered[0]}, set(ordered[1:])
    while pending:
        path = shortest_step(problem, state, net, group, pending)
        if path is None:
            route.missing = pending
            break
        # The search lays nothing, so two wires of a path may want the same hole. The path then stops there,
        # and the next search goes on from what has been done. Its first step is always possible.
        for strip, step in reversed(path):
            wire = None
            if isinstance(step, tuple):
                first, second, corners = step
                if first not in state.free[first.strip] or second not in state.free[second.strip]:
                    break
                wire = Wire(net, first, second, corners)
            group.add(strip)
            pending.discard(strip)
            route.strips.add(strip)
            state.owner[strip] = net
            if wire:
                route.wires.append(wire)
                state.lay(wire)
            elif step is not None:
                route.chevrons.append(step)
    return route


def shared_stretches(wires):
    """(wire, wire, stretch, pitches) wherever two wires run along together. The stretch is (line, from, to)."""
    found = []
    for i, wire in enumerate(wires):
        for other in wires[:i]:
            for line, start, end in wire.stretches:
                for other_line, a, b in other.stretches:
                    if line == other_line and max(start, a) < min(end, b):
                        low, high = max(start, a), min(end, b)
                        found.append((other, wire, (line, low, high), high - low))
    return found


def cost_of(problem, routes):
    """Lower is better. Failing to connect a strip is worse than anything else."""
    missing = sum(len(r.missing) for r in routes.values())
    cost = sum(problem.chevron_cost(c) for r in routes.values() for c in r.chevrons)
    wires = [w for r in routes.values() for w in r.wires]
    cost += sum(problem.wire_cost(w.points) for w in wires)
    cost += PLAN_SHARED_HOLE_COST * sum(n for _, _, _, n in shared_stretches(wires))
    return missing, round(cost, 6)


def attempt(problem, order):
    """Route all the nets one after the other in that order, and return their routes."""
    state = State(problem)
    return {net: route_net(problem, state, net) for net in order}


def improve(problem, routes):
    """Reroute each net with all the others in place, until that gains nothing."""
    best = cost_of(problem, routes)
    changed = True
    while changed and problem.steps < PLAN_MAX_STEPS:
        changed = False
        for net in sorted(routes):
            others = [r for n, r in routes.items() if n != net]
            candidate = route_net(problem, State(problem, others), net)
            trial = dict(routes)
            trial[net] = candidate
            cost = cost_of(problem, trial)
            if cost < best:
                routes, best, changed = trial, cost, True
    return routes


def solve_routes(problem):
    """The best routes found, and the number of orders tried."""
    nets = sorted(net for net, strips in problem.terminals.items() if len(strips) > 1)
    if not nets:
        return {}, 0
    randomizer = random.Random(0)
    largest_first = sorted(nets, key=lambda n: (-len(problem.terminals[n]), n))
    best, best_cost, tries = None, None, 0
    order = largest_first
    while tries < PLAN_MAX_ATTEMPTS and problem.steps < PLAN_MAX_STEPS:
        tries += 1
        routes = attempt(problem, order)
        cost = cost_of(problem, routes)
        if best is None or cost < best_cost:
            best = improve(problem, routes)
            best_cost = cost_of(problem, best)
        if tries % 2:
            order = list(nets)
            randomizer.shuffle(order)
        else:
            # The nets that came off worst go first next time.
            def trouble(net):
                """Return a sort key that puts the nets with the worst routes first."""
                return -(len(best[net].missing) * 100 + len(best[net].wires) * 10 + len(best[net].chevrons))
            shuffled = list(nets)
            randomizer.shuffle(shuffled)
            order = sorted(shuffled, key=trouble)
    return best, tries


def check(plan, problem):
    """Verify a plan from scratch: all the strips of a net joined, and no two nets joined together."""
    parent = {}

    def root(s):
        """Find the set a strip belongs to, with path halving."""
        parent.setdefault(s, s)
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s

    for route in plan.routes.values():
        for chevron in route.chevrons:
            parent[root(chevron.first.strip)] = root(chevron.second.strip)
        for wire in route.wires:
            parent[root(wire.first.strip)] = root(wire.second.strip)
    seen = {}
    for strip, owner in problem.owner.items():
        if owner == BLOCKED:
            net = f"blocked {strip}"
        else:
            net = owner
        if seen.setdefault(root(strip), net) != net:
            raise AssertionError(f"plan joins {net} and {seen[root(strip)]}")
    for net, strips in problem.terminals.items():
        missing = plan.routes[net].missing if net in plan.routes else set()
        if len({root(s) for s in strips - missing}) > 1:
            raise AssertionError(f"plan leaves {net} in pieces")
    # The user's own wires may go anywhere and share holes; check_manual_wire() and check_shared_pads() report
    # them. A planned wire ends neither on another planned wire's hole nor on a manual wire's.
    planned = [w for w in plan.wires if w not in plan.manual]
    wire_holes = [h for w in planned for h in (w.first, w.second)]
    if len(wire_holes) != len(set(wire_holes)):
        raise AssertionError("plan uses a hole for two wires")
    free = {h for holes in problem.free.values() for h in holes}
    if not free.issuperset(h for w in planned for h in (w.first, w.second)):
        raise AssertionError("plan puts a wire in a hole with a pin or under a part, or on a manual wire")
    pins = [(hole.x, hole.y, hole.x, hole.y) for hole in (problem.board.holes[k] for k in problem.occupied)]
    for wire in planned:
        for (x0, y0), (x1, y1) in zip(wire.path, wire.path[1:]):
            if x0 != x1 and y0 != y1:
                raise AssertionError(f"plan has a wire of {wire.net} that is not along a row or a column")
            for box in problem.boxes + pins:
                if (max(min(x0, x1), box[0]) <= min(max(x0, x1), box[2])
                        and max(min(y0, y1), box[1]) <= min(max(y0, y1), box[3])):
                    raise AssertionError(f"plan runs a wire of {wire.net} into a part or a pin at {box}")


def under_part(board, plan, chevron):
    """The part whose courtyard covers a chevron on the top side, or None."""
    if chevron.side != "F":
        return None
    for part in board.parts:
        if part.ref in plan.placed and in_box(part.box, chevron.x, chevron.y):
            return part.ref
    return None


def shut_in_strips(problem, plan):
    """The strips of incomplete nets with no way out: no hole for a wire, and chevrons only to other nets. The search
    grows from one strip and names those it did not reach, but the one shut in may well be where it started, so what
    it did reach is looked at as well, as a whole."""
    owner = dict(problem.owner)
    for net, route in plan.routes.items():
        owner.update((strip, net) for strip in route.strips)

    def shut(side, net):
        return (all(not problem.free.get(strip) for strip in side)
                and all(owner.get(there, net) != net
                        for strip in side for there, _ in problem.edges.get(strip, ()) if there not in side))

    found = set()
    for net, missing in plan.missing.items():
        reached = plan.routes[net].strips - missing
        if reached and shut(reached, net):
            found |= reached
        found |= {strip for strip in missing if shut({strip}, net)}
    return found


def solve(board, search=True):
    """The Plan for a PlacedBoard. With search False it has only the user's tracks, with no routes looked for and
    nothing checked but those tracks, for the nets button."""
    plan = Plan(board, {})
    occupied = assign_pins(board, plan)
    problem = Problem(board, plan, occupied)
    plan.terminals = problem.terminals
    manual = add_manual(problem, plan)
    plan.routes, plan.attempts = solve_routes(problem) if search else ({}, 0)
    for net, extra in manual.items():
        route = plan.routes.setdefault(net, Route(net))
        route.strips |= extra.strips
        route.chevrons += extra.chevrons
        route.wires += extra.wires
        plan.manual.update(extra.chevrons, extra.wires)
    if search:
        check(plan, problem)
    for wire in problem.manual_wires:
        check_manual_wire(board, plan, wire)
    check_shared_pads(plan, problem.manual_wires)
    if not search:
        return plan
    plan.no_way_out = shut_in_strips(problem, plan)
    plan.wire_room = {h.strip for h in board.holes.values()
                      if h.strip in plan.no_way_out and (h.col, h.row) not in problem.occupied}
    for net, chevron in plan.chevrons:
        part = under_part(board, plan, chevron)
        if part:
            plan.warnings.append(f"Chevron {chevron.name} ({net}) is under {part}: close it before fitting.")
    for one, other, ((kind, line), low, high), _ in shared_stretches(plan.wires):
        ends = [board.holes[(n, line) if kind == "row" else (line, n)].name for n in (low, high)]
        nets = f"Two {one.net} wires" if one.net == other.net else f"Wires of {one.net} and {other.net}"
        plan.warnings.append(f"{nets} run together from {ends[0]} to {ends[1]}.")
    return plan
