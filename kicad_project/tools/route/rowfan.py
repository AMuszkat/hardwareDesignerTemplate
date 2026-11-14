#!/usr/bin/env python3
"""rowfan.py -- a via for every signal pin of a fine-pitch row (a QFN's, a connector's), found together.

    tools/kpy tools/route/rowfan.py --board board1 --ref U100 --pins 6,7,8,9,13 \\
        [--diag 6,7] [--reach 1.2] [--width 0.1016] [--via 0.4/0.2] [--ignore-refs C107] \\
        [--plan routing/R6_pmic.json] [--out routing/R6_fan.json] [--group R6fan]

At 0.4 mm pitch a 0.4 mm via fits beyond a pin's tip only 0.30 mm out (its own pad's gap), and two neighbours'
vias then stand 0.4 mm apart -- under the 0.50 a via pair needs.  So a row fans out in staggered rows, and which
pin takes which row depends on what stands beyond the tips (capacitors, a crystal, an inductor, the board edge).
One pin at a time (`stitch.py --pads`) takes the nearest site and walls its neighbour in; this searches them
together.

For each pin: candidate vias on its axis, outward from the tip, every --step from the first legal distance to
--reach; with --diag also on the two 45-degree lines out of its centre (the guide's last resort: such a link is
listed, to be declared in vialink_exceptions.json).  A candidate is legal alone when the via clears every other
net's copper on every layer by the larger class clearance, every other hole by --hole-gap, other nets' copper by
--hole-cu from its hole, keeps the via-to-pad gap from every pad but its own (and from its own pad too), and its
link -- one straight segment out of the pad's centre -- clears every other net's copper on the pad's layer.
--plan lays a plan (polygons, tracks, vias) first, so planned power copper counts.  Then a depth-first search
picks one candidate per pin so that every pair holds: vias of two nets 2 r + clearance apart (holes --hole-gap),
a link clear of another pin's via and link, no two links crossing -- shortest links first.  A pin with no
candidate, or a set with no joint choice, is reported with what blocks it; exit 1.
The output is a plan (tracks + vias) for `route.py check`, which is the judge.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route as R  # noqa: E402


def dseg(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def seg_seg(a, b, c, d):
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = ccw(c, d, a), ccw(c, d, b), ccw(a, b, c), ccw(a, b, d)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(dseg(a, c, d), dseg(b, c, d), dseg(c, a, b), dseg(d, a, b))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--pins", required=True, help="pad numbers, comma separated")
    ap.add_argument("--diag", default="", help="pins that may take a 45-degree link (or 'all')")
    ap.add_argument("--inward", action="store_true", help="also the axis INWARD (a connector's two rows: the room between them)")
    ap.add_argument("--reach", type=float, default=1.2, help="mm from the pad's centre along the axis")
    ap.add_argument("--step", type=float, default=0.025)
    ap.add_argument("--width", type=float, default=0.1016)
    ap.add_argument("--clearance", type=float, default=0.1016)
    ap.add_argument("--via", default="0.4/0.2")
    ap.add_argument("--hole-gap", type=float, default=0.28)
    ap.add_argument("--hole-cu", type=float, default=0.1778)
    ap.add_argument("--gap", type=float, default=None, help="via copper to any pad (default: the guide's)")
    ap.add_argument("--ignore-refs", default="")
    ap.add_argument("--plan", default=None)
    ap.add_argument("--keep", type=int, default=60, help="candidates kept per pin for the search (shortest first)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--group", default="rowfan")
    a = ap.parse_args()
    a.board = R.P.board_dir_arg(a.board)
    B = R.RBoard(a.board)
    pn, b, mm, FM = B.pn, B.b, B.pn.ToMM, B.pn.FromMM
    pad_d, drill = (float(x) for x in a.via.split("/"))
    r, hr, w, clr = pad_d / 2, drill / 2, a.width, a.clearance
    gap = a.gap if a.gap is not None else float(B.gates.get("via_link", {}).get("gap", 0.1016))
    if a.plan:
        plan = json.loads((Path(a.plan) if Path(a.plan).is_absolute() else Path(a.board) / a.plan).read_text())
        B.write_plan(dict(plan, tracks=plan.get("tracks", []), vias=plan.get("vias", [])))
    with R.P.quiet_stderr():
        pn.ZONE_FILLER(b).Fill(b.Zones())
    cu = list(b.GetEnabledLayers().CuStack())
    err = FM(0.002)
    gone = {s for s in a.ignore_refs.split(",") if s}
    f = b.FindFootprintByReference(a.ref)
    want = [p for p in f.Pads() if p.GetNumber() in a.pins.split(",")]
    diag = set(p.GetNumber() for p in want) if a.diag == "all" else set(a.diag.split(","))
    x0 = min(mm(p.GetPosition().x) for p in want) - a.reach - 1.5
    x1 = max(mm(p.GetPosition().x) for p in want) + a.reach + 1.5
    y0 = min(mm(p.GetPosition().y) for p in want) - a.reach - 1.5
    y1 = max(mm(p.GetPosition().y) for p in want) + a.reach + 1.5
    near = lambda q: x0 <= mm(q.x) <= x1 and y0 <= mm(q.y) <= y1          # noqa: E731

    def nclr(item):
        for name in ("GetEffectiveNetClass", "GetNetClass"):
            g = getattr(item, name, None)
            try:
                nc = g() if g else None
                if nc is not None and nc.HasClearance():
                    return mm(nc.GetClearance())
            except Exception:
                pass
        return clr

    pads = [p for fp in b.Footprints() if fp.GetReference() not in gone for p in fp.Pads() if near(p.GetPosition())]
    tracks = [t for t in b.GetTracks() if t.Type() != pn.PCB_VIA_T and (near(t.GetStart()) or near(t.GetEnd()))]
    vias = [t for t in b.GetTracks() if t.Type() == pn.PCB_VIA_T and near(t.GetPosition())]
    shapes = []
    for d in b.GetDrawings():
        try:
            if d.Type() == pn.PCB_SHAPE_T and d.IsOnCopperLayer() and d.GetNetCode() > 0:
                shapes.append(d)
        except Exception:
            pass
    zones = [z for z in b.Zones() if not z.GetIsRuleArea()]
    same = lambda p, q: (p.GetParentFootprint().GetReference() == q.GetParentFootprint().GetReference()       # noqa: E731
                         and p.GetNumber() == q.GetNumber() and p.GetPosition() == q.GetPosition())

    # per net: what its via may not touch (per layer), what its hole may not touch, what its link may not touch (pad layer)
    cache = {}

    def obstacles(pad):
        net = pad.GetNetname()
        key = (net, pad.GetNumber())
        if key in cache:
            return cache[key]
        own_c = nclr(pad)
        lay = next(l for l in cu if pad.IsOnLayer(l))
        vf = {l: pn.SHAPE_POLY_SET() for l in cu}
        hf = pn.SHAPE_POLY_SET()
        lf = pn.SHAPE_POLY_SET()
        for t in tracks:
            if t.GetNetname() == net:
                continue
            c = max(own_c, nclr(t))
            t.TransformShapeToPolygon(vf[t.GetLayer()], t.GetLayer(), FM(r + c), err, pn.ERROR_OUTSIDE)
            t.TransformShapeToPolygon(hf, t.GetLayer(), FM(hr + a.hole_cu), err, pn.ERROR_OUTSIDE)
            if t.GetLayer() == lay:
                t.TransformShapeToPolygon(lf, lay, FM(w / 2 + c), err, pn.ERROR_OUTSIDE)
        for d in shapes:
            if d.GetNetname() == net or d.GetLayer() not in vf:
                continue
            c = max(own_c, nclr(d))
            d.TransformShapeToPolygon(vf[d.GetLayer()], d.GetLayer(), FM(r + c), err, pn.ERROR_OUTSIDE)
            d.TransformShapeToPolygon(hf, d.GetLayer(), FM(hr + a.hole_cu), err, pn.ERROR_OUTSIDE)
            if d.GetLayer() == lay:
                d.TransformShapeToPolygon(lf, lay, FM(w / 2 + c), err, pn.ERROR_OUTSIDE)
        for z in zones:
            if z.GetNetname() == net:
                continue
            for l in z.GetLayerSet().CuStack():
                if l in vf:
                    fz = pn.SHAPE_POLY_SET(z.GetFilledPolysList(l))
                    fz.Inflate(FM(r + max(own_c, 0.1016)), pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
                    vf[l].Append(fz)
        for p in pads:
            if same(p, pad):
                continue
            c = max(gap, own_c, nclr(p)) if p.GetNetname() != net else gap
            for l in cu:
                if p.IsOnLayer(l):
                    p.TransformShapeToPolygon(vf[l], l, FM(r + c), err, pn.ERROR_OUTSIDE)
                    if p.GetNetname() != net:
                        p.TransformShapeToPolygon(hf, l, FM(hr + a.hole_cu), err, pn.ERROR_OUTSIDE)
            if p.GetNetname() != net and p.IsOnLayer(lay):
                p.TransformShapeToPolygon(lf, lay, FM(w / 2 + max(own_c, nclr(p))), err, pn.ERROR_OUTSIDE)
        own = pn.SHAPE_POLY_SET()
        pad.TransformShapeToPolygon(own, lay, FM(r + gap), err, pn.ERROR_OUTSIDE)
        for l in cu:
            vf[l].Simplify()
        hf.Simplify()
        lf.Simplify()
        vn = [(mm(v.GetPosition().x), mm(v.GetPosition().y), mm(v.GetWidth(pn.F_Cu)) / 2, mm(v.GetDrillValue()) / 2,
               v.GetNetname(), nclr(v)) for v in vias]
        cache[key] = (vf, hf, lf, own, vn, own_c)
        return cache[key]

    cands = {}
    for pad in want:
        vf, hf, lf, own, vn, own_c = obstacles(pad)
        cx, cy = mm(pad.GetPosition().x), mm(pad.GetPosition().y)
        bb = pad.GetBoundingBox()
        hw, hh = mm(bb.GetWidth()) / 2, mm(bb.GetHeight()) / 2
        fc = f.GetPosition()
        # the pad's outward axis: away from the footprint's centre, along its long side
        ux, uy = (1.0 if cx > mm(fc.x) else -1.0, 0.0) if hw > hh else (0.0, 1.0 if cy > mm(fc.y) else -1.0)
        dirs = [(ux, uy, "axis")]
        if a.inward:
            dirs.append((-ux, -uy, "axis"))
        if pad.GetNumber() in diag:
            s2 = math.sqrt(0.5)
            if ux:
                dirs += [(ux * s2, s2, "45"), (ux * s2, -s2, "45")]
            else:
                dirs += [(s2, uy * s2, "45"), (-s2, uy * s2, "45")]
        out = []
        half = hw if ux else hh                                  # the pad's half length along its axis
        for dx, dy, kind in dirs:
            k = 0
            d0 = half + gap + r + 1e-4 if kind == "axis" else 0.1    # on the axis the first site is exactly the gap beyond the tip
            while True:
                dist = d0 + k * a.step
                k += 1
                if dist > a.reach + 1e-9:
                    break
                x, y = round(cx + dx * dist, 4), round(cy + dy * dist, 4)
                v = pn.VECTOR2I(FM(x), FM(y))
                if own.Contains(v):
                    continue                                     # inside its own pad's gap
                if any(vf[l].Contains(v) for l in cu) or hf.Contains(v):
                    continue
                bad = False
                for vx, vy, vr, vh, vnet, vc in vn:
                    dd = math.hypot(vx - x, vy - y)
                    if dd - vh - hr < a.hole_gap - 1e-6 or (vnet != pad.GetNetname() and dd - vr - r < max(own_c, vc) - 1e-6):
                        bad = True
                        break
                    if vnet != pad.GetNetname() and dseg((vx, vy), (cx, cy), (x, y)) < vr + w / 2 + max(own_c, vc) - 1e-6:
                        bad = True
                        break
                if bad:
                    continue
                # the link, sampled: outside every other net's copper grown by w/2 + clearance
                n = max(2, int(dist / 0.02))
                if any(lf.Contains(pn.VECTOR2I(FM(cx + (x - cx) * i / n), FM(cy + (y - cy) * i / n))) for i in range(n + 1)):
                    continue
                out.append((dist + (0.5 if kind == "45" else 0.0), (x, y), kind))
        out.sort()
        cands[pad.GetNumber()] = (pad, (cx, cy), out[:a.keep] if a.keep else out)

    order = sorted(cands, key=lambda k: len(cands[k][2]))
    for k in order:
        pad, c, out = cands[k]
        print(f"  pin {k:>3} {R.short(pad.GetNetname()):18s}: {len(out)} candidate(s)" + (f", nearest {out[0][1]} ({out[0][2]})" if out else "  <-- NONE"))
    if any(not cands[k][2] for k in order):
        print("ROWFAN FAIL: a pin has no via site at all")
        return 1

    best = [None, 0]
    chosen = {}

    def ok(k, cand):
        pad, c, _ = cands[k]
        net = pad.GetNetname()
        (x, y) = cand[1]
        for j, (cj, pj) in chosen.items():
            padj, ccj, _ = cands[j]
            netj = padj.GetNetname()
            (xj, yj) = cj[1]
            dv = math.hypot(x - xj, y - yj)
            if dv - 2 * hr < a.hole_gap - 1e-6:
                return False
            if net != netj:
                if dv < 2 * r + clr - 1e-6:
                    return False
                if dseg((xj, yj), c, (x, y)) < r + w / 2 + clr - 1e-6 or dseg((x, y), ccj, (xj, yj)) < r + w / 2 + clr - 1e-6:
                    return False
                if seg_seg(c, (x, y), ccj, (xj, yj)) < w + clr - 1e-6:
                    return False
        return True

    nodes = [0]

    def dfs(i, cost):
        nodes[0] += 1
        if nodes[0] > 400000:
            return
        if best[0] is not None and cost >= best[1]:
            return
        if i == len(order):
            best[0], best[1] = dict(chosen), cost
            return
        k = order[i]
        for cand in cands[k][2]:
            if ok(k, cand):
                chosen[k] = (cand, None)
                dfs(i + 1, cost + cand[0])
                del chosen[k]

    dfs(0, 0.0)
    if best[0] is None:
        # who cannot be placed with whom: the largest prefix that closes
        print(f"ROWFAN FAIL: no joint choice for pins {','.join(order)} ({nodes[0]} nodes searched)")
        for drop in order:
            sub = [k for k in order if k != drop]
            chosen.clear()
            saved = order[:]
            order[:] = sub
            best[0] = None
            nodes[0] = 0
            dfs(0, 0.0)
            if best[0] is not None:
                print(f"   without pin {drop}: the others close")
            order[:] = saved
        return 1
    tracks, vs, diag_used = [], [], []
    for k in order:
        cand, _ = best[0][k]
        pad, c, _ = cands[k]
        (x, y), kind = cand[1], cand[2]
        print(f"  pin {k:>3} {R.short(pad.GetNetname()):18s} via ({x:.4f}, {y:.4f}) link {math.dist(c, (x, y)):.3f} mm ({kind})")
        tracks.append({"net": pad.GetNetname(), "layer": b.GetLayerName(next(l for l in cu if pad.IsOnLayer(l))), "width": w,
                       "pts": [[round(c[0], 4), round(c[1], 4)], [x, y]]})
        vs.append({"net": pad.GetNetname(), "at": [x, y], "pad": pad_d, "drill": drill})
        if kind == "45":
            diag_used.append({"via": [x, y], "why": f"{a.ref}.{k}: no axis site in a {pad_d} mm via's reach at this pitch"})
    print(f"ROWFAN PASS: {len(vs)} via(s), {len(diag_used)} at 45 degrees; total link {best[1]:.2f} mm")
    if a.out:
        p = Path(a.out) if Path(a.out).is_absolute() else Path(a.board) / a.out
        p.write_text(json.dumps({"group": a.group, "why": f"{a.ref}'s pins {a.pins}: a via each, found together (rowfan.py)",
                                 "tracks": tracks, "vias": vs, "zones": [], "vialink_exceptions": diag_used}, indent=1))
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
