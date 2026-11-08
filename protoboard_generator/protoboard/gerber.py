# SPDX-License-Identifier: GPL-3.0-or-later
"""Gerber and drill files, and the .zip that packs them.

Plain text formats, simple enough to write directly, so KiCad is not needed for them. A Gerber
file is a plotter program with one file per layer. The commands used here:

  %FSLAX46Y46*%     coordinates are integers with 6 decimals, so 1 mm is 1000000
  %ADD10C,0.15*%    defines aperture (pen) D10, a circle of 0.15 mm diameter
  D10*              picks up that aperture
  X..Y..D02*        moves to a point with the light off
  X..Y..D01*        draws a line to a point with the light on
  X..Y..D03*        flashes the aperture once at a point: that is how a pad is drawn
  G36* ... G37*     fills the polygon drawn in between: that is how a chevron is drawn
  G02* / G03*       the next D01 is a clockwise / counterclockwise arc; I, J: centre minus start
  G01*              back to straight lines
  %TF...*%          attributes describing the file, read by the fab's software

The output imitates the structure of KiCad's files, so as not to surprise the fabs' software.
"""

import datetime
import json
import math
import uuid
import zipfile

from .constants import BOARD_THICKNESS, GERBER_LAYERS, JOB_STACKUP, PROJECT_CLEARANCE
from .geometry import circumcentre, is_counterclockwise


def gerber_coord(point):
    """Format a point for a Gerber file, in the 4.6 format with mm units."""
    return f"X{round(point[0] * 1e6)}Y{round(point[1] * 1e6)}"


def gerber_file(board, primitives, function, polarity, project_id, stamp):
    """Text of an RS-274X (Gerber X2) file: format 4.6 in mm, as KiCad writes it."""
    apertures = {}
    for p in primitives:
        diameter = {"flash": p[-1], "line": p[-1], "arc": p[-1]}.get(p[0])
        if diameter is not None and diameter not in apertures:
            apertures[diameter] = 10 + len(apertures)
    out = [
        "%TF.GenerationSoftware,MCboard,generate_protoboard,1.0*%",
        f"%TF.CreationDate,{stamp}*%",
        f"%TF.ProjectId,{project_id}*%",
        "%TF.SameCoordinates,Original*%",
        f"%TF.FileFunction,{function}*%",
        f"%TF.FilePolarity,{polarity}*%",
        "%FSLAX46Y46*%",
        "G04 Gerber Fmt 4.6, Leading zero omitted, Abs format (unit mm)*",
        "%MOMM*%",
        "%LPD*%",
        "G01*",
    ]
    out += [f"%ADD{code}C,{diameter:.6f}*%" for diameter, code in apertures.items()]
    # Selecting the aperture and moving only when needed keeps long text strokes as one drawn line.
    current = pen = None

    def select(diameter):
        """Change to the aperture of that diameter, if it is not the current one."""
        nonlocal current
        if apertures[diameter] != current:
            current = apertures[diameter]
            out.append(f"D{current}*")

    def move(point):
        """Move to a point with the light off, unless the pen is already there."""
        nonlocal pen
        if pen != gerber_coord(point):
            out.append(f"{gerber_coord(point)}D02*")

    # The four kinds of primitive described in Board.__init__
    for p in primitives:
        if p[0] == "flash":
            select(p[3])
            pen = gerber_coord(board.from_corner(p[1], p[2]))
            out.append(f"{pen}D03*")
        elif p[0] == "line":
            select(p[5])
            move(board.from_corner(p[1], p[2]))
            pen = gerber_coord(board.from_corner(p[3], p[4]))
            out.append(f"{pen}D01*")
        elif p[0] == "arc":
            select(p[4])
            # Convert first: flipping y also swaps clockwise and counterclockwise.
            start, mid, end = (board.from_corner(*q) for q in p[1:4])
            centre = circumcentre(start, mid, end)
            move(start)
            out += ["G75*", "G03*" if is_counterclockwise(start, mid, end) else "G02*"]
            pen = gerber_coord(end)
            out.append(f"{pen}I{round((centre[0] - start[0]) * 1e6)}"
                       f"J{round((centre[1] - start[1]) * 1e6)}D01*")
            out.append("G01*")
        else:  # region
            points = [board.from_corner(*q) for q in p[1]]
            out.append("G36*")
            out.append(f"{gerber_coord(points[0])}D02*")
            out += [f"{gerber_coord(q)}D01*" for q in points[1:] + points[:1]]
            out.append("G37*")
            pen = gerber_coord(points[0])
    out.append("M02*")
    return "\n".join(out) + "\n"


