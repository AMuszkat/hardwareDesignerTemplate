#!/usr/bin/env python3
"""A BGA's via fanout: where each ball's via goes, and how many nets can leave over several layers.

    tools/kpy tools/route/fanout.py plan   --board board1 --ref U1 --via 0.4,0.2 \
        --clearance 0.0889 --width 0.0889 --in-pad '^(VDD|NVCC|\\+)' --share '^GND$' \
        --priority '^DRAM_' --out placement/vias_U1.json
    tools/kpy tools/route/fanout.py layers --board board1 --ref U1 \
        --vias placement/vias_U1.json --layers routing/R1_layers_spec.json --out routing/R1_layers.json

Both read the footprint's balls from the board (or `--balls FILE`, a JSON list of
{ball, x, y, net, d}: a part not on a board yet, or a test).  Nothing is written to
the board: `plan` writes a fanout model (the vias file escape_bound.py, netgroup
specs and route.py plans take), `layers` writes {net: layer}.

PLAN -- the pitch decides the kind of fanout, and it is printed with its arithmetic:

  dog-bone     a via fits at the interstitial point between four balls (coarse pitch,
               typically 0.8 mm and up, often 0.65 mm).  Every ball that needs a via
               gets one there on a short diagonal stub; the matching only picks which
               of the four corners.  NONE of the fine-pitch rules below apply, and no
               via goes in a pad.
  fine pitch   no via fits between four balls (0.5 mm with a 0.4 mm via).  A via goes
               in a DEPOPULATED ball site next to its ball, on an orthogonal stub or a
               diagonal one where the stub clears the two balls it passes, else in the
               ball's own pad (via-in-pad: filled and capped, a fab option).  A maximum
               matching gives as many balls as possible a site, `--priority` nets first;
               the rest fall back to the pad.  NXP's i.MX 8M EVKs fan out this way.
  in-pad only  not even a depopulated site takes a via: every via in its pad (or HDI).

Which balls need a via, by net (regexes on the net's last path element):
  --skip      none (they leave on the footprint's own side); unconnected balls never do;
              `--skip-rings K`: own-via balls on the outer K rings neither
  --in-pad    always in the pad (fine pitch: rails whose many vias feed a pour)
  --share     a site where one is free, else (`--share-mode cluster`) joined on top to a
              same-net neighbour that has one, else in the pad -- GND.  `own` (default)
              gives every such ball its own via: lower inductance, more sites used
  the rest    their own via (each its own site)
`--fixed FILE` pre-places some vias (same format as the output) before the matching:
a site kept free for a lane, a stub turned the other way.  `--no-diag`: orthogonal only.

The plan is a CAPACITY move, not a routing: run `layers` on it.  A greedy matching can
box in a ball on an inner ring (board1: a DQS ball next to the outer ring lost its lane);
pin that ball's site with --fixed and re-plan.

LAYERS -- the most nets that can escape, over several layers at once.  Each layer is the
planar cut relaxation of escape_bound.py (the obstacles' Delaunay triangulation, each edge
passing as many tracks as fit, max-flow to outside the field); the source sets one layer
can escape form a matroid, so a matroid partition gives the EXACT maximum number of nets
placed over all layers together -- where per-layer greedy splits stall, because a pocket
of two or three balls with one exit makes per-layer capacities not add.  On an inner
layer the partition sees every non-pour via as a hole (optimistic); each layer's set is
then re-checked with its sources' own pads, and a net the exact check traps is moved off
that layer and the partition re-run.  The result is a bound: a split it passes can still
stall a router at one gap; one it fails cannot be routed with that model and those rules.

The layer file:

    {"layers": [
       {"name": "F.Cu",   "side": "component", "width": 0.0889, "clearance": 0.0889},
       {"name": "In2.Cu", "width": 0.1016, "clearance": 0.1016, "nfp": true,
        "hole_clearance": 0.1778},
       {"name": "B.Cu",   "width": 0.0889, "clearance": 0.0889},
       {"name": "In5.Cu", "width": 0.1016, "clearance": 0.1016, "nfp": true,
        "pad_nets": ["+1V8", "+3V3"], "exit": "east"}],
     "nets": ["^DRAM_"],              # regexes: the nets to place (default: every net whose via
                                      # is kind "own" in plan's output; give it for an older model)
     "fixed": {"DRAM_CK_T_A": "B.Cu"},
     "together": [["DRAM_CK_T_A", "DRAM_CK_C_A"]]}   # reported if split, not enforced

"side": "component" -- the footprint's own side: every land is an obstacle and a net
leaves from its ball, no via needed.  Any other outer layer: every via keeps its pad.
Inner: a via keeps its pad only with "nfp": false or for "pad_nets" (the layer's pours);
"exit": any | east | west | north | south (screen axes, y down), as escape_bound.py.
"pad_gap": mm -- the least copper gap between two different nets' via pads on this layer (its
sources' and its pad_nets' vias): an inner layer's pair rule; one of each closer pair moves off.
"only": [regex, ...] -- the nets this layer may take (a pour layer opened to a few low-speed
signals); a fixed net is placed where it is fixed regardless.  To fill one layer before another,
run without the second first and add it only for what is left.
Default via pad / drill from the model file's own `via` key or --via.  Exit 0: every net
placed; 1: some left.
"""
import argparse
import collections
import json
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "place"))
from escape_bound import _delaunay  # noqa: E402

