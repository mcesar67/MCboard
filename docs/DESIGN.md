# MCboard design notes

Why the board and its tools are the way they are: the pattern, the dimensions, what was rejected, and how to check a
change. How to use them is in the [user manual](MANUAL.md); this file does not repeat it.

## Code

- Generator: `protoboard_generator/generate_protoboard.py`, standard library only, so that KiCad is needed only to
  check or edit its output. It is a thin entry point: the code is in `protoboard/`, whose `__init__.py` is the reading
  guide. It needs `protoboard/`, `rules/` and `fonts/` next to it.
- Planner: `protoboard_generator/plan_board.py`, and the KiCad button in `kicad_plugin/`, installed by
  `install_kicad_plugin.py`, which copies the code into KiCad's plugin folder.
- Font: `protoboard_generator/fonts/newstroke.txt`, rebuilt by `fonts/extract_newstroke.py`.
- Fab rules: `protoboard_generator/rules/`, from kicad-druid (MIT; see `rules/ORIGIN.txt`).
- Pictures in `docs/`: `kicad-cli pcb render` of a 14x13.
- Everything is in English: comments, help, messages, identifiers, and the names of files, footprints and nets.
- Licence: GPL-3.0-or-later (`LICENSE`, `NOTICE.md`). New `.py` files start with the SPDX line.

## The pattern

The default period, 0-based row index in the code:

| Row (index % 6) | Kind |
|---|---|
| 0 | Bus |
| 1, 2 | 2-contact group |
| 3, 4, 5 | 3-contact group |

### Why 5+1

The layout is `n-m`: strips of `n` holes separated by `m` rows. For a component `W` pitches wide, with `P = n + m`
and `r = W mod P`, the free contacts outside the component, in the best position:

- if `r > m`: `(n - 1) + (P - r)`
- if `r <= m`: `(n - 1) - r`, valid only if `W >= P` and the result is not negative

Free contacts per width, with all the 2-3 jumpers closed (strips of 5):

| Layout | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | min | mean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5-1 (chosen) | 7 | 6 | 5 | 4 | 3 | 8 | 7 | 6 | 5 | 4 | 3 | 5.5 |
| 5-2 (standard breadboard) | 8 | 7 | 6 | 5 | 4 | 3 | 2 | 8 | 7 | 6 | 2 | 5.6 |

Layouts giving 0, 1 or no fit at some width (3 to 12) were discarded, and so were layouts with no gap row (`m = 0`).
5-1 was chosen over 6-1 and 7-1, which score higher but take more board area per period, and it stays close to how a
breadboard is used.

The 5 is split into 2+3 for two reasons:

- The small groups are usable on their own, and closing the chevron restores a full strip of 5.
- Dual-row 2.54 mm headers fit at index 2 and 3, across the 2-3 chevrons, with every pin on its own net and 1 and 2
  extra contacts. A pin row must not sit on a bus row, so this is the only row pair that works.

The reference module was the ESP32-C6-DevKitC-1: pin rows 22.86 mm apart (`W = 9`). Its best positions are pin rows at
index 4 and 1 (rows E and N) or 5 and 2 (F and O), with 7 free contacts with the 2-3 chevrons closed but only 2 with
them open, and the buses only under the module.

### `--pattern`

`--pattern` exists for circuits that want other strip lengths; the arithmetic above tells how each one fits a part.

- `+` separates the elements because no shell, on Linux or Windows, splits or expands it, so the value needs no quotes.
- `b+3+2` is the 2+3 period upside down (index `m` becomes `6 - m`), so every rule here holds for it once turned over.
- Two bus rows have nothing between them, no chevron and no track: a closed jumper between rails would short two
  supplies, and the rails are reached from the groups anyway. The silkscreen draws a plain line there.
- A space (`s`) has no hole, chevron, track or silkscreen, so it is where strips end for good. `classic` gives 7.62 mm
  between the last hole of one strip of 5 and the first of the next, the row spacing of a narrow DIP.
- Rows are counted two ways. `--rows`, the name on the board and the drawing count grid rows, spaces included, because
  the size of the board follows from them. Row letters and net names count hole rows, as on a breadboard, which is also
  how the planner sees the board: it reads only the holes. The planner measures obstacles and wire costs by row
  position, not number, since a space puts more than a pitch between two neighbouring hole rows.