# Excellon, the format for drilling: a header listing the tools (T1C1.000 is tool 1, 1 mm), then
# the tool selection and one line per hole. Plated and unplated holes go in separate files, so that
# the fab can tell them apart. The coordinates must match those of the Gerbers.

def drill_file(board, holes, diameter, plated, stamp):
    """Text of an Excellon file with decimal coordinates in mm, as KiCad writes it."""
    function = ("Plated,1,2,PTH", "Plated,PTH,ComponentDrill") if plated \
        else ("NonPlated,1,2,NPTH", "NonPlated,NPTH,ComponentDrill")
    out = [
        "M48",
        f"; DRILL file MCboard generate_protoboard date {stamp}",
        "; FORMAT={-:-/ absolute / metric / decimal}",
        f"; #@! TF.CreationDate,{stamp}",
        "; #@! TF.GenerationSoftware,MCboard,generate_protoboard,1.0",
        f"; #@! TF.FileFunction,{function[0]}",
        "FMAT,2",
        "METRIC",
        f"; #@! TA.AperFunction,{function[1]}",
        f"T1C{diameter:.3f}",
        "%",
        "G90",
        "G05",
        "T1",
    ]
    for x, y in holes:
        px, py = board.from_corner(x, y)
        out.append(f"X{px:.3f}Y{py:.3f}")
    out.append("M30")
    return "\n".join(out) + "\n"


# The job file (.gbrjob) describes the whole set in JSON: board size, layers and stack-up. It is in
# the same order and has the same fields as KiCad's.

def write_fab_zip(board, base):
    """Write <base>_<w>x<h>_mm.zip with the Gerbers, the job file and the drill files. Returns the path."""
    a = board.args
    project = base.name
    guid = str(uuid.uuid5(uuid.NAMESPACE_URL, project))
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    width, height = board.board_size()
    files = {}
    attributes = []
    for layer, suffix, function, polarity in GERBER_LAYERS:
        if not board.art[layer]:
            continue
        path = f"{project}-{suffix}"
        files[path] = gerber_file(board, board.art[layer], function, polarity,
                                  f"{project},{guid},rev?", stamp)
        attributes.append({
            "Path": path,
            "FileFunction": function.replace("Soldermask", "SolderMask").replace(",NP", ""),
            "FilePolarity": polarity,
        })
    files[f"{project}-job.gbrjob"] = json.dumps({
        "Header": {
            "GenerationSoftware": {"Vendor": "MCboard", "Application": "generate_protoboard",
                                   "Version": "1.0"},
            "CreationDate": stamp,
        },
        "GeneralSpecs": {
            "ProjectId": {"Name": project, "GUID": guid, "Revision": "rev?"},
            "Size": {"X": round(width, 4), "Y": round(height, 4)},
            "LayerNumber": 2,
            "BoardThickness": BOARD_THICKNESS,
            "Finish": "None",
        },
        "DesignRules": [{
            "Layers": "Outer",
            "PadToPad": PROJECT_CLEARANCE,
            "PadToTrack": PROJECT_CLEARANCE,
            "TrackToTrack": PROJECT_CLEARANCE,
            "MinLineWidth": min(a.track, a.bus),
        }],
        "FilesAttributes": attributes,
        "MaterialStackup": JOB_STACKUP,
    }, indent=2) + "\n"
    files[f"{project}-PTH.drl"] = drill_file(board, board.pth, a.drill, True, stamp)
    if board.npth:
        files[f"{project}-NPTH.drl"] = drill_file(board, board.npth, a.mount_drill, False, stamp)
    # Rounding first keeps floating point noise from adding a millimetre to an exact size.
    size = f"{math.ceil(round(width, 4))}x{math.ceil(round(height, 4))}"
    zip_path = base.with_name(f"{project}_{size}_mm.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return zip_path
