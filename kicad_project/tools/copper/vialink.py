#!/usr/bin/env python3
"""vialink.py -- the via-link audit (the guide's "Vias and their links").

    tools/kpy tools/copper/vialink.py [--board B] [--out SITES.json]        exit 1 on any live site
    tools/kpy tools/copper/vialink.py --pcb scratch.kicad_pcb --guide <board dir>

A via is joined to a pad of its net by a link that runs INTO THE VIA'S CENTRE
and OUT OF THE PAD'S CENTRE, as ONE STRAIGHT segment along one of the pad's
axes.  A straight 45-degree link is allowed only as the guide says ("45-degree
via link": last resort / allowed / never); any other angle, or a bend, is not.
A via never touches a pad: its copper keeps >= the guide's "via to pad gap"
from every pad on its layers, its own net's included -- a gap KiCad never
tests within a net, so this audit is the only check.

Sites (each with the via, the pad, the segments, a point):
  via-touch    the via's copper is closer than GAP to a pad of its net
  via-angle    the link has a segment at an angle other than 0/45/90, or bends
  via-45       one straight 45-degree link: listed so each one is a choice ("never": a site)
  via-offset   axial, but not leaving the pad's centre line or not ending at the via's centre
Inside a field part's courtyard (the guide's `via field` and `bga field` rows:
a BGA's dog-bones and site vias) only the BGA's OWN balls are lifted: a via
there is not judged against that part's pads (gap or link), and every other
part's link to it -- a capacitor's, a resistor's under the BGA -- is judged
exactly as anywhere (board1 RV61: 14 such links under U300 passed while the
whole courtyard was skipped); such a site carries "field": REF.  A field
part's OWN ball's link is its escape and is lifted wherever its via lands
(10_layout #39 (2): a ring-2 ball leaves between two ring-1 lands and turns 45
to its lane, to a via outside the ball field -- just outside the courtyard
too; board1 R3ab_c); that via's GAP outside the courtyard is judged as
anywhere, against every pad.  Not judged
either: a via whose centre lies in a pad of its own net (via-in-pad: no link
to judge), links to a `via field` part's pads, and vias with no link to a pad
unless they touch one.  WHICH RUNS ARE LINKS (10_layout #48 (3), board1 Q87): the copper from a
pad to a via of its net with no other pad, via or tee between them -- walked by topology, however
many pieces and however long -- whose pad and via centres are at most LINK_MAX (3.0 mm) apart IN A
STRAIGHT LINE, never measured along the copper: a link made longer (a meander, a tune) is still a
link and is judged.  A longer run is a route, judged by the joints and shape rules, not here
(counted under skipped).  A site declared in
<board>/routing/vialink_exceptions.json ([{"via": [x, y], "why": ...}]) is
listed as declared and does not fail.  Importable: audit(b, gap, exc, fields,
field_refs) -> (sites, stats) (fields: [(ref, box nm)], guide.field_parts); live(sites, diag)."""
import argparse
import collections
import json
import math
import sys

import common as C
import pcbnew

mm = pcbnew.ToMM; FM = pcbnew.FromMM; V = pcbnew.VECTOR2I
sh = C.short; P = lambda v: (v.x, v.y)
dist = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1])
SNAP = 2000
LINK_MAX = 3.0   # mm, pad centre to via centre in a straight line: longer is a route (10_layout #48 (3), Q87)


def pad_frame(p, lid):
    o = p.GetEffectivePolygon(lid).Outline(0)
    xs = [o.CPoint(i).x for i in range(o.PointCount())]; ys = [o.CPoint(i).y for i in range(o.PointCount())]
    return ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2), (max(xs) - min(xs), max(ys) - min(ys))


def in_field(v, fields):
    """The refs of the field parts whose courtyard holds via v.  fields: [(ref, (x0, y0, x1, y1) nm)]
    (guide.field_parts).  Only those parts' own pads are lifted for the via: another part's link
    to it is judged as anywhere (board1 RV61).  A bare box (the old form) lifts the whole via."""
    p = v.GetPosition(); out = set()
    for f in fields:
        ref, (x0, y0, x1, y1) = f if len(f) == 2 else (None, f)
        if x0 <= p.x <= x1 and y0 <= p.y <= y1: out.add(ref)
    return out


