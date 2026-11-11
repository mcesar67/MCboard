# SPDX-License-Identifier: GPL-3.0-or-later
"""Parametric protoboard generator, and planner of circuits on its boards. Entry points:
../generate_protoboard.py and ../plan_board.py.

The modules, from the top down:

  cli            the command line, then the calls to all of the below
  board          class Board: all the design decisions, what goes where on the board
  kicad_project  the .kicad_pro and .kicad_dru
  gerber         Gerber and drill text formats, and the .zip that packs them
  pdf            the printable drawing of both sides
  font           reads the stroke glyphs of ../fonts, needed because Gerbers cannot refer to a font
  mounting       pure functions choosing the mounting hole strips
  pattern        the row pattern: --pattern, and class RowPattern, which row is in which strip
  geometry       pure functions for circles
  constants      every dimension and fab limit that is not a command line option

The planner uses pdf, font, constants and the row names of pattern, and nothing of class Board:

  plan_cli       the command line
  kicad_file     reads a .kicad_pcb into a PlacedBoard, with no KiCad involved
  planner        pins to strips, then which chevrons to close and which wires to add
  plan_pdf       the drawing with the marks, and the report as text pages

../kicad_plugin/plan_action.py is the button in KiCad: a thin layer over those, and the only code using KiCad's API.

The flow is short:

  main() -> Board.build()         decides the geometry; writes nothing to disk
         -> Board.text()          the .kicad_pcb, from Board.items
         -> write_project()       the .kicad_pro and .kicad_dru
         -> write_fab_zip()       Gerbers, job and drill files, from Board.art, .pth and .npth
         -> write_pdf()           the drawing, from the same Board.art and .npth

The key idea: Board records everything twice. Board.items holds KiCad text, and Board.art holds
the same shapes as plain data for the writers that do not use KiCad. Whatever is drawn in one must
also be drawn in the other, or the board and the Gerbers will differ. A PlacedBoard has its own art,
read back from the file, so a board edited by hand comes out as it is.

Two coordinate systems, both in mm:

  sheet    What KiCad uses, and all of class Board: y grows downwards, and the board corner sits
           at --origin-x, --origin-y on the drawing sheet.
  fab      What Gerbers, drill files and the PDF use: y grows upwards, and the lower left board
           corner is 0, 0. Board.from_corner() converts sheet to fab.

Vocabulary:

  hole     a plated hole of the grid, with its pad; addressed by 0-based col and row
  group    1 or more holes of a column joined by a vertical track (a "strip"); their sizes are the
           numbers of --pattern. Not to be confused with the KiCad group "MCboard", which holds every item
  bus      whole rows, b or 2b... in --pattern, each joined by a horizontal track of its own (a rail)
  space    a row of the grid with nothing on it, s or 2s... in --pattern; row letters skip it
  chevron  a solder jumper between two neighbouring holes, open until soldered
  island   a small square top pad between 4 holes, for SMD feet, tied for good to one hole's pad
  strip    also a line of mounting holes along one side of the board
  margin   the distance from the board edge to the centre of the first hole of the grid
  label    a column number or row letter on the silkscreen
  name     the "MCboard <cols>x<rows>" text
  along / across   position parallel to a side of the board / distance in from that side
"""
