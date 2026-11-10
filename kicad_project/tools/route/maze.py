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
import sys
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import par  # noqa: E402  (tools/par.py: fork, results in job order)


# -- the compiled core (tools/route/_maze.c, built by tools/setup.sh into tools/.native/) ------
# The same search, bit for bit (the agentic test compares both); HW_MAZE_CORE=python forces the
# Python one.  CORE says which is active: every router run prints it.
_CORE_ABI = "maze-core 2"     # _maze.c's maze_core_version(); bump both when the arguments change


def _load_core():
    import ctypes
    import os
    if os.environ.get("HW_MAZE_CORE", "").lower() == "python":
        return None, "python (HW_MAZE_CORE=python)"
    nat = Path(__file__).resolve().parent.parent / ".native"
    for name in ("libmaze.so", "libmaze.dylib"):
        f = nat / name
        if f.exists():
            try:
                lib = ctypes.CDLL(str(f))
                lib.maze_astar.restype = ctypes.c_long
                lib.maze_core_version.restype = ctypes.c_char_p
                ver = lib.maze_core_version().decode()
                if ver != _CORE_ABI:          # an old build takes other arguments: never call it
                    return None, f"python ({f.name} is {ver}, this maze.py needs {_CORE_ABI}: re-run tools/setup.sh)"
                return lib, f"compiled ({ver}, {f.name})"
            except OSError as e:
                return None, f"python (could not load {f.name}: {e})"
    return None, "python (no compiled core: kicad_project/tools/setup.sh builds it with a C compiler)"


def _core_note(lib):
    """The ONE line a run owes its reader on stderr when the search is not the compiled one it
    could be (ROADMAP R9), or None.  Two cases:

      * the Python search is running and nobody asked for it (HW_MAZE_CORE=python is asking: the
        harness does, to compare the two cores) -- no library, one built for another ABI, one
        that does not load.  The same routes, about ten times slower: a 20 s group takes
        minutes, and a run that says so only in its first stdout line was read past for weeks;
      * the compiled core is running but route/_maze.c is NEWER than the library (a pull that
        changed the search, no rebuild): the two cores may no longer agree, which is the one
        thing the harness's both-core comparison exists to rule out.  By modification time: a
        false line after a checkout that rewrote _maze.c costs one rebuild, a missed one a
        silently different route.

    CORE (stdout, every router run) keeps the reason; this line is the instruction."""
    import os
    if os.environ.get("HW_MAZE_CORE", "").lower() == "python":
        return None
    if lib is None:
        return "router core: python (about 10x slower) - run kicad_project/tools/setup.sh"
    here = Path(__file__).resolve().parent
    src = here / "_maze.c"
    for name in ("libmaze.so", "libmaze.dylib"):
        f = here.parent / ".native" / name
        if f.exists():
            try:
                if src.exists() and src.stat().st_mtime > f.stat().st_mtime:
                    return (f"router core: compiled, but route/_maze.c is newer than .native/{name} "
                            "(a stale build) - run kicad_project/tools/setup.sh")
            except OSError:
                pass
            break
    return None


