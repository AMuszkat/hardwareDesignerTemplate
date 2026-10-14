#!/usr/bin/env python3
"""shove.py -- push other nets' tracks and vias out of the way of a shape (a via's new place, a link), on the board
held by vfcore (K.b), with undo.  Used by viafix.py --push.

    import vfcore as K, shove as SH
    U = SH.Undo(); ok = SH.clear(shape, net, layers, U, hole=(centre, drill_radius))   # shape: pcbnew SHAPE_SEGMENT
    ...; U.rollback() undoes everything, U.rollback(m) back to a checkpoint m = U.mark()

A TRACK is pushed by translating it -- and, where it must, the chain it belongs to -- perpendicular to itself, away
from the shape, by the least of STEP .. MAXPUSH mm that clears it at the STRICTER of the two classes' clearances; when
no translation works, by a local BUMP: a 45-degree jog out, a run parallel to the track past the shape, a jog back.
Every joint keeps its angle (the guide's joint rule), because each segment keeps its direction:
  * a chain moves rigidly; at each OUTER end of it
      - a plain joint to one segment U SLIDES along U (U changes length only); where U would get shorter than allowed
        or reverse, or is parallel to the chain's end, U joins the chain and moves too (at most MAXCHAIN segments);
      - a pad, a via or a junction at the end stays put and the chain is met by ONE 45-degree jog (at a junction the
        jog must meet the other segments at 45, 135 or 180 degrees); a tee (an end in the middle of a segment) is
        refused; where the push runs along the end segment it simply gets longer or shorter;
  * a diagonal keeps at least one track width, nothing shrinks below MINSEG or reverses.
A VIA of another net is pushed straight away from the shape, and every track on it re-attached by vfcore.reattach
(0/45, from its anchor) -- but never a via linked to a pad (its own link must stay straight), a
dog-bone in a FROZEN box, or a via of a FROZEN class or net.
New copper is checked against every other net's pad, polygon and the edge (a hit fails that offset), against the
shapes already cleared, and against other nets' tracks and vias, which are pushed in turn, at most DEPTH deep."""
import math
import time
import common as CM
import vfcore as K
pcbnew = CM.pcbnew_module()
import vialink as VL
MAXPUSH = 0.6; DEPTH = 3; STEP = 0.025; MINSEG = 0.05; MAXCHAIN = 8
BUDGET = 20.0                              # seconds per clear(): a cascade that has not closed by then gives up
_deadline = [0.0]
# Copper never pushed: the guide's `frozen` row (classes, net-name prefixes) and
# the courtyards of its `via field` parts.  configure() sets them from vfcore.
FROZEN_CLASSES = set()
FROZEN_NETS = ()
FROZEN_BOXES = []


def configure():
    global FROZEN_CLASSES, FROZEN_NETS, FROZEN_BOXES
    fr = K.G.get("frozen") or {}
    FROZEN_CLASSES = set(fr.get("classes", [])); FROZEN_NETS = tuple(fr.get("nets", []))
    FROZEN_BOXES = list(K.FIELDS)

class Undo:
    def __init__(self): self.log = []
    def mark(self): return len(self.log)
    def remove(self, it): K.b.Remove(it); self.log.append(('rm', it, None))
    def add(self, it): K.b.Add(it); self.log.append(('add', it, None))
    def move(self, v, p): self.log.append(('pos', v, K.P(v.GetPosition()))); v.SetPosition(K.V(*map(int, p)))
    def rollback(self, m=0):
        while len(self.log) > m:
            op, it, old = self.log.pop()
            if op == 'rm': K.b.Add(it)
            elif op == 'add': K.b.Remove(it)
            else: it.SetPosition(K.V(*old))

