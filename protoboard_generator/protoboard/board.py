# SPDX-License-Identifier: GPL-3.0-or-later
"""All the design decisions: what goes where on the board.

Used in this order: Board(args), build(), text(). Sections of the class:

  setup and addressing     margins, grid position of a hole, nets
  items                    holes, chevrons, islands, tracks: everything on copper
  top silkscreen           what shows the connections, then text and labels
  mounting holes           the strips of unplated holes
  name                     finding a place for 'MCboard <cols>x<rows>'
  build                    calls all of the above in order
"""

import math
import re
import sys
import uuid

from .constants import (FORMAT_VERSION, GERBER_LAYERS, ISLAND_SIDE, ISLAND_TIE, ISLAND_TIE_INTO_PAD,
                        LABEL_CHAR_WIDTH, LABEL_GAP, LABEL_THICKNESS, LABEL_TO_EDGE,
                        MOUNT_TO_EDGE, MOUNT_TO_LABEL, NAME_GAP, NAME_GAP_TOP, NAME_TO_EDGE, SILK_BAR_SLACK,
                        MANUAL_CHEVRON_LAYER, MANUAL_WIRE_LAYER, PLAN_MARKS_LAYER, SILK_BAR_STEPS, SILK_TO_PAD, SOLDER_MASK_MIN_WEB, WORKING_CONNECTION_WIDTH,
                        WIRE_LAYER_NAME, WORKING_CONNECTIONS_LAYER, WORKING_LABELS_LAYER)
from .font import text_extent, text_strokes, text_width
from .mounting import SideSpec, corner_strip, holes_that_fit, mount_min_distance, placed, side_strip, widest_spacing
from .pattern import RowPattern


UUID_RE = re.compile(r'\(uuid "([0-9a-f-]{36})"\)')


def new_uuid():
    """Return a new identifier for the uuid of a board item."""
    return str(uuid.uuid4())