EPS = 1e-6


def short(net):
    return (net or "").split("/")[-1]


# ----------------------------------------------------------------------------- the lattice
class Field:
    """A ball field: the lattice the balls sit on, which sites are populated, the land."""

    def __init__(self, balls, land=None):
        self.balls = balls
        xs = sorted({round(b["x"], 4) for b in balls})
        ys = sorted({round(b["y"], 4) for b in balls})
        dx = [q - p for p, q in zip(xs, xs[1:]) if q - p > 1e-3]
        dy = [q - p for p, q in zip(ys, ys[1:]) if q - p > 1e-3]
        self.px, self.py = min(dx), min(dy)
        self.x0, self.y0, self.x1, self.y1 = xs[0], ys[0], xs[-1], ys[-1]
        self.nx = round((self.x1 - self.x0) / self.px) + 1
        self.ny = round((self.y1 - self.y0) / self.py) + 1
        self.at = {}
        for b in balls:
            p = self.ij(b["x"], b["y"])
            if p is None:
                sys.exit(f"ball {b['ball']} at ({b['x']}, {b['y']}) is off the {self.px} x {self.py} lattice: "
                         "a staggered or mixed-pitch field is not handled")
            self.at[p] = b
        self.land = land or min(b.get("d") or 0 for b in balls) or None
        if not self.land:
            sys.exit("no land diameter: give --land")
        self.depop = [(i, j) for i in range(self.nx) for j in range(self.ny) if (i, j) not in self.at]

    def ij(self, x, y):
        i, j = round((x - self.x0) / self.px), round((y - self.y0) / self.py)
        if 0 <= i < self.nx and 0 <= j < self.ny and abs(self.x0 + i * self.px - x) < 1e-3 \
                and abs(self.y0 + j * self.py - y) < 1e-3:
            return (i, j)
        return None

    def xy(self, p):
        return (self.x0 + p[0] * self.px, self.y0 + p[1] * self.py)

    def ring(self, p):
        return min(p[0], p[1], self.nx - 1 - p[0], self.ny - 1 - p[1])

    def near(self, x, y, r):
        """Balls within r of (x, y)."""
        i0, i1 = math.floor((x - r - self.x0) / self.px), math.ceil((x + r - self.x0) / self.px)
        j0, j1 = math.floor((y - r - self.y0) / self.py), math.ceil((y + r - self.y0) / self.py)
        out = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                b = self.at.get((i, j))
                if b and math.hypot(b["x"] - x, b["y"] - y) <= r + EPS:
                    out.append(b)
        return out


def seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    L = vx * vx + vy * vy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * vx + (py - ay) * vy) / L))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def geometry(F, pad, clr, width):
    """Which kind of fanout the pitch allows, with the arithmetic behind it."""
    p_min = min(F.px, F.py)
    via_ball = F.land / 2 + clr + pad / 2              # via centre to another net's ball centre
    via_via = pad + clr                                 # two vias of different nets
    stub_ball = F.land / 2 + clr + width / 2            # a stub's centreline to another net's ball
    inter = math.hypot(F.px, F.py) / 2                  # interstitial point to its four balls
    g = dict(pitch=(F.px, F.py), land=F.land, via_ball=via_ball, via_via=via_via, stub_ball=stub_ball,
             interstitial=inter)
    g["dogbone"] = inter + EPS >= via_ball and p_min + EPS >= via_via
    g["depop"] = p_min + EPS >= via_ball and p_min + EPS >= via_via
    g["diag"] = inter + EPS >= stub_ball                 # a diagonal stub passes two balls at inter
    g["mode"] = "dog-bone" if g["dogbone"] else "fine pitch" if g["depop"] else "in-pad only"
    return g


