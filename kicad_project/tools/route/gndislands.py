"""Does an escape on a GND layer close an island of the plane, and does it run under a pad it may not?  DRC has no test
for it and no pour exists yet to show it (board1's RV24.2: two escapes on neighbouring via sites, 0.8 mm2, no ground via).

    tools/kpy tools/route/gndislands.py dump --board BOARD.kicad_pcb --layer In6.Cu --box 18.5,21.0,22.3,26.0 [--under R405.1,C404.1] --json OUT
    python3   tools/route/gndislands.py find OUT                     # numpy + scipy: KiCad's interpreter has neither

`dump` (pcbnew) writes the layer's copper of every net but GND in the box -- tracks, each via as the board has it there
(a pad, or the bare hole), the GND vias, and the named pads' boxes.  `find` rasters the plane as a pour would fill it
(--clr 0.1016 from other copper, 0.1778 from a bare hole, nothing narrower than --min-width 0.1016), and lists every
piece that does not reach the box's border: an ISLAND, with its area and the GND vias in it (none = no way to feed it).
It also measures each escape under the named pads in plan view.  `ISLANDS PASS` when no island lacks a GND via and no
track lies under a named pad.
"""
import json, math, os, sys
A = sys.argv[1:]
opt = lambda k, d=None: A[A.index(k) + 1] if k in A else d
if A and A[0] == "dump":
    import pcbnew
    pcb = opt("--board")
    if not pcb:
        sys.exit("gndislands.py dump: --board BOARD.kicad_pcb")
    b = pcbnew.LoadBoard(pcb); b.BuildConnectivity(); mm = pcbnew.ToMM; sh = lambda n: n.rsplit("/", 1)[-1]
    layer = opt("--layer", "In6.Cu"); lid = b.GetLayerID(layer)
    X0, Y0, X1, Y1 = [float(v) for v in opt("--box").split(",")]
    near = lambda x, y: X0 - 1 <= x <= X1 + 1 and Y0 - 1 <= y <= Y1 + 1
    out = {"layer": layer, "box": [X0, Y0, X1, Y1], "tracks": [], "vias": [], "gnd_vias": [], "pads": []}
    for t in b.GetTracks():
        n = sh(t.GetNetname())
        if t.Type() == pcbnew.PCB_VIA_T:
            q = t.GetPosition(); x, y = mm(q.x), mm(q.y)
            if not near(x, y):
                continue
            if n == "GND":
                out["gnd_vias"].append([x, y])
            else:
                out["vias"].append({"net": n, "at": [x, y], "pad": mm(t.GetWidth(lid)), "drill": mm(t.GetDrillValue()), "flashed": bool(t.FlashLayer(lid))})
        elif t.GetLayer() == lid and n != "GND":
            s, e = t.GetStart(), t.GetEnd()
            if near(mm(s.x), mm(s.y)) or near(mm(e.x), mm(e.y)):
                out["tracks"].append({"net": n, "a": [mm(s.x), mm(s.y)], "b": [mm(e.x), mm(e.y)], "w": mm(t.GetWidth())})
    want = set((opt("--under", "") or "").split(",")) - {""}
    for f in b.GetFootprints():
        for p in f.Pads():
            k = f"{f.GetReference()}.{p.GetNumber()}"
            if k in want:
                bb = p.GetBoundingBox()
                out["pads"].append({"pad": k, "net": sh(p.GetNetname()), "box": [mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())]})
            if p.GetDrillSize().x > 0 and near(mm(p.GetPosition().x), mm(p.GetPosition().y)) and sh(p.GetNetname()) != "GND":
                out["vias"].append({"net": sh(p.GetNetname()), "at": [mm(p.GetPosition().x), mm(p.GetPosition().y)], "pad": mm(p.GetSize().x), "drill": mm(p.GetDrillSize().x), "flashed": True})
    json.dump(out, open(opt("--json"), "w"))
    print(f"{layer} {out['box']}: {len(out['tracks'])} track segments, {len(out['vias'])} vias of other nets, {len(out['gnd_vias'])} GND vias, {len(out['pads'])} pads named")
    sys.exit(0)
import numpy as np
from scipy import ndimage
D = json.load(open(A[1])); CLR = float(opt("--clr", "0.1016")); HOLE = float(opt("--clr-hole", "0.1778")); MINW = float(opt("--min-width", "0.1016")); R = float(opt("--res", "0.01"))
X0, Y0, X1, Y1 = D["box"]; nx = int(round((X1 - X0) / R)) + 1; ny = int(round((Y1 - Y0) / R)) + 1
xs = X0 + np.arange(nx) * R; ys = Y0 + np.arange(ny) * R; XX, YY = np.meshgrid(xs, ys)
blocked = np.zeros((ny, nx), bool)


def dseg(a, b):
    ax, ay = a; bx, by = b; dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
    t = np.clip(((XX - ax) * dx + (YY - ay) * dy) / L2, 0, 1) if L2 else 0
    return np.hypot(XX - (ax + t * dx), YY - (ay + t * dy))


for t in D["tracks"]:
    blocked |= dseg(t["a"], t["b"]) < t["w"] / 2 + CLR
for v in D["vias"]:
    blocked |= np.hypot(XX - v["at"][0], YY - v["at"][1]) < (v["pad"] / 2 + CLR if v["flashed"] else v["drill"] / 2 + HOLE)
r = int(round(MINW / 2 / R)); yy, xx = np.ogrid[-r:r + 1, -r:r + 1]; disk = xx * xx + yy * yy <= r * r
free = ndimage.binary_opening(~blocked, structure=disk) if r else ~blocked
lab, n = ndimage.label(free)
border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
bad = 0
for k in range(1, n + 1):
    if k in border:
        continue
    m = lab == k; area = m.sum() * R * R
    if area < 0.01:
        continue
    yi, xi = np.nonzero(m)
    inside = [g for g in D["gnd_vias"] if X0 <= g[0] <= X1 and Y0 <= g[1] <= Y1 and m[int(round((g[1] - Y0) / R)), int(round((g[0] - X0) / R))]]
    bad += not inside
    print(f"  ISLAND {area:.2f} mm2, x {xs[xi.min()]:.2f}-{xs[xi.max()]:.2f}, y {ys[yi.min()]:.2f}-{ys[yi.max()]:.2f}: {len(inside)} GND via(s) in it" + ("" if inside else "  <-- none"))
under = 0
for p in D["pads"]:
    l, t_, rr, bt = p["box"]; pm = (XX >= l) & (XX <= rr) & (YY >= t_) & (YY <= bt)
    cover = 1 - (free & pm).sum() / max(1, pm.sum())
    hits = []
    for t in D["tracks"]:
        ov = (dseg(t["a"], t["b"]) <= t["w"] / 2) & pm
        if ov.any():
            hits.append(f"{t['net']} {ov.sum() * R * R:.3f} mm2")
    gap = min((float((dseg(t["a"], t["b"]) - t["w"] / 2)[pm].min()) for t in D["tracks"]), default=9.0)
    under += bool(hits)
    print(f"  under {p['pad']} ({p['net']}): the plane covers {100 * (1 - cover):.0f} % of the pad; "
          + (f"tracks under it: {', '.join(sorted(set(hits)))}" if hits else f"no track under it (nearest {gap:.3f} mm away in plan)"))
print(f"{D['layer']} {D['box']}: {n} piece(s) of plane at {CLR} / {MINW} mm; islands with no GND via: {bad}")
print("ISLANDS", "PASS" if not bad and not under else "FAIL")
sys.exit(1 if bad or under else 0)
