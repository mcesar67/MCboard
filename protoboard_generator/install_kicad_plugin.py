#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy the planner into KiCad's plugin folder as the 'MCboard plan' button.

The plugin keeps its own copy of the code, so run this again after changing it. It only replaces its
own folder inside KiCad's plugin folder.

Usage:
  python3 install_kicad_plugin.py [--version 9.0] [--dir <plugin folder>]

In the PCB editor, Tools > External Plugins > Open Plugin Directory shows the folder to give to --dir.
"""

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "mcboard_plan"


def default_folder(version):
    """Return KiCad's usual plugin folder for this system and KiCad version."""
    home = Path.home()
    if sys.platform.startswith("linux"):
        return home / ".local" / "share" / "kicad" / version / "scripting" / "plugins"
    return home / "Documents" / "KiCad" / version / "scripting" / "plugins"


def main():
    """Copy the plugin and the protoboard package into KiCad's plugin folder."""
    ap = argparse.ArgumentParser(description="Install the 'MCboard plan' button in KiCad.")
    ap.add_argument("--version", default="9.0", help="KiCad version folder (9.0)")
    ap.add_argument("--dir", help="the plugin folder, if it is not the default one for that version")
    args = ap.parse_args()
    target = (Path(args.dir) if args.dir else default_folder(args.version)) / NAME
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    no_cache = shutil.ignore_patterns("__pycache__")
    for source in (HERE / "kicad_plugin").glob("*.py"):
        shutil.copy2(source, target)
    shutil.copytree(HERE / "protoboard", target / "protoboard", ignore=no_cache)
    (target / "fonts").mkdir()
    for name in ("newstroke.txt", "NOTICE.txt"):
        shutil.copy2(HERE / "fonts" / name, target / "fonts")
    print(f"Installed in {target}")
    print("In the PCB editor: Tools > External Plugins > Refresh Plugins, then MCboard plan.")


if __name__ == "__main__":
    main()