def print_geometry(F, g, pad, drill, clr, width):
    px, py = g["pitch"]
    print(f"field {F.nx} x {F.ny} at {px:.3f} x {py:.3f} mm, {len(F.at)} balls, {len(F.depop)} depopulated sites; "
          f"land {F.land:.3f}, via {pad}/{drill}, track {width} / {clr}")
    print(f"  via between four balls: {g['interstitial']:.4f} to each ball vs land/2 + clr + via/2 = "
          f"{g['via_ball']:.4f} -> {'fits' if g['dogbone'] else 'does NOT fit'}")
    print(f"  via in a depopulated site: pitch {min(px, py):.4f} vs {g['via_ball']:.4f} (ball) and "
          f"{g['via_via']:.4f} (via) -> {'fits' if g['depop'] else 'does NOT fit'}")
    print(f"  diagonal stub between two balls: {g['interstitial']:.4f} vs land/2 + clr + w/2 = "
          f"{g['stub_ball']:.4f} -> {'fits' if g['diag'] else 'does NOT fit'}")
    print(f"MODE: {g['mode']}")


# ----------------------------------------------------------------------------- plan
def classify(F, a):
    rx = {k: re.compile(getattr(a, k)) if getattr(a, k) else None for k in ("skip", "in_pad", "share", "priority")}
    cls = {}
    for p, b in F.at.items():
        n = short(b["net"])
        if not n or n.startswith("unconnected") or n.startswith("Net-("):
            cls[p] = "none" if not n or n.startswith("unconnected") else "own"
        else:
            cls[p] = "own"
        if cls[p] == "none":
            continue
        if rx["skip"] and rx["skip"].search(n):
            cls[p] = "none"
        elif rx["in_pad"] and rx["in_pad"].search(n):
            cls[p] = "in_pad"
        elif rx["share"] and rx["share"].search(n):
            cls[p] = "share"
        elif F.ring(p) < a.skip_rings:
            cls[p] = "none"
    return cls, rx["priority"]


def candidates(F, g, b, diag, pad, clr, width):
    """Via positions for ball b, nearest first: (x, y, key)."""
    bx, by = b["x"], b["y"]
    if g["dogbone"]:
        pts = [(bx + sx * F.px / 2, by + sy * F.py / 2, ("x", round(2 * (bx + sx * F.px / 2 - F.x0) / F.px),
                round(2 * (by + sy * F.py / 2 - F.y0) / F.py))) for sx in (-1, 1) for sy in (-1, 1)]
    elif g["depop"]:
        i, j = F.ij(bx, by)
        steps = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if diag else [])
        pts = []
        for di, dj in steps:
            q = (i + di, j + dj)
            if 0 <= q[0] < F.nx and 0 <= q[1] < F.ny and q not in F.at:
                x, y = F.xy(q)
                pts.append((x, y, ("s",) + q))
    else:
        return []
    out = []
    for x, y, key in pts:
        ok = True
        for o in F.near(x, y, g["via_ball"] + F.px):
            if o is b or o["net"] == b["net"]:
                continue
            if math.hypot(o["x"] - x, o["y"] - y) + EPS < g["via_ball"] or \
                    seg_dist(o["x"], o["y"], bx, by, x, y) + EPS < g["stub_ball"]:
                ok = False
                break
        if ok:
            out.append((x, y, key))
    return out


