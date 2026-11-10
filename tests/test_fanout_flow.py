#!/usr/bin/env python3
"""
fanout.py's escape oracle (Layer.ok) answers as a flow built from nothing does.

    python3 tests/test_fanout_flow.py

`Layer.ok(nets)` says whether every source of a set escapes a layer at once
-- a maximum flow.  It keeps ONE flow and moves it to the set asked for (a
source dropped gives its unit back, a source added is one search); the
partition over the layers asks thousands of sets that differ by one or two
sources, and building each flow from nothing was the whole run time of
`fanout.py layers` (35 s -> 5 s on the large fixture).  The answer is a
property of the set alone, so the two must agree on EVERY set, in any order
of asking.  Asserted, on ball fields with seeded random radii and sources:

  * walks -- add one, drop one, swap one, jump to an unrelated set, ask a
    set again after the cache is emptied -- each answer against REFERENCE,
    the plain algorithm (a copy of the graph, one search per source);
  * `partition` (Edmonds' matroid partition over three layers, tight enough
    that sources are exchanged between layers and some stay unplaced) and
    `exact_escape`, run on the oracle and on the reference: the same sets;
  * the oracle's flow stays a flow: after every walk each source it holds
    carries exactly one unit and no edge is over its capacity;
  * `layers --timeout`: a partition stopped after k new questions (a counted
    clock, every k in a stride) hands back sets the reference accepts, each
    net on one layer at most, and the SAME layers then answer a whole
    partition as if never interrupted.

Standard library only.  Last line: PASS / FAIL, with the counts and both times.
"""
import collections
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kicad_project" / "tools" / "route"))
import fanout                                                    # noqa: E402


def reference(lay, nets):
    """The flow built from nothing: the oracle's definition."""
    G = {u: dict(v) for u, v in lay.base.items()}
    INF = 10 ** 6
    for k in nets:
        s = ("s", k)
        G.setdefault("S", {})[s] = 1
        G.setdefault(s, {})["S"] = 0
        for t in lay.att[k]:
            G[s][t] = INF
            G.setdefault(t, {}).setdefault(s, 0)
    f = 0
    while f < len(nets):
        par = {"S": None}
        q = collections.deque(["S"])
        while q and "T" not in par:
            u = q.popleft()
            for v, cp in G.get(u, {}).items():
                if cp > 0 and v not in par:
                    par[v] = u
                    q.append(v)
        if "T" not in par:
            break
        v = "T"
        while par[v] is not None:
            u = par[v]
            G[u][v] -= 1
            G[v][u] = G[v].get(u, 0) + 1
            v = u
        f += 1
    return f == len(nets)


class RefLayer(fanout.Layer):
    def ok(self, nets):
        key = frozenset(nets)
        if key not in self.cache:
            self.cache[key] = reference(self, key)
        return self.cache[key]


def field(rng, n, pitch, r_lo, r_hi, n_src, cls=fanout.Layer, exit_ok=None, w=0.09, c=0.09):
    """An n x n field of round obstacles; n_src of them are sources."""
    obst = {(round(i * pitch, 4), round(j * pitch, 4)): round(rng.uniform(r_lo, r_hi), 4)
            for i in range(n) for j in range(n)}
    src = rng.sample(sorted(obst), n_src)
    cand = {f"N{k:03d}": p for k, p in enumerate(src)}
    return cls("L", obst, cand, w, c, exit_ok), obst, cand


def sane(lay):
    """The kept flow: one unit out of each held source, nothing over capacity."""
    if lay._G is None:
        return None
    G = lay._G
    for k in lay._cur:
        out = sum(lay.INF - cp for cp in G[("s", k)].values())
        if out != 1:
            return f"source {k} carries {out} units"
    if {u[1] for u in G if u != "T" and u[0] == "s"} != lay._cur:
        return "a source node the flow does not hold is in the graph"
    for u, vs in G.items():
        for v, cp in vs.items():
            if cp < 0:
                return f"edge {u} -> {v} is over its capacity by {-cp}"
    return None


def walks(rng, bad):
    asked = t_new = t_ref = 0
    for trial in range(6):
        n = rng.choice((7, 9, 11))
        lay, _obst, cand = field(rng, n, 0.5, 0.14, 0.2, rng.randrange(n * 2, n * 4))
        names = sorted(cand)
        cur = set()
        for step in range(260):
            r = rng.random()
            if r < 0.45 and len(cur) < len(names):
                cur = cur | {rng.choice([x for x in names if x not in cur])}
            elif r < 0.65 and cur:
                cur = cur - {rng.choice(sorted(cur))}
            elif r < 0.9 and cur and len(cur) < len(names):
                cur = (cur - {rng.choice(sorted(cur))}) | {rng.choice([x for x in names if x not in cur])}
            elif r < 0.96:
                cur = set(rng.sample(names, rng.randrange(0, len(names) + 1)))
            else:
                lay.cache.clear()                       # the same sets again, from another flow
            t = time.perf_counter()
            got = lay.ok(cur)
            t_new += time.perf_counter() - t
            t = time.perf_counter()
            want = reference(lay, cur)
            t_ref += time.perf_counter() - t
            asked += 1
            if got != want:
                bad.append(f"walk {trial} step {step}: {len(cur)} sources, oracle {got}, reference {want}")
                break
            if not got and len(cur) > 1 and rng.random() < 0.5:
                cur = cur - {rng.choice(sorted(cur))}      # stay near the edge of what fits
        why = sane(lay)
        if why:
            bad.append(f"walk {trial}: {why}")
    return asked, t_new, t_ref


EXITS = (lambda x, y: x > 3.6, lambda x, y: x < 0.4, None)        # east edge, west edge, any: three unlike layers
TIGHT = ((0.2, 0.27), (0.2, 0.27), (0.22, 0.28), (0.22, 0.28))    # obstacle radii: some channels shut, few spare


