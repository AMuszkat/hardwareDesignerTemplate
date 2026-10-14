#!/usr/bin/env python3
"""vfcore.py -- the via fixer's library (viafix.py, vianeed.py, entryfix.py), on
ONE board held in memory.

    import vfcore as K; K.init(board_path_or_(board, path), board_dir, G)   # then K.b, K.via_legal(...), ...

What it holds (all in KiCad internal units, nm):
  obstacles(net, layer, x, y)   other nets' pads, tracks, vias, net-tied polygons near a point, each with the STRICTER
                                of the two classes' clearances (KiCad's rule) -- [(shape, clearance, name, item)]
  blocked(path, w, ob)          the first obstacle a polyline of width w hits, or the board edge
  stacks(path, w, net, layer, rm)  a new segment lying on an old one of its own net (stacked copper)
  via_ctx / via_legal           is a via legal at a point on EVERY copper layer: clearances, holes (min_hole_to_hole),
                                own-net pads >= GAP + 0.01, the edge, rule areas.  via_blockers() lists every reason
  route_to(A, firsts, T, ...)   0/45-degree paths of 1-3 segments by route.py's joint rules (the end on a via is free)
  walk_out / reattach           move the end of a track that sits on a via to a new via place, walking back to an anchor
EDGE is the Edge.Cuts outline's box less the guide's "copper to edge" (a rectangular board; a board of another
shape keeps its bounding box here, and KiCad's DRC judges the real outline in route.py check)."""
import math
import common as C
pcbnew = C.pcbnew_module()                 # quiet import: wx debug lines stay off stderr
import vialink as VL

FM = pcbnew.FromMM; mm = pcbnew.ToMM; V = pcbnew.VECTOR2I
sh = C.short; uid = lambda x: x.m_Uuid.AsString(); P = lambda v: (v.x, v.y)
dist = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
b = None; GAP = 0.15; EDGE_MARGIN = 0.5; G = {}; FIELDS = []; FIELD_REFS = []


