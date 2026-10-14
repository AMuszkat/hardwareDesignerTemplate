#!/usr/bin/env python3
"""cudump.py -- the board's copper as JSON, for crops.py (which needs matplotlib,
not pcbnew): tracks, vias, pads, courtyards, references.

    tools/kpy tools/copper/cudump.py [--board B] [--out DUMP.json]
    tools/kpy tools/copper/cudump.py --pcb trial.kicad_pcb --out after.json"""
import argparse
import json
import sys

import common as C


def dump(b, pcbnew):
    mm = pcbnew.ToMM
    LAY = C.copper_layers(b, pcbnew)
    D = dict(layers=LAY, tracks=[], vias=[], pads=[], refs=[], court=[])
    for f in b.GetFootprints():
        p0 = f.GetPosition(); D["refs"].append((f.GetReference(), mm(p0.x), mm(p0.y)))
        c = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
        for k in range(c.OutlineCount()):
            o = c.Outline(k); D["court"].append([(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())])
        for p in f.Pads():
            if not p.GetNumber():
                continue
            bb = p.GetBoundingBox()
            D["pads"].append(dict(ref=f.GetReference() + "." + p.GetNumber(), net=C.short(p.GetNetname()),
                                  layers=[L for L in LAY if p.IsOnLayer(b.GetLayerID(L))],
                                  box=[mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())],
                                  round=p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE))
    for t in b.GetTracks():
        n = C.short(t.GetNetname())
        if t.Type() == pcbnew.PCB_VIA_T:
            D["vias"].append(dict(net=n, at=[mm(t.GetPosition().x), mm(t.GetPosition().y)], pad=mm(t.GetWidth(pcbnew.F_Cu))))
        else:
            a, e = t.GetStart(), t.GetEnd()
            D["tracks"].append(dict(net=n, layer=t.GetLayerName(), width=mm(t.GetWidth()), pts=[[mm(a.x), mm(a.y)], [mm(e.x), mm(e.y)]]))
    return D


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    C.add_board_args(ap)
    ap.add_argument("--out", help="default <board>/routing/copper.json")
    a = C.args(ap)
    b = C.load(a)
    out = C.out_path(a, "copper.json")
    json.dump(dump(b, C.pcbnew_module()), open(out, "w"))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
