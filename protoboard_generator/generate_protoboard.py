#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Generate a KiCad project for a solderable protoboard with a periodic pattern, by default 5+1:

  row 0      horizontal bus (drilled)
  rows 1-2   2-contact group (vertical strip)
  rows 3-5   3-contact group (vertical strip)
  row 6      horizontal bus ... and the pattern repeats every 6 rows

There is a chevron solder jumper, open by default, between the bus and the
2-contact group, between the 2- and 3-contact groups, and between the
3-contact group and the next bus. That is the default, --pattern b+2+3: the
rows from the top, joined by + and repeated, a number being a group of that many
rows and b a bus row. b+3+2, b+5 and b+2+3+b+3+2 work too. 2b is two bus rows,
each a rail of its own with nothing between them; only the outer ones have
chevrons, to the groups beside them. s is a space, a row with nothing on it:
2b+2s+5+2s+5+2s, or classic, is a classic breadboard. A pattern may have no bus, such as 5.

- All track copper is on the bottom side (B.Cu). The top side only carries the
  annular rings of the plated holes unless --top smt is used (order it as a 2-layer
  board; plated holes need a ring on both sides).
- Solder mask covers everything except the pads and the chevrons (the chevron
  opening includes the gap so it can be bridged with solder).
- Every strip and every bus has its own net, so KiCad's DRC checks the
  clearances between them.
- --top smt adds exposed chevrons on F.Cu, so SMT parts can be soldered: between
  columns on every row except the buses, and between groups over the bottom ones.
- The top silkscreen shows the existing connections (strips and buses): a bar
  over each track and a line between groups ('bars'), short lines between holes
  ('lines') or a capsule around each group and bus ('outline'), keeping the fab's
  pad-to-silkscreen clearance. The 'bars' lines break at the chevrons of --top smt.
- Both silkscreens label the columns (1, 2, ...) along the top and bottom edges
  of the grid and the rows (A, B, ..., Z, AA, AB, ...) along the left and right
  edges. Bottom text is mirrored so that it reads correctly from that side.
- M3 mounting holes run in the outer margin, in strips centred on each side. The
  spacing inside a strip is always a multiple of 5 mm, and of 10 mm when possible.
  The strips on the longer sides reach the corners; those on the shorter sides
  only fill the space left between them, so they may be empty. A side with no
  holes gets the smaller --margin-plain. --mount-top and the like set a side by
  hand, as holes x spacing : alignment, such as 3x40:left, and the board grows if
  that needs it. --array-x and --array-y then place the grid of holes in it.
- The top silkscreen carries the name "MCboard <cols>x<rows>", wherever it fits in
  the outer margin, and also --text if given, in another margin when possible.

Usage:
  python3 generate_protoboard.py --cols 30 --rows 61 --fab jlcpcb

By default everything goes to <cols>x<rows>_YYYYMMDDTHHMM/MCboard_<cols>x<rows>.*;
-o sets another folder and base name. Besides the .kicad_pcb, the following are
written next to it with the same name:
  - <name>.kicad_pro  project with mask web width, severities and constraints
  - <name>.kicad_dru  fab custom design rules (2 layers, 1 oz), copied from the
                      'rules' folder next to this script
                      (source: github.com/Cimos/kicad-druid, MIT license)
  - <name>_<w>x<h>_mm.zip  with the board width and height in whole mm, rounded up:
                      the fab package: Gerbers, job file and Excellon drill files.
                      Written directly by this script, KiCad is not needed, so it
                      does not include anything added to the board later in KiCad
  - <name>.pdf        A4 drawing of both sides, at the largest of 200%, 141% and 100%
                      that fits inside 12.7 mm margins, with the scale written below the
                      board. The bottom side is drawn as seen from below, so its text
                      reads normally and column 1 is on the right. It goes right next to
                      the top side, or on a second page if both do not fit at 100%

With --working-copy, also <name>_working.kicad_pcb, .kicad_pro and .kicad_dru: the same board for
placing parts on, with its own top silkscreen on User.1 and User.2. Never order from it.

The script needs the 'protoboard', 'rules' and 'fonts' folders next to it.

For the board to start and end with a bus, use rows = 6*k + 1 (7, 13, 19, ...).
The file is written in KiCad 9 format; KiCad 9 and 10 open it, KiCad 7 and 8 do not.
"""

from protoboard.cli import main

if __name__ == "__main__":
    main()
