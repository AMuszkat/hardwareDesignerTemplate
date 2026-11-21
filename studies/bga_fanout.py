"""
bga_fanout -- does a through-via fan-out close on a BGA's own ball map?

A count on the ball lattice for any BGA with depopulated sites, the fan-out
NXP's and most vendors' notes draw: no via in a land; the outer rings leave
on the top layer; every other ball takes a via in an EMPTY lattice site next
to it (orthogonal or diagonal, a short top-layer stub); one via a site; a
signal ball has a via of its own; up to `share` balls of ONE power or ground
net, each next to the site, may use one via.

    site_vias(balls, cols, rows, share, ...) -> (closes, vias, balls that need one)

Every project number is passed in (the ball map, the lattice, how many rings
leave on the top, how many balls a rail via may carry); the study that calls
it holds them (designpoint).  It is an integer program, solved by CP-SAT
through kicad_project/tools/cpsat_milp.py: seconds on a 27 x 27 lattice.

It proves the SITES exist, not that every track leaves the field: the board's
escape bound does that.  Run it before a land or a via rule is published --
sweep `share` to find the smallest sharing that closes, and run the
pessimistic case (signal_top_rings one less) to see how much the top layer's
escape carries.  First used: s18 (a 486-ball, 0.5 mm pitch SoC, 2026-11-21),
after two weeks of allowances on an in-pad fan-out that this count would have
ruled out in two seconds.
"""
import os
import sys

import numpy as np
from scipy.optimize import Bounds, LinearConstraint
from scipy.sparse import lil_matrix

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kicad_project", "tools"))
from cpsat_milp import milp  # noqa: E402

NB8 = [(dc, dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1) if (dc, dr) != (0, 0)]


def ring(c, r, cols, rows):
    """1 for the outermost ring of a cols x rows lattice, counting inwards."""
    return min(c, r, cols - 1 - c, rows - 1 - r) + 1


def site_vias(balls, cols, rows, share, signal_top_rings=2, rail_free_rings=1, rail_outside_rings=2,
              most=False, time_limit=300):
    """balls: {(col, row): (name, net, kind)}, kind 's' signal, 'g' / 'p' ground / power (may share), 'u' unconnected.
    signal_top_rings   signal balls in rings <= this leave on the top layer and need no via
    rail_free_rings    power / ground balls in rings <= this take a via outside the field
    rail_outside_rings power / ground balls in rings <= this MAY take one outside instead of a site
    most               maximise the vias (the fewest balls without one of their own); else any answer
    -> (closes, vias used, balls that need a via)."""
    empty = {(c, r) for c in range(cols) for r in range(rows) if (c, r) not in balls}
    need = []
    for (c, r), (b, net, kind) in sorted(balls.items()):
        rg = ring(c, r, cols, rows)
        if kind == "u" or (kind == "s" and rg <= signal_top_rings) or (kind in "gp" and rg <= rail_free_rings):
            continue
        opts = [(c + dc, r + dr) for dc, dr in NB8 if (c + dc, r + dr) in empty]
        need.append((b, net, kind, opts, kind in "gp" and rg <= rail_outside_rings))
    xs, ys = [], {}                      # x: (ball index, site or None = outside); y: (site, net)
    for i, (b, net, kind, opts, outside) in enumerate(need):
        for s in opts:
            xs.append((i, s))
            ys.setdefault((s, net), len(ys))
        if outside:
            xs.append((i, None))
    nx, ny = len(xs), len(ys)
    rows_ = []                           # (coefficients, lo, hi)
    for i in range(len(need)):           # every ball gets exactly one via
        rows_.append(({j: 1 for j, (bi, s) in enumerate(xs) if bi == i}, 1, 1))
    for j, (bi, s) in enumerate(xs):     # a ball's via exists as its net's
        if s is not None:
            rows_.append(({j: 1, nx + ys[(s, need[bi][1])]: -1}, -1, 0))
    for s in empty:                      # one via a site
        col = {nx + k: 1 for (s2, net), k in ys.items() if s2 == s}
        if col:
            rows_.append((col, 0, 1))
    for (s, net), k in ys.items():       # balls a via: 1 a signal, <= share a rail; no via without a ball
        col = {j: 1 for j, (bi, s2) in enumerate(xs) if s2 == s and need[bi][1] == net}
        cap = share if need[next(bi for bi, s2 in xs if s2 == s and need[bi][1] == net)][2] in "gp" else 1
        col[nx + k] = -cap
        rows_.append((col, -cap, 0))
        col2 = {j: -1 for j in col if j < nx}
        col2[nx + k] = 1
        rows_.append((col2, -len(col2), 0))
    a = lil_matrix((len(rows_), nx + ny))
    lo, hi = [], []
    for i, (col, l, h) in enumerate(rows_):
        for j, v in col.items():
            a[i, j] = v
        lo.append(l)
        hi.append(h)
    c = np.zeros(nx + ny)
    if most:
        c[nx:] = -1
    res = milp(c, constraints=LinearConstraint(a.tocsr(), lo, hi), integrality=np.ones(nx + ny),
               bounds=Bounds(0, 1), options={"time_limit": time_limit, "mip_rel_gap": 0})
    if res.x is None:
        return False, 0, len(need)
    return True, int(round(sum(res.x[nx:]))), len(need)
