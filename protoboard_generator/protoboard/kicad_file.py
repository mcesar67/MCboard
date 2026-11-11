# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads a placed board from a .kicad_pcb file, without KiCad.

Works on the file text, so it does not depend on the installed KiCad, and it reads the format the
generator writes (KiCad 9), those it wrote before (KiCad 7) and those KiCad 9 and 10 save. These tell it what is what:

  footprint "Hole"            a hole of the grid; its pad's net is the strip or bus it belongs to, unless a plan has
                              been applied, which gives it the schematic's net and puts the strip's in the Value
  footprint "ChevronJumper"   a chevron; its two pads have the nets of the holes it joins
  footprint "Island"          a square top pad; its pad's net is that of the strip it is tied to
  footprint "MountingHole"    an unplated hole
  anything else               a part placed by the user, whose pads are its pins

Those names are used instead of the references, which a netlist import could reuse on other parts. Row and column
come from the sorted positions of the holes.

Tracks on the inner copper layers named MCboard.Wires<n> and MCboard.Chevrons are what the user has decided on, which
manual.py reads: straight and curved tracks on a wire layer are wires, straight ones on the chevron layer close
chevrons. Locked tracks there are the apply button's, and are left out, unless read_board() is asked to take them
too, as the nets button does.

It keeps a copy of the drawing too, in the same form as Board.art, so that pdf.py can draw this board.
"""

import math
import re
from dataclasses import dataclass, field

from .constants import CHEVRON_LAYER_NAME, WIRE_LAYER_NAME, WORKING_CONNECTIONS_LAYER, WORKING_SILK_LAYERS
from .font import text_strokes, text_width
from .pattern import row_label

HOLE, CHEVRON, ISLAND, MOUNT = "Hole", "ChevronJumper", "Island", "MountingHole"
HOLE_VALUE = "hole"  # the Value the generator gives a Hole footprint; applying a plan puts the strip's name there
ART_LAYERS = ("F.Cu", "B.Cu", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask", "Edge.Cuts")
CIRCLE_STEPS = 48


class NotAPlacedBoard(Exception):
    """The file cannot be read, or it was not made by this generator."""


# ---------- S-expressions ----------

def parse(text):
    """The file as nested lists of strings, with the quotes removed."""
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+', text)
    stack, current = [], []
    for t in tokens:
        if t == "(":
            stack.append(current)
            current = []
        elif t == ")":
            if not stack:
                raise NotAPlacedBoard("unbalanced parentheses")
            stack[-1].append(current)
            current = stack.pop()
        elif t.startswith('"'):
            current.append(re.sub(r"\\(.)", r"\1", t[1:-1]))
        else:
            current.append(t)
    if stack or not current or not isinstance(current[0], list):
        raise NotAPlacedBoard("unbalanced parentheses")
    return current[0]


def kids(node, name):
    """Return the child lists of a node with that name."""
    return [n for n in node if isinstance(n, list) and n and n[0] == name]


def kid(node, name):
    """Return the first child list of a node with that name, or None."""
    found = kids(node, name)
    return found[0] if found else None


def point(node):
    """(x, y) of a node like (start 1 2) or (xy 1 2)."""
    return float(node[1]), float(node[2])


def layer_of(node):
    """Return the name of a node's layer, or None."""
    found = kid(node, "layer")
    return found[1] if found else None


def line_width(node):
    """Width of a drawn shape: (stroke (width w) ...) in new files, (width w) in old ones."""
    stroke = kid(node, "stroke")
    found = kid(stroke, "width") if stroke else kid(node, "width")
    return float(found[1]) if found else 0.0


def polygon(node):
    """Return the points of a node's pts list."""
    return [point(p) for p in kids(kid(node, "pts"), "xy")]


def reference(fp, field="Reference"):
    """Return a footprint's reference, or another of its fields such as "Value", as a property (newer files) or
    fp_text (older ones)."""
    for p in kids(fp, "property"):
        if p[1] == field:
            return p[2]
    for p in kids(fp, "fp_text"):
        if p[1] == field.lower():
            return p[2]
    return "?"