def tables(b):
    segs = collections.defaultdict(list)
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_TRACE_T and t.GetStart() != t.GetEnd(): segs[(t.GetNetCode(), t.GetLayer())].append(t)
    pads = collections.defaultdict(list)
    for f in b.GetFootprints():
        for p in f.Pads():
            for lid in (pcbnew.F_Cu, pcbnew.B_Cu):
                if p.IsOnLayer(lid): pads[lid].append((f.GetReference() + '.' + p.GetNumber(), p))
    return segs, pads


CENTRE = 5000          # nm: a link end this near a via's centre ends AT the centre (judge_link's test)
COLLINEAR = 1e-3       # the sine of the angle two segments may make and still be one straight line


class Piece:
    """One straight run of copper: consecutive collinear segments of one net, layer and width,
    joined end to end with no third item at a joint.  A track broken at a rule area's edge
    (10_layout #30) is still ONE link: the break may fall inside the via's own copper, and the
    piece from the pad to the break read as a link ending off the via's centre (board1 RV59.8).
    a is the via side once the piece is in a chain; members run a -> b."""
    def __init__(self, members, a, b, w): self.members, self.a, self.b, self.w = members, a, b, w
    def GetStart(self): return V(*self.a)
    def GetEnd(self): return V(*self.b)
    def GetWidth(self): return self.w
    def GetLength(self): return dist(self.a, self.b)
    def flip(self): return Piece(self.members[::-1], self.b, self.a, self.w)


class Chain(list):
    """A link's real segments, via to pad (what a fixer rips: callers iterate it as a list);
    .pieces is the same copper as straight runs (what judge_link judges)."""
    pieces = None


def _seg_dist(q, s, e):
    """distance (nm) from point q to the segment s-e"""
    dx, dy = e[0] - s[0], e[1] - s[1]; L2 = dx * dx + dy * dy
    t = 0.0 if not L2 else max(0.0, min(1.0, ((q[0] - s[0]) * dx + (q[1] - s[1]) * dy) / L2))
    return dist(q, (s[0] + t * dx, s[1] + t * dy))


def _straight_on(far, J, nxt):
    """far -> J -> nxt is one straight line through J (not a turn, not a fold back)"""
    u = (far[0] - J[0], far[1] - J[1]); w = (nxt[0] - J[0], nxt[1] - J[1])
    lu, lw = math.hypot(*u), math.hypot(*w)
    if not lu or not lw: return False
    return abs(u[0] * w[1] - u[1] * w[0]) <= COLLINEAR * lu * lw and u[0] * w[0] + u[1] * w[1] < 0


def _piece(g, geo, skip, vias_net, net_pads, lid):
    """segment g = (track, uuid, start, end, width) extended through every plain joint at both ends.
    A joint is plain when exactly one other segment of the net and layer touches it (an end or a
    middle: a tee is a third item), that segment has the same width and continues the straight line,
    no via of the net is centred on it, it is not inside a pad of the net, and, when it lies in a via's
    copper, the segment beyond it ends in that via too (a break that fell inside the via the link
    runs into; a track that runs on THROUGH the via's copper meets it at the joint).  skip: uuids
    that never join (the chain's pieces already laid)."""
    t, u, a, b, w = g; mem = [t]; used = {u} | skip
    for side in (0, 1):
        while True:
            J = b if side else a; far = a if side else b
            touch = [h for h in geo if h[1] not in used and _seg_dist(J, h[2], h[3]) < SNAP]
            if len(touch) != 1: break
            h = touch[0]
            if dist(h[2], J) < SNAP: nxt = h[3]
            elif dist(h[3], J) < SNAP: nxt = h[2]
            else: break                                          # J on the other's middle: a tee
            if h[4] != w or not _straight_on(far, J, nxt): break
            if any(dist(c, J) < CENTRE or (dist(c, J) <= rr and dist(c, nxt) > rr) for c, rr in vias_net): break   # a via at the joint
            if any(pp.GetEffectivePolygon(lid).Contains(V(*J)) for _n, pp in net_pads): break
            used.add(h[1])
            if side: mem.append(h[0]); b = nxt
            else: mem.insert(0, h[0]); a = nxt
    return Piece(mem, a, b, w)