def plan(F, g, cls, prio, a, fixed=()):
    pad, clr, width = a.pad, a.clearance, a.width
    cand = {p: candidates(F, g, F.at[p], not a.no_diag, pad, clr, width) for p, c in cls.items()
            if c in ("own", "share")}
    owner, mate, where = {}, {}, {}           # site key -> ball; ball -> site key; site key -> (x, y)
    for p, c in cand.items():
        for x, y, k in c:
            where[k] = (x, y)
    vias = []
    for v in fixed:                           # pre-placed: their site is taken, their ball served
        bp = F.ij(v["bx"], v["by"]) if "bx" in v else None
        vias.append(dict(v, kind="fixed"))
        for k, xy in list(where.items()):
            if abs(xy[0] - v["x"]) < 1e-3 and abs(xy[1] - v["y"]) < 1e-3:
                owner[k] = bp
        if bp:
            mate[bp] = None
    taken_fixed = {k for k, b in owner.items()}

    def aug(b, seen):
        for x, y, k in cand[b]:
            if k in seen or k in taken_fixed:
                continue
            seen.add(k)
            if k not in owner or aug(owner[k], seen):
                owner[k] = b
                mate[b] = k
                return True
        return False

    def order(ps):
        return sorted(ps, key=lambda p: (0 if prio and prio.search(short(F.at[p]["net"])) else 1, F.ring(p), p))

    own = order([p for p, c in cls.items() if c == "own" and p not in mate])
    share = order([p for p, c in cls.items() if c == "share" and p not in mate])
    for p in own:
        aug(p, set())
    if a.share_mode == "own":
        for p in share:
            aug(p, set())
    else:                                     # cluster: fewest vias -- a ball joins a served same-net neighbour
        served = set(mate)
        changed = True
        while changed:
            changed = False
            for p in share:
                if p in served:
                    continue
                net = F.at[p]["net"]
                nb = [(p[0] + di, p[1] + dj) for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1))]
                if any(q in served and F.at.get(q, {}).get("net") == net for q in nb):
                    served.add(p)
                    mate.setdefault(p, ("join",))
                    changed = True
                elif aug(p, set()):
                    served.add(p)
                    changed = True
    for p, k in mate.items():
        if k is None or k == ("join",):
            continue
        b = F.at[p]
        x, y = where[k]
        vias.append(dict(ball=b["ball"], net=b["net"], x=round(x, 4), y=round(y, 4), bx=b["x"], by=b["y"],
                         in_pad=False, kind=cls[p]))
    fallback = []
    for p, c in sorted(cls.items()):
        if c == "in_pad" or (c in ("own", "share") and p not in mate):
            b = F.at[p]
            vias.append(dict(ball=b["ball"], net=b["net"], x=b["x"], y=b["y"], bx=b["x"], by=b["y"],
                             in_pad=True, kind=c))
            if c != "in_pad":
                fallback.append(p)
    joined = [p for p, k in mate.items() if k == ("join",)]
    return vias, fallback, joined


def cmd_plan(a):
    a.pad, a.drill = (float(v) for v in a.via.split(","))
    balls = load_balls(a)
    F = Field(balls, a.land)
    g = geometry(F, a.pad, a.clearance, a.width)
    print_geometry(F, g, a.pad, a.drill, a.clearance, a.width)
    cls, prio = classify(F, a)
    if g["mode"] == "in-pad only":
        for p, c in cls.items():
            if c in ("own", "share"):
                cls[p] = "in_pad"
    elif g["mode"] == "dog-bone" and a.in_pad:
        print("  note: --in-pad on a dog-bone field puts those vias in their pads although a dog-bone fits")
    fixed = json.load(open(a.fixed)) if a.fixed else []
    vias, fallback, joined = plan(F, g, cls, prio, a, fixed)
    cnt = collections.Counter(c for c in cls.values())
    moved = [v for v in vias if not v["in_pad"]]
    print(f"balls: {cnt['own']} own via, {cnt['share']} shared-net, {cnt['in_pad']} in pad by rule, "
          f"{cnt['none']} no via")
    print(f"vias: {len(vias)} -- {len(moved)} off the pad ({'interstitial' if g['dogbone'] else 'depopulated sites'}), "
          f"{sum(v['in_pad'] for v in vias)} in the pad, {len(joined)} shared-net balls joined on top to a neighbour")
    if fallback:
        fb = collections.defaultdict(list)
        for p in fallback:
            fb[cls[p]].append(f"{F.at[p]['ball']}:{short(F.at[p]['net'])}")
        for c, lst in fb.items():
            print(f"  {len(lst)} {c} ball(s) found no free site -> via in the pad: {' '.join(lst[:40])}"
                  + (" ..." if len(lst) > 40 else ""))
    if a.out:
        out = Path(a.out)
        if not out.is_absolute() and a.board_dir:
            out = a.board_dir / out
        json.dump(vias, open(out, "w"), indent=0)
        print(f"wrote {out} ({len(vias)} vias)")
    return 0


# ----------------------------------------------------------------------------- layers
class Stopped(Exception):
    """`layers --timeout` ran out: raised where the time goes (a new flow question), and re-raised
    by the step it interrupted with (S, left, the step's name)."""


