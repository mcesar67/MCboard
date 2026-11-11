# SPDX-License-Identifier: GPL-3.0-or-later
"""Every dimension and fab limit that is not a command line option. All in mm unless stated otherwise."""

# The .kicad_pcb format of each KiCad version that --kicad takes: (version, generator_version)
FORMAT_VERSIONS = {"9": ("20241229", "9.0"), "10": ("20260206", "10.0")}
KICAD_DEFAULT = "10"

# Custom rules file per fab (2-layer, 1 oz copper variant)
FAB_RULES = {
    "jlcpcb": "JLCPCB-2L-1oz.kicad_dru",
    "pcbway": "PCBWay-2L-1oz.kicad_dru",
    "generic": "Generic-2L-1oz.kicad_dru",  # satisfies both JLCPCB and PCBWay
}

# General project constraints (Board Setup > Constraints), in mm.
# The strictest of JLCPCB and PCBWay for 2 layers and 1 oz; the per-item
# details are refined by the rules in the .kicad_dru file.
PROJECT_RULES = {
    "min_clearance": 0.127,
    "min_track_width": 0.127,
    "min_connection": 0.127,
    "min_through_hole_diameter": 0.3,
    "min_hole_to_hole": 0.5,
    "min_hole_clearance": 0.33,
    "min_copper_edge_clearance": 0.3,
    "min_via_annular_width": 0.15,
    "min_via_diameter": 0.5,
    "min_silk_clearance": 0.15,
    "min_text_height": 1.0,
    "min_text_thickness": 0.15,
}

# Limits and distances of the silkscreen, labels, name and mounting holes
SOLDER_MASK_MIN_WEB = 0.2  # minimum solder mask web (JLCPCB, green mask)
ISLAND_SIDE = 0.93         # the square top pads of --top smt; at 0.94 the mask web is exactly the 0.2
                           # minimum, and KiCad closes such a web on plotting, joining openings of two nets
ISLAND_TIE = 0.4           # the track, under the mask, from an island to its hole's pad
ISLAND_TIE_INTO_PAD = 0.05 # how far the centre of that track ends inside the pad's outer edge
SILK_TO_PAD = 0.15         # pad (mask opening) to silkscreen clearance
SILK_BAR_STEPS = 6         # straight pieces in each curved end of a silkscreen bar
SILK_BAR_SLACK = 0.001     # so that rounding of a coordinate does not break that clearance
LABEL_THICKNESS = 0.15     # minimum line width for silkscreen text (JLCPCB, PCBWay)
LABEL_GAP = 0.3            # between the labels and the connection silkscreen
LABEL_CHAR_WIDTH = 1.0     # conservative character advance, as a fraction of text size
LABEL_TO_EDGE = 0.3        # minimum distance from a label to the board edge
MARGIN_DEFAULT = 8.5       # fits M3 holes outside labels of up to two letters
MARGIN_PLAIN = 4.0         # default margin on sides without mounting holes
MOUNT_TO_EDGE = 3.0        # board edge to mounting hole centre; fits a 5 mm hex standoff
MOUNT_TO_LABEL = 0.3       # mounting hole edge to the labels
MOUNT_MIN_SPACING = 10     # closest two mounting holes of a strip may be, in mm
MOUNT_MIN_FIXED = 5.0      # the same for a spacing given by the user; two 5 mm hex standoffs touch
MOUNT_MIN_WEB = 0.5        # least material between two mounting holes of a fixed spacing
MOUNT_MAX_INSET = 10.0     # how far a longer-side strip may stop short of each corner
MOUNT_GAP_TOLERANCE = 1.25 # opposite strips this many times the spacing apart need no holes between
NAME_TO_EDGE = 0.25        # board edge to the name
NAME_GAP = 0.2             # the name to the labels: the fabs' 0.15, plus what digits exceed their nominal box
NAME_GAP_TOP = 0.25        # the same in the top margin, because a 4 rises 0.09 above that box