# ---------- what the board holds ----------

@dataclass(frozen=True)
class Hole:
    """A plated hole of the grid. Columns and rows are 0-based."""
    col: int
    row: int
    x: float
    y: float
    strip: str    # the name of its strip or bus, the net the generator gave it
    net: str = ""  # its pad's net now: the same, or the schematic's once a plan is applied

    @property
    def name(self):
        """As labelled on the silkscreen: row letter, then column number."""
        return f"{row_label(self.row)}{self.col + 1}"


@dataclass(frozen=True)
class Chevron:
    """A solder jumper between two neighbouring holes."""
    ref: str
    side: str     # "B" or "F"
    first: Hole
    second: Hole

    @property
    def x(self):
        """Return the sheet x of its centre."""
        return (self.first.x + self.second.x) / 2

    @property
    def y(self):
        """Return the sheet y of its centre."""
        return (self.first.y + self.second.y) / 2

    @property
    def name(self):
        """Return its name as the report gives it, such as C4-D4."""
        return f"{self.first.name}-{self.second.name}"


@dataclass(frozen=True)
class Island:
    """A square top pad of --top smt, tied for good to a strip."""
    x: float
    y: float
    half: float  # half its side
    hole: Hole   # one of the holes of the strip it is tied to

    def covers(self, x, y):
        """Say whether a point is on the square."""
        return abs(x - self.x) <= self.half and abs(y - self.y) <= self.half


@dataclass(frozen=True)
class Pin:
    """A pad of a placed part."""
    part: str
    number: str
    x: float
    y: float
    net: str
    smd: bool
    drill: float

    @property
    def name(self):
        """Return its name as the report gives it, such as R1.2."""
        return f"{self.part}.{self.number}"


@dataclass
class Part:
    """A placed part: a footprint that is not one of the generator's own."""
    ref: str
    pins: list = field(default_factory=list)
    box: tuple = None  # courtyard on the sheet: x0, y0, x1, y1


@dataclass
class PlacedBoard:
    """A generated board read from a file, with whatever parts have been placed on it."""
    path: str
    holes: dict      # (col, row): Hole
    chevrons: list
    parts: list
    art: dict        # layer: primitives, as in Board.art
    pth: list
    npth: list
    drill: float
    mount_drill: float
    corner: tuple    # the sheet position of the upper left board corner
    size: tuple      # width, height
    islands: list = field(default_factory=list)
    # The user's tracks. manual is {wire layer name: (lines, arcs)}, a line being ((x, y), (x, y)) and an arc
    # ((x, y), (x, y), (x, y)), start, middle and end; each layer is apart, as KiCad joins no tracks across layers.
    manual: dict = field(default_factory=dict)
    manual_chevrons: list = field(default_factory=list)  # straight tracks on MCboard.Chevrons: ((x, y), (x, y))
    manual_other: dict = field(default_factory=dict)  # {layer name: how many items on it mean nothing to the planner}
    layers: dict = field(default_factory=dict)  # {"In1.Cu": "MCboard.Wires1", ...}: the MCboard inner layers
    renamed: dict = field(default_factory=dict)  # {"In2.Cu": "MCboard.Wires2"}: those with another name on the board
    foreign: list = field(default_factory=list)  # [("In1.Cu", "GND")]: inner layers that are not a working copy's

    @property
    def cols(self):
        """Return the number of hole columns."""
        return 1 + max(c for c, _ in self.holes)

    @property
    def rows(self):
        """Return the number of hole rows."""
        return 1 + max(r for _, r in self.holes)


# ---------- geometry ----------

def on_sheet(fp_x, fp_y, angle, u, v):
    """A point in footprint coordinates, on the sheet. KiCad's y grows downwards and a positive angle
    turns counterclockwise as seen on screen."""
    a = math.radians(angle)
    return fp_x + u * math.cos(a) + v * math.sin(a), fp_y - u * math.sin(a) + v * math.cos(a)


