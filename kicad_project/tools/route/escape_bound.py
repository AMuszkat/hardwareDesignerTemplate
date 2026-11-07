#!/usr/bin/env python3
"""How many nets CAN escape a BGA's ball field on one layer -- an upper bound no router beats.

    tools/kpy tools/route/escape_bound.py --board board1 --ref U1 --layer In2.Cu \
        --spec <board>/routing/R1_esc_In2_spec.json --vias <board>/placement/vias_U1.json \
        --width 0.0889 --clearance 0.0889 --nfp

When a negotiated escape (netgroup.py) will not converge, this says whether ANY router
could: a planar cut relaxation.  The ball lattice is triangulated; each triangle edge
gets the most tracks that can cross it (an obstacle end keeps the track's centre R away,
two tracks of different nets are width + clearance apart along it); max-flow from the
escaping nets' vias to outside the lattice.  Every real routing is such a flow, so

  * bound < N  -> PROVEN infeasible on this via model, this rule and this layer; the
    min cut (printed: the balls on it, their nets) is where the wall is;
  * bound == N -> not excluded (not proven routable: the router still has to find it).

Obstacles on an inner layer are the vias: a net's own via (its source) keeps its pad,
R = pad/2 + clearance + width/2; every other via is R = drill/2 + copper-to-hole +
width/2 with --nfp (non-functional pads removed, the fab's option) or the pad otherwise.
A source on the lattice's outer ring escapes for free.  Inner layers only: on the
footprint's own side every land is an obstacle (not modelled).

Sources: `--spec` (a netgroup spec: each net's `src` that sits on a ball) or `--sources`
(JSON {name: [x, y]}).  Vias: `--vias` (JSON [{x, y}, ...], e.g. a fanout model) or the
board's vias inside the footprint's lattice.  Exit 0 not excluded, 1 proven short.

Calibrate once against an escape the router DID solve (the bound must reach its N).
"""
import argparse
import collections
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "place"))


