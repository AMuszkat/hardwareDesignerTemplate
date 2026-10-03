#!/usr/bin/env python3
"""Bring placed footprints' graphics back in line with the library, on the CLOSED board.

    tools/kpy tools/place/refresh.py --board board1_main J1 J2
    tools/kpy tools/place/refresh.py --board board1_main --layers CrtYd,SilkS J3

After `fp.py tidy` changes a library footprint's courtyard or silk, the board
still holds the old copy.  The documented refresh is Konnect's
`update_footprints_from_library`, which needs the board open in live KiCad with
its IPC API answering; when that path is closed (the same reason `addfp.py`
exists), this is the targeted
pcbnew edit the `placement` skill allows instead, and it does exactly one thing:
for each named footprint, every SHAPE on the chosen front-side layers is
replaced by the library's, transformed to where the part stands.  Pads, texts
(Reference, Value), fields, the 3D model and the placement are not touched, so
a land change is NOT this tool's job -- that is a re-sign and a re-place.

`place.py sync` must say "all identical" afterwards; nothing else counts.

⚠ The library footprints are kept alive until the board is saved.  Freeing
each one as soon as its shapes are copied crashes pcbnew on the second
footprint of a run, without a traceback.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("refs", nargs="+")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--layers", default="CrtYd", help="comma list of CrtYd, SilkS, Fab (front side)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    a.board = P.board_dir_arg(a.board)

    B = P.Board(a.board)
    pn, b = B.pn, B.b
    if P.locks(B.pcb) and not a.dry_run:
        P.die("KiCad has the board open; close it first", 3)
    want = {"CrtYd": pn.F_CrtYd, "SilkS": pn.F_SilkS, "Fab": pn.F_Fab}
    bad = [k for k in a.layers.split(",") if k not in want]
    if bad:
        P.die(f"--layers takes {', '.join(want)}; got {bad}")
    layers = [want[k] for k in a.layers.split(",")]
    pretties = sorted((Path(a.board) / "lib").glob("*.pretty"))
    if len(pretties) != 1:
        P.die(f"expected one lib/*.pretty, found {len(pretties)}")
    pretty = pretties[0]
    stem = pretty.name[:-len(".pretty")]
    keep = []                                  # library footprints live until SaveBoard
    for ref in a.refs:
        f = B.fps.get(ref)
        if f is None:
            P.die(f"{ref} is not on the board")
        if f.GetLayer() != pn.F_Cu:
            P.die(f"{ref} is on the back; this tool handles front-side parts only")
        nick, name = str(f.GetFPID().GetLibNickname()), str(f.GetFPID().GetLibItemName())
        if nick != stem:
            P.die(f"{ref} is {nick}:{name}, not a {stem} footprint")
        L = pn.FootprintLoad(str(pretty.resolve()), name)
        if L is None:
            P.die(f"{name} is not in {pretty.name}")
        keep.append(L)
        L.SetOrientation(f.GetOrientation())
        L.SetPosition(f.GetPosition())
        for lay in layers:
            old = [g for g in f.GraphicalItems() if g.GetLayer() == lay and g.GetClass() == "PCB_SHAPE"]
            new = [g for g in L.GraphicalItems() if g.GetLayer() == lay and g.GetClass() == "PCB_SHAPE"]
            print(f"{ref:5s} {name}: {b.GetLayerName(lay)} {len(old)} shape(s) -> {len(new)}")
            if a.dry_run:
                continue
            for g in old:
                f.Remove(g)
            keep.append(old)                   # Remove hands the shapes to Python: freeing
                                               # them before SaveBoard crashes pcbnew
            for g in new:
                f.Add(g.Duplicate())
    if a.dry_run:
        print("(dry run, nothing written)")
        return 0
    bak = Path(str(B.pcb) + ".refresh-bak")
    shutil.copy2(B.pcb, bak)
    with P.quiet_stderr():
        if not pn.SaveBoard(str(B.pcb), b):
            P.die("SaveBoard failed")
    print(f"saved (previous copy: {bak.name}); now run `place.py sync`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
