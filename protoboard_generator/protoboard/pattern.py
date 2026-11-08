# SPDX-License-Identifier: GPL-3.0-or-later
"""The row pattern: which strip each row belongs to, and how rows are named.

--pattern lists the rows of the board from the top, as elements joined by +, and repeats down the board. A number
is a group of that many rows: each column of it is one strip, joined by a vertical track. b is a bus row, and 2b
two of them, and so on. s is a space: a row of the grid with nothing on it, no hole, chevron or track, and 2s two.
"b+3+2+2b+5" is a bus row, a group of 3, one of 2, two bus rows and a group of 5, then again from the start. The
default, "b+2+3", is the 6-row period of the original board. "2b+2s+5+2s+5+2s", or "classic", is a classic
breadboard: two rails, then two strips of 5 three pitches apart, 7.62 mm, which a DIP straddles.

A strip is what one net joins: each bus row on its own, or one column of a group. Neighbouring strips have a
chevron between them, except two bus rows, which are separate rails with nothing between them. Nothing crosses a
space. A pattern may have no bus at all, but not only spaces.

Rows are counted two ways. --rows and the drawing count every row of the grid, spaces too. Letters and net names
count only the rows with holes, as on a breadboard, and so does the planner, which only sees the holes."""

import re

PATTERN_EXAMPLE = "b+2+3, b+3+2+2b+5, 2b+2s+5+2s+5+2s or 5"
ALIASES = {"classic": "2b+2s+5+2s+5+2s"}  # names for whole patterns
ELEMENT = re.compile(r"(\d*)([bs]?)")


def parse_pattern(text):
    """--pattern as a list of (kind, rows), kind being "b" for bus rows, "s" for spaces and "g" for a group;
    raise ValueError if it is not valid."""
    text = text.replace(" ", "").lower()
    text = ALIASES.get(text, text)
    elements = []
    for part in text.split("+"):
        match = ELEMENT.fullmatch(part)
        if not part or not match or (not match.group(1) and not match.group(2)):
            elements = None
            break
        count = int(match.group(1) or 1)
        if count < 1:
            elements = None
            break
        elements.append((match.group(2) or "g", count))
    if not elements:
        raise ValueError(f"use group sizes, bus rows (b, 2b...) and spaces (s, 2s...) joined by +, such as "
                         f"{PATTERN_EXAMPLE}")
    if all(kind == "s" for kind, _ in elements):
        raise ValueError("the pattern has only spaces: it needs a group or a bus")
    return elements


def pattern_option(text):
    """--pattern written the one way the files and messages use: lower case, no spaces, 1b as b and 1s as s."""
    return "+".join(str(rows) if kind == "g" else (kind if rows == 1 else f"{rows}{kind}")
                    for kind, rows in parse_pattern(text))


def has_bus(pattern):
    """Whether the pattern has any bus row."""
    return any(kind == "b" for kind, _ in parse_pattern(pattern))


def ideal_rows(rows, pattern):
    """The numbers of rows, nearest below and above `rows`, with which the board ends on a whole bus, or on a
    whole group if the pattern has no bus. The lower one is None when there is none."""
    elements = parse_pattern(pattern)
    target = "b" if has_bus(pattern) else "g"
    only_buses = all(kind == "b" for kind, _ in elements)
    total, n, lower = 0, 0, None
    while True:
        kind, count = elements[n % len(elements)]
        total += count
        n += 1
        follows = elements[n % len(elements)][0]
        # A group is whole by itself; a bus ends where something else follows it, or at every element of a
        # pattern of buses only.
        if kind == target and (target == "g" or follows != "b" or only_buses):
            if total > rows:
                return lower, total
            lower = total


class RowPattern:
    """Which strip each row belongs to, worked out once for the whole board. Rows are those of the grid.

    The pattern is followed a whole repetition past the last row, so that a group cut short by the edge of the
    board still knows where it would have ended, and what comes after it."""

    def __init__(self, rows, pattern="b+2+3"):
        self.bus = []    # per row: whether it is a bus row
        self.space = []  # per row: whether it is a space, with no holes
        self.start = []  # per row: first row of its strip; a bus row or a space is one of its own
        self.end = []    # per row: last row of its strip, where the pattern would end it
        elements = parse_pattern(pattern)
        length = sum(count for _, count in elements)
        n = 0
        while len(self.bus) < rows + length + 1:
            kind, count = elements[n % len(elements)]
            if kind == "g":
                self.add_group(count)
            else:
                self.add_single(count, kind == "s")
            n += 1
        self.rows = rows
        self.hole_rows = [r for r in range(rows) if not self.space[r]]
        self.hole_index = {r: i for i, r in enumerate(self.hole_rows)}

    def add_single(self, count, space):
        """Append `count` bus rows, each a strip of its own, or spaces."""
        for _ in range(count):
            row = len(self.bus)
            self.bus.append(not space)
            self.space.append(space)
            self.start.append(row)
            self.end.append(row)

    def add_group(self, size):
        """Append a group of `size` rows, one strip per column."""
        first = len(self.bus)
        self.bus += [False] * size
        self.space += [False] * size
        self.start += [first] * size
        self.end += [first + size - 1] * size

    def is_bus(self, r):
        """Whether row r is a bus row."""
        return self.bus[r]

    def is_space(self, r):
        """Whether row r is a space, with no holes."""
        return self.space[r]

    def joined(self, r):
        """Whether rows r and r + 1 are one strip, joined by a vertical track in every column."""
        return self.start[r] == self.start[r + 1]

    def has_chevron(self, r):
        """Whether there is a chevron between rows r and r + 1 in every column: between two strips, unless both
        are bus rows, which are rails apart, or either is a space."""
        return (not self.joined(r) and not (self.bus[r] and self.bus[r + 1])
                and not self.space[r] and not self.space[r + 1])

    def label(self, r):
        """The letter of a row with holes: spaces are skipped, so the letters run on."""
        return row_label(self.hole_index[r])

    def island_rows(self):
        """The rows r, on the board, whose cells to r + 1 have islands with --top smt: one row of cells per group
        of 2 rows or more. More than one would add places, not parts, since a group joins each column into one
        strip: a second part there would share the first one's strips.

        A SOT-23 there has its third foot on a vertical chevron, and those are only at the edges of a group, so
        the cell is the group's first or last. It is the one next to the bus when the group touches only one bus,
        and otherwise the first. An edge with no chevron, beside a space or an edge of the board, is no use, so
        the cell moves to the other edge, and a group with neither has none. For b+2+3 and b+3+2 this gives rows
        1 and 4 of each period."""
        found = []
        for r in range(self.rows - 1):
            if self.bus[r] or self.space[r] or self.start[r] != r or self.end[r] == r:
                continue  # not the first row of a group of 2 or more
            last = self.end[r]
            above = r > 0 and self.bus[r - 1]
            below = self.bus[last + 1]
            top_chevron = r > 0 and not self.space[r - 1]
            bottom_chevron = last + 1 < self.rows and not self.space[last + 1]
            if not top_chevron and not bottom_chevron:
                continue
            cell = last - 1 if below and not above else r
            if cell == r and not top_chevron:
                cell = last - 1
            elif cell == last - 1 and not bottom_chevron:
                cell = r
            if cell + 1 < self.rows:
                found.append(cell)
        return found


def row_label(r):
    """Spreadsheet-style row label: 0 -> A, 25 -> Z, 26 -> AA, ..."""
    label = ""
    r += 1
    while r > 0:
        r, m = divmod(r - 1, 26)
        label = chr(ord("A") + m) + label
    return label
