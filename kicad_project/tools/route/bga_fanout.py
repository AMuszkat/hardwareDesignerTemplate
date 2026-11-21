#!/usr/bin/env python3
"""A fine-pitch BGA's fan-out by ring and site, as a plan: NXP's rule (AN10778 §4.3; board1 D115).

    tools/.venv/bin/python tools/route/bga_fanout.py plan --board board1 --config routing/U1_fanout.json \
        --out routing/U1_fanout_plan.json [--vias-out routing/U1_vias.json]
    tools/kpy tools/route/bga_fanout.py balls --board board1 --ref U1 --out balls.json   # (plan calls it itself)

The plan needs OR-Tools (tools/.venv); the balls come from the board through tools/kpy (pcbnew) -- `plan`
runs that step itself unless the config (or --balls) names a dump.  Nothing is written to a board.

THE RULE (every number from the config, none from here):
  rings 1..R (`rings_top`, NXP: 2) leave on the TOP layer: a ring-1 ball straight out of its land, a
     ring-2 ball through a LANE -- the gap between two ring-1 lands, or an empty ring-1 site -- one track a
     lane.  `lanes` below counts, per side, ring-2 tracks against the lanes they can reach (k-1/2, k+1/2,
     k if ring-1 site k is empty): an exact bipartite matching, corner ring-2 balls on either side.
  ring 1 power/ground balls go OUT of the field to a via (corners diagonally): no site.
  every other connected ball (ring >= 3, and ring-2 power/ground under `pg_ring2: "site"`) takes a via in an
     EMPTY lattice site next to it, orthogonal or diagonal (the stub's clearances are computed from the
     config and printed; a diagonal that does not clear is dropped), sites in ring < `min_site_ring` excluded
     (a via there walls the top-layer lanes).  One net a site.  A signal ball never shares; a power/ground
     ball shares with at most `share_cap`-1 others of its net, each on its own stub.  No two diagonal stubs
     in one lattice square (they would cross).
  the assignment is EXACT (CP-SAT), lexicographic: (1) every ball served; (2) the most vias -- share no
     more than the field needs; (3) the weighted vias, `core_nets` weighted highest -- they share last;
     (4) the fewest diagonal stubs.  An infeasible (1) is reported with the balls the solver could not
     serve when each is relaxed alone (`--why`).

CONFIG (JSON, the board's numbers, each with its source):
  {"ref": "U1", "land": 0.225, "via": [0.4, 0.2], "width": 0.0889, "clearance": 0.0889,
   "rings_top": 2, "pg_ring2": "site", "min_site_ring": 2, "share_cap": 4,
   "power_nets": "^(GND|VDD|NVCC|\\\\+)", "core_nets": "^(VDD_ARM|VDD_SOC)$",
   "weights": {"core": 4, "power": 2, "ground": 1}, "ground_nets": "^GND$",
   "rail_current_A": {"VDD_ARM": 3.0, ...}, "source": "10_layout.md #39 ..."}
  optional (each absent = the rule above unchanged):
  "share_cap_net": {"VDD_GPU_VPU_DRAM": 3}  a net's own cap (the smaller of it and share_cap wins)
  "via_current": {"A_a_via": 0.527, "rail_A": {"VDD_ARM": 2.2}, "source": ...}  a net's cap from what one via
     may carry: floor(A_a_via / (rail_A / the net's connected balls)), at least 1 (the D83 neck rule)
  "obstacles": path | [[x, y, r] | {x, y, r} | {x, y, hw, hh, net, tag}]  copper a through via passes (the
     other side's pads under the field): a site whose via pad comes nearer than `obstacle_clearance`
     (default `clearance`) is barred, for every net -- its own net's pad too (a via never touches a pad)
  "require_vias": path | [{net, x, y, hw, hh, tag[, reach]}]  a pad (a decoupling cap's) that must get a via
     of its net, `require_reach` (default 1.27) centre to centre, `require_link`: "axis" (default: on the
     pad's axis, the via-links rule) | "diag" (45 degrees too, last resort) | "any" (one straight link at any
     angle: inside a BGA courtyard, where the guide's `bga field` row lifts the via-link rule), the link
     clear of other nets' pads and of every other net's via it would pass -- a legal straight link
  "cap_room": {"caps": [{tag, ball[, ground_net, reach, fp]}], "fp": {d, pw, ph, cw, ch}, "bounds": [x0, y0,
     x1, y1], "reach": 1.27, "clearance", "own_gap", "link_width", "ground_net": "GND", "grid": 0.02, "keep": 2,
     "pin1_rot": {"0": [-1, 0], ...}, "source"}  a ball's ONLY capacitor must keep a legal spot AT THAT BALL'S
     via (whichever site the solve gives it): its power pad ON that via or one straight axis link <= reach to it,
     its ground pad the same to a via of the ground net, the courtyard inside `bounds`; a pad keeps `clearance`
     to every other net's via (`own_gap` to its own outside the pad), a link via/2 + link_width/2 + clearance, a
     link never runs back along the body.  The spots (grid `grid`, 4 directions; `fp` d = pad centre from the
     cap's centre, pw x ph the pad along x x across, cw x ch the courtyard) are enumerated per ball site, and the
     solve must pick one a cap, the vias its pads cannot live beside left out, courtyards + links of the caps
     never overlapping (`link`: "axis" default | "diag" -- 45-degree links too, their copper not in the overlap
     test).  Reported in counts.solver.cap_room (`pin1_rot` maps the power pad's direction to rot)
     `angles`: [deg, ...] (cap_room or one cap): also the cap turned that far off the axes (each with its four
     turns), judged in its own frame -- links along the pad's axes --, its courtyard's corners in `bounds`, only
     the courtyard's bounding box in the overlap test (default none: the axis turns only; old configs unchanged)
  "soft_pads_first": true  when the plan is infeasible, the relaxed solve meets the most required pads / cap
     spots FIRST and then serves the most balls: its unserved list names the balls those pads would cost

OUTPUT (--out): {"balls": {ball: {net, ring, kind, exit, side, lane | site, shared_with}}, "vias":
  [{x, y, net, balls}], "lanes": {side: {...}}, "rails": {net: {balls, vias, max_balls_a_via, A_a_via}},
  "counts": {...}}.  --vias-out: fanout.py's model format (one entry per ball served by a site via), the
  input escape_bound.py and `fanout.py layers` take.  Exit 0 when every ball is served and every ring-2
  track has a lane; 1 otherwise (the counts say which).
"""
import argparse
import collections
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
EPS = 1e-6


def short(n):
    return (n or "").split("/")[-1]


def connected(n):
    s = short(n)
    return bool(s) and not s.startswith("unconnected")


# ----------------------------------------------------------------------------- balls
def cmd_balls(a):
    sys.path[:0] = [str(HERE), str(TOOLS / "place")]
    import fanout
    ns = argparse.Namespace(balls=None, board=a.board, ref=a.ref)
    b = fanout.load_balls(ns)
    json.dump(b, open(a.out, "w"), indent=0)
    print(f"{len(b)} balls of {a.ref} -> {a.out}")
    return 0


def board_dir(board):
    sys.path.insert(0, str(TOOLS))
    import boardsel
    for f in ("board_dir_arg", "resolve", "board_dir"):
        if hasattr(boardsel, f):
            try:
                return Path(getattr(boardsel, f)(board))
            except Exception:
                pass
    return Path(board)


