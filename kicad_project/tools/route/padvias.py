#!/usr/bin/env python3
"""padvias.py -- vias IN a pad: the array a power pad takes down to its plane, as a plan route.py judges.

    tools/kpy tools/route/padvias.py --board board1 --pads C100.1:4,C100.2:4,U100.57:20 \\
        [--via 0.4/0.2] [--pitch 0.5] [--out routing/R6_padvias.json] [--group R6pv] [--count]

A regulator's output capacitor, an input capacitor, an exposed pad: the pad is the copper and the vias stand in
it (10_layout: 'a via in the pad needs no link').  `stitch.py --only-pads` gives a pad ONE via beside it on a
link; this gives a pad as many as it is asked for, inside it, and says how many fit.

For each REF.PAD[:N] (N missing: as many as fit):
  * candidate centres on a 0.05 mm lattice where the whole via pad lies inside the pad's copper (--overhang lets it
    reach that far past the edge: a rounded 0603 land holds 2 x 2 at 0.5 mm only with 0.05);
  * a centre is legal when, on EVERY copper layer, the via's pad clears the copper of every other net (tracks,
    pads, fills) by the larger of the two nets' class clearances, its hole clears every other hole by --hole-gap
    (edge to edge) and other nets' copper by --hole-cu, and its pad keeps the guide's via-to-pad gap from every
    OTHER pad, its own net's included;
  * N centres are kept, at least --pitch apart (and --pitch from the net's vias already on the board), spread
    over the pad: the first nearest the pad's centre, each next one the farthest from those chosen.
--count only prints, per pad, the legal centres and how many fit at --pitch (a survey: nothing is written).
Nothing is written to the board: the output is a plan (vias only) for `route.py check` / `apply`; KiCad's DRC
is the judge, this search only the proposer.  Exit 1 when a pad got fewer than it was asked for.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import route as R  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("--pads", required=True, help="REF.PAD[:N],... (N missing: as many as fit)")
    ap.add_argument("--via", default="0.4/0.2", help="pad / drill, mm")
    ap.add_argument("--pitch", type=float, default=0.5, help="mm between two vias of one pad, centre to centre")
    ap.add_argument("--hole-gap", type=float, default=0.28, help="hole edge to hole edge (14_fab)")
    ap.add_argument("--hole-cu", type=float, default=0.1778, help="hole edge to another net's copper (14_fab)")
    ap.add_argument("--gap", type=float, default=None, help="via copper to any other pad (default: the guide's via-to-pad gap)")
    ap.add_argument("--step", type=float, default=0.05)
    ap.add_argument("--overhang", type=float, default=0.0, help="mm the via's pad may reach past the pad's edge (0: wholly inside)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--group", default="padvias")
    ap.add_argument("--why", default="in-pad vias of the power pads")
    ap.add_argument("--count", action="store_true")
    a = ap.parse_args()
    a.board = R.P.board_dir_arg(a.board)
    B = R.RBoard(a.board)
    pn, b, mm, FM = B.pn, B.b, B.pn.ToMM, B.pn.FromMM
    pad_d, drill = (float(x) for x in a.via.split("/"))
    r, hr = pad_d / 2, drill / 2
    gap = a.gap if a.gap is not None else float(B.gates.get("via_link", {}).get("gap", 0.1016))
    with R.P.quiet_stderr():
        pn.ZONE_FILLER(b).Fill(b.Zones())
    cu = [l for l in b.GetEnabledLayers().CuStack()]
    err = FM(0.002)

    def clr(item):
        """the item's net-class clearance (KiCad takes the larger of two nets')"""
        for name in ("GetEffectiveNetClass", "GetNetClass"):
            g = getattr(item, name, None)
            try:
                nc = g() if g else None
                if nc is not None and nc.HasClearance():
                    return mm(nc.GetClearance())
            except Exception:
                pass
        return 0.1016

    want = []
    for spec in a.pads.split(","):
        rp, _, n = spec.partition(":")
        ref, _, num = rp.partition(".")
        f = b.FindFootprintByReference(ref)
        pads = [p for p in f.Pads() if p.GetNumber() == num] if f else []
        if not pads:
            sys.exit(f"no pad {rp} on the board")
        for p in pads:
            want.append((rp, p, int(n) if n else None))

    tracks = [t for t in b.GetTracks() if t.Type() != pn.PCB_VIA_T]
    vias = [t for t in b.GetTracks() if t.Type() == pn.PCB_VIA_T]
    allpads = [p for f in b.Footprints() for p in f.Pads()]
    placed = []                                                # (x, y, net) of this run, every pad
    out, short = [], 0
    for rp, pad, n in want:
        net = pad.GetNetname()
        own_c = clr(pad)
        lay = next(l for l in cu if pad.IsOnLayer(l))
        body = pn.SHAPE_POLY_SET()
        pad.TransformShapeToPolygon(body, lay, 0, err, pn.ERROR_INSIDE)
        body.Deflate(FM(max(r - a.overhang, 0.0)), pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
        bb = pad.GetBoundingBox()
        x0, y0, x1, y1 = mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())
        near = lambda q, m=1.5: x0 - m <= mm(q.x) <= x1 + m and y0 - m <= mm(q.y) <= y1 + m          # noqa: E731
        # what the via's PAD may not touch, per layer; what its HOLE may not touch (any layer)
        forbid = {l: pn.SHAPE_POLY_SET() for l in cu}
        holecu = {l: pn.SHAPE_POLY_SET() for l in cu}
        for t in tracks:
            if t.GetNetname() == net or not (near(t.GetStart()) or near(t.GetEnd())):
                continue
            c = max(own_c, clr(t))
            t.TransformShapeToPolygon(forbid[t.GetLayer()], t.GetLayer(), FM(r + c), err, pn.ERROR_OUTSIDE)
            t.TransformShapeToPolygon(holecu[t.GetLayer()], t.GetLayer(), FM(hr + a.hole_cu), err, pn.ERROR_OUTSIDE)
        for z in b.Zones():
            if z.GetIsRuleArea() or z.GetNetname() == net:
                continue
            for l in z.GetLayerSet().CuStack():
                if l in forbid:
                    f_ = pn.SHAPE_POLY_SET(z.GetFilledPolysList(l))
                    f_.Inflate(FM(r + max(own_c, 0.1016)), pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
                    forbid[l].Append(f_)
        for p in allpads:
            if (p.GetParentFootprint().GetReference() == pad.GetParentFootprint().GetReference()
                    and p.GetNumber() == pad.GetNumber() and p.GetPosition() == pad.GetPosition()):
                continue                                         # the pad itself (SWIG hands out a new wrapper each time: never `is`)
            if not near(p.GetPosition(), 3.0):
                continue
            c = max(gap, max(own_c, clr(p)) if p.GetNetname() != net else gap)
            for l in cu:
                if p.IsOnLayer(l):
                    p.TransformShapeToPolygon(forbid[l], l, FM(r + c), err, pn.ERROR_OUTSIDE)
                    if p.GetNetname() != net:
                        p.TransformShapeToPolygon(holecu[l], l, FM(hr + a.hole_cu), err, pn.ERROR_OUTSIDE)
        for l in cu:
            forbid[l].Simplify()
            holecu[l].Simplify()
        vnear = []
        for v in vias:
            if near(v.GetPosition()):
                q = v.GetPosition()
                vnear.append((mm(q.x), mm(q.y), mm(v.GetWidth(pn.F_Cu)) / 2, mm(v.GetDrillValue()) / 2, v.GetNetname(), clr(v),
                              v.GetRemoveUnconnected()))

        def legal(x, y):
            v = pn.VECTOR2I(FM(x), FM(y))
            if not body.Contains(v):
                return False
            for vx, vy, vr, vh, vn, vc, nfp in vnear:
                d = math.hypot(vx - x, vy - y)
                if d - vh - hr < a.hole_gap - 1e-6:
                    return False
                if vn != net and d - vr - r < max(own_c, vc) - 1e-6:
                    return False                                  # the outer layers always; an inner pad kept is the same circle
            for px, py, pnet in placed:
                d = math.hypot(px - x, py - y)
                if d - 2 * hr < a.hole_gap - 1e-6 or (pnet != net and d - 2 * r < own_c - 1e-6):
                    return False
            return not any(forbid[l].Contains(v) or holecu[l].Contains(v) for l in cu)

        cells = []
        j = 0
        while y0 + j * a.step <= y1 + 1e-9:
            i = 0
            while x0 + i * a.step <= x1 + 1e-9:
                x, y = round(x0 + i * a.step, 4), round(y0 + j * a.step, 4)
                if legal(x, y):
                    cells.append((x, y))
                i += 1
            j += 1
        cx, cy = mm(pad.GetPosition().x), mm(pad.GetPosition().y)
        own_v = [(vx, vy) for vx, vy, _r, _h, vn, _c, _n in vnear if vn == net]
        free = [c for c in cells if all(math.dist(c, o) >= a.pitch - 1e-6 for o in own_v)]
        def spread(k):
            """up to k centres at least --pitch apart: a corner first, each next one the farthest from those chosen"""
            got, left = [], list(free)
            while left and (k is None or len(got) < k):
                if not got:
                    c = min(left, key=lambda c: (round(c[0] + c[1], 3), c)) if k != 1 else \
                        min(left, key=lambda c: (round(math.dist(c, (cx, cy)), 3), c))
                else:
                    c = max(left, key=lambda c: (round(min(math.dist(c, p) for p in got), 3), -c[0] - c[1]))
                got.append(c)
                left = [q for q in left if math.dist(q, c) >= a.pitch - 1e-6]
            return got

        fit = len(spread(None))
        pick = spread(n)
        side = b.GetLayerName(lay)
        tag = ""
        if n is not None and len(pick) < n:
            short += 1
            tag = f"  <-- {n} asked"
        print(f"{rp:9s} {R.short(net):18s} {side:5s} pad {x1 - x0:.2f} x {y1 - y0:.2f}: {len(cells):4d} legal centre(s), "
              f"{fit} fit at {a.pitch} mm, {len(own_v)} via(s) of the net there already; "
              f"{'kept ' + str(len(pick)) + ': ' + ' '.join(f'({x:.2f}, {y:.2f})' for x, y in pick) if not a.count else ''}{tag}")
        if not a.count:
            for x, y in pick:
                placed.append((x, y, net))
                out.append({"net": net, "at": [x, y], "pad": pad_d, "drill": drill})
    if a.count:
        return 0
    plan = {"group": a.group, "why": a.why, "tracks": [], "vias": out, "zones": []}
    if a.out:
        p = Path(a.out) if Path(a.out).is_absolute() else Path(a.board) / a.out
        p.write_text(json.dumps(plan, indent=1))
        print(f"wrote {p}: {len(out)} via(s) in {len(want)} pad(s)")
    return 1 if short else 0


if __name__ == "__main__":
    sys.exit(main())
