"""How many MORE 4 mil tracks fit across a line on an inner layer -- the free lanes of a strait, before a plan counts on them.

    tools/kpy tools/route/lanecount.py BOARD.kicad_pcb --layer In5.Cu --x 17.0,22.7 --y 14.6,15.0,15.4,15.8,16.0,16.2,16.6,17.0,17.5,18.0

For each y: the x intervals where a 0.1016 mm track's CENTRE may lie on that layer (0.1016 mm from every other net's track
and via pad, 0.1778 mm from a bare hole), and how many tracks at 0.2032 mm pitch they take.  The least over the y's of a
run is what the run can carry -- BUT READ WHERE THE RUNS ARE: "8 more fit" was a corridor behind three other tracks and two
dead-end slots beside a via; the count is room only where a net can get to it and out again.
"""
import math, os, sys
import pcbnew
A = sys.argv[1:]; opt = lambda k, d=None: A[A.index(k) + 1] if k in A else d
pcb = next((a for a in A if a.endswith(".kicad_pcb")), None)
if not pcb:
    sys.exit("lanecount.py: name the .kicad_pcb file")
b = pcbnew.LoadBoard(pcb); b.BuildConnectivity(); mm = pcbnew.ToMM; sh = lambda n: n.rsplit("/", 1)[-1]
layer = opt("--layer", "In5.Cu"); lid = b.GetLayerID(layer)
X0, X1 = [float(v) for v in opt("--x").split(",")]; YS = [float(v) for v in opt("--y").split(",")]
W, C, H = 0.1016, 0.1016, 0.1778
tracks = []; vias = []
for t in b.GetTracks():
    if t.Type() == pcbnew.PCB_VIA_T:
        vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetWidth(lid)) / 2 if t.FlashLayer(lid) else None, mm(t.GetDrillValue()) / 2, sh(t.GetNetname())))
    elif t.GetLayer() == lid:
        tracks.append(((mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y)), mm(t.GetWidth()) / 2, sh(t.GetNetname())))


def dseg(px, py, a, e):
    dx, dy = e[0] - a[0], e[1] - a[1]; L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
    return math.hypot(px - a[0] - t * dx, py - a[1] - t * dy)


worst = None
for y in YS:
    free = []; run = None; here = set(); n = int(round((X1 - X0) / 0.005))
    for i in range(n + 1):
        x = X0 + i * 0.005; ok = True
        for a, e, hw, net in tracks:
            if abs(y - (a[1] + e[1]) / 2) < abs(a[1] - e[1]) / 2 + 0.5 and dseg(x, y, a, e) < hw + C + W / 2:
                ok = False; here.add(net); break
        if ok:
            for vx, vy, pr, dr, net in vias:
                if abs(vx - x) < 0.6 and abs(vy - y) < 0.6 and math.hypot(vx - x, vy - y) < ((pr + C) if pr else (dr + H)) + W / 2:
                    ok = False; break
        if ok and run is None:
            run = x
        if not ok and run is not None:
            free.append((run, x - 0.005)); run = None
    if run is not None:
        free.append((run, X1))
    lanes = sum(int((b_ - a_) / 0.2032 + 1e-9) + 1 for a_, b_ in free)
    worst = lanes if worst is None else min(worst, lanes)
    print(f"y {y:6.2f}: {lanes:2d} more track(s) fit  | free centre runs: " + " ".join(f"{a_:.2f}-{b_:.2f}" for a_, b_ in free if b_ - a_ >= 0) + f"  | there now: {len(here)} nets")
print(f"{layer} x {X0}-{X1}: the least over these lines is {worst} more track(s)")