def load_balls(a, cfg, bdir):
    src = a.balls or cfg.get("balls")
    if src:
        p = Path(src)
        if not p.is_absolute():
            p = (bdir / p) if bdir and (bdir / p).exists() else p
        return json.load(open(p)), str(p)
    out = Path(a.out).with_suffix(".balls.json")
    cmd = [str(TOOLS / "kpy"), str(HERE / "bga_fanout.py"), "balls", "--ref", cfg["ref"], "--out", str(out)]
    if a.board:
        cmd += ["--board", a.board]
    subprocess.run(cmd, check=True)
    return json.load(open(out)), str(out)


# ----------------------------------------------------------------------------- the lattice
class Field:
    def __init__(self, balls):
        xs = sorted({round(b["x"], 4) for b in balls})
        ys = sorted({round(b["y"], 4) for b in balls})
        self.p = min(q - p for p, q in zip(xs, xs[1:]) if q - p > 1e-3)
        py = min(q - p for p, q in zip(ys, ys[1:]) if q - p > 1e-3)
        if abs(py - self.p) > 1e-3:
            sys.exit(f"pitch {self.p} x {py}: a rectangular pitch is not handled")
        self.x0, self.y0 = xs[0], ys[0]
        self.nx = round((xs[-1] - xs[0]) / self.p) + 1
        self.ny = round((ys[-1] - ys[0]) / self.p) + 1
        self.at = {}
        for b in balls:
            i, j = round((b["x"] - self.x0) / self.p), round((b["y"] - self.y0) / self.p)
            if abs(self.x0 + i * self.p - b["x"]) > 1e-3 or abs(self.y0 + j * self.p - b["y"]) > 1e-3:
                sys.exit(f"ball {b['ball']} off the lattice")
            self.at[(i, j)] = b

    def empties(self):
        if not hasattr(self, "_empties"):
            self._empties = [(i, j) for i in range(self.nx) for j in range(self.ny) if (i, j) not in self.at]
        return self._empties

    def xy(self, q):
        return (round(self.x0 + q[0] * self.p, 4), round(self.y0 + q[1] * self.p, 4))

    def ring(self, q):
        return min(q[0], q[1], self.nx - 1 - q[0], self.ny - 1 - q[1]) + 1

    def inside(self, q):
        return 0 <= q[0] < self.nx and 0 <= q[1] < self.ny

    def sides(self, q):
        """The sides a ball on ring r faces (both at a corner): N = y min, S = y max, W = x min, E = x max."""
        r = self.ring(q) - 1
        s = []
        if q[1] == r:
            s.append(("N", q[0]))
        if q[1] == self.ny - 1 - r:
            s.append(("S", q[0]))
        if q[0] == r:
            s.append(("W", q[1]))
        if q[0] == self.nx - 1 - r:
            s.append(("E", q[1]))
        return s

    def ring1_site(self, side, k):
        return {"N": (k, 0), "S": (k, self.ny - 1), "W": (0, k), "E": (self.nx - 1, k)}[side]


def geometry(F, cfg):
    p, land, (vp, _vd), w, c = F.p, cfg["land"], cfg["via"], cfg["width"], cfg["clearance"]
    g = {
        "via_land": p - vp / 2 - land / 2,                       # site via to an orthogonal neighbour's land
        "via_via": p - vp,                                       # two vias in neighbouring sites, pad to pad
        "diag_stub_land": p / math.sqrt(2) - land / 2 - w / 2,   # a diagonal stub to the two corner lands
        "diag_stub_via": p / math.sqrt(2) - vp / 2 - w / 2,      # a diagonal stub to a via in a corner site
        "lane_gap": (p - land) / 2 - w / 2,                      # one track between two lands, a side
        "lane_pitch_gap": p / 2 - w,                             # two lanes side by side (k, k+1/2), edge to edge
    }
    g["site_ok"] = g["via_land"] + EPS >= c and g["via_via"] + EPS >= c
    g["diag_ok"] = g["diag_stub_land"] + EPS >= c and g["diag_stub_via"] + EPS >= c
    g["lane_ok"] = g["lane_gap"] + EPS >= c
    return g


# ----------------------------------------------------------------------------- classify
def classify(F, cfg):
    pw = re.compile(cfg["power_nets"])
    gn = re.compile(cfg.get("ground_nets", "^GND$"))
    core = re.compile(cfg.get("core_nets", "^$"))
    excl = set(cfg.get("exclude_balls", []))
    R = cfg.get("rings_top", 2)
    out = {}
    for q, b in F.at.items():
        n = short(b["net"])
        r = F.ring(q)
        if not connected(b["net"]) or b["ball"] in excl:
            out[q] = dict(kind="none", ring=r)
            continue
        if pw.search(n):
            k = "ground" if gn.search(n) else "core" if core.search(n) else "power"
            pg = True
        else:
            k, pg = "signal", False
        if r == 1:
            ex = "out" if pg else "top"
        elif r <= R:
            ex = ("site" if cfg.get("pg_ring2", "site") == "site" else "out") if pg else "top"
        else:
            ex = "site"
        out[q] = dict(kind=k, ring=r, exit=ex, pg=pg)
    return out


