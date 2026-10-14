#!/usr/bin/env python3
"""entryfix.py [--board B] [--sites SITES.json] [--out PREFIX] [--only I,J,..] [--skip I,J,..] [--save SCRATCH.kicad_pcb] [--no-viamove] [--no-nudge]
-- re-lay the track ends that enter a pad badly (shape.py's entry-corner / entry-offset / cap-out sites) so they enter
AXIALLY and CENTRED, every joint at 0 or 45 degrees by route.py's own test: two segments turn 0 or 45; at three or
more every pair of axes is 0, 45 or 135 apart; a right-angle turn needs a diagonal >= one width.  (tools/kpy)

For each site, on the board held in memory: walk back from the pad along the track through plain joints to an ANCHOR
-- a via, another pad of the net, a tee, a junction -- and offer every anchor on the way (re-laying less is cheaper):
    anchor -> K -> C         or   anchor -> O' -> K -> C
K on the pad's centre line, outside it by delta; C the pad's centre; directions from the 8 of 0/45; the first
direction obeys the joints the anchor keeps (a tee may also slide along its through-line).  From another pad the
path leaves THAT pad axially too.  A via whose only copper is this link may instead MOVE onto the pad's axis, off it
by the guide's gap (checked on every copper layer, hole to hole, no SMD pad overlapped), and link straight.

Every candidate is held against the other nets' copper on its layer (pads, tracks, vias, copper polygons) at the
STRICTER of the two class clearances and the guide's margin from the board edge; the cheapest that clears is applied
in memory before the next site, so neighbours see each other.  Output is ONE route.py plan (rips, ripvias, then the
new tracks and vias, necks for widths already there) and a report, per site: anchor, choice, or why it is left for a
hand fix.  KiCad DRC, route.py angles and shape.py on a scratch copy remain the judges; this only proposes."""
import argparse
import json
import math
import os
import common as CM
pcbnew = CM.pcbnew_module()                 # quiet import: wx debug lines stay off stderr
import vfcore as K

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
CM.add_board_args(ap)
ap.add_argument("--sites", help="shape.py's sites JSON (default <board>/routing/shape_sites.json)")
ap.add_argument("--out", help="plan prefix (default <board>/routing/entryfix): OUT.json, OUT_report.txt")
ap.add_argument("--only"); ap.add_argument("--skip")
ap.add_argument("--save"); ap.add_argument("--no-viamove", action="store_true"); ap.add_argument("--no-nudge", action="store_true")
a = CM.args(ap)
CM.load(a)
sitesf = str(CM.in_board(a, a.sites)) if a.sites else str(CM.out_path(a, "shape_sites.json"))
if not os.path.exists(sitesf):
    CM.die(f"no sites file {sitesf}: run shape.py first")
out = str(CM.out_path(a, "entryfix"))
only = {int(x) for x in a.only.split(',')} if a.only else None
skip = {int(x) for x in a.skip.split(',')} if a.skip else set()
VIAMOVE = not a.no_viamove
b = K.init(str(a.pcb), a.board, CM.G)
FM = pcbnew.FromMM; mm = pcbnew.ToMM; V = pcbnew.VECTOR2I
sh = CM.short; uid = lambda x: x.m_Uuid.AsString()
HOLE2HOLE = K.HOLE2HOLE
cls, clr, cw = K.cls, K.clr, K.cw
EDGE = K.EDGE
CU = [b.GetLayerName(l) for l in K.CU]
SNAP = 2000
S = json.load(open(sitesf))
KINDS = ('entry-corner', 'entry-offset', 'cap-out')
DIRS = K.DIRS
unit = K.unit; ang = K.ang; near = K.near; P = K.P; dist = K.dist
segkey, viakey = K.segkey, K.viakey
before, vbefore = K.before, K.vbefore
GAPV = FM(K.GAP)                                 # a moved via keeps the guide's gap from its pad

tracks = K.tracks
def via_at(net, pt):
    for v in b.GetTracks():
        if v.Type() == pcbnew.PCB_VIA_T and v.GetNetname() == net and dist(P(v.GetPosition()), pt) <= v.GetWidth(pcbnew.F_Cu) / 2: return v
def pad_at(net, lid, pt, not_uid):
    for f in b.GetFootprints():
        for q in f.Pads():
            if q.GetNetname() == net and q.IsOnLayer(lid) and uid(q) != not_uid and q.GetEffectivePolygon(lid).Contains(V(*pt)): return q
