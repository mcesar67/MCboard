# MCboard plan: KiCad plugin

Place the parts of a circuit on a generated MCboard in KiCad, press a button, and get the list of chevrons to close and
wires to add, with a printable drawing of both sides.

It has four buttons, in this order on the toolbar:

<img src="nets_48.png" width="32" alt="MCboard nets"> <img src="icon_48.png" width="32" alt="MCboard plan">
<img src="apply_48.png" width="32" alt="MCboard apply"> <img src="clear_48.png" width="32" alt="MCboard clear">

- **MCboard nets** is for wiring by hand. After you draw chevrons and wires as tracks on the `MCboard.Chevrons` and
  `MCboard.Wires<n>` layers, it updates the nets: each strip takes the net of the pins on it, and the strips your
  tracks join share a net. The ratsnest then shows only the connections still missing. It does not plan, so it is
  instant. The tracks an earlier apply added stay. One **Edit > Undo** takes it back.
- **MCboard plan** only reads the board, including any chevrons and wires you route yourself as tracks on the
  `MCboard.Chevrons` and `MCboard.Wires<n>` layers, and draws marks on a layer of its own, `MCboard.Plan` (`User.5`).
  It never changes copper, nets or footprints.
- **MCboard apply** puts the plan into the working copy, so that KiCad's ratsnest follows it: it sets the nets of the
  board's pads and tracks and of your own tracks, and adds the other chevrons to close as tracks on
  `MCboard.Chevrons` and the other wires as tracks on `MCboard.Wires<n>`. The tracks it adds are locked; pressing it
  again replaces them, and unlocking one keeps it as yours. One **Edit > Undo** takes a press back.
- **MCboard clear** takes the board back to before plan and apply: it removes the tracks apply added and the plan's
  marks, and gives the strips their own nets again. Your own tracks and the parts stay. One **Edit > Undo** takes it
  back.

How to prepare the board, place the parts, and read the report and the drawing is in
[Part 2 of the user manual](../../docs/MANUAL.md#part-2-plan-a-circuit). This file only covers installing the buttons.

## Requirements

- KiCad 9.0 or later, with its API turned on: **Preferences > Plugins > Enable KiCad API**.
- On Linux, a Python 3 in the `PATH` that can make virtual environments (on Debian and Ubuntu, the `python3-venv`
  package). On Windows and macOS KiCad brings its own Python.
- An internet connection the first time, for KiCad to fetch the `kicad-python` package.
- Python 3 to run the installer. It needs nothing beyond the standard library.
- A working copy made by `generate_protoboard.py --working-copy`, or another generated board with a `User.5` layer
  named `MCboard.Plan`. The plugin does not work on any other board. Your own tracks and the apply button also need
  the inner copper layers `MCboard.Wires<n>` and `MCboard.Chevrons`, which only working copies have. They are taken
  by place, so their names do not matter.
- The buttons have been tried on KiCad 9 and 10, on Linux and Windows 11, but not on macOS.

## Install

From the `protoboard_generator` folder:

```bash
python3 install_kicad_plugin.py
```

On Windows, use `python` or `py` instead of `python3` if that is the command you have.

It copies the plugin to a folder named `mcboard_plan` inside KiCad's plugin folder:

| System | Default plugin folder |
|---|---|
| Linux | `~/.local/share/kicad/<version>/plugins` |
| Windows, macOS | `~/Documents/KiCad/<version>/plugins` |

If yours is elsewhere, for example when Windows keeps Documents in OneDrive, pass the folder on:

```bash
python3 install_kicad_plugin.py --dir "C:\path\to\KiCad\9.0\plugins"
```

`<version>` is the newest KiCad version with a folder there, such as `10.0`. To install for another, add
`--version 9.0`.

Then restart the PCB editor. The first time, KiCad makes a Python environment for the plugin and installs
`kicad-python` in it, which takes a minute; the buttons appear on the toolbar as **MCboard nets**, **MCboard plan**,
**MCboard apply** and **MCboard clear** when that is done.

The button used to be a SWIG Action Plugin in `scripting/plugins`. When installing to the default folder, the installer
removes that earlier version, so that there are not two buttons.

## Update

The plugin holds its own copy of the code, so changes in this folder do not reach KiCad by themselves. Run the
installer again, which replaces only its own folder, and restart the PCB editor.

## Uninstall

Delete the `mcboard_plan` folder from the plugin folder and restart KiCad.

## The buttons do not appear

- Check that the API is on, in **Preferences > Plugins**, and that `mcboard_plan` is in the plugin folder of the KiCad
  version you run. Then restart KiCad.
- The Python environment may have failed to build, for example with no internet or, on Linux, no `python3-venv`. In
  the PCB editor's preferences, in the list of plugin buttons, right-click it and choose **Recreate Plugin
  Environment**.
- Other problems are in [the manual's troubleshooting](../../docs/MANUAL.md#212-troubleshooting).

## Without the button

The same planner runs from the command line, with no KiCad needed: `python3 plan_board.py <board>.kicad_pcb`. See
[Run the plan](../../docs/MANUAL.md#25-run-the-plan).
