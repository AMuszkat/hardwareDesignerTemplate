#!/usr/bin/env python3
"""thermals.py -- the guide's thermal policy, typed into every pour.

    tools/kpy tools/copper/thermals.py [--board B]            what differs (nothing written)
    tools/kpy tools/copper/thermals.py [--board B] --apply    write it: backup, refill, read back (KiCad closed)

The guide's `thermals` row: `outer: all pads; inner: THT only` -- each of
outer (F.Cu / B.Cu) and inner pours gets `all pads` (spokes on SMD and THT),
`THT only` (spokes on through-hole pins, SMD n/a) or `none`.  Vias stay solid.
`thermal pad` rows (`U5.5: solid`, `C12.2: none`) set one pad's own
connection.  Rule areas are left alone.  Inner planes get a 0.2 mm relief gap
and 0.3 mm spokes: KiCad's 0.5 mm default round a row of 2.54 mm pins merges
into a slot that cuts the plane into islands.  route.py apply runs this (with
--apply) when the plan says "thermals": true.  Importable: apply(b, pcbnew, G) -> changes."""
import argparse
import shutil
import sys

import common as C

WANT = {"allpads": "THERMAL", "thtonly": "THT_THERMAL", "none": "NONE"}


def apply(b, pcbnew, G):
    """Set the policy on the in-memory board; returns [(what, from, to)]."""
    pol = G.get("thermals")
    ch = []
    if not pol:
        return ch
    ZC = {k: getattr(pcbnew, "ZONE_CONNECTION_" + v) for k, v in WANT.items()}
    for tp in G.get("thermal_pads", []):
        fp = b.FindFootprintByReference(tp["ref"])
        pad = next((q for q in fp.Pads() if q.GetNumber() == tp["pad"]), None) if fp else None
        if pad is None:
            C.die(f"thermal pad {tp['ref']}.{tp['pad']}: not on the board")
        want = pcbnew.ZONE_CONNECTION_FULL if tp["how"] == "solid" else pcbnew.ZONE_CONNECTION_NONE
        if pad.GetLocalZoneConnection() != want:
            ch.append((f"pad {tp['ref']}.{tp['pad']}", pad.GetLocalZoneConnection(), tp["how"]))
            pad.SetLocalZoneConnection(want)
    for z in b.Zones():
        if z.GetIsRuleArea() or not z.GetNetCode():
            continue
        lays = [b.GetLayerName(l) for l in z.GetLayerSet().Seq()]
        outer = any(l in ("F.Cu", "B.Cu") for l in lays)
        want = ZC[pol["outer"] if outer else pol["inner"]]
        name = f"zone {C.short(z.GetNetname())} {','.join(lays)}"
        if z.GetPadConnection() != want:
            ch.append((name, z.GetPadConnection(), want))
            z.SetPadConnection(want)
        if not outer and (z.GetThermalReliefGap(), z.GetThermalReliefSpokeWidth()) != (pcbnew.FromMM(0.2), pcbnew.FromMM(0.3)):
            z.SetThermalReliefGap(pcbnew.FromMM(0.2)); z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.3))
            ch.append((name, "relief", "0.2 gap / 0.3 spoke"))
    return ch


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_board_args(ap)
    ap.add_argument("--apply", action="store_true")
    a = C.args(ap)
    b = C.load(a)
    pcbnew = C.pcbnew_module()
    if not C.G.get("thermals"):
        print("the guide has no 'thermals' row: nothing to type")
        return 0
    ch = apply(b, pcbnew, C.G)
    for what, old, new in ch:
        print(f"  {what}: {old} -> {new}")
    if not ch:
        print("every pour already follows the guide")
        return 0
    if not a.apply:
        print(f"{len(ch)} change(s); --apply writes them")
        return 0
    bak = str(a.pcb) + ".thermals-bak"
    shutil.copy(a.pcb, bak)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    if not pcbnew.SaveBoard(str(a.pcb), b):
        C.die("SaveBoard failed")
    print(f"written and refilled; previous copy {bak}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
