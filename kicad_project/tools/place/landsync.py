#!/usr/bin/env python3
"""Copy a re-signed LAND from the library onto placed footprints, on the CLOSED board.

    tools/kpy tools/place/landsync.py --board board1 U1 U300 [--dry-run]

After a land change in the library (`fp.py sign` / `adopt` -- pad sizes, the pads'
own solder-mask and paste margins, shape, pad property), the board still holds the
old pads.  The documented path is Konnect's `update_footprints_from_library` (KiCad
open, IPC); it also strips pad properties (README, schupdate).  This does the land
and nothing else: for each named footprint, every board pad takes from the library
pad of the same number its shape, size, solder-mask margin, paste margin and
property.  Refused unless both sides have the same pad numbers at the same
footprint-local positions (a moved ball is a re-place, not this).  Nets, positions,
graphics, fields, the model and placement are untouched.

Backup `<board>.landsync-bak`, saved with pcbnew and read back; then
`place.py sync` (identical) and `place.py drc`.

Why a tool: refresh.py copies graphics only, by design, and a library footprint
re-placed by script loses its nets and its schematic link.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import place as P  # noqa: E402


def land(p, pn):
    sz = p.GetSize(pn.F_Cu)
    return (p.GetShape(), sz.x, sz.y, p.GetLocalSolderMaskMargin(), p.GetLocalSolderPasteMargin(),
            p.GetProperty(), p.GetThermalSpokeAngle().AsDegrees())


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
    pretties = sorted((Path(a.board) / "lib").glob("*.pretty"))
    if len(pretties) != 1:
        P.die(f"expected one lib/*.pretty, found {len(pretties)}")
    pretty, stem = pretties[0], pretties[0].name[:-len(".pretty")]
    io = pn.PCB_IO_KICAD_SEXPR()               # one plugin (fp README: FootprintLoad per call can fail)
    keep, want = [], {}
    for ref in a.refs:
        f = B.fps.get(ref)
        if f is None:
            P.die(f"{ref} is not on the board")
        nick, name = str(f.GetFPID().GetLibNickname()), str(f.GetFPID().GetLibItemName())
        if nick != stem:
            P.die(f"{ref} is {nick}:{name}, not a {stem} footprint")
        L = io.FootprintLoad(str(pretty.resolve()), name)
        if L is None:
            P.die(f"{name} is not in {pretty.name}")
        keep.append(L)
        lib = {p.GetNumber(): p for p in L.Pads() if p.GetNumber()}
        brd = {p.GetNumber(): p for p in f.Pads() if p.GetNumber()}
        if set(lib) != set(brd):
            P.die(f"{ref}: pad numbers differ from the library ({len(brd)} vs {len(lib)}) -- re-place, not this")
        moved = [n for n in lib if (lib[n].GetFPRelativePosition().x, lib[n].GetFPRelativePosition().y)
                 != (brd[n].GetFPRelativePosition().x, brd[n].GetFPRelativePosition().y)]
        if moved:
            P.die(f"{ref}: pads {moved[:5]} sit elsewhere in the library -- re-place, not this")
        n = 0
        for k, p in brd.items():
            q = lib[k]
            if land(p, pn) == land(q, pn):
                continue
            n += 1
            if not a.dry_run:
                p.SetShape(q.GetShape())
                p.SetSize(pn.F_Cu, q.GetSize(pn.F_Cu))
                p.SetLocalSolderMaskMargin(q.GetLocalSolderMaskMargin())
                p.SetLocalSolderPasteMargin(q.GetLocalSolderPasteMargin())
                p.SetProperty(q.GetProperty())
                # a pad's default thermal-spoke angle follows its shape (45 for a circle, 90 otherwise);
                # SetShape keeps the old one, which KiCad then saves as an override the library lacks
                p.SetThermalSpokeAngle(q.GetThermalSpokeAngle())
        want[ref] = {k: land(q, pn) for k, q in lib.items()}
        print(f"{ref:5s} {name}: {n} of {len(brd)} pads take the library's land")
    if a.dry_run:
        print("(dry run, nothing written)")
        return 0
    bak = Path(str(B.pcb) + ".landsync-bak")
    shutil.copy2(B.pcb, bak)
    with P.quiet_stderr():
        if not pn.SaveBoard(str(B.pcb), b):
            P.die("SaveBoard failed", 4)
        back = pn.LoadBoard(str(B.pcb))
    bad = [f.GetReference() for f in back.GetFootprints() if f.GetReference() in want
           and any(land(p, pn) != want[f.GetReference()][p.GetNumber()] for p in f.Pads() if p.GetNumber())]
    if bad:
        P.die(f"read back: the land did not take on {bad} -- restore {bak}", 4)
    print(f"saved; read back OK (backup {bak.name}). Now: place.py sync, then place.py drc")
    sys.stdout.flush()
    os._exit(0)                                # the library copies are owned twice; GC segfaults


if __name__ == "__main__":
    sys.exit(main())