# ----------------------------------------------------------------------------- lanes (rings 1..R, top layer)
def lanes(F, cls):
    """Ring-2 top-layer tracks against lanes, exact (max bipartite matching)."""
    lane_of = collections.defaultdict(list)          # ring-2 ball -> [(side, pos2)] (pos in half-pitches)
    side_lanes = collections.defaultdict(set)
    r1 = collections.Counter()
    for q, c in cls.items():
        if c["kind"] == "none" or c.get("exit") not in ("top", "out"):
            continue
        if c["ring"] == 1:
            if c["exit"] == "out":
                continue                             # ring-1 power/ground: straight out, no lane counted
            for s, k in F.sides(q)[:1]:
                r1[s] += 1
        elif c["ring"] == 2:
            for s, k in F.sides(q):
                for d in (-1, 1):
                    lane_of[q].append((s, 2 * k + d))
                if F.ring1_site(s, k) not in F.at:
                    lane_of[q].append((s, 2 * k))
    n = F.nx  # square field
    for s in "NSWE":
        for k2 in range(1, 2 * (n - 1)):
            if k2 % 2 or F.ring1_site(s, k2 // 2) not in F.at:
                side_lanes[s].add(k2)
    owner = {}

    def aug(q, seen):
        for L in lane_of[q]:
            if L in seen:
                continue
            seen.add(L)
            if L not in owner or aug(owner[L], seen):
                owner[L] = q
                return True
        return False
    unmatched = [q for q in sorted(lane_of) if not aug(q, set())]
    got = {q: L for L, q in owner.items()}
    rep = {}
    for s in "NSWE":
        r2 = [q for q in lane_of if got.get(q, (None,))[0] == s or (q not in got and F.sides(q)[0][0] == s)]
        rep[s] = dict(ring1_tracks=r1[s], ring2_tracks=len(r2), lanes=len(side_lanes[s]),
                      ring2_laned=sum(1 for q in r2 if q in got),
                      no_lane=[F.at[q]["ball"] for q in r2 if q not in got])
    return got, unmatched, rep


# ----------------------------------------------------------------------------- sites (CP-SAT)
def site_steps(g):
    return [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if g["diag_ok"] else [])


def site_cands(F, g, cfg, q):
    """The empty lattice sites a ball may take a via in: next to it, inside the field, ring >= min_site_ring."""
    msr = cfg.get("min_site_ring", 2)
    bar = cfg.get("_barred", ())
    return [(q[0] + di, q[1] + dj) for di, dj in site_steps(g)
            if F.inside((q[0] + di, q[1] + dj)) and (q[0] + di, q[1] + dj) not in F.at
            and F.ring((q[0] + di, q[1] + dj)) >= msr and (q[0] + di, q[1] + dj) not in bar]


def ring2_fallback(F, g, cls, cfg):
    """A ring-2 power/ground ball with NO empty site beside it (a depopulated package's solid outer frame) goes
    out of the field on the top layer through a lane, as a ring-2 signal does (#39 (4): 'a ring-2 power / GND
    ball may go outside the field instead').  A ring-3+ ball with no site stays unserved -- the solve says so."""
    moved = []
    for q, c in cls.items():
        if c.get("pg") and c["ring"] == 2 and c.get("exit") == "site" and not site_cands(F, g, cfg, q):
            c["exit"], c["why"] = "out", "no empty site beside it"
            moved.append(F.at[q]["ball"])
    return sorted(moved)


def load_list(v, bdir):
    if isinstance(v, str):
        p = Path(v)
        if not p.is_absolute() and bdir and (bdir / p).exists():
            p = bdir / p
        return json.load(open(p))
    return v or []


def obstacle_gap(o, x, y):
    """Edge distance from (x, y) to an obstacle: a disc [x, y, r] / {x, y, r} or a box {x, y, hw, hh}."""
    if isinstance(o, (list, tuple)):
        return math.dist((x, y), o[:2]) - o[2]
    if "r" in o:
        return math.dist((x, y), (o["x"], o["y"])) - o["r"]
    return math.hypot(max(abs(x - o["x"]) - o["hw"], 0), max(abs(y - o["y"]) - o["hh"], 0))


def seg_box_gap(a, b, o):
    """Edge distance from the segment a-b to an obstacle (sampled at 0.01 mm: a report-grade bound)."""
    n = max(2, int(math.dist(a, b) / 0.01) + 1)
    return min(obstacle_gap(o, a[0] + (b[0] - a[0]) * t / (n - 1), a[1] + (b[1] - a[1]) * t / (n - 1))
               for t in range(n))


def barred_sites(F, cfg, obs):
    """Empty lattice sites whose via pad comes nearer an obstacle than the clearance: {site: tag}."""
    clr = cfg.get("obstacle_clearance", cfg["clearance"])
    r = cfg["via"][0] / 2
    out = {}
    for i in range(F.nx):
        for j in range(F.ny):
            q = (i, j)
            if q in F.at:
                continue
            x, y = F.xy(q)
            for o in obs:
                if obstacle_gap(o, x, y) < r + clr - EPS:
                    out[q] = o.get("tag", "obstacle") if isinstance(o, dict) else "obstacle"
                    break
    return out


def net_caps(F, cls, cfg):
    """{net: max balls a via} from share_cap_net and via_current; nets in neither are absent."""
    caps = dict(cfg.get("share_cap_net", {}))
    vc = cfg.get("via_current")
    if vc:
        nb = collections.Counter(short(F.at[q]["net"]) for q, c in cls.items() if c["kind"] != "none")
        for n, I in vc.get("rail_A", {}).items():
            if nb[n]:
                k = max(1, int(math.floor(vc["A_a_via"] * nb[n] / I + EPS)))
                caps[n] = min(caps.get(n, k), k)
    return caps


def req_cands(F, cfg, r, obs):
    """The lattice sites a required pad may take its via in, with the sites its link crosses: [(site, [between])]."""
    reach = r.get("reach", cfg.get("require_reach", 1.27))
    clr = cfg.get("obstacle_clearance", cfg["clearance"])
    w = cfg["width"]
    bar = cfg.get("_barred", {})
    out = []
    for i in range(F.nx):
        for j in range(F.ny):
            s = (i, j)
            if s in F.at or s in bar:
                continue
            x, y = F.xy(s)
            dx, dy = x - r["x"], y - r["y"]
            d = math.hypot(dx, dy)
            if d > reach + EPS:
                continue
            axis = abs(dx) < 1e-3 or abs(dy) < 1e-3
            diag = abs(abs(dx) - abs(dy)) < 1e-3
            mode = cfg.get("require_link", "diag" if cfg.get("require_diag") else "axis")
            if not (axis or (mode == "diag" and diag) or mode == "any"):
                continue
            if any(obstacle_gap(o, r["x"], r["y"]) > EPS and (not isinstance(o, dict) or o.get("net") != r["net"])
                   and seg_box_gap((r["x"], r["y"]), (x, y), o) < w / 2 + clr - EPS for o in obs):
                continue
            between = []                       # empty sites whose via would come too near the link
            lim = cfg["via"][0] / 2 + w / 2 + clr
            for u in F.empties():
                if u != s and seg_box_gap((r["x"], r["y"]), (x, y), [*F.xy(u), 0]) < lim - EPS:
                    between.append(u)
            out.append((s, between))
    return out


def _rect_gap(cx, cy, hw, hh, x, y):
    return math.hypot(max(abs(x - cx) - hw, 0), max(abs(y - cy) - hh, 0))


def _seg_pt(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _seg_rect(a, b, r):
    """Edge distance from the segment a-b to the rect (cx, cy, hw, hh) (0 when it enters it)."""
    n = max(2, int(math.dist(a, b) / 0.005) + 1)
    return min(_rect_gap(*r, a[0] + (b[0] - a[0]) * t / (n - 1), a[1] + (b[1] - a[1]) * t / (n - 1))
               for t in range(n))


def _seg_seg(a, b, c, d):
    def ccw(p, q, r):
        return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])
    if ccw(a, b, c) * ccw(a, b, d) < 0 and ccw(c, d, a) * ccw(c, d, b) < 0:
        return 0.0
    return min(_seg_pt(a, b, c), _seg_pt(a, b, d), _seg_pt(c, d, a), _seg_pt(c, d, b))


