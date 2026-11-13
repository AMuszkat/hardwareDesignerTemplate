#!/usr/bin/env python3
"""D83-style pour gate as a COMPARISON: which rail vias reach their pour through copper at least W wide, before and
after a change.

    python3 tools/route/reachcmp.py --board board1 --before board1_module/board1_module.kicad_pcb.R2cross-bak \\
        --gate routing/reach_gate.json [--widths 0.1,0.175,0.3,0.5]

`pourreach.py` at the fab's minimum width answers "is any rail via cut off?".  It does not show a via that reached
through 0.3 mm of copper before a group and reaches through 0.12 mm after it -- a neck the group made, which the
layout rules judge by current.  This runs pourreach on the board as it is (after) and on a copy made from `--before`
(a backup file such as `<board>.kicad_pcb.<group>-bak`, or a board folder), for every layer / box / width of the
gate file, and prints only what changed: `REACH SAME` (exit 0) or `REACH CHANGED: n case(s) newly cut off` (exit 1,
each via named with the width it no longer reaches at).  A changed result is not a failure by itself: the report
states each new neck with its current.

The gate file is the board's (its numbers do not belong in a tool):
    {"layers": {"In4.Cu": ["VDD_SOC", "VDD_ARM"], "In5.Cu": ["+3V3", "+1V8"]},
     "boxes": [{"name": "U1 courtyard", "box": [21.705, 14.205, 36.295, 28.795], "sides": "W,N,E,S"},
               {"name": "from the PMIC", "box": [17.0, 7.5, 36.295, 28.795], "sides": "N", "layers": ["In4.Cu"]}],
     "widths": [0.1, 0.175, 0.3, 0.5]}
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KP = HERE.parents[1]
sys.path.insert(0, str(KP / "tools"))
import boardsel  # noqa: E402


def board_copy(before, board_dir):
    """a board folder outside the project made from a backup .kicad_pcb (pourreach takes a folder)"""
    before = Path(before)
    if before.is_dir():
        return str(before), None
    tmp = Path(tempfile.mkdtemp(prefix="reachcmp-")) / board_dir.name
    tmp.mkdir()
    shutil.copy(before, tmp / (board_dir.name + ".kicad_pcb"))
    for ext in (".kicad_pro", ".kicad_dru"):
        f = board_dir / (board_dir.name + ext)
        if f.exists():
            shutil.copy(f, tmp / f.name)
    return str(tmp), tmp.parent


def cut(board, layer, nets, box, sides, w, res):
    js = tempfile.mktemp(suffix=".json")
    r = subprocess.run([str(KP / "tools" / "kpy"), str(HERE / "pourreach.py"), "--board", board, "--layer", layer,
                        "--box", ",".join(str(v) for v in box), "--pad-nets", ",".join(nets), "--nfp",
                        "--min-width", str(w), "--sides", sides, "--res", str(res), "--json", js],
                       cwd=str(KP), capture_output=True, text=True)
    if not os.path.exists(js):
        sys.exit("pourreach failed:\n" + r.stdout[-800:] + r.stderr[-800:])
    rows = json.load(open(js)); os.remove(js)
    return {(v["net"], round(v["at"][0], 3), round(v["at"][1], 3)) for v in rows if not v["reaches"]}, len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", default=None)
    ap.add_argument("--before", required=True, help="a backup .kicad_pcb of this board, or a board folder")
    ap.add_argument("--gate", required=True, help="the board's gate file (relative to the board folder)")
    ap.add_argument("--widths", default=None)
    ap.add_argument("--res", type=float, default=0.025)
    a = ap.parse_args()
    bdir = Path(boardsel.resolve(a.board))
    gate = json.load(open(bdir / a.gate if not os.path.isabs(a.gate) else a.gate))
    widths = [float(v) for v in a.widths.split(",")] if a.widths else gate.get("widths", [0.1, 0.175, 0.3, 0.5])
    before, tmp = board_copy(a.before if os.path.isabs(a.before) else KP / a.before, bdir)
    changed = 0
    try:
        for layer, nets in gate["layers"].items():
            for bx in gate["boxes"]:
                if bx.get("layers") and layer not in bx["layers"]:
                    continue
                for w in widths:
                    b, n = cut(before, layer, nets, bx["box"], bx["sides"], w, a.res)
                    c, _ = cut(str(bdir), layer, nets, bx["box"], bx["sides"], w, a.res)
                    new, gone = sorted(c - b), sorted(b - c)
                    changed += len(new)
                    print(f"{layer} {bx['name']:18s} >= {w:5.3f} mm: cut off {len(b):2d} -> {len(c):2d} of {n}"
                          + (f"   NEWLY CUT {new}" if new else "") + (f"   now reach {gone}" if gone else ""))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    print("REACH", "SAME" if not changed else f"CHANGED: {changed} case(s) newly cut off")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