- The ideal number of rows ends on a whole bus (a bus ends where a non-bus element follows), or on a whole group if the
  pattern has no bus.
- Nets are `BUS_R<n>` and `C<col>_R<first row of group>`, 1-based hole rows. Each strip and bus has its own net so that
  DRC checks the clearances between them. The planner reads the strips from the nets, so it needs no option.
- The rest of this file is written for the default pattern.

## Top side (`--top smt`)

Exposed chevrons on F.Cu, the same size as the bottom ones:

- Horizontal between columns on every row except the buses. Tips: right on even row indices, left on odd.
- Vertical only between groups, over the bottom ones. Tips: up on columns numbered odd on the board, down on even ones.
- None inside a group, where the holes are already joined and a part would be shorted, or along a bus.

The tip points from the first hole to the second, and the first hole holds the footprint. Nets are those of the two
holes, so DRC checks them as usual. Refs are `PBJT<n>`.

Islands let a SOT-23 reach 3 separate strips, which the chevrons alone do badly (42 % of a foot on copper at best,
0.08 from the next net):

- A 0.93 square pad in the cell between 4 holes, inside a group, so the silkscreen stays as it is. There is no separate
  option. Refs are `PBIS<n>`, footprint `Island`.
- One row of cells per group of 2 rows or more. A second row would add places, not parts: the group joins each column,
  so a second part there would share the first one's strips.
- The third foot needs a vertical chevron, which is only at a group's edge, so the cell is the group's first or last:
  next to the bus when the group touches only one bus, otherwise the first. An edge with no chevron, beside a space or
  at an edge of the board, is no use, so the cell moves to the other edge, and a group with neither has none. A centre
  cell would leave the third foot short (56 %, as E-F with the pin up). This gives rows 1 and 4 for 2+3 and 3+2.
- Each is tied for good to one hole's pad by a 0.4 track on F.Cu, under the mask, on the diagonal: left on even column
  indices, right on odd; down on odd row indices, up on even. A solder bridge instead would be too delicate. Up or down
  makes no electrical difference, as both holes are one strip.
- The cell at `c = cols - 2` is left out when `c` is even, as it has no partner for a part.
- Computed with typical sizes (foot 0.42 x 0.5, feet at +-0.95 and +-1.1), not datasheets: 82 % of the worst foot on
  copper, 0.22 from the next net, and x +-0.15, y +-0.05 to 0.09 keeps 0.15. SOT-23-6 was given up: its only position
  with 6 separate nets had about 0.02 of tolerance.
- The mask web beside a chevron is 0.205. At 0.94, with a web of exactly 0.2, KiCad's plot closed it and joined the
  openings of an island and a chevron, while the zip and DRC showed nothing. `island_errors()` requires more than the
  minimum for that reason.

## Dimensions (mm)

| Item | Value | Why |
|---|---|---|
| Pitch | 2.54 | |
| Drill | 1.0 | Fits square header pins (0.64, about 0.9 diagonal) and common leads |
| Pad | 1.6 | 0.3 annular ring; leaves 0.94 between pads for the chevron |
| Strip track | 1.0 | |
| Bus | 1.0 | Must not exceed the pad diameter, or it encroaches on the chevrons (rejected) |
| Chevron gap | 0.25 | Perpendicular; anything below 0.15 is rejected |
| Chevron width / V depth | 1.0 / 0.3 | Gap is centred between the two pads |
| Island / tie | 0.93 / 0.4 | Web 0.205 to the chevrons, 0.218 to the pads |
| Mask expansion | 0.05 | |
| Mask minimum web | 0.2 | JLCPCB, green mask |
| Silkscreen line / to pad | 0.15 / 0.15 | Clearance measured from the mask opening |
| Silkscreen bar | Track or bus width | Ends curve around the pad at 1.0 from the hole centre |
| Margin, sides with holes | 8.5 | Fits M3 holes outside labels of up to two letters |
| Margin, sides without holes | 4.0 | Also used on all sides with `--no-mount-holes` |
| Mounting hole | 3.2, unplated | M3 close fit; centre 3.0 from the edge, room for a 5 mm hex standoff |
| Label height / line | 1.0 / 0.15 | JLCPCB minimums. Labels that do not fit are rejected |

## Layers

