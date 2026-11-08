# Third-party material

Copyright (C) 2026 [mcesar67](https://github.com/mcesar67). The project as a whole is licensed under
GPL-3.0-or-later (see `LICENSE`). It includes:

| Path | What | Origin | Licence |
|---|---|---|---|
| `protoboard_generator/fonts/newstroke.txt` | KiCad's newstroke stroke font, converted to a text table | Vladimir Uryvaev and the KiCad developers, from KiCad's `common/newstroke_font.cpp`, branch 9.0 | Stated in different ways upstream, see below |
| `protoboard_generator/rules/*.kicad_dru` | Fab design rules, 2 layers, 1 oz | [kicad-druid](https://github.com/Cimos/kicad-druid) | MIT, see `rules/LICENSE-kicad-druid` and `rules/ORIGIN.txt` |

## The font

KiCad's `tools/newstroke/README.txt` says the font is released under CC0. The header of the source file says
GPL-2.0-or-later for the file, MIT for the code used to derive the CJK glyphs, and SIL OFL 1.1 for Source Han Sans,
the font those glyphs come from.

This project is GPL-3.0-or-later partly so that it can be distributed under any of those readings.
`protoboard_generator/fonts/NOTICE.txt` gives the exact commit and reproduces that header in full. It is not legal
advice; if the licence of the font matters for your use, read the upstream statements yourself.

## Boards you generate

The Gerbers, drill files and KiCad files the generator writes draw text with this font. Whether its licence or this
project's applies to a board you generate is not something this project has looked into.
