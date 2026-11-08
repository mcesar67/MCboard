# SPDX-License-Identifier: GPL-3.0-or-later
"""Circle geometry. KiCad defines an arc by three points on it, Gerber and the PDF by its centre."""


def circumcentre(a, b, c):
    """Centre of the circle through three points."""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    sq = [p[0] ** 2 + p[1] ** 2 for p in (a, b, c)]
    return ((sq[0] * (b[1] - c[1]) + sq[1] * (c[1] - a[1]) + sq[2] * (a[1] - b[1])) / d,
            (sq[0] * (c[0] - b[0]) + sq[1] * (a[0] - c[0]) + sq[2] * (b[0] - a[0])) / d)


def is_counterclockwise(a, b, c):
    """Whether going from a to c through b turns counterclockwise, with y up."""
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) > 0
