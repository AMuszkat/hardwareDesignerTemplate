"""viawebs.py -- do the antipads of neighbouring vias MERGE into a slot in a plane?  (board1's RV27.1)

    tools/kpy tools/route/viawebs.py --board BOARD.kicad_pcb --box x0,y0,x1,y1 [--layers In1.Cu,In3.Cu,In6.Cu]
                                     [--min 0.1016] [--clr 0.1016] [--hole 0.1778] [--plane-net GND]

A plane clears a via's PAD by the clearance (0.1016) where the via is flashed on that layer, and its bare HOLE by the copper-to-hole
(0.1778) where the unconnected pad is removed.  Between two vias of other nets the plane's web is their centre distance less the
two clearance radii; under --min (the plane's minimum width) the two antipads are one slot.  `gnd_islands.py` does not see it: a
slot that reaches the box's edge is no island (tools/route/gndislands.py).  A row of signal vias at 0.67 mm with their pads on
every inner layer left 0.070 mm webs in three planes under a module; with the unconnected pads removed, 0.117 mm.  Prints every pair under 2 x --min and `WEBS PASS|FAIL`.
"""
import math, os, sys
import pcbnew
A = sys.argv[1:]; opt = lambda k, d=None: A[A.index(k) + 1] if k in A else d
PCB = opt("--board") or sys.exit("viawebs.py: --board BOARD.kicad_pcb")
x0, y0, x1, y1 = (float(v) for v in opt("--box").split(","))
LAYERS = opt("--layers", "In1.Cu,In3.Cu,In6.Cu").split(","); MIN = float(opt("--min", "0.1016"))
CLR, HOLE = float(opt("--clr", "0.1016")), float(opt("--hole", "0.1778")); PLANE = opt("--plane-net", "GND")
b = pcbnew.LoadBoard(PCB); mm = pcbnew.ToMM; sh = lambda n: n.rsplit("/", 1)[-1]
bad = 0
for L in LAYERS:
    lid = b.GetLayerID(L); vs = []
    for t in b.GetTracks():
        if t.Type() != pcbnew.PCB_VIA_T: continue
        x, y = mm(t.GetPosition().x), mm(t.GetPosition().y)
        if not (x0 <= x <= x1 and y0 <= y <= y1) or sh(t.GetNetname()) == PLANE: continue
        fl = bool(t.FlashLayer(lid))
        vs.append((x, y, sh(t.GetNetname()), (mm(t.GetWidth(lid)) / 2 + CLR) if fl else (mm(t.GetDrillValue()) / 2 + HOLE), fl))
    worst = None
    for i, a in enumerate(vs):
        for c in vs[i + 1:]:
            if a[2] == c[2]: continue
            web = math.hypot(a[0] - c[0], a[1] - c[1]) - a[3] - c[3]
            if worst is None or web < worst[0]: worst = (web, a, c)
            if web < 2 * MIN:
                tag = "MERGED" if web < MIN else "ok"
                bad += web < MIN
                print(f"  {L}: {a[2]} ({a[0]:.2f}, {a[1]:.2f}) {'pad' if a[4] else 'hole'} - {c[2]} ({c[0]:.2f}, {c[1]:.2f}) {'pad' if c[4] else 'hole'}: web {web:.4f} mm  {tag}")
    if worst:
        print(f"{L}: {len(vs)} vias of other nets in the box, {sum(1 for v in vs if v[4])} flashed; narrowest web {worst[0]:.4f} mm ({worst[1][2]} - {worst[2][2]})")
print("WEBS PASS" if not bad else f"WEBS FAIL ({bad} pair(s) under {MIN} mm: one slot)")
sys.exit(1 if bad else 0)