# Fab package. A mask file has negative polarity because it draws the openings, not the mask.
# (layer, file name suffix, Gerber file function, polarity). Empty layers are not written.
GERBER_LAYERS = (
    ("F.Cu", "F_Cu.gtl", "Copper,L1,Top", "Positive"),
    ("B.Cu", "B_Cu.gbl", "Copper,L2,Bot", "Positive"),
    ("F.SilkS", "F_Silkscreen.gto", "Legend,Top", "Positive"),
    ("B.SilkS", "B_Silkscreen.gbo", "Legend,Bot", "Positive"),
    ("F.Mask", "F_Mask.gts", "Soldermask,Top", "Negative"),
    ("B.Mask", "B_Mask.gbs", "Soldermask,Bot", "Negative"),
    ("Edge.Cuts", "Edge_Cuts.gm1", "Profile,NP", "Positive"),
)
JOB_STACKUP = [
    {"Type": "Legend", "Name": "Top Silk Screen"},
    {"Type": "SolderPaste", "Name": "Top Solder Paste"},
    {"Type": "SolderMask", "Thickness": 0.01, "Name": "Top Solder Mask"},
    {"Type": "Copper", "Thickness": 0.035, "Name": "F.Cu"},
    {"Type": "Dielectric", "Thickness": 1.51, "Material": "FR4", "Name": "F.Cu/B.Cu",
     "Notes": "Type: dielectric layer 1 (from F.Cu to B.Cu)"},
    {"Type": "Copper", "Thickness": 0.035, "Name": "B.Cu"},
    {"Type": "SolderMask", "Thickness": 0.01, "Name": "Bottom Solder Mask"},
    {"Type": "SolderPaste", "Name": "Bottom Solder Paste"},
    {"Type": "Legend", "Name": "Bottom Silk Screen"},
]
BOARD_THICKNESS = 1.6
# The top silkscreen of the board in a --working-copy, kept apart from that of the parts placed on it.
# KiCad can only show or hide whole layers, and footprints bring their silkscreen to F.SilkS.
# From User.5 on, because a new KiCad 10 board has User.1 to User.4 already, and File > Append Board keeps the open
# board's names for the layers it has: layers it lacks arrive with these names.
# a dotted line through the holes of each strip and bus, and the drills
WORKING_CONNECTIONS_LAYER = ("User.6", "MCboard.Connections")
WORKING_CONNECTION_WIDTH = 0.05  # thinner than MANUAL_LINE_WIDTH, so that wires stand out over it
WORKING_LABELS_LAYER = ("User.7", "MCboard.Labels")          # rows, columns, name and --text
WORKING_SILK_LAYERS = (WORKING_CONNECTIONS_LAYER[0], WORKING_LABELS_LAYER[0])
PLAN_MARKS_LAYER = ("User.5", "MCboard.Plan")             # the plan button's marks, cleared on each run
USER_LAYERS = (PLAN_MARKS_LAYER, WORKING_CONNECTIONS_LAYER, WORKING_LABELS_LAYER)  # the working copy's own
USER_LAYER_NUMBERS = {"User.5": 47, "User.6": 49, "User.7": 51}  # KiCad's numbers for them in the file
LAYER_REPORT_TITLE = "Working copy layers, KiCad = MCboard:"   # heads the list in the console and the PDF
MANUAL_LINE_WIDTH = 0.3    # default width of lines drawn on the user layers
# Inner copper layers of a working copy: --wire-layers for wires, named 1 on from the first inner layer after the
# board's own copper, then one for closed chevrons. Tracks the user draws on them are the manual part of a plan;
# the apply button adds its own, locked. KiCad numbers inner layers from In1 with no gaps, only up to In30, and
# loads no file with an odd number of copper layers, so --wire-layers is raised by one when the total is odd.
WIRE_LAYER_NAME = "MCboard.Wires{}"
CHEVRON_LAYER_NAME = "MCboard.Chevrons"
MAX_INNER_COPPER = 30
APPLY_CHEVRON_TRACK = 0.3  # width of the track that stands for a closed chevron, once a plan is applied
APPLY_WIRE_TRACK = 0.3     # and of those that stand for a wire

PROJECT_CLEARANCE = 0.2    # the Default net class clearance in the .kicad_pro