def links_of(b, v, lid, segs, pads, routes=None):
    """the chains from via v to a pad of its net on layer lid: [(pad name, pad, Chain of segments, via end)].
    The chain is walked in straight pieces (Piece): collinear segments split at a plain joint are one.
    A run whose pad and via centres lie more than LINK_MAX apart in a straight line is a route, not a
    link: it is left out (appended to `routes` as (pad name, straight mm) when a list is given)."""
    vp = P(v.GetPosition()); r = v.GetWidth(pcbnew.F_Cu) / 2; out = []
    ss = segs[(v.GetNetCode(), lid)]
    vias_net = [(P(o.GetPosition()), o.GetWidth(pcbnew.F_Cu) / 2) for o in b.GetTracks()
                if o.Type() == pcbnew.PCB_VIA_T and o.GetNetCode() == v.GetNetCode()]
    net_pads = [(n, p) for n, p in pads[lid] if p.GetNetCode() == v.GetNetCode()]
    geo = [(o, o.m_Uuid.AsString(), P(o.GetStart()), P(o.GetEnd()), o.GetWidth()) for o in ss]
    seen = set()
    for g0 in geo:
        if g0[1] in seen or min(dist(g0[2], vp), dist(g0[3], vp)) > r: continue
        pc = _piece(g0, geo, set(), vias_net, net_pads, lid); seen |= {m.m_Uuid.AsString() for m in pc.members}
        if dist(pc.b, vp) < dist(pc.a, vp): pc = pc.flip()
        if dist(pc.a, vp) > r: continue                          # the via lies on a joint inside the run, not at an end
        chain = [pc]; cur = pc.b; via_end = pc.a; hit = None
        # walked by topology only (#48 (3)): the pad ends it; a via, a tee or a dead end stops it.
        # Every step takes a segment not yet used, so it ends within len(geo) steps.  No copper cap:
        # the old bound (4 pieces / 3 mm of copper) read a tuned link as no link at all (b1d DSI_CLK).
        while True:
            hp = next(((n, p) for n, p in net_pads if p.GetEffectivePolygon(lid).Contains(V(*cur))), None)
            if hp: hit = hp; break
            if any(dist(c, cur) <= rr for c, rr in vias_net): break
            used = {m.m_Uuid.AsString() for c in chain for m in c.members}
            nxt = [g for g in geo if g[1] not in used and (dist(g[2], cur) < SNAP or dist(g[3], cur) < SNAP)]
            if len(nxt) != 1: break
            pn = _piece(nxt[0], geo, used, vias_net, net_pads, lid)
            if dist(pn.b, cur) < dist(pn.a, cur): pn = pn.flip()
            chain.append(pn); cur = pn.b
        if hit:
            pcen, _ = pad_frame(hit[1], lid); straight = dist(pcen, vp)
            if straight > FM(LINK_MAX):                          # a route, not a link (#48 (3))
                if routes is not None: routes.append((hit[0], round(mm(straight), 3)))
                continue
            ch = Chain(m for c in chain for m in c.members); ch.pieces = chain
            out.append((hit[0], hit[1], ch, via_end))
    return out


def touch_gap(p, lid, vp, r, GAP):
    """the copper gap between a via (centre vp, radius r) and pad p, or None if it is >= GAP"""
    ps = p.GetEffectivePolygon(lid); reach = int(r + FM(GAP))
    if not ps.Collide(V(*vp), reach - 1000): return None
    lo, hi = 0, reach
    while hi - lo > 500:
        m = (lo + hi) // 2
        if ps.Collide(V(*vp), m): hi = m
        else: lo = m
    return mm(hi - r)


