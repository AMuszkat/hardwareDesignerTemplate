#!/usr/bin/env python3
"""shape.py -- the copper-shape audit: sites where tracks, pads and polygons of
one net make a shape that is not a clean union.  None of these is a DRC error
or an angle error; all of them are copper the user saw and did not want.

    tools/kpy tools/copper/shape.py [--board B] [--out SITES.json] [--dump COPPER.json]
    tools/kpy tools/copper/shape.py --pcb scratch.kicad_pcb --guide <board dir> ...

Each site: kind, net, layer, a point, a size, the item uuids -- for the crop
(crops.py sites) and the fix (entryfix.py).  Importable: audit(b, pcbnew, ...)

  entry-corner  a track enters a rectangular SMD pad across one of its corners
  entry-wide    a track >= 0.1 wider than the pad it ends in (ears)
  entry-offset  an axial entry whose stadium is not inside the pad's cross dimension (a step)
  cap-out       a track end inside a pad whose round cap sticks out of all copper of the net
  bulb          a free end where every continuation is >= 0.3 narrower (a bus ending in a round cap)
  overlap-end   a track end inside another same-net segment, off its centreline, cap showing
  stacked       two parallel same-net segments overlapping lengthwise, not hidden by a pad
  nub           morphology: a convex sliver thinner than 2*OPEN
  notch         morphology: a concave wedge or slot narrower than 2*CLOSE and longer than 2*CLOSE
  poly-neck     a net-tied polygon that falls apart when shrunk by half its class width
  poly-pour     a polygon on an outer layer touching a fill of its own net (solid into the pour)

Exempt: a straight 45-degree via link (vialink.py lists it), a round via end,
the courtyards of the guide's `via field` parts (a BGA's dog-bone V is the
escape's own shape) and any (net, layer) that has a pour of that net (the pour
fills the voids; pour.py judges it).  Pieces under --area mm^2, or thinner on
average than 0.01 mm, are polygoniser noise and dropped.  OPEN must stay under
half the thinnest track or the opening eats the track itself.  The .kicad_pro
must sit beside the board (class widths)."""
import argparse
import collections
import json
import math
import sys

import common as CM