def footprint_place(fp):
    """Return a footprint's x, y and rotation in degrees."""
    at = kid(fp, "at")
    return float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0


def drill_of(pad):
    """Largest drill size of a pad, or 0. Forms: (drill 1), (drill oval 1 2), (drill oval 1)."""
    found = kid(pad, "drill")
    if not found:
        return 0.0
    numbers = [float(n) for n in found[1:] if isinstance(n, str) and n != "oval"]
    return max(numbers) if numbers else 0.0


def pad_net(pad):
    """The name is the last item both in (net 3 "A") and in KiCad 10's (net "A")."""
    found = kid(pad, "net")
    return found[-1] if found else ""


def courtyard(fp, place):
    """Bounding box on the sheet of a footprint's courtyard, or None if it has none."""
    points = []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
        for shape in kids(fp, kind):
            if layer_of(shape) not in ("F.CrtYd", "B.CrtYd"):
                continue
            if kind == "fp_poly":
                local = polygon(shape)
            elif kind == "fp_circle":
                cx, cy = point(kid(shape, "center"))
                ex, ey = point(kid(shape, "end"))
                r = math.hypot(ex - cx, ey - cy)
                local = [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
            else:
                ends = [point(kid(shape, n)) for n in ("start", "mid", "end") if kid(shape, n)]
                # The rectangle's other two corners count too, once the part is turned.
                local = ends + ([(ends[0][0], ends[1][1]), (ends[1][0], ends[0][1])] if kind == "fp_rect" else [])
            points += [on_sheet(*place, u, v) for u, v in local]
    if not points:
        return None
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


# ---------- the drawing, for the PDF ----------

def text_primitives(node):
    """The strokes of a gr_text, as KiCad would draw it. Text without a glyph is left out."""
    effects = kid(node, "effects")
    font = kid(effects, "font") if effects else None
    size = kid(font, "size") if font else None
    if not size:
        return []
    hide = kid(node, "hide") or (effects and (kid(effects, "hide") or "hide" in effects))
    if hide:
        return []
    height = float(size[1])
    thickness = kid(font, "thickness")
    width = float(thickness[1]) if thickness else height / 8
    justify = kid(effects, "justify") or []
    at = kid(node, "at")
    x, y = float(at[1]), float(at[2])
    angle = float(at[3]) if len(at) > 3 else 0.0
    text = node[1]
    try:
        strokes = text_strokes(text, height)
        shift = text_width(text, height) / 2
    except (IndexError, ValueError):
        return []
    shift = shift if "left" in justify else -shift if "right" in justify else 0
    out = []
    for polyline in strokes:
        polyline = [(u + shift, v) for u, v in polyline]
        if "mirror" in justify:
            polyline = [(-u, v) for u, v in polyline]
        placed = [on_sheet(x, y, angle, u, v) for u, v in polyline]
        out += [("line", *p, *q, width) for p, q in zip(placed, placed[1:])]
    return out


def shape_primitives(node):
    """A shape drawn directly on the board, as primitives."""
    kind, width = node[0], line_width(node)
    if kind == "gr_text":
        return text_primitives(node)
    if kind == "gr_line":
        return [("line", *point(kid(node, "start")), *point(kid(node, "end")), width)]
    if kind == "gr_arc" and kid(node, "mid"):
        return [("arc", point(kid(node, "start")), point(kid(node, "mid")), point(kid(node, "end")), width)]
    if kind == "gr_rect":
        (x0, y0), (x1, y1) = point(kid(node, "start")), point(kid(node, "end"))
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        return [("line", *p, *q, width) for p, q in zip(corners, corners[1:])]
    if kind == "gr_circle":
        cx, cy = point(kid(node, "center"))
        ex, ey = point(kid(node, "end"))
        r = math.hypot(ex - cx, ey - cy)
        ring = [(cx + r * math.cos(2 * math.pi * i / CIRCLE_STEPS), cy + r * math.sin(2 * math.pi * i / CIRCLE_STEPS))
                for i in range(CIRCLE_STEPS + 1)]
        return [("line", *p, *q, width) for p, q in zip(ring, ring[1:])]
    if kind == "gr_poly":
        points = polygon(node)
        fill = kid(node, "fill")
        if fill and fill[1] not in ("none", "no"):
            return [("region", points)]
        closed = points + points[:1]
        return [("line", *p, *q, width) for p, q in zip(closed, closed[1:])]
    return []


def board_art(tree):
    """Everything drawn on the board itself and on its tracks, per layer. The planner's tracks are on inner layers,
    which are not drawn."""
    art = {layer: [] for layer in ART_LAYERS}
    for kind in ("gr_text", "gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly"):
        for node in kids(tree, kind):
            # The circles around the drills of a working copy: the PDF draws the drills itself.
            if kind == "gr_circle" and layer_of(node) == WORKING_CONNECTIONS_LAYER[0]:
                continue
            # A working copy keeps its top text and its connection lines elsewhere; the PDF draws them as silkscreen.
            layer = "F.SilkS" if layer_of(node) in WORKING_SILK_LAYERS else layer_of(node)
            if layer in art:
                art[layer] += shape_primitives(node)
    for node in kids(tree, "segment"):
        if layer_of(node) in art:
            art[layer_of(node)].append(("line", *point(kid(node, "start")), *point(kid(node, "end")),
                                        float(kid(node, "width")[1])))
    return art


def mcboard_layers(tree):
    """The inner copper layers, from the layer table, whose lines are (<number> "In1.Cu" signal ["<name>"]).

    They are taken by position, as a working copy has them: all wire layers but the last, MCboard.Chevrons. Their
    names are not trusted, because KiCad's Append Board keeps the names a project already had, and its API cannot
    rename a layer. A name that is neither KiCad's own nor an MCboard one means the layer is not a working copy's.
    Returns ({"In1.Cu": "MCboard.Wires1", ..., "In2.Cu": "MCboard.Chevrons"}, {layer: its other name on this board},
    [(layer, name)] of the layers that are not a working copy's), the first empty if there are any of those."""
    inner = []
    for entry in kid(tree, "layers") or []:
        if isinstance(entry, list) and len(entry) > 2 and re.fullmatch(r"In\d+\.Cu", str(entry[1])):
            inner.append((int(entry[1][2:-3]), entry[1], entry[3] if len(entry) > 3 else entry[1]))
    inner.sort()
    roles = {layer: WIRE_LAYER_NAME.format(i + 1) for i, (_, layer, _) in enumerate(inner[:-1])}
    if inner:
        roles[inner[-1][1]] = CHEVRON_LAYER_NAME
    if len(inner) < 2:
        roles = {}
    names = {layer: name for _, layer, name in inner}
    foreign = [(layer, name) for layer, name in names.items() if name != layer and not name.startswith("MCboard.")]
    renamed = {layer: names[layer] for layer, role in roles.items() if names[layer] not in (layer, role)}
    if foreign:
        return {}, {}, foreign
    return roles, renamed, foreign


def is_locked(node):
    """Whether an item is locked: (locked yes) in new files, a bare locked in old ones."""
    found = kid(node, "locked")
    return "locked" in node or bool(found and found[1:2] == ["yes"])


def manual_tracks(tree, layers, locked=False):
    """The user's tracks, in the order of PlacedBoard: {wire layer name: (lines, arcs)}, chevron lines, and
    {layer name: count} of what means nothing there. A chevron is closed with a straight track. Other copper on those
    layers, such as a drawn shape, is counted but not used; so are arcs on the chevron layer."""
    wires, chevrons, other = {}, [], {}
    for kind in ("segment", "arc"):
        for node in kids(tree, kind):
            name = layers.get(layer_of(node))
            if not name or (is_locked(node) and not locked):
                continue
            ends = point(kid(node, "start")), point(kid(node, "end"))
            if name == CHEVRON_LAYER_NAME:
                if kind == "segment":
                    chevrons.append(ends)
                else:
                    other[name] = other.get(name, 0) + 1
                continue
            lines, arcs = wires.setdefault(name, ([], []))
            if kind == "segment":
                lines.append(ends)
            else:
                arcs.append((ends[0], point(kid(node, "mid")), ends[1]))
    for kind in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly"):
        for node in kids(tree, kind):
            name = layers.get(layer_of(node))
            if name:
                other[name] = other.get(name, 0) + 1
    return dict(sorted(wires.items())), chevrons, other


def edge_box(art, holes):
    """(x0, y0, x1, y1) of the board edge, or of the holes if a file has no edge."""
    points = []
    for p in art["Edge.Cuts"]:
        points += [p[1:3], p[3:5]] if p[0] == "line" else list(p[1:4]) if p[0] == "arc" else []
    if not points:
        points = [(h.x, h.y) for h in holes.values()]
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


# ---------- the file ----------

def read_grid(footprints):
    """The holes, as {(col, row): Hole}, plus the pad and drill sizes."""
    pads = []
    for fp in footprints:
        fx, fy, angle = footprint_place(fp)
        pad = kids(fp, "pad")[0]
        at = kid(pad, "at")
        x, y = on_sheet(fx, fy, angle, float(at[1]), float(at[2]))
        value = reference(fp, "Value")
        strip = pad_net(pad) if value in (HOLE_VALUE, "?") else value
        pads.append((x, y, strip, pad, pad_net(pad)))
    xs = sorted({round(p[0], 3) for p in pads})
    ys = sorted({round(p[1], 3) for p in pads})
    holes = {}
    for x, y, strip, _, net in pads:
        if not strip:
            raise NotAPlacedBoard(f"the hole at {x:.2f}, {y:.2f} has lost its net; "
                                  "regenerate the board and place the parts again")
        col, row = xs.index(round(x, 3)), ys.index(round(y, 3))
        holes[col, row] = Hole(col, row, x, y, strip, net)
    if len(holes) != len(xs) * len(ys):
        raise NotAPlacedBoard("the holes do not form a regular grid; has one been moved?")
    return holes, pads


def read_board(path, locked=False):
    """The PlacedBoard in a .kicad_pcb. Raises NotAPlacedBoard if it cannot be used. The locked tracks on the MCboard
    inner layers, which an apply added, are left out of the user's tracks, unless locked."""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = parse(fh.read())
    except (OSError, UnicodeDecodeError) as error:
        raise NotAPlacedBoard(str(error)) from error
    if not tree or tree[0] != "kicad_pcb":
        raise NotAPlacedBoard("it is not a .kicad_pcb file")
    footprints = {HOLE: [], CHEVRON: [], ISLAND: [], MOUNT: [], "": []}
    for fp in kids(tree, "footprint"):
        footprints[fp[1] if fp[1] in footprints else ""].append(fp)
    if not footprints[HOLE]:
        raise NotAPlacedBoard("it has no holes from generate_protoboard")
    holes, hole_pads = read_grid(footprints[HOLE])

    art = board_art(tree)
    pth = [(h.x, h.y) for h in holes.values()]
    size_of_pad = float(kid(hole_pads[0][3], "size")[1])
    drill = drill_of(hole_pads[0][3])
    for x, y in pth:
        art["F.Cu"].append(("flash", x, y, size_of_pad))
        art["B.Cu"].append(("flash", x, y, size_of_pad))

    by_place = {(round(h.x, 3), round(h.y, 3)): h for h in holes.values()}
    chevrons = [read_chevron(fp, by_place, art) for fp in footprints[CHEVRON]]
    ties = [(point(kid(n, "start")), point(kid(n, "end"))) for n in kids(tree, "segment") if layer_of(n) == "F.Cu"]
    islands = [read_island(fp, holes, ties, art) for fp in footprints[ISLAND]]

    npth = [footprint_place(fp)[:2] for fp in footprints[MOUNT]]
    mount_drill = drill_of(kids(footprints[MOUNT][0], "pad")[0]) if footprints[MOUNT] else 0.0
    parts = [read_part(fp) for fp in footprints[""]]
    x0, y0, x1, y1 = edge_box(art, holes)
    layers, renamed, foreign = mcboard_layers(tree)
    return PlacedBoard(str(path), holes, chevrons, [p for p in parts if p.pins], art, pth, npth, drill,
                       mount_drill, (x0, y0), (x1 - x0, y1 - y0), islands, *manual_tracks(tree, layers, locked), layers,
                       renamed, foreign)


def read_chevron(fp, by_place, art):
    """A Chevron, adding its copper to the drawing. The footprint sits on the first hole, and the point
    halfway between the two anchors of its pads is halfway between the holes."""
    fx, fy, angle = footprint_place(fp)
    pads = kids(fp, "pad")
    side = "F" if any(s.startswith("F.Cu") for s in kid(pads[0], "layers")[1:]) else "B"
    anchors = []
    for pad in pads:
        at = kid(pad, "at")
        ax, ay = on_sheet(fx, fy, angle, float(at[1]), float(at[2]))
        anchors.append((ax, ay))
        for shape in kids(kid(pad, "primitives"), "gr_poly"):
            art[f"{side}.Cu"].append(("region", [on_sheet(ax, ay, angle, u, v) for u, v in polygon(shape)]))
    mid_x = (anchors[0][0] + anchors[1][0]) / 2
    mid_y = (anchors[0][1] + anchors[1][1]) / 2
    other = (round(2 * mid_x - fx, 3), round(2 * mid_y - fy, 3))
    first, second = by_place.get((round(fx, 3), round(fy, 3))), by_place.get(other)
    if first is None or second is None:
        raise NotAPlacedBoard(f"chevron {reference(fp)} is not between two holes; has it been moved?")
    return Chevron(reference(fp), side, first, second)


def read_island(fp, holes, ties, art):
    """An Island, adding its square to the drawing. Its tie is a track, which board_art() has already. The tie
    says which of the four holes round it the island belongs to; the net alone does not, once a plan is applied."""
    x, y, _ = footprint_place(fp)
    pad = kids(fp, "pad")[0]
    half = float(kid(pad, "size")[1]) / 2
    art["F.Cu"].append(("region", [(x - half, y - half), (x + half, y - half),
                                   (x + half, y + half), (x - half, y + half)]))
    net = pad_net(pad)
    near = lambda h, p: math.hypot(h.x - p[0], h.y - p[1])
    for start, end in ties:
        for here, there in ((start, end), (end, start)):
            if math.hypot(here[0] - x, here[1] - y) < 0.001:
                return Island(x, y, half, min(holes.values(), key=lambda h: near(h, there)))
    tied = [h for h in holes.values() if h.net == net]
    if not tied:
        raise NotAPlacedBoard(f"island {reference(fp)} has no hole with its net; regenerate the board")
    return Island(x, y, half, min(tied, key=lambda h: near(h, (x, y))))


def read_part(fp):
    """A Part. Pads with no copper (paste only) and unplated holes are not pins."""
    place = footprint_place(fp)
    part = Part(reference(fp), box=courtyard(fp, place))
    for pad in kids(fp, "pad"):
        layers = kid(pad, "layers") or []
        if pad[2] == "np_thru_hole" or not any(".Cu" in str(s) for s in layers[1:]):
            continue
        at = kid(pad, "at")
        x, y = on_sheet(*place, float(at[1]), float(at[2]))
        part.pins.append(Pin(part.ref, pad[1], x, y, pad_net(pad), pad[2] != "thru_hole", drill_of(pad)))
    return part