DEADLINE = [None]                                        # time.monotonic() to stop at, or None


class Layer:
    """One layer's escapable source sets: a flow oracle over a fixed obstacle set."""

    def __init__(self, name, obst, cand, w, c, exit_ok):
        self.name, self.cand, self.w, self.c = name, cand, w, c
        P = w + c
        pts = sorted(obst)
        tri = [tuple(pts[i] for i in t) for t in _delaunay(pts)]
        et = collections.defaultdict(list)
        for tid, t in enumerate(tri):
            for e in ((t[0], t[1]), (t[1], t[2]), (t[0], t[2])):
                et[tuple(sorted(e))].append(tid)
        hull = {p for e, ts in et.items() if len(ts) == 1 for p in e}

        def cap(p, q):
            gap = math.hypot(p[0] - q[0], p[1] - q[1]) - obst[p] - obst[q]
            return 0 if gap < -1e-9 else int(math.floor(gap / P + 1e-9)) + 1
        self.base = B = collections.defaultdict(dict)

        def add(u, v, cp):
            B[u][v] = B[u].get(v, 0) + cp
            B[v].setdefault(u, 0)
        for e, ts in et.items():
            cp = cap(*e)
            if len(ts) == 2:
                add(("t", ts[0]), ("t", ts[1]), cp)
                add(("t", ts[1]), ("t", ts[0]), cp)
            elif exit_ok is None or exit_ok((e[0][0] + e[1][0]) / 2, (e[0][1] + e[1][1]) / 2):
                add(("t", ts[0]), "T", cp)
        inc = collections.defaultdict(list)
        for tid, t in enumerate(tri):
            for p in t:
                inc[p].append(tid)
        self.att = {}
        for k, p in cand.items():
            at = [("t", t) for t in inc[p]]
            if p in hull and (exit_ok is None or exit_ok(*p)):
                at.append("T")
            self.att[k] = at
        self.cache = {}
        self._G, self._cur = None, set()

    def ok(self, nets):
        """Do these sources all escape: is the maximum flow one unit per source?

        The answer is a property of the set, so it is cached by the set; the WORK is kept too.
        The oracle holds one flow (`_cur`: the sources that each carry a unit, `_G`: its
        residual graph) and moves it to the set asked for: a source that left gives its unit
        back along its own path (`_drop`), a new one is one augmenting search (`_push`). The
        partition asks S + y and S - z + y over and over, each one or two searches from the
        flow of S instead of a flow built from nothing -- one search per source and a copy of
        the graph, which was the whole run time of `layers` on a 536-ball field."""
        key = frozenset(nets)
        hit = self.cache.get(key)
        if hit is None:
            hit = self.cache[key] = self._reach(key)
        return hit

    INF = 10 ** 6

    def _reach(self, key):
        if DEADLINE[0] is not None and time.monotonic() > DEADLINE[0]:
            raise Stopped()
        if self._G is None or len(self._cur ^ key) > len(key):
            self._G = {u: dict(v) for u, v in self.base.items()}
            self._cur = set()
        for z in sorted(self._cur - key):
            self._drop(z)
        # a source that finds no path with the others in place has none with more of them
        return all(self._push(k) for k in sorted(key - self._cur))

    def _push(self, k):
        """One more unit, from source k to the outside; False (and no trace of k) if none."""
        G, s = self._G, ("s", k)
        G[s] = {t: self.INF for t in self.att[k]}
        for t in G[s]:
            G.setdefault(t, {})[s] = 0
        par = {s: None}
        q = collections.deque([s])
        while q and "T" not in par:
            u = q.popleft()
            for v, cp in G[u].items():
                if cp > 0 and v not in par:
                    par[v] = u
                    q.append(v)
        if "T" not in par:
            for t in G.pop(s):
                del G[t][s]
            return False
        v = "T"
        while par[v] is not None:
            u = par[v]
            G[u][v] -= 1
            G[v][u] = G[v].get(u, 0) + 1
            v = u
        self._cur.add(k)
        return True

    def _drop(self, z):
        """Take source z's unit back: one unit pushed from the outside to z through the residual
        graph (the unit's own path, reversed, is such a way, so there is always one)."""
        G, s = self._G, ("s", z)
        par = {s: None}
        q = collections.deque([s])
        while q and "T" not in par:
            u = q.popleft()
            for v in G[u]:
                if v not in par and G[v].get(u, 0) > 0:      # residual v -> u, walked from z's end
                    par[v] = u
                    q.append(v)
        v = "T"
        while par[v] is not None:
            u = par[v]
            G[u][v] += 1
            G[v][u] -= 1
            v = u
        for t in G.pop(s):                                   # z's unit is gone: its edges are idle
            del G[t][s]
        self._cur.discard(z)


