#!/usr/bin/env python3
"""A small multi-layer maze router over a rectangular window of the board, with
negotiated congestion (PathFinder) on top.

Driven by netgroup.py from a spec file; written for a bus of two dozen nets
through a funnel a few mm wide, where a greedy router -- single-layer or
two-layer, any net order -- tops out well short of all of them: whichever net
is routed first takes the straight line and seals the others out.  Negotiation
is the standard cure -- every net is routed IGNORING the others at first,
overlaps are priced, and the price rises until nobody overlaps.

The model, all in one place:

  * a grid (0.1 mm) per routing layer, plus one "via" grid;
  * STATIC obstacles: foreign pads, foreign copper, keep-outs.  Stamped once,
    inflated by what a new 0.2 mm track (or a 0.6 mm via, on the via grid)
    must keep clear;
  * PER-NET geometry, reference-counted, in two kinds:
      - FIXED: copper the net already owns on the board (its escape).  Hard
        for every other net, free for the owner -- that is what lets a route
        start on its own copper;
      - ROUTED: what this router laid.  Hard in `route(..., pres=None)`, PRICED
        in negotiation;
  * A* over (cell, layer, heading) with a turn penalty and a via cost; a layer
    change is allowed only where the via grid says a through via is legal;
  * a route may end on ANY of a set of goal cells -- where a permutation is
    free (any of N equivalent pins), which one a net lands on is the router's
    choice, and two nets wanting the same one is just another overlap to
    negotiate.

Clearances are the conservative ones, set by the spec: CLR between the routed
nets themselves (their own class) and PADCLR to anything foreign, because KiCad
enforces the stricter of two nets' classes.  The module defaults below are only
defaults; netgroup.py overwrites them from the spec.

What it took to make it converge (each line is a failure without it):

  * keep real parents in the search -- a turn penalty makes the cost field
    non-monotonic, and walking down it dead-ends;
  * a fine grid creeps (a ribbon shifts one cell per iteration and never
    finishes); a SLOT grid (step = track + clearance) converges or fails fast;
  * `allowed` layers per net -- free layer changes turn a bus into vias;
  * 45-degree moves -- Manhattan cannot slide a ribbon as tracks peel off;
  * turn by 45 degrees at most (`only45`, `start_dir`, `goal_dirs`): the joint
    where a route leaves its prelaid copper and where it lands are joints too;
  * a 45-degree step is refused past an obstacle in either corner cell, and
    priced past another net's centre-line (`core`);
  * charge the clipper too (`conflicts`) -- flag only the victim and the pair
    locks for ever;
  * strict halos on a slot grid (the neighbour cell is EXACTLY the legal
    distance), with "inside a rectangle" tested separately so a zero-radius
    keep-out still blocks;
  * guides (`bias`) and history that decays (`hist_decay`).
"""

import heapq
import math
import random
from array import array

CLR = 0.15        # between the nets this router lays (Default class)
PADCLR = 0.20     # to foreign pads and copper (the strictest class on the board)
W = 0.2           # track it lays
VIA_R = 0.3       # via it lays (0.6 / 0.3)
VIA_SEP = 0.8     # centre spacing of its vias (spec "via_sep"): keep a plane's web between antipads