def partitions(rng, bad):
    moved = left = 0
    t_new = t_ref = 0.0
    for trial in range(4):
        seed = rng.randrange(10 ** 9)
        res = []
        for cls in (fanout.Layer, RefLayer):
            r2 = random.Random(seed)
            n = 9
            obst = {(round(i * 0.5, 4), round(j * 0.5, 4)): 0.0 for i in range(n) for j in range(n)}
            src = r2.sample(sorted(obst), 54)
            names = {p: f"N{k:03d}" for k, p in enumerate(src)}
            layers = []
            for li in range(3):
                o = {p: round(r2.uniform(*TIGHT[trial]), 4) for p in obst}
                # not every source may use every layer
                cand = {nm: p for p, nm in names.items() if r2.random() < 0.8}
                layers.append(cls(f"L{li}", o, cand, 0.09, 0.09, EXITS[li]))
            nets = sorted(names.values())
            fixed = {nets[0]: "L1", nets[1]: "L2"}
            t = time.perf_counter()
            S, un = fanout.partition(layers, nets, fixed)
            dt = time.perf_counter() - t
            ex = fanout.exact_escape({p: round(r2.uniform(0.16, 0.2), 4) for p in obst},
                                     {p: nm for p, nm in list(names.items())[:40]},
                                     {"name": "X", "width": 0.09, "clearance": 0.09}, None)
            res.append(({k: sorted(v) for k, v in S.items()}, un, sorted(ex)))
            if cls is fanout.Layer:
                t_new += dt
                for L in layers:
                    why = sane(L)
                    if why:
                        bad.append(f"partition {trial} {L.name}: {why}")
            else:
                t_ref += dt
        if res[0] != res[1]:
            bad.append(f"partition {trial}: the oracle and the reference disagree "
                       f"(unplaced {len(res[0][1])} / {len(res[1][1])})")
        left += sum(1 for x in res[0][1] if any(x in L.cand for L in layers))   # had a layer, lost it
        moved += sum(len(v) for v in res[0][0].values())
    return moved, left, t_new, t_ref


class Ticks:
    """fanout's clock for the stop test: one tick per question that reaches the flow."""
    def __init__(self):
        self.n = 0

    def monotonic(self):
        self.n += 1
        return self.n


def stops(rng, bad):
    n = 9
    obst = {(round(i * 0.5, 4), round(j * 0.5, 4)): 0.0 for i in range(n) for j in range(n)}
    for _ in range(50):                                  # an instance that exchanges and leaves some over
        r2 = random.Random(rng.randrange(10 ** 9))
        names = {p: f"N{k:03d}" for k, p in enumerate(r2.sample(sorted(obst), 54))}
        spec = []
        for li in range(3):
            spec.append((f"L{li}", {p: round(r2.uniform(*TIGHT[0]), 4) for p in obst},
                         {nm: p for p, nm in names.items() if r2.random() < 0.8}, EXITS[li]))
        nets = sorted(names.values())
        fixed = {nets[0]: "L2"}

        def build(cls):
            return [cls(nm, o, cand, 0.09, 0.09, ex) for nm, o, cand, ex in spec]
        want = fanout.partition(build(fanout.Layer), nets, fixed)
        if any(x in cand for x in want[1] for _, _, cand, _ in spec):
            break
    else:
        bad.append("stop: no instance left a source that had a layer unplaced")
    clock, real = Ticks(), fanout.time
    fanout.time = clock
    try:
        fanout.DEADLINE[0] = 10 ** 9
        fanout.partition(build(fanout.Layer), nets, fixed)
        total, tried = clock.n, 0
        for k in range(0, total, max(1, total // 40)):
            layers, ref = build(fanout.Layer), {L.name: L for L in build(RefLayer)}
            clock.n, fanout.DEADLINE[0] = 0, k
            try:
                fanout.partition(layers, nets, fixed)
                bad.append(f"stop {k} of {total}: the partition was not stopped")
                continue
            except fanout.Stopped as e:
                S, left, step = e.args
            tried += 1
            flat = [x for s in S.values() for x in s]
            if len(flat) != len(set(flat)) or sorted(set(nets) - set(flat)) != left or step != "partition":
                bad.append(f"stop {k}: a net on two layers, or the unplaced list is not the rest")
            for ln, s in S.items():
                if not reference(ref[ln], s):
                    bad.append(f"stop {k}: {ln}'s {len(s)} sources do not escape together")
            fanout.DEADLINE[0] = None
            if fanout.partition(layers, nets, fixed) != want:
                bad.append(f"stop {k}: the same layers, asked again without a limit, answer differently")
    finally:
        fanout.time, fanout.DEADLINE[0] = real, None
    return tried, total


def main():
    rng = random.Random(20261110)
    bad = []
    asked, w_new, w_ref = walks(rng, bad)
    placed, left, p_new, p_ref = partitions(rng, bad)
    tried, total = stops(rng, bad)
    if not left:
        bad.append("no partition left a source that had a layer unplaced: the exchange step was not exercised")
    for b in bad:
        print("  BAD " + b)
    if bad:
        print(f"FAIL: {len(bad)} disagreement(s); first: {bad[0]}")
        return 1
    print(f"PASS: {asked} sets in 6 walks answered as the flow built from nothing ({w_new:.2f} s against "
          f"{w_ref:.2f} s); 4 partitions over 3 layers ({placed} placed, {left} that had a layer left over) and exact_escape "
          f"identical ({p_new:.2f} s against {p_ref:.2f} s); a partition stopped at {tried} points of its "
          f"{total} questions each left escapable sets and an oracle that still answers right")
    return 0


if __name__ == "__main__":
    sys.exit(main())