- All tracks and buses are on B.Cu, apart from the ties of the islands. Mask covers everything except the pads, the
  chevrons and the islands, on both sides; the opening includes the chevron gap, so it can be bridged. There is always
  mask between columns.
- F.Cu has only the annular rings of the plated holes, plus the chevrons and islands of `--top smt`. The board is
  2-layer because plated holes need a ring on both sides. Copper 1 oz.

## Mounting holes

- The automatic spacing inside a strip is a multiple of 5 mm, and of 10 mm whenever that does not cost more than one
  extra hole. This is a requirement and must hold for any board size.
- The automatic strips on the two longer sides reach the corners: fewest holes that stop within 10 mm of the end of
  the hole line. The strips on the shorter sides only fill the space between those two, so they do not share corner
  holes, and are empty when the longer-side strips are at most 1.25 x `--mount-spacing` apart.
- Which sides are the longer ones is decided by the array, `cols >= rows`, or by the size it needs once grown
  (`stretched_array()`), so the strips can end up on what is now the shorter side of a nearly square board.
- A side with no holes uses the smaller `--margin-plain`, so that the board is no wider than it needs to be.
- A side given by hand inherits nothing from the opposite one. There is deliberately no option for the distance between
  opposite strips: it follows from the array plus the margins, or from the other pair's `3x40`, and is not a multiple
  of 5.
- Where strips meet, holes in the same place are one, which is how strips share a corner. Holes closer than 5 mm (or the
  drill plus 0.5, if larger): `3x40` beats `3` or `x40`, which beat automatic, and otherwise the corner strips win.
  Two `3x40`-style sides colliding is an error, as neither may give way.
- A screw head or washer may cover part of a label, but the drill itself must clear the labels: 0.3 from the nominal
  label box, and a margin below that is rejected. At 0.2 the glyphs of a two-digit column number came within 0.14 of
  the hole, under the fabs' 0.15.
- Holes are unplated so that a screw connects to nothing.

## Name and text

- The name tries bottom, top, right, left (rotated on the sides); in a margin it goes on the line of the mounting holes
  if possible, centred in the last free stretch. `--text` tries the other margins first. A name that fits nowhere is a
  warning; a `--text` that fits nowhere is an error, because it was asked for by hand.
- The top margin leaves 0.25 to the labels instead of 0.2, because a `4` rises 0.09 above its nominal box.
- `~{`, `_{`, `^{` and `${` are refused because KiCad reads them as formatting and would draw something other than the
  Gerbers.
- `fonts/newstroke.txt` is the glyph table compiled into KiCad, from its 9.0 branch; master differs in about 278 glyph
  lines. Its licence statements are mixed (see `NOTICE.md`), which is why the project is GPL-3.0-or-later. Label sizes
  other than 1 scale correctly but are not calibrated, so they can differ from KiCad by some hundredths of a mm.

## Fab package and PDF

The generator writes these itself, without KiCad, from a copy of the geometry kept in `Board.art`, `Board.pth` and
`Board.npth`. **Anything drawn into `Board.items` must also go into `art`**, or the board and the Gerbers will differ.

- `.zip`: Gerbers (`F_Cu.gtl`, `B_Cu.gbl`, `F_Silkscreen.gto`, `B_Silkscreen.gbo`, `F_Mask.gts`, `B_Mask.gbs`,
  `Edge_Cuts.gm1`), `-job.gbrjob`, `-PTH.drl` and `-NPTH.drl`, lower left corner at 0, 0. Paste layers are empty and
  not written. The job file's `Size` is the true board size; KiCad's includes the width of the edge line.
- `.pdf`: A4 portrait, 12.7 mm margins. Pads are drawn as rings with the real drill, which show more than solid discs.
  The bottom side is seen from below, so its text reads normally. The scale is the largest of 200 %, 141 % and 100 % at
  which both sides fit, rotated only if that gains scale; otherwise a page per side, and no PDF if one side does not fit
  at 100 %.
- The scale note, the mounting hole distances and the command line do not count for the fit, so they never lower the
  scale; on a board that nearly fills the page they enter the margin (by at most 5 mm, 9.5 mm and one line).
- The command line at the foot leaves out `-o`, whose path may say more about the computer than the reader needs, and
  `--working-copy`, which only adds a file. Lines break between options, never inside one (`PDF_COMMAND_LINES`).