def cap_room_cands(F, cfg, cr, cap, q, psites, keys_at, key, knet):
    """The spots a ball-only capacitor may take at ball q: [{sp, sg, forbid, ...}].

    A spot is the cap's power pad ON (centre inside the pad) or ONE straight axis link of <= reach from the via
    site sp the ball's stub ends in, its ground pad the same way to a site sg holding a via of the ground net,
    the courtyard inside `bounds`, a link never running back along the cap's body nor within link_width/2 +
    clearance of the other pad or the other link; `forbid` = the (site, key) vias the spot cannot live beside
    (a pad's rect nearer a via than via/2 + clearance -- `own_gap` for its own net outside the pad -- or a link
    nearer one than via/2 + link_width/2 + clearance).  Grid `grid` mm, the cap's pad geometry `fp`."""
    fp = dict(cr["fp"], **cap.get("fp", {}))
    d, grid, reach = fp["d"], cr.get("grid", 0.02), cap.get("reach", cr.get("reach", 1.27))
    clr, own, wl = cr.get("clearance", cfg["clearance"]), cr.get("own_gap", cr.get("clearance", cfg["clearance"])), \
        cr.get("link_width", cfg["width"])
    vr = cfg["via"][0] / 2
    gnet = cap.get("ground_net", cr.get("ground_net", "GND"))
    diag = cr.get("link", "axis") == "diag"
    bx0, by0, bx1, by1 = cr["bounds"]
    pnet, pkey = short(F.at[q]["net"]), key[q]
    gsites = [(t, k) for t, ks in keys_at.items() for k in sorted(ks) if knet[k] == gnet]
    rnd = lambda v: round(round(v / grid) * grid, 4)
    nstep = int(reach / grid + EPS)
    best = {}
    for sp in psites:
        spx, spy = F.xy(sp)
        for u in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            horiz = u[1] == 0
            hw, hh = (fp["pw"] / 2, fp["ph"] / 2) if horiz else (fp["ph"] / 2, fp["pw"] / 2)
            cw, ch = (fp["cw"], fp["ch"]) if horiz else (fp["ch"], fp["cw"])
            p1s = [(rnd(spx + i * grid), rnd(spy + j * grid))
                   for i in range(-int(hw / grid + EPS), int(hw / grid + EPS) + 1)
                   for j in range(-int(hh / grid + EPS), int(hh / grid + EPS) + 1)]
            p1s += [(rnd(spx - e[0] * k * grid), rnd(spy - e[1] * k * grid))
                    for e in ((1, 0), (-1, 0), (0, 1), (0, -1)) for k in range(1, nstep + 1)]
            if diag:
                p1s += [(rnd(spx - e[0] * k * grid), rnd(spy - e[1] * k * grid))
                        for e in ((1, 1), (-1, 1), (1, -1), (-1, -1)) for k in range(1, int(nstep / math.sqrt(2)) + 1)]
            for P1 in p1s:
                C = (round(P1[0] - u[0] * d, 4), round(P1[1] - u[1] * d, 4))
                P2 = (round(C[0] - u[0] * d, 4), round(C[1] - u[1] * d, 4))
                if not (bx0 <= C[0] - cw / 2 and C[0] + cw / 2 <= bx1 and by0 <= C[1] - ch / 2 and C[1] + ch / 2 <= by1):
                    continue

                def link(P, other, v, net):
                    """None: illegal; 0: via in the pad; else the link length."""
                    if abs(v[0] - P[0]) <= hw + EPS and abs(v[1] - P[1]) <= hh + EPS:
                        return 0.0
                    if not (abs(v[0] - P[0]) < 1e-6 or abs(v[1] - P[1]) < 1e-6
                            or (diag and abs(abs(v[0] - P[0]) - abs(v[1] - P[1])) < 1e-6)):
                        return None
                    L = math.dist(P, v)
                    if L > reach + EPS or _rect_gap(P[0], P[1], hw, hh, *v) < vr + own - EPS:
                        return None
                    if ((v[0] - P[0]) * u[0] + (v[1] - P[1]) * u[1]) * (1 if P == P1 else -1) < -EPS:
                        return None                  # back along the cap's body
                    if _seg_rect(P, v, (other[0], other[1], hw, hh)) < wl / 2 + clr - EPS:
                        return None
                    return L
                L1 = link(P1, P2, (spx, spy), pnet)
                if L1 is None:
                    continue
                for sg, gk in gsites:
                    gx, gy = F.xy(sg)
                    if abs(gx - P2[0]) > reach + hw or abs(gy - P2[1]) > reach + hh:
                        continue
                    L2 = link(P2, P1, (gx, gy), gnet)
                    if L2 is None:
                        continue
                    if L1 > 0 and L2 > 0 and _seg_seg(P1, (spx, spy), P2, (gx, gy)) < wl + clr - EPS:
                        continue
                    forbid, ok = [], True
                    for t, ks in keys_at.items():
                        tx, ty = F.xy(t)
                        if abs(tx - C[0]) > 2 * reach + 1 or abs(ty - C[1]) > 2 * reach + 1:
                            continue
                        for k in ks:
                            nk = knet[k]
                            bad = False
                            for P, n in ((P1, pnet), (P2, gnet)):
                                if abs(tx - P[0]) <= hw + EPS and abs(ty - P[1]) <= hh + EPS:
                                    bad |= nk != n
                                elif _rect_gap(P[0], P[1], hw, hh, tx, ty) < vr + (own if nk == n else clr) - EPS:
                                    bad = True
                            for P, v, n, L in ((P1, (spx, spy), pnet, L1), (P2, (gx, gy), gnet, L2)):
                                if L > 0 and nk != n and _seg_pt(P, v, (tx, ty)) < vr + wl / 2 + clr - EPS:
                                    bad = True
                            if bad:
                                if (t == sp and k == pkey) or (t == sg and k == gk):
                                    ok = False
                                    break
                                forbid.append((t, k))
                        if not ok:
                            break
                    if not ok:
                        continue
                    sig = (sp, sg, gk, frozenset(forbid))
                    boxes = [(C[0] - cw / 2, C[1] - ch / 2, cw, ch)]
                    for P, v, L in ((P1, (spx, spy), L1), (P2, (gx, gy), L2)):
                        if L <= 0 or (abs(v[0] - P[0]) > 1e-6 and abs(v[1] - P[1]) > 1e-6):
                            continue                 # a 45-degree link: its box is not modelled
                        e = wl / 2 + clr / 2
                        b = [min(P[0], v[0]) - e, min(P[1], v[1]) - e, max(P[0], v[0]) + e, max(P[1], v[1]) + e]
                        if abs(v[1] - P[1]) < 1e-6:          # clip the part inside the courtyard
                            b[0], b[2] = (max(b[0], C[0] + cw / 2), b[2]) if v[0] > P[0] else (b[0], min(b[2], C[0] - cw / 2))
                        else:
                            b[1], b[3] = (max(b[1], C[1] + ch / 2), b[3]) if v[1] > P[1] else (b[1], min(b[3], C[1] - ch / 2))
                        if b[2] - b[0] > 1e-4 and b[3] - b[1] > 1e-4:
                            boxes.append((b[0], b[1], b[2] - b[0], b[3] - b[1]))
                    spot = dict(sp=sp, sg=sg, gkey=gk, forbid=forbid, centre=C, dir=u, p1=P1, p2=P2,
                                L1=round(L1, 3), L2=round(L2, 3), boxes=boxes)
                    best.setdefault(sig, []).append(spot)
    out = []
    keep = cr.get("keep", 2)
    for sig, L in best.items():
        L.sort(key=lambda s: (s["L1"] + s["L2"], s["centre"]))
        kept = []
        for s in L:
            if all(math.dist(s["centre"], k["centre"]) > 0.09 for k in kept):
                kept.append(s)
            if len(kept) >= keep:
                break
        out += kept
    for ang in cap.get("angles", cr.get("angles", [])):
        if abs(((ang + 45) % 90) - 45) > 1e-6:      # a rotation off the axes; the axis ones are above
            out += cap_room_cands_rot(F, cfg, cr, cap, q, psites, keys_at, key, knet, ang)
    return out