def pad_of(name):
    ref, num = name.rsplit('.', 1); f = b.FindFootprintByReference(ref)
    return next(q for q in f.Pads() if q.GetNumber() == num)
def pad_frame(q, lid):
    o = q.GetEffectivePolygon(lid).Outline(0)
    xs = [o.CPoint(i).x for i in range(o.PointCount())]; ys = [o.CPoint(i).y for i in range(o.PointCount())]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    return ((x0 + x1) // 2, (y0 + y1) // 2), {(1, 0): (x1 - x0) // 2, (-1, 0): (x1 - x0) // 2, (0, 1): (y1 - y0) // 2, (0, -1): (y1 - y0) // 2}, min(x1 - x0, y1 - y0)
def axis_ok(q): po = q.GetOrientation().AsDegrees() % 90; return abs(po) < 0.1 or abs(po - 90) < 0.1

def obstacles(net, lid, cx, cy, half=3.0):
    return [(shp, c, name) for shp, c, name, _ in K.obstacles(net, lid, cx, cy, half)]
blocked = K.blocked
plen = K.plen
stacks = K.stacks

def chain_from(t, O, target_uid, net, lid):
    """walk back from O along plain joints; yield (anchor kind, anchor point, chain of segments to remove, kept info)"""
    chain, node, out_ = [t], O, []
    for _ in range(4):
        cu = {uid(c) for c in chain}
        v = via_at(net, node); pd = pad_at(net, lid, node, target_uid)
        if v: out_.append(('via', node, list(chain), v)); break
        if pd: out_.append(('pad', node, list(chain), pd)); break
        ends = [o for o in tracks(net, lid) if uid(o) not in cu and (dist(P(o.GetStart()), node) < SNAP or dist(P(o.GetEnd()), node) < SNAP)]
        host = [o for o in tracks(net, lid) if uid(o) not in cu and o not in ends and o.HitTest(V(*node), 1) and
                dist(P(o.GetStart()), node) >= SNAP and dist(P(o.GetEnd()), node) >= SNAP]
        host = [o for o in host if uid(o) not in {uid(e) for e in ends}]
        if host: out_.append(('tee', node, list(chain), host[0])); break
        if len(ends) >= 2: out_.append(('junction', node, list(chain), ends)); break
        if not ends: out_.append(('end', node, list(chain), None)); break
        out_.append(('joint', node, list(chain), ends))
        nxt = ends[0]
        if nxt.GetWidth() != t.GetWidth(): break
        chain.append(nxt); node = P(nxt.GetEnd()) if dist(P(nxt.GetStart()), node) < SNAP else P(nxt.GetStart())
        if sum(c.GetLength() for c in chain) > FM(3.5): break
    return out_
def kept_dirs(kind, A, info):
    if kind in ('joint', 'junction'):
        return [unit((P(o.GetEnd())[0] - A[0], P(o.GetEnd())[1] - A[1])) if dist(P(o.GetStart()), A) < SNAP else
                unit((P(o.GetStart())[0] - A[0], P(o.GetStart())[1] - A[1])) for o in info]
    if kind == 'tee':
        u = unit((P(info.GetEnd())[0] - P(info.GetStart())[0], P(info.GetEnd())[1] - P(info.GetStart())[1])); return [u, (-u[0], -u[1])]
    return []
def first_ok(g, kd):
    if not kd: return True
    if len(kd) == 1: return near(ang(kd[0], g), (180, 135))
    return all(near(ang(d, g), (180, 135, 45)) for d in kd)

def paths_to(A, C, n, h, w, firsts, lam_min, deltas):
    """every path A -> [O'] -> K -> C with K = C + n*(h+delta): first direction from `firsts`, last into the pad"""
    into = (-n[0], -n[1]); res = []
    for g1 in firsts:
        u1 = unit(g1)
        # two segments: A -> K -> C (or straight A -> C)
        if near(ang(g1, into), (0,)):
            lat = (A[0] - C[0]) * (-n[1]) + (A[1] - C[1]) * n[0]
            if abs(lat) < 2 and (A[0] - C[0]) * n[0] + (A[1] - C[1]) * n[1] > h: res.append(([A, C], 'straight in'))
        elif near(ang(g1, into), (45,)):
            # K = A + lam*g1 on the axis line {C + n*s}
            if n[1] == 0 and g1[1]: lam_steps = (C[1] - A[1]) // g1[1] if (C[1] - A[1]) % g1[1] == 0 else None
            elif n[0] == 0 and g1[0]: lam_steps = (C[0] - A[0]) // g1[0] if (C[0] - A[0]) % g1[0] == 0 else None
            else: lam_steps = None
            if lam_steps is not None and lam_steps > 0:
                K = (A[0] + g1[0] * lam_steps, A[1] + g1[1] * lam_steps)
                sK = (K[0] - C[0]) * n[0] + (K[1] - C[1]) * n[1]
                if sK >= h + deltas[0] and dist(A, K) >= lam_min: res.append(([A, K, C], f'45 onto the axis, delta {mm(sK - h):.2f}'))
        # three segments: A -> O' -> K -> C
        for g2 in DIRS:
            if g2 == g1 or not near(ang(g1, g2), (45,)) or not near(ang(g2, into), (45,)): continue
            det = g1[0] * g2[1] - g1[1] * g2[0]           # integer steps: k*g1 + m*g2 = K - A, solved exactly
            if det == 0: continue
            for dl in deltas:
                for nudge in (0, 1):
                    K = (C[0] + n[0] * (h + dl + nudge), C[1] + n[1] * (h + dl + nudge)); rx, ry = K[0] - A[0], K[1] - A[1]
                    kn, mn = rx * g2[1] - ry * g2[0], g1[0] * ry - g1[1] * rx
                    if kn % det == 0 and mn % det == 0: break
                else: continue
                k1, m1 = kn // det, mn // det
                lam, mu = k1 * math.hypot(*g1), m1 * math.hypot(*g2)
                if lam < lam_min or mu < max(w, FM(0.1)): continue
                Op = (A[0] + g1[0] * k1, A[1] + g1[1] * k1)
                res.append(([A, Op, K, C], f'jog, delta {mm(dl):.2f}, diagonal {mm(mu):.2f}'))
    return res

def via_legal(v, Np, net, obs, holes, smd, rules):
    r = v.GetWidth(pcbnew.F_Cu) // 2
    if Np[0] - r < EDGE[0] or Np[1] - r < EDGE[1] or Np[0] + r > EDGE[2] or Np[1] + r > EDGE[3]: return 'board edge'
    circ = pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r)
    hit = next((name for L in CU for shp, c, name in obs[L] if shp.Collide(circ, c - FM(0.001))), None)
    if hit: return hit
    if any(dist(Np, hp) - hr - v.GetDrillValue() // 2 < HOLE2HOLE for hp, hr in holes): return 'hole to hole'
    if any(q.GetEffectiveShape(pcbnew.F_Cu).Collide(pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r - FM(0.006)), 0) for q in smd): return 'overlaps an SMD pad'
    if any(z.Outline().Contains(V(*Np)) for z in rules): return 'rule area'
    return None