_CORE, CORE = _load_core()
CORE_NOTE = _core_note(_CORE)
if CORE_NOTE:
    print(CORE_NOTE, file=sys.stderr)

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
        self.snon = {k: bytearray(n) for k in self.keys}    # static cells NOT from a round obstacle
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
        # static_exact: the same for STATIC round obstacles (vias, round pads) laid with
        # block_disc(): their halo grows by the most a 45-degree step's chord can cut into it,
        # and a 45-degree step is then judged by its end cells only.  Off (the default), a corner
        # cell inside a via's halo forbids the step -- which shuts the diagonal lane between two
        # diagonal vias of a 0.5 mm BGA field (0.354 mm from each, against a 0.333 halo).
        self.static_exact = False
        # xdir[layer] = {cell: {headings}}: copper on another layer this layer may cross only
        # square (netgroup's "cross_square") -- a step into or out of such a cell must run on
        # one of its headings, so a track crosses that copper at 90 degrees or not at all
        self.xdir = {}
        # xdir_net[layer][net] = {cell: {headings}}: the same, for one net only (an entry with "nets")
        self.xdir_net = {}
        # clr_at(x, y, kind): the clearance between ROUTED nets at a point ("track" / "via"),
        # for a group whose fields have their own area rule (netgroup sets it); None: CLR
        self.clr_at = None
        # why[key][cell] = index into labels: WHAT a static cell is (see mark()).  None until the
        # first mark(): a grid nobody labels pays nothing for it.
        self.why = None
        self.labels = []
        self._tag = None
        self.stuck = None        # negotiate()'s report when it stopped on a held conflict
        self.walled = None

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
    def mark(self, label):
        """Name what the static obstacles stamped from here on ARE ("via of GND at (23.4, 17.4)
        [spec reserve_vias]"), until the next mark(); mark(None) stops labelling.  The obstacle
        map itself is one bit per cell: when a net turns out to be closed in, "blocked" is all
        it can say, and the session is left to find the via beside the start point by hand
        (board1's R2 on B.Cu).  blocked_end() reads these labels back for the cells of the wall.
        The last label stamped on a cell wins; search and results do not depend on any of it."""
        if self.why is None:
            self.why = {k: {} for k in self.keys}
        if label is None:
            self._tag = None
            return
        self.labels.append(label)
        self._tag = len(self.labels) - 1

    def block(self, keys, cells):
        for k in keys:
            g, h = self.static[k], self.snon[k]
            for c in cells:
                g[c] = 1
                h[c] = 1
        if self._tag is not None:
            for k in keys:
                self.why[k].update(dict.fromkeys(cells, self._tag))

    def block_disc(self, keys, x, y, r):
        """A round obstacle of halo radius r (a via, a round pad).  With static_exact its cells
        are marked as round-only, its halo grows by g = sqrt(r^2 + (step/sqrt2)^2) - r -- the
        deepest a 45-degree step (chord step*sqrt2) between two cells outside r + g can reach --
        and route() judges a 45-degree step past it by the step's end cells alone."""
        if not self.static_exact:
            self.block(keys, self.disc(x, y, r))
            return
        cells = self.disc(x, y, r + self._grow(r))
        for k in keys:
            st = self.static[k]
            for c in cells:
                st[c] = 1
        if self._tag is not None:
            for k in keys:
                self.why[k].update(dict.fromkeys(cells, self._tag))

    def unblock(self, keys, cells):
        for k in keys:
            g, h = self.static[k], self.snon[k]
            for c in cells:
                g[c] = 0
                h[c] = 0
        if self.why is not None:
            for k in keys:
                w = self.why[k]
                for c in cells:
                    w.pop(c, None)

    def static_pad(self, L, T, R, B, layers):
        self.block(layers, self.rect(L, T, R, B, PADCLR + W / 2))
        self.block(["via"], self.rect(L, T, R, B, PADCLR + VIA_R))

    def static_track(self, layer, a, b, w, clr=PADCLR):
        if layer in self.layers:
            self.block([layer], self.seg(a, b, w / 2 + clr + W / 2))
        self.block(["via"], self.seg(a, b, w / 2 + clr + VIA_R))

    def static_via(self, x, y, r, clr=PADCLR, sep=0.0):
        self.block_disc(self.layers, x, y, r + clr + W / 2)
        self.block(["via"], self.disc(x, y, max(sep, r + clr + VIA_R)))

    def _grow(self, R):
        """How far a halo of radius R must grow so that a 45-degree step (chord step*sqrt2)
        between two cells outside it stays outside R: sqrt(R^2 + step^2/2) - R.  Computed on the
        obstacle's WHOLE halo -- on its clearance part alone (the old formula) it is three times
        too big at a 0.0889 mm BGA rule and shuts the 0.354 mm lanes between diagonal vias."""
        return math.sqrt(R * R + self.step * self.step / 2) - R + 1e-4

    def _seg_local(self, a, b, rfun):
        """A segment's halo whose radius varies along it: rfun(x, y) at each sample."""
        n = max(1, int(math.dist(a, b) / (self.step / 2)))
        out = set()
        for k in range(n + 1):
            t = k / n
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            out |= self.disc(x, y, rfun(x, y))
        return out

    def _halo(self, tracks, vias, m=0.0, local=False):
        """m: True grows each item by _grow() of its own halo (fix_exact); a number, by that.
        local: the ROUTED nets' clearance between each other comes from clr_at(x, y, kind)
        where the grid has one (a BGA field's area rule inside its courtyard), else CLR."""
        add = {k: set() for k in self.keys}
        ca = self.clr_at if (local and self.clr_at) else None
        # a TRACK's halo takes the field's rule inside a field for FIXED copper too (a prelaid
        # escape stub beside another net's, board1's top-row stubs 0.5 apart: at the group's
        # 0.356 they shut each other's lane); fixed vias are pre-shrunk by netgroup instead
        cat = self.clr_at if self.clr_at else None
        for lay, a, b, w in tracks:
            if lay in self.layers:
                if cat:
                    def rf(x, y, w=w):
                        R_ = w / 2 + cat(x, y, "track") + W / 2
                        return R_ + (self._grow(R_) if m is True else m)
                    add[lay] |= self._seg_local(a, b, rf)
                else:
                    R = w / 2 + CLR + W / 2
                    add[lay] |= self.seg(a, b, R + (self._grow(R) if m is True else m))
            add["via"] |= self.seg(a, b, w / 2 + CLR + VIA_R)
        for x, y, r, sep in vias:
            R = r + (ca(x, y, "via") if ca else CLR) + W / 2
            t = self.disc(x, y, R + (self._grow(R) if m is True else m))
            for lay in self.layers:
                add[lay] |= t
            add["via"] |= self.disc(x, y, max(sep, r + CLR + VIA_R))
        return add

    def _own(self, net):
        return self.own.setdefault(net, {"fixed": {k: set() for k in self.keys},
                                         "routed": {k: set() for k in self.keys},
                                         "core": {k: set() for k in self.layers}})

    def _own_ro(self, net):
        """_own() for a reader: a net nothing was registered for gets empty sets and the grid is
        left as it was (the diagnoses run before and between searches and must not move them)."""
        o = self.own.get(net)
        if o is None:
            e = frozenset()
            o = {"fixed": {k: e for k in self.keys}, "routed": {k: e for k in self.keys},
                 "core": {k: e for k in self.layers}}
        return o

    def register(self, net, kind, tracks=(), vias=()):
        """tracks: (layer, a, b, width); vias: (x, y, radius, centre_sep)."""
        m = True if (kind == "fixed" and self.fix_exact) else 0.0
        add = self._halo(tracks, vias, m, local=(kind == "routed"))
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
              layer_cost=None, via_mask=None, bias=None, start_dir=0, goal_dirs=None, box=None):
        """goals: a cell, an (x, y) or a set of cells, all on goal_layer (one layer, or a list: any of them).
        pres=None: other nets' routed copper is an obstacle.  pres=p: it costs
        p per net overlapped, plus the cell's history (negotiation).
        start_dir: the heading the net ARRIVES at src with (its connector's last
        leg), so the first step obeys the turn rule too; 0 = free (a pad, a via).
        goal_dirs: headings the route may arrive at a goal with -- the copper
        beyond the goal has a direction, and the joint there is a joint.
        box: (i0, j0, i1, j1) cell bounds the search may not leave (a corridor)."""
        nx, ny, step = self.nx, self.ny, self.step
        bi0, bj0, bi1, bj1 = (max(0, box[0]), max(0, box[1]), min(nx - 1, box[2]), min(ny - 1, box[3])) \
            if box else (0, 0, nx - 1, ny - 1)
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
        sn = [self.snon[k] for k in L]
        sexact = self.static_exact
        fx = [self.fix[k] for k in L]
        dy = [self.dyn[k] for k in L]
        hs = [self.hist[k] for k in L]
        cr = [self.core[k] for k in L]
        mcore = [own["core"][k] for k in L]
        vst, vfx, vdy, vhs = self.static["via"], self.fix["via"], self.dyn["via"], self.hist["via"]
        vmfix, vmdyn = own["fixed"]["via"], own["routed"]["via"]
        ok_layer = [k in allowed for k in L]
        xd = [self.xdir_net.get(k, {}).get(net, self.xdir.get(k)) if self.xdir_net.get(k, {}).get(net) is not None
              else self.xdir.get(k) for k in L]
        lc = [(layer_cost or {}).get(k, 1.0) * step for k in L]
        # a through-hole source pad starts on every layer it has: src_layer may be a list
        li0s = [L.index(k) for k in ([src_layer] if isinstance(src_layer, str) else src_layer)]
        # a through-hole goal (a via, a THT pin) is reached on any layer it has: goal_layer may be a list
        ligs = {L.index(k) for k in ([goal_layer] if isinstance(goal_layer, str) else goal_layer)}
        ligm = sum(1 << k for k in ligs)
        sc = self.cell(*src)
        soft = pres is not None
        fexact = self.fix_exact
        INF = 1e18

        dist = {(sc, l0, start_dir): 0.0 for l0 in li0s}
        par = {}
        hk = step if not self.diagonal else step * 0.7071     # L1 / sqrt(2) <= octile
        if _CORE is not None and not any(xd) and (via_mask is None or isinstance(via_mask, (bytearray, bytes))):
            got = self._route_native(L, st, sn, fx, dy, hs, cr, mfix, mdyn, mcore, vst, vfx, vdy, vhs,
                                     vmfix, vmdyn, via_mask, ok_layer, lc, sc, li0s, start_dir, ligm, goals,
                                     goal_dirs, hmap, hk, soft, pres, via_cost, bias, limit, box,
                                     (0.0, self.turn45, 1e9, 1e9, 1e9) if self.only45 else
                                     (0.0, self.turn45, turn, self.turn135, 1e9))
            if got is not NotImplemented:
                return got
        heap = [(hmap[sc] * hk, 0.0, sc, l0, start_dir) for l0 in li0s]
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
            if li in ligs and c in goals and (not goal_dirs or dr in goal_dirs or dr == 0):
                goal = (c, li, dr)
                break
            n_exp += 1
            if n_exp > limit:
                break
            i, j = c % nx, c // nx
            s_l, f_l, d_l, h_l, mf, md, step_c = st[li], fx[li], dy[li], hs[li], mfix[li], mdyn[li], lc[li]
            sn_l = sn[li]
            k_l, mk = cr[li], mcore[li]
            xd_l = xd[li]
            xc = xd_l.get(c) if xd_l else None
            for di, dj, nd in moves:
                ni, nj = i + di, j + dj
                if not (bi0 <= ni <= bi1 and bj0 <= nj <= bj1):
                    continue
                nc = nj * nx + ni
                if s_l[nc]:
                    continue
                if xd_l:
                    xn = xd_l.get(nc)
                    if (xn is not None and nd not in xn) or (xc is not None and nd not in xc):
                        continue
                    if di and dj and any(nd not in xd_l.get(cc, (nd,)) for cc in (j * nx + ni, nj * nx + i)):
                        continue                  # a 45-degree step grazes its corner cells too
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
                        if s_l[cc] and (not sexact or sn_l[cc]):
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
                    heapq.heappush(heap, (cost + hmap[nc] * hk + (0.0 if li in ligs else via_cost),
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
                    heapq.heappush(heap, (cost + hmap[c] * hk + (0.0 if nli in ligs else via_cost),
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

    def _route_native(self, L, st, sn, fx, dy, hs, cr, mfix, mdyn, mcore, vst, vfx, vdy, vhs, vmfix, vmdyn,
                      via_mask, ok_layer, lc, sc, li0s, start_dir, ligm, goals, goal_dirs, hmap, hk, soft,
                      pres, via_cost, bias, limit, box, tcost):
        """maze_astar in _maze.c; NotImplemented when an input is of a kind it does not take."""
        import ctypes as C
        nx, ny, nl = self.nx, self.ny, len(L)
        n = nx * ny
        U8, F32 = C.POINTER(C.c_uint8), C.POINTER(C.c_float)
        if bias is not None:
            arrs = bias if isinstance(bias, list) else [bias] * nl
            if not all(isinstance(a, array) and a.typecode == "f" for a in arrs):
                return NotImplemented
        keep = []

        def u8(buf):
            if buf is None:
                return None
            if isinstance(buf, bytes):
                buf = bytearray(buf)
            keep.append(buf)
            return C.cast((C.c_uint8 * len(buf)).from_buffer(buf), U8)

        def f32(buf):
            keep.append(buf)
            return C.cast((C.c_float * len(buf)).from_buffer(buf), F32)

        def mask(cells):
            m = bytearray(n)
            for c in cells:
                m[c] = 1
            return m

        def u8s(bufs):
            return (U8 * nl)(*[u8(b) for b in bufs])

        hc = getattr(self, "_hmap_cache", None)
        if hc is None or hc[0] is not hmap:
            hc = (hmap, array("i", hmap))
            self._hmap_cache = hc
        gc = getattr(self, "_goal_cache", None)
        if gc is None or gc[0] is not goals:
            gc = (goals, mask(goals))
            self._goal_cache = gc
        gd = 0
        for g_ in (goal_dirs or ()):
            gd |= (1 << g_) if 1 <= g_ <= 8 else (1 << 15)   # [99]: a via only -- no heading matches
        bx = (C.c_int32 * 4)(*(box if box else (0, 0, nx - 1, ny - 1)))
        srcs = (C.c_int32 * len(li0s))(*([sc] * len(li0s)))
        sls = (C.c_int8 * len(li0s))(*li0s)
        cap = 2 * n + 64
        oc, ol, od = (C.c_int32 * cap)(), (C.c_int8 * cap)(), (C.c_int8 * cap)()
        hm = hc[1]
        r = _CORE.maze_astar(
            C.c_int(nx), C.c_int(ny), C.c_int(nl), bx,
            u8s(st), u8s(sn), u8s(fx), u8s(dy), (F32 * nl)(*[f32(h) for h in hs]), u8s(cr),
            u8s([mask(m) for m in mfix]), u8s([mask(m) for m in mdyn]), u8s([mask(m) for m in mcore]),
            u8(vst), u8(vfx), u8(vdy), f32(vhs), u8(mask(vmfix)), u8(mask(vmdyn)), u8(via_mask),
            (C.c_uint8 * nl)(*[1 if k else 0 for k in ok_layer]), (C.c_double * nl)(*lc),
            (C.c_double * 5)(*tcost),
            C.c_int(1 if self.diagonal else 0), C.c_int(1 if soft else 0),
            C.c_int(1 if self.static_exact else 0), C.c_int(1 if self.fix_exact else 0),
            C.c_double(pres if soft else 0.0), C.c_double(via_cost), C.c_double(hk),
            C.cast((C.c_int32 * n).from_buffer(hm), C.POINTER(C.c_int32)), u8(gc[1]), C.c_int(ligm), C.c_int(gd),
            srcs, sls, C.c_int(len(li0s)), C.c_int(start_dir),
            (F32 * nl)(*[f32(a) for a in (bias if isinstance(bias, list) else [bias] * nl)]) if bias is not None else None,
            C.c_long(int(limit)), oc, ol, od, C.c_long(cap))
        if r == -2 or r == -1:
            return NotImplemented                     # the Python search: path too long, or no memory
        if r == 0:
            return None
        states = [(oc[k], ol[k], od[k]) for k in range(r)]
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

        def corner(c, key):
            # route()'s corner rule: a round static obstacle (static_exact) and fixed copper
            # (fix_exact) are judged by the step's end cells only
            if self.static[key][c] and (not self.static_exact or self.snon[key][c]):
                return False
            f = self.fix[key][c]
            return not (f and not self.fix_exact and not (f == 1 and c in own["fixed"][key]))
        c0 = self.cell(*src) if isinstance(src, tuple) else src
        starts = [(c0, li[k]) for k in ([src_layer] if isinstance(src_layer, str) else src_layer)]
        seen, dq = set(starts), collections.deque(starts)
        while dq:
            c, l = dq.popleft()
            i, j = c % nx, c // nx
            key = self.layers[l]
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                ni, nj = i + di, j + dj
                if 0 <= ni < nx and 0 <= nj < ny:
                    nc = nj * nx + ni
                    if di and dj and self.diagonal and not (corner(j * nx + ni, key) and corner(nj * nx + i, key)):
                        continue                     # the corner rule route() applies to a 45-degree step
                    if free(nc, key) and (nc, l) not in seen:
                        seen.add((nc, l)); dq.append((nc, l))
            if free(c, "via") and (via_mask is None or via_mask[c]):
                for k in L:
                    l2 = li[k]
                    if l2 != l and free(c, k) and (c, l2) not in seen:
                        seen.add((c, l2)); dq.append((c, l2))
        return seen

    # -- why a net has no route: floods, never searches ------------------------------------
    def flood(self, net, starts, layers, via_mask=None, hard=False, soft_nets=(), stop=None,
              stop_any=False, cap=0):
        """Every (cell, layer index) `net` could stand on from `starts` [(cell, layer name)],
        changing layer only onto `layers`.  The turn rule, the 45-degree corner rule, cross_square
        masks, corridors and the A* expansion limit are all IGNORED, so the set is a superset of
        what route() can reach: "the flood does not get there" is a proof that no route does,
        whatever the prices -- which is the only kind of statement a run may stop on.

        hard=False: static obstacles and the other nets' FIXED copper block (what negotiation
        can never move).  hard=True: the other nets' ROUTED copper blocks too, as it lies now
        (route(pres=None)'s rule), except the nets in soft_nets.
        stop: a set of (cell, layer name); the flood returns at the first one it reaches.  With
        stop_any a stop state counts even when its cell is not free (a source cell is where the
        net stands, free or not; a goal cell must be free to be stepped on).
        cap: give up after this many states (0: none).

        Returns (seen, hit, closed): hit is the stop state reached as (cell, layer name) or None;
        closed is False when the cap cut the flood short (then `seen` is only a lower bound)."""
        import collections
        L = self.layers
        li = {k: i for i, k in enumerate(L)}
        own = self._own_ro(net)
        okl = [k in layers for k in L]
        st, fx, dy = [self.static[k] for k in L], [self.fix[k] for k in L], [self.dyn[k] for k in L]
        mf, md = [own["fixed"][k] for k in L], [own["routed"][k] for k in L]
        vst, vfx, vdy = self.static["via"], self.fix["via"], self.dyn["via"]
        vmf, vmd = own["fixed"]["via"], own["routed"]["via"]
        soft = [self.own[n]["routed"] for n in soft_nets if n in self.own and n != net]
        nx, ny = self.nx, self.ny
        stops = {(c, li[k]) for c, k in stop} if stop else set()

        def free(c, l):
            if st[l][c]:
                return False
            f = fx[l][c]
            if f and not (f == 1 and c in mf[l]):
                return False
            if hard:
                o = dy[l][c]
                if o and c in md[l]:
                    o -= 1
                if o and soft:
                    o -= sum(1 for r in soft if c in r[L[l]])
                if o > 0:
                    return False
            return True

        def via_free(c):
            if vst[c] or (via_mask is not None and not via_mask[c]):
                return False
            f = vfx[c]
            if f and not (f == 1 and c in vmf):
                return False
            if hard:
                o = vdy[c]
                if o and c in vmd:
                    o -= 1
                if o and soft:
                    o -= sum(1 for r in soft if c in r["via"])
                if o > 0:
                    return False
            return True

        moves = ((1, 0), (-1, 0), (0, 1), (0, -1))
        if self.diagonal:
            moves += ((1, 1), (1, -1), (-1, 1), (-1, -1))
        first = [(c, li[k]) for c, k in starts]
        seen, dq = set(first), collections.deque(first)
        for s_ in first:
            if s_ in stops:
                return seen, (s_[0], L[s_[1]]), True
        while dq:
            if cap and len(seen) > cap:
                return seen, None, False
            c, l = dq.popleft()
            i, j = c % nx, c // nx
            for di, dj in moves:
                ni, nj = i + di, j + dj
                if 0 <= ni < nx and 0 <= nj < ny:
                    nc = nj * nx + ni
                    s_ = (nc, l)
                    if s_ in seen:
                        continue
                    if stop_any and s_ in stops:
                        seen.add(s_)
                        return seen, (nc, L[l]), True
                    if free(nc, l):
                        seen.add(s_)
                        if s_ in stops:
                            return seen, (nc, L[l]), True
                        dq.append(s_)
            if via_free(c):
                for l2 in range(len(L)):
                    s_ = (c, l2)
                    if l2 == l or not okl[l2] or s_ in seen:
                        continue
                    if free(c, l2):
                        seen.add(s_)
                        if s_ in stops:
                            return seen, (c, L[l2]), True
                        dq.append(s_)
        return seen, None, True

    @staticmethod
    def _aslist(layer):
        return [layer] if isinstance(layer, str) else list(layer)

    def _job_ends(self, jb):
        """A job's source states and goal states as [(cell, layer name)], and its goal cells."""
        goals = jb["goals"]
        if isinstance(goals, tuple):
            goals = {self.cell(*goals)}
        elif isinstance(goals, int):
            goals = {goals}
        src = jb["src"]
        sc = self.cell(*src) if isinstance(src, tuple) else src
        ss = [(sc, k) for k in self._aslist(jb["src_layer"])]
        gs = [(c, k) for c in goals for k in self._aslist(jb["goal_layer"])]
        return ss, gs, goals

    def way_round(self, jb, soft_nets=()):
        """True when jb's net could be laid right now with every other net's copper where it
        lies (the nets in soft_nets excepted): a flood, so True is only "not proven shut"."""
        ss, gs, _g = self._job_ends(jb)
        _seen, hit, _closed = self.flood(jb["net"], ss, jb["allowed"], via_mask=jb.get("via_mask"),
                                         hard=True, soft_nets=soft_nets, stop=set(gs))
        return hit is not None

    def blocked_end(self, jb, cap=0):
        """Is one END of this job shut in by what negotiation cannot move -- static obstacles,
        other nets' fixed copper, the window's edge?  None when it is not (or when `cap` states
        were not enough to tell); else a report naming the end and the wall.

        This is the case a bare "no solution" hides best (board1's R2 on B.Cu: one net reached
        419 cells round its source and none of its goal, because of a via reserved beside its
        start; negotiation said "walled in" in under a second and the session found the via by
        hand).  It is not congestion: no price, no seed and no number of iterations opens it.

        Two floods (flood(): no turn rule, the other ROUTED nets ignored), each stopped at `cap`
        states: from the source towards the goal, and from the goal's free cells back.  A flood
        that ENDS without meeting the other end has found a closed region; the smaller closed
        region is the blocked end.  cap=0 floods without limit (the run has already failed:
        the answer is worth a second); a small cap (a few thousand states) is the cheap test
        before negotiating -- a region that small and closed is a start point, not a room.

        The report: dict(net, end "source"|"goal", at (x, y), layers, cells, by_layer,
        goals, other (states the other end's flood holds), other_closed, edge [sides of the
        window the region touches], wall [nearest first: dict(kind "obstacle"|"net"|"unnamed",
        label, net, layer, cells, dist, at)], via [the same, for what forbids a layer change
        inside the region], no_via (cells of the region with no legal via)).  `label` is what
        mark() said the obstacle was."""
        net = jb["net"]
        ss, gs, goals = self._job_ends(jb)
        lays = set(jb["allowed"]) | {k for _c, k in ss} | {k for _c, k in gs}
        vm = jb.get("via_mask")
        fwd, hit, fclosed = self.flood(net, ss, lays, via_mask=vm, stop=set(gs), cap=cap)
        if hit is not None:
            return None
        li = {k: i for i, k in enumerate(self.layers)}
        own = self._own_ro(net)

        def free(c, k):
            if self.static[k][c]:
                return False
            f = self.fix[k][c]
            return not (f and not (f == 1 and c in own["fixed"][k]))
        gfree = [(c, k) for c, k in gs if free(c, k)]
        bwd, bhit, bclosed = (self.flood(net, gfree, lays, via_mask=vm, stop=set(ss), stop_any=True, cap=cap)
                              if gfree else (set(), None, True))
        if bhit is not None:
            bclosed = False                   # the two floods disagree (a via rule): the source side decides
        if fclosed and (not bclosed or (gfree and len(fwd) <= len(bwd))):
            end, region, other, oclosed, at_cells = "source", fwd, len(bwd), bclosed, [c for c, _k in ss]
        elif bclosed:
            end, region, other, oclosed, at_cells = "goal", bwd, len(fwd), fclosed, sorted(goals)
        else:
            return None                       # neither flood ended inside the cap: not a small closed region
        nx, ny = self.nx, self.ny
        pts = [self.xy(c) for c in at_cells]
        if len(pts) > 64:
            pts = pts[::len(pts) // 64 + 1]

        def dist(c):
            x, y = self.xy(c)
            return min(math.hypot(x - px, y - py) for px, py in pts)
        wall, viaw, edge, no_via = {}, {}, set(), 0

        def note(table, kind, label, n2, key, c):
            rec = table.setdefault((kind, label, n2), dict(kind=kind, label=label, net=n2, layer=key, cells=set(),
                                                           dist=1e9, at=None))
            if c not in rec["cells"]:
                rec["cells"].add(c)
                d = dist(c)
                if d < rec["dist"]:
                    rec["dist"], rec["at"], rec["layer"] = d, self.xy(c), key

        def why(table, c, key):
            """What makes cell c on grid `key` unusable for this net."""
            if self.static[key][c]:
                t = self.why[key].get(c) if self.why is not None else None
                note(table, "obstacle" if t is not None else "unnamed",
                     self.labels[t] if t is not None else "a static obstacle (no label)", None, key, c)
                return
            for n2, o2 in self.own.items():
                if n2 != net and c in o2["fixed"][key]:
                    note(table, "net", f"{n2}'s own copper", n2, key, c)
        moves = ((1, 0), (-1, 0), (0, 1), (0, -1)) + (((1, 1), (1, -1), (-1, 1), (-1, -1)) if self.diagonal else ())
        states = region if region else {(c, li[k]) for c, k in gs}       # no free goal cell: the goal cells ARE the wall
        by_layer = {}
        for c, l in states:
            key = self.layers[l]
            by_layer[key] = by_layer.get(key, 0) + 1
            if not region:
                why(wall, c, key)
                continue
            i, j = c % nx, c // nx
            for di, dj in moves:
                ni, nj = i + di, j + dj
                if not (0 <= ni < nx and 0 <= nj < ny):
                    edge.add("west" if ni < 0 else "east" if ni >= nx else "north" if nj < 0 else "south")
                    continue
                nc = nj * nx + ni
                if (nc, l) not in region and not free(nc, key):
                    why(wall, nc, key)
            others = [k for k in self.layers if k in lays and k != key]
            if others and not any((c, li[k]) in region for k in others):
                no_via += 1
                if not free(c, "via"):
                    why(viaw, c, "via")
                elif vm is not None and not vm[c]:
                    note(viaw, "unnamed", "the job's via mask", None, "via", c)
                else:
                    for k in others:
                        if not free(c, k):
                            why(viaw, c, k)

        def rows(table):
            out = sorted(table.values(), key=lambda r: (r["dist"], r["label"]))
            for r in out:
                r["cells"] = len(r["cells"])
            return out
        return dict(net=net, end=end, at=pts[0] if end == "source" else self.xy(min(goals)),
                    layers=sorted(by_layer, key=self.layers.index), cells=len(region), by_layer=by_layer,
                    goals=len(gs), free_goals=len(gfree), other=other, other_closed=oclosed,
                    edge=sorted(edge), wall=rows(wall), via=rows(viaw), no_via=no_via)

    @staticmethod
    def blocked_lines(d, top=6):
        """blocked_end()'s report as the block a run prints (netgroup.py, the tests)."""
        end, other = d["end"], ("goal" if d["end"] == "source" else "source")
        where = ", ".join(f"{d['by_layer'][k]} on {k}" for k in d["layers"]) or "none"
        out = [f"BLOCKED {end.upper()}: {d['net']} is shut in at its {end}, near ({d['at'][0]:.3f}, {d['at'][1]:.3f}) mm"]
        if end == "source":
            out.append(f"   from its source it reaches {d['cells']} cell(s) ({where}) and none of its "
                       f"{d['goals']} goal cell(s)")
        elif not d["cells"]:
            out.append(f"   none of its {d['goals']} goal cell(s) is free to stand on")
        else:
            out.append(f"   from its goal it reaches {d['cells']} cell(s) ({where}) and never its source")
        out.append(f"   (its {other} end reaches {d['other']}{'' if d['other_closed'] else '+'} cell(s))")
        if d["wall"]:
            out.append("   what closes it, nearest first (distance from the " + end + "; cells of the wall):")
            for r in d["wall"][:top]:
                out.append(f"      {r['dist']:6.3f} mm  {r['layer']:7s} {r['label']}  -- {r['cells']} cell(s), "
                           f"nearest at ({r['at'][0]:.3f}, {r['at'][1]:.3f})")
            if len(d["wall"]) > top:
                out.append(f"      ... and {len(d['wall']) - top} more")
        if d["edge"]:
            out.append(f"   it also ends at the window's {' / '.join(d['edge'])} edge: a wider window may open it")
        if d["via"]:
            out.append(f"   no layer change inside it ({d['no_via']} cell(s) without a legal via), because of:")
            for r in d["via"][:max(2, top // 2)]:
                out.append(f"      {r['dist']:6.3f} mm  {r['layer']:7s} {r['label']}  -- {r['cells']} cell(s)")
        out.append("   a blocked " + ("start" if end == "source" else "end") + " point, not congestion: no "
                   "price, seed or iteration count opens it.  Move or drop the nearest blocker, start the net "
                   "somewhere else, open another layer to it, or widen the window.")
        return out

    def stuck_lines(self):
        """self.stuck as the block a run prints: the nets, the cells in board mm, what it means."""
        d = self.stuck
        out = [f"STUCK at iteration {d['iteration']}: the same {d['ncells']} cell(s) have been contested for "
               f"{d['window']} iterations with no new cell and no fewer overlaps, and no net on them has a way "
               "round the others"]
        out.append("   nets: " + ", ".join(f"{n} ({c} cell(s))" for n, c in d["nets"].items()))
        for key, b in d["boxes"].items():
            out.append(f"   {key}: {b['cells']} cell(s) in x {b['x0']:.3f} .. {b['x1']:.3f}, "
                       f"y {b['y0']:.3f} .. {b['y1']:.3f} mm")
        out.append("   these nets need the same channel: give one another layer / move the via / widen the window")
        if d["open_if_lifted"]:
            out.append("   (" + ", ".join(d["open_if_lifted"]) + " would pass if the nets that are NOT in conflict "
                       "lay elsewhere: the channel is shut by routed nets, not by the board alone -- another "
                       "--seed or --full may open it without a change to the spec)")
        else:
            out.append("   (no way even with every net outside this conflict lifted: the board itself has too few "
                       "lanes there for these nets)")
        return out

    def _stuck_test(self, it, jobs, bad, recent, window):
        """negotiate()'s second test, once the overlaps have recycled the same cells for `window`
        iterations: could ANY net that overlaps now be laid clear of all the others as they lie
        (way_round(): a flood, so "no" is certain)?  One that could will be, as the price
        rises -- the run is slow, not stuck.  Fills self.stuck and returns True when none can."""
        byjob = {jb["net"]: jb for jb in jobs}
        now = [n for n in sorted(bad) if bad[n] and n in byjob]
        for n in now:
            if self.way_round(byjob[n]):
                return False
        per = {}
        for cur in recent:
            for n, cells in cur.items():
                per.setdefault(n, set()).update(cells)
        union = set().union(*per.values()) if per else set()
        boxes = {}
        for key, c in sorted(union):
            x, y = self.xy(c)
            b = boxes.setdefault(key, dict(cells=0, x0=x, y0=y, x1=x, y1=y))
            b["cells"] += 1
            b["x0"], b["y0"], b["x1"], b["y1"] = min(b["x0"], x), min(b["y0"], y), max(b["x1"], x), max(b["y1"], y)
        settled = [n for n in byjob if n not in per]
        self.stuck = dict(iteration=it, window=window, ncells=len(union),
                          nets={n: len(per[n]) for n in sorted(per)}, boxes=boxes,
                          cells=[[k, round(self.xy(c)[0], 4), round(self.xy(c)[1], 4)] for k, c in sorted(union)],
                          open_if_lifted=[n for n in now if settled and self.way_round(byjob[n], soft_nets=settled)])
        return True

    def _route_in_corridor(self, jb, prev, corridor, **kw):
        args = (jb["net"], jb["src"], jb["src_layer"], jb["goals"], jb["goal_layer"], jb["allowed"])
        opt = dict(via_mask=jb.get("via_mask"), bias=jb.get("bias"), start_dir=jb.get("start_dir", 0),
                   goal_dirs=jb.get("goal_dirs"), **kw)
        if not corridor:
            return self.route(*args, **opt)
        nx = self.nx
        cells = [self.cell(*jb["src"])] + list(jb["goals"])
        if prev:
            cells += [c for _l, run in prev[0] for c in run] + list(prev[1])
        ii, jj = [c % nx for c in cells], [c // nx for c in cells]
        span = max(max(ii) - min(ii), max(jj) - min(jj))
        m = max(int(corridor / self.step + 0.5), int(0.3 * span))
        while True:
            box = (min(ii) - m, min(jj) - m, max(ii) + m, max(jj) + m)
            res = self.route(*args, box=box, **opt)
            whole = box[0] <= 0 and box[1] <= 0 and box[2] >= nx - 1 and box[3] >= self.ny - 1
            if res is not None or whole:
                return res
            m *= 2

    def negotiate(self, jobs, iters=60, pres0=0.5, pres_mul=1.5, hist_inc=0.3,
                  log=print, seed=1, on_iter=None, pres_max=1e30, hist_decay=1.0, rip="conflict", stall=4,
                  corridor=None, jacobi=False, stuck=20, **kw):
        """jobs: [dict(net, src, src_layer, goals, goal_layer, allowed)].
        Returns {net: (runs, vias)} with no overlaps, or None.  on_iter(it, paths, bad) runs
        after every iteration; a truthy return (a reason) stops an unconverged run there.
        self.walled names the net that had no path at all (None otherwise).

        stuck=N (default 20; 0: never): stop -- None, with the report in self.stuck -- when the
        run is negotiating over something no price can settle.  Three conditions, all needed:

          1. NO NEW CELL for N iterations: every cell contested in each of them was already
             contested within the N iterations before it.  A search that is still working moves
             its overlaps (a ribbon creeping sideways contests each lane once); one that is
             stuck recycles the same cells -- the SAME cells every time when two nets want one
             lane, each lane in turn when three nets want two (so "contested in most of the
             last N" is no test: there each cell is contested every second iteration).
          2. NO NEW MINIMUM of the overlap count in those N iterations: a run whose overlaps
             shrink onto cells it already knew is converging, however slowly.
          3. NO WAY ROUND.  1 and 2 also hold for a net whose clean detour is merely long: the
             price reaches it later (pres grows by pres_mul per iteration; at 1.1 a detour of
             a few hundred cells round six contested ones is taken near iteration 25-40).  So
             every net that overlaps now is flooded with ALL the other nets' copper hard where
             it lies (way_round(): no turn rule, no expansion limit -- a superset of any route,
             so "no way" is certain).  Only when NONE of them has a way is the run stopped.

          The overlap COUNT standing still is not among them: it sits flat for dozens of
          iterations in runs that then converge.

        The earliest stop is iteration N + 1; while 1 and 2 hold, 3 is tested every N//4
        iterations.  It reads the grid and changes nothing: a run that converges gives the same
        paths with it on or off (the harness's router fixture, 14 iterations: its result hash
        did not move).  board1's R2 on In2.Cu is what it is for: two nets through a one-track
        channel at an IC's edge, six cells contested from iteration 6 on, 433 iterations and
        three minutes before the run gave up.

        rip="conflict" (the default): after iteration 1 only the nets that overlap another are
        ripped up and routed again -- the rest keep their paths, and their cells' rising
        present and history costs still push the others round them.  When the overlap count has
        not fallen for `stall` iterations, one iteration re-routes every net (a net that does
        not overlap may have to move to make room).  rip="full" re-routes every net every
        iteration (classic PathFinder; the result the router gave before conflict-only rip-up).
        Deterministic per seed either way.

        corridor=M (mm): each net's search stays inside the box round its source, its goals
        and its previous path, grown by max(M, 30 % of the source-goal distance); a net that
        finds no path there tries again in a box twice as wide, up to the whole window, so a
        corridor never walls a net in that the window would not.  None: the whole window.

        jacobi=True: the nets of one iteration are routed in parallel (tools/par.py, forked),
        each against the congestion the iteration STARTED with -- not against the nets routed
        before it in the same iteration (Gauss-Seidel, the default).  A different convergence:
        two nets may both take the same free lane and meet there next iteration.  The same
        result with HW_JOBS=1: a net routed in-process restores its old path before the next."""
        self.walled = None
        self.stuck = None
        if rip not in ("conflict", "full"):
            raise ValueError(f"rip must be 'conflict' or 'full', got {rip!r}")
        bad, best, since, full_next = {}, None, 0, False
        stuck = max(0, int(stuck or 0))
        seen_in, recent, last_new, low, last_low, tested = {}, [], 0, None, 0, 0
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
            full = rip == "full" or it == 1 or full_next
            full_next = False
            todo = [jb for jb in jobs if full or jb["net"] not in paths or bad.get(jb["net"])]
            if jacobi:
                def one(jb, pres=pres):
                    n = jb["net"]
                    old = paths.get(n)
                    self.unroute(n)
                    res = self._route_in_corridor(jb, old, corridor, pres=pres, hmap=hm, **kw)
                    self.unroute(n)
                    if old:
                        self.lay(n, *old)          # the next net sees the iteration's starting congestion
                    return res
                results = par.pmap(one, todo)
                for jb, res in zip(todo, results):
                    n = jb["net"]
                    if res is None:
                        log(f"   iteration {it}: {n} has no path even through the others -- walled in")
                        self.walled = n
                        return None
                for jb, res in zip(todo, results):
                    n = jb["net"]
                    self.unroute(n)
                    paths[n] = res
                    self.lay(n, *res)
            for jb in ([] if jacobi else todo):
                n = jb["net"]
                self.unroute(n)
                res = self._route_in_corridor(jb, paths.get(n), corridor, pres=pres, hmap=hm, **kw)
                if res is None:
                    log(f"   iteration {it}: {n} has no path even through the others -- walled in")
                    self.walled = n
                    return None
                paths[n] = res
                self.lay(n, *res)
            bad = {n: self.conflicts(n, *paths[n]) for n in paths}
            nbad = sum(1 for v in bad.values() if v)
            ncell = sum(len(v) for v in bad.values())
            log(f"   iteration {it}: pres {pres:.2f}, {nbad} nets overlap on {ncell} cells"
                + (" (all nets re-routed)" if full and rip != "full" and it > 1 else ""))
            if best is None or ncell < best:
                best, since = ncell, 0
            else:
                since += 1
                if since >= stall:
                    full_next, since = True, 0
            self.last = (dict(paths), bad)
            stop = on_iter(it, paths, bad) if on_iter else None
            if not ncell:
                return paths
            if stop:
                log(f"   iteration {it}: stopped -- {stop}")
                return None
            if stuck:
                cur = {n: frozenset(v) for n, v in bad.items() if v}
                cells = set().union(*cur.values())
                if any(kc not in seen_in for kc in cells):
                    last_new = it                  # a cell not contested in the `stuck` iterations before
                if low is None or ncell < low:
                    low, last_low = ncell, it
                recent.append(cur)
                for kc in cells:
                    seen_in[kc] = seen_in.get(kc, 0) + 1
                if len(recent) > stuck:
                    for kc in set().union(*recent.pop(0).values()):
                        if seen_in[kc] == 1:
                            del seen_in[kc]
                        else:
                            seen_in[kc] -= 1
                if it - max(last_new, last_low) >= stuck and it - tested >= max(1, stuck // 4):
                    tested = it
                    if self._stuck_test(it, jobs, bad, recent, stuck):
                        log(f"   iteration {it}: stuck -- the same {self.stuck['ncells']} cell(s) for {stuck} "
                            "iterations and no net on them has a way round the others")
                        return None
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