def partition(layers, nets, fixed):
    """Matroid partition (Edmonds): the most nets placed, each layer's set escapable."""
    S = {L.name: set() for L in layers}
    where = {}
    LY = {L.name: L for L in layers}
    try:
        for x, ln in fixed.items():
            if ln in LY and x in LY[ln].cand and LY[ln].ok(S[ln] | {x}):
                S[ln].add(x)
                where[x] = ln
        free = [x for x in sorted(nets) if x not in fixed]
        for x in free:                                       # greedy start
            for L in layers:
                if x in L.cand and L.ok(S[L.name] | {x}):
                    S[L.name].add(x)
                    where[x] = L.name
                    break
        improved = True
        while improved:
            improved = False
            for x0 in [x for x in free if x not in where]:
                par = {x0: None}
                q = collections.deque([x0])
                done = None
                while q and not done:
                    y = q.popleft()
                    for L in layers:
                        if y not in L.cand or where.get(y) == L.name:
                            continue
                        if L.ok(S[L.name] | {y}):
                            done = (y, L.name)
                            break
                        for z in sorted(S[L.name]):
                            if z not in par and z not in fixed and L.ok((S[L.name] - {z}) | {y}):
                                par[z] = (y, L.name)
                                q.append(z)
                if done:
                    y, ln = done
                    chain = [(y, ln)]
                    while par[y] is not None:
                        py_, pl = par[y]
                        chain.append((py_, pl))
                        y = py_
                    for el, _ in chain:
                        if el in where:
                            S[where[el]].discard(el)
                    for el, l2 in chain:
                        S[l2].add(el)
                        where[el] = l2
                    improved = True
    except Stopped:                                      # S is whole between two questions
        raise Stopped(S, sorted(x for x in nets if x not in where), "partition") from None
    return S, sorted(x for x in nets if x not in where)