# The PDF. 141% is the step between A sizes.
PDF_PAGE = (210.0, 297.0)  # A4 in mm, portrait
PDF_MARGIN = 12.7
PDF_SIDE_GAP = 2.5  # mm on paper between the two sides; a 30x61 at 100% needs it this small
PDF_SCALES = (2.0, 1.41, 1.0)
PDF_COPPER_RGB = (0.80, 0.50, 0.20)
PDF_DRILL_LINE = 0.1       # line width of the mounting holes, in board mm
PDF_CAPTION_SIZE = 3.5     # height of the scale caption on paper, in mm (10 pt)
PDF_CAPTION_GAP = 1.5      # board edge to the top of the caption on paper, in mm
PDF_CAPTION_ASCENT = 0.72  # top of Helvetica capitals above the baseline, as a fraction of its size
PDF_NOTE_SIZE = 2.8        # height of the mounting hole notes on paper, in mm (8 pt)
PDF_NOTE_LEADING = 3.6     # distance between the lines of those notes on paper, in mm
PDF_NOTE_DESCENT = 0.21    # Helvetica descenders below the baseline, as a fraction of its size
PDF_NOTE_EDGE = 5.0        # closest the notes come to the page edge, outside what printers cannot reach
# Helvetica advance widths of the ASCII characters from space on, in thousandths of the size, from its AFM
PDF_HELVETICA_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
PDF_COMMAND_SIZE = 2.1     # the command line at the foot of the page, in Courier, on paper in mm (6 pt)
PDF_COMMAND_LEADING = 2.6
PDF_COMMAND_ASCENT = 0.63  # top of Courier capitals above the baseline, as a fraction of its size
PDF_COMMAND_GAP = 1.0      # the lowest thing drawn above it to the top of its first line
PDF_COMMAND_BOTTOM = 4.0   # bottom of the page to the baseline of its last line
PDF_COMMAND_LINES = 3      # what fits in the bottom margin; a longer command is cut short with ...
PDF_LOCKED_TRIES = 9       # names tried when a PDF is open in a viewer that locks it: <name>.pdf, then -2 to -9

# Working out a plan for a placed board (planner.py, manual.py)
PLAN_PIN_TOLERANCE = 0.25  # most a through-hole pin may be off a hole; a lead bends this much
PLAN_PIN_EXACT = 0.05      # beyond this, the report says that the pin is slightly off
PLAN_SMD_REACH = 1.5       # most an SMD pad may be from its nearest hole: half a pitch, plus a little
PLAN_SMD_ON_CHEVRON = 1.0  # a top chevron with an SMD pad this close is under the pad, so it is not free
PLAN_CHEVRON_COST = 1.0    # one solder bridge
PLAN_TOP_CHEVRON_COST = 1.01  # so that, of two chevrons one over the other, the bottom one is chosen
PLAN_WIRE_COST = 5.0       # a wire is worth this many chevrons: it has to be cut and soldered at both ends
PLAN_VERTICAL_COST_PER_MM = 0.2  # on top of that, for each mm running along a column: wires are mostly horizontal
PLAN_WIRE_COST_PER_MM = 0.01  # only to choose the shorter of two wires
PLAN_SHARED_HOLE_COST = 1.0  # for each pitch two wires run along together: possible to build, but untidy
PLAN_EXTRA_BEND_COST = 0.5  # for each bend after the first, on a wire that has to go round parts
PLAN_MAX_ATTEMPTS = 300    # orders in which to try the nets
PLAN_MAX_STEPS = 2000000
PLAN_MANUAL_MEET = 0.02    # manual tracks whose ends are this close meet there

# The marks on the plan's PDF, in board mm
PLAN_MARK_RGB = (0.85, 0.0, 0.75)   # chevrons to close: magenta, as red is a wire colour
# Wires to add: the colour of MCboard.Wires<n> is the n-th, in turn, in the order of Dupont jumper colours. Orange
# and yellow are left out, as they hardly show on the copper and on white; brown is dark for the same reason.
PLAN_WIRE_COLOURS = (("black", (0.0, 0.0, 0.0)), ("brown", (0.35, 0.15, 0.0)), ("red", (0.85, 0.0, 0.0)),
                     ("green", (0.0, 0.6, 0.0)), ("blue", (0.0, 0.2, 0.85)), ("violet", (0.45, 0.0, 0.75)),
                     ("grey", (0.5, 0.5, 0.5)))
PLAN_OTHER_SIDE_DASH = (0.4, 0.3)   # on and off, for the marks of a chevron on the other side
PLAN_PART_RGB = (0.0, 0.45, 0.2)    # outlines and names of the parts
PLAN_RING_RADIUS = 1.0              # around a chevron to close
PLAN_RING_LINE = 0.2
PLAN_BRIDGE_LINE = 0.5              # across its gap, like the solder will be
PLAN_WIRE_LINE = 0.3
PLAN_WIRE_END = 0.5                 # radius of the circle at each end of a wire
PLAN_WIRE_BEND = 0.8                # how far from a corner of a wire its curve starts
PLAN_PIN_RADIUS = 0.35              # dot on a hole with a pin in it
PLAN_PART_LINE = 0.1
PLAN_TEXT_SIZE = 1.3                # names of parts and wires
PLAN_REPORT_SIZE = 10.0             # the text page, in points
PLAN_REPORT_LEADING = 12.0
PLAN_REPORT_COLUMNS = 87            # lines of that page are wrapped at this many characters
