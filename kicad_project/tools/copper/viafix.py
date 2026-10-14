#!/usr/bin/env python3
"""viafix.py [--board B] [--out PREFIX] [--reach 1.0] [--maxmove 1.2] [--skip-net PREFIX,..] [--only UUID,..] [--skip UUID,..]
             [--avoid UUID:SIDE,..] [--bar UUID:x,y;..] [--no45] [--rip-fill] [--push [--maxpush 0.6] [--pushbudget 16] [--pushtime 20]]
             [--save SCRATCH.kicad_pcb]   (tools/kpy)
-- make every via-pad link meet the via-link rule (vialink.py): the via off the pad by >= GAP, joined to it by ONE
straight segment from the pad's centre to the via's centre along one of the pad's axes; a straight 45-degree link only
when no axial place exists (--no45 forbids it).  For each via vialink.py reports, on the board held in memory:

  1. its pad: the one it links to (or, with no link, the one it touches); a via linked to TWO pads takes only places on
     an axis of both, so both links are straight; three or more are left for a hand fix
  2. candidate places: on each axis of that pad, the via's copper GAP .. GAP+REACH off the pad (then the 45-degree
     diagonals); the via's present place first if it already qualifies (then only the link is re-laid)
  3. a place is legal when, on all four copper layers, other nets' copper keeps the stricter class clearance, every
     pad of its own net keeps >= GAP, holes keep 0.5 mm (min_hole_to_hole), the edge 0.5 mm, no rule area forbids vias
  4. every OTHER track that ends on the via (any layer) is re-attached to the new place: walked back to an anchor (a
     joint, a junction, a tee, another via) and re-routed in up to three 0/45-degree segments by route.py's joint
     rules (a via entry is free); a place none of whose tracks can follow is not taken
  5. the link: one segment, pad centre to via centre, as wide as the widest it replaces; duplicates and the net-tied
     polygons that filled the old place go with it

The cheapest legal place wins (no move < a short move < 45 degrees) and is applied in memory before the next via.
--push: a place blocked only by other nets' tracks or plain vias is opened by shove.py (translation or a local bump,
every joint 0/45; frozen: the guide's `frozen` row and its via-field parts); every push is undone with the fix if it fails.
--rip-fill: also rip a net-tied fill polygon round the old place (it can bring back the notch it filled -- gate it).
--avoid UUID:SIDE (x+, x-, y+, y-) bars a side, for a GND pad whose thermal spokes a link would starve.  Output is the
diff against the board as loaded:
  OUT.json (ONE route.py plan: rips, ripvias, polyrips, then tracks, vias, necks -- `route.py check` judges it and
  `route.py apply` lays it), OUT_report.txt (per via: what was done, or why it is left), OUT_moves.json (for crops.py)."""
import argparse
import collections
import json
import os
import sys
import common as CM
pcbnew = CM.pcbnew_module()                 # quiet import: wx debug lines stay off stderr
import vialink as VL
import vfcore as K
import shove as SH

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
CM.add_board_args(ap)
ap.add_argument("--out", help="plan prefix (default <board>/routing/viafix): OUT.json, OUT_report.txt, OUT_moves.json")
ap.add_argument("--reach", type=float, default=1.0); ap.add_argument("--maxmove", type=float, default=1.2)
ap.add_argument("--skip-net", default="", help="net-name prefixes whose vias another rule places (comma-separated)")
ap.add_argument("--only"); ap.add_argument("--skip"); ap.add_argument("--avoid"); ap.add_argument("--bar")
ap.add_argument("--no45", action="store_true"); ap.add_argument("--rip-fill", action="store_true")
ap.add_argument("--push", action="store_true"); ap.add_argument("--pushbudget", type=int, default=16)
ap.add_argument("--maxpush", type=float); ap.add_argument("--pushtime", type=float)
ap.add_argument("--save", help="also save the fixed board to this scratch file")
a = CM.args(ap)
CM.load(a)
out = str(CM.out_path(a, "viafix"))
REACH, MAXMOVE = a.reach, a.maxmove
SKIPNET = [x for x in a.skip_net.split(',') if x]
only = set(a.only.split(',')) if a.only else None
skipv = set(a.skip.split(',')) if a.skip else set()
AVOID = collections.defaultdict(set)
for x in (a.avoid.split(',') if a.avoid else []):
    u_, s_ = x.split(':'); AVOID[u_].add(s_)
