# MCboard plan: KiCad plugin

Place the parts of a circuit on a generated MCboard in KiCad, press a button, and get the list of chevrons to close and
wires to add, with a printable drawing of both sides.

The plugin only reads the board, including any lines you draw on `User.3` and `User.4`, and draws marks on a layer of
its own, `MCboard.Plan` (`User.5`). It never changes copper, nets or footprints.

How to prepare the board, place the parts, and read the report and the drawing is in
[Part 2 of the user manual](../../docs/MANUAL.md#part-2-plan-a-circuit). This file only covers installing the button.

## Requirements

- KiCad 9.0.
- Python 3 to run the installer. It needs nothing beyond the standard library.
- A board made by `generate_protoboard.py`. The plugin does not work on any other board.

## Install

From the `protoboard_generator` folder:

```bash
python3 install_kicad_plugin.py
```

On Windows, use `python` or `py` instead of `python3` if that is the command you have.

It copies the plugin to a folder named `mcboard_plan` inside KiCad's plugin folder:

| System | Default plugin folder |
|---|---|
| Linux | `~/.local/share/kicad/9.0/scripting/plugins` |
| Windows, macOS | `~/Documents/KiCad/9.0/scripting/plugins` |

If yours is elsewhere, for example when Windows keeps Documents in OneDrive, look it up in the PCB editor with
**Tools > External Plugins > Open Plugin Directory** and pass it on:

```bash
python3 install_kicad_plugin.py --dir "C:\path\to\scripting\plugins"
```

`--version` changes the `9.0` in those paths.

Then, in the PCB editor, choose **Tools > External Plugins > Refresh Plugins**. The button appears as
**Tools > External Plugins > MCboard plan**, and also on the toolbar.

## Update

The plugin holds its own copy of the code, so changes in this folder do not reach KiCad by themselves. Run the
installer again, which replaces only its own folder. Then refresh the plugins, and restart KiCad if it still behaves as
before.

## Uninstall

Delete the `mcboard_plan` folder from the plugin folder and restart KiCad.

## The button does not appear

Check that `mcboard_plan` is in the folder shown by **Open Plugin Directory**, and use **Refresh Plugins**. The
installer's default may not be the folder your KiCad uses, in which case install with `--dir`. Other problems are in
[the manual's troubleshooting](../../docs/MANUAL.md#212-troubleshooting).

## Without the button

The same planner runs from the command line, with no KiCad needed: `python3 plan_board.py <board>.kicad_pcb`. See
[Run the plan](../../docs/MANUAL.md#25-run-the-plan).