def fmt(v):
    """Format a number for KiCad (no trailing zeros)."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Board:
    """One protoboard: draws the geometry, and keeps it both as KiCad text and as Gerber primitives.

    Call build(), then text() for the .kicad_pcb. The writers in gerber.py and pdf.py read art, pth and npth.
    """
    def __init__(self, args):
        """Set up an empty board from the parsed options; build() draws it."""
        self.args = args
        self.pattern = RowPattern(args.rows, args.pattern)
        if not self.pattern.hole_rows:
            sys.exit(f"--pattern {args.pattern} leaves no row of holes in {args.rows} rows.")
        self.items = []
        self.nets = {"": 0}
        self.margin, self.slack = self.margins()
        # The same geometry as self.items, for the Gerbers, the drill files and the PDF. Per layer,
        # in sheet coordinates: ("flash", x, y, diameter), ("line", x1, y1, x2, y2, width),
        # ("arc", start, mid, end, width) or ("region", points).
        self.art = {layer[0]: [] for layer in GERBER_LAYERS}
        self.pth = []   # plated holes
        self.npth = []  # mounting holes
        self.name_placement = None
        self.text_placement = None
        self.n_top_chevrons = 0
        self.n_islands = 0
        self.mount_info = None  # (number of holes, strips) once drawn, if the board has them

    # ---------- setup and addressing ----------

    def side_spec(self, side):
        """Return the mounting hole spec given for a side, or an empty one if it stays automatic."""
        return self.args.mount_sides.get(side, SideSpec())

    def line_needs(self):
        """(x, y) The least length of the lines of holes, set by the sides given a number and a spacing."""
        return (max(self.side_spec(s).span() for s in ("top", "bottom")),
                max(self.side_spec(s).span() for s in ("left", "right")))

    def stretched_array(self):
        """(x, y) The size of the hole grid, or what it would have to be to fill the board once the holes
        have made it grow."""
        a = self.args
        need_x, need_y = self.line_needs()
        beyond = 2 * (a.margin - MOUNT_TO_EDGE)
        return max((a.cols - 1) * a.pitch, need_x - beyond), max((a.rows - 1) * a.pitch, need_y - beyond)

    def corner_strips_horizontal(self):
        """Whether the strips that reach the corners run along the top and bottom sides."""
        width, height = self.stretched_array()
        return width >= height

    def corner_sides(self):
        """Return the pair of sides whose automatic strips reach the corners."""
        return ("top", "bottom") if self.corner_strips_horizontal() else ("left", "right")

    def margins(self):
        """({side: margin}, (x, y) slack). A side without mounting holes only needs room for its labels, and
        when the holes need a larger board, the room left over goes where --array-x and --array-y say."""
        a = self.args
        sides = ("top", "bottom", "left", "right")
        if a.no_mount_holes:
            return dict.fromkeys(sides, a.margin), (0, 0)
        corner_sides = self.corner_sides()
        stretched = self.stretched_array()
        short = stretched[1] if self.corner_strips_horizontal() else stretched[0]
        auto_side_holes = side_strip(2 * a.margin + short - 2 * MOUNT_TO_EDGE, a.mount_spacing)[1] > 0

        def has_holes(side):
            """Say whether a side will carry mounting holes."""
            spec = self.side_spec(side)
            if spec.holes is not None:
                return spec.holes > 0
            return spec.spacing is not None or side in corner_sides or auto_side_holes

        margin = {side: a.margin if has_holes(side) else a.margin_plain for side in sides}
        need_x, need_y = self.line_needs()
        slack_x = max(0, need_x + 2 * MOUNT_TO_EDGE - margin["left"] - margin["right"] - (a.cols - 1) * a.pitch)
        slack_y = max(0, need_y + 2 * MOUNT_TO_EDGE - margin["top"] - margin["bottom"] - (a.rows - 1) * a.pitch)
        before_x = {"left": 0, "right": 1}.get(a.array_x, 0.5)
        before_y = {"top": 0, "bottom": 1}.get(a.array_y, 0.5)
        margin["left"] += slack_x * before_x
        margin["right"] += slack_x * (1 - before_x)
        margin["top"] += slack_y * before_y
        margin["bottom"] += slack_y * (1 - before_y)
        return margin, (slack_x, slack_y)

    def net(self, name):
        """Return (number, name) of a net, adding it to the table the first time it is seen."""
        if name not in self.nets:
            self.nets[name] = len(self.nets)
        return self.nets[name], name

    def x(self, col):
        """Return the sheet x of a column."""
        return self.args.origin_x + self.margin["left"] + col * self.args.pitch

    def y(self, row):
        """Return the sheet y of a row."""
        return self.args.origin_y + self.margin["top"] + row * self.args.pitch

    def net_of(self, col, row):
        """Return the net of the bus or strip that a hole belongs to. The row in the name counts holes only."""
        index = self.pattern.hole_index
        if self.pattern.is_bus(row):
            return self.net(f"BUS_R{index[row] + 1}")
        return self.net(f"C{col + 1}_R{index[self.pattern.start[row]] + 1}")

    def name(self):
        """Return the name printed on the board."""
        return f"MCboard {self.args.cols}x{self.args.rows}"

    def from_corner(self, x, y):
        """Sheet coordinates to fab coordinates: lower left board corner at 0, 0 and y up."""
        return x - self.args.origin_x, self.board_size()[1] - (y - self.args.origin_y)

    # ---------- items ----------

    # Each method that draws does the same two things: it records the shape in self.art, for the
    # Gerbers and the PDF, and appends the same thing to self.items as KiCad text.

    def hole(self, col, row, ref):
        """Draw one plated hole, with its pad and mask opening on both sides."""
        a = self.args
        net_id, net_name = self.net_of(col, row)
        centre = (self.x(col), self.y(row))
        self.pth.append(centre)
        self.art["F.Cu"].append(("flash", *centre, a.pad))
        self.art["B.Cu"].append(("flash", *centre, a.pad))
        for layer in ("F.Mask", "B.Mask"):
            self.art[layer].append(("flash", *centre, a.pad + 2 * a.mask_margin))
        self.items.append(
            f'  (footprint "Hole" (layer "F.Cu") (uuid "{new_uuid()}")\n'
            f"    (at {fmt(self.x(col))} {fmt(self.y(row))})\n"
            f"    (attr board_only exclude_from_pos_files exclude_from_bom)\n"
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.Fab") (hide yes)\n'
            f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
            f'    (property "Value" "hole" (at 0 0 0) (layer "F.Fab") (hide yes)\n'
            f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
            f'    (pad "1" thru_hole circle (at 0 0) (size {fmt(a.pad)} {fmt(a.pad)}) (drill {fmt(a.drill)})'
            f' (layers "*.Cu" "*.Mask") (net {net_id} "{net_name}") (uuid "{new_uuid()}"))\n'
            f"  )"
        )

    # A chevron is two copper polygons, one on each side of a V-shaped gap, joining two holes one
    # pitch apart. Each polygon runs into its pad, which is what connects it. With d = chev_depth:
    #
    #        +-------------+   y_top: just outside the drill, but inside the pad
    #        |    upper    |
    #        '-.         .-'   mid - d/2 - gap_v/2: the sides of the upper half end here
    #        .  '-.   .-'  .   its tip continues to x = 0, at mid + d/2 - gap_v/2
    #        |'-.  'v'  .-'|   mid - d/2 + gap_v/2: the sides of the lower half start here
    #        |   '-. .-'   |   its notch bottoms at x = 0, at mid + d/2 + gap_v/2
    #        |    lower    |
    #        +-------------+   pitch - y_top
    #
    # The V gives a longer gap than a straight cut, and solder bridges it more easily.

    def chevron_geometry(self):
        """Polygons (relative to the upper hole) of the two chevron halves."""
        a = self.args
        p, w, d, g = a.pitch, a.chev_width, a.chev_depth, a.gap
        mid = p / 2
        gap_v = g * math.sqrt(1 + (2 * d / w) ** 2)  # gap measured vertically
        y_top = a.drill / 2 + 0.05                     # stay clear of the drill
        upper = [(-w / 2, y_top), (w / 2, y_top),
                 (w / 2, mid - d / 2 - gap_v / 2), (0, mid + d / 2 - gap_v / 2),
                 (-w / 2, mid - d / 2 - gap_v / 2)]
        lower = [(-w / 2, mid - d / 2 + gap_v / 2), (0, mid + d / 2 + gap_v / 2),
                 (w / 2, mid - d / 2 + gap_v / 2), (w / 2, p - y_top),
                 (-w / 2, p - y_top)]
        return upper, lower, y_top, mid, gap_v

    # Called first in build(), so that impossible option values stop with a clear message.

    def check_geometry(self):
        """Exit with every error found if the dimensions cannot make a sound board."""
        a = self.args
        r = a.pad / 2
        w, d = a.chev_width, a.chev_depth
        _, _, y_top, mid, gap_v = self.chevron_geometry()
        errors = []
        if a.drill >= a.pad:
            errors.append("pad must be larger than the drill")
        if a.pad >= a.pitch:
            errors.append("pad does not fit in the pitch")
        if w / 2 >= r:
            errors.append("chevron is wider than the pad")
        elif y_top >= math.sqrt(r * r - (w / 2) ** 2):
            errors.append("chevron corners do not sit on the pad; make the chevron narrower")
        # The gap must not touch any pad: sample the gap edges.
        for i in range(21):
            x = -w / 2 + w * i / 20
            frac = 1 - abs(x) / (w / 2)
            centre = mid - d / 2 + d * frac
            gap_top, gap_bottom = centre - gap_v / 2, centre + gap_v / 2
            pad_upper = math.sqrt(max(r * r - x * x, 0))
            pad_lower = a.pitch - pad_upper
            if gap_top < pad_upper + 0.05 or gap_bottom > pad_lower - 0.05:
                errors.append("chevron gap runs too close to a pad; reduce depth or gap")
                break
        if a.gap < 0.15:
            errors.append("gap below 0.15 mm: probably outside manufacturing tolerances")
        if a.bus > a.pad:
            errors.append("bus is wider than the pad and would encroach on the chevrons")
        if a.top == "smt":
            errors += self.island_errors()
        if errors:
            sys.exit("Invalid geometry:\n  - " + "\n  - ".join(errors))

    def chevron(self, col, row, to_col, to_row, side, ref):
        """Chevron solder jumper between two neighbouring holes, with the tip pointing from the first
        to the second. side is "B" or "F", the copper and mask layers it goes on."""
        a = self.args
        upper, lower, y_top, mid, gap_v = self.chevron_geometry()
        net1, name1 = self.net_of(col, row)
        net2, name2 = self.net_of(to_col, to_row)
        copper, mask_layer = f"{side}.Cu", f"{side}.Mask"
        # chevron_geometry() draws it pointing down; this turns it to the direction asked for.
        vx, vy = to_col - col, to_row - row

        def turned(points):
            """Rotate points given along the x axis to the direction of this chevron."""
            return [(u * vy + v * vx, -u * vx + v * vy) for u, v in points]

        # Custom pad anchors: inside each polygon, away from the drill.
        anchor1 = turned([(0, (y_top + (mid - a.chev_depth / 2 - gap_v / 2)) / 2)])[0]
        anchor2 = turned([(0, (a.pitch - y_top + (mid + a.chev_depth / 2 + gap_v / 2)) / 2)])[0]
        upper, lower = turned(upper), turned(lower)
        centre = turned([(0, mid)])[0]

        # KiCad pads need an anchor point, and their custom shape is relative to it. The polygons
        # were computed relative to the first hole, so here they are shifted to their anchor.
        def poly(points, anchor):
            """Return the KiCad text of a filled polygon, relative to the footprint's anchor."""
            pts = " ".join(f"(xy {fmt(px - anchor[0])} {fmt(py - anchor[1])})" for px, py in points)
            return f"(gr_poly (pts {pts}) (width 0) (fill yes))"

        # The mask opening is one rectangle over the whole chevron, gap included: the gap has to be
        # exposed or it could not be bridged with solder.
        half_w = a.chev_width / 2 + a.mask_margin
        mask = turned([(-half_w, y_top), (half_w, y_top),
                       (half_w, a.pitch - y_top), (-half_w, a.pitch - y_top)])
        mask_pts = " ".join(f"(xy {fmt(px)} {fmt(py)})" for px, py in mask)
        anchor_size = 0.2

        def on_sheet(points):
            """Move points relative to the first hole to sheet coordinates."""
            return [(self.x(col) + px, self.y(row) + py) for px, py in points]

        self.art[copper].append(("region", on_sheet(upper)))
        self.art[copper].append(("region", on_sheet(lower)))
        # This opening contains those of the two halves, each expanded by the mask margin.
        self.art[mask_layer].append(("region", on_sheet(mask)))
        self.items.append(
            f'  (footprint "ChevronJumper" (layer "F.Cu") (uuid "{new_uuid()}")\n'
            f"    (at {fmt(self.x(col))} {fmt(self.y(row))})\n"
            f"    (attr board_only exclude_from_pos_files exclude_from_bom allow_soldermask_bridges)\n"
            f'    (property "Reference" "{ref}" (at {fmt(centre[0])} {fmt(centre[1])} 0) (layer "F.Fab") (hide yes)\n'
            f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
            f'    (property "Value" "chevron" (at {fmt(centre[0])} {fmt(centre[1])} 0) (layer "F.Fab") (hide yes)\n'
            f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
            f"    (fp_poly (pts {mask_pts}) (stroke (width 0) (type solid)) (fill yes)"
            f' (layer "{mask_layer}") (uuid "{new_uuid()}"))\n'
            f'    (pad "1" smd custom (at {fmt(anchor1[0])} {fmt(anchor1[1])}) (size {anchor_size} {anchor_size})'
            f' (layers "{copper}" "{mask_layer}") (net {net1} "{name1}")\n'
            f"      (options (clearance outline) (anchor circle))\n"
            f"      (primitives {poly(upper, anchor1)}) (uuid \"{new_uuid()}\"))\n"
            f'    (pad "2" smd custom (at {fmt(anchor2[0])} {fmt(anchor2[1])}) (size {anchor_size} {anchor_size})'
            f' (layers "{copper}" "{mask_layer}") (net {net2} "{name2}")\n'
            f"      (options (clearance outline) (anchor circle))\n"
            f"      (primitives {poly(lower, anchor2)}) (uuid \"{new_uuid()}\"))\n"
            f"  )"
        )

    def top_chevrons(self):
        """The chevrons of --top smt, on F.Cu: between columns on every row except the buses and spaces, and
        over the bottom ones between strips. Inside a group or along a bus the holes are already
        joined, so a chevron there would short a part. Returns how many there are.

        Tips alternate: to the right on even rows and to the left on odd ones, up on the columns
        numbered odd on the board and down on the even ones."""
        a = self.args
        count = 0
        for r in range(a.rows):
            if not self.pattern.is_bus(r) and not self.pattern.is_space(r):
                for c in range(a.cols - 1):
                    count += 1
                    start, end = (c, c + 1) if r % 2 == 0 else (c + 1, c)
                    self.chevron(start, r, end, r, "F", f"PBJT{count}")
        for c in range(a.cols):
            for r in range(a.rows - 1):
                if self.pattern.has_chevron(r):
                    count += 1
                    start, end = (r, r + 1) if c % 2 else (r + 1, r)
                    self.chevron(c, start, c, end, "F", f"PBJT{count}")
        return count

    def island_errors(self):
        """What the options break around an island: the mask between its opening and the openings of its
        neighbours, and the silkscreen bar beside it."""
        a = self.args
        half = ISLAND_SIDE / 2 + a.mask_margin
        corner = a.pitch / 2 - half
        errors = []
        # Strictly more than the minimum here: KiCad's plot closes a web of exactly the minimum.
        if a.pitch / 2 - half - a.chev_width / 2 - a.mask_margin < SOLDER_MASK_MIN_WEB + 1e-9:
            errors.append("no room for the islands of --top smt beside the chevrons; "
                          "make the chevrons narrower or the pitch larger")
        if math.hypot(corner, corner) - a.pad / 2 - a.mask_margin < SOLDER_MASK_MIN_WEB + 1e-9:
            errors.append("no room for the islands of --top smt between the pads; make the pads smaller")
        if a.silk == "bars" and a.pitch / 2 - half - a.track / 2 < SILK_TO_PAD - 1e-9:
            errors.append("the silkscreen bars come too close to the islands of --top smt; make the track narrower")
        return errors

    def island(self, col, row, ref):
        """A square top pad in the cell right and below of a hole, for a foot of an SMT part, permanently tied
        to one of its corner holes by a track under the mask. The track is what joins it, as for any other pad.

        The tie goes left on even columns and right on odd ones, down on odd rows and up on even ones."""
        a = self.args
        tied = (col + col % 2, row + row % 2)
        net_id, net_name = self.net_of(*tied)
        x, y = self.x(col) + a.pitch / 2, self.y(row) + a.pitch / 2
        hole_x, hole_y = self.x(tied[0]), self.y(tied[1])
        to_hole = math.hypot(hole_x - x, hole_y - y)
        reach = (to_hole - a.pad / 2 + ISLAND_TIE_INTO_PAD) / to_hole
        end = (x + (hole_x - x) * reach, y + (hole_y - y) * reach)

        def square(half):
            """Return the corners of a square around the island's centre."""
            return [(x - half, y - half), (x + half, y - half), (x + half, y + half), (x - half, y + half)]

        self.art["F.Cu"].append(("region", square(ISLAND_SIDE / 2)))
        self.art["F.Cu"].append(("line", x, y, *end, ISLAND_TIE))
        self.art["F.Mask"].append(("region", square(ISLAND_SIDE / 2 + a.mask_margin)))
        self.items.append(
            f'  (footprint "Island" (layer "F.Cu") (uuid "{new_uuid()}")\n'
            f"    (at {fmt(x)} {fmt(y)})\n"
            f"    (attr board_only exclude_from_pos_files exclude_from_bom)\n"
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.Fab") (hide yes)\n'
            f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
            f'    (property "Value" "island" (at 0 0 0) (layer "F.Fab") (hide yes)\n'
            f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
            f'    (pad "1" smd rect (at 0 0) (size {fmt(ISLAND_SIDE)} {fmt(ISLAND_SIDE)})'
            f' (layers "F.Cu" "F.Mask") (net {net_id} "{net_name}") (uuid "{new_uuid()}"))\n'
            f"  )"
        )
        self.items.append(
            f"  (segment (start {fmt(x)} {fmt(y)}) (end {fmt(end[0])} {fmt(end[1])}) (width {fmt(ISLAND_TIE)})"
            f' (layer "F.Cu") (net {net_id}) (uuid "{new_uuid()}"))'
        )

    def islands(self):
        """The islands of --top smt, inside the groups only, so that the silkscreen lines stay whole. A SOT-23
        goes on an even-numbered column, with two feet on the islands at its sides and the third on a
        chevron, so it has three separate strips. The rows are those of RowPattern.island_rows(), one per group.
        A cell that could not be half of such a pair has none. Returns how many there are."""
        a = self.args
        count = 0
        for r in self.pattern.island_rows():
            for c in range(a.cols - 1):
                if c % 2 or c + 1 < a.cols - 1:
                    count += 1
                    self.island(c, r, f"PBIS{count}")
        return count

    def segment(self, x1, y1, x2, y2, width, col, row):
        """Draw a copper track on B.Cu, in the net of the given hole."""
        net_id, _ = self.net_of(col, row)
        self.art["B.Cu"].append(("line", x1, y1, x2, y2, width))
        self.items.append(
            f"  (segment (start {fmt(x1)} {fmt(y1)}) (end {fmt(x2)} {fmt(y2)}) (width {fmt(width)})"
            f' (layer "B.Cu") (net {net_id}) (uuid "{new_uuid()}"))'
        )

    # ---------- top silkscreen ----------

    def silk_line(self, x1, y1, x2, y2):
        """Draw a line on the top silkscreen."""
        self.art["F.SilkS"].append(("line", x1, y1, x2, y2, self.args.silk_width))
        self.items.append(
            f"  (gr_line (start {fmt(x1)} {fmt(y1)}) (end {fmt(x2)} {fmt(y2)})"
            f" (stroke (width {fmt(self.args.silk_width)}) (type solid))"
            f' (layer "F.SilkS") (uuid "{new_uuid()}"))'
        )

    def silk_polygon(self, points):
        """Draw a filled polygon on the top silkscreen."""
        self.art["F.SilkS"].append(("region", points))
        pts = " ".join(f"(xy {fmt(x)} {fmt(y)})" for x, y in points)
        self.items.append(
            f"  (gr_poly (pts {pts}) (stroke (width 0) (type solid)) (fill yes)"
            f' (layer "F.SilkS") (uuid "{new_uuid()}"))'
        )

    def silk_arc(self, start, mid, end):
        """Draw an arc on the top silkscreen."""
        self.art["F.SilkS"].append(("arc", start, mid, end, self.args.silk_width))
        self.items.append(
            f"  (gr_arc (start {fmt(start[0])} {fmt(start[1])}) (mid {fmt(mid[0])} {fmt(mid[1])})"
            f" (end {fmt(end[0])} {fmt(end[1])})"
            f" (stroke (width {fmt(self.args.silk_width)}) (type solid))"
            f' (layer "F.SilkS") (uuid "{new_uuid()}"))'
        )

    # All the connection silkscreen and the labels are measured out from this radius: the pad, its
    # mask opening, the clearance that the fabs ask for, and half of the line itself.

    def silk_clear_radius(self):
        """Distance from a hole centre to the centreline of the nearest allowed silk."""
        a = self.args
        return a.pad / 2 + a.mask_margin + SILK_TO_PAD + a.silk_width / 2

    def silk_connection(self, x1, y1, x2, y2):
        """'lines' style: the visible part of a track between two adjacent holes."""
        clear = self.silk_clear_radius()
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 2 * clear:
            return
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        self.silk_line(x1 + ux * clear, y1 + uy * clear, x2 - ux * clear, y2 - uy * clear)

    # Two straight lines and two semicircles around a group or bus. Used by the 'outline' style.

    def silk_capsule(self, x1, y1, x2, y2):
        """'outline' style: a capsule around the holes from (x1, y1) to (x2, y2)."""
        r = self.silk_clear_radius()
        if x1 == x2:  # vertical
            self.silk_line(x1 - r, y1, x1 - r, y2)
            self.silk_line(x1 + r, y1, x1 + r, y2)
            self.silk_arc((x1 - r, y1), (x1, y1 - r), (x1 + r, y1))
            self.silk_arc((x1 + r, y2), (x1, y2 + r), (x1 - r, y2))
        else:  # horizontal
            self.silk_line(x1, y1 - r, x2, y1 - r)
            self.silk_line(x1, y1 + r, x2, y1 + r)
            self.silk_arc((x1, y1 + r), (x1 - r, y1), (x1, y1 - r))
            self.silk_arc((x2, y1 - r), (x2 + r, y1), (x2, y1 + r))

    # The 'bars' style. A bar lies over the track between two joined holes, with ends that curve around
    # both pads. Each curve is straight pieces tangent to the clearance circle, so that no piece cuts
    # into it, the rest of the circle being what the fabs ask for.

    def silk_bar(self, x1, y1, x2, y2, width):
        """A bar as wide as the track, between the holes at (x1, y1) and (x2, y2)."""
        a = self.args
        radius = a.pad / 2 + a.mask_margin + SILK_TO_PAD + SILK_BAR_SLACK
        half = width / 2
        length = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        vertex = radius / math.cos(math.asin(half / radius) / SILK_BAR_STEPS)
        limit = math.asin(half / vertex)
        angles = [limit * (2 * i / SILK_BAR_STEPS - 1) for i in range(SILK_BAR_STEPS + 1)]
        local = [(vertex * math.cos(t), vertex * math.sin(t)) for t in angles]
        local += [(length - vertex * math.cos(t), vertex * math.sin(t)) for t in reversed(angles)]
        self.silk_polygon([(x1 + s * ux - t * uy, y1 + s * uy + t * ux) for s, t in local])

    def silk_boundary(self, y, crossed):
        """The 'bars' style's line between two rows of different strips. It only breaks where the
        vertical chevrons of --top smt cross it, if `crossed`, and reaches as far as the labels allow."""
        a = self.args
        reach = self.silk_clear_radius()
        first, last = self.x(0), self.x(a.cols - 1)
        if a.top != "smt" or not crossed:
            self.silk_line(first - reach, y, last + reach, y)
            return
        stop = a.chev_width / 2 + a.mask_margin + SILK_TO_PAD + a.silk_width / 2
        if reach > stop:
            self.silk_line(first - reach, y, first - stop, y)
            self.silk_line(last + stop, y, last + reach, y)
        for c in range(a.cols - 1):
            if self.x(c) + stop < self.x(c + 1) - stop:
                self.silk_line(self.x(c) + stop, y, self.x(c + 1) - stop, y)

    def silk_bars(self):
        """Draw the bars over the tracks and the lines between strips, groups or bus rows, for --silk bars."""
        a = self.args
        radius = a.pad / 2 + a.mask_margin + SILK_TO_PAD
        errors = []
        if 2 * radius >= a.pitch:
            errors.append("no room for bars between holes at this pitch and pad")
        if max(a.track, a.bus) / 2 >= radius:
            errors.append("track or bus too wide for the bars between holes")
        if a.pitch / 2 - a.silk_width / 2 < radius:
            errors.append("no room for the lines between rows at this pitch and pad")
        if errors:
            sys.exit("Invalid 'bars' silkscreen:\n  - " + "\n  - ".join(errors))
        for r in range(a.rows - 1):
            if self.pattern.is_space(r) or self.pattern.is_space(r + 1):
                continue  # a space shows the edge of a strip by itself
            if not self.pattern.joined(r):
                self.silk_boundary((self.y(r) + self.y(r + 1)) / 2, self.pattern.has_chevron(r))
            else:
                for c in range(a.cols):
                    self.silk_bar(self.x(c), self.y(r), self.x(c), self.y(r + 1), a.track)
        for r in range(a.rows):
            if self.pattern.is_bus(r):
                for c in range(a.cols - 1):
                    self.silk_bar(self.x(c), self.y(r), self.x(c + 1), self.y(r), a.bus)

    def connection_runs(self):
        """Yield the end holes (x1, y1, x2, y2) of every strip and bus. A strip of one hole has both ends equal."""
        cols, rows = self.args.cols, self.args.rows
        for c in range(cols):
            r = 0
            while r < rows:
                if self.pattern.is_bus(r) or self.pattern.is_space(r):
                    r += 1
                    continue
                start = r
                while r + 1 < rows and self.pattern.joined(r):
                    r += 1
                yield self.x(c), self.y(start), self.x(c), self.y(r)
                r += 1
        for r in range(rows):
            if self.pattern.is_bus(r):
                yield self.x(0), self.y(r), self.x(cols - 1), self.y(r)

    def silkscreen(self):
        """Draw the connection marks chosen with --silk on the top side."""
        a = self.args
        if a.silk == "none":
            return
        cols, rows = a.cols, a.rows
        if a.silk == "bars":
            self.silk_bars()
        elif a.silk == "lines":
            for c in range(cols):
                for r in range(rows - 1):
                    if self.pattern.joined(r):
                        self.silk_connection(self.x(c), self.y(r), self.x(c), self.y(r + 1))
            for r in range(rows):
                if self.pattern.is_bus(r):
                    for c in range(cols - 1):
                        self.silk_connection(self.x(c), self.y(r), self.x(c + 1), self.y(r))
        else:  # outline
            # Pitch must leave room for two capsule lines between adjacent holes.
            if 2 * self.silk_clear_radius() + a.silk_width / 2 >= a.pitch:
                sys.exit("Not enough room between holes for the 'outline' silkscreen style.")
            for run in self.connection_runs():
                self.silk_capsule(*run)

    # Text is drawn by KiCad from the gr_text item, and for the Gerbers from the font file, which holds
    # KiCad's own glyphs. Mirroring and rotating below must therefore do what KiCad does.

    def silk_text(self, text, x, y, layer, vertical=False):
        """A centred text. Vertical means rotated by 90 degrees, reading upwards."""
        a = self.args
        bottom = layer.startswith("B.")
        mirror = " (justify mirror)" if bottom else ""
        for polyline in text_strokes(text, a.label_size):
            if bottom:
                polyline = [(-u, v) for u, v in polyline]
            if vertical:
                polyline = [(v, -u) for u, v in polyline]
            for (u1, v1), (u2, v2) in zip(polyline, polyline[1:]):
                self.art[layer].append(("line", x + u1, y + v1, x + u2, y + v2, LABEL_THICKNESS))
        angle = " 90" if vertical else ""
        quoted = text.replace("\\", "\\\\").replace('"', '\\"')
        self.items.append(
            f'  (gr_text "{quoted}" (at {fmt(x)} {fmt(y)}{angle}) (layer "{layer}") (uuid "{new_uuid()}")\n'
            f"    (effects (font (size {fmt(a.label_size)} {fmt(a.label_size)})"
            f" (thickness {fmt(LABEL_THICKNESS)})){mirror}))"
        )

    # Labels sit in the margin, outside the connection silkscreen, because there is no room between holes.
    # Widths come from a conservative guess, not from the font, so the margin is safe for any letters.

    def label_metrics(self):
        """Distance from a hole centre to the labels, and half their height and width."""
        a = self.args
        clear = self.silk_clear_radius() + a.silk_width / 2 + LABEL_GAP
        half_height = (a.label_size + LABEL_THICKNESS) / 2
        half_width = len(self.pattern.label(self.pattern.hole_rows[-1])) * a.label_size * LABEL_CHAR_WIDTH / 2
        return clear, half_height, half_width

    def labels(self):
        """Column numbers and row letters on both silkscreens, at the same positions."""
        a = self.args
        if a.no_labels:
            return
        cols, rows = a.cols, a.rows
        char_width = a.label_size * LABEL_CHAR_WIDTH
        clear, half_height, half_width = self.label_metrics()

        errors = []
        if len(str(cols)) * char_width >= a.pitch:
            errors.append(f"column numbers up to {cols} would touch at this label size; "
                          "reduce --label-size or use --no-labels")
        if a.label_size + LABEL_THICKNESS >= a.pitch:
            errors.append("row labels would touch at this label size; reduce --label-size")
        for margin, half_size, sides in (
                (min(self.margin["top"], self.margin["bottom"]), half_height, "top and bottom"),
                (min(self.margin["left"], self.margin["right"]), half_width, "left and right")):
            needed = clear + 2 * half_size + LABEL_TO_EDGE
            if margin < needed:
                errors.append(f"{sides} margins must be at least {needed:.2f} mm for the labels; "
                              "increase --margin or --margin-plain, or use --no-labels")
        if errors:
            sys.exit("Invalid labels:\n  - " + "\n  - ".join(errors))

        col_offset = clear + half_height
        row_offset = clear + half_width
        for layer in ("F.SilkS", "B.SilkS"):
            for c in range(cols):
                for y in (self.y(0) - col_offset, self.y(rows - 1) + col_offset):
                    self.silk_text(str(c + 1), self.x(c), y, layer)
            for r in self.pattern.hole_rows:
                for x in (self.x(0) - row_offset, self.x(cols - 1) + row_offset):
                    self.silk_text(self.pattern.label(r), x, self.y(r), layer)

    # ---------- mounting holes ----------

    def board_size(self):
        """Return the width and height of the board."""
        a = self.args
        return (self.margin["left"] + self.margin["right"] + (a.cols - 1) * a.pitch,
                self.margin["top"] + self.margin["bottom"] + (a.rows - 1) * a.pitch)

    def side_strip_holes(self, side, length):
        """(spacing, holes) of the strip on one side, whose line of holes has this length."""
        a = self.args
        spec = self.side_spec(side)
        if not spec.chooses():
            if side in self.corner_sides():
                spacing, spans = corner_strip(length, a.mount_spacing)
                return spacing, spans + 1
            return side_strip(length, a.mount_spacing)
        if spec.spacing is None:
            if spec.holes < 2:
                return 0, spec.holes
            spacing = widest_spacing(length, spec.holes, mount_min_distance(a.mount_drill))
            if spacing is None:
                sys.exit(f"--mount-{side}: {spec.holes} holes do not fit in a line of {length:.2f} mm. "
                         "Give a spacing as well, and the board will grow to fit them.")
            return spacing, spec.holes
        if spec.holes is None:
            return spec.spacing, holes_that_fit(length, spec.spacing)
        return spec.spacing, spec.holes

    def mount_layout(self):
        """Hole positions relative to the board corner, and {side: (spacing, holes, holes left out)}."""
        a = self.args
        width, height = self.board_size()
        line_x, line_y = width - 2 * MOUNT_TO_EDGE, height - 2 * MOUNT_TO_EDGE
        corner_sides = self.corner_sides()
        # Where strips meet, a side with its number and spacing given beats one with only one of them, that
        # beats an automatic one, and otherwise the corner strips win. Only two exact sides are an error.
        # Two holes in the same place are one, and this is how strips share a corner.
        order = sorted(("top", "bottom", "left", "right"),
                       key=lambda side: (not self.side_spec(side).exact(), not self.side_spec(side).chooses(),
                                         side not in corner_sides))
        least = mount_min_distance(a.mount_drill)
        points, exact, strips = [], [], {}
        for side in order:
            spacing, holes = self.side_strip_holes(side, line_x if side in ("top", "bottom") else line_y)
            align = self.side_spec(side).align or "centre"
            left_out = 0
            for along in placed(line_x if side in ("top", "bottom") else line_y, spacing, holes, align):
                point = {"top": (along, 0), "bottom": (along, line_y),
                         "left": (0, along), "right": (line_x, along)}[side]
                near = [q for q in points if math.dist(point, q) < least]
                if any(math.dist(point, q) < 1e-6 for q in near):
                    continue
                if near and self.side_spec(side).exact() and any(q in exact for q in near):
                    sys.exit(f"Mounting holes on the {side} side come within {least:g} mm of holes of another "
                             "side. Change their spacing, number or alignment.")
                if near:
                    left_out += 1
                    continue
                points.append(point)
                if self.side_spec(side).exact():
                    exact.append(point)
            strips[side] = spacing, holes, left_out
        return [(MOUNT_TO_EDGE + x, MOUNT_TO_EDGE + y) for x, y in points], strips

    def check_mount(self):
        """Exit with every error found if a mounting hole comes too close to the labels or the grid."""
        a = self.args
        depth_y = depth_x = a.pad / 2 + a.mask_margin
        if not a.no_labels:
            clear, half_height, half_width = self.label_metrics()
            depth_y, depth_x = clear + 2 * half_height, clear + 2 * half_width
        _, strips = self.mount_layout()
        errors = []
        for side, (_, holes, _) in strips.items():
            depth = depth_y if side in ("top", "bottom") else depth_x
            needed = MOUNT_TO_EDGE + a.mount_drill / 2 + MOUNT_TO_LABEL + depth
            if holes and self.margin[side] < needed:
                errors.append(f"the {side} margin must be at least {needed:.2f} mm for the mounting "
                              "holes; increase --margin or use --no-mount-holes")
        if errors:
            sys.exit("Invalid mounting holes:\n  - " + "\n  - ".join(errors))

    def mounting_holes(self):
        """Unplated holes, so that a screw never connects to anything."""
        a = self.args
        points, strips = self.mount_layout()
        for side, (_, holes, _) in strips.items():
            spec = self.side_spec(side)
            if spec.spacing and spec.holes is None and holes == 1:
                print(f"Warning: --mount-{side} spacing {spec.spacing:g} does not fit twice in the line "
                      "of holes; that strip has a single hole.", file=sys.stderr)
        for i, (x, y) in enumerate(points, 1):
            centre = (a.origin_x + x, a.origin_y + y)
            self.npth.append(centre)
            for layer in ("F.Mask", "B.Mask"):
                self.art[layer].append(("flash", *centre, a.mount_drill + 2 * a.mask_margin))
            self.items.append(
                f'  (footprint "MountingHole" (layer "F.Cu") (uuid "{new_uuid()}")\n'
                f"    (at {fmt(a.origin_x + x)} {fmt(a.origin_y + y)})\n"
                f"    (attr board_only exclude_from_pos_files exclude_from_bom)\n"
                f'    (property "Reference" "PBMH{i}" (at 0 0 0) (layer "F.Fab") (hide yes)\n'
                f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
                f'    (property "Value" "mounting hole" (at 0 0 0) (layer "F.Fab") (hide yes)\n'
                f"      (effects (font (size 0.4 0.4) (thickness 0.06))) (uuid \"{new_uuid()}\"))\n"
                f'    (pad "" np_thru_hole circle (at 0 0) (size {fmt(a.mount_drill)} {fmt(a.mount_drill)})'
                f' (drill {fmt(a.mount_drill)}) (layers "*.Cu" "*.Mask") (uuid "{new_uuid()}"))\n'
                f"  )"
            )
        return len(points), strips

    def mount_report(self):
        """One line per side that has holes, for the console and the PDF. Centre-to-centre distances."""
        if not self.mount_info:
            return []
        _, strips = self.mount_info
        width, height = self.board_size()
        pairs = [("top", "bottom", "Top and bottom", height - 2 * MOUNT_TO_EDGE),
                 ("left", "right", "Left and right", width - 2 * MOUNT_TO_EDGE)]
        pairs.sort(key=lambda pair: pair[0] not in self.corner_sides())
        lines = []
        for first, second, both, distance in pairs:
            same = strips[first] == strips[second] and self.side_spec(first).align == self.side_spec(second).align
            for name, side, other in [(both, first, second)] if same else [(first.capitalize(), first, second),
                                                                            (second.capitalize(), second, first)]:
                spacing, holes, left_out = strips[side]
                if holes:
                    align = self.side_spec(side).align
                    lines.append(f"{name}: {holes} hole{'s' if holes > 1 else ''}{' each' if same else ''}, "
                                 + (f"centres {spacing:g} mm apart, " if holes > 1 else "")
                                 + (f"at the {align} corner, " if align and align != "centre" else "")
                                 + (f"strips {distance:.2f} mm apart" if same else
                                    f"{distance:.2f} mm from the {other} strip" if strips[other][1] else f"no holes on the {other}")
                                 + (f", {left_out} left out next to other holes" if left_out else ""))
        return lines

    def growth_report(self):
        """A line per direction in which the mounting holes have made the board larger, for the console."""
        width, height = self.board_size()
        return [f"Board {name} of {size:.2f} mm set by the mounting holes, grid of holes at the {where}"
                for name, size, slack, where in (("width", width, self.slack[0], self.args.array_x),
                                                 ("height", height, self.slack[1], self.args.array_y))
                if slack > 1e-6]

    # ---------- name ----------

    # Each margin is treated as a line: the holes that the text would touch cut it into free
    # stretches, and the name needs one that is long enough.

    def name_spans(self, across, height, length, hole_along_across, taken=None):
        """Free stretches of a margin at a distance across from the edge, with holes as obstacles,
        and also taken, a (start, end) already used in that margin."""
        a = self.args
        reach = a.mount_drill / 2 + MOUNT_TO_LABEL
        blocked = [(along - reach, along + reach) for along, hole_across in hole_along_across
                   if abs(hole_across - across) < reach + height / 2]
        if taken:
            blocked.append(taken)
        spans = []
        start = NAME_TO_EDGE
        for low, high in sorted(blocked):
            spans.append((start, low))
            start = max(start, high)
        spans.append((start, length - NAME_TO_EDGE))
        return spans

    def place_text(self, text, sides=("bottom", "top", "right", "left"), taken=None):
        """Put a text in the first of these margins where it fits. In a margin, it goes on the line
        of the holes if it can, in the free stretch furthest along. taken is a (side, start, end)
        already used. Returns (side, x, y), or None."""
        a = self.args
        width, height = self.board_size()
        ascent, descent = (e + LABEL_THICKNESS / 2 for e in text_extent(text, a.label_size))
        name_width = text_width(text, a.label_size) + LABEL_THICKNESS
        depth_y = depth_x = self.silk_clear_radius() + a.silk_width / 2
        if not a.no_labels:
            clear, half_height, half_width = self.label_metrics()
            depth_y, depth_x = clear + 2 * half_height, clear + 2 * half_width
        # Everything below is relative to the board corner, and for each side is expressed as
        # "across" (distance in from that edge) and "along" (position parallel to it).
        holes = [(x - a.origin_x, y - a.origin_y) for x, y in self.npth]
        geometry = {"bottom": (self.margin["bottom"], depth_y, width), "top": (self.margin["top"], depth_y, width),
                    "right": (self.margin["right"], depth_x, height), "left": (self.margin["left"], depth_x, height)}
        for side in sides:
            margin, depth, length = geometry[side]
            # The top of the text faces the grid at the bottom, and at the right when rotated.
            inner, outer = (ascent, descent) if side in ("bottom", "right") else (descent, ascent)
            low = NAME_TO_EDGE + outer
            high = margin - depth - (NAME_GAP_TOP if side == "top" else NAME_GAP) - inner
            if high < low - 1e-6:
                continue
            across = min(max(MOUNT_TO_EDGE, low), high)
            if side in ("bottom", "top"):
                obstacles = [(x, height - y if side == "bottom" else y) for x, y in holes]
            else:
                obstacles = [(y, width - x if side == "right" else x) for x, y in holes]
            stretch = taken[1:] if taken and taken[0] == side else None
            spans = self.name_spans(across, 2 * max(ascent, descent), length, obstacles, stretch)
            for start, end in reversed(spans):
                if end - start >= name_width:
                    along = (start + end) / 2
                    if side in ("bottom", "top"):
                        x, y = along, height - across if side == "bottom" else across
                    else:
                        x, y = width - across if side == "right" else across, along
                    return side, a.origin_x + x, a.origin_y + y
        return None

    def add_name(self):
        """Put the board's name on the top silkscreen, or warn if it fits nowhere."""
        self.name_placement = self.place_text(self.name())
        if self.name_placement:
            side, x, y = self.name_placement
            self.silk_text(self.name(), x, y, "F.SilkS", vertical=side in ("left", "right"))
        else:
            print(f'Warning: "{self.name()}" does not fit in any margin and was left out. '
                  "Increase --margin or use smaller labels.", file=sys.stderr)

    def add_user_text(self):
        """The --text, in a margin other than the name's if it fits, or else beside the name. This
        is an error if it fits nowhere, because the user asked for this text by hand."""
        a = self.args
        sides = ["bottom", "top", "right", "left"]
        taken = None
        if self.name_placement:
            side, x, y = self.name_placement
            sides.remove(side)
            sides.append(side)
            along = x - a.origin_x if side in ("bottom", "top") else y - a.origin_y
            half = (text_width(self.name(), a.label_size) + LABEL_THICKNESS) / 2 + NAME_GAP
            taken = (side, along - half, along + half)
        self.text_placement = self.place_text(a.text, sides, taken)
        if not self.text_placement:
            length = text_width(a.text, a.label_size) + LABEL_THICKNESS
            sys.exit(f'--text "{a.text}" is {length:.1f} mm long and does not fit in any margin. '
                     "Shorten it, increase --margin or change the mounting holes.")
        side, x, y = self.text_placement
        self.silk_text(a.text, x, y, "F.SilkS", vertical=side in ("left", "right"))

    # ---------- build ----------

    # The order matters in two places. The checks must pass before anything is drawn, and the name
    # goes after the mounting holes, because it has to avoid them.

    def build(self):
        """Check the dimensions and draw the whole board."""
        a = self.args
        self.check_geometry()
        if not a.no_mount_holes:
            self.check_mount()
        # Everything on copper, column by column
        cols, rows = a.cols, a.rows
        n_holes = n_jumpers = 0
        for c in range(cols):
            for r in self.pattern.hole_rows:
                n_holes += 1
                self.hole(c, r, f"PBH{n_holes}")
        # Vertical tracks of each group
        for c in range(cols):
            for r in range(rows - 1):
                if self.pattern.joined(r):
                    self.segment(self.x(c), self.y(r), self.x(c), self.y(r + 1), a.track, c, r)
        # Horizontal buses
        for r in range(rows):
            if self.pattern.is_bus(r) and cols > 1:
                self.segment(self.x(0), self.y(r), self.x(cols - 1), self.y(r), a.bus, 0, r)
        # Jumpers between neighbouring strips: a bus and a group, or two groups. Bus rows have none between them.
        for c in range(cols):
            for r in range(rows - 1):
                if self.pattern.has_chevron(r):
                    n_jumpers += 1
                    self.chevron(c, r, c, r + 1, "B", f"PBJP{n_jumpers}")
        self.n_top_chevrons = self.top_chevrons() if a.top == "smt" else 0
        self.n_islands = self.islands() if a.top == "smt" else 0
        self.silkscreen()
        self.labels()
        self.mount_info = None if a.no_mount_holes else self.mounting_holes()
        self.add_name()
        if self.args.text:
            self.add_user_text()
        # Board outline
        x0, y0 = a.origin_x, a.origin_y
        width, height = self.board_size()
        x1, y1 = x0 + width, y0 + height
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        for (px, py), (qx, qy) in zip(corners, corners[1:]):
            self.art["Edge.Cuts"].append(("line", px, py, qx, qy, 0.1))
        self.items.append(
            f"  (gr_rect (start {fmt(x0)} {fmt(y0)}) (end {fmt(x1)} {fmt(y1)})"
            f' (stroke (width 0.1) (type default)) (fill no) (layer "Edge.Cuts") (uuid "{new_uuid()}"))'
        )
        return n_holes, n_jumpers, (x1 - x0, y1 - y0)

    # The final .kicad_pcb: a fixed header, the list of nets and then every item built above.

    def working_items(self):
        """Return the items for a working copy. The board's top text moves to its own layer. Its connection marks
        would cover the parts, so a thin line through the holes of each strip and bus takes their place, whatever
        --silk says, with a circle of the same width around every drill, so that the holes still show under a part
        whose body hides the pads. None of this is in self.art: it is never fabricated."""
        a = self.args
        moved = []
        for item in self.items:
            if item.startswith("  (gr_") and '(layer "F.SilkS")' in item:
                if not item.startswith("  (gr_text"):
                    continue
                item = item.replace('(layer "F.SilkS")', f'(layer "{WORKING_LABELS_LAYER[0]}")', 1)
            moved.append(item)
        for x1, y1, x2, y2 in self.connection_runs():
            if (x1, y1) != (x2, y2):
                moved.append(f"  (gr_line (start {fmt(x1)} {fmt(y1)}) (end {fmt(x2)} {fmt(y2)})"
                             f" (stroke (width {fmt(WORKING_CONNECTION_WIDTH)}) (type dot))"
                             f' (layer "{WORKING_CONNECTIONS_LAYER[0]}") (uuid "{new_uuid()}"))')
        drills = [(self.x(c), self.y(r), a.drill) for c in range(a.cols) for r in self.pattern.hole_rows]
        drills += [(x, y, a.mount_drill) for x, y in self.npth]
        for x, y, drill in drills:
            moved.append(f"  (gr_circle (center {fmt(x)} {fmt(y)}) (end {fmt(x + drill / 2)} {fmt(y)})"
                         f" (stroke (width {fmt(WORKING_CONNECTION_WIDTH)}) (type solid)) (fill no)"
                         f' (layer "{WORKING_CONNECTIONS_LAYER[0]}") (uuid "{new_uuid()}"))')
        return moved

    def text(self, working=False):
        """Return the contents of the .kicad_pcb file, or of its working copy. Call build() first.

        The working copy is the same board for placing parts on, not for ordering: see working_items().
        """
        a = self.args
        extra_layers = "".join(f'    ({number} "{name}" user "{label}")\n' for number, (name, label) in
                               ((39, WORKING_CONNECTIONS_LAYER), (41, WORKING_LABELS_LAYER),
                                (43, MANUAL_WIRE_LAYER), (45, MANUAL_CHEVRON_LAYER),
                                (47, PLAN_MARKS_LAYER))) if working else ""
        nets = "\n".join(f'  (net {n} "{name}")' for name, n in self.nets.items())
        items = self.working_items() if working else self.items
        return (
            f'(kicad_pcb (version {FORMAT_VERSION}) (generator "generate_protoboard") (generator_version "9.0")\n'
            f"  (general (thickness 1.6) (legacy_teardrops no))\n"
            f'  (paper "A3")\n'
            f"  (layers\n"
            f"{self.copper_layers(working)}"
            f'    (9 "F.Adhes" user "F.Adhesive") (11 "B.Adhes" user "B.Adhesive")\n'
            f'    (13 "F.Paste" user) (15 "B.Paste" user)\n'
            f'    (5 "F.SilkS" user "F.Silkscreen") (7 "B.SilkS" user "B.Silkscreen")\n'
            f'    (1 "F.Mask" user) (3 "B.Mask" user)\n'
            f'    (17 "Dwgs.User" user "User.Drawings") (19 "Cmts.User" user "User.Comments")\n'
            f'    (21 "Eco1.User" user "User.Eco1") (23 "Eco2.User" user "User.Eco2")\n'
            f'    (25 "Edge.Cuts" user) (27 "Margin" user)\n'
            f'    (31 "F.CrtYd" user "F.Courtyard") (29 "B.CrtYd" user "B.Courtyard")\n'
            f'    (35 "F.Fab" user) (33 "B.Fab" user)\n'
            f"{extra_layers}"
            f"  )\n"
            f"  (setup (pad_to_mask_clearance {fmt(a.mask_margin)})"
            f" (solder_mask_min_width {fmt(SOLDER_MASK_MIN_WEB)})"
            # On the first hole, so that KiCad's grid falls on the holes whenever it divides the pitch.
            f" (grid_origin {fmt(self.x(0))} {fmt(self.y(0))}))\n"
            f"{nets}\n"
            + "\n".join(items)
            # After the items, as KiCad writes it: a group lists its members by uuid.
            + "\n" + self.group(items)
            + "\n)\n"
        )

    def copper_layers(self, working):
        """The copper lines of the layer table. A working copy adds --wire-layers inner layers, named
        MCboard.Wires1 on, an even number (cli.py sees to it). KiCad numbers In<n>.Cu 2n + 2."""
        wires = getattr(self.args, "wire_layers", 2) if working else 0
        if not wires:
            return '    (0 "F.Cu" signal) (2 "B.Cu" signal)\n'
        lines = ['    (0 "F.Cu" signal)\n']
        for n in range(1, wires + 1):
            lines.append(f'    ({2 * n + 2} "In{n}.Cu" signal "{WIRE_LAYER_NAME.format(n)}")\n')
        lines.append('    (2 "B.Cu" signal)\n')
        return "".join(lines)

    @staticmethod
    def group(items):
        """A locked group of every item, so that the whole MCboard can be selected and deleted at once in a
        board where parts were placed on it, and is not moved by mistake. Each item's own uuid is its first."""
        ids = [f'"{UUID_RE.search(item).group(1)}"' for item in items]
        # A few to a line: KiCad cannot load a file with a line of about 1 MB, which one line would be on a large board.
        members = "\n".join("    " + " ".join(ids[i:i + 10]) for i in range(0, len(ids), 10))
        return f'  (group "MCboard" (uuid "{new_uuid()}") (locked yes) (members\n{members}\n  ))'
