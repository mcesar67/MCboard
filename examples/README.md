# Examples

Boards generated as they are, ready to open in KiCad or to order. Each folder has the KiCad project, the zip for the fab
and the A4 drawing. They were made with `--fab generic`, the default, which satisfies both JLCPCB and PCBWay.

| Folder | Board | Size |
|---|---|---|
| `classic_64x20/` | A classic breadboard: two rails at each edge, two strips of 5 per column, no mounting holes | 170.02 x 58.26 mm |
| `default_30x19/` | The default pattern, a hole in each corner, `--silk outline` | 81.66 x 62.72 mm |
| `smt_30x19/` | The same with `--top smt`, with the default `--silk bars` | 81.66 x 62.72 mm |

To make them again, from the `protoboard_generator` folder:

```bash
python3 generate_protoboard.py --cols 64 --rows 20 --pattern classic --no-mount-holes --margin-plain 5 -o ../examples/classic_64x20/
python3 generate_protoboard.py --cols 30 --rows 19 --mount-x 2 --mount-y 0 --silk outline -o ../examples/default_30x19/
python3 generate_protoboard.py --cols 30 --rows 19 --mount-x 2 --mount-y 0 --top smt -o ../examples/smt_30x19/
```

`--margin-plain 5` makes room for the name, which does not fit in the 4 mm margin of a board with no mounting holes.
`--mount-x 2 --mount-y 0` puts two holes on the top and bottom sides and none on the others, so they end up in the
corners. The options are explained in the [user manual](../docs/MANUAL.md).
