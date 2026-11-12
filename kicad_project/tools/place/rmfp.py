#!/usr/bin/env python3
"""Remove footprints the schematic no longer has, on the CLOSED board.

    tools/kpy tools/place/rmfp.py --board board1 TP2 R65 R67 [--dry-run]

`schupdate.py` refuses a reference that is on one side only, and KiCad's own Update PCB (with
"delete footprints with no symbols") needs the board open.  This does that one deletion, guarded:

  * a named reference still in the schematic's netlist is refused (this is not a way to unplace a part);
  * a footprint with copper ending on one of its pads is refused -- rip that copper first, through
    its group (`route.py rip`, or a plan's `rips` / `ripvias`), so the manifests stay true;
  * backup `<board>.rmfp-bak`, saved with pcbnew, read back.

Then `schupdate.py` (nets, links) and `place.py drc`: schematic parity 0.
"""
import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402
import schupdate  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("refs", nargs="+")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)
    B = P.Board(a.board)
    pn, b = B.pn, B.b
    if P.locks(B.pcb) and not a.dry_run:
        P.die("KiCad has the board open; close it first", 3)
    comps = schematic_refs(B.pcb)
    gone = []
    for ref in a.refs:
        f = B.fps.get(ref)
        if f is None:
            P.die(f"{ref} is not on the board")
        if ref in comps:
            P.die(f"{ref} is still in the schematic: remove it there first (plan, draw)")
        for p in f.Pads():
            for t in b.GetTracks():
                if t.GetNetCode() != p.GetNetCode() or p.GetNetCode() == 0:
                    continue
                ends = [t.GetPosition()] if t.Type() == pn.PCB_VIA_T else [t.GetStart(), t.GetEnd()]
                if any(p.HitTest(e) for e in ends) and (t.Type() == pn.PCB_VIA_T or p.IsOnLayer(t.GetLayer())):
                    P.die(f"{ref} pad {p.GetNumber()} ({p.GetNetname()}) has copper on it at "
                          f"({pn.ToMM(ends[0].x):.3f}, {pn.ToMM(ends[0].y):.3f}): rip it through its group first")
        gone.append(f)
        print(f"{ref:6s} {f.GetFPID().GetUniStringLibId()} at ({pn.ToMM(f.GetPosition().x):.3f}, {pn.ToMM(f.GetPosition().y):.3f}) "
              f"{'B' if f.IsFlipped() else 'F'}: removed")
    if a.dry_run:
        print("(dry run, nothing written)")
        return 0
    shutil.copy2(B.pcb, Path(str(B.pcb) + ".rmfp-bak"))
    for f in gone:
        b.Remove(f)
    with P.quiet_stderr():
        if not pn.SaveBoard(str(B.pcb), b):
            P.die("SaveBoard failed")
    left = {f.GetReference() for f in pn.LoadBoard(str(B.pcb)).GetFootprints()}
    bad = [r for r in a.refs if r in left]
    if bad:
        P.die(f"read back: still on the board: {bad}")
    print(f"saved, read back: {len(gone)} footprint(s) gone (previous copy: {Path(B.pcb).name}.rmfp-bak); "
          "now schupdate.py, then place.py drc")
    return 0


def schematic_refs(pcb):
    """The references in the schematic's own netlist (schupdate's reader)."""
    comps = schupdate.schematic(pcb)
    comps = comps[0] if isinstance(comps, tuple) else comps
    return set(comps)


if __name__ == "__main__":
    sys.exit(main())
