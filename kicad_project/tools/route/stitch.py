#!/usr/bin/env python3
"""stitch.py -- stitching vias for a net's pours, as a plan route.py judges like any other.

    tools/kpy tools/route/stitch.py --board board2 --plan routing/R6_pours.json --net GND \\
        --pitch 5 [--grid 10] [--via 0.6/0.3] --out routing/R6_stitch.json

A layout that asks for "GND stitching vias every 5 mm or so along the pours' edges and round
the cut-out" asks for a few hundred placements that each have to clear every other net's
copper on every layer.  This places them, on the board as it is plus the zones of --plan
(written in memory and filled, never saved):

  tie     every island of the net's fill, on every layer it pours, that holds no via of the
          net gets one (a GND pour that tracks split is legal only when each piece reaches the
          plane; a through-hole pin alone leaves a pocket DRC calls starved_thermal);
  ring    along every Edge.Cuts contour -- the outline and each cut-out -- just inside the
          copper-to-edge band, one via per --pitch mm;
  grid    with --grid G, one per G x G cell inside, where no via of the net stands already;
  pads    with --pads (or --only-pads), every SMD pad of the net that no track of the net
          touches gets a via of its own on one of its axes, at the pad's edge + the via-to-pad
          gap + the via's radius or further out to --reach, linked by ONE straight segment out of
          the pad's centre (the via-link rule; the class width, or the pad's width if narrower).
          --diag tries a straight 45-degree link where no axis has room (the guide's last resort:
          each is printed, to be declared in vialink_exceptions.json).  A pad with a hole in its
          middle (a standoff ring) is not dropped this way.  Run it BEFORE the signal groups
          that would wall the pads in (board2: R5's inner-layer routes left eight GND pads no
          site; laid first, R5 routed round them).

A site is legal when, for a via of --via pad / drill:
  - its pad lies inside the net's FILL on every layer the net pours (it joins them), and
    clear of every other net's copper (tracks, vias, pads, other pours' fills) on the layers
    it does not pour, by the class clearance;
  - it keeps the guide's "via to pad gap" from EVERY pad, its own net's included;
  - its centre is at least --sep from every via (the board's routing via_sep);
  - it keeps --own-gap from the net's own copper on layers the net does not pour (closer, the
    slot between them is the shape rule's notch);
  - it keeps the copper-to-edge minimum (Board Setup's) from Edge.Cuts;
  - (the guide's "via clear of class" does not apply to the net it exempts, as GND usually is).
Each wanted spot searches outward on a 0.05 mm lattice to --reach mm for the nearest legal
site; a spot with none is listed (MISSED), not forced.  --pad-list / --skip-pads choose pads.  Nothing is written to the board: the output is
a plan (vias, plus --plan's zones and keys when --merge) for `route.py check` / `apply`.
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
    ap.add_argument("--plan", help="a plan whose zones are laid (in memory) before the search")
    ap.add_argument("--net", default="GND")
    ap.add_argument("--pitch", type=float, default=5.0, help="mm between ring vias along each contour")
    ap.add_argument("--grid", type=float, default=0.0, help="mm: one via per cell inside (0: none)")
    ap.add_argument("--inset", type=float, default=0.0,
                    help="mm inside the copper-to-edge band for the ring (default: half the via pad + 0.25)")
    ap.add_argument("--reach", type=float, default=1.5, help="mm searched round each wanted spot")
    ap.add_argument("--via", default="0.6/0.3", help="pad/drill mm")
    ap.add_argument("--sep", type=float, default=0.8, help="mm, via centre to any via centre")
    ap.add_argument("--clearance", type=float, default=0.2, help="mm to other nets' copper (the class's)")
    ap.add_argument("--gap", type=float, default=None, help="mm via copper to any pad (default: the guide's)")
    ap.add_argument("--own-gap", type=float, default=0.6,
                    help="mm from the net's OWN copper on layers it does not pour (a closer via leaves a slot the "
                         "shape rule calls a notch: 2 x CLOSE)")
    ap.add_argument("--min-island", type=float, default=0.3, help="mm2: smaller untied islands are left (listed)")
    ap.add_argument("--pads", action="store_true",
                    help="also drop every SMD pad of the net that no track of it touches to a via of its own, "
                         "on one of the pad's axes, linked straight out of its centre")
    ap.add_argument("--diag", action="store_true",
                    help="--pads: where no axis site exists, try a straight 45-degree link (the guide's last "
                         "resort: each one is printed, to be declared in vialink_exceptions.json)")
    ap.add_argument("--only-pads", action="store_true", help="--pads, and no tie / ring / grid")
    ap.add_argument("--pad-list", default="", help="REF.PAD, comma separated: drop only these")
    ap.add_argument("--skip-pads", default="", help="REF or REF.PAD, comma separated: not dropped")
    ap.add_argument("--merge", action="store_true", help="write --plan's zones and keys into the output too")
    ap.add_argument("--group", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    a.board = R.P.board_dir_arg(a.board)
    B = R.RBoard(a.board)
    pn, b, mm, FM = B.pn, B.b, B.pn.ToMM, B.pn.FromMM
    pad_d, drill = (float(x) for x in a.via.split("/"))
    r = pad_d / 2
    gap = a.gap if a.gap is not None else float(B.gates.get("via_link", {}).get("gap", a.clearance))
    edge = mm(b.GetDesignSettings().m_CopperEdgeClearance)
    net = a.net
    full = next((n for n in b.GetNetsByName().keys() if R.short(str(n)) == net), None)
    if full is None:
        sys.exit(f"no net {net} on the board")
    full = str(full)

    plan = json.loads((Path(a.board) / a.plan).read_text() if a.plan and not Path(a.plan).exists()
                      else Path(a.plan).read_text()) if a.plan else {}
    if plan:
        B.write_plan(dict(plan, tracks=plan.get("tracks", []), vias=plan.get("vias", [])))
    with R.P.quiet_stderr():
        pn.ZONE_FILLER(b).Fill(b.Zones())

    cu = [B.canon(pn.BOARD.GetStandardLayerName(l)) for l in b.GetEnabledLayers().CuStack()]
    lid = {k: b.GetLayerID(k) for k in cu}
    err = FM(0.005)

    # the net's fill per layer, shrunk by the via's radius: a centre inside it puts the pad in the fill
    own_fill, pours = {}, set()
    for z in b.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != full:
            continue
        for l in z.GetLayerSet().CuStack():
            k = B.canon(pn.BOARD.GetStandardLayerName(l))
            pours.add(k)
            s = own_fill.setdefault(k, pn.SHAPE_POLY_SET())
            s.Append(z.GetFilledPolysList(l))
    inside = {}
    for k, s in own_fill.items():
        e = pn.SHAPE_POLY_SET(s)
        e.Simplify()
        e.Deflate(FM(r) + 1000, pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
        inside[k] = e

    # what a via may not touch, per layer: other nets' copper grown by r + clearance (on a layer the
    # net pours, the fill's own clearance is not tight enough alone -- a 45-degree fill edge let a
    # via stand 0.166 mm from a track; so every layer is tested against the copper itself)
    grow = FM(r + a.clearance)
    forbid = {k: pn.SHAPE_POLY_SET() for k in cu}
    own_grow = FM(r + a.own_gap)
    for t in b.GetTracks():
        if t.Type() == pn.PCB_VIA_T:
            continue                                         # vias: by --sep, below
        k = B.canon(t.GetLayerName())
        if t.GetNetname() == full and k in pours:
            continue                                         # the net's own track inside its pour
        t.TransformShapeToPolygon(forbid[k], t.GetLayer(), own_grow if t.GetNetname() == full else grow,
                                  err, pn.ERROR_OUTSIDE)
    for z in b.Zones():
        if z.GetIsRuleArea() or z.GetNetname() == full:
            continue
        for l in z.GetLayerSet().CuStack():
            k = B.canon(pn.BOARD.GetStandardLayerName(l))
            if k in forbid:
                f = pn.SHAPE_POLY_SET(z.GetFilledPolysList(l))
                f.Inflate(grow, pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
                forbid[k].Append(f)
    # every pad, any net, on any layer: the via-to-pad gap (and its hole)
    padz = pn.SHAPE_POLY_SET()
    padpolys = []                                            # (pad, its gap ring, the plain gap ring)
    tht_own = []                                             # the net's through-hole pins: they tie an island too
    for f in b.Footprints():
        for p in f.Pads():
            own = p.GetNetname() == full
            if own and p.GetAttribute() == pn.PAD_ATTRIB_PTH:
                tht_own.append(p)
            for k in cu:
                if p.IsOnLayer(lid[k]):
                    # the gap from every pad; from the net's own pads on a layer it does not pour, the
                    # notch distance as well
                    g_ = max(gap, a.clearance) if not own else (max(gap, a.own_gap) if k not in pours else gap)
                    p.TransformShapeToPolygon(padz, lid[k], FM(r + g_), err, pn.ERROR_OUTSIDE)
                    one = pn.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(one, lid[k], FM(r + g_), err, pn.ERROR_OUTSIDE)
                    near_gap = pn.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(near_gap, lid[k], FM(r + gap), err, pn.ERROR_OUTSIDE)
                    padpolys.append((p, one, near_gap))
            if p.GetDrillSize().x:                           # an NPTH / PTH hole on no copper layer
                h = pn.SHAPE_POLY_SET()
                p.TransformHoleToPolygon(h, FM(r + a.clearance), err, pn.ERROR_OUTSIDE)
                padz.Append(h)
    for k in forbid:
        forbid[k].Simplify()
    padz.Simplify()
    board = pn.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(board, True)
    inner = pn.SHAPE_POLY_SET(board)
    inner.Deflate(FM(edge + r), pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)

    vias = [(mm(t.GetPosition().x), mm(t.GetPosition().y), t.GetNetname() == full)
            for t in b.GetTracks() if t.Type() == pn.PCB_VIA_T]
    placed = []

    def V(x, y):
        return pn.VECTOR2I(FM(x), FM(y))

    def legal(x, y, ignore=None):
        """ignore: the pad this via is linked to -- only the plain via-to-pad gap applies to it"""
        v = V(x, y)
        if not inner.Contains(v):
            return False
        if padz.Contains(v):
            if ignore is None:
                return False
            for p_, one, near_gap in padpolys:
                if (near_gap if p_ is ignore else one).Contains(v):
                    return False
        if any(math.dist((x, y), (vx, vy)) < a.sep - 1e-6 for vx, vy, _o in vias):
            return False
        for k in cu:
            if k in pours and not inside[k].Contains(v):
                return False
            if forbid[k].Contains(v):
                return False
        # the links this run has laid: the net's own copper, at the notch distance
        if any(_dseg((x, y), a_, b_) < r + a.own_gap + w_ / 2 - 1e-6 for a_, b_, w_, pad_ in new_links
               if pad_ is not ignore):
            return False
        return True

    new_links = []                                           # (a, b, width, pad) laid by --pads

    def _dseg(p, a_, b_):
        dx, dy = b_[0] - a_[0], b_[1] - a_[1]
        L = dx * dx + dy * dy
        u = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a_[0]) * dx + (p[1] - a_[1]) * dy) / L))
        return math.dist(p, (a_[0] + u * dx, a_[1] + u * dy))

    spiral = sorted(((i * 0.05, j * 0.05) for i in range(-int(a.reach / 0.05), int(a.reach / 0.05) + 1)
                     for j in range(-int(a.reach / 0.05), int(a.reach / 0.05) + 1)
                     if math.hypot(i, j) * 0.05 <= a.reach), key=lambda d: math.hypot(*d))

    def place(x, y, why):
        for dx, dy in spiral:
            px, py = round(x + dx, 3), round(y + dy, 3)
            if legal(px, py):
                vias.append((px, py, True))
                placed.append({"net": full, "at": [px, py], "pad": pad_d, "drill": drill, "why": why})
                return True
        return False

    missed = []
    z_relief = max([z.GetThermalReliefGap() for z in b.Zones() if z.GetNetname() == full] or [0])
    # 1. tie every island that holds no via of the net
    for k in ([] if a.only_pads else sorted(pours)):
        s = own_fill[k]
        for i in range(s.OutlineCount()):
            isl = pn.SHAPE_POLY_SET()
            isl.AddOutline(s.Outline(i))
            for h in range(s.HoleCount(i)):
                isl.AddHole(s.Hole(i, h))
            if any(isl.Contains(V(vx, vy)) for vx, vy, own in vias if own):
                continue
            if any(isl.Collide(p.GetPosition(), FM(0.05) + max(p.GetSize().x, p.GetSize().y) // 2 + z_relief)
                   for p in tht_own if p.IsOnLayer(lid[k])):
                continue                                     # a through-hole pin of the net ties it
            area = isl.Area() / 1e12
            bb = isl.BBox()
            c = (mm(bb.Centre().x), mm(bb.Centre().y))
            if area < a.min_island:
                missed.append(f"tie {k} island {area:.2f} mm2 at ({c[0]:.2f}, {c[1]:.2f}): under --min-island")
                continue
            # the legal point of the island nearest its middle
            e = pn.SHAPE_POLY_SET(isl)
            e.Deflate(FM(r) + 1000, pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
            pts = []
            x0, y0, x1, y1 = mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom())
            y_ = y0
            while y_ <= y1:
                x_ = x0
                while x_ <= x1:
                    if e.Contains(V(x_, y_)):
                        pts.append((math.dist((x_, y_), c), round(x_, 3), round(y_, 3)))
                    x_ += 0.05
                y_ += 0.05
            for _d, px, py in sorted(pts):
                if legal(px, py):
                    vias.append((px, py, True))
                    placed.append({"net": full, "at": [px, py], "pad": pad_d, "drill": drill,
                                   "why": f"ties the {area:.1f} mm2 {k} island at ({c[0]:.1f}, {c[1]:.1f})"})
                    break
            else:
                missed.append(f"tie {k} island {area:.2f} mm2 at ({c[0]:.2f}, {c[1]:.2f}): no legal site")
    n_tie = len(placed)

    # 2. the ring: every Edge.Cuts contour, just inside the band, every --pitch mm
    off = edge + r + (a.inset if a.inset else r / 2 + 0.25)
    for i in range(0 if a.only_pads else board.OutlineCount()):
        rings = [board.Outline(i)] + [board.Hole(i, h) for h in range(board.HoleCount(i))]
        for ri, ln in enumerate(rings):
            ring = pn.SHAPE_POLY_SET()
            ring.AddOutline(ln)
            if ri == 0:
                ring.Deflate(FM(off), pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
            else:
                ring.Inflate(FM(off), pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
            for j in range(ring.OutlineCount()):
                c_ = ring.Outline(j)
                pts = [(mm(c_.CPoint(q).x), mm(c_.CPoint(q).y)) for q in range(c_.PointCount())]
                pts.append(pts[0])
                per = sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))
                nv = max(1, round(per / a.pitch))
                step, acc, seg = per / nv, 0.0, 0
                for kk in range(nv):
                    want = kk * step
                    while seg < len(pts) - 2 and acc + math.dist(pts[seg], pts[seg + 1]) < want:
                        acc += math.dist(pts[seg], pts[seg + 1])
                        seg += 1
                    p, q = pts[seg], pts[seg + 1]
                    L = math.dist(p, q) or 1.0
                    t = (want - acc) / L
                    x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
                    if any(math.dist((x, y), (vx, vy)) < a.pitch * 0.5 for vx, vy, own in vias if own):
                        continue                             # a via of the net already stitches here
                    if not place(x, y, "ring" if ri == 0 else "round the cut-out"):
                        missed.append(f"ring at ({x:.2f}, {y:.2f}): no legal site within {a.reach} mm")
    n_ring = len(placed) - n_tie

    # 3. the inside grid
    if a.grid and not a.only_pads:
        bb = board.BBox()
        x0, y0, x1, y1 = mm(bb.GetX()), mm(bb.GetY()), mm(bb.GetRight()), mm(bb.GetBottom())
        y = y0 + a.grid / 2
        while y < y1:
            x = x0 + a.grid / 2
            while x < x1:
                if inner.Contains(V(x, y)) and not any(math.dist((x, y), (vx, vy)) < a.grid * 0.5
                                                       for vx, vy, own in vias if own):
                    if not place(x, y, "grid"):
                        missed.append(f"grid at ({x:.2f}, {y:.2f}): no legal site within {a.reach} mm")
                x += a.grid
            y += a.grid
    n_grid = len(placed) - n_tie - n_ring

    # 4. SMD pads of the net that nothing of the net touches: a via on one of the pad's axes
    tracks_out, n_pad, diag45 = [], 0, []
    if a.pads or a.only_pads:
        skip = {q for q in a.skip_pads.split(",") if q}
        only = {q for q in a.pad_list.split(",") if q}
        cw = B.class_width(full) or 0.2
        # other nets' copper per layer, grown by the link's half width + clearance
        tf = {}
        def track_forbid(k):
            if k not in tf:
                g = FM(cw / 2 + a.clearance)
                s_ = pn.SHAPE_POLY_SET()
                for t in b.GetTracks():
                    if t.GetNetname() != full and (t.Type() == pn.PCB_VIA_T or B.canon(t.GetLayerName()) == k):
                        t.TransformShapeToPolygon(s_, lid[k], g, err, pn.ERROR_OUTSIDE)
                for f_ in b.Footprints():
                    for p_ in f_.Pads():
                        if p_.GetNetname() != full and p_.IsOnLayer(lid[k]):
                            p_.TransformShapeToPolygon(s_, lid[k], g, err, pn.ERROR_OUTSIDE)
                for z in b.Zones():
                    if not z.GetIsRuleArea() and z.GetNetname() != full and lid[k] in list(z.GetLayerSet().CuStack()):
                        f2 = pn.SHAPE_POLY_SET(z.GetFilledPolysList(lid[k]))
                        f2.Inflate(g, pn.CORNER_STRATEGY_ROUND_ALL_CORNERS, err)
                        s_.Append(f2)
                s_.Simplify()
                tf[k] = s_
            return tf[k]
        touched = set()
        for t in b.GetTracks():
            if t.GetNetname() == full and t.Type() != pn.PCB_VIA_T:
                for q in (t.GetStart(), t.GetEnd()):
                    touched.add((B.canon(t.GetLayerName()), q.x, q.y))
        for f_ in b.Footprints():
            for p_ in f_.Pads():
                key = f"{f_.GetReference()}.{p_.GetNumber()}"
                if p_.GetNetname() != full or p_.GetAttribute() != pn.PAD_ATTRIB_SMD or \
                        p_.HitTest(p_.GetPosition()) is False or \
                        f_.GetReference() in skip or key in skip or (only and key not in only):
                    continue
                k = "B.Cu" if p_.IsOnLayer(pn.B_Cu) and not p_.IsOnLayer(pn.F_Cu) else "F.Cu"
                if k in pours:
                    continue                                 # the pour reaches it
                if any(L_ == k and p_.HitTest(pn.VECTOR2I(x_, y_)) for L_, x_, y_ in touched):
                    continue                                 # already linked by a track of the net
                cx, cy = mm(p_.GetPosition().x), mm(p_.GetPosition().y)
                bb = p_.GetBoundingBox()
                ex, ey = mm(bb.GetWidth()) / 2, mm(bb.GetHeight()) / 2
                best = None
                for dx, dy, half, cross in ((1, 0, ex, 2 * ey), (-1, 0, ex, 2 * ey), (0, 1, ey, 2 * ex), (0, -1, ey, 2 * ex)):
                    w = round(min(cw, cross), 4)
                    d = half + gap + r
                    while d <= half + gap + r + a.reach + 1e-9:
                        x, y = round(cx + dx * d, 3), round(cy + dy * d, 3)
                        if legal(x, y, ignore=p_):
                            n_s = max(2, int(d / 0.05))
                            tfk = track_forbid(k)
                            if not any(tfk.Contains(V(cx + (x - cx) * i / n_s, cy + (y - cy) * i / n_s))
                                       for i in range(n_s + 1)):
                                if best is None or d < best[0]:
                                    best = (d, x, y, w)
                                break
                        d += 0.05
                diag = False
                if best is None and a.diag:
                    # the guide's "45-degree via link: last resort": a straight diagonal out of the centre
                    q = math.sqrt(0.5)
                    for dx, dy in ((q, q), (q, -q), (-q, q), (-q, -q)):
                        w = round(min(cw, 2 * min(ex, ey)), 4)
                        d = math.hypot(ex, ey) + gap + r
                        while d <= math.hypot(ex, ey) + gap + r + a.reach + 1e-9:
                            x, y = round(cx + dx * d, 3), round(cy + dy * d, 3)
                            if legal(x, y, ignore=p_):
                                n_s = max(2, int(d / 0.05))
                                tfk = track_forbid(k)
                                if not any(tfk.Contains(V(cx + (x - cx) * i / n_s, cy + (y - cy) * i / n_s))
                                           for i in range(n_s + 1)):
                                    if best is None or d < best[0]:
                                        best = (d, x, y, w)
                                    break
                            d += 0.05
                    diag = best is not None
                if best is None:
                    missed.append(f"pad {key}: no legal via on its axes" + (" or diagonals" if a.diag else "")
                                  + f" within {a.reach} mm")
                    continue
                d, x, y, w = best
                if diag:
                    diag45.append(f"{key} -> ({x}, {y})")
                vias.append((x, y, True))
                placed.append({"net": full, "at": [x, y], "pad": pad_d, "drill": drill, "why": f"{key} down"})
                tracks_out.append({"net": full, "layer": k, "width": w, "pts": [[round(cx, 4), round(cy, 4)], [x, y]]})
                new_links.append(((cx, cy), (x, y), w, p_))
                n_pad += 1

    out = dict(plan) if (a.merge and plan) else {}
    out["group"] = a.group or plan.get("group") or "stitch"
    why = (f"{net} stitching ({Path(__file__).name}): {n_tie} tie, {n_ring} ring at {a.pitch:g} mm"
           + (f", {n_grid} grid at {a.grid:g} mm" if a.grid else "")
           + (f", {n_pad} pad(s) dropped" if (a.pads or a.only_pads) else "") + f"; via {a.via}, sep {a.sep:g}")
    out["why"] = (plan.get("why", "") + " -- " if a.merge and plan else "") + why
    out["vias"] = (plan.get("vias", []) if a.merge and plan else []) + [
        {k: v for k, v in p.items() if k != "why"} for p in placed]
    out["tracks"] = (plan.get("tracks", []) if a.merge and plan else []) + tracks_out
    Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"{why}\n-> {a.out}")
    for m in diag45:
        print(f"  45-DEGREE {m}")
    for m in missed:
        print(f"  MISSED {m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