def via_context(v, net, C):
    holes = [(P(x.GetPosition()), x.GetDrillValue() // 2) for x in b.GetTracks() if x.Type() == pcbnew.PCB_VIA_T and uid(x) != uid(v)]
    holes += [(P(q.GetPosition()), max(q.GetDrillSize().x, q.GetDrillSize().y) // 2) for f in b.GetFootprints() for q in f.Pads() if q.GetDrillSize().x > 0]
    obs = {L: obstacles(net, b.GetLayerID(L), C[0], C[1]) for L in CU}
    smd = [q for f in b.GetFootprints() for q in f.Pads() if q.GetNetname() == net and q.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and q.IsOnLayer(pcbnew.F_Cu)]
    rules = [z for z in b.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    return obs, holes, smd, rules
def via_free(v, net, removable):
    r = v.GetWidth(pcbnew.F_Cu) // 2; Vp = P(v.GetPosition()); rem = {uid(t) for t in removable}
    for L in CU:
        for t in tracks(net, b.GetLayerID(L)):
            if uid(t) not in rem and (dist(P(t.GetStart()), Vp) <= r or dist(P(t.GetEnd()), Vp) <= r): return False
    return True
def via_nudges(v, C, half, net, w, ob, deltas, removable, reach=0.45, step=0.05):
    """the via shifted (<= reach) to the nearest legal place from which a clean link exists"""
    if not via_free(v, net, removable): return []
    ctx = via_context(v, net, C); Vp = P(v.GetPosition()); res = []; n_ = int(round(reach / step))
    grid = sorted(((dx, dy) for dx in range(-n_, n_ + 1) for dy in range(-n_, n_ + 1) if (dx, dy) != (0, 0) and math.hypot(dx, dy) <= n_ + 1e-9),
                  key=lambda d: math.hypot(*d))
    for dx, dy in grid:
        Np = (Vp[0] + dx * FM(step), Vp[1] + dy * FM(step))
        if via_legal(v, Np, net, *ctx): continue
        best = None
        for nn, h in half.items():
            for path, how in paths_to(Np, C, nn, h, w, DIRS, FM(0.05), deltas):
                if blocked(path, w, ob) or stacks(path, w, net, b.GetLayerID('F.Cu') if False else LID_CUR[0], {uid(c) for c in removable}): continue
                c_ = 0.15 * (len(path) - 2) + plen(path) / 1e6 * 0.2
                if best is None or c_ < best[0]: best = (c_, path, how)
        if best: res.append((math.hypot(dx, dy) * step + best[0], Np, best[1], best[2]))
        if len(res) >= 6: break
    return res
def via_moves(v, C, half, net, target_pad, removable):
    """the via re-sited on the pad's axis, abutting it; only if its copper is this link alone"""
    r = v.GetWidth(pcbnew.F_Cu) // 2; Vp = P(v.GetPosition()); res = []
    rem = {uid(t) for t in removable}
    for L in CU:
        lid = b.GetLayerID(L)
        for t in tracks(net, lid):
            if uid(t) not in rem and (dist(P(t.GetStart()), Vp) <= r or dist(P(t.GetEnd()), Vp) <= r): return res
    holes = [(P(x.GetPosition()), x.GetDrillValue() // 2) for x in b.GetTracks() if x.Type() == pcbnew.PCB_VIA_T and uid(x) != uid(v)]
    holes += [(P(q.GetPosition()), max(q.GetDrillSize().x, q.GetDrillSize().y) // 2) for f in b.GetFootprints() for q in f.Pads() if q.GetDrillSize().x > 0]
    obs = {L: obstacles(net, b.GetLayerID(L), C[0], C[1]) for L in CU}
    smd = [q for f in b.GetFootprints() for q in f.Pads() if q.GetNetname() == net and q.GetAttribute() == pcbnew.PAD_ATTRIB_SMD]
    rules = [z for z in b.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    for n, h in half.items():
        for gap in (GAPV, GAPV + FM(0.03), GAPV + FM(0.08)):
            Np = (C[0] + n[0] * (h + r + gap), C[1] + n[1] * (h + r + gap))
            if Np[0] - r < EDGE[0] or Np[1] - r < EDGE[1] or Np[0] + r > EDGE[2] or Np[1] + r > EDGE[3]: continue
            circ = pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r)
            hit = next((name for L in CU for shp, c, name in obs[L] if shp.Collide(circ, c - FM(0.001))), None)
            if not hit and any(dist(Np, hp) - hr - v.GetDrillValue() // 2 < HOLE2HOLE for hp, hr in holes): hit = 'hole to hole'
            if not hit and any(q.GetEffectiveShape(pcbnew.F_Cu).Collide(pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r - FM(0.006)), 0)
                               for q in smd if q.IsOnLayer(pcbnew.F_Cu)): hit = 'overlaps an SMD pad'
            if not hit and any(z.Outline().Contains(V(*Np)) for z in rules): hit = 'rule area'
            res.append((Np, n, hit, mm(gap)))
    return res

report = []; LID_CUR = [None]
def log(i, s, msg): report.append(f"{i:2} {s['kind']:12} {s['net']:16} {s.get('into', ''):8} {s['at']}  {msg}")
moved = []
for i, s in enumerate(S):
    if s['kind'] not in KINDS or (only and i not in only) or i in skip: continue
    if not s.get('into') or not s.get('uuids'): log(i, s, 'HAND: no pad or no track recorded'); continue
    t = next((x for x in b.GetTracks() if uid(x) == s['uuids'][0]), None)
    if t is None: log(i, s, 'done with an earlier site (its track was re-laid)'); continue
    net, lid, w = t.GetNetname(), t.GetLayer(), t.GetWidth(); LID_CUR[0] = lid
    p = pad_of(s['into'])
    if not axis_ok(p): log(i, s, f'HAND: pad at {p.GetOrientation().AsDegrees():.0f} degrees'); continue
    C, half, cross = pad_frame(p, lid)
    if w > cross: log(i, s, f'HAND: track {mm(w):.2f} wider than the pad ({mm(cross):.2f}) -- a polygon'); continue
    ppoly = p.GetEffectivePolygon(lid)
    A0, B0 = P(t.GetStart()), P(t.GetEnd())
    inA, inB = ppoly.Contains(V(*A0)), ppoly.Contains(V(*B0))
    if inA and inB:
        # the whole segment lies in the pad: step to the segment that leads in
        log(i, s, 'HAND: the segment lies wholly in the pad'); continue
    if not inA and not inB: log(i, s, 'HAND: neither end in the pad'); continue
    E, O = (A0, B0) if inA else (B0, A0)
    ob = obstacles(net, lid, C[0], C[1])
    dmin, dpref = FM(0.05), max(w // 2, FM(0.1))
    deltas = sorted({dmin, FM(0.08), dpref, dpref + FM(0.05), dpref + FM(0.1), dpref + FM(0.2), dpref + FM(0.35)})
    cands = []
    for kind, A, chain, info in chain_from(t, O, uid(p), net, lid):
        removed_len = sum(c.GetLength() for c in chain)
        # same-net segments that only join the anchor to the pad go too (stacked duplicates of the link)
        extra = []
        if kind in ('via', 'pad'):
            region = (lambda pt: dist(pt, P(info.GetPosition())) <= info.GetWidth(pcbnew.F_Cu) / 2) if kind == 'via' else \
                     (lambda pt: info.GetEffectivePolygon(lid).Contains(V(*pt)))
            cu = {uid(c) for c in chain}
            for o in tracks(net, lid):
                if uid(o) in cu: continue
                ends_ = [P(o.GetStart()), P(o.GetEnd())]
                if all(region(e) or ppoly.Contains(V(*e)) for e in ends_) and any(region(e) for e in ends_) and any(ppoly.Contains(V(*e)) for e in ends_):
                    extra.append(o)
        rm = chain + extra
        wl = max(c.GetWidth() for c in rm)          # a link re-laid takes the widest of what it replaces, never narrower
        if wl > cross: continue
        if kind == 'via':
            starts = [(P(info.GetPosition()), DIRS, FM(0.05), 0.0)]
        elif kind == 'pad':
            if not axis_ok(info): continue
            Cs, hs, _ = pad_frame(info, lid)
            starts = [(Cs, [n_], hs[n_] + dmin, 0.0) for n_ in hs]
        elif kind in ('joint', 'junction'):
            kd = kept_dirs(kind, A, info); starts = [(A, [g for g in DIRS if first_ok(g, kd)], max(w, FM(0.1)), 0.0)]
        elif kind == 'tee':
            # the branch starts ON the host's centreline (lattice points of an axis-aligned or 45-degree host), and may slide
            kd = kept_dirs(kind, A, info); hs_, he_ = P(info.GetStart()), P(info.GetEnd())
            hd = (he_[0] - hs_[0], he_[1] - hs_[1])
            if not (hd[0] == 0 or hd[1] == 0 or abs(hd[0]) == abs(hd[1])): continue
            gh = ((hd[0] > 0) - (hd[0] < 0), (hd[1] > 0) - (hd[1] < 0)); gl = math.hypot(*gh); nsteps = max(abs(hd[0]), abs(hd[1]))
            k0 = round(((A[0] - hs_[0]) * gh[0] + (A[1] - hs_[1]) * gh[1]) / (gl * gl))
            starts = []
            for sl in (0, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3, 0.45, -0.45, 0.6, -0.6):
                k_ = k0 + round(FM(sl) / gl)
                if k_ * gl < w or (nsteps - k_) * gl < w: continue
                As = (hs_[0] + gh[0] * k_, hs_[1] + gh[1] * k_)
                starts.append((As, [g for g in DIRS if first_ok(g, kd)], max(w, FM(0.1)), abs(sl)))
        else:
            continue
        for St, firsts, lam_min, slide in starts:
            for n_, h in half.items():
                for path, how in paths_to(St, C, n_, h, wl, firsts, lam_min, deltas):
                    hit = blocked(path, wl, ob) or stacks(path, wl, net, lid, {uid(c) for c in rm})
                    face = (O[0] - C[0]) * n_[0] + (O[1] - C[1]) * n_[1] > 0
                    short = ('delta' in how and float(how.split('delta ')[1].split(',')[0]) < mm(dpref) - 1e-6)
                    cost = (plen(path) - removed_len) / 1e6 + 0.15 * (len(path) - 2) + (0.3 if short else 0) + (0 if face else 0.1) + 0.1 * slide + 0.05 * len(chain)
                    cands.append((hit is None, cost, f'{kind} anchor, {how}' + (f', tee slid {slide:.2f}' if slide else ''), path, hit, rm, None, wl))
        if kind == 'via' and VIAMOVE and len(chain) == 1 and not a.no_nudge:
            for c_, Np, path, how_ in via_nudges(info, C, half, net, wl, ob, deltas, rm):
                cands.append((True, 0.5 + c_, f'via nudged {mm(dist(Np, P(info.GetPosition()))):.2f}, then {how_}', path, None, rm, (info, Np), wl))
        if kind == 'via' and VIAMOVE and len(chain) == 1:
            for Np, n_, hit, gap in via_moves(info, C, half, net, p, rm):
                path = [Np, C]; hit = hit or blocked(path, wl, ob) or stacks(path, wl, net, lid, {uid(c) for c in rm})
                cost = 0.35 + dist(Np, P(info.GetPosition())) / 1e6
                cands.append((hit is None, cost, f'via moved {mm(dist(Np, P(info.GetPosition()))):.2f} onto the axis (gap {gap:.2f}), straight in', path, hit, rm, (info, Np), wl))
    good = sorted([c for c in cands if c[0]], key=lambda c: c[1])
    if not good:
        bl = sorted({c[4] for c in cands if c[4]})
        log(i, s, f'HAND: {len(cands)} candidates, none clear (blocked by {", ".join(bl[:5]) or "-"})'); continue
    ok, cost, how, path, hit, rm, mv, w = good[0]
    for r_ in rm: b.Remove(r_)
    if mv:
        moved.append((sh(net), mm(mv[0].GetPosition().x), mm(mv[0].GetPosition().y))); mv[0].SetPosition(V(*mv[1]))
    for k in range(len(path) - 1):
        if path[k] == path[k + 1]: continue
        n_ = pcbnew.PCB_TRACK(b); n_.SetStart(V(*path[k])); n_.SetEnd(V(*path[k + 1])); n_.SetWidth(w); n_.SetLayer(lid)
        b.Add(n_); n_.SetNet(b.FindNet(net))
    log(i, s, f'FIX {how}, w {mm(w):.2f}: ' + ' -> '.join(f'({mm(x):.3f},{mm(y):.3f})' for x, y in path) + f'  [removed {len(rm)}]')

after = {segkey(t) for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T}
vafter = {viakey(v) for v in b.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
f6 = lambda x: round(mm(x), 6)
rips = [[sh(k[0]), k[1], f6(k[3][0]), f6(k[3][1]), f6(k[4][0]), f6(k[4][1]), f6(k[2])] for k in sorted(before - after)]
adds = [dict(net=k[0], layer=k[1], width=f6(k[2]), pts=[[f6(k[3][0]), f6(k[3][1])], [f6(k[4][0]), f6(k[4][1])]]) for k in sorted(after - before)]
vadds = [dict(net=k[0], at=[f6(k[1]), f6(k[2])], pad=f6(k[3]), drill=f6(k[4])) for k in sorted(vafter - vbefore)]
vrips = [[sh(k[0]), f6(k[1]), f6(k[2])] for k in sorted(vbefore - vafter)]
necks = sorted({(d['net'], d['width']) for d in adds if abs(d['width'] - cw(d['net'])) > 1e-6})
plan = dict(group=os.path.basename(out), why='entryfix.py: axial, centred pad entries (shape.py sites)',
            rips=[dict(net=r[0], layer=r[1], pts=[[r[2], r[3]], [r[4], r[5]]], width=r[6]) for r in rips],
            ripvias=[dict(net=n_, at=[x_, y_]) for n_, x_, y_ in vrips], polyrips=[],
            tracks=adds, vias=vadds, zones=[], polys=[], thermals=bool(CM.G.get("thermals")),
            necks=[dict(net=n, width=wd, why='the width the track already had, re-laid') for n, wd in necks])
json.dump(plan, open(out + '.json', 'w'), indent=1)
open(out + '_report.txt', 'w').write('\n'.join(report) + '\n')
print('\n'.join(report)); print(f'{len(rips)} segments ripped, {len(adds)} added, {len(vrips)} vias moved; necks {necks}')
if a.save: pcbnew.SaveBoard(str(CM.out_in_board(a, a.save)), b)
