# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad action plugin: the planner as a button in the PCB editor. Installed by install_kicad_plugin.py."""

from .action import PlanAction

PlanAction().register()
