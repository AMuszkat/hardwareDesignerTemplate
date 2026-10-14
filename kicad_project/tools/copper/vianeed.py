#!/usr/bin/env python3
"""vianeed.py [--board B] [--out OUT.json] [--side-reach 0.3]  (tools/kpy)
-- for every via the via-link gate (vialink.py) still flags: WHAT IS IN THE WAY of a legal place.  Read-only.

For each via: its pad (the one it links to, or the one it touches), then on each of that pad's four axes the first place
where the via clears the pad by GAP + 0.01 (and a few more out to --side-reach further), and at each place EVERY blocker
(vfcore.via_blockers: other nets' copper on any layer at the stricter clearance, holes at min_hole_to_hole, pads of its
own net, the edge, rule areas) plus anything another net has across the straight link from the pad's centre.  A side
whose only blockers are tracks is one a track push can open ('tracks'); one with a via in the way needs that via moved
('via'); a pad or a hole of a part needs the part moved ('part').  The summary ranks the sides of each via by that.
Output: OUT.json (per via, per side, per place: the blockers) and a table on stdout."""
import argparse
import collections
import json
import common as CM
pcbnew = CM.pcbnew_module()                 # quiet import: wx debug lines stay off stderr
import vialink as VL
import vfcore as K

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
CM.add_board_args(ap)
ap.add_argument("--out", help="default <board>/routing/vianeed.json")
ap.add_argument("--side-reach", type=float, default=0.3)
a = CM.args(ap)
CM.load(a)
b = K.init(str(a.pcb), a.board, CM.G); GAP = K.GAP; SR = a.side_reach; EXC = CM.exceptions(a.board)
out = CM.out_path(a, "vianeed.json")
FM, mm, V, P, uid, sh, dist = K.FM, K.mm, K.V, K.P, K.uid, K.sh, K.dist

def pad_of(v, ss):
    segs, pads = VL.tables(b)
    links = [(n, p, lid) for lid in (pcbnew.F_Cu, pcbnew.B_Cu) for n, p, ch, ve in VL.links_of(b, v, lid, segs, pads)]
    names = {n for n, p, lid in links}
    if any(n.startswith(r + '.') for r in K.FIELD_REFS for n in names) or any(s['pad'].startswith(r + '.') for r in K.FIELD_REFS for s in ss): return 'a via-field part', None, None
    if len(names) > 1: return f'linked to {len(names)} pads {sorted(names)}', None, None
    if links: return links[0]
    t = sorted([s for s in ss if s['kind'] == 'via-touch'], key=lambda s: s['gap'])
    if not t: return 'no link, no touched pad', None, None
    lid = b.GetLayerID(t[0]['layer']); return t[0]['pad'], next(p for n, p in pads[lid] if n == t[0]['pad']), lid

def klass(bl):
    """what a set of blockers needs: '' (free), 'tracks', 'via', 'part', 'edge'"""
    ks = set()
    for kind, name, it, lid in bl:
        if kind == 'edge' or kind == 'rule': ks.add('edge')
        elif kind == 'own-pad' or (kind == 'hole' and it.Type() != pcbnew.PCB_VIA_T): ks.add('part')
        elif kind == 'hole' or (it is not None and it.Type() == pcbnew.PCB_VIA_T): ks.add('via')
        elif it is not None and it.Type() == pcbnew.PCB_PAD_T: ks.add('part')
        else: ks.add('tracks')
    for k in ('edge', 'part', 'via', 'tracks'):
        if k in ks: return k
    return ''

sites, _ = VL.audit(b, GAP, EXC, K.FIELDS, K.FIELD_REFS)
byvia = collections.OrderedDict()
for s in sites:
    if 'declared' not in s: byvia.setdefault(s['via_uuid'], []).append(s)
res = []; table = collections.Counter()
for vu, ss in byvia.items():
    v = next(x for x in b.GetTracks() if uid(x) == vu); net = v.GetNetname(); r = v.GetWidth(pcbnew.F_Cu) // 2; vp = P(v.GetPosition())
    pname, pad, lid = pad_of(v, ss)
    row = dict(via=[round(mm(vp[0]), 3), round(mm(vp[1]), 3)], uuid=vu, net=sh(net), kinds=sorted({s['kind'] for s in ss}), pad=pname, sides={})
    if pad is None: row['best'] = 'hand: ' + pname; res.append(row); table[row['best'].split(' [')[0]] += 1; continue
    C, size = VL.pad_frame(pad, lid); half = {(1, 0): size[0] // 2, (-1, 0): size[0] // 2, (0, 1): size[1] // 2, (0, -1): size[1] // 2}
    ctx = K.via_ctx(v, net, C[0], C[1]); obL = K.obstacles(net, lid, C[0], C[1])
    for n, h in half.items():
        places = []
        g = FM(GAP + 0.01)
        while g <= FM(GAP + 0.01 + SR) + 1:
            Np = (int(C[0] + n[0] * (h + r + g)), int(C[1] + n[1] * (h + r + g)))
            bl = K.via_blockers(v, Np, ctx)
            seg = pcbnew.SHAPE_SEGMENT(V(*Np), V(*C), max(FM(K.cw(net)), 1))
            bl += [('copper', nm + ' (link)', it, lid) for shp, c, nm, it in obL if shp.Collide(seg, c - FM(0.001))]
            places.append(dict(at=[round(mm(Np[0]), 3), round(mm(Np[1]), 3)], gap=round(mm(g), 3), move=round(mm(dist(Np, vp)), 3),
                               needs=klass(bl), blockers=sorted({f"{k}: {nm}" for k, nm, it, l_ in bl})))
            g += FM(0.05)
        row['sides'][K.SIDES[n]] = places
    order = {'': 0, 'tracks': 1, 'via': 2, 'part': 3, 'edge': 4}
    best = min(((order[p['needs']], p['move'], sd, p) for sd, ps in row['sides'].items() for p in ps), key=lambda t: t[:2])
    row['best'] = (best[3]['needs'] or 'free') + f" ({best[2]} {best[3]['move']:.2f} mm: " + '; '.join(best[3]['blockers'][:4]) + ')'
    res.append(row); table[best[3]['needs'] or 'free'] += 1
json.dump(res, open(out, 'w'), indent=1)
print(f'{len(res)} vias with a live site; what the cheapest place needs: ' + ', '.join(f'{k} {n}' for k, n in table.most_common()))
for row in sorted(res, key=lambda r: (r['best'].split(' ')[0], r['net'], r['via'])):
    print(f"{row['net']:10s} via {str(row['via']):18s} {row['pad']:9s} {','.join(k.replace('via-', '') for k in row['kinds']):18s} {row['best'][:150]}")