def cmd_layers(a):
    balls = load_balls(a)
    F = Field(balls, a.land)
    spec = json.load(open(resolve(a, a.layers)))
    model = json.load(open(resolve(a, a.vias)))
    pad, drill = (float(v) for v in a.via.split(","))
    vias = {(round(v["x"], 4), round(v["y"], 4)): v for v in model}
    rx = [re.compile(r) for r in spec.get("nets", [])]
    ball_of = {}
    for b in balls:
        ball_of.setdefault(short(b["net"]), (round(b["x"], 4), round(b["y"], 4)))
    src_via = {}
    for xy, v in vias.items():
        if v.get("kind", "own") in ("own", "fixed"):
            src_via.setdefault(short(v["net"]), xy)
    nets = sorted(n for n in src_via if (not rx or any(r.search(n) for r in rx)))
    comp = [L for L in spec["layers"] if L.get("side") == "component"]
    for L in comp:                                      # nets with no via can still leave on the footprint's side
        nets = sorted(set(nets) | {n for n in ball_of if rx and any(r.search(n) for r in rx)})
    exits = {"any": None, "east": lambda x, y: x > F.x1 - 0.75 * F.px, "west": lambda x, y: x < F.x0 + 0.75 * F.px,
             "south": lambda x, y: y > F.y1 - 0.75 * F.py, "north": lambda x, y: y < F.y0 + 0.75 * F.py}
    ball_xy = {(round(b["x"], 4), round(b["y"], 4)) for b in balls}

    def obstacles(L, exact_src=()):
        w, c = L["width"], L["clearance"]
        hc = L.get("hole_clearance", c)
        r_pad = pad / 2 + c + w / 2
        r_hole = drill / 2 + hc + w / 2
        if L.get("side") == "component":
            o = {p: F.land / 2 + c + w / 2 for p in ball_xy}
            for p in vias:
                if p not in ball_xy:
                    o[p] = r_pad
            return o, {n: ball_of[n] for n in nets if n in ball_of}
        if L.get("outer"):
            return {p: r_pad for p in vias}, {n: src_via[n] for n in nets if n in src_via}
        pn = set(L.get("pad_nets", []))
        nfp = L.get("nfp", True)
        o = {p: (r_pad if (not nfp or short(v["net"]) in pn or p in exact_src) else r_hole) for p, v in vias.items()}
        return o, {n: src_via[n] for n in nets if n in src_via}

    for L in spec["layers"]:
        if L.get("side") != "component" and "outer" not in L:
            L["outer"] = L["name"] in ("F.Cu", "B.Cu")
    ban = collections.defaultdict(set)
    fixed = dict(spec.get("fixed", {}))
    t0 = time.monotonic()
    DEADLINE[0] = t0 + a.timeout if a.timeout else None
    stopped, prev = None, None
    for rnd in range(40):
        try:
            S, left, again = _layers_round(spec, rnd, obstacles, exits, nets, fixed, ban, src_via, vias, pad)
        except Stopped as e:
            S, left, step = e.args
            stopped = f"in round {rnd + 1}'s {step}"
            if prev and sum(map(len, prev.values())) > sum(map(len, S.values())):
                S, left = prev, sorted(set(nets) - set().union(*prev.values()))
                stopped += f"; the sets are round {rnd}'s, less the nets its checks moved off"
            break
        if not again:
            break
        prev = {ln: s - ban[ln] for ln, s in S.items()}  # what this round leaves standing
    DEADLINE[0] = None
    if stopped:
        print(f"STOPPED at --timeout {a.timeout:g} s {stopped}: NOT the answer. Each layer's set below "
              f"passes the bound, but more nets may fit and the exact check has not passed; "
              f"give it more time, or fewer nets or layers per run.")
    print(f"{a.ref}: {len(nets)} nets over {len(spec['layers'])} layers")
    for L in spec["layers"]:
        print(f"  {L['name']:8s} {len(S[L['name']]):3d}  {' '.join(sorted(S[L['name']]))}")
    print(f"  unplaced {len(left)}{': ' + ' '.join(left) if left else ''}")
    lay = {n: ln for ln, s in S.items() for n in s}
    for grp in spec.get("together", []):
        got = {lay.get(n) for n in grp}
        if len(got) > 1:
            print(f"  split: {', '.join(f'{n} {lay.get(n)}' for n in grp)}")
    if a.out:
        out = resolve(a, a.out, new=True)
        if stopped:                                      # never under the name a later step reads
            out = out.with_name(out.stem + ".partial.json")
        json.dump(dict(sorted(lay.items())), open(out, "w"), indent=1)
        print(f"wrote {out}")
    return 0 if not left and not stopped else 1


def _layers_round(spec, rnd, obstacles, exits, nets, fixed, ban, src_via, vias, pad):
    """One round of `layers`: partition, exact re-check, pad gaps. (S, left, another round?)"""
    layers = []
    for L in spec["layers"]:
        o, cand = obstacles(L)
        cand = {n: p for n, p in cand.items() if n not in ban[L["name"]]}
        if L.get("only"):                            # a layer open to some nets only (a pour layer's signals)
            cand = {n: p for n, p in cand.items() if any(re.search(r, n) for r in L["only"])}
        layers.append(Layer(L["name"], o, cand, L["width"], L["clearance"], exits[L.get("exit", "any")]))
    S, left = partition(layers, nets, fixed)
    bad = []
    for L in spec["layers"]:                         # exact: a source keeps its own pad on its layer
        if L.get("side") == "component" or L.get("outer") or not S[L["name"]]:
            continue
        srcs = {src_via[n]: n for n in S[L["name"]]}
        o, _ = obstacles(L, exact_src=set(srcs))
        try:
            got = exact_escape(o, srcs, L, exits[L.get("exit", "any")])
        except Stopped:
            raise Stopped(S, left, "exact check") from None
        fl, trapped = len(got), sorted(set(srcs.values()) - got)
        if fl < len(srcs):
            bad.append((L["name"], fl, len(srcs), trapped))
    # "pad_gap": two vias of different nets that both keep a pad on this layer (its sources, its pour
    # vias) must be that far apart copper to copper -- an inner layer's pair rule (board 1's D65:
    # 0.4 pads at 0.5 mm are 0.100 apart against 0.1016). One of each pair leaves the layer,
    # the one in most pairs first; a fixed net or a pour via never moves.
    pairs_moved = []
    for L in spec["layers"]:
        g = L.get("pad_gap")
        if g is None or not S[L["name"]]:
            continue
        pn = set(L.get("pad_nets", []))
        padded = {src_via[n]: n for n in S[L["name"]]}
        padded.update({p: short(v["net"]) for p, v in vias.items() if short(v["net"]) in pn})
        pts = sorted(padded)
        close = [(a_, b_) for i, a_ in enumerate(pts) for b_ in pts[i + 1:]
                 if padded[a_] != padded[b_] and math.dist(a_, b_) < pad + g - 1e-9]
        while close:
            deg = collections.Counter(padded[q] for e in close for q in e
                                      if padded[q] in S[L["name"]] and padded[q] not in fixed)
            if not deg:
                break
            n = max(sorted(deg), key=deg.get)
            ban[L["name"]].add(n)
            pairs_moved.append((L["name"], n))
            close = [e for e in close if n not in (padded[e[0]], padded[e[1]])]
    for ln, n in pairs_moved:
        print(f"  round {rnd + 1}: {ln} pad gap -- {n} moved off it")
    for ln, fl, n, trapped in bad:
        movable = [t for t in trapped if t not in fixed] or sorted(S[ln] - set(fixed))
        out = movable[-(n - fl):]                    # the excess, from the min cut
        ban[ln].update(out)
        print(f"  round {rnd + 1}: {ln} exact {fl} of {n} -- {' '.join(out)} moved off it")
    return S, left, bool(bad or pairs_moved)