def flow_bound(lat, vias, srcs, w, c, r_own, r_other):
    """lat: {(i, j): (x, y)} every lattice site; vias: set of (i, j); srcs: {(i, j): name}."""
    NX = max(i for i, _ in lat) + 1
    NY = max(j for _, j in lat) + 1
    P = w + c
    R = {p: (r_own if p in srcs else r_other) for p in vias}

    def xy(p):
        return lat[p]

    def cap(a, b):
        (ax, ay), (bx, by) = xy(a), xy(b)
        g = math.hypot(ax - bx, ay - by) - R.get(a, 0) - R.get(b, 0)
        return 0 if g < -1e-9 else int(math.floor(g / P + 1e-9)) + 1

    tri, et = [], collections.defaultdict(list)
    for i in range(NX - 1):
        for j in range(NY - 1):
            a, b, cc, d = (i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)
            # the diagonal joining two obstacles, when there is one: the tighter relaxation
            T = [(a, b, cc), (a, cc, d)] if ((a in R and cc in R) or not (b in R and d in R)) else [(a, b, d), (b, cc, d)]
            for t in T:
                tid = len(tri)
                tri.append(t)
                for e in ((t[0], t[1]), (t[1], t[2]), (t[0], t[2])):
                    et[tuple(sorted(e))].append(tid)
    G = collections.defaultdict(dict)
    INF = 10 ** 6

    def add(u, v, cp):
        G[u][v] = G[u].get(v, 0) + cp
        G[v].setdefault(u, 0)

    for e, ts in et.items():
        cp = cap(*e)
        if len(ts) == 2:
            add(("t", ts[0]), ("t", ts[1]), cp)
            add(("t", ts[1]), ("t", ts[0]), cp)
        else:
            add(("t", ts[0]), "T", cp)
    inc = collections.defaultdict(list)
    for tid, t in enumerate(tri):
        for p in t:
            inc[p].append(tid)
    for p in srcs:
        add("S", ("s", p), 1)
        if p[0] in (0, NX - 1) or p[1] in (0, NY - 1):
            add(("s", p), "T", INF)
        for tid in inc[p]:
            add(("s", p), ("t", tid), INF)
    f = 0
    while True:
        par = {"S": None}
        q = collections.deque(["S"])
        while q and "T" not in par:
            u = q.popleft()
            for v, cp in G[u].items():
                if cp > 0 and v not in par:
                    par[v] = u
                    q.append(v)
        if "T" not in par:
            break
        v, m = "T", INF
        while par[v] is not None:
            m = min(m, G[par[v]][v])
            v = par[v]
        v = "T"
        while par[v] is not None:
            u = par[v]
            G[u][v] -= m
            G[v][u] += m
            v = u
        f += m
    seen = {"S"}
    q = collections.deque(["S"])
    while q:
        u = q.popleft()
        for v, cp in G[u].items():
            if cp > 0 and v not in seen:
                seen.add(v)
                q.append(v)
    trapped = sorted(srcs[p] for p in srcs if ("s", p) in seen)
    cut = []
    for e, ts in et.items():
        ins = [("t", x) in seen for x in ts]
        if (len(ts) == 2 and ins[0] != ins[1]) or (len(ts) == 1 and ins[0]):
            cut.append((e, cap(*e)))
    return f, trapped, cut


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--layer", required=True, help="an inner layer (for the report)")
    ap.add_argument("--spec", help="netgroup spec: nets' src on the balls are the sources")
    ap.add_argument("--sources", help="JSON {name: [x, y]}")
    ap.add_argument("--vias", help="JSON [{x, y}, ...]; default: the board's vias on the lattice")
    ap.add_argument("--width", type=float, required=True)
    ap.add_argument("--clearance", type=float, required=True)
    ap.add_argument("--pad", type=float, default=0.4)
    ap.add_argument("--drill", type=float, default=0.2)
    ap.add_argument("--hole-clearance", type=float, help="copper to hole (default: the board's)")
    ap.add_argument("--nfp", action="store_true", help="other nets' vias have no pad on this layer")
    a = ap.parse_args()
    import place as P
    a.board = P.board_dir_arg(a.board)
    B = P.Board(a.board)
    pn = B.pn
    f = B.fps.get(a.ref) or P.die(f"{a.ref} is not on the board")
    MM = 1e6
    balls = [(p.GetNumber(), p.GetPosition().x / MM, p.GetPosition().y / MM, p.GetNetname())
             for p in f.Pads() if p.GetNumber()]
    xs = sorted({round(x, 4) for _, x, _, _ in balls})
    ys = sorted({round(y, 4) for _, _, y, _ in balls})
    px = min(b - a_ for a_, b in zip(xs, xs[1:]))
    py = min(b - a_ for a_, b in zip(ys, ys[1:]))
    NX, NY = round((xs[-1] - xs[0]) / px) + 1, round((ys[-1] - ys[0]) / py) + 1

    def ij(x, y):
        i, j = round((x - xs[0]) / px), round((y - ys[0]) / py)
        ok = 0 <= i < NX and 0 <= j < NY and abs(xs[0] + i * px - x) < 1e-3 and abs(ys[0] + j * py - y) < 1e-3
        return (i, j) if ok else None

    lat = {(i, j): (xs[0] + i * px, ys[0] + j * py) for i in range(NX) for j in range(NY)}
    ball = {ij(x, y): (n, net) for n, x, y, net in balls}
    if a.vias:
        vxy = [(v["x"], v["y"]) for v in json.load(open(a.vias))]
    else:
        vxy = [(t.GetPosition().x / MM, t.GetPosition().y / MM) for t in B.b.GetTracks()
               if t.GetClass() == "PCB_VIA"]
    vias = {p for p in (ij(x, y) for x, y in vxy) if p}
    if a.spec:
        S = {k: n["src"] for k, n in json.load(open(a.spec))["nets"].items()}
    elif a.sources:
        S = json.load(open(a.sources))
    else:
        P.die("--spec or --sources")
    srcs = {}
    for k, xy in S.items():
        p = ij(*xy)
        if p:
            srcs[p] = k
            vias.add(p)
    hc = a.hole_clearance if a.hole_clearance is not None else B.b.GetDesignSettings().m_HoleClearance / MM
    r_own = a.pad / 2 + a.clearance + a.width / 2
    r_other = (a.drill / 2 + hc + a.width / 2) if a.nfp else r_own
    fl, trapped, cut = flow_bound(lat, vias, srcs, a.width, a.clearance, r_own, r_other)
    print(f"{a.ref} {a.layer}: {len(srcs)} nets leave on in-pad vias here ({len(S) - len(srcs)} sources off the lattice "
          f"ignored); {len(vias)} vias; lattice {NX} x {NY} at {px:.3f} x {py:.3f}; track {a.width} / {a.clearance}, "
          f"R own {r_own:.4f}, other {r_other:.4f}{' (holes: --nfp)' if a.nfp else ''}")
    if fl >= len(srcs):
        print(f"BOUND {fl} of {len(srcs)}: not excluded")
        return 0
    print(f"BOUND {fl} of {len(srcs)}: PROVEN SHORT by {len(srcs) - fl}")
    print(f"  inside the min cut ({len(trapped)}): {' '.join(trapped)}")
    open_ = [(e, cp) for e, cp in cut if cp]
    print(f"  the cut passes {sum(cp for _, cp in cut)} tracks, through:")
    for (p, q), cp in sorted(open_):
        print(f"    {ball.get(p, ('-', ''))[0]:>4s} - {ball.get(q, ('-', ''))[0]:<4s} {cp}")
    sys.stdout.flush()
    return 1


if __name__ == "__main__":
    sys.exit(main())
