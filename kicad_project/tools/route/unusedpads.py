#!/usr/bin/env python3
"""unusedpads.py -- drop the UNCONNECTED inner pads of the vias in a box (KiCad's "remove unconnected layers", start and end
kept: UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END), for the vias whose net matches a pattern.

    tools/kpy tools/route/unusedpads.py --board boardN|DIR --box X0 Y0 X1 Y1 --nets REGEX [--file PCB] [--dry | --apply]

--board names the board (or a scratch folder outside kicad_project/ that holds one .kicad_pcb); --file a .kicad_pcb to use
instead of the folder's.  --nets is matched (re.fullmatch) against the net's short name (the part after the last '/'),
e.g. 'GND|DRAM_.*'.  A via is in the box when its centre is (edges included).  --dry (the default) prints what --apply would
do and writes nothing; --apply sets the mode, saves, reloads the file in a fresh LoadBoard and reads every changed via back
(its mode, and the inner layers it is no longer flashed on).

Each via changed is printed with the inner layers it now LACKS (pcbnew's FlashLayer after the change: a layer a track of its
net or a pour of its net connects keeps its pad).  The zones are REFILLED after the change (and the refilled board is what
--apply saves): FlashLayer sees a pour only through the zone filler's per-layer override, which is not saved in the file, so
without a refill a GND stitch via reads as losing its plane pads too (TRAP below).  A via already in the mode is listed as
`kept` and not counted as changed.

Exit 0: done (dry or applied, read back equal); 1: no via in the box matched the nets; 2: an error (bad args, KiCad running
on the real board, the read-back differs).  Refuses the REAL board (a board folder of kicad_project/) while `pgrep -x kicad`
finds KiCad: it would write under an open editor that later saves its own copy over this one.

TRAP (board1 R1, D148, 2026-11-27): a DRAM via in the U1-U500 strip kept its inner pads; on In5 / In7, where it has no track,
its 0.4 pad took 0.4032 of a lane from its centre where its bare hole takes 0.2778 (GND) / 0.3032 (DQ).  The fab removes
non-functional pads only where the board says so (14_fab "Non-functional pads"); this is how the board says so.
TRAP (same day): read after LoadBoard without a refill, FlashLayer said a GND via in the GND planes lacked In1/In6/In8 too --
the planes connect it only through ZONE_FILLER's override; refill, then read.
TRAP (same day): pcbnew.SaveBoard of a .kicad_pcb with no .kicad_pro beside it WRITES a default .kicad_pro (KiCad's rules,
not the fab's): a scratch base built that way DRC'd 1335 errors (every via under a 0.5 minimum) where the board has 10.
--apply refuses a board whose .kicad_pro is missing.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import boardsel  # noqa: E402

KP = HERE.parent.parent


def die(msg, code=2):
    print("unusedpads: " + msg, file=sys.stderr)
    sys.exit(code)


def mm(v):
    return v / 1e6


def inner_layers(pcbnew, b):
    return [L for L in b.GetEnabledLayers().CuStack() if L not in (pcbnew.F_Cu, pcbnew.B_Cu)]


def lacks(pcbnew, b, v):
    return [b.GetLayerName(L) for L in inner_layers(pcbnew, b) if v.IsOnLayer(L) and not v.FlashLayer(L)]


def mode_of(pcbnew, v):
    try:
        return v.Padstack().UnconnectedLayerMode()
    except Exception:
        return ("remove" if v.GetRemoveUnconnected() else "keep") + ("+start_end" if v.GetKeepStartEnd() else "")


def set_mode(pcbnew, v):
    try:
        v.Padstack().SetUnconnectedLayerMode(pcbnew.UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END)
    except Exception:
        v.SetRemoveUnconnected(True)
        v.SetKeepStartEnd(True)


def refill(pcbnew, b):
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    b.BuildConnectivity()


def want_mode(pcbnew):
    return pcbnew.UNCONNECTED_LAYER_MODE_REMOVE_EXCEPT_START_AND_END


def scan(pcbnew, b, box, rx):
    x0, y0, x1, y1 = box
    out = []
    for t in b.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T:
            continue
        x, y = mm(t.GetPosition().x), mm(t.GetPosition().y)
        if not (x0 - 1e-6 <= x <= x1 + 1e-6 and y0 - 1e-6 <= y <= y1 + 1e-6):
            continue
        if not rx.fullmatch(t.GetNetname().rsplit("/", 1)[-1]):
            continue
        out.append(t)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board")
    ap.add_argument("--file")
    ap.add_argument("--box", nargs=4, type=float, required=True, metavar=("X0", "Y0", "X1", "Y1"))
    ap.add_argument("--nets", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    try:
        rx = re.compile(a.nets)
    except re.error as e:
        die(f"--nets {a.nets!r}: {e}")
    box = (min(a.box[0], a.box[2]), min(a.box[1], a.box[3]), max(a.box[0], a.box[2]), max(a.box[1], a.box[3]))
    if a.file:
        pcb = Path(a.file).resolve()
        if not pcb.is_file():
            die(f"no such file {a.file}")
    else:
        pcb = Path(boardsel.one_pcb(boardsel.resolve(a.board))).resolve()
    real = KP in pcb.parents
    if a.apply and not pcb.with_suffix(".kicad_pro").is_file():
        die(f"{pcb.with_suffix('.kicad_pro')} is missing: SaveBoard would write KiCad's DEFAULT project beside the board "
            f"(min hole clearance 0.25, edge 0.5 ...) and every DRC on the copy would run at those rules -- copy the board's "
            f".kicad_pro (and .kicad_dru) next to it first")
    if real and a.apply:
        if subprocess.run(["pgrep", "-x", "kicad"], stdout=subprocess.DEVNULL).returncode == 0:
            die(f"{pcb} is a board of kicad_project/ and KiCad is running (pgrep -x kicad): close it first")
        if list(pcb.parent.glob("~*.lck")):
            die(f"{pcb.parent} has a KiCad lock file: the project is open")
    import pcbnew
    b = pcbnew.LoadBoard(str(pcb))
    b.BuildConnectivity()
    vias = scan(pcbnew, b, box, rx)
    print(f"unusedpads: {pcb} -- box {list(box)}, nets {a.nets!r}: {len(vias)} via(s)")
    if not vias:
        print("NOTHING MATCHED")
        return 1
    W = want_mode(pcbnew)
    refill(pcbnew, b)
    before = {v.m_Uuid.AsString(): lacks(pcbnew, b, v) for v in vias}
    changed = []
    for v in vias:
        if mode_of(pcbnew, v) != W:
            set_mode(pcbnew, v)
            changed.append(v)
    if changed:
        refill(pcbnew, b)
    for v in vias:
        already = v not in changed
        after = lacks(pcbnew, b, v)
        x, y = mm(v.GetPosition().x), mm(v.GetPosition().y)
        print(f"  {'kept   ' if already else 'CHANGED'} {v.GetNetname().rsplit('/', 1)[-1]:<16} ({x:.3f}, {y:.3f})"
              f"  lacks {','.join(after) or '-'}" + (f"  (lacked {','.join(before[v.m_Uuid.AsString()]) or '-'})" if before[v.m_Uuid.AsString()] != after else ""))
    print(f"unusedpads: {len(changed)} changed, {len(vias) - len(changed)} already in the mode")
    if not a.apply:
        print("DRY RUN: nothing written (--apply writes)")
        return 0
    if not changed:
        print("APPLY: nothing to write")
        return 0
    want = {(v.m_Uuid.AsString()): lacks(pcbnew, b, v) for v in vias}
    pcbnew.SaveBoard(str(pcb), b)
    b2 = pcbnew.LoadBoard(str(pcb))
    refill(pcbnew, b2)
    got = {}
    for t in b2.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T and t.m_Uuid.AsString() in want:
            got[t.m_Uuid.AsString()] = (mode_of(pcbnew, t) == W, lacks(pcbnew, b2, t))
    bad = [u for u in want if u not in got or not got[u][0] or got[u][1] != want[u]]
    if bad:
        die(f"READ BACK FAILED on {len(bad)} via(s): {bad[:5]}")
    print(f"APPLIED: {len(changed)} via(s) written, {len(want)} read back in the mode with the same layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