def init(board, board_dir, G_, gap=None):
    """Load BOARD (a path, or (loaded board, path)); G_ is the guide's rules."""
    global b, GAP, G, FIELDS, FIELD_REFS, EDGE_MARGIN, HOLE2HOLE, EDGE, CU, before, vbefore, pbefore
    import guide
    if isinstance(board, tuple):
        b, path = board
    else:
        path = board; b = pcbnew.LoadBoard(str(board))
    G = G_ or {}
    C.settings(str(path).rsplit("/", 1)[0] if "/" in str(path) else ".", board_dir)
    GAP = gap if gap is not None else (G.get("via_link") or {}).get("gap", 0.15)
    EDGE_MARGIN = (G.get("edge") or {}).get("min", mm(b.GetDesignSettings().m_CopperEdgeClearance))
    HOLE2HOLE = FM(C.HOLE2HOLE)
    FIELDS = guide.field_boxes(b, G, pcbnew); FIELD_REFS = [f["ref"] for f in G.get("field_vias", [])]
    ed = [d for d in b.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
    if not ed:
        C.die("no board outline on Edge.Cuts")
    x0 = min(d.GetBoundingBox().GetX() + d.GetWidth() // 2 for d in ed); y0 = min(d.GetBoundingBox().GetY() + d.GetWidth() // 2 for d in ed)
    x1 = max(d.GetBoundingBox().GetRight() - d.GetWidth() // 2 for d in ed); y1 = max(d.GetBoundingBox().GetBottom() - d.GetWidth() // 2 for d in ed)
    EDGE = (x0 + FM(EDGE_MARGIN), y0 + FM(EDGE_MARGIN), x1 - FM(EDGE_MARGIN), y1 - FM(EDGE_MARGIN))
    CU = list(b.GetEnabledLayers().CuStack())
    before = {segkey(t) for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T}
    vbefore = {viakey(v) for v in b.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    pbefore = {polykey(d) for d in polys_all()}
    return b


def cls(net): return C.cls(b, net)
clr = lambda net: C.clr(b, net); cw = lambda net: C.width(b, net)
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
SIDES = {(1, 0): 'x+', (-1, 0): 'x-', (0, 1): 'y+', (0, -1): 'y-'}
SNAP = 2000
def unit(d): L = math.hypot(*d); return (d[0] / L, d[1] / L)
def ang(u, v):
    u, v = unit(u), unit(v); return math.degrees(math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))))
near = lambda x, ks: any(abs(x - k) < 0.5 for k in ks)
def segkey(t):
    s_, e = t.GetStart(), t.GetEnd(); p = sorted([(s_.x, s_.y), (e.x, e.y)])
    return (t.GetNetname(), t.GetLayerName(), t.GetWidth(), p[0], p[1])
def viakey(v): return (v.GetNetname(), v.GetPosition().x, v.GetPosition().y, v.GetWidth(pcbnew.F_Cu), v.GetDrillValue())
def polykey(d): ps = d.GetPolyShape(); return (d.GetNetname(), d.GetLayerName(), ps.CVertex(0).x, ps.CVertex(0).y)
polys_all = lambda: [d for d in b.GetDrawings() if d.Type() == pcbnew.PCB_SHAPE_T and d.GetShape() == pcbnew.SHAPE_T_POLY and d.GetNetCode()]

def tracks(net, lid): return [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T and t.GetNetname() == net and t.GetLayer() == lid]
def obstacles(net, lid, cx, cy, half=3.5):
    x0, y0, x1, y1 = cx - FM(half), cy - FM(half), cx + FM(half), cy + FM(half)
    inwin = lambda bb: bb.GetRight() >= x0 and bb.GetLeft() <= x1 and bb.GetBottom() >= y0 and bb.GetTop() <= y1
    ob = []
    for f in b.GetFootprints():
        for q in f.Pads():
            if q.IsOnLayer(lid) and q.GetNetname() != net and inwin(q.GetBoundingBox()):
                ob.append((q.GetEffectiveShape(lid), FM(max(clr(net), clr(q.GetNetname()))), f.GetReference() + '.' + q.GetNumber(), q))
    for t in b.GetTracks():
        if t.GetNetname() == net or not inwin(t.GetBoundingBox()): continue
        if t.Type() == pcbnew.PCB_VIA_T or t.GetLayer() == lid:
            ob.append((t.GetEffectiveShape(lid), FM(max(clr(net), clr(t.GetNetname()))), sh(t.GetNetname()) + (' via' if t.Type() == pcbnew.PCB_VIA_T else ' track'), t))
    for d in polys_all():
        if d.GetNetname() != net and d.GetLayer() == lid and inwin(d.GetBoundingBox()):
            ob.append((d.GetEffectiveShape(lid), FM(max(clr(net), clr(d.GetNetname()))), sh(d.GetNetname()) + ' polygon', d))
    return ob
def blocked(path, w, ob):
    for i in range(len(path) - 1):
        (ax, ay), (bx, by) = path[i], path[i + 1]
        if min(ax, bx) - w // 2 < EDGE[0] or min(ay, by) - w // 2 < EDGE[1] or max(ax, bx) + w // 2 > EDGE[2] or max(ay, by) + w // 2 > EDGE[3]:
            return 'board edge'
        seg = pcbnew.SHAPE_SEGMENT(V(ax, ay), V(bx, by), w)
        for shp, c, name, *_ in ob:
            if shp.Collide(seg, c - FM(0.001)): return name
    return None
def stacks(path, w, net, lid, rm):
    for k in range(len(path) - 1):
        A_, B_ = path[k], path[k + 1]; d = (B_[0] - A_[0], B_[1] - A_[1]); L_ = math.hypot(*d)
        if L_ < 1: continue
        u_ = (d[0] / L_, d[1] / L_)
        for o in tracks(net, lid):
            if uid(o) in rm: continue
            C_, D_ = P(o.GetStart()), P(o.GetEnd()); e = (D_[0] - C_[0], D_[1] - C_[1]); Le = math.hypot(*e)
            if Le < 1 or abs(u_[0] * e[1] / Le - u_[1] * e[0] / Le) > 0.009: continue
            lat = abs((C_[0] - A_[0]) * u_[1] - (C_[1] - A_[1]) * u_[0])
            if lat >= (w + o.GetWidth()) / 2: continue
            if lat < 1000 and o.GetWidth() >= w: continue
            t1, t2 = sorted([(C_[0] - A_[0]) * u_[0] + (C_[1] - A_[1]) * u_[1], (D_[0] - A_[0]) * u_[0] + (D_[1] - A_[1]) * u_[1]])
            if min(L_, t2) - max(0.0, t1) > FM(0.05): return f'stacks on {sh(net)}'
    return None
def plen(path): return sum(dist(path[k], path[k + 1]) for k in range(len(path) - 1))

def via_ctx(v, net, cx, cy):
    holes = [(P(x.GetPosition()), x.GetDrillValue() // 2) for x in b.GetTracks() if x.Type() == pcbnew.PCB_VIA_T and uid(x) != uid(v)]
    holes += [(P(q.GetPosition()), max(q.GetDrillSize().x, q.GetDrillSize().y) // 2) for f in b.GetFootprints() for q in f.Pads() if q.GetDrillSize().x > 0]
    obs = {lid: obstacles(net, lid, cx, cy) for lid in CU}
    own = [(q, lid) for f in b.GetFootprints() for q in f.Pads() if q.GetNetname() == net for lid in (pcbnew.F_Cu, pcbnew.B_Cu) if q.IsOnLayer(lid)]
    rules = [z for z in b.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    return obs, holes, own, rules
def via_legal(v, Np, ctx):
    obs, holes, own, rules = ctx; r = v.GetWidth(pcbnew.F_Cu) // 2
    if Np[0] - r < EDGE[0] or Np[1] - r < EDGE[1] or Np[0] + r > EDGE[2] or Np[1] + r > EDGE[3]: return 'board edge'
    circ = pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r)
    for lid in CU:
        for shp, c, name, *_ in obs[lid]:
            if shp.Collide(circ, c - FM(0.001)): return name
    if any(dist(Np, hp) - hr - v.GetDrillValue() // 2 < HOLE2HOLE for hp, hr in holes): return 'hole to hole'
    for q, lid in own:
        if VL.touch_gap(q, lid, Np, r, GAP + 0.01) is not None: return f'touches {q.GetParentFootprint().GetReference()}.{q.GetNumber()}'
    if any(z.Outline().Contains(V(*Np)) for z in rules): return 'rule area'
    return None

def route_to(A, firsts, T, w, ob, net, lid, rm, lam_min):
    """paths A -> T in 1-3 segments, the first direction from `firsts`, every joint 0/45 (the end on a via is free)"""
    res = []; D = (T[0] - A[0], T[1] - A[1])
    def ok_len(k, g, mn): return k > 0 and k * math.hypot(*g) >= mn
    for g1 in firsts:
        # one segment
        if (D[0] == 0 or D[1] == 0 or abs(D[0]) == abs(D[1])) and (D[0] or D[1]) and near(ang(D, g1), (0,)) and math.hypot(*D) >= lam_min:
            res.append([A, T])
        for g2 in DIRS:
            if not near(ang(g1, g2), (45,)): continue
            det = g1[0] * g2[1] - g1[1] * g2[0]
            if det == 0: continue
            kn, mn_ = D[0] * g2[1] - D[1] * g2[0], g1[0] * D[1] - g1[1] * D[0]
            if kn % det == 0 and mn_ % det == 0:
                k1, k2 = kn // det, mn_ // det
                if ok_len(k1, g1, lam_min) and ok_len(k2, g2, FM(0.05)):
                    res.append([A, (A[0] + g1[0] * k1, A[1] + g1[1] * k1), T])
            # three segments: g1 for a set length, then g2, then g3
            for g3 in DIRS:
                if g3 == g2 or not near(ang(g2, g3), (45,)): continue
                det2 = g2[0] * g3[1] - g2[1] * g3[0]
                if det2 == 0: continue
                for L1 in (0.0, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5):
                    k1 = max(1, round((lam_min + FM(L1)) / math.hypot(*g1)))
                    O1 = (A[0] + g1[0] * k1, A[1] + g1[1] * k1); D2 = (T[0] - O1[0], T[1] - O1[1])
                    kn2, mn2 = D2[0] * g3[1] - D2[1] * g3[0], g2[0] * D2[1] - g2[1] * D2[0]
                    if kn2 % det2 or mn2 % det2: continue
                    k2, k3 = kn2 // det2, mn2 // det2
                    if ok_len(k2, g2, max(w, FM(0.1))) and ok_len(k3, g3, FM(0.05)):
                        res.append([A, O1, (O1[0] + g2[0] * k2, O1[1] + g2[1] * k2), T])
    good = []
    for path in res:
        if blocked(path, w, ob) or stacks(path, w, net, lid, rm): continue
        good.append((plen(path) / 1e6 + 0.15 * (len(path) - 2), path))
    return sorted(good)

def walk_out(t0, vp, r, net, lid, rm):
    """from a track ending on the via, walk away to anchors: [(kind, point, chain, info)]"""
    s_, e_ = P(t0.GetStart()), P(t0.GetEnd()); node = e_ if dist(s_, vp) <= r else s_
    chain, res = [t0], []
    for _ in range(4):
        cu = {uid(c) for c in chain} | rm
        ends = [o for o in tracks(net, lid) if uid(o) not in cu and (dist(P(o.GetStart()), node) < SNAP or dist(P(o.GetEnd()), node) < SNAP)]
        host = [o for o in tracks(net, lid) if uid(o) not in cu and uid(o) not in {uid(x) for x in ends} and o.HitTest(V(*node), 1)
                and dist(P(o.GetStart()), node) >= SNAP and dist(P(o.GetEnd()), node) >= SNAP]
        vv = next((x for x in b.GetTracks() if x.Type() == pcbnew.PCB_VIA_T and x.GetNetname() == net and dist(P(x.GetPosition()), node) <= x.GetWidth(pcbnew.F_Cu) / 2), None)
        pd = next((q for f in b.GetFootprints() for q in f.Pads() if q.GetNetname() == net and q.IsOnLayer(lid) and q.GetEffectivePolygon(lid).Contains(V(*node))), None)
        if vv: res.append(('via', P(vv.GetPosition()), list(chain), None)); break
        if pd: res.append(('pad', node, list(chain), pd)); break
        if host: res.append(('tee', node, list(chain), host[0])); break
        if len(ends) >= 2: res.append(('junction', node, list(chain), ends)); break
        if not ends: res.append(('end', node, list(chain), None)); break
        res.append(('joint', node, list(chain), ends))
        nxt = ends[0]
        if nxt.GetWidth() != t0.GetWidth(): break
        chain.append(nxt); node = P(nxt.GetEnd()) if dist(P(nxt.GetStart()), node) < SNAP else P(nxt.GetStart())
        if sum(c.GetLength() for c in chain) > FM(4.0): break
    return res
def firsts_at(kind, A, info):
    if kind in ('via',): return DIRS
    if kind in ('joint', 'junction'):
        kd = [unit((P(o.GetEnd())[0] - A[0], P(o.GetEnd())[1] - A[1])) if dist(P(o.GetStart()), A) < SNAP else
              unit((P(o.GetStart())[0] - A[0], P(o.GetStart())[1] - A[1])) for o in info]
    elif kind == 'tee':
        u = unit((P(info.GetEnd())[0] - P(info.GetStart())[0], P(info.GetEnd())[1] - P(info.GetStart())[1])); kd = [u, (-u[0], -u[1])]
    else: return []
    if len(kd) == 1: return [g for g in DIRS if near(ang(kd[0], g), (180, 135))]
    return [g for g in DIRS if all(near(ang(d, g), (180, 135, 45)) for d in kd)]

def reattach(t0, vp, r, Np, net, rm):
    """re-route the track t0 (ending on the via at vp) to end on Np instead: (new path, removed chain) or (None, why)"""
    lid = t0.GetLayer(); w = t0.GetWidth(); ob = obstacles(net, lid, Np[0], Np[1]); best = None; why = 'no anchor'
    for kind, A, chain, info in walk_out(t0, vp, r, net, lid, rm):
        if kind in ('end', 'pad'): why = f'{kind} anchor'; continue
        if kind == 'via' and dist(A, vp) <= r: why = 'the moving via itself'; continue      # never anchor on the via being moved
        cu = rm | {uid(c) for c in chain}
        if kind == 'tee':
            starts = [A]
        else:
            starts = [A]
        for St in starts:
            for c_, path in route_to(St, firsts_at(kind, St, info), Np, w, ob, net, lid, cu, max(w, FM(0.1)) if kind != 'via' else FM(0.05))[:1]:
                cost = c_ - sum(c.GetLength() for c in chain) / 1e6 + 0.05 * len(chain)
                if best is None or cost < best[0]: best = (cost, path, chain)
        if best is None: why = f'no clear path from the {kind} anchor'
    return (best[1], best[2]) if best else (None, why)


def via_blockers(v, Np, ctx, r=None):
    """EVERY reason a via (radius r, default v's) cannot sit at Np: [(kind, name, item, layer)] -- kind is 'edge',
    'copper' (another net's pad/track/via/polygon, item set), 'hole' (min_hole_to_hole, item the via or pad),
    'own-pad' (a pad of its net nearer than GAP + 0.01), 'rule' (a no-via rule area)"""
    obs, holes, own, rules = ctx; r = r or v.GetWidth(pcbnew.F_Cu) // 2; res = []
    if Np[0] - r < EDGE[0] or Np[1] - r < EDGE[1] or Np[0] + r > EDGE[2] or Np[1] + r > EDGE[3]: res.append(('edge', 'board edge', None, None))
    circ = pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r); seen = set()
    for lid in CU:
        for shp, c, name, it in obs[lid]:
            if shp.Collide(circ, c - FM(0.001)) and (uid(it), lid) not in seen:
                seen.add((uid(it), lid)); res.append(('copper', name, it, lid))
    hl = [x for x in b.GetTracks() if x.Type() == pcbnew.PCB_VIA_T and uid(x) != uid(v)] + \
         [q for f in b.GetFootprints() for q in f.Pads() if q.GetDrillSize().x > 0]
    for it in hl:
        hr = it.GetDrillValue() // 2 if it.Type() == pcbnew.PCB_VIA_T else max(it.GetDrillSize().x, it.GetDrillSize().y) // 2
        if dist(Np, P(it.GetPosition())) - hr - v.GetDrillValue() // 2 < HOLE2HOLE:
            res.append(('hole', (sh(it.GetNetname()) + ' via') if it.Type() == pcbnew.PCB_VIA_T else it.GetParentFootprint().GetReference() + '.' + it.GetNumber(), it, None))
    for q, lid in own:
        if VL.touch_gap(q, lid, Np, r, GAP + 0.01) is not None:
            res.append(('own-pad', f'{q.GetParentFootprint().GetReference()}.{q.GetNumber()}', q, lid))
    if any(z.Outline().Contains(V(*Np)) for z in rules): res.append(('rule', 'rule area', None, None))
    return res