def judge_link(name, p, lid, chain, via_end, vp):
    """None if the link is clean, else (kind, extra).  A Chain is judged by its straight pieces."""
    chain = getattr(chain, 'pieces', None) or chain
    C, size = pad_frame(p, lid); po = p.GetOrientation().AsDegrees() % 90
    if abs(po) > 0.1 and abs(po - 90) > 0.1: return ('skip', {})
    angs = []
    for c in chain:
        d_ = (c.GetEnd().x - c.GetStart().x, c.GetEnd().y - c.GetStart().y); angs.append(math.degrees(math.atan2(abs(d_[1]), abs(d_[0]))))
    on45 = all(abs(x - 45) < 0.5 for x in angs); axial = all(abs(x) < 0.5 or abs(x - 90) < 0.5 for x in angs)
    centred_via = dist(via_end, vp) < 5000
    last = chain[-1]; A_, B_ = P(last.GetStart()), P(last.GetEnd()); L_ = dist(A_, B_)
    off = abs((B_[0] - A_[0]) * (C[1] - A_[1]) - (B_[1] - A_[1]) * (C[0] - A_[0])) / L_ if L_ else 0
    w = round(mm(max(c.GetWidth() for c in chain)), 3)
    if len(chain) == 1 and axial and centred_via and off < 10000: return None
    if len(chain) == 1 and on45 and centred_via and off < 10000: return ('via-45', dict(w=w))
    if len(chain) > 1 or not (axial or on45): return ('via-angle', dict(angles=[round(x, 1) for x in angs], nseg=len(chain), w=w))
    return ('via-offset', dict(off_pad=round(mm(off), 3), off_via=round(mm(dist(via_end, vp)), 3), w=w))


def audit(b, GAP=0.15, EXC=(), fields=(), field_refs=()):
    """(sites, stats).  fields: [(ref, courtyard box nm)] -- a via inside one is not judged against
    that part's own pads; a link to a field part's own pad (its ball's escape) is never judged,
    wherever the via lies; field_refs: parts whose pads' links are never judged."""
    segs, pads = tables(b); sites, skipped = [], collections.Counter(); nvia = nlink = 0
    balls_of = tuple(f[0] + '.' for f in fields if len(f) == 2 and f[0])   # every field part's own pads
    def exc_of(vp):
        return next((e for e in EXC if abs(e['via'][0] - mm(vp[0])) < 0.01 and abs(e['via'][1] - mm(vp[1])) < 0.01), None)
    def site(kind, v, pname, lid, at, **kw):
        vp = P(v.GetPosition()); e = exc_of(vp)
        if own: kw['field'] = ','.join(sorted(own))
        d = dict(kind=kind, net=sh(v.GetNetname()), layer=b.GetLayerName(lid), via=[round(mm(vp[0]), 4), round(mm(vp[1]), 4)],
                 via_uuid=v.m_Uuid.AsString(), pad=pname, at=[round(mm(at[0]), 3), round(mm(at[1]), 3)], **kw)
        if e: d['declared'] = e.get('why', ''); skipped['declared exception'] += 1
        sites.append(d)
    for v in b.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T: continue
        own = in_field(v, fields)
        if None in own: skipped['via inside a via-field part'] += 1; continue
        mine = lambda name: any(name.startswith(r + '.') for r in own)   # the field's own balls: lifted
        vp = P(v.GetPosition()); r = v.GetWidth(pcbnew.F_Cu) / 2
        # a via IN a pad of its own net (a capacitor's, a fine-pitch BGA's via-in-pad, filled and capped)
        # needs no link to THAT pad, and its gap to the neighbouring lands is DRC's to judge; another
        # pad's link to it is still a link (board1 RV61.2: C301.1's, to a via in C302.1's pad)
        host = {n for lid in (pcbnew.F_Cu, pcbnew.B_Cu) for n, p in pads[lid]
                if p.GetNetCode() == v.GetNetCode() and p.HitTest(v.GetPosition())}
        rts = []; links = {lid: links_of(b, v, lid, segs, pads, rts) for lid in (pcbnew.F_Cu, pcbnew.B_Cu)}
        if rts: skipped[f'run over {LINK_MAX} mm straight: a route (#48 (3))'] += len(rts)
        # the gap is lifted for the field's own balls' escape and site vias (#48 (2)): a via of a ball
        ball = any(mine(n) for n in host) or any(mine(L[0]) for ls in links.values() for L in ls)
        if host: skipped['via in a pad of its own net (via-in-pad: no link)'] += 1
        else: nvia += 1
        if ball and not host: skipped["via of its field's own ball (gap lifted)"] += 1
        for lid in (pcbnew.F_Cu, pcbnew.B_Cu):
            for name, p in ([] if host or ball else pads[lid]):
                if p.GetNetCode() != v.GetNetCode() or mine(name): continue
                g = touch_gap(p, lid, vp, r, GAP)
                if g is not None: site('via-touch', v, name, lid, vp, gap=round(g, 3))
            for name, p, chain, via_end in links[lid]:
                if name in host: continue                        # the pad the via sits in
                if any(name.startswith(r + '.') for r in field_refs): skipped['link to a via-field part'] += 1; continue
                if mine(name): skipped["link to its field's own ball"] += 1; continue
                if name.startswith(balls_of): skipped["escape link of a field's ball, via outside its courtyard"] += 1; continue
                nlink += 1; j = judge_link(name, p, lid, chain, via_end, vp)
                if j is None: continue
                if j[0] == 'skip': skipped['pad not axis-aligned'] += 1; continue
                C, _ = pad_frame(p, lid)
                site(j[0], v, name, lid, ((vp[0] + C[0]) / 2, (vp[1] + C[1]) / 2), segments=[c.m_Uuid.AsString() for c in chain], **j[1])
    return sites, dict(nvia=nvia, nlink=nlink, skipped=dict(skipped))




