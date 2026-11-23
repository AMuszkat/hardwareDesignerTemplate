"""guardblock.py -- a guarded block holds no other net's copper (a crystal's keep-out, a sense line's island).

The block is the named nets' pads on the named parts plus those nets' tracks on the outline layer, grown by --clr.
Every track, via and zone fill of any OTHER net (and not --own, e.g. GND for a guard) on the --layers is tested
against it.  KiCad's DRC cannot say this: a rule area would have to follow the tracks, and route.py audit does not
know the block.

  tools/kpy tools/copper/guardblock.py BOARD.kicad_pcb --parts Y100,C122,C123 --nets PMIC_XTAL_IN,PMIC_XTAL_OUT \
        --layers F.Cu,In1.Cu,In2.Cu,In3.Cu,In4.Cu,In5.Cu,In6.Cu --own GND --clr 0.1016 [--zones]

Prints one line per intrusion (net, kind, layer, where, overlap) and `GUARDBLOCK PASS` / `GUARDBLOCK FAIL n`;
exit 0 / 1.  Zone fills are tested only with --zones (a whole plane under a block may be what the rule wants).
"""
import argparse, sys
import pcbnew

ap = argparse.ArgumentParser()
ap.add_argument("board"); ap.add_argument("--parts", required=True); ap.add_argument("--nets", required=True)
ap.add_argument("--layers", default="F.Cu"); ap.add_argument("--outline-layer", default="F.Cu")
ap.add_argument("--own", default="GND"); ap.add_argument("--clr", type=float, default=0.1016)
ap.add_argument("--zones", action="store_true")
a = ap.parse_args()
b = pcbnew.LoadBoard(a.board); FM = pcbnew.FromMM; mm = pcbnew.ToMM; E = FM(0.002)
leaf = lambda n: n.rsplit('/', 1)[-1]
nets = set(a.nets.split(",")); own = set(x for x in a.own.split(",") if x)
OL = b.GetLayerID(a.outline_layer)
blk = pcbnew.SHAPE_POLY_SET()
for ref in a.parts.split(","):
    f = b.FindFootprintByReference(ref)
    if f is None: sys.exit(f"guardblock: no part {ref}")
    for p in f.Pads():
        if leaf(p.GetNetname()) in nets: p.TransformShapeToPolygon(blk, OL, FM(a.clr), E, pcbnew.ERROR_OUTSIDE)
for t in b.GetTracks():
    if t.Type() != pcbnew.PCB_VIA_T and leaf(t.GetNetname()) in nets and t.GetLayer() == OL:
        t.TransformShapeToPolygon(blk, OL, FM(a.clr), E, pcbnew.ERROR_OUTSIDE)
blk.Simplify()
if not blk.OutlineCount(): sys.exit("guardblock: the block is empty (parts / nets named wrong?)")
layers = [b.GetLayerID(n) for n in a.layers.split(",")]
bad = 0
def hit(s):
    s.BooleanIntersection(blk)
    return sum(abs(s.Outline(i).Area()) for i in range(s.OutlineCount())) / 1e12 if s.OutlineCount() else 0
for t in b.GetTracks():
    n = leaf(t.GetNetname())
    if n in nets or n in own: continue
    for L in layers:
        if not t.IsOnLayer(L): continue
        s = pcbnew.SHAPE_POLY_SET(); t.TransformShapeToPolygon(s, L, 0, E, pcbnew.ERROR_INSIDE)
        ar = hit(s)
        if ar > 1e-6:
            kind = "via" if t.Type() == pcbnew.PCB_VIA_T else "track"
            p0 = t.GetPosition() if kind == "via" else t.GetStart(); p1 = t.GetEnd()
            where = f"({mm(p0.x):.3f},{mm(p0.y):.3f})" + ("" if kind == "via" else f"-({mm(p1.x):.3f},{mm(p1.y):.3f})")
            print(f"IN BLOCK  {n:20s} {kind:5s} {b.GetLayerName(L):7s} {where}  overlap {ar:.4f} mm2"); bad += 1
            break
if a.zones:
    for z in b.Zones():
        n = leaf(z.GetNetname())
        if n in nets or n in own: continue
        for L in layers:
            if z.IsOnLayer(L):
                ar = hit(pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(L)))
                if ar > 1e-6: print(f"IN BLOCK  {n:20s} zone  {b.GetLayerName(L):7s} overlap {ar:.4f} mm2"); bad += 1
bb = blk.BBox()
print(f"block bbox ({mm(bb.GetLeft()):.3f},{mm(bb.GetTop()):.3f})-({mm(bb.GetRight()):.3f},{mm(bb.GetBottom()):.3f}), "
      f"{blk.OutlineCount()} outline(s), layers {a.layers}")
print("GUARDBLOCK", "PASS" if not bad else f"FAIL {bad}")
sys.exit(1 if bad else 0)
