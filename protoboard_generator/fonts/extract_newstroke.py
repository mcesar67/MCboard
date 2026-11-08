#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild newstroke.txt from KiCad's common/newstroke_font.cpp.

Usage: python3 extract_newstroke.py <path to newstroke_font.cpp> [source description]
"""

import re
import sys
from pathlib import Path


def glyphs(source):
    """The C strings of the newstroke_font[] array, one per code point from U+0020."""
    body = source[source.index("newstroke_font[] ="):source.index("newstroke_font_bufsize")]
    body = body[body.index("{") + 1:body.rindex("}")]
    entries = []
    current = None
    for token in re.findall(r'/\*.*?\*/|//[^\n]*|"(?:[^"\\]|\\.)*"|,', body, re.S):
        if token == ",":
            entries.append(current)
            current = None
        elif token.startswith('"'):
            current = (current or "") + re.sub(r"\\(.)", r"\1", token[1:-1])
    if current is not None:
        entries.append(current)
    return entries


def main():
    """Write newstroke.txt from the source file named on the command line."""
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    entries = glyphs(Path(sys.argv[1]).read_text(encoding="latin-1"))
    origin = sys.argv[2] if len(sys.argv) > 2 else Path(sys.argv[1]).name
    target = Path(__file__).resolve().parent / "newstroke.txt"
    target.write_text(f"# KiCad newstroke font, from {origin}. See NOTICE.txt.\n" + "\n".join(entries) + "\n",
                      encoding="ascii", newline="\n")
    print(f"{target}: {len(entries)} entries")


if __name__ == "__main__":
    main()