class _Box:
    def __init__(self, x0, y0, x1, y1): self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
    def GetX(self): return self.x0
    def GetY(self): return self.y0
    def GetRight(self): return self.x1
    def GetBottom(self): return self.y1
    def GetWidth(self): return self.x1 - self.x0
    def GetHeight(self): return self.y1 - self.y0
    def GetCenter(self): return K.V((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)

class Multi:
    """several shapes as one: what a bump pushes is the bump's whole new copper, not one segment of it"""
    def __init__(self, shapes): self.s = list(shapes)
    def BBox(self):
        bs = [x.BBox() for x in self.s]
        return _Box(min(b_.GetX() for b_ in bs), min(b_.GetY() for b_ in bs), max(b_.GetRight() for b_ in bs), max(b_.GetBottom() for b_ in bs))
    def Collide(self, other, c): return any(_coll(x, other, c) for x in self.s)

def _coll(a, b_, c):
    if isinstance(a, Multi): return a.Collide(b_, c)
    if isinstance(b_, Multi): return b_.Collide(a, c)
    return a.Collide(b_, c)

def frozen(t):
    net = t.GetNetname()
    if (K.b.FindNet(net).GetNetClassName() or 'Default') in FROZEN_CLASSES: return True
    if any(K.sh(net).startswith(x) for x in FROZEN_NETS): return True
    bb = t.GetBoundingBox()
    return any(bb.GetRight() >= x0 and bb.GetLeft() <= x1 and bb.GetBottom() >= y0 and bb.GetTop() <= y1 for x0, y0, x1, y1 in FROZEN_BOXES)

def _seg(t): return (K.P(t.GetStart()), K.P(t.GetEnd()))
def _same(p, q): return K.dist(p, q) < K.SNAP
def _unit(d):
    L = math.hypot(*d); return (d[0] / L, d[1] / L) if L else (0.0, 0.0)
def _dot(a, b): return a[0] * b[0] + a[1] * b[1]
def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _add(a, b, k=1.0): return (a[0] + k * b[0], a[1] + k * b[1])
def _far(seg, node):
    P0, P1 = _seg(seg); return P1 if _same(P0, node) else P0
def _diag(u): return abs(abs(u[0]) - abs(u[1])) < 1e-3

def _at(node, net, lid, excl):
    """what sits at a track end: (segments ending there, anchor: 'via' / 'pad' / 'tee' / None)"""
    ends = [o for o in K.tracks(net, lid) if K.uid(o) not in excl and (_same(K.P(o.GetStart()), node) or _same(K.P(o.GetEnd()), node))]
    if any(x.Type() == pcbnew.PCB_VIA_T and x.GetNetname() == net and K.dist(K.P(x.GetPosition()), node) <= x.GetWidth(pcbnew.F_Cu) / 2
           for x in K.b.GetTracks()): return ends, 'via'
    if any(q.GetNetname() == net and q.IsOnLayer(lid) and q.GetEffectivePolygon(lid).Contains(K.V(*map(int, node)))
           for f in K.b.GetFootprints() for q in f.Pads()): return ends, 'pad'
    if any(K.uid(o) not in excl and not (_same(K.P(o.GetStart()), node) or _same(K.P(o.GetEnd()), node)) and o.HitTest(K.V(*map(int, node)), 1)
           for o in K.tracks(net, lid)): return ends, 'tee'
    return ends, None

def _geometry(T, s):
    """the copper that replaces T's chain translated by vector s: (segments to remove, [(pts, width)]) or None"""
    net, lid = T.GetNetname(), T.GetLayer()
    A, B = _seg(T); pts = [A, B]; segs = [T]; excl = {K.uid(T)}; ends = {}
    for _ in range(2 * MAXCHAIN):
        grown = False
        for side in ('a', 'b'):
            if side in ends: continue
            E = pts[0] if side == 'a' else pts[-1]; inner = pts[1] if side == 'a' else pts[-2]
            tt = _unit(_sub(inner, E)); L = K.dist(E, inner); w = (segs[0] if side == 'a' else segs[-1]).GetWidth()
            nb, anc = _at(E, net, lid, excl)
            if anc == 'tee': return None
            if anc or len(nb) != 1:
                a_ = _dot(s, tt); bv = _sub(s, (tt[0] * a_, tt[1] * a_)); b_ = math.hypot(*bv)
                if b_ < 1:                                         # the push runs along the end segment
                    if L + a_ < K.FM(MINSEG) + w: return None
                    ends[side] = ('ext', E, None, None)
                else:
                    mu = b_ - a_                                   # J = E + s + mu*tt; J - E = b_*tt + bv: 45 degrees
                    if mu > L - K.FM(MINSEG) - w: return None
                    J = _add(_add(E, s), tt, mu); jd = _unit(_sub(J, E))
                    if anc is None:                                # a junction: #17's pairs of axes
                        if K.dist(E, J) < w: return None            # and the jog's diagonal >= one track width
                        for o in nb:
                            if not K.near(K.ang(_unit(_sub(_far(o, E), E)), jd), (45, 135, 180)): return None
                    ends[side] = ('jog', E, J, None)
                continue
            U = nb[0]; far = _far(U, E); u = _unit(_sub(far, E))
            det = u[0] * (-tt[1]) - u[1] * (-tt[0])
            ok = False
            if abs(det) > 1e-3:
                lam = (s[0] * (-tt[1]) - s[1] * (-tt[0])) / det    # E2 = E + lam*u = E + s + mu*tt
                mu = (u[0] * s[1] - u[1] * s[0]) / det
                E2 = _add(E, u, lam)
                if _dot(_sub(far, E2), u) > max(K.FM(MINSEG), U.GetWidth() if _diag(u) else 0) and mu < L - K.FM(MINSEG) - w:
                    ends[side] = ('slide', E, E2, (U, far)); ok = True
            if not ok:                                             # U joins the chain
                if len(segs) >= MAXCHAIN: return None
                excl.add(K.uid(U))
                if side == 'a': pts.insert(0, far); segs.insert(0, U)
                else: pts.append(far); segs.append(U)
                grown = True
        if len(ends) == 2 and not grown: break
    else: return None
    if len(ends) < 2: return None
    tp = [_add(p, s) for p in pts]; new = []; remove = list(segs)
    for side, i in (('a', 0), ('b', len(tp) - 1)):
        kind, E, X, extra = ends[side]
        if kind == 'slide':
            U, far = extra; tp[i] = X; remove.append(U); new.append(([X, far], U.GetWidth()))
        elif kind == 'jog':
            new.append(([E, X], (segs[0] if side == 'a' else segs[-1]).GetWidth())); tp[i] = X
        else: tp[i] = E
    for k in range(len(segs)):
        if _dot(_sub(tp[k + 1], tp[k]), _sub(pts[k + 1], pts[k])) <= 0 or K.dist(tp[k], tp[k + 1]) < K.FM(MINSEG): return None
        new.append(([tp[k], tp[k + 1]], segs[k].GetWidth()))
    return remove, new

def _bump(T, shape, s, c):
    """a local detour of T around shape: T's line, a 45-degree jog out by |s|, a run parallel to T past the shape, a
    45-degree jog back -- (segments to remove, [(pts, width)]) or None when T is too short for it"""
    A, B = _seg(T); w = T.GetWidth(); d = math.hypot(*s); L = K.dist(A, B)
    if L < 1: return None
    if d * math.sqrt(2) < w: return None      # each jog is half of a right-angle pair: its diagonal >= one track width (#17)
    t = _unit(_sub(B, A)); bb = shape.BBox()
    us = [_dot(_sub((x, y), A), t) for x in (bb.GetX(), bb.GetRight()) for y in (bb.GetY(), bb.GetBottom())]
    m = c + w / 2 + d + c + K.FM(0.05)        # past the shape AND past the jogs of a bump that pushed this one
    u0, u1 = min(us) - m, max(us) + m
    if u0 - d < K.FM(MINSEG) or u1 + d > L - K.FM(MINSEG): return None
    J0 = _add(A, t, u0 - d); J1 = _add(_add(A, t, u0), s); J2 = _add(_add(A, t, u1), s); J3 = _add(A, t, u1 + d)
    return [T], [([A, J0], w), ([J0, J1], w), ([J1, J2], w), ([J2, J3], w), ([J3, B], w)]

def _hits(pts, w, net, lid, keep):
    """(fatal, items): fatal names a non-pushable obstacle or a kept shape hit; items are other nets' tracks and
    vias hit (each pushable or not decided by the caller)"""
    seg = pcbnew.SHAPE_SEGMENT(K.V(*map(int, pts[0])), K.V(*map(int, pts[1])), w)
    (ax, ay), (bx, by) = pts
    if min(ax, bx) - w // 2 < K.EDGE[0] or min(ay, by) - w // 2 < K.EDGE[1] or max(ax, bx) + w // 2 > K.EDGE[2] or max(ay, by) + w // 2 > K.EDGE[3]:
        return 'board edge', []
    for shp, knet in keep:
        if knet != net and _coll(shp, seg, K.FM(max(K.clr(net), K.clr(knet))) - K.FM(0.001)): return 'a place being cleared', []
    items = []; cx, cy = (ax + bx) / 2, (ay + by) / 2; half = K.dist(pts[0], pts[1]) / 2e6 + 1.5
    for shp, c, name, it in K.obstacles(net, lid, cx, cy, half):
        if not shp.Collide(seg, c - K.FM(0.001)): continue
        if it.Type() in (pcbnew.PCB_TRACE_T, pcbnew.PCB_VIA_T): items.append(it)
        else: return name, []
    return None, items

def _cascade(made, net, lid, keep, U, depth, dirn=None):
    """push whatever the new segments `made` [(pts, w)] hit; True if all of it moved"""
    whole = Multi([pcbnew.SHAPE_SEGMENT(K.V(*map(int, p[0])), K.V(*map(int, p[1])), ww) for p, ww in made])
    for _ in range(8):
        hit = None
        for pts, ww in made:
            fatal, items = _hits(pts, ww, net, lid, keep)
            if fatal: return False
            if items: hit = items[0]; break
        if hit is None: return True
        if depth <= 0: return False
        if hit.Type() == pcbnew.PCB_VIA_T: ok = push_via(hit, whole, net, keep, U, depth - 1, dirn=dirn)
        else: ok = push_track(hit, whole, net, keep, U, depth - 1, dirn=dirn)
        if not ok: return False
    return False

def push_track(T, shape, snet, keep, U, depth, dirn=None):
    """push track T (another net's) clear of shape (net snet); keep = [(shape, net)] already cleared"""
    if frozen(T) or time.time() > _deadline[0]: return False
    net, lid = T.GetNetname(), T.GetLayer()
    A, B = _seg(T); t = _unit(_sub(B, A)); n = (-t[1], t[0])
    c = K.FM(max(K.clr(net), K.clr(snet)))
    bb = shape.BBox(); C = (bb.GetCenter().x, bb.GetCenter().y)
    if _dot(_sub(C, A), n) > 0: n = (-n[0], -n[1])
    if dirn is not None:                                  # a cascade keeps going the way it started, never back
        if _dot(n, dirn) < 0.5: return False             # it lies behind the push, or across it
    # each offset size in turn, both kinds; a local bump first when the shape is short beside the track
    bbx = shape.BBox(); ext = max(abs(_dot((bbx.GetWidth(), 0), t)), abs(_dot((0, bbx.GetHeight()), t))) + abs(_dot((bbx.GetWidth(), bbx.GetHeight()), t))
    local = ext < 0.5 * K.dist(A, B)
    k = 1; tries = []
    while k * STEP <= MAXPUSH + 1e-9:
        d = K.FM(k * STEP); k += 1; sv = (n[0] * d, n[1] * d)
        pair = [_bump(T, shape, sv, c), _geometry(T, sv)] if local else [_geometry(T, sv), _bump(T, shape, sv, c)]
        tries += [g for g in pair if g is not None]
    for remove, new in tries:
        if time.time() > _deadline[0]: return False
        if any(_coll(pcbnew.SHAPE_SEGMENT(K.V(*map(int, p[0])), K.V(*map(int, p[1])), ww), shape, c - K.FM(0.001)) for p, ww in new): continue
        m = U.mark()
        for it in remove: U.remove(it)
        made = []
        for pts, ww in new:
            if K.dist(pts[0], pts[1]) < 1: continue
            x = pcbnew.PCB_TRACK(K.b); x.SetStart(K.V(*map(int, pts[0]))); x.SetEnd(K.V(*map(int, pts[1]))); x.SetWidth(ww); x.SetLayer(lid)
            U.add(x); x.SetNet(K.b.FindNet(net)); made.append((pts, ww))
        off = lambda q: abs(_dot(_sub(q, A), n))                 # distance from T's old line
        changed = [(pts, ww) for pts, ww in made if off(pts[0]) > 1000 or off(pts[1]) > 1000]   # T's untouched pieces cannot newly hit
        if _cascade(changed, net, lid, keep + [(shape, snet)], U, depth, dirn=n): return True
        U.rollback(m)
    return False

def pad_linked(v):
    segs, pads = VL.tables(K.b)
    return any(VL.links_of(K.b, v, lid, segs, pads) for lid in (pcbnew.F_Cu, pcbnew.B_Cu))

def push_via(X, shape, snet, keep, U, depth, hole=None, dirn=None):
    """push via X (another net's, not linked to a pad) clear of shape; its tracks re-attached"""
    if frozen(X) or time.time() > _deadline[0] or pad_linked(X): return False
    net = X.GetNetname(); r = X.GetWidth(pcbnew.F_Cu) // 2; Xp = K.P(X.GetPosition())
    bb = shape.BBox(); C = (bb.GetCenter().x, bb.GetCenter().y); u = _unit(_sub(Xp, C))
    if u == (0.0, 0.0): return False
    if dirn is not None and _dot(u, dirn) < 0.3: return False
    c = K.FM(max(K.clr(net), K.clr(snet)))
    mine = [t for L in K.CU for t in K.tracks(net, L) if K.dist(K.P(t.GetStart()), Xp) <= r or K.dist(K.P(t.GetEnd()), Xp) <= r]
    k = 1
    while k * STEP <= MAXPUSH + 1e-9:
        d = K.FM(k * STEP); k += 1
        Np = (int(Xp[0] + u[0] * d), int(Xp[1] + u[1] * d))
        circ = pcbnew.SHAPE_SEGMENT(K.V(*Np), K.V(*Np), 2 * r)
        if _coll(circ, shape, c - K.FM(0.001)): continue
        if any(knet != net and _coll(circ, shp, K.FM(max(K.clr(net), K.clr(knet))) - K.FM(0.001)) for shp, knet in keep): continue
        if hole and K.dist(Np, hole[0]) - hole[1] - X.GetDrillValue() // 2 < K.HOLE2HOLE: continue
        if K.via_legal(X, Np, K.via_ctx(X, net, Np[0], Np[1])): continue
        re = []
        for t in mine:
            path, ch = K.reattach(t, Xp, r, Np, net, set())
            if path is None: re = None; break
            re.append((t, path, ch))
        if re is None: continue
        m = U.mark(); U.move(X, Np); made = []; seen = set()
        for t, path, ch in re:
            for cc in ch:
                if K.uid(cc) not in seen: seen.add(K.uid(cc)); U.remove(cc)
        for t, path, ch in re:
            for i in range(len(path) - 1):
                if path[i] == path[i + 1]: continue
                x = pcbnew.PCB_TRACK(K.b); x.SetStart(K.V(*map(int, path[i]))); x.SetEnd(K.V(*map(int, path[i + 1]))); x.SetWidth(t.GetWidth()); x.SetLayer(t.GetLayer())
                U.add(x); x.SetNet(K.b.FindNet(net)); made.append(((path[i], path[i + 1]), t.GetWidth(), t.GetLayer()))
        ok = True
        for pts, ww, lid in made:
            if not _cascade([(pts, ww)], net, lid, keep + [(shape, snet)], U, depth, dirn=u): ok = False; break
        if ok: return True
        U.rollback(m)
    return False

def clear(shape, net, layers, U, keep=(), hole=None):
    """push every other net's track (on `layers`) and via out of `shape` (net `net`); with hole=(centre, drill radius)
    also every via whose hole is nearer than min_hole_to_hole.  True if the shape is then clear of both."""
    keep = list(keep); _deadline[0] = time.time() + BUDGET
    for _ in range(16):
        bb = shape.BBox(); C = (bb.GetCenter().x, bb.GetCenter().y); half = max(bb.GetWidth(), bb.GetHeight()) / 2e6 + 1.0
        hit = None
        for lid in layers:
            for shp, c, name, it in K.obstacles(net, lid, C[0], C[1], half):
                if it.Type() in (pcbnew.PCB_TRACE_T, pcbnew.PCB_VIA_T) and shp.Collide(shape, c - K.FM(0.001)): hit = it; break
            if hit: break
        if hit is None and hole:
            hit = next((x for x in K.b.GetTracks() if x.Type() == pcbnew.PCB_VIA_T and x.GetNetname() != net and
                        K.dist(K.P(x.GetPosition()), hole[0]) - x.GetDrillValue() // 2 - hole[1] < K.HOLE2HOLE), None)
        if hit is None: return True
        if hit.Type() == pcbnew.PCB_VIA_T: ok = push_via(hit, shape, net, keep, U, DEPTH, hole)
        else: ok = push_track(hit, shape, net, keep, U, DEPTH)
        if not ok: return False
    return False