def cap_room_cands_rot(F, cfg, cr, cap, q, psites, keys_at, key, knet, ang):
    """cap_room_cands for a cap rotated `ang` degrees off the axes (its four turns ang + k*90): the same rules,
    judged in the cap's own frame (pads, links along the PAD's axes -- #48 (3) --, clearances, the body);
    the courtyard's corners inside `bounds`; in the overlap test only the courtyard's bounding box (a link's
    copper is not modelled, as for a 45-degree link)."""
    fp = dict(cr["fp"], **cap.get("fp", {}))
    d, grid, reach = fp["d"], cr.get("grid", 0.02), cap.get("reach", cr.get("reach", 1.27))
    clr, own, wl = cr.get("clearance", cfg["clearance"]), cr.get("own_gap", cr.get("clearance", cfg["clearance"])), \
        cr.get("link_width", cfg["width"])
    vr = cfg["via"][0] / 2
    gnet = cap.get("ground_net", cr.get("ground_net", "GND"))
    diag = cr.get("link", "axis") == "diag"
    bx0, by0, bx1, by1 = cr["bounds"]
    pnet, pkey = short(F.at[q]["net"]), key[q]
    hw, hh, cw, ch = fp["pw"] / 2, fp["ph"] / 2, fp["cw"], fp["ch"]
    gsites = [(t, k) for t, ks in keys_at.items() for k in sorted(ks) if knet[k] == gnet]
    nstep = int(reach / grid + EPS)
    TOL = 1e-6
    best = {}
    for sp in psites:
        O = F.xy(sp)
        for turn in range(4):
            phi = math.radians(ang + 90 * turn)
            u, w = (math.cos(phi), math.sin(phi)), (-math.sin(phi), math.cos(phi))
            loc = lambda p: ((p[0] - O[0]) * u[0] + (p[1] - O[1]) * u[1], (p[0] - O[0]) * w[0] + (p[1] - O[1]) * w[1])
            glob = lambda l: (round(O[0] + l[0] * u[0] + l[1] * w[0], 4), round(O[1] + l[0] * u[1] + l[1] * w[1], 4))
            p1s = [(i * grid, j * grid) for i in range(-int(hw / grid + EPS), int(hw / grid + EPS) + 1)
                   for j in range(-int(hh / grid + EPS), int(hh / grid + EPS) + 1)]
            p1s += [(-e[0] * k * grid, -e[1] * k * grid) for e in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    for k in range(1, nstep + 1)]
            if diag:
                p1s += [(-e[0] * k * grid, -e[1] * k * grid) for e in ((1, 1), (-1, 1), (1, -1), (-1, -1))
                        for k in range(1, int(nstep / math.sqrt(2)) + 1)]
            near = [(t, loc(F.xy(t)), ks) for t, ks in keys_at.items()
                    if abs(F.xy(t)[0] - O[0]) <= 2 * reach + 2 and abs(F.xy(t)[1] - O[1]) <= 2 * reach + 2]
            for P1 in p1s:
                C, P2 = (P1[0] - d, P1[1]), (P1[0] - 2 * d, P1[1])
                corners = [glob((C[0] + sx * cw / 2, C[1] + sy * ch / 2)) for sx in (-1, 1) for sy in (-1, 1)]
                if not all(bx0 <= x <= bx1 and by0 <= y <= by1 for x, y in corners):
                    continue

                def link(P, other, v, sgn):
                    if abs(v[0] - P[0]) <= hw + EPS and abs(v[1] - P[1]) <= hh + EPS:
                        return 0.0
                    if not (abs(v[0] - P[0]) < TOL or abs(v[1] - P[1]) < TOL
                            or (diag and abs(abs(v[0] - P[0]) - abs(v[1] - P[1])) < TOL)):
                        return None
                    L = math.dist(P, v)
                    if L > reach + EPS or _rect_gap(P[0], P[1], hw, hh, *v) < vr + own - EPS:
                        return None
                    if (v[0] - P[0]) * sgn < -EPS:
                        return None
                    if _seg_rect(P, v, (other[0], other[1], hw, hh)) < wl / 2 + clr - EPS:
                        return None
                    return L
                L1 = link(P1, P2, (0.0, 0.0), 1)
                if L1 is None:
                    continue
                for sg, gk in gsites:
                    G = loc(F.xy(sg))
                    if abs(G[0] - P2[0]) > reach + hw or abs(G[1] - P2[1]) > reach + hw:
                        continue
                    L2 = link(P2, P1, G, -1)
                    if L2 is None:
                        continue
                    if L1 > 0 and L2 > 0 and _seg_seg(P1, (0.0, 0.0), P2, G) < wl + clr - EPS:
                        continue
                    forbid, ok = [], True
                    for t, T, ks in near:
                        for k in ks:
                            nk = knet[k]
                            bad = False
                            for P, n in ((P1, pnet), (P2, gnet)):
                                if abs(T[0] - P[0]) <= hw + EPS and abs(T[1] - P[1]) <= hh + EPS:
                                    bad |= nk != n
                                elif _rect_gap(P[0], P[1], hw, hh, *T) < vr + (own if nk == n else clr) - EPS:
                                    bad = True
                            for P, v, n, L in ((P1, (0.0, 0.0), pnet, L1), (P2, G, gnet, L2)):
                                if L > 0 and nk != n and _seg_pt(P, v, T) < vr + wl / 2 + clr - EPS:
                                    bad = True
                            if bad:
                                if (t == sp and k == pkey) or (t == sg and k == gk):
                                    ok = False
                                    break
                                forbid.append((t, k))
                        if not ok:
                            break
                    if not ok:
                        continue
                    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
                    sig = (sp, sg, gk, frozenset(forbid))
                    spot = dict(sp=sp, sg=sg, gkey=gk, forbid=forbid, centre=glob(C),
                                dir=(round(u[0], 4), round(u[1], 4)), angle=round((ang + 90 * turn) % 360, 3),
                                p1=glob(P1), p2=glob(P2), L1=round(L1, 3), L2=round(L2, 3),
                                boxes=[(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))])
                    best.setdefault(sig, []).append(spot)
    out, keep = [], cr.get("keep", 2)
    for sig, L in best.items():
        L.sort(key=lambda s: (s["L1"] + s["L2"], s["centre"]))
        kept = []
        for s in L:
            if all(math.dist(s["centre"], k["centre"]) > 0.09 for k in kept):
                kept.append(s)
            if len(kept) >= keep:
                break
        out += kept
    return out