- Both fonts declare `WinAnsiEncoding`: without it a viewer uses the fonts' standard encoding, which draws the `'` of a
  quoted `--text` as a curly quote that a shell would not take.
- Both PDFs, the generator's and the plan's, go through `write_unlocked()`: a file held open by a viewer, as on Windows,
  becomes `<name>-2.pdf`, up to `-9` (`PDF_LOCKED_TRIES`), rather than a traceback after the work is done.

## Top silkscreen

- `bars`: a bar over each track between joined holes, and a 0.15 line halfway between rows at each group boundary,
  from 1.15 before the first column to 1.15 after the last, which needs no extra margin. With `--top smt` it breaks at
  each vertical chevron, ending 0.70 from the column centre.
- `outline` would cross the chevrons of `--top smt`, so the two are refused together.
- The bars are filled polygons: `gr_poly` on the board and regions in the Gerbers and PDF. Each curved end is straight
  pieces tangent to the clearance circle, so no piece cuts into it.

## Planner

- `protoboard/kicad_file.py` reads the `.kicad_pcb` with its own parser, not `pcbnew`, so it does not depend on the
  installed KiCad. Only `kicad_plugin/action.py` uses `pcbnew`, kept thin because KiCad has deprecated those bindings.
  It reads file formats 7, 9 and 10; format 10 has no top-level net table.
- Generated footprints are `board_only`, with refs `PBH`, `PBJP`, `PBJT`, `PBIS`, `PBMH`, so a netlist update neither
  deletes them nor matches them to a schematic's `H1` or `JP1`. The planner goes by footprint name (`Hole`,
  `ChevronJumper`, `MountingHole`), not by ref.
- **The model.** Strips and buses are nodes and chevrons are edges. Different nets must not share a node, which makes it
  a vertex-disjoint Steiner forest, NP-hard, so the planner is a heuristic and may not find the fewest wires or chevrons.
- **Wires** are expensive extra steps between free holes (`PLAN_WIRE_COST`), and each mm along a column costs more
  (`PLAN_VERTICAL_COST_PER_MM`), so they come out mostly horizontal, as in hand-wired builds. There is deliberately no
  option for another behaviour. Wires go on the top side, so none may end or run inside a part's courtyard or over a
  hole with a pin: a hard rule, verified by `check()`. A part with no courtyard blocks only its pins. Wires follow rows
  and columns, as an L when possible, otherwise with as many bends as it takes (`detour()`, only tried when no L is
  open, `PLAN_EXTRA_BEND_COST`).
- Running along another wire costs `PLAN_SHARED_HOLE_COST` per pitch and gives a warning. Forbidding it left 7 to 10
  strips of a 30x61 test unconnected, even with 20 times the work, and overlapping wires can still be built. Crossing a
  wire is free. A planned wire never ends on a hole where another wire ends.
- **Determinism.** Many net orders from a fixed seed, then rerouting net by net, with limits (`PLAN_MAX_ATTEMPTS`,
  `PLAN_MAX_STEPS`) that count work and not time, so the same board always gives the same plan. `check()` verifies the
  result independently and raises if it is wrong.
- Pins within 0.25 of a hole count as in it, with a warning beyond 0.05. A part with all pins off the grid is "not on
  the board", not an error of position. Pins with `unconnected-` nets need no connection but still warn if they share
  a strip with a net.
- An SMD pad whose centre is on an island takes the strip the island is tied to, which is not always the nearest
  (`hole_of_pin()`). Any other SMD pad is taken to connect to the nearest hole within 1.5.
- A chevron through a bus takes the whole bus, so at most one net can use each bus. A strip whose holes all have a pin
  or are under a part can only leave by chevron. So **placement decides whether a plan exists**: a DIP on rows B and E
  has all the strips of its B side under it, all needing bus A; straddling a bus (rows F and I) gives every pin a way out.
- Random pins on a 30x61 (117 pins, 24 nets): 85 chevrons, 108 wires, 2 strips unconnected, about 6 s; 10M steps do not
  change that. It is not a realistic circuit, but it sets the scale of the limits.

### Manual lines

Lines on `User.3` and `User.4` (`MANUAL_WIRE_LAYER`, `MANUAL_CHEVRON_LAYER`, `add_manual()`) are soldering decided by
hand.

- Chevrons and wires have a layer each because on one layer they look the same, and because then a one-pitch wire
  across a chevron is an ordinary line. A straight line on `User.4` across one chevron closes it, the bottom one if
  there are two; any other line there is an error.
- On `User.3` an end within `PLAN_PIN_TOLERANCE` of a hole is on it, and ends within `PLAN_MANUAL_MEET` are the same
  point. Lines meeting sharply on a hole are separate wires, which is how two wires go to one pad. A wire passing over a
  pad needs telling apart from that, so its corner is drawn rounded: where an arc meets another line or arc the point
  is a `Bend`, even over a hole, and connects to nothing. Sharp corners off any hole are corners too. Only the two ends
  of a wire join strips.
- Lines that share strips are taken together. If they join two nets they are all an error and left out, since keeping
  some would be a guess about which is wrong. Otherwise their strips go to that net at no cost (`manual_links`), and
  their holes stop being free. Without a net, the strips are kept from the plan under `MANUAL_NO_NET`.
- A manual wire may run under a part or end on a pin, because only the user knows whether it fits: `check_manual_wire()`
  reports it and `check()` does not reject it. `check_shared_pads()` warns about manual wires sharing a hole.
- The layer names `MCboard.ManualWires` and `MCboard.ManualChevrons` are only labels: the file identifies layers by
  their canonical names, which is what the planner reads.

### Working copy and board swap

- `--working-copy` takes the board's top silkscreen off `F.SilkS`, because KiCad shows or hides whole layers and
  footprints bring their silkscreen to `F.SilkS`. The `gr_text` goes to `User.2`. The connection marks are not moved,
  because bars or outlines cover the parts: `User.1` has instead one dotted line, `WORKING_CONNECTION_WIDTH` wide, from
  end to end of each strip and bus, whatever `--silk` says. Dots mark the connection more faintly than dashes, which at
  this width leave only two or so between two holes. `kicad_file.py` reads both layers back as `F.SilkS`, so a plan from
  a working copy shows those lines, solid because the reader keeps no line style, where a plan from the plain board
  shows its silkscreen; the PDF paints the drills after the silkscreen so that the lines stop at the holes. Layer colours belong to KiCad's theme, so the file does not
  set them. Its `.kicad_pro` sets the default line width of other layers to `MANUAL_LINE_WIDTH` (0.3), because KiCad's
  0.1 is hard to see over the pads.
- Every item the generator writes is a member of one locked group named `MCboard` (`Board.group()`, from the first uuid
  of each item, which is its own). A board can then be appended to a project's PCB and deleted again with one click,
  leaving the parts and the manual lines, which does away with a script to swap boards. Its members are written a few to
  a line, because KiCad cannot load a file with a line of about 1 MB, which a single line reaches near 100x100. Locked, because a click on a hole
  selects the whole group and a drag would move the board under the parts. The group changes neither the Gerbers nor
  the plan.
- KiCad's grid origin is set to hole A1, so that a grid dividing 2.54 falls on the holes.

### The button

- The marks go on `User.5`, named `MCboard.Plan` (`PLAN_MARKS_LAYER`), in a group named `MCboard plan`. Each run deletes
  the group and everything on that layer, so marks that lost their group, after a board swap or an undo, go too. A
  `User.5` under another name is refused rather than emptied.
- The button never changes the layer's visibility: set from a plugin, the Layers panel and the view fall out of step,
  and the next click hides the layer. A plugin also cannot make KiCad rebuild the Layers panel, so an added layer shows
  there only after the board is reopened; the report says so.
- It plans what the editor has, saved or not, through a temporary copy (`SaveBoard` with `aSkipSettings`, so the open
  project keeps its name).

## Rejected options

- **Thermal reliefs on bus pads:** not needed, a 1 mm track is little thermal mass.
- **Doubling the bus on the other side:** one side only, masked, is enough.
- **Holes larger than 1.0 across the grid:** a 1.2 drill needs a pad of at least 1.8, which leaves 0.74 between pads and
  squeezes the chevron.
- **`--silk lines` as default:** only about 0.4 mm dashes fit between pads.
- **`--silk outline` as default:** it does not fit with `--top smt`. Square, filleted or concentric corners, and bars
  without the lines, were rejected too.
- **A full top plane clear of the pads:** it fits but is of little use.
- **Islands at the centre cell of a group:** the third foot of a SOT-23 needs a vertical chevron, which is only at a
  group's edge.
- **More than one island row per group:** more places, not more parts, since the strips are shared.
- **Chevrons between two bus rows:** a closed one would short two supplies.

## Design rules

- The `.kicad_dru` files are the 2-layer, 1 oz variants from kicad-druid. They are generated upstream: do not hand-edit
  them other than for a local experiment.
- An unrecognised layer name makes KiCad reject the whole file, and headless DRC does not report it. After editing one,
  check Board Setup > Design Rules > Custom Rules for errors.
- Upstream values are not identical to the fab pages. For example, JLCPCB's PTH annular ring is 0.18 in kicad-druid and
  0.13 on JLCPCB's capabilities page (checked 2026-11-01). The design clears both.
- `lib_footprint_*` DRC warnings are silenced in the `.kicad_pro`, because the footprints are generated rather than taken
  from a library.

## Checking changes

Development uses KiCad 9.0.x. The generator writes KiCad 9 format (20241229), so that KiCad 9 opens it as its own file
rather than one to convert; KiCad 7 and 8 cannot open it.

```bash
python3 generate_protoboard.py --cols 8 --rows 13 -o /tmp/check/proto
kicad-cli pcb drc --severity-all -o /tmp/check/drc.rpt /tmp/check/proto.kicad_pcb
```

- A clean result has 0 violations and 0 unconnected pads. Check `--top smt` too.
- After changing anything drawn, compare the zip with `kicad-cli pcb export gerbers` and `export drill` on the same
  `.kicad_pcb`: same geometry, shifted by the origin (KiCad: x + 50, y - 50 - board height, by default). Also export and
  look at B.Cu, B.Mask and F.SilkS.
- KiCad's own mask Gerber is regions merged with `solder_mask_min_width`, so compare openings by region. With `--top smt`
  it has one region per island, 16 on a 6x13 (27 in all); fewer means a web was closed.
- To confirm that the fab rules are loaded, `--cols 4 --rows 7 --pad 1.3 --chev-width 0.6 --chev-depth 0.2` must give
  annular width violations on its 28 pads (0.15 ring against a 0.18 minimum).
- Rendering the exported SVG with ImageMagick can show a false notch in a silkscreen line near a `4`. The Gerber has no
  such notch; check the Gerber before treating one as real.
- After changing the pattern, generate other shapes too, such as `--pattern b+3+2+2b+5 --top smt`,
  `--pattern 3b+2+2+2 --silk outline`, `--pattern 5 --top smt`, `--pattern 2b` and `--pattern classic --top smt`: DRC
  must be clean, the rails of one bus must have nothing between them, and the islands must sit on a group's edge cells.

After changing the planner, run `plan_board.py` on the boards in `tests/t555/`:

- `placed.kicad_pcb`: TRIG cannot reach strip D5 to F5, whose holes all have a pin or are under a part.
- `placed_across_bus.kicad_pcb`: a complete plan, 15 chevrons and 3 wires.
- `imported.kicad_pcb`: all 6 parts not on the board.
- Manual lines, added as `gr_line`s to a copy of `placed_across_bus.kicad_pcb`:
  - `C4`-`D4` on `User.4` closes that chevron, and `D4`-`D8` on `User.3` is a wire from the same hole, both tagged
    `manual`, with the same 15 chevrons and 3 wires;
  - `D5`-`C5` on `User.3` is a wire with a warning, and `C4`-`C5` on `User.4` an error;
  - `D10`-`D4` with `D10`-`D8` are two wires sharing that pad;
  - `D4` to `C7` with a `gr_arc` at `D5` and another at `C5` is one wire `corners near D5, near C5` that closes no
    chevron and joins neither of those strips;
  - `F1`-`F10` warns that it runs under R2 and U1, and `C4`-`D1` is an error that joins DIS and GND.
- The generator's PDF must not change when only the plan's PDF does, and with `--working-copy` the plan's PDF from
  `b_working.kicad_pcb` must match that from `b.kicad_pcb`.

After changing the button, run `run_plan()` from `action.py` twice through `pcbnew` on a copy of a placed board, saving
and reloading in between: the marks must stay on `MCboard.Plan` (`User.5`), one set only, and a `User.5` under another
name must be refused.