def audit(b, pcbnew, LAYERS=None, OPEN=0.055, CLOSE=0.15, AMIN=0.003, FIELDS=(), dump=None):
    """The sites of the loaded board.  LAYERS: copper layer names (default all)."""
    mm = pcbnew.ToMM; FM = pcbnew.FromMM; sh = CM.short
    LAYERS = LAYERS or CM.copper_layers(b, pcbnew)
    ERR = FM(0.005); RND = pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS
    infield = lambda x, y: any(mm(x0) <= x <= mm(x1) and mm(y0) <= y <= mm(y1) for x0, y0, x1, y1 in FIELDS)
    SNAP = 0.002
    sites = []
    def site(kind, net, L, x, y, size, uuids, **kw):
        sites.append(dict(kind=kind, net=net, layer=L, at=[round(x, 3), round(y, 3)], size=round(size, 3), field=infield(x, y),
                          uuids=uuids, **kw))

    # ---- items per (net, layer) ----
    pads = collections.defaultdict(list); segs = collections.defaultdict(list); vias = collections.defaultdict(list)
    for f in b.Footprints():
        for p in f.Pads():
            if not p.GetNumber() or not p.GetNetCode(): continue
            for L in LAYERS:
                lid = b.GetLayerID(L)
                if p.IsOnLayer(lid): pads[(p.GetNetCode(), L)].append((f.GetReference() + '.' + p.GetNumber(), p, lid))
    for t in b.GetTracks():
        if not t.GetNetCode(): continue
        if t.Type() == pcbnew.PCB_VIA_T:
            for L in LAYERS:
                lid = b.GetLayerID(L)
                if t.IsOnLayer(lid): vias[(t.GetNetCode(), L)].append((t, lid))
        elif t.Type() == pcbnew.PCB_TRACE_T:
            L = t.GetLayerName()
            if L in LAYERS and t.GetStart() != t.GetEnd(): segs[(t.GetNetCode(), L)].append(t)
    polys = collections.defaultdict(list)       # net-tied copper polygons (poly.py): copper of the net, judged like a pad
    for d in b.GetDrawings():
        if d.Type() == pcbnew.PCB_SHAPE_T and d.GetNetCode() and d.GetLayerName() in LAYERS: polys[(d.GetNetCode(), d.GetLayerName())].append(d)
    netname = lambda code: sh(b.FindNet(code).GetNetname())

    P = lambda v: (mm(v.x), mm(v.y))
    def pt_seg(p, a_, b_):     # distance from p to segment a-b, and the parameter t of the foot
        ax, ay = a_; bx, by = b_; px, py = p
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy)), t
    def pt_line(p, a_, b_):    # unsigned distance from p to the infinite line a-b
        ax, ay = a_; bx, by = b_; px, py = p
        dx, dy = bx - ax, by - ay
        return abs(dx * (py - ay) - dy * (px - ax)) / math.hypot(dx, dy)

    def pad_poly(p, lid):
        ps = pcbnew.SHAPE_POLY_SET(); p.TransformShapeToPolygon(ps, lid, 0, ERR, pcbnew.ERROR_INSIDE); return ps
    def inside(ps, x, y): return ps.Contains(pcbnew.VECTOR2I(FM(x), FM(y)))
    def gap(ps, q):            # distance from a point outside the polygon to its copper, by bisection on the collision clearance
        v = pcbnew.VECTOR2I(FM(q[0]), FM(q[1])); lo, hi = 0, 2000
        while hi - lo > 1:
            m = (lo + hi) // 2
            if ps.Collide(v, FM(m / 1000.0)): hi = m
            else: lo = m
        return hi / 1000.0

    skipped = collections.Counter()
    PADPOLY = {}
    def covered(code, L, q, me):   # is the point inside some copper of the net on this layer other than segment `me`
        for v, lid in vias[(code, L)]:
            vx, vy = P(v.GetPosition())
            if math.hypot(vx - q[0], vy - q[1]) <= mm(v.GetWidth(lid)) / 2: return True
        for name, p, lid in pads[(code, L)]:
            k = (name, L)
            if k not in PADPOLY: PADPOLY[k] = pad_poly(p, lid)
            if inside(PADPOLY[k], *q): return True
        for o in segs[(code, L)]:
            if o is me: continue
            d, tt = pt_seg(q, P(o.GetStart()), P(o.GetEnd()))
            if d <= mm(o.GetWidth()) / 2: return True
        for d_ in polys[(code, L)]:
            if d_.GetPolyShape().Contains(pcbnew.VECTOR2I(FM(q[0]), FM(q[1]))): return True
        return False

    # ---- rules 1-4: track ends against the pad or via they end in ----
    for (code, L), ss in segs.items():
        net = netname(code)
        pp = [(name, p, lid, pad_poly(p, lid)) for name, p, lid in pads[(code, L)]]
        vv = vias[(code, L)]
        for t in ss:
            w = mm(t.GetWidth()); A, B = P(t.GetStart()), P(t.GetEnd())
            for end, other in ((A, B), (B, A)):
                dx, dy = other[0] - end[0], other[1] - end[1]; phi = math.degrees(math.atan2(dy, dx))
                # via first: an end on a via is free of angle; only the width is judged
                hit_v = None
                for v, lid in vv:
                    vx, vy = P(v.GetPosition()); r = mm(v.GetWidth(lid)) / 2
                    if math.hypot(vx - end[0], vy - end[1]) <= r + 1e-6: hit_v = (v, r); break
                if hit_v: continue
                if any(d_.GetPolyShape().Contains(pcbnew.VECTOR2I(FM(end[0]), FM(end[1]))) for d_ in polys[(code, L)]): continue
                for name, p, lid, ps in pp:
                    if not inside(ps, *end): continue
                    shape = p.GetShape(lid)
                    sx, sy = mm(p.GetSize(lid).x), mm(p.GetSize(lid).y); th = p.GetOrientation().AsDegrees()
                    cx, cy_ = P(p.GetPosition())
                    if shape == pcbnew.PAD_SHAPE_CIRCLE or (shape == pcbnew.PAD_SHAPE_OVAL and abs(sx - sy) < 1e-6):
                        if w > sx + 0.1: site('entry-wide', net, L, *end, w - sx, [t.m_Uuid.AsString()], into=name, w=w, cross=sx)
                    else:
                        rel = (phi - th) % 90.0; rel = min(rel, 90 - rel)
                        axial = rel <= 0.5
                        Ln = math.hypot(dx, dy); ux, uy = dx / Ln, dy / Ln; nx, ny = -uy, ux
                        def side(q):      # which pad side a boundary point is on, in the pad's frame
                            c, s_ = math.cos(math.radians(-th)), math.sin(math.radians(-th))
                            qx, qy = q[0] - cx, q[1] - cy_; lx, ly = qx * c - qy * s_, qx * s_ + qy * c
                            ex, ey = abs(lx) - sx / 2, abs(ly) - sy / 2
                            return ('x+' if lx > 0 else 'x-') if ex > ey else ('y+' if ly > 0 else 'y-')
                        def exit_pt(q0):  # walk from q0 along u until outside the pad
                            if not inside(ps, *q0): return q0
                            k = 0.0
                            while k < Ln and inside(ps, q0[0] + ux * k, q0[1] + uy * k): k += 0.01
                            return (q0[0] + ux * k, q0[1] + uy * k)
                        sides = {side(exit_pt((end[0] + nx * h, end[1] + ny * h))) for h in (w / 2 - 0.002, -(w / 2 - 0.002))}
                        along_x = abs(((phi - th) % 180.0) - 90) > 45
                        cross = sy if along_x else sx
                        off = pt_line((cx, cy_), end, other)
                        if axial and w > cross + 0.1:
                            site('entry-wide', net, L, *end, w - cross, [t.m_Uuid.AsString()], into=name, w=w, cross=cross)
                        elif axial and off + w / 2 - cross / 2 >= 0.05:
                            site('entry-offset', net, L, *end, off + w / 2 - cross / 2, [t.m_Uuid.AsString()], into=name, w=w, cross=cross, off=round(off, 3))
                        elif not axial and len(sides) > 1:
                            sd = sorted(sides)
                            if sd[0][0] != sd[1][0]:          # adjacent sides: the pad corner between them is the one swept
                                c, s_ = math.cos(math.radians(th)), math.sin(math.radians(th))
                                lx, ly = (1 if 'x+' in sides else -1), (1 if 'y+' in sides else -1)
                                ddx, ddy = (lx * c - ly * s_) / math.sqrt(2), (lx * s_ + ly * c) / math.sqrt(2)
                                o_ = ps.Outline(0); best = None
                                for i in range(o_.PointCount()):
                                    q = (mm(o_.CPoint(i).x), mm(o_.CPoint(i).y)); dv = (q[0] - cx) * ddx + (q[1] - cy_) * ddy
                                    if best is None or dv > best[0]: best = (dv, q)
                                corner = best[1]; gapc = pt_line(corner, end, other) - w / 2
                                if abs(gapc) >= 0.05:
                                    # a via abutting the pad's corner, linked at 45 degrees with the band centred on the corner, is the
                                    # clean bridge (R17b, 2026-10-13): its edges meet the pad's two sides at 135 degrees
                                    # a straight 45-degree via link -- pad centre to via centre -- is the via-link rule's declared last
                                    # resort (vialink.py lists every one as via-45); it is not counted twice here
                                    bv = next(((P(v.GetPosition()), mm(v.GetWidth(vl)) / 2) for v, vl in vv
                                               if math.hypot(P(v.GetPosition())[0] - other[0], P(v.GetPosition())[1] - other[1]) <= 0.005), None)
                                    if bv and abs(rel - 45) <= 0.5 and math.hypot(end[0] - cx, end[1] - cy_) <= 0.005:
                                        skipped['via-45 link (vialink.py lists it)'] += 1
                                    else:
                                        site('entry-corner', net, L, *end, abs(gapc), [t.m_Uuid.AsString()], into=name, w=w, angle=round(rel, 1), sides=sd,
                                             corner_out=round(gapc, 3))
                    # the end cap: 16 points on the circle of radius w/2 - 2um must all be inside the pad, unless another
                    # same-net segment continues from this end (then the cap is covered by the joint)
                    cont = any(o is not t and (math.hypot(P(o.GetStart())[0] - end[0], P(o.GetStart())[1] - end[1]) < SNAP or
                                                math.hypot(P(o.GetEnd())[0] - end[0], P(o.GetEnd())[1] - end[1]) < SNAP) for o in ss)
                    if not cont:
                        r = w / 2 - 0.002; Ln = math.hypot(dx, dy); ux, uy = dx / Ln, dy / Ln
                        pts = [(end[0] + r * math.cos(k * math.pi / 8), end[1] + r * math.sin(k * math.pi / 8)) for k in range(16)]
                        rear = [q for q in pts if (q[0] - end[0]) * ux + (q[1] - end[1]) * uy < -1e-9]
                        outside = [q for q in rear if not inside(ps, *q) and not covered(code, L, q, t)]
                        if outside:
                            prot = max(gap(ps, q) for q in outside)
                            if prot >= 0.05:
                                site('cap-out', net, L, *end, prot, [t.m_Uuid.AsString()], into=name, w=w, pts_out=len(outside))
                    break

    # ---- rules 5-6: track ends and bodies against other segments ----
    for (code, L), ss in segs.items():
        net = netname(code)
        pp = [(name, p, lid, pad_poly(p, lid)) for name, p, lid in pads[(code, L)]]
        vv = [(P(v.GetPosition()), mm(v.GetWidth(lid)) / 2) for v, lid in vias[(code, L)]]
        def free(pt):
            return any(math.hypot(vx - pt[0], vy - pt[1]) <= r for (vx, vy), r in vv) or any(inside(ps, *pt) for _, _, _, ps in pp) or \
                any(d_.GetPolyShape().Contains(pcbnew.VECTOR2I(FM(pt[0]), FM(pt[1]))) for d_ in polys[(code, L)])
        geo = [(t, P(t.GetStart()), P(t.GetEnd()), mm(t.GetWidth())) for t in ss]
        for i, (t, A, B, w) in enumerate(geo):
            for end in (A, B):
                if free(end): continue
                cont = [wo for j, (o, C, D, wo) in enumerate(geo) if j != i and
                        (math.hypot(C[0] - end[0], C[1] - end[1]) < SNAP or math.hypot(D[0] - end[0], D[1] - end[1]) < SNAP)]
                if cont and max(cont) <= w - 0.3:
                    site('bulb', net, L, *end, w - max(cont), [t.m_Uuid.AsString()], w=w, w_next=max(cont))
                for j, (o, C, D, wo) in enumerate(geo):
                    if i == j: continue
                    if math.hypot(C[0] - end[0], C[1] - end[1]) < SNAP or math.hypot(D[0] - end[0], D[1] - end[1]) < SNAP: continue
                    d, tt = pt_seg(end, C, D)
                    if d < wo / 2 - 1e-6 and d > SNAP and 0 < tt < 1 and d + w / 2 - wo / 2 >= 0.03:
                        # only the part of the cap that NO other copper of the net covers shows (R17a, 2026-10-13: a joint's
                        # end lying inside a third track was reported although the continuing segment hides its cap)
                        r = w / 2 - 0.002
                        cap = [(end[0] + r * math.cos(k * math.pi / 16), end[1] + r * math.sin(k * math.pi / 16)) for k in range(32)]
                        shown = [q for q in cap if pt_seg(q, C, D)[0] > wo / 2 + 1e-6 and not covered(code, L, q, t)]
                        prot = max((pt_seg(q, C, D)[0] - wo / 2 for q in shown), default=0.0)
                        if prot < 0.03: skipped['overlap-end hidden by other copper'] += 1; continue
                        site('overlap-end', net, L, *end, prot, [t.m_Uuid.AsString(), o.m_Uuid.AsString()], w=w, w_other=wo, off=round(d, 3))
        for i, (t, A, B, w) in enumerate(geo):
            for j, (o, C, D, wo) in enumerate(geo):
                if j <= i: continue
                ang = abs(math.degrees(math.atan2(B[1] - A[1], B[0] - A[0])) - math.degrees(math.atan2(D[1] - C[1], D[0] - C[0]))) % 180
                if min(ang, 180 - ang) > 0.5: continue
                if pt_line(C, A, B) >= (w + wo) / 2 - 1e-6: continue
                # projection overlap along A-B
                ux, uy = B[0] - A[0], B[1] - A[1]; Ln = math.hypot(ux, uy); ux, uy = ux / Ln, uy / Ln
                s = sorted([((C[0] - A[0]) * ux + (C[1] - A[1]) * uy), ((D[0] - A[0]) * ux + (D[1] - A[1]) * uy)])
                lo, hi = max(0.0, s[0]), min(Ln, s[1])
                if hi - lo > 0.02:
                    vis = [k for k in range(int((hi - lo) / 0.02) + 1) if not free((A[0] + ux * (lo + k * 0.02), A[1] + uy * (lo + k * 0.02)))]
                    if len(vis) * 0.02 < 0.05: skipped['stacked hidden in a pad'] += 1; continue
                    k0 = vis[len(vis) // 2]; mx, my = A[0] + ux * (lo + k0 * 0.02), A[1] + uy * (lo + k0 * 0.02)
                    site('stacked', net, L, mx, my, len(vis) * 0.02, [t.m_Uuid.AsString(), o.m_Uuid.AsString()], w=w, w_other=wo, overlap=round(hi - lo, 2))

    # ---- rules 7-8: morphology of the union ----
    def union(code, L):
        ps = pcbnew.SHAPE_POLY_SET()
        for name, p, lid in pads[(code, L)]: p.TransformShapeToPolygon(ps, lid, 0, ERR, pcbnew.ERROR_INSIDE)
        for t in segs[(code, L)]: t.TransformShapeToPolygon(ps, t.GetLayer(), 0, ERR, pcbnew.ERROR_INSIDE)
        for v, lid in vias[(code, L)]: v.TransformShapeToPolygon(ps, lid, 0, ERR, pcbnew.ERROR_INSIDE)
        for d_ in polys[(code, L)]: ps.Append(d_.GetPolyShape())
        ps.Simplify(); return ps
    poured = {(z.GetNetCode(), b.GetLayerName(l)) for z in b.Zones() if not z.GetIsRuleArea() for l in z.GetLayerSet().Seq()}
    def pieces(ps, kind, net, L, minsize):
        ps.Simplify()
        for k in range(ps.OutlineCount()):
            o = ps.Outline(k); area = abs(o.Area()) / 1e12
            if area < AMIN: continue
            per = sum(math.hypot(o.CPoint((i + 1) % o.PointCount()).x - o.CPoint(i).x, o.CPoint((i + 1) % o.PointCount()).y - o.CPoint(i).y)
                      for i in range(o.PointCount())) / 1e6
            if per > 0 and 2 * area / per < 0.01:          # a hairline of a few um along a diagonal: polygonisation, not copper (R17c)
                skipped['polygon noise (a hairline, mean width < 0.01)'] += 1; continue
            bb = o.BBox(); x, y = mm(bb.GetX() + bb.GetWidth() / 2), mm(bb.GetY() + bb.GetHeight() / 2)
            size = max(mm(bb.GetWidth()), mm(bb.GetHeight()))
            if size < minsize: continue
            if infield(x, y): skipped[kind + ' in field'] += 1; continue
            if kind == 'notch':
                grown = pcbnew.SHAPE_POLY_SET(); grown.AddOutline(o); grown.Inflate(FM(0.01), RND, ERR)
                npad = sum(1 for name, p, lid in pads[(cur_code, L)] if grown.Collide(PADPOLY.setdefault((name, L), pad_poly(p, lid)), 0))
                nvia = sum(1 for v, lid in vias[(cur_code, L)] if grown.Collide(pcbnew.VECTOR2I(FM(P(v.GetPosition())[0]), FM(P(v.GetPosition())[1])), v.GetWidth(lid) // 2))
                if npad >= 2: skipped['notch between two pads (footprint geometry)'] += 1; continue
                wall, apex = -1, False
                if size < 4 * CLOSE:
                    verts = [(mm(o.CPoint(i).x), mm(o.CPoint(i).y)) for i in range(o.PointCount())]
                    apex = any(math.hypot(jx - vx, jy - vy) < 0.35 for jx, jy in joints[(cur_code, L)] for vx, vy in verts)   # the void's apex sits w/2*cot(22.5) past the joint
                    # share of the piece's wall that is a pad or via: a tee's wedge only brushes a pad at its open end,
                    # a pocket beside a via has the via for a wall (measured: tee 0.25-0.33, via pocket 0.42-0.5)
                    def onpv(q):
                        v = pcbnew.VECTOR2I(FM(q[0]), FM(q[1]))
                        return any(PADPOLY.setdefault((name, L), pad_poly(p, lid)).Collide(v, FM(0.02)) for name, p, lid in pads[(cur_code, L)]) or \
                               any(math.hypot(P(vv.GetPosition())[0] - q[0], P(vv.GetPosition())[1] - q[1]) <= mm(vv.GetWidth(lid)) / 2 + 0.02 for vv, lid in vias[(cur_code, L)])
                    wall = sum(1 for q in verts if onpv(q)) / max(1, len(verts))
                    if (apex and wall < 0.4) or (not npad and not nvia): skipped['notch at a track joint (a tee or a bend)'] += 1; continue
            site(kind, net, L, x, y, max(mm(bb.GetWidth()), mm(bb.GetHeight())), [], area=round(area, 4), wall=round(wall, 2), apex=apex,
                 poly=[[round(mm(o.CPoint(i).x), 3), round(mm(o.CPoint(i).y), 3)] for i in range(o.PointCount())])
    joints = collections.defaultdict(list)       # every point where two same-net segments share an end, or one ends on another
    for (code, L), ss in segs.items():
        ends = [(P(t.GetStart()), P(t.GetEnd()), mm(t.GetWidth())) for t in ss]
        for i, (A, B, w) in enumerate(ends):
            for e in (A, B):
                for j, (C, D, wo) in enumerate(ends):
                    if i == j: continue
                    if math.hypot(C[0] - e[0], C[1] - e[1]) < SNAP or math.hypot(D[0] - e[0], D[1] - e[1]) < SNAP or pt_seg(e, C, D)[0] < SNAP:
                        joints[(code, L)].append(e); break
    keys = sorted(set(segs) | set(pads), key=lambda k: (k[1], k[0]))
    for code, L in keys:
        if not segs.get((code, L)): continue                 # a net with no track on this layer has only pads: nothing to judge
        net = netname(code); cur_code = code
        if (code, L) in poured: skipped['poured ' + net + ' ' + L] += 1; continue
        U = union(code, L)
        op = pcbnew.SHAPE_POLY_SET(U); op.Deflate(FM(OPEN), RND, ERR); op.Inflate(FM(OPEN), RND, ERR)
        nub = pcbnew.SHAPE_POLY_SET(U); nub.BooleanSubtract(op); pieces(nub, 'nub', net, L, 0)
        cl = pcbnew.SHAPE_POLY_SET(U); cl.Inflate(FM(CLOSE), RND, ERR); cl.Deflate(FM(CLOSE), RND, ERR)
        notch = pcbnew.SHAPE_POLY_SET(cl); notch.BooleanSubtract(U); pieces(notch, 'notch', net, L, 2 * CLOSE)

    fills = collections.defaultdict(list)
    for z in b.Zones():
        if z.GetIsRuleArea(): continue
        for l in z.GetLayerSet().Seq(): fills[(sh(z.GetNetname()), b.GetLayerName(l))].append(z.GetFilledPolysList(l))

    # ---- rules 9-10: the net-tied polygons themselves (10_layout #17, rev 141) ----
    npoly = 0
    for (code, L), dl in polys.items():
        net = netname(code); w = CM.width(b, b.FindNet(code).GetNetname())
        for d_ in dl:
            npoly += 1
            bb = d_.GetBoundingBox(); x, y = mm(bb.GetX() + bb.GetWidth() / 2), mm(bb.GetY() + bb.GetHeight() / 2)
            core = pcbnew.SHAPE_POLY_SET(d_.GetPolyShape()); core.Deflate(FM(w / 2 - 0.005), RND, ERR); core.Simplify()
            parts = [k for k in range(core.OutlineCount()) if abs(core.Outline(k).Area()) / 1e12 >= 1e-5]
            if len(parts) > 1:
                site('poly-neck', net, L, x, y, w, [d_.m_Uuid.AsString()], w_class=w, parts=len(parts))
            if L in ('F.Cu', 'B.Cu'):
                grown = pcbnew.SHAPE_POLY_SET(d_.GetPolyShape()); grown.Inflate(FM(0.02), RND, ERR)
                for f in fills.get((net, L), []):
                    hit = pcbnew.SHAPE_POLY_SET(grown); hit.BooleanIntersection(f)
                    if hit.OutlineCount() and abs(hit.Area()) / 1e12 >= 1e-4:
                        site('poly-pour', net, L, x, y, abs(hit.Area()) / 1e12, [d_.m_Uuid.AsString()]); break
    skipped['polygons judged (neck, pour)'] += npoly

    seen = set(); uniq = []
    for s in sites:
        k = (s['kind'], s['layer'], s['net'], tuple(s['at']))
        if k in seen: continue
        if s['kind'] != 'poly-pour' and any(f.Contains(pcbnew.VECTOR2I(FM(s['at'][0]), FM(s['at'][1]))) for f in fills.get((s['net'], s['layer']), [])):
            skipped['under own pour'] += 1; continue
        seen.add(k); uniq.append(s)
    sites = uniq
    if dump:                 # every net's copper union per layer (no fills) + pad labels, for crops.py
        D = dict(layers=LAYERS, copper=[], pads=[])
        for code, L in keys:
            U = union(code, L); U.Fracture()
            for k in range(U.OutlineCount()):
                o = U.Outline(k)
                D['copper'].append(dict(net=netname(code), layer=L, poly=[[round(mm(o.CPoint(i).x), 3), round(mm(o.CPoint(i).y), 3)] for i in range(o.PointCount())]))
        for (code, L), pl in pads.items():
            for name, p, lid in pl:
                D['pads'].append(dict(name=name, net=netname(code), layer=L, at=[round(v, 3) for v in P(p.GetPosition())]))
        json.dump(D, open(dump, 'w'))

    return sites


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    CM.add_board_args(ap)
    ap.add_argument("--out", help="sites JSON (default <board>/routing/shape_sites.json)")
    ap.add_argument("--dump", help="also write every net's copper union, for crops.py sites")
    ap.add_argument("--layers", help="comma-separated copper layers (default: all)")
    ap.add_argument("--open", type=float, default=0.055)
    ap.add_argument("--close", type=float, default=0.15)
    ap.add_argument("--area", type=float, default=0.003)
    a = CM.args(ap)
    b = CM.load(a)
    pcbnew = CM.pcbnew_module()
    import guide
    fields = guide.field_boxes(b, CM.G, pcbnew)
    sites = audit(b, pcbnew, a.layers.split(",") if a.layers else None, a.open, a.close, a.area, fields,
                  str(CM.out_in_board(a, a.dump)) if a.dump else None)
    out = CM.out_path(a, "shape_sites.json")
    json.dump(sites, open(out, "w"), indent=1)
    cnt = collections.Counter(s["kind"] for s in sites)
    print(f"{len(sites)} shape sites: " + (", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) or "none") + f"  -> {out}")
    for s in sorted(sites, key=lambda s: (s["kind"], s["layer"], s["net"])):
        extra = {k: v for k, v in s.items() if k not in ("kind", "net", "layer", "at", "size", "uuids", "poly", "field")}
        print(f"  {s['kind']:12s} {s['layer']:6s} {s['net']:14s} {s['at']} size {s['size']}  {extra}")
    return 1 if sites else 0


if __name__ == "__main__":
    sys.exit(main())