def solve_sites(F, g, cls, cfg, workers, reqs=(), obs=(), soft=False):
    from ortools.sat.python import cp_model
    cap = cfg.get("share_cap", 4)
    ncap = cfg.get("_net_caps", {})
    wts = cfg.get("weights", {"core": 4, "power": 2, "ground": 1})
    need = [q for q, c in cls.items() if c.get("exit") == "site"]
    cand = {q: site_cands(F, g, cfg, q) for q in need}
    m = cp_model.CpModel()
    x = {(q, s): m.NewBoolVar("") for q in need for s in cand[q]}
    key = {q: (short(F.at[q]["net"]) if cls[q]["pg"] else "SIG:" + F.at[q]["ball"]) for q in need}
    by_site = collections.defaultdict(list)
    for (q, s) in x:
        by_site[s].append(q)
    y = {}
    for s, qs in by_site.items():
        for k in {key[q] for q in qs}:
            y[(s, k)] = m.NewBoolVar("")
        m.Add(sum(y[(s, k)] for k in {key[q] for q in qs}) <= 1)
        for k in {key[q] for q in qs}:
            mine = [x[(q, s)] for q in qs if key[q] == k]
            for v in mine:
                m.AddImplication(v, y[(s, k)])
            m.Add(sum(mine) >= y[(s, k)])
            m.Add(sum(mine) <= (min(cap, ncap.get(k, cap)) if not k.startswith("SIG:") else 1))
    for q in need:
        m.Add(sum(x[(q, s)] for s in cand[q]) == 1) if not soft else m.Add(sum(x[(q, s)] for s in cand[q]) <= 1)
    knet = {key[q]: short(F.at[q]["net"]) for q in need}
    zreq = []                                        # required pads: one same-net via in reach, link clear
    req_info = []
    for r in reqs:
        zs = []
        for s, between in req_cands(F, cfg, r, obs):
            ks = [k for k in {key[q] for q in by_site.get(s, [])} if knet[k] == r["net"]]
            if not ks:
                continue
            z = m.NewBoolVar("")
            m.Add(sum(y[(s, k)] for k in ks) >= z)
            for t in between:
                for k in {key[q] for q in by_site.get(t, [])}:
                    if knet[k] != r["net"]:
                        m.Add(z + y[(t, k)] <= 1)
            zs.append((s, z))
        if not zs:
            req_info.append(dict(tag=r.get("tag"), net=r["net"], why="no site of its net in reach"))
        else:
            if not soft:
                m.Add(sum(z for _, z in zs) >= 1)
        zreq.append((r, zs))
    zroom = []                                       # cap room: a ball-only cap's legal spot at its ball's via
    cr = cfg.get("cap_room")
    if cr:
        qof = {F.at[q]["ball"]: q for q in F.at}
        xi, yi = [], []
        S = lambda v: int(round(v * 1000))
        keys_at = {t: {key[b] for b in qs} for t, qs in by_site.items()}
        yc, ycuse = {}, collections.defaultdict(list)   # cap-only vias: an empty site no ball takes (#44)
        knet = dict(knet)
        if cr.get("free_vias"):
            fmr = cr.get("free_via_min_ring", cfg.get("min_site_ring", 2))
            bar = cfg.get("_barred", {})
            for n in {c.get("ground_net", cr.get("ground_net", "GND")) for c in cr["caps"]}:
                k = "CAPVIA:" + n
                knet[k] = n
                for t in F.empties():
                    if F.ring(t) >= fmr and t not in bar:
                        yc[(t, k)] = m.NewBoolVar("")
                        keys_at.setdefault(t, set()).add(k)
                        real = [y[(t, k2)] for k2 in {key[b] for b in by_site.get(t, [])}]
                        m.Add(sum(real) + yc[(t, k)] <= 1)
        Y = lambda t, k: yc[(t, k)] if (t, k) in yc else y[(t, k)]
        for cap in cr["caps"]:
            q = qof.get(cap["ball"])
            if q not in cand:
                req_info.append(dict(tag=cap["tag"], net="?", why=f"ball {cap['ball']} takes no site via"))
                continue
            spots = cap_room_cands(F, cfg, cr, cap, q, cand[q], keys_at, key, knet)
            zs = []
            for sp in spots:
                z = m.NewBoolVar("")
                m.AddImplication(z, x[(q, sp["sp"])])
                m.AddImplication(z, Y(sp["sg"], sp["gkey"]))
                if (sp["sg"], sp["gkey"]) in yc:
                    ycuse[(sp["sg"], sp["gkey"])].append(z)
                for t, k in sp["forbid"]:
                    m.AddImplication(z, Y(t, k).Not())
                for bx, by, bw, bh in sp["boxes"]:
                    xi.append(m.NewOptionalFixedSizeIntervalVar(S(bx), S(bw), z, ""))
                    yi.append(m.NewOptionalFixedSizeIntervalVar(S(by), S(bh), z, ""))
                zs.append((sp, z))
            if not zs:
                req_info.append(dict(tag=cap["tag"], net=short(F.at[q]["net"]), why="no legal spot at any site"))
            elif not soft:
                m.Add(sum(z for _, z in zs) >= 1)
            zroom.append((cap, zs))
        if xi:
            m.AddNoOverlap2D(xi, yi)
        for tk, v in yc.items():                     # a cap-only via exists only for a cap that uses it
            m.Add(v <= sum(ycuse[tk])) if ycuse[tk] else m.Add(v == 0)
    if any("why" in i for i in req_info) and not soft:
        return None, dict(status="REQUIRED PAD WITH NO SITE", need=len(need), required=req_info), cand
    diag = collections.defaultdict(list)             # lattice square (lower-left corner) -> its diagonal stubs
    for (q, s), v in x.items():
        if q[0] != s[0] and q[1] != s[1]:
            diag[(min(q[0], s[0]), min(q[1], s[1]))].append(v)
    for vs in diag.values():
        if len(vs) > 1:
            m.Add(sum(vs) <= 1)
    nvias = sum(y.values())
    kind_of = {}
    for q in need:
        kind_of.setdefault(key[q], cls[q]["kind"])
    wv = sum((1 if k.startswith("SIG:") else wts[kind_of[k]]) * v for (s, k), v in y.items())
    nd = sum(v for (q, s), v in x.items() if q[0] != s[0] and q[1] != s[1])
    sv = cp_model.CpSolver()
    sv.parameters.num_workers = workers
    sv.parameters.max_time_in_seconds = 120
    stages = []
    order = (("vias", nvias, 1), ("weighted", wv, 1), ("diagonals", nd, -1))
    if soft:                                         # what an infeasible rule leaves: the most balls, then pads
        served = sum(x.values())
        mz = []
        for _, zs in zreq:
            if zs:
                b = m.NewBoolVar("")
                m.Add(sum(z for _, z in zs) >= b)
                mz.append(b)
        for _, zs in zroom:
            if zs:
                b = m.NewBoolVar("")
                m.Add(sum(z for _, z in zs) >= b)
                mz.append(b)
        order = (("served", served, 1), ("required_met", sum(mz), 1)) + order[:1]
        if cfg.get("soft_pads_first"):               # which balls the required pads / cap room would cost
            order = (order[1], order[0], order[2])
    for name, obj, sense in order:
        m.Maximize(obj) if sense > 0 else m.Minimize(obj)
        st = sv.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None, dict(status=sv.StatusName(st), need=len(need)), cand
        val = round(sv.ObjectiveValue())
        stages.append((name, val, sv.StatusName(st)))
        m.ClearHints()
        for v in x.values():
            m.AddHint(v, sv.Value(v))
        m.Add(obj == val) if st == cp_model.OPTIMAL else (m.Add(obj >= val) if sense > 0 else m.Add(obj <= val))
    pick = {q: s for (q, s), v in x.items() if sv.Value(v)}
    info = dict(status="ok" if not soft else "relaxed", stages=stages, need=len(need))
    if soft:
        info["unserved"] = sorted(F.at[q]["ball"] + " " + short(F.at[q]["net"]) for q in need if q not in pick)
        info["required_unmet"] = sorted(r.get("tag", "?") + " " + r["net"] for r, zs in zreq
                                        if not any(sv.Value(z) for _, z in zs))
        info["required_unmet"] += sorted(c["tag"] + " cap room at " + c["ball"] for c, zs in zroom
                                         if not any(sv.Value(z) for _, z in zs))
        return pick, info, cand
    if reqs:
        rr = []
        for r, zs in zreq:
            got = sorted((math.dist((r["x"], r["y"]), F.xy(s)), F.xy(s)) for s, z in zs if sv.Value(z))
            rr.append(dict(tag=r.get("tag"), net=r["net"], via=list(got[0][1]), mm=round(got[0][0], 3)))
        info["required"] = rr
    if zroom:
        rr = []
        for c, zs in zroom:
            sp = next(s for s, z in zs if sv.Value(z))
            e = dict(tag=c["tag"], ball=c["ball"], spots=len(zs), power_via=list(F.xy(sp["sp"])),
                     gnd_via=list(F.xy(sp["sg"])), gnd_via_new=sp["gkey"].startswith("CAPVIA:"), centre=list(sp["centre"]), power_dir=list(sp["dir"]),
                     power_pad=list(sp["p1"]), gnd_pad=list(sp["p2"]), L_power=sp["L1"], L_gnd=sp["L2"])
            if cr.get("pin1_rot"):
                e["rot"] = next((float(r) for r, v in cr["pin1_rot"].items() if tuple(v) == tuple(sp["dir"])), None)
            if "angle" in sp:                         # a rotated spot: power pad toward `angle` (y down)
                e["angle"] = sp["angle"]
                e["rot"] = round(((180 - sp["angle"]) + 180) % 360 - 180, 3)
            rr.append(e)
        info["cap_room"] = rr
    return pick, info, cand