class Grid:
    def __init__(self, x0, y0, x1, y1, layers, step=0.1, strict=False):
        self.x0, self.y0, self.step = x0, y0, step
        # strict: a cell EXACTLY at the clearance distance is free.  That is what a
        # SLOT grid needs (step = track + clearance, one track per cell): the
        # neighbouring cell is at precisely the legal distance.
        self.eps = -1e-7 if strict else 1e-9
        self.nx = int(round((x1 - x0) / step)) + 1
        self.ny = int(round((y1 - y0) / step)) + 1
        self.layers = list(layers)
        self.keys = self.layers + ["via"]
        n = self.nx * self.ny
        self.static = {k: bytearray(n) for k in self.keys}
        self.fix = {k: bytearray(n) for k in self.keys}     # other nets' board copper: hard
        self.dyn = {k: bytearray(n) for k in self.keys}     # routed here: hard or priced
        self.core = {k: bytearray(n) for k in self.layers}  # routed CENTRE-LINES (for 45-degree corners)
        self.hist = {k: array("f", bytes(4 * n)) for k in self.keys}
        self.own = {}
        self.via_sep = VIA_SEP                              # centre spacing of the vias it lays
        self.diagonal = False                               # 45-degree moves (see route())
        self.only45 = False                                 # ... and no turn sharper than 45 (the angle rule)
        self.turn45, self.turn135 = 0.15, 1.2
        # fix_exact: a 45-degree step is judged against FIXED copper by its end cells only, and
        # that copper's halo grows by the most a step can cut into a round halo between two free
        # cells (the sagitta of a step-long chord).  Off (the default), a corner cell inside the
        # halo forbids the step -- which forbids a route parallel to a frozen diagonal at the pitch
        # the router itself lays them (an earlier run's bundles, frozen as fixed copper).
        self.fix_exact = False

    # -- geometry -> cells ---------------------------------------------------
    def cell(self, x, y):
        i = min(self.nx - 1, max(0, int(round((x - self.x0) / self.step))))
        j = min(self.ny - 1, max(0, int(round((y - self.y0) / self.step))))
        return j * self.nx + i

    def xy(self, c):
        # 6 decimals: a stock QFN pad sits at y = 122.4375, which 3 would move
        return (round(self.x0 + (c % self.nx) * self.step, 6),
                round(self.y0 + (c // self.nx) * self.step, 6))

    def disc(self, x, y, r):
        ci, cj = (x - self.x0) / self.step, (y - self.y0) / self.step
        n = int(r / self.step) + 1
        r2 = (r / self.step) ** 2 + self.eps / self.step ** 2
        out = set()
        for j in range(max(0, int(cj) - n), min(self.ny - 1, int(cj) + n + 1) + 1):
            dj = j - cj
            for i in range(max(0, int(ci) - n), min(self.nx - 1, int(ci) + n + 1) + 1):
                di = i - ci
                if di * di + dj * dj <= r2:
                    out.add(j * self.nx + i)
        return out

    def seg(self, a, b, r):
        if a[0] == b[0] or a[1] == b[1]:          # axis-aligned: the capsule IS a rounded rect
            return self.rect(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]), r)
        n = max(1, int(math.dist(a, b) / (self.step / 2)))
        out = set()
        for k in range(n + 1):
            t = k / n
            out |= self.disc(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, r)
        return out

    def rect(self, L, T, R, B, r=0.0):
        out = set()
        i0 = max(0, int((L - r - self.x0) / self.step) - 1)
        i1 = min(self.nx - 1, int((R + r - self.x0) / self.step) + 1)
        j0 = max(0, int((T - r - self.y0) / self.step) - 1)
        j1 = min(self.ny - 1, int((B + r - self.y0) / self.step) + 1)
        for j in range(j0, j1 + 1):
            y = self.y0 + j * self.step
            dy = max(T - y, 0.0, y - B)
            for i in range(i0, i1 + 1):
                x = self.x0 + i * self.step
                dx = max(L - x, 0.0, x - R)
                d2 = dx * dx + dy * dy
                # inside the rectangle, or within r of it.  "Inside" has a tolerance: a cell ON
                # the edge computes 1e-14 outside it about half the time (y0 + j * step), and on a
                # strict grid (eps < 0) that dropped the last row of a zero-radius rectangle,
                # which then blocked nothing.
                if d2 <= 1e-12 or d2 <= r * r + self.eps:
                    out.add(j * self.nx + i)
        return out

    # -- obstacles -----------------------------------------------------------
    def block(self, keys, cells):
        for k in keys:
            g = self.static[k]
            for c in cells:
                g[c] = 1

    def unblock(self, keys, cells):
        for k in keys:
            g = self.static[k]
            for c in cells:
                g[c] = 0

    def static_pad(self, L, T, R, B, layers):
        self.block(layers, self.rect(L, T, R, B, PADCLR + W / 2))
        self.block(["via"], self.rect(L, T, R, B, PADCLR + VIA_R))

    def static_track(self, layer, a, b, w, clr=PADCLR):
        if layer in self.layers:
            self.block([layer], self.seg(a, b, w / 2 + clr + W / 2))
        self.block(["via"], self.seg(a, b, w / 2 + clr + VIA_R))

    def static_via(self, x, y, r, clr=PADCLR, sep=0.0):
        self.block(self.layers, self.disc(x, y, r + clr + W / 2))
        self.block(["via"], self.disc(x, y, max(sep, r + clr + VIA_R)))

    def _halo(self, tracks, vias, m=0.0):
        add = {k: set() for k in self.keys}
        for lay, a, b, w in tracks:
            if lay in self.layers:
                add[lay] |= self.seg(a, b, w / 2 + CLR + W / 2 + m)
            add["via"] |= self.seg(a, b, w / 2 + CLR + VIA_R)
        for x, y, r, sep in vias:
            t = self.disc(x, y, r + CLR + W / 2 + m)
            for lay in self.layers:
                add[lay] |= t
            add["via"] |= self.disc(x, y, max(sep, r + CLR + VIA_R))
        return add

    def _own(self, net):
        return self.own.setdefault(net, {"fixed": {k: set() for k in self.keys},
                                         "routed": {k: set() for k in self.keys},
                                         "core": {k: set() for k in self.layers}})

    def register(self, net, kind, tracks=(), vias=()):
        """tracks: (layer, a, b, width); vias: (x, y, radius, centre_sep)."""
        m = 0.0
        if kind == "fixed" and self.fix_exact:
            r_ = CLR + W / 2
            m = r_ - math.sqrt(max(0.0, r_ * r_ - (self.step * 0.70710678) ** 2)) + 1e-3
        add = self._halo(tracks, vias, m)
        own = self._own(net)
        grid = self.fix if kind == "fixed" else self.dyn
        for k in self.keys:
            new = add[k] - own[kind][k]
            g = grid[k]
            for c in new:
                g[c] += 1
            own[kind][k] |= new

    def unroute(self, net):
        own = self.own.get(net)
        if not own:
            return
        for k in self.keys:
            g = self.dyn[k]
            for c in own["routed"][k]:
                g[c] -= 1
            own["routed"][k] = set()
        for k in self.layers:
            g = self.core[k]
            for c in own["core"][k]:
                g[c] -= 1
            own["core"][k] = set()

    # -- routing ---------------------------------------------------------------
    def hmap(self, goals):
        """Manhattan distance to the nearest goal cell, ignoring obstacles: an
        admissible heuristic for a route that may end on any of them."""
        nx, ny = self.nx, self.ny
        INF = 1 << 30
        d = [INF] * (nx * ny)
        for c in goals:
            d[c] = 0
        for j in range(ny):                      # two-pass L1 distance transform
            row = j * nx
            for i in range(nx):
                c = row + i
                v = d[c]
                if i and d[c - 1] + 1 < v:
                    v = d[c - 1] + 1
                if j and d[c - nx] + 1 < v:
                    v = d[c - nx] + 1
                d[c] = v
        for j in range(ny - 1, -1, -1):
            row = j * nx
            for i in range(nx - 1, -1, -1):
                c = row + i
                v = d[c]
                if i < nx - 1 and d[c + 1] + 1 < v:
                    v = d[c + 1] + 1
                if j < ny - 1 and d[c + nx] + 1 < v:
                    v = d[c + nx] + 1
                d[c] = v
        return d

    def route(self, net, src, src_layer, goals, goal_layer, allowed,
              via_cost=2.0, turn=0.4, limit=900000, pres=None, hmap=None,
              layer_cost=None, via_mask=None, bias=None, start_dir=0, goal_dirs=None):
        """goals: a cell, an (x, y) or a set of cells, all on goal_layer.
        pres=None: other nets' routed copper is an obstacle.  pres=p: it costs
        p per net overlapped, plus the cell's history (negotiation).
        start_dir: the heading the net ARRIVES at src with (its connector's last
        leg), so the first step obeys the turn rule too; 0 = free (a pad, a via).
        goal_dirs: headings the route may arrive at a goal with -- the copper
        beyond the goal has a direction, and the joint there is a joint."""
        nx, ny, step = self.nx, self.ny, self.step
        L = self.layers
        if isinstance(goals, tuple):
            goals = {self.cell(*goals)}
        elif isinstance(goals, int):
            goals = {goals}
        if hmap is None:
            hmap = self.hmap(goals)
        own = self._own(net)
        mfix = [own["fixed"][k] for k in L]
        mdyn = [own["routed"][k] for k in L]
        st = [self.static[k] for k in L]
        fx = [self.fix[k] for k in L]
        dy = [self.dyn[k] for k in L]
        hs = [self.hist[k] for k in L]
        cr = [self.core[k] for k in L]
        mcore = [own["core"][k] for k in L]
        vst, vfx, vdy, vhs = self.static["via"], self.fix["via"], self.dyn["via"], self.hist["via"]
        vmfix, vmdyn = own["fixed"]["via"], own["routed"]["via"]
        ok_layer = [k in allowed for k in L]
        lc = [(layer_cost or {}).get(k, 1.0) * step for k in L]
        li0, lig = L.index(src_layer), L.index(goal_layer)
        sc = self.cell(*src)
        soft = pres is not None
        fexact = self.fix_exact
        INF = 1e18

        start = (sc, li0, start_dir)
        dist = {start: 0.0}
        par = {}
        hk = step if not self.diagonal else step * 0.7071     # L1 / sqrt(2) <= octile
        heap = [(hmap[sc] * hk, 0.0, sc, li0, start_dir)]
        goal = None
        n_exp = 0
        # (di, dj, heading); headings 1..8 run clockwise from east so a turn's
        # angle is 45 degrees times their circular difference
        moves = ((1, 0, 1), (0, 1, 3), (-1, 0, 5), (0, -1, 7))
        if self.diagonal:
            moves += ((1, 1, 2), (-1, 1, 4), (-1, -1, 6), (1, -1, 8))
        # The angle rule: a track turns by 45 degrees at a joint, never more.  A right
        # angle is then two steps with a diagonal cell between them -- 0.495 mm on the
        # slot grid, over one track width.  (only45 off: the old costs, for a study.)
        tcost = (0.0, self.turn45, 1e9, 1e9, 1e9) if self.only45 else (0.0, self.turn45, turn, self.turn135, 1e9)
        while heap:
            _f, d, c, li, dr = heapq.heappop(heap)
            if d > dist.get((c, li, dr), INF):
                continue
            if li == lig and c in goals and (not goal_dirs or dr in goal_dirs or dr == 0):
                goal = (c, li, dr)
                break
            n_exp += 1
            if n_exp > limit:
                break
            i, j = c % nx, c // nx
            s_l, f_l, d_l, h_l, mf, md, step_c = st[li], fx[li], dy[li], hs[li], mfix[li], mdyn[li], lc[li]
            k_l, mk = cr[li], mcore[li]
            for di, dj, nd in moves:
                ni, nj = i + di, j + dj
                if not (0 <= ni < nx and 0 <= nj < ny):
                    continue
                nc = nj * nx + ni
                if s_l[nc]:
                    continue
                f = f_l[nc]
                if f and not (f == 1 and nc in mf):
                    continue
                if dr:
                    t = abs(nd - dr)
                    t = tcost[t if t <= 4 else 8 - t]
                    if t >= 1e9:
                        continue
                else:
                    t = 0.0
                cost = d + t + (step_c if not (di and dj) else step_c * 1.41421356)
                o = d_l[nc]
                if o and nc in md:
                    o -= 1
                if di and dj:
                    # a 45-degree step passes 0.707 cells from BOTH corner cells: no
                    # obstacle, no foreign board copper and no foreign centre-line there
                    for cc in (j * nx + ni, nj * nx + i):
                        if s_l[cc]:
                            o = -1
                            break
                        f = f_l[cc]
                        if f and not fexact and not (f == 1 and cc in mf):
                            o = -1
                            break
                        k = k_l[cc]
                        if k and cc in mk:
                            k -= 1
                        o += k
                    if o < 0:
                        continue
                if o:
                    if not soft:
                        continue
                    cost += pres * o
                if soft:
                    cost += h_l[nc]
                if bias is not None:
                    # a per-layer bias is a list with one array per layer (a plane layer
                    # inside its apron is free for one net and dear for its neighbour); a
                    # single array applies on every layer
                    cost += (bias[li][nc] if isinstance(bias, list) else bias[nc])
                key = (nc, li, nd)
                if cost < dist.get(key, INF):
                    dist[key] = cost
                    par[key] = (c, li, dr)
                    heapq.heappush(heap, (cost + hmap[nc] * hk + (0.0 if li == lig else via_cost),
                                          cost, nc, li, nd))
            # a layer change, where a through via is legal (and wanted: via_mask)
            if vst[c] or (via_mask is not None and not via_mask[c]):
                continue
            f = vfx[c]
            if f and not (f == 1 and c in vmfix):
                continue
            vo = vdy[c]
            if vo and c in vmdyn:
                vo -= 1
            if vo and not soft:
                continue
            for nli in range(len(L)):
                if nli == li or not ok_layer[nli]:
                    continue
                if st[nli][c]:
                    continue
                f = fx[nli][c]
                if f and not (f == 1 and c in mfix[nli]):
                    continue
                o = dy[nli][c]
                if o and c in mdyn[nli]:
                    o -= 1
                if o and not soft:
                    continue
                cost = d + via_cost
                if soft:
                    cost += pres * (vo + o) + vhs[c] + hs[nli][c]
                key = (c, nli, 0)
                if cost < dist.get(key, INF):
                    dist[key] = cost
                    par[key] = (c, li, dr)
                    heapq.heappush(heap, (cost + hmap[c] * hk + (0.0 if nli == lig else via_cost),
                                          cost, c, nli, 0))
        if goal is None:
            return None
        states = [goal]
        while states[-1] in par:
            states.append(par[states[-1]])
        states.reverse()
        # -> per-layer cell runs and the vias between them
        runs, vias = [], []
        cur_l, cells = states[0][1], [states[0][0]]
        for c, li, _dr in states[1:]:
            if li != cur_l:
                runs.append((L[cur_l], cells))
                vias.append(c)
                cur_l, cells = li, [c]
            elif c != cells[-1]:
                cells.append(c)
        runs.append((L[cur_l], cells))
        return runs, vias

    def polyline(self, cells):
        pts = [self.xy(cells[0])]
        for k in range(1, len(cells) - 1):
            a, b, c2 = cells[k - 1], cells[k], cells[k + 1]
            if (b - a) != (c2 - b):
                pts.append(self.xy(b))
        if len(cells) > 1:
            pts.append(self.xy(cells[-1]))
        return pts

    def lay(self, net, runs, vias):
        """Register a routed path; returns (tracks, vias) as registered."""
        tr = []
        for layer, cells in runs:
            pts = self.polyline(cells)
            tr += [(layer, pts[k], pts[k + 1], W) for k in range(len(pts) - 1)]
        vv = [(*self.xy(c), VIA_R, self.via_sep) for c in vias]
        self.register(net, "routed", tracks=tr, vias=vv)
        own = self._own(net)
        for layer, cells in runs:
            g, mine = self.core[layer], own["core"][layer]
            for c in cells:
                if c not in mine:
                    g[c] += 1
                    mine.add(c)
        return tr, vv

    def conflicts(self, net, runs, vias):
        """Cells of this net that are in conflict with another ROUTED net: a
        centre-line cell inside the other's halo, a via inside its via halo, or
        -- the asymmetric case -- a 45-degree step whose corner cell carries the
        other's centre-line.  Without that last one only the VICTIM of a clipped
        corner is ever charged, the clipper never feels it, and the pair locks."""
        own = self._own(net)
        nx = self.nx
        bad = []
        for layer, cells in runs:
            g, mine = self.dyn[layer], own["routed"][layer]
            k, mk = self.core[layer], own["core"][layer]
            for c in cells:
                if g[c] - (1 if c in mine else 0) > 0:
                    bad.append((layer, c))
            for a, b in zip(cells, cells[1:]):
                d = b - a
                if d in (nx + 1, nx - 1, -nx + 1, -nx - 1):
                    ai, aj, bi, bj = a % nx, a // nx, b % nx, b // nx
                    for cc in (aj * nx + bi, bj * nx + ai):
                        if k[cc] - (1 if cc in mk else 0) > 0:
                            bad += [(layer, a), (layer, b)]
        g, mine = self.dyn["via"], own["routed"]["via"]
        for c in vias:
            if g[c] - (1 if c in mine else 0) > 0:
                bad.append(("via", c))
        return bad

    def reach(self, net, src, src_layer, allowed=None, via_mask=None):
        """Every (cell, layer) this net could stand on from src, ignoring the turn
        rule and the other ROUTED nets: what separates "contested" from "walled in"
        when negotiation gives up.  Returns the set."""
        import collections
        L = [k for k in self.layers if allowed is None or k in allowed]
        li = {k: i for i, k in enumerate(self.layers)}
        own = self._own(net)
        nx, ny = self.nx, self.ny

        def free(c, key):
            if self.static[key][c]:
                return False
            f = self.fix[key][c]
            return not (f and not (f == 1 and c in own["fixed"][key]))
        start = (self.cell(*src) if isinstance(src, tuple) else src, li[src_layer])
        seen, dq = {start}, collections.deque([start])
        while dq:
            c, l = dq.popleft()
            i, j = c % nx, c // nx
            key = self.layers[l]
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                ni, nj = i + di, j + dj
                if 0 <= ni < nx and 0 <= nj < ny:
                    nc = nj * nx + ni
                    if di and dj and self.diagonal and not (free(j * nx + ni, key) and free(nj * nx + i, key)):
                        continue                     # the corner rule route() applies to a 45-degree step
                    if free(nc, key) and (nc, l) not in seen:
                        seen.add((nc, l)); dq.append((nc, l))
            if free(c, "via") and (via_mask is None or via_mask[c]):
                for k in L:
                    l2 = li[k]
                    if l2 != l and free(c, k) and (c, l2) not in seen:
                        seen.add((c, l2)); dq.append((c, l2))
        return seen

    def negotiate(self, jobs, iters=60, pres0=0.5, pres_mul=1.5, hist_inc=0.3,
                  log=print, seed=1, on_iter=None, pres_max=1e30, hist_decay=1.0, **kw):
        """jobs: [dict(net, src, src_layer, goals, goal_layer, allowed)].
        Returns {net: (runs, vias)} with no overlaps, or None."""
        allgoals = set()
        for jb in jobs:
            allgoals |= set(jb["goals"])
        hm = self.hmap(allgoals)
        paths = {}
        pres = pres0
        rnd = random.Random(seed)
        jobs = list(jobs)
        for it in range(1, iters + 1):
            if it > 1:
                rnd.shuffle(jobs)                 # a fixed order lets two nets swap places for ever
            for jb in jobs:
                n = jb["net"]
                self.unroute(n)
                res = self.route(n, jb["src"], jb["src_layer"], jb["goals"], jb["goal_layer"],
                                 jb["allowed"], pres=pres, hmap=hm, via_mask=jb.get("via_mask"),
                                 bias=jb.get("bias"), start_dir=jb.get("start_dir", 0),
                                 goal_dirs=jb.get("goal_dirs"), **kw)
                if res is None:
                    log(f"   iteration {it}: {n} has no path even through the others -- walled in")
                    return None
                paths[n] = res
                self.lay(n, *res)
            bad = {n: self.conflicts(n, *paths[n]) for n in paths}
            nbad = sum(1 for v in bad.values() if v)
            ncell = sum(len(v) for v in bad.values())
            log(f"   iteration {it}: pres {pres:.2f}, {nbad} nets overlap on {ncell} cells")
            self.last = (dict(paths), bad)
            if on_iter:
                on_iter(it, paths, bad)
            if not ncell:
                return paths
            if hist_decay < 1.0:                  # stale history outlives the conflict that made it and,
                for k in self.keys:               # with guided nets, bends tracks round cells nobody wants
                    h = self.hist[k]
                    for c in range(len(h)):
                        if h[c]:
                            h[c] *= hist_decay
            for v in bad.values():
                for k, c in v:
                    self.hist[k][c] += hist_inc
            pres = min(pres * pres_mul, pres_max)   # past this, history has to do the work
        return None
