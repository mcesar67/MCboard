#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Say what to do on a generated board once the parts are placed on it in KiCad: which chevrons to
close and which wires to add. It reads the .kicad_pcb, and writes a report and <board>_plan.pdf.

Usage:
  python3 plan_board.py board.kicad_pcb

The 'protoboard' and 'fonts' folders must be next to this script. See kicad_plugin/ for the same thing
as a button in KiCad.
"""

import sys

from protoboard.plan_cli import main

if __name__ == "__main__":
    sys.exit(main())
