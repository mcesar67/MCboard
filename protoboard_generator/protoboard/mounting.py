# SPDX-License-Identifier: GPL-3.0-or-later
"""Mounting hole strips: pure functions that decide spacing and hole count.

The holes form four strips, one along each side. A strip has three values, the spacing, the number of holes
and the alignment. The user can set any of them per side, and what is left is chosen here: corner_strip() for
the two longer sides, side_strip() for the two others, and centred.
"""

import re
from dataclasses import dataclass

from .constants import MOUNT_GAP_TOLERANCE, MOUNT_MAX_INSET, MOUNT_MIN_FIXED, MOUNT_MIN_SPACING, MOUNT_MIN_WEB

SIDE_FORMAT = re.compile(r"(\d+)?(?:x(\d+(?:\.\d+)?))?(?::(\w+))?")
SIDE_ALIGNS = {"top": ("centre", "left", "right"), "bottom": ("centre", "left", "right"),
               "left": ("centre", "top", "bottom"), "right": ("centre", "top", "bottom")}


@dataclass(frozen=True)
class SideSpec:
    """What the user asked for on one side. What is None is chosen automatically."""
    holes: int = None
    spacing: float = None
    align: str = None

    def chooses(self):
        """Whether this replaces the automatic choice of holes, not only moves it."""
        return self.holes is not None or self.spacing is not None

    def exact(self):
        """Whether the number and spacing of the holes are both fixed, which leaves nothing to give way."""
        return self.holes is not None and self.spacing is not None

    def span(self):
        """The length of line this insists on, which makes the board grow if needed."""
        return (self.holes - 1) * self.spacing if self.holes and self.spacing else 0



def corner_strip(length, max_spacing):
    """(spacing, spans) of a strip that should reach both ends of a side.

    Uses the fewest holes that stop within MOUNT_MAX_INSET of each end. A spacing
    that is not a multiple of 10 mm counts as one extra hole, so it is only chosen
    when it saves at least two.
    """
    # Try everything and let min() choose. The tuples sort by: effective hole count, then whether
    # the spacing is a multiple of 10, then how far the strip stops from the corner.
    candidates = []
    for spacing in range(MOUNT_MIN_SPACING, max_spacing + 1, 5):
        for spans in range(1, int(length // spacing) + 1):
            inset = (length - spans * spacing) / 2
            if inset <= MOUNT_MAX_INSET:
                odd = spacing % 10 != 0
                candidates.append((spans + odd, odd, inset, spacing, spans))
    if not candidates:
        return 0, 0  # too short for two holes: a single centred one
    return min(candidates)[3:]


def side_strip(gap, max_spacing):
    """(spacing, holes) of a strip filling the gap between two opposite corner strips."""
    # Add holes one at a time until the stretch left at each end of the strip is not too large.
    limit = max_spacing * MOUNT_GAP_TOLERANCE
    if gap <= limit:
        return 0, 0
    holes = 1
    while True:
        spacing = 0
        if holes > 1:
            spacing = round(gap / (holes + 1) / 10) * 10
            spacing = max(MOUNT_MIN_SPACING, min(spacing, max_spacing // 10 * 10))
        if (gap - (holes - 1) * spacing) / 2 <= limit:
            return spacing, holes
        holes += 1


def placed(length, spacing, holes, align="centre"):
    """Positions along a line of holes of the given length. Left or top puts the first hole at the start of
    the line, right or bottom puts the last one at its end."""
    span = (holes - 1) * spacing
    start = {"left": 0, "top": 0, "right": length - span, "bottom": length - span}.get(align, (length - span) / 2)
    return [start + i * spacing for i in range(holes)]


def mount_min_distance(drill):
    """Closest that two holes may be when the user sets the spacing or two strips meet, in mm."""
    return max(MOUNT_MIN_FIXED, drill + MOUNT_MIN_WEB)


def holes_that_fit(length, spacing):
    """How many holes fit in a line at a given spacing: at least one."""
    return max(int((length + 1e-6) // spacing) + 1, 1)


def widest_spacing(length, holes, least):
    """The largest multiple of 5 mm at which this many holes fit in a line, or None."""
    spacing = int((length + 1e-6) / (holes - 1) // 5) * 5
    return spacing if spacing >= least else None


def parse_side(text, side):
    """A SideSpec from 'holes x spacing : align', where every part is optional. Raises ValueError."""
    match = SIDE_FORMAT.fullmatch(text)
    if not match or not any(match.groups()):
        raise ValueError(f"'{text}' is not of the form 3, x40, 3x40 or any of these followed by :align")
    holes, spacing, align = match.groups()
    holes = None if holes is None else int(holes)
    spacing = None if spacing is None else float(spacing)
    if holes == 0 and (spacing or align):
        raise ValueError("0 holes take no spacing or alignment")
    if align and align not in SIDE_ALIGNS[side]:
        raise ValueError(f"'{align}' is not one of {', '.join(SIDE_ALIGNS[side])} for this side")
    return SideSpec(holes, spacing, align)
