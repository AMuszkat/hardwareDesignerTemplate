#!/usr/bin/env python3
"""Re-width drawn copper after the fab's widths moved (a new stack-up): a route PLAN, not an edit.

    tools/kpy tools/route/rewidth.py --board board1 --class SE_50 --from 0.178 --to 0.128 \
        [--layers F.Cu,B.Cu] [--group RWse50] [--out routing/RW_se50.json]

Every track of the net class on those layers whose width is --from (within 0.001 mm) is ripped and
laid again on the same points at --to, so `route.py check` judges it like any group and `route.py
apply` records it in a manifest.  Necks and other widths are left alone.  Same points means the same
centre line: right for a single-ended line (its clearances only grow when it narrows), WRONG for a
coupled pair, whose gap is the rule -- narrowing a pair in place opens its gap by (from - to).  So a
differential class is refused unless --in-place-pair is given on purpose; a pair is re-drawn by the
script that drew it, with the new width and gap (board1, D111: 8 -> 10 layers).
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import boardsel  # noqa: E402
import pcbnew as pn  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("--class", dest="cls", required=True)
    ap.add_argument("--from", dest="w0", type=float, required=True)
    ap.add_argument("--to", dest="w1", type=float, required=True)
    ap.add_argument("--layers", default="F.Cu,B.Cu")
    ap.add_argument("--group")
    ap.add_argument("--out")
    ap.add_argument("--in-place-pair", action="store_true")
    a = ap.parse_args()
    board = Path(boardsel.resolve(a.board))
    pcb = next(board.glob("*.kicad_pcb"))
    b = pn.LoadBoard(str(pcb)); mm = pn.ToMM
    lays = set(a.layers.split(","))
    pairish = a.cls.upper().endswith(("DIFF", "_85", "_90", "_100")) or "PAIR" in a.cls.upper()
    if pairish and not a.in_place_pair:
        sys.exit(f"rewidth: {a.cls} looks like a differential class -- narrowing it in place opens its gap by "
                 f"{a.w0 - a.w1:.3f} mm; re-draw it with its own script, or pass --in-place-pair on purpose")
    rips, tracks, nets = [], [], set()
    for t in b.GetTracks():
        if t.Type() == pn.PCB_VIA_T or t.GetLayerName() not in lays:
            continue
        if t.GetNet().GetNetClassName() != a.cls or abs(mm(t.GetWidth()) - a.w0) > 0.001:
            continue
        s, e = t.GetStart(), t.GetEnd()
        pts = [[round(mm(s.x), 6), round(mm(s.y), 6)], [round(mm(e.x), 6), round(mm(e.y), 6)]]
        rips.append({"net": t.GetNetname(), "layer": t.GetLayerName(), "width": round(mm(t.GetWidth()), 4), "pts": pts})
        tracks.append({"net": t.GetNetname(), "layer": t.GetLayerName(), "width": a.w1, "pts": pts})
        nets.add(t.GetNetname())
    grp = a.group or f"RW{a.cls.replace('_', '').lower()}"
    out = Path(a.out) if a.out else board / "routing" / f"{grp}.json"
    if not out.is_absolute():
        out = board / out
    plan = {"group": grp,
            "why": f"re-width: {a.cls} on {', '.join(sorted(lays))} from {a.w0} to {a.w1} mm, same centre lines "
                   f"({len(tracks)} segments, {len(nets)} nets)",
            "rips": rips, "tracks": tracks, "vias": [], "zones": []}
    out.write_text(json.dumps(plan, indent=1))
    print(f"wrote {out}: {len(tracks)} segments of {len(nets)} nets, {a.w0} -> {a.w1} on {', '.join(sorted(lays))}")


if __name__ == "__main__":
    main()