def exact_escape(o, srcs, L, exit_ok):
    """The sources that escape with per-obstacle radii (each source its own pad): a greedy
    grow, exact on a matroid."""
    lay = Layer(L["name"], o, {n: p for p, n in srcs.items()}, L["width"], L["clearance"], exit_ok)
    names = sorted(srcs.values())
    if lay.ok(set(names)):
        return set(names)
    got = set()
    for n in names:
        if lay.ok(got | {n}):
            got.add(n)
    return got


# ----------------------------------------------------------------------------- input
def resolve(a, f, new=False):
    p = Path(f)
    if p.is_absolute() or p.exists() or not a.board_dir:
        return p
    q = a.board_dir / p
    return q if (new or q.exists()) else p


def load_balls(a):
    a.board_dir = None
    if a.balls:
        return json.load(open(a.balls))
    import place as P
    a.board_dir = P.board_dir_arg(a.board)
    B = P.Board(a.board_dir)
    f = B.fps.get(a.ref) or P.die(f"{a.ref} is not on the board")
    out = []
    for p in f.Pads():
        if not p.GetNumber():
            continue
        try:
            s = p.GetSize()
        except TypeError:
            s = p.GetSize(B.pn.F_Cu)
        out.append(dict(ball=p.GetNumber(), x=p.GetPosition().x / 1e6, y=p.GetPosition().y / 1e6,
                        net=p.GetNetname(), d=min(s.x, s.y) / 1e6))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "layers"):
        s = sub.add_parser(name)
        s.add_argument("--board")
        s.add_argument("--ref", default="U1")
        s.add_argument("--balls", help="JSON [{ball, x, y, net, d}] instead of the board")
        s.add_argument("--land", type=float, help="ball land diameter (default: the smallest pad)")
        s.add_argument("--via", default="0.4,0.2", help="via pad,drill (mm)")
        s.add_argument("--out")
    p = sub.choices["plan"]
    p.add_argument("--clearance", type=float, required=True)
    p.add_argument("--width", type=float, required=True, help="the stub's width")
    p.add_argument("--skip", help="nets with no via (they leave on the footprint's side)")
    p.add_argument("--skip-rings", type=int, default=0, help="own-via balls on the outer K rings: no via")
    p.add_argument("--in-pad", help="nets whose vias stay in the pad")
    p.add_argument("--share", help="nets whose balls may share (GND)")
    p.add_argument("--share-mode", choices=["own", "cluster"], default="own")
    p.add_argument("--priority", help="nets matched first")
    p.add_argument("--no-diag", action="store_true", help="orthogonal stubs only")
    p.add_argument("--fixed", help="pre-placed vias (the output's format)")
    q = sub.choices["layers"]
    q.add_argument("--vias", required=True, help="the fanout model (plan's output)")
    q.add_argument("--layers", required=True, help="the layer file (see above)")
    q.add_argument("--timeout", type=float, default=0,
                   help="seconds; stops at the next flow question with the split so far printed and written "
                        "as <out>.partial.json, exit 1 (use this, not a `timeout` prefix)")
    a = ap.parse_args()
    return cmd_plan(a) if a.cmd == "plan" else cmd_layers(a)


if __name__ == "__main__":
    sys.exit(main())