NO45 = a.no45 or (CM.G.get("via_link") or {}).get("diag") == "never"
RIPFILL = a.rip_fill; PUSH = a.push; PUSHBUDGET = a.pushbudget
FM = pcbnew.FromMM
K.init(str(a.pcb), a.board, CM.G)
GAP = K.GAP
from vfcore import *                       # noqa: E402,F403
SH.configure()
if PUSH:
    if a.maxpush: SH.MAXPUSH = a.maxpush
    if a.pushtime: SH.BUDGET = a.pushtime
UND = SH.Undo()
BAR = collections.defaultdict(list)
for x in (a.bar.split(';') if a.bar else []):
    u_, xy = x.split(':'); BAR[u_].append(tuple(FM(float(t)) for t in xy.split(',')))

S, _st = VL.audit(b, GAP, CM.exceptions(a.board), K.FIELDS, K.FIELD_REFS)
S = [s for s in S if 'declared' not in s and not (s['kind'] == 'via-45' and not NO45)]
byvia = collections.OrderedDict()
for s in S: byvia.setdefault(s['via_uuid'], []).append(s)
report = []; MOVES = []
b.BuildConnectivity(); OPEN = [b.GetConnectivity().GetUnconnectedCount(False)]
def log(v, msg): report.append(f"{sh(v.GetNetname()):10s} via ({mm(v.GetPosition().x):.3f},{mm(v.GetPosition().y):.3f})  {msg}")
segs, pads = VL.tables(b)
for vu, ss in byvia.items():
    if (only and vu not in only) or vu in skipv: continue
    v = next((x for x in b.GetTracks() if uid(x) == vu), None)
    if v is None: continue
    net = v.GetNetname(); r = v.GetWidth(pcbnew.F_Cu) // 2; vp = P(v.GetPosition())
    if any(sh(net).startswith(x) for x in SKIPNET): log(v, f'skipped: {sh(net)} (a net whose via places another rule fixes)'); continue
    segs, pads = VL.tables(b)
    links = [(n, p, lid, ch, ve) for lid in (pcbnew.F_Cu, pcbnew.B_Cu) for n, p, ch, ve in VL.links_of(b, v, lid, segs, pads)]
    lpads = {n for n, p, lid, ch, ve in links}
    if any(n.startswith(r + '.') for r in K.FIELD_REFS for n in lpads) or any(s['pad'].startswith(r + '.') for r in K.FIELD_REFS for s in ss):
        log(v, 'skipped: a via-field part (the fab rules govern it)'); continue
    if len(lpads) > 2: log(v, f'HAND: linked to {len(lpads)} pads {sorted(lpads)}'); continue
    PADS = []
    if links:
        for n, p, l_, ch, ve in links:
            if n not in [x[0] for x in PADS]: PADS.append((n, p, l_))
    else:
        touched = sorted([s for s in ss if s['kind'] == 'via-touch'], key=lambda s: s['gap'])
        if not touched: log(v, 'HAND: no link and no touched pad'); continue
        n = touched[0]['pad']; l_ = b.GetLayerID(touched[0]['layer'])      # the closest pad is the via's own
        PADS = [(n, next(p for nn, p in pads[l_] if nn == n), l_)]
    pname = '+'.join(x[0] for x in PADS)
    if any(abs(p.GetOrientation().AsDegrees() % 90) > 0.1 and abs(p.GetOrientation().AsDegrees() % 90 - 90) > 0.1 for n, p, l_ in PADS):
        log(v, f'HAND: {pname} is not axis-aligned'); continue
    FR = [(n, p, l_) + VL.pad_frame(p, l_) for n, p, l_ in PADS]           # (name, pad, layer, centre, size)
    nm0, P_, lid, C, size = FR[0]; half = {(1, 0): size[0] // 2, (-1, 0): size[0] // 2, (0, 1): size[1] // 2, (0, -1): size[1] // 2}
    chain_uids = {uid(c) for n, p, l_, ch, ve in links for c in ch}
    # duplicates of the link: same-net segments with both ends in the via or the pad
    dup = [t for n, p, l_, *_ in FR for t in tracks(net, l_) if uid(t) not in chain_uids and
           all(dist(e, vp) <= r or p.GetEffectivePolygon(l_).Contains(V(*e)) for e in (P(t.GetStart()), P(t.GetEnd())))]
    rm_link = [c for n, p, l_, ch, ve in links for c in ch] + dup
    rm_uids = {uid(c) for c in rm_link}
    WL = []
    for n, p, l_, Cx, sz in FR:
        ws = [c.GetWidth() for nn, pp, ll, ch, ve in links if nn == n for c in ch]
        WL.append(min(max(ws or [FM(cw(net))]), min(sz)))
    wl = WL[0]
    others = [t for L in CU for t in tracks(net, L) if uid(t) not in rm_uids and (dist(P(t.GetStart()), vp) <= r or dist(P(t.GetEnd()), vp) <= r)]
    inside = [t for t in others if dist(P(t.GetStart()), vp) <= r and dist(P(t.GetEnd()), vp) <= r]   # a stub wholly inside the via goes with it
    others = [t for t in others if t not in inside and uid(t) not in {uid(x) for x in inside}]; rm_link += inside; rm_uids |= {uid(x) for x in inside}
    vpolys = [d for d in polys_all() if d.GetNetname() == net and d.GetPolyShape().Collide(V(*vp), r)]
    if vpolys and not RIPFILL: log(v, f'HAND: {pname}: a fill polygon (R17) joins this via -- re-think the fill with the via'); continue
    ctx = via_ctx(v, net, C[0], C[1])
    cands = []
    if len(FR) == 1:
        for n, h in half.items():
            if SIDES[n] in AVOID[vu]: continue
            lat = (vp[0] - C[0]) * (-n[1]) + (vp[1] - C[1]) * n[0]; axd = (vp[0] - C[0]) * n[0] + (vp[1] - C[1]) * n[1]
            if abs(lat) < 2 and axd - h - r >= FM(GAP) - 1000:       # the via already sits on this axis, far enough
                cands.append((0.0, vp, 'kept in place, link re-laid straight'))
            g = FM(GAP)
            while g <= FM(GAP + REACH) + 1:
                Np = (C[0] + n[0] * (h + r + g), C[1] + n[1] * (h + r + g))
                cands.append((dist(Np, vp) / 1e6 + 0.3 * mm(g - FM(GAP)) + 0.05, Np, f'moved {mm(dist(Np, vp)):.2f} onto the {SIDES[n]} axis, gap {mm(g):.3f}'))
                g += FM(0.025) if g < FM(GAP + 1.2) else FM(0.05)
        if not NO45:
            for d in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                for k_ in range(1, 90, 2):
                    Np = (C[0] + d[0] * FM(0.02) * k_, C[1] + d[1] * FM(0.02) * k_)
                    gg = VL.touch_gap(P_, lid, Np, r, GAP)
                    if gg is not None: continue
                    if dist(Np, C) > FM(1.6): break
                    cands.append((5.0 + dist(Np, vp) / 1e6, Np, f'45-DEGREE LINK (last resort): moved {mm(dist(Np, vp)):.2f}'))
    else:
        # one via for two pads: a place on an axis of EACH pad, so both links are straight and centred
        (na, pa, la, CA, sa), (nb_, pb, lb, CB, sb) = FR
        pts_ = []
        if abs(CA[1] - CB[1]) < 2000 or abs(CA[0] - CB[0]) < 2000:          # the pads share an axis: between them
            k_ax = 0 if abs(CA[1] - CB[1]) < 2000 else 1
            lo, hi = sorted([CA[k_ax], CB[k_ax]]); t_ = lo
            while t_ <= hi:
                pts_.append((int(t_), CA[1]) if k_ax == 0 else (CA[0], int(t_))); t_ += FM(0.025)
        else:
            pts_ = [(CA[0], CB[1]), (CB[0], CA[1])]
        for Np in pts_:
            if VL.touch_gap(pa, la, Np, r, GAP) is None and VL.touch_gap(pb, lb, Np, r, GAP) is None:
                cands.append((dist(Np, vp) / 1e6, Np, f'one via for {na} and {nb_}: moved {mm(dist(Np, vp)):.2f}, both links straight'))
    cands.sort(key=lambda c: c[0]); done = False; reasons = collections.Counter(); pushes = 0
    print(f'.. {sh(net)} via ({mm(vp[0]):.3f},{mm(vp[1]):.3f}) {pname}', file=sys.stderr, flush=True)
    for cost, Np, how in cands[:420]:
        moved = Np != vp
        if any(dist(Np, q) < FM(0.06) for q in BAR[vu]): reasons['barred (made a shape site)'] += 1; continue
        if dist(Np, vp) > FM(MAXMOVE): reasons[f'move > {MAXMOVE} mm'] += 1; continue
        m0 = UND.mark(); npush = 0
        if moved:
            why = via_legal(v, Np, ctx)
            if why and PUSH and pushes < PUSHBUDGET:
                bl = via_blockers(v, Np, ctx)
                if bl and all(k_ in ('copper', 'hole') and it_ is not None and it_.Type() in (pcbnew.PCB_TRACE_T, pcbnew.PCB_VIA_T)
                              for k_, nm_, it_, l_ in bl):
                    pushes += 1
                    circ = pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r)
                    if SH.clear(circ, net, CU, UND, hole=(Np, v.GetDrillValue() // 2)):
                        ctx2 = via_ctx(v, net, C[0], C[1]); why = via_legal(v, Np, ctx2)
                        if why: UND.rollback(m0); why = 'push left: ' + why
                        else: npush = UND.mark() - m0
                    else: UND.rollback(m0); why = 'push failed: ' + why
            if why: reasons[why] += 1; continue
        else:
            if any(s['kind'] == 'via-touch' for s in ss): reasons['still touches'] += 1; continue
        hit = None
        for (n, p, l_, Cx, sz), wx in zip(FR, WL):
            link = [Np, Cx]; obx = obstacles(net, l_, Cx[0], Cx[1])
            h_ = blocked(link, wx, obx)
            if h_ and PUSH and (h_.endswith(' track') or h_.endswith(' via')):
                m1 = UND.mark(); lseg = pcbnew.SHAPE_SEGMENT(V(*Np), V(*Cx), wx)
                if SH.clear(lseg, net, [l_], UND, keep=[(pcbnew.SHAPE_SEGMENT(V(*Np), V(*Np), 2 * r), net)]):
                    h_ = blocked(link, wx, obstacles(net, l_, Cx[0], Cx[1]))
                    if not h_: npush = UND.mark() - m0
                else: UND.rollback(m1)
            h_ = h_ or stacks(link, wx, net, l_, rm_uids)
            if h_: hit = h_; break
        if hit: UND.rollback(m0); reasons['link: ' + hit] += 1; continue
        re = []
        if moved:
            failed = None
            for t_o in others:
                path, ch = reattach(t_o, vp, r, Np, net, rm_uids)
                if path is None: failed = f'{b.GetLayerName(t_o.GetLayer())} track: {ch}'; break
                re.append((t_o, path, ch))
            if failed: UND.rollback(m0); reasons[failed] += 1; continue
        # apply, then prove the net is still whole; undo if not
        removed, added, seen = [], [], set()
        for c in rm_link + vpolys + [c for t_o, path, ch in re for c in ch]:
            if c is None or uid(c) in seen: continue
            seen.add(uid(c)); b.Remove(c); removed.append(c)
        if moved: v.SetPosition(V(*Np))
        def add(path, w_, l_):
            for k in range(len(path) - 1):
                if path[k] == path[k + 1]: continue
                n_ = pcbnew.PCB_TRACK(b); n_.SetStart(V(*path[k])); n_.SetEnd(V(*path[k + 1])); n_.SetWidth(w_); n_.SetLayer(l_)
                b.Add(n_); n_.SetNet(b.FindNet(net)); added.append(n_)
        for (n, p, l_, Cx, sz), wx in zip(FR, WL): add([Np, Cx], wx, l_)
        for t_o, path, ch in re: add(path, t_o.GetWidth(), t_o.GetLayer())
        b.BuildConnectivity(); now_open = b.GetConnectivity().GetUnconnectedCount(False)
        if now_open > OPEN[0]:
            for n_ in added: b.Remove(n_)
            for c in removed: b.Add(c)
            if moved: v.SetPosition(V(*vp))
            UND.rollback(m0); b.BuildConnectivity(); reasons['opens a connection'] += 1; continue
        MOVES.append(dict(uuid=vu, net=sh(net), pad=pname, old=[mm(vp[0]), mm(vp[1])], new=[mm(Np[0]), mm(Np[1])], how=how))
        log(v, f'FIX {pname}: {how}; link w {mm(wl):.2f}; {len(re)} track(s) re-attached; {len(vpolys)} polygon(s) and {len(dup)} duplicate(s) removed'
               + (f'; PUSHED other nets: {npush} edit(s)' if npush else ''))
        done = True; break
    if not done: log(v, f'HAND: {pname}: no legal place ({", ".join(f"{k} x{n}" for k, n in reasons.most_common(4))})')

after = {segkey(t) for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T}
vafter = {viakey(v) for v in b.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
pafter = {polykey(d) for d in polys_all()}
f6 = lambda x: round(mm(x), 6)
rips = [[sh(k[0]), k[1], f6(k[3][0]), f6(k[3][1]), f6(k[4][0]), f6(k[4][1]), f6(k[2])] for k in sorted(before - after)]
adds = [dict(net=k[0], layer=k[1], width=f6(k[2]), pts=[[f6(k[3][0]), f6(k[3][1])], [f6(k[4][0]), f6(k[4][1])]]) for k in sorted(after - before)]
vadds = [dict(net=k[0], at=[f6(k[1]), f6(k[2])], pad=f6(k[3]), drill=f6(k[4])) for k in sorted(vafter - vbefore)]
vrips = [[sh(k[0]), f6(k[1]), f6(k[2])] for k in sorted(vbefore - vafter)]
prips = [dict(net=sh(k[0]), layer=k[1], rip=True, pts=[[f6(k[2]), f6(k[3])]]) for k in sorted(pbefore - pafter)]
necks = sorted({(d['net'], d['width']) for d in adds if abs(d['width'] - cw(d['net'])) > 1e-6})
# ONE plan carries everything route.py apply needs: rips first, then the new copper
plan = dict(group=os.path.basename(out), why='viafix.py: via links straight, centre to centre, vias off their pads (vialink.py)',
            rips=[dict(net=r_[0], layer=r_[1], pts=[[r_[2], r_[3]], [r_[4], r_[5]]], width=r_[6]) for r_ in rips],
            ripvias=[dict(net=n_, at=[x_, y_]) for n_, x_, y_ in vrips],
            polyrips=[dict(net=p_['net'], layer=p_['layer'], at=p_['pts'][0]) for p_ in prips],
            tracks=adds, vias=vadds, zones=[], polys=[], thermals=bool(CM.G.get("thermals")),
            necks=[dict(net=n, width=wd, why='the width the track already had, re-laid') for n, wd in necks])
json.dump(plan, open(out + '.json', 'w'), indent=1)

open(out + '_report.txt', 'w').write('\n'.join(report) + '\n'); json.dump(MOVES, open(out + '_moves.json', 'w'), indent=1)
nfix = sum(1 for l in report if ' FIX ' in l); nhand = sum(1 for l in report if ' HAND' in l)
print('\n'.join(report)); print(f'{nfix} vias fixed, {nhand} left; {len(rips)} segments ripped, {len(adds)} added, {len(vrips)} vias moved, {len(prips)} polygons ripped')
if a.save: pcbnew.SaveBoard(str(CM.out_in_board(a, a.save)), b)