def live(sites, diag="last resort"):
    """The sites that count: declared ones never; via-45 only when the guide says 'never'."""
    return [s for s in sites if "declared" not in s and not (s["kind"] == "via-45" and diag != "never")]


def run(b, G, board_dir, gap=None):
    """(sites, live sites, stats) with the guide's settings and the board's exceptions."""
    import guide
    vl = G.get("via_link") or {}
    gap = gap if gap is not None else vl.get("gap")
    if gap is None:
        return None, None, None
    sites, st = audit(b, gap, C.exceptions(board_dir), guide.field_parts(b, G, pcbnew),
                      [f["ref"] for f in G.get("field_vias", [])])
    return sites, live(sites, vl.get("diag", "last resort")), st


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_board_args(ap)
    ap.add_argument("--out", help="sites JSON (default <board>/routing/vialink_sites.json)")
    ap.add_argument("--gap", type=float, help="via-to-pad gap, mm (default: the guide's)")
    a = C.args(ap)
    b = C.load(a)
    sites, lv, st = run(b, C.G, a.board, a.gap)
    if sites is None:
        C.die("the guide has no 'via to pad gap' row (and no --gap): the via-link rule is not active on this board")
    out = C.out_path(a, "vialink_sites.json")
    json.dump(sites, open(out, "w"), indent=1)
    cnt = collections.Counter(s["kind"] for s in lv)
    n45 = sum(1 for s in sites if s["kind"] == "via-45" and "declared" not in s and s not in lv)
    ndecl = sum(1 for s in sites if "declared" in s)
    print(f"{st['nvia']} vias judged, {st['nlink']} via-pad links; skipped: {st['skipped']}")
    print(f"{len(lv)} vialink sites: " + (", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) or "none")
          + f"  ({ndecl} declared" + (f", {n45} straight 45-degree links listed" if n45 else "") + f")  -> {out}")
    for s in sorted(lv, key=lambda s: (s["kind"], s["net"])):
        extra = {k: v for k, v in s.items() if k not in ("kind", "net", "layer", "via", "via_uuid", "pad", "at", "segments")}
        print(f"  {s['kind']:10s} {s['layer']:5s} {s['net']:12s} via {s['via']} pad {s['pad']:8s} {extra}")
    return 1 if lv else 0


if __name__ == "__main__":
    sys.exit(main())