# ----------------------------------------------------------------------------- plan
def cmd_plan(a):
    bdir = board_dir(a.board) if a.board else None
    cp = Path(a.config)
    if not cp.is_absolute() and bdir and (bdir / cp).exists():
        cp = bdir / cp
    cfg = json.load(open(cp))
    if bdir and not Path(a.out).is_absolute():
        a.out = str(bdir / a.out)
    balls, bsrc = load_balls(a, cfg, bdir)
    for b in balls:
        if cfg.get("land"):
            b["d"] = cfg["land"]
    F = Field(balls)
    g = geometry(F, cfg)
    c = cfg["clearance"]
    print(f"{cfg['ref']}: {F.nx} x {F.ny} lattice at {F.p} mm, {len(F.at)} balls, "
          f"{F.nx * F.ny - len(F.at)} empty sites; land {cfg['land']}, via {cfg['via'][0]}/{cfg['via'][1]}, "
          f"track {cfg['width']} / clearance {c}")
    print(f"  site via to a neighbour land {g['via_land']:.4f}, to a neighbour via {g['via_via']:.4f} -> "
          f"{'fits' if g['site_ok'] else 'DOES NOT FIT'}")
    print(f"  diagonal stub to corner lands {g['diag_stub_land']:.4f}, to a corner-site via {g['diag_stub_via']:.4f}"
          f" -> {'allowed' if g['diag_ok'] else 'dropped'}")
    print(f"  one track between two lands {g['lane_gap']:.4f} a side; two lanes 1/2 pitch apart "
          f"{g['lane_pitch_gap']:.4f} edge to edge -> {'fits' if g['lane_ok'] else 'DOES NOT FIT'}")
    if not g["site_ok"]:
        return 2
    obs = load_list(cfg.get("obstacles"), bdir)
    reqs = load_list(cfg.get("require_vias"), bdir)
    if obs:
        cfg["_barred"] = barred_sites(F, cfg, obs)
        print(f"  obstacles: {len(obs)}; {len(cfg['_barred'])} of {F.nx * F.ny - len(F.at)} empty sites barred "
              f"(via pad {cfg['via'][0] / 2} + {cfg.get('obstacle_clearance', c)} from them)")
    cls = classify(F, cfg)
    if cfg.get("share_cap_net") or cfg.get("via_current"):
        cfg["_net_caps"] = net_caps(F, cls, cfg)
        print(f"  per-net share caps: {cfg['_net_caps']}")
    fb = ring2_fallback(F, g, cls, cfg)
    if fb:
        print(f"  ring-2 power/ground with no empty site beside it -> out on the top layer: {' '.join(fb)}")
    got, unmatched, lrep = lanes(F, cls)
    pick, info, cand = solve_sites(F, g, cls, cfg, a.workers, reqs, obs)
    if not pick:                                     # why: the same rule with every ball and pad made soft
        _, why, _ = solve_sites(F, g, cls, cfg, a.workers, reqs, obs, soft=True)
        info["why"] = {k: why.get(k) for k in ("stages", "unserved", "required_unmet")}
        print(f"  INFEASIBLE -- relaxed (most balls served, then required pads): {why.get('stages')}")
        print(f"    unserved balls ({len(why.get('unserved') or [])}): {' '.join(why.get('unserved') or [])}")
        if reqs or cfg.get("cap_room"):
            print(f"    required pads unmet ({len(why.get('required_unmet') or [])}): "
                  f"{' '.join(why.get('required_unmet') or [])}")

    cnt = collections.Counter()
    for q, cc in cls.items():
        if cc["kind"] == "none":
            cnt["none"] += 1
            continue
        t = "signal" if cc["kind"] == "signal" else "pg"
        cnt[(t, "ring<=%d" % cfg.get("rings_top", 2) if cc["ring"] <= cfg.get("rings_top", 2) else "ring3+")] += 1
        cnt[(t, cc["exit"])] += 1
    vias = {}
    if pick:
        for q, s in pick.items():
            vias.setdefault(s, []).append(q)
    out_b = {}
    for q, cc in sorted(cls.items(), key=lambda t: F.at[t[0]]["ball"]):
        b = F.at[q]
        e = dict(net=short(b["net"]), ring=cc["ring"], kind=cc["kind"], x=b["x"], y=b["y"])
        if cc["kind"] != "none":
            e["exit"] = cc["exit"]
            if cc["exit"] == "top":
                sd = F.sides(q)
                e["side"] = sd[0][0] if cc["ring"] == 1 else (got[q][0] if q in got else sd[0][0])
                if cc["ring"] >= 2:
                    e["lane"] = got[q][1] / 2 if q in got else None
            elif cc["exit"] == "out":
                sd = F.sides(q)
                e["side"] = "".join(s for s, _ in sd)
                e["diagonal"] = len(sd) > 1
                if cc["ring"] >= 2:
                    e["side"] = got[q][0] if q in got else sd[0][0]
                    e["lane"] = got[q][1] / 2 if q in got else None
                    e["diagonal"] = False
                if cc.get("why"):
                    e["why"] = cc["why"]
            elif pick and q in pick:
                s = pick[q]
                e["site"] = list(F.xy(s))
                e["stub"] = "diagonal" if (s[0] != q[0] and s[1] != q[1]) else "orthogonal"
                e["shared_with"] = [F.at[o]["ball"] for o in vias[s] if o != q]
        out_b[b["ball"]] = e
    vlist = [dict(x=F.xy(s)[0], y=F.xy(s)[1], net=short(F.at[qs[0]]["net"]),
                  balls=sorted(F.at[q]["ball"] for q in qs)) for s, qs in sorted(vias.items())]
    rails = {}
    cur = cfg.get("rail_current_A", {})
    pgnet = {short(F.at[q]["net"]) for q, cc in cls.items() if cc.get("pg")}
    for v in vlist:
        if v["net"] not in pgnet:
            continue
        r = rails.setdefault(v["net"], dict(balls=0, vias=0, hist=collections.Counter()))
        r["balls"] += len(v["balls"])
        r["vias"] += 1
        r["hist"][len(v["balls"])] += 1
    for q, cc in cls.items():                       # pg balls that leave the field (ring 1, or ring 2 'out')
        if cc.get("exit") == "out":
            r = rails.setdefault(short(F.at[q]["net"]), dict(balls=0, vias=0, hist=collections.Counter()))
            r["out_balls"] = r.get("out_balls", 0) + 1
    for n, r in rails.items():
        r["max_balls_a_via"] = max(r["hist"]) if r["hist"] else 0
        r["balls_a_via"] = {str(k): v for k, v in sorted(r.pop("hist").items())}
        if n in cur and r["vias"]:
            r["I_A"] = cur[n]
            r["A_a_via"] = round(cur[n] / (r["vias"] + r.get("out_balls", 0)), 4)
            r["A_a_ball"] = round(cur[n] / (r["balls"] + r.get("out_balls", 0)), 4)
    sig_site = sum(1 for v in vlist if not cls[next(q for q in vias[(round((v["x"] - F.x0) / F.p),
                                                                    round((v["y"] - F.y0) / F.p))])]["pg"])
    pg_vias = len(vlist) - sig_site
    pg_site_balls = sum(len(v["balls"]) for v in vlist) - sig_site
    counts = dict(
        balls=len(F.at), unconnected_or_excluded=cnt["none"],
        signal_top=cnt[("signal", "top")], signal_site=cnt[("signal", "site")],
        signal_total=cnt[("signal", "top")] + cnt[("signal", "site")],
        pg_out=cnt[("pg", "out")], pg_site=cnt[("pg", "site")],
        site_balls_needed=info["need"], served=len(pick or {}),
        vias=len(vlist), signal_vias=sig_site, pg_vias=pg_vias,
        pg_balls_without_own_via=pg_site_balls - pg_vias,
        diagonal_stubs=sum(1 for e in out_b.values() if e.get("stub") == "diagonal"),
        ring2_no_lane=[F.at[q]["ball"] for q in unmatched], ring2_pg_out_no_site=fb, solver=info)
    if obs:
        counts["barred_sites"] = sorted(list(F.xy(q)) for q in cfg["_barred"])
    if cfg.get("_net_caps"):
        counts["net_caps"] = cfg["_net_caps"]
    plan = dict(ref=cfg["ref"], config=str(cp), balls_from=bsrc, geometry=g, counts=counts, lanes=lrep,
                rails=rails, vias=vlist, balls=out_b, source=cfg.get("source", ""))
    json.dump(plan, open(a.out, "w"), indent=1)

    print(f"signal balls: {counts['signal_total']} -- {counts['signal_top']} leave on top (rings 1-"
          f"{cfg.get('rings_top', 2)}), {counts['signal_site']} on a site via")
    print(f"power/ground balls: {cnt[('pg', 'top')] + counts['pg_out'] + counts['pg_site']} -- "
          f"{counts['pg_out']} out of the field, {counts['pg_site']} on site vias")
    print(f"site vias: {info['need']} balls need one; " + (
        f"served {counts['served']} on {counts['vias']} vias ({counts['signal_vias']} signal, "
        f"{counts['pg_vias']} power/ground); {counts['pg_balls_without_own_via']} balls without a via of their "
        f"own; {counts['diagonal_stubs']} diagonal stubs; solver {info['stages']}"
        if pick else f"NOT SERVED -- {info['status']}"))
    print("rails (site vias; out = balls leaving the field):")
    for n, r in sorted(rails.items(), key=lambda t: -t[1]["balls"]):
        print(f"  {n:20s} balls {r['balls']:3d}  vias {r['vias']:3d}  out {r.get('out_balls', 0)}  "
              f"balls a via {r['balls_a_via']}" + (f"  {r['I_A']} A -> {r['A_a_via']} A a via" if 'I_A' in r else ""))
    print("top-layer exits, rings 1-2 (lanes = gaps between ring-1 lands + empty ring-1 sites):")
    for s, r in lrep.items():
        print(f"  {s}: ring-1 {r['ring1_tracks']:3d} straight; ring-2 {r['ring2_tracks']:3d} tracks, {r['lanes']:3d}"
              f" lanes, {r['ring2_laned']:3d} laned" + (f"  NO LANE: {' '.join(r['no_lane'])}" if r['no_lane'] else ""))
    for r in info.get("required", []):
        if "why" in r:
            print(f"  REQUIRED {r['tag']} {r['net']}: {r['why']}")
    for r in info.get("cap_room", []):
        print(f"  cap room {r['tag']} at {r['ball']}: power via {r['power_via']} (link {r['L_power']}), GND via "
              f"{r['gnd_via']}{' NEW (cap only)' if r['gnd_via_new'] else ''} (link {r['L_gnd']}), centre {r['centre']} power pad toward {r['power_dir']}"
              f" -- one of {r['spots']} spots")
    if cfg.get("cap_room") and pick:
        print(f"cap room: {len(info['cap_room'])} of {len(cfg['cap_room']['caps'])} ball-only caps have a legal spot")
    if reqs and pick:
        far = max(info["required"], key=lambda r: r["mm"])
        print(f"required pads: {len(reqs)} of {len(reqs)} get a same-net via on their axis in reach "
              f"(farthest {far['tag']} {far['mm']} mm)")
    if a.vias_out:
        vo = Path(a.vias_out)
        if bdir and not vo.is_absolute():
            vo = bdir / vo
        flat = [dict(ball=bb, net=F.at[q]["net"], x=F.xy(s)[0], y=F.xy(s)[1], bx=F.at[q]["x"], by=F.at[q]["y"],
                     in_pad=False, kind="share" if cls[q]["pg"] else "own")
                for s, qs in vias.items() for q in qs for bb in [F.at[q]["ball"]]]
        json.dump(flat, open(vo, "w"), indent=0)
        print(f"wrote {vo} ({len(flat)} ball->via entries, fanout.py's model format)")
    print(f"wrote {a.out}")
    ok = bool(pick) and not unmatched
    print("BGA_FANOUT " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("balls")
    b.add_argument("--board")
    b.add_argument("--ref", required=True)
    b.add_argument("--out", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--board")
    p.add_argument("--config", required=True)
    p.add_argument("--balls", help="a ball dump [{ball, x, y, net, d}] instead of the board")
    p.add_argument("--out", required=True)
    p.add_argument("--vias-out")
    p.add_argument("--workers", type=int, default=int(os.environ.get("HW_JOBS", "8")))
    a = ap.parse_args()
    return cmd_balls(a) if a.cmd == "balls" else cmd_plan(a)


if __name__ == "__main__":
    sys.exit(main())
