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
Not judged: vias inside the guide's `via field` parts (a BGA's dog-bones),
links to those parts' pads, and vias with no link to a pad unless they touch
one.  A link is the chain of segments from a via to a pad through plain
joints, at most 4 segments and 3 mm.  A site declared in
<board>/routing/vialink_exceptions.json ([{"via": [x, y], "why": ...}]) is
listed as declared and does not fail.  Importable: audit(b, gap, exc, fields,
field_refs) -> (sites, stats); live(sites, diag)."""
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


def pad_frame(p, lid):
    o = p.GetEffectivePolygon(lid).Outline(0)
    xs = [o.CPoint(i).x for i in range(o.PointCount())]; ys = [o.CPoint(i).y for i in range(o.PointCount())]
    return ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2), (max(xs) - min(xs), max(ys) - min(ys))


def in_field(v, fields):
    """A via inside a via-field part's courtyard (the fab rules' business, not this audit's)."""
    p = v.GetPosition()
    return any(x0 <= p.x <= x1 and y0 <= p.y <= y1 for x0, y0, x1, y1 in fields)


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


def links_of(b, v, lid, segs, pads):
    """the chains from via v to a pad of its net on layer lid: [(pad name, pad, [segments], via end)]"""
    vp = P(v.GetPosition()); r = v.GetWidth(pcbnew.F_Cu) / 2; out = []
    ss = segs[(v.GetNetCode(), lid)]
    vias_net = [o for o in b.GetTracks() if o.Type() == pcbnew.PCB_VIA_T and o.GetNetCode() == v.GetNetCode()]
    for t0 in ss:
        ends = [P(t0.GetStart()), P(t0.GetEnd())]
        if min(dist(e, vp) for e in ends) > r: continue
        chain = [t0]; cur = max(ends, key=lambda e: dist(e, vp)); via_end = min(ends, key=lambda e: dist(e, vp)); hit = None
        for _ in range(4):
            hp = next(((n, p) for n, p in pads[lid] if p.GetNetCode() == v.GetNetCode() and p.GetEffectivePolygon(lid).Contains(V(*cur))), None)
            if hp: hit = hp; break
            if any(dist(P(o.GetPosition()), cur) <= o.GetWidth(pcbnew.F_Cu) / 2 for o in vias_net): break
            used = {c.m_Uuid.AsString() for c in chain}
            nxt = [o for o in ss if o.m_Uuid.AsString() not in used and (dist(P(o.GetStart()), cur) < SNAP or dist(P(o.GetEnd()), cur) < SNAP)]
            if len(nxt) != 1 or sum(c.GetLength() for c in chain) > FM(3.0): break
            o = nxt[0]; chain.append(o); cur = P(o.GetEnd()) if dist(P(o.GetStart()), cur) < SNAP else P(o.GetStart())
        if hit: out.append((hit[0], hit[1], chain, via_end))
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
    """None if the link is clean, else (kind, extra)"""
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
    """(sites, stats).  fields: courtyard boxes (nm) whose vias are not judged;
    field_refs: parts whose pads' links are not judged."""
    segs, pads = tables(b); sites, skipped = [], collections.Counter(); nvia = nlink = 0
    def exc_of(vp):
        return next((e for e in EXC if abs(e['via'][0] - mm(vp[0])) < 0.01 and abs(e['via'][1] - mm(vp[1])) < 0.01), None)
    def site(kind, v, pname, lid, at, **kw):
        vp = P(v.GetPosition()); e = exc_of(vp)
        d = dict(kind=kind, net=sh(v.GetNetname()), layer=b.GetLayerName(lid), via=[round(mm(vp[0]), 4), round(mm(vp[1]), 4)],
                 via_uuid=v.m_Uuid.AsString(), pad=pname, at=[round(mm(at[0]), 3), round(mm(at[1]), 3)], **kw)
        if e: d['declared'] = e.get('why', ''); skipped['declared exception'] += 1
        sites.append(d)
    for v in b.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T: continue
        if in_field(v, fields): skipped['via inside a via-field part'] += 1; continue
        nvia += 1; vp = P(v.GetPosition()); r = v.GetWidth(pcbnew.F_Cu) / 2
        for lid in (pcbnew.F_Cu, pcbnew.B_Cu):
            for name, p in pads[lid]:
                if p.GetNetCode() != v.GetNetCode(): continue
                g = touch_gap(p, lid, vp, r, GAP)
                if g is not None: site('via-touch', v, name, lid, vp, gap=round(g, 3))
            for name, p, chain, via_end in links_of(b, v, lid, segs, pads):
                if any(name.startswith(r + '.') for r in field_refs): skipped['link to a via-field part'] += 1; continue
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
    sites, st = audit(b, gap, C.exceptions(board_dir), guide.field_boxes(b, G, pcbnew),
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
