#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Copy the planner into KiCad's plugin folder as the 'MCboard plan' button.

The button is an IPC API plugin: KiCad runs it in a Python environment of its own, which it makes the first time
from requirements.txt. The plugin keeps its own copy of the code, so run this again after changing it. It only
replaces its own folder inside KiCad's plugin folder and, when installing to the default folder, removes the
earlier version of the button (a SWIG Action Plugin in scripting/plugins) if it finds it.

Usage:
  python3 install_kicad_plugin.py [--version 9.0] [--dir <plugin folder>]
"""

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "mcboard_plan"
PLUGIN_FILES = ("*.py", "plugin.json", "requirements.txt", "*.png")


def documents_folder(version):
    """Return KiCad's usual folder for this user's add-ons, for this system and KiCad version."""
    home = Path.home()
    if sys.platform.startswith("linux"):
        return home / ".local" / "share" / "kicad" / version
    return home / "Documents" / "KiCad" / version


def remove_old_button(version):
    """Remove the SWIG version of the button, which this script used to install, so that there are not two."""
    old = documents_folder(version) / "scripting" / "plugins" / NAME
    if (old / "action.py").is_file() and (old / "protoboard").is_dir():
        shutil.rmtree(old)
        print(f"Removed the earlier version of the button from {old}")


def main():
    """Copy the plugin and the protoboard package into KiCad's plugin folder."""
    ap = argparse.ArgumentParser(description="Install the 'MCboard plan' button in KiCad.")
    ap.add_argument("--version", default="9.0", help="KiCad version folder (9.0)")
    ap.add_argument("--dir", help="the plugin folder, if it is not the default one for that version")
    args = ap.parse_args()
    target = (Path(args.dir) if args.dir else documents_folder(args.version) / "plugins") / NAME
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    no_cache = shutil.ignore_patterns("__pycache__")
    for pattern in PLUGIN_FILES:
        for source in (HERE / "kicad_plugin").glob(pattern):
            shutil.copy2(source, target)
    shutil.copytree(HERE / "protoboard", target / "protoboard", ignore=no_cache)
    (target / "fonts").mkdir()
    for name in ("newstroke.txt", "NOTICE.txt"):
        shutil.copy2(HERE / "fonts" / name, target / "fonts")
    if not args.dir:  # with --dir, the KiCad installation in use is not known
        remove_old_button(args.version)
    print(f"Installed in {target}")
    print("KiCad must have its API turned on: Preferences > Plugins > Enable KiCad API. Then restart the PCB editor; "
          "the first time, KiCad takes a while to set up the button's Python environment before the button shows.")


if __name__ == "__main__":
    main()
