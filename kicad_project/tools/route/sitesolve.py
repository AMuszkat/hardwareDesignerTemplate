#!/usr/bin/env python3
"""sitesolve.py -- an EXACT count of via sites: how many of a set of nets can drop a via in a region at
once, under the board's own rules and exit-lane gate.  A count, never a plan: it writes no board.

    tools/kpy tools/route/sitesolve.py gen --board COPY SPEC.json OUT.json[.gz]
              [--pull NAME:D ...] [--drop NAME ...] [--line NAME ...] [--gate assign|plan|strict]
    tools/.venv/bin/python tools/route/sitesolve.py solve CANDS.json[.gz] OUT.json
              [--free BALL,..] [--freeany] [--gnd new|old|off] [--gate G] [--need N] [--atmost N]
              [--time S] [--workers N]
    python3 tools/route/sitesolve.py run --board COPY SPEC.json --out DIR [gen terms] [solve terms]
    python3 tools/route/sitesolve.py table --board COPY SPEC.json ROWS.json --out DIR [--jobs N]

THE TRAP.  A depth-first site search capped at N nodes cannot prove "no set": it stops, and "none in
400k nodes" reads like a wall.  board1 NORTHGAP r10 (2026-11-24): the capped DFS found 3 of 8 nets and
the exact count on the same strip found 4 of 8 -- and proved 8 of 8 infeasible.  This tool is that exact
count, generic: `gen` lists EVERY candidate site on a grid (pcbnew), `solve` (CP-SAT, ortools) maximises
the nets sited; its answer is OPTIMAL or INFEASIBLE -- a proof inside the model -- or says FEASIBLE at
the time limit.

GEN (pcbnew, tools/kpy) lists, for every net of the spec:
  * its ENDS: where its link starts -- the net's pad centre ("pad": "REF.NUM") or one polyline stub per
    alternative ("ends": [{"pts": [[x, y], ...], "rel": false, "width": W, "layer": L}]; "rel" makes the
    points offsets from the pad).  A stub that fails the fixed copper at "stub_clearance" is dropped.
  * every via SITE on the grid anchored at the end (x = ex + i*grid, y = ey + j*grid) inside "region",
    no further than "link.max_lateral" across the link axis, with the SHORTEST legal link: "straight"
    (along "link.axis" only) or "straight45" (straight plus one 45-degree run, either order);
  * what each candidate BLOCKS: the (ball, lane) pairs of "exit_lanes" (exitlane.py's own judge and
    rule: lane width/2 + the stricter clearance), stub, link and via together;
  * GND stitch candidates ("gnd"): sites on gnd.grid in gnd.box inside a filled pour of gnd.net on
    gnd.layers ("inner" = every copper layer but the outer two), each covering the candidates within
    gnd.reach that have no gnd.net via within reach already;
  * every PAIRWISE CONFLICT among the new items (vias c-c, via to track, track to track on a layer).
Fixed copper (read in "box" + 0.6 mm): tracks (arcs as two chords), vias (every layer), pads (circle or
bounding box, per copper layer), net-tied copper polygons (outline + inside), the spec's "tracks"
(copper planned but not on the board).  Pours are not obstacles (they refill).
CLEARANCE: "clearance" in the spec is a flat number (a model term, as an older count used); absent, the
larger of the two nets' class clearances and Board Setup's minimum clearance -- RBoard's classes, as
route.py reads them.  Every gap is tested at clearance + "margin".  Two new vias stand at least
r1 + r2 + clearance apart (same net too) and drill/2 + drill/2 + Board Setup's hole-to-hole; a new via's
drill keeps Board Setup's hole clearance to other copper.  Not modelled: a custom .kicad_dru rule other
than the exit-lane rule, copper-to-edge, pad-local overrides.  Layer names are the board's own.
Via size: "via" [dia, drill], else the first net's class via.  Link width: "link.width", else the class's.
WHAT-IF TERMS (each named in the spec, chosen on the command line, so a table row is a set of terms):
  --pull NAME:D   "pulls": {NAME: {"net", "layer", "verts": [x0, y0, x1, y1], "dir": [dx, dy]}} -- that
                  net-tied polygon's vertices inside verts moved D mm along dir; the polygon's narrowest
                  depth along dir over the moved span is reported (neck) before and after.
  --drop NAME     "drops": {NAME: {"net", "layer", "box"}} -- that net's tracks on that layer with both
                  ends in box taken out (in memory).
  --line NAME     "lines": {NAME: {"net", "layer", "dir": "v"|"h", "at": [lo, hi], "span": [a, b],
                  "grid", "width"}} -- a straight line of that net whose position is a VARIABLE (one of
                  the legal positions on the grid; the solver picks it with the sites).

SOLVE (CP-SAT, tools/.venv): at most one site a net; no two conflicting items; the EXIT GATE of the
lanes (exitlane.evaluate's: assign -- every ring-1 ball's straight lane free and every ring-2 ball
matched to a free lane of its own; plan -- assign and each ring-2 ball's planned lane free; strict --
no lane shut), except the balls --free names (or --freeany chooses, each a cost); GND (--gnd new: a
site with no GND via within reach needs a chosen new stitch within reach; old: only the vias there; off:
ignored).  Objective: nets sited, then fewest freed balls, then fewest stitches, then link length.
Prints OPTIMAL / INFEASIBLE / FEASIBLE-at-limit, the nets sited and failing, the freed balls, and writes
the sites (via, link, stitch, line position, matching) as JSON.

TABLE: ROWS.json = [{"label", "gen": [gen terms], "solve": [solve terms], "cost": "..."}]; each distinct
gen is run once (par.cmap, kpy), each row solved in parallel, and DIR/table.tsv written:
terms | sited/N | failing nets | cost | status | solve file.  RUN: one row, the candidate file cached in
DIR by a hash of the spec, the board file and the gen terms.

PROOF: tools/route/test_sitesolve.py -- a synthetic board whose count is known by hand (k of N, one more
with a polygon pulled, INFEASIBLE when N > slots) and, given board1's scratch copy, the identity with the
r10c count it replaces.
"""
import collections
import gzip
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
KPY = TOOLS / "kpy"
VPY = TOOLS / ".venv" / "bin" / "python"
CELL = 0.5
GATES = ("assign", "plan", "strict")


def die(msg, code=2):
    print(f"sitesolve: {msg}", file=sys.stderr)
    sys.exit(code)


def say(*a):
    print(*a, flush=True)


def leaf(n):
    return (n or "").split("/")[-1]


def load_json(p):
    p = str(p)
    with (gzip.open(p, "rt") if p.endswith(".gz") else open(p)) as f:
        return json.load(f)


def dump_json(obj, p):
    p = str(p)
    with (gzip.open(p, "wt") if p.endswith(".gz") else open(p, "w")) as f:
        json.dump(obj, f)


def opt(a, name, default=None, many=False):
    """--name VALUE (many: every occurrence)."""
    out = [a[i + 1] for i, x in enumerate(a[:-1]) if x == name]
    if many:
        return out
    return out[-1] if out else default


# ---------------------------------------------------------------------------------------- geometry
def psd(px, py, A, B):
    (ax, ay), (bx, by) = A, B
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def ssd(A, B, C, D):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2, d3, d4 = cross(C, D, A), cross(C, D, B), cross(A, B, C), cross(A, B, D)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(psd(*A, C, D), psd(*B, C, D), psd(*C, A, B), psd(*D, A, B))


def rect_pt(r, x, y):
    return math.hypot(max(r[0] - x, 0, x - r[2]), max(r[1] - y, 0, y - r[3]))


def rect_seg(r, A, B):
    if rect_pt(r, *A) == 0 or rect_pt(r, *B) == 0:
        return 0.0
    cs = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    return min(ssd(A, B, cs[i], cs[(i + 1) % 4]) for i in range(4))


def in_poly(x, y, pts):
    c = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def runs_across(pts, c, vertical):
    """The polygon's spans on the line x = c (vertical) or y = c."""
    out = []
    for p, q in zip(pts, pts[1:] + pts[:1]):
        (u1, v1), (u2, v2) = (p, q) if vertical else ((p[1], p[0]), (q[1], q[0]))
        if (u1 > c) != (u2 > c):
            out.append(v1 + (c - u1) * (v2 - v1) / (u2 - u1))
    out.sort()
    return list(zip(out[0::2], out[1::2]))


class Idx:
    def __init__(s):
        s.c = collections.defaultdict(list)

    @staticmethod
    def _cells(bb):
        for i in range(int(math.floor(bb[0] / CELL)), int(math.floor(bb[2] / CELL)) + 1):
            for j in range(int(math.floor(bb[1] / CELL)), int(math.floor(bb[3] / CELL)) + 1):
                yield (i, j)

    def add(s, bb, it):
        for k in s._cells(bb):
            s.c[k].append(it)

    def q(s, bb):
        seen, out = set(), []
        for k in s._cells(bb):
            for it in s.c.get(k, ()):
                if id(it) not in seen:
                    seen.add(id(it))
                    out.append(it)
        return out


# ---------------------------------------------------------------------------------------- gen
def gen(a):
    import pcbnew
    sys.path.insert(0, str(HERE))
    import route as RT
    import exitlane as EL
    T0 = time.time()
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    if len(pos) != 2 or not opt(a, "--board"):
        die("gen --board COPY SPEC.json OUT.json [--pull NAME:D] [--drop NAME] [--line NAME] [--gate G]")
    spec_p, out_p = pos
    S = load_json(spec_p)
    bdir = Path(opt(a, "--board")).expanduser().resolve()
    pulls = {}
    for t in opt(a, "--pull", many=True):
        k, _, d = t.rpartition(":")
        if k not in S.get("pulls", {}):
            die(f"--pull {t}: no pull named {k!r} in the spec ({', '.join(S.get('pulls', {})) or 'none'})")
        pulls[k] = float(d)
    drops = opt(a, "--drop", many=True)
    lines = opt(a, "--line", many=True)
    for k in drops:
        if k not in S.get("drops", {}):
            die(f"--drop {k}: not in the spec's drops")
    for k in lines:
        if k not in S.get("lines", {}):
            die(f"--line {k}: not in the spec's lines")
    gate_o = opt(a, "--gate")
    if gate_o and gate_o not in GATES:
        die(f"--gate takes {', '.join(GATES)}")

    RB = RT.RBoard(bdir)
    b, M = RB.b, pcbnew.ToMM
    NETS = list(S["nets"])
    FULL = {}
    for n in b.GetNetsByName().keys():
        FULL.setdefault(leaf(str(n)), str(n))
    for n in NETS:
        if n not in FULL:
            die(f"net {n} is not on the board")
    LINK = S.get("link", {})
    LL = LINK.get("layer", "F.Cu")
    AX = LINK.get("axis", "y")
    STYLE = LINK.get("style", "straight45")
    DXW = float(LINK.get("max_lateral", 0.0))
    MAXLEN = LINK.get("max_len")
    GRID = float(S.get("grid", 0.025))
    REG = S["region"]
    BOX = S.get("box") or [REG[0] - 3, REG[1] - 3, REG[2] + 3, REG[3] + 3]
    MARG = float(S.get("margin", 0.0))
    FLAT = S.get("clearance")
    MINCLR = RB.clearance or 0.0
    H2H = RB.hole_to_hole or 0.0
    HCLR = M(b.GetDesignSettings().m_HoleClearance)

    cc = {}

    def cls_clr(n):
        if n not in cc:
            try:
                cc[n] = float(RB.class_clearance(FULL.get(n, n)) or 0.0) if n else 0.0
            except SystemExit:
                cc[n] = 0.0
        return cc[n]

    def clr(n, m):
        if FLAT is not None:
            return float(FLAT)
        return max(cls_clr(n), cls_clr(m), MINCLR)

    def OK(n, m):
        return clr(n, m) + MARG

    def cls_num(n, key, dflt):
        try:
            v = RB._class(FULL.get(n, n), key)
        except SystemExit:
            v = None
        return float(v) if v is not None else dflt

    VIA = S.get("via") or [cls_num(NETS[0], "via_diameter", 0.4), cls_num(NETS[0], "via_drill", 0.2)]
    VR, VD = VIA[0] / 2, VIA[1]
    LW = float(LINK.get("width") or cls_num(NETS[0], "track_width", 0.1))

    def vsep(n, m):
        return max(2 * VR + clr(n, m), VD + H2H)

    def inbox(x, y, g=0.6):
        return BOX[0] - g <= x <= BOX[2] + g and BOX[1] - g <= y <= BOX[3] + g

    # ------------------------------------------------------------ fixed copper
    FX = Idx()

    def addseg(n, l, P, Q, hw):
        FX.add((min(P[0], Q[0]) - hw, min(P[1], Q[1]) - hw, max(P[0], Q[0]) + hw, max(P[1], Q[1]) + hw),
               ("s", n, l, tuple(P), tuple(Q), hw))

    def addcirc(n, l, x, y, r, drill):
        FX.add((x - r, y - r, x + r, y + r), ("c", n, l, x, y, r, drill))

    gnd_net = (S.get("gnd") or {}).get("net", "GND")
    gnd_vias, dropped = [], []
    for t in b.GetTracks():
        n = leaf(t.GetNetname())
        if t.GetClass() == "PCB_VIA":
            x, y = M(t.GetPosition().x), M(t.GetPosition().y)
            if inbox(x, y):
                addcirc(n, None, x, y, M(t.GetWidth(pcbnew.F_Cu)) / 2, M(t.GetDrillValue()))
                if n == gnd_net:
                    gnd_vias.append((x, y))
            continue
        l = b.GetLayerName(t.GetLayer())
        P, Q = (M(t.GetStart().x), M(t.GetStart().y)), (M(t.GetEnd().x), M(t.GetEnd().y))
        pts = [P, Q]
        if t.GetClass() == "PCB_ARC":
            m = t.GetMid()
            pts = [P, (M(m.x), M(m.y)), Q]
        if not any(inbox(*p) for p in pts):
            continue
        hit = None
        for k in drops:
            d = S["drops"][k]
            bx = d["box"]
            if n == d["net"] and l == d["layer"] and all(bx[0] <= p[0] <= bx[2] and bx[1] <= p[1] <= bx[3] for p in (P, Q)):
                hit = k
        if hit:
            dropped.append(dict(drop=hit, net=n, layer=l, a=[round(v, 4) for v in P], b=[round(v, 4) for v in Q]))
            continue
        for p, q in zip(pts, pts[1:]):
            addseg(n, l, p, q, M(t.GetWidth()) / 2)
    for k in drops:
        got = [d for d in dropped if d["drop"] == k]
        say(f"--drop {k}: {len(got)} track(s) out: " + "; ".join(f"{d['layer']} {d['a']}-{d['b']}" for d in got))
        if not got:
            die(f"--drop {k}: no track of {S['drops'][k]['net']} on {S['drops'][k]['layer']} inside its box")
    pads = {}
    for f in b.GetFootprints():
        for p in f.Pads():
            x, y = M(p.GetPosition().x), M(p.GetPosition().y)
            pads[f"{f.GetReference()}.{p.GetNumber()}"] = (x, y, leaf(p.GetNetname()))
            if not inbox(x, y):
                continue
            n = leaf(p.GetNetname())
            bb = p.GetBoundingBox()
            r = [M(bb.GetX()), M(bb.GetY()), M(bb.GetRight()), M(bb.GetBottom())]
            for lid in p.GetLayerSet().CuStack():
                l = b.GetLayerName(lid)
                if p.GetShape() == pcbnew.PAD_SHAPE_CIRCLE:
                    addcirc(n, l, x, y, (r[2] - r[0]) / 2, 0.0)
                else:
                    FX.add(tuple(r), ("r", n, l, r))
    for t in S.get("tracks", []):
        for P, Q in zip(t["pts"], t["pts"][1:]):
            addseg(leaf(t["net"]), t["layer"], tuple(P), tuple(Q), t.get("width", LW) / 2)
    POLYS, NECK = [], {}
    for d in b.GetDrawings():
        if d.GetClass() != "PCB_SHAPE" or d.GetNetCode() <= 0 or not d.IsOnCopperLayer() \
                or d.GetShape() != pcbnew.SHAPE_T_POLY:
            continue
        o = d.GetPolyShape().Outline(0)
        pts = [(M(o.CPoint(i).x), M(o.CPoint(i).y)) for i in range(o.PointCount())]
        if not any(inbox(*q, 1.0) for q in pts):
            continue
        n, l = leaf(d.GetNetname()), b.GetLayerName(d.GetLayer())
        for k, D in pulls.items():
            pl = S["pulls"][k]
            if pl["net"] != n or pl["layer"] != l:
                continue
            vb, dv = pl["verts"], pl["dir"]
            mv = [i for i, (x, y) in enumerate(pts) if vb[0] <= x <= vb[2] and vb[1] <= y <= vb[3]]
            if not mv:
                continue
            vertical = abs(dv[1]) >= abs(dv[0])
            us = [pts[i][0 if vertical else 1] for i in mv]
            u0, u1 = min(us), max(us)

            def neck(pp):
                best, st = (9e9, None), 0.005
                nst = int(round((u1 - u0) / st))
                for kk in range(nst + 1):
                    c = u0 + st * kk + (st / 2 if kk == 0 else (-st / 2 if kk == nst else 0))
                    rr = runs_across(pp, c, vertical)
                    if rr:
                        lo, hi = max(rr, key=lambda r: r[1] - r[0])
                        best = min(best, (round(hi - lo, 3), round(c, 3)))
                return best
            before = neck(pts)
            nrm = math.hypot(*dv)
            pts = [(x + D * dv[0] / nrm, y + D * dv[1] / nrm) if i in mv else (x, y) for i, (x, y) in enumerate(pts)]
            after = neck(pts)
            NECK[k] = dict(net=n, layer=l, d=D, moved=len(mv), span=[round(u0, 3), round(u1, 3)],
                           neck_before=before, neck_after=after)
            say(f"--pull {k}:{D}: {len(mv)} vertices of {n} {l} moved; narrowest depth over them "
                f"{before[0]} -> {after[0]} mm")
        POLYS.append((n, l, pts))
        for P, Q in zip(pts, pts[1:] + pts[:1]):
            addseg(n, l, P, Q, M(d.GetWidth()) / 2)
    for k in pulls:
        if k not in NECK:
            die(f"--pull {k}: no polygon of {S['pulls'][k]['net']} on {S['pulls'][k]['layer']} has a vertex in verts")

    def via_ok(net, x, y):
        """(least margin over the required gap to fixed copper, ok-by-via-spacing)."""
        g, vok = 9.0, True
        for it in FX.q((x - 1.0, y - 1.0, x + 1.0, y + 1.0)):
            if it[1] == net:
                continue
            k = OK(net, it[1])
            if it[0] == "c":
                d = math.hypot(it[3] - x, it[4] - y) - it[5]
                if it[6] and d + it[5] < it[6] / 2 + VD / 2 + H2H:
                    vok = False
            elif it[0] == "s":
                d = psd(x, y, it[3], it[4]) - it[5]
            else:
                d = rect_pt(it[3], x, y)
            g = min(g, d - VR - k)
            if d < VD / 2 + HCLR:               # the drill to other copper (Board Setup's hole clearance)
                vok = False
        for n, l, pts in POLYS:
            if n != net and in_poly(x, y, pts):
                g = -1.0
        return g, vok

    def link_ok(net, pts, hw, layer, extra=0.0):
        """least margin over the required gap of a polyline on a layer (extra: added to every gap)."""
        g = 9.0
        for n, l, pp in POLYS:
            if n != net and l == layer and any(in_poly(*q, pp) for q in pts):
                return -1.0
        for A, B in zip(pts, pts[1:]):
            bb = (min(A[0], B[0]) - 0.8, min(A[1], B[1]) - 0.8, max(A[0], B[0]) + 0.8, max(A[1], B[1]) + 0.8)
            for it in FX.q(bb):
                if it[1] == net:
                    continue
                k = OK(net, it[1]) - extra
                if it[0] == "c":
                    if it[2] is None or it[2] == layer:
                        g = min(g, psd(it[3], it[4], A, B) - it[5] - hw - k)
                elif it[0] == "s":
                    if it[2] == layer:
                        g = min(g, ssd(A, B, it[3], it[4]) - it[5] - hw - k)
                elif it[2] == layer:
                    g = min(g, rect_seg(it[3], A, B) - hw - k)
            if g < 0:
                return g
        return g

    # ------------------------------------------------------------ the exit lanes (exitlane.py's judge)
    LANES, balls, pre, R, LANE_LAYER = [], collections.OrderedDict(), [], None, None
    XL = S.get("exit_lanes")
    if XL:
        ents = [x for x in (EL.load_spec(RB.dir) or []) if x["ref"] == XL["ref"]]
        e = dict(ents[0]) if ents else {"ref": XL["ref"]}
        if gate_o:
            e["gate"] = gate_o
        R = EL.judge_entry(RB, e)
        LANE_LAYER = R.get("layer") or LL
        excl = set(NETS) | set(XL.get("exclude_nets", []))
        xr, yr = XL.get("x") or [-1e9, 1e9], XL.get("y") or [-1e9, 1e9]
        sides = XL.get("side")
        sides = [sides] if isinstance(sides, str) else sides
        LANES = [L for L in R["lanes"] if (not sides or L["side"] in sides)
                 and xr[0] <= L["seg"][0] <= xr[1] and yr[0] <= L["seg"][1] <= yr[1]
                 and L["ball"] not in R["routed"] and leaf(L["net"]) not in excl]
        for L in LANES:
            v = balls.setdefault(L["ball"], dict(net=leaf(L["net"]), ring=L["ring"], keys=[], plan_keys=[],
                                                 x=R["balls"][L["ball"]]["x"]))
            k = list(L["key"])
            if k not in v["keys"]:
                v["keys"].append(k)
            if L.get("tag") == "plan" and k not in v["plan_keys"]:
                v["plan_keys"].append(k)
        pre = sorted({(bb, tuple(k)) for bb, k in R["blocked"] if bb in balls})
        say(f"exit gate {R['gate']} ({XL['ref']}): {len(LANES)} lanes of {len(balls)} balls outside the nets, "
            f"{len(pre)} shut by the board already; rule {R['rule'].source}")
    NEED = {}

    def need(L, net):
        k = (L["net"], net)
        if k not in NEED:
            NEED[k] = R["rule"].width(L["net"]) / 2 + R["rule"].clearance(L["net"], FULL.get(net, net))
        return NEED[k]

    def blocks(net, via, segs):
        """(ball, key) pairs shut: via = (x, y) or None, segs = [(A, B, hw, layer)]."""
        out = set()
        for L in LANES:
            s = L["seg"]
            nd = need(L, net) - 1e-4
            if via and EL.pt_seg(via[0], via[1], s) - VR < nd:
                out.add((L["ball"], tuple(L["key"])))
                continue
            for A, B, hw, lay in segs:
                if lay == LANE_LAYER and EL.seg_seg(s, (A[0], A[1], B[0], B[1])) - hw < nd:
                    out.add((L["ball"], tuple(L["key"])))
                    break
        return sorted(out)

    # ------------------------------------------------------------ ends
    ENDS = {}
    SCLR = S.get("stub_clearance")
    for n in NETS:
        d = S["nets"][n] or {}
        org = None
        if d.get("pad"):
            if d["pad"] not in pads:
                die(f"{n}: pad {d['pad']} is not on the board")
            org = pads[d["pad"]][:2]
            if pads[d["pad"]][2] != n:
                die(f"{n}: pad {d['pad']} is on net {pads[d['pad']][2]}")
        es = d.get("ends") or [{"pts": [[0, 0]], "rel": True}]
        ENDS[n] = []
        for E in es:
            if E.get("rel") and org is None:
                die(f"{n}: a relative end needs \"pad\"")
            P = [((org[0] + p[0], org[1] + p[1]) if E.get("rel") else (p[0], p[1])) for p in E["pts"]]
            P = [(round(x, 4), round(y, 4)) for x, y in P]
            hw, lay = E.get("width", LW) / 2, E.get("layer", LL)
            segs = [(p, q, hw, lay) for p, q in zip(P, P[1:])]
            ok = True
            if len(P) > 1:
                extra = (clr(n, n) - float(SCLR)) if SCLR is not None and FLAT is not None else 0.0
                if SCLR is not None and FLAT is None:
                    extra = max(0.0, max(MINCLR, cls_clr(n)) - float(SCLR))
                ok = link_ok(n, P, hw, lay, extra) >= 0
            ENDS[n].append(dict(x=P[-1][0], y=P[-1][1], pts=[list(p) for p in P], segs=segs, ok=ok,
                                blocks=blocks(n, None, segs)))
        say(f"{n}: ends " + ", ".join(f"({E['x']}, {E['y']}){'' if E['ok'] else ' FAILS'}"
                                      f"{' blocks ' + str(len(E['blocks'])) if E['blocks'] else ''}" for E in ENDS[n]))

    def shapes(ex, ey, x, y):
        u, v, ue, ve = (x, y, ex, ey) if AX == "y" else (y, x, ey, ex)       # v runs along the axis
        du, dv = abs(u - ue), abs(v - ve)
        sv = 1 if v > ve else -1
        out = []
        if du < 1e-9:
            if dv > 1e-9:
                out.append([(ue, ve), (u, v)])
        elif STYLE == "straight45" and du <= DXW + 1e-9:
            if dv >= du - 1e-9:
                out.append([(ue, ve), (ue, v - sv * du), (u, v)] if dv - du > 1e-9 else [(ue, ve), (u, v)])
            if dv > du + 1e-9:
                out.append([(ue, ve), (u, ve + sv * du), (u, v)])
        if AX != "y":
            out = [[(q[1], q[0]) for q in p] for p in out]
        return out

    def span(lo, hi, o, g):
        """grid points o + k*g within [lo, hi]"""
        k0, k1 = math.ceil((lo - o) / g - 1e-9), math.floor((hi - o) / g + 1e-9)
        return [round(o + g * k, 4) for k in range(k0, k1 + 1)]

    cands = []
    for n in NETS:
        nc = 0
        for ei, E in enumerate(ENDS[n]):
            if not E["ok"]:
                continue
            ex, ey = E["x"], E["y"]
            lat = DXW if STYLE == "straight45" else 0.0
            if AX == "y":
                xs = span(max(REG[0], ex - lat), min(REG[2], ex + lat), ex, GRID)
                ys = span(REG[1], REG[3], ey, GRID)
            else:
                xs = span(REG[0], REG[2], ex, GRID)
                ys = span(max(REG[1], ey - lat), min(REG[3], ey + lat), ey, GRID)
            best = {}
            for x in xs:
                for y in ys:
                    shp = shapes(ex, ey, x, y)
                    if not shp:
                        continue
                    g, vok = via_ok(n, x, y)
                    if g < 0 or not vok:
                        continue
                    for pts in shp:
                        Ln = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))
                        if MAXLEN is not None and Ln > MAXLEN + 1e-9:
                            continue
                        if (x, y) in best and best[(x, y)][0] <= Ln:
                            continue
                        if link_ok(n, pts, LW / 2, LL) < 0:
                            continue
                        best[(x, y)] = (Ln, pts)
            for (x, y), (Ln, pts) in best.items():
                lsegs = [(p, q, LW / 2, LL) for p, q in zip(pts, pts[1:])]
                bl = sorted(set(E["blocks"]) | set(blocks(n, (x, y), lsegs)))
                ng = min([math.hypot(gx - x, gy - y) for gx, gy in gnd_vias] or [9e9])
                cands.append(dict(id=len(cands), net=n, end=ei, ex=ex, via=[x, y], link=[list(p) for p in pts],
                                  len=round(Ln, 4), segs=[[list(p), list(q), hw, l] for p, q, hw, l in E["segs"] + lsegs],
                                  blocks=[[bb, list(k)] for bb, k in bl], gnd=round(ng, 4)))
                nc += 1
        say(f"{n}: {nc} candidate sites ({sum(1 for c in cands if c['net'] == n and not c['blocks'])} shut no lane)"
            f"  t={time.time() - T0:.0f}s")

    # ------------------------------------------------------------ GND stitches
    G = S.get("gnd")
    stitches, GR = [], None
    if G:
        GR = float(G.get("reach", 1.4))
        zs = [z for z in b.Zones() if not z.GetIsRuleArea()]
        pcbnew.ZONE_FILLER(b).Fill(zs)
        cu = [b.GetLayerName(l) for l in b.GetEnabledLayers().CuStack()]
        want = cu[1:-1] if G.get("layers", "inner") == "inner" else list(G["layers"])
        gz = [(z, l) for z in zs if leaf(z.GetNetname()) == G.get("net", "GND") for l in z.GetLayerSet().CuStack()
              if b.GetLayerName(l) in want]

        def in_pour(x, y):
            v = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
            return sorted({b.GetLayerName(l) for z, l in gz if z.HitTestFilledArea(l, v)})
        needy = [c for c in cands if c["gnd"] > GR]
        CI = Idx()
        for c in needy:
            CI.add((c["via"][0] - GR, c["via"][1] - GR, c["via"][0] + GR, c["via"][1] + GR), c)
        gb, gg = G.get("box") or BOX, float(G.get("grid", 0.05))
        for x in span(gb[0], gb[2], gb[0], gg):
            for y in span(gb[1], gb[3], gb[1], gg):
                cov = [c["id"] for c in CI.q((x, y, x, y)) if math.hypot(c["via"][0] - x, c["via"][1] - y) <= GR]
                if not cov:
                    continue
                g, vok = via_ok(G.get("net", "GND"), x, y)
                if g < 0 or not vok:
                    continue
                pl = in_pour(x, y)
                if not pl:
                    continue
                stitches.append(dict(id=len(cands) + len(stitches), net=G.get("net", "GND"), via=[x, y], cov=cov,
                                     pour=pl, segs=[], blocks=[[bb, list(k)] for bb, k in blocks(G.get("net", "GND"), (x, y), [])]))
        say(f"{len(stitches)} {G.get('net', 'GND')} stitch candidates for {len(needy)} sites with no "
            f"{G.get('net', 'GND')} via within {GR}  t={time.time() - T0:.0f}s")

    # ------------------------------------------------------------ movable lines
    lopts = []
    for k in lines:
        Ld = S["lines"][k]
        lw = float(Ld.get("width", LW))
        got = 0
        for c in span(Ld["at"][0], Ld["at"][1], Ld["at"][0], float(Ld.get("grid", GRID))):
            pts = [(c, Ld["span"][0]), (c, Ld["span"][1])] if Ld.get("dir", "v") == "v" else \
                  [(Ld["span"][0], c), (Ld["span"][1], c)]
            if link_ok(Ld["net"], pts, lw / 2, Ld["layer"]) < 0:
                continue
            sg = [(pts[0], pts[1], lw / 2, Ld["layer"])]
            lopts.append(dict(id=len(cands) + len(stitches) + len(lopts), line=k, net=Ld["net"], at=c,
                              segs=[[list(p), list(q), hw, l] for p, q, hw, l in sg],
                              blocks=[[bb, list(kk)] for bb, kk in blocks(Ld["net"], None, sg)]))
            got += 1
        say(f"--line {k}: {got} legal positions: " + ",".join(str(o["at"]) for o in lopts if o["line"] == k))
        if not got:
            die(f"--line {k}: no legal position for {Ld['net']} on {Ld['layer']} in {Ld['at']}", 3)

    # ------------------------------------------------------------ pairwise conflicts
    items = cands + stitches + lopts
    OKMAX = max([OK(p, q) for p in NETS + [gnd_net] for q in NETS + [gnd_net]] or [0.2])
    D = 2 * VR + OKMAX + 0.01

    def bbox_of(it):
        pts = [tuple(it["via"])] if "via" in it else []
        for s in it["segs"]:
            pts += [tuple(s[0]), tuple(s[1])]
        return (min(p[0] for p in pts) - VR, min(p[1] for p in pts) - VR,
                max(p[0] for p in pts) + VR, max(p[1] for p in pts) + VR)

    PI = Idx()
    for it in items:
        it["_bb"] = bbox_of(it)
        PI.add(it["_bb"], it)

    def conflict(p, q):
        same = p["net"] == q["net"]
        if same and (("line" in p and "line" in q) or ("end" in p and "end" in q)):
            return False                       # one of them at most, by the model
        a1, a2 = p["_bb"], q["_bb"]
        k = OK(p["net"], q["net"])
        if max(a1[0] - a2[2], a2[0] - a1[2], a1[1] - a2[3], a2[1] - a1[3]) > k + 0.01:
            return False
        if "via" in p and "via" in q and math.hypot(p["via"][0] - q["via"][0], p["via"][1] - q["via"][1]) < vsep(p["net"], q["net"]):
            return True
        if same:
            return False
        for u, v in ((p, q), (q, p)):
            if "via" in u:
                for A, B, hw, _l in v["segs"]:
                    if psd(u["via"][0], u["via"][1], A, B) - VR - hw < k:
                        return True
        for A, B, h1, l1 in p["segs"]:
            for C, Dd, h2, l2 in q["segs"]:
                if l1 == l2 and ssd(A, B, C, Dd) - h1 - h2 < k:
                    return True
        return False

    conf = []
    for it in items:
        bb = it["_bb"]
        for jt in PI.q((bb[0] - D, bb[1] - D, bb[2] + D, bb[3] + D)):
            if jt["id"] > it["id"] and conflict(it, jt):
                conf.append([it["id"], jt["id"]])
    for it in items:
        it.pop("_bb", None)
    say(f"{len(conf)} conflicting pairs among {len(items)} items  t={time.time() - T0:.0f}s")
    pcb = Path(RB.pcb)
    out = dict(tool="sitesolve", args=a, spec=str(spec_p), board=str(bdir),
               board_sha1=hashlib.sha1(pcb.read_bytes()).hexdigest()[:12],
               rules=dict(clearance=FLAT if FLAT is not None else "classes", margin=MARG, via=VIA, link_width=LW,
                          min_clearance=MINCLR, hole_to_hole=H2H, hole_clearance=HCLR),
               nets=NETS, ends={n: [dict(x=E["x"], y=E["y"], pts=E["pts"], ok=E["ok"], blocks=[[bb, list(k)] for bb, k in E["blocks"]])
                                    for E in ENDS[n]] for n in NETS},
               gate=R["gate"] if R else None, balls=balls, preblocked=[[bb, list(k)] for bb, k in pre],
               gnd_net=gnd_net if G else None, gnd_vias=gnd_vias, gr=GR, neck=NECK, dropped=dropped,
               cands=cands, stitches=stitches, lines=lopts, conflicts=conf)
    dump_json(out, out_p)
    say(f"-> {out_p}  t={time.time() - T0:.0f}s")


# ---------------------------------------------------------------------------------------- solve
def solve(a):
    from ortools.sat.python import cp_model
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--")
                                                                   or a[i - 1] in ("--freeany",))]
    if len(pos) != 2:
        die("solve CANDS.json OUT.json [--free B,..] [--freeany] [--gnd new|old|off] [--gate G] [--need N] "
            "[--atmost N] [--time S] [--workers N]")
    T0 = time.time()
    D = load_json(pos[0])
    FREE = set(x for x in (opt(a, "--free") or "").split(",") if x)
    FREEANY = "--freeany" in a
    GND = opt(a, "--gnd", "new")
    if GND not in ("new", "old", "off"):
        die("--gnd takes new, old or off")
    GATE = opt(a, "--gate") or D.get("gate") or "assign"
    if GATE not in GATES:
        die(f"--gate takes {', '.join(GATES)}")
    NEED = opt(a, "--need")
    ATMOST = opt(a, "--atmost")
    TL = float(opt(a, "--time", 600))
    m = cp_model.CpModel()
    ZERO = m.NewConstant(0)
    C, ST, LO = D["cands"], D["stitches"], D["lines"]
    GR = D.get("gr")
    if GND != "off" and GR is None:
        GND = "off"
    x = {c["id"]: m.NewBoolVar(f"c{c['id']}") for c in C}
    s = {t["id"]: m.NewBoolVar(f"s{t['id']}") for t in ST} if GND == "new" else {}
    f = {t["id"]: m.NewBoolVar(f"l{t['id']}") for t in LO}
    byline = collections.defaultdict(list)
    for t in LO:
        byline[t["line"]].append(f[t["id"]])
    for ys in byline.values():
        m.AddExactlyOne(ys)
    var = {**x, **s, **f}
    bynet = collections.defaultdict(list)
    for c in C:
        bynet[c["net"]].append(c)
    sited = {}
    for n in D["nets"]:
        sited[n] = m.NewBoolVar(f"n_{n}")
        m.Add(sum(x[c["id"]] for c in bynet[n]) == sited[n])
    for i, j in D["conflicts"]:
        if i in var and j in var:
            m.AddBoolOr([var[i].Not(), var[j].Not()])
    if GND != "off":
        cov = collections.defaultdict(list)
        for t in ST:
            for cid in t["cov"]:
                cov[cid].append(t["id"])
        for c in C:
            if c["gnd"] <= GR:
                continue
            if GND == "old":
                m.Add(x[c["id"]] == 0)
            else:
                m.AddBoolOr([x[c["id"]].Not()] + [s[t] for t in cov[c["id"]]])
    # the exit gate
    balls = D["balls"]
    fv = {}
    for bl in balls:
        fv[bl] = True if bl in FREE else (m.NewBoolVar(f"free_{bl}") if FREEANY else False)
    blk = collections.defaultdict(list)
    for it in C + ST + LO:
        for bb, k in it.get("blocks", []):
            if bb in balls:
                blk[(bb, tuple(k))].append(it["id"])
    for bb, k in D["preblocked"]:
        blk[(bb, tuple(k))].append("pre")

    def must_be_free(bl, k):
        """lane k of ball bl free unless the ball is freed."""
        for i in blk[(bl, k)]:
            if i == "pre":
                if fv[bl] is False:
                    m.Add(ZERO == 1)          # shut by the board and not freed: infeasible
                else:
                    m.Add(fv[bl] == 1)
                continue
            if i not in var:
                continue
            if fv[bl] is False:
                m.Add(var[i] == 0)
            else:
                m.AddImplication(var[i], fv[bl])

    mv, keyuse = {}, collections.defaultdict(list)
    for bl, v in balls.items():
        if fv[bl] is True:
            continue
        keys = [tuple(k) for k in v["keys"]]
        if v["ring"] == 1 or GATE == "strict":
            for k in keys:
                must_be_free(bl, k)
            continue
        if GATE == "plan":
            for k in map(tuple, v.get("plan_keys", [])):
                must_be_free(bl, k)
        opts = []
        for k in keys:
            y = m.NewBoolVar(f"m_{bl}_{k}")
            mv[(bl, k)] = y
            opts.append(y)
            keyuse[k].append(y)
            for i in blk[(bl, k)]:
                if i == "pre":
                    m.Add(y == 0)
                elif i in var:
                    m.AddBoolOr([y.Not(), var[i].Not()])
        if fv[bl] is False:
            m.AddExactlyOne(opts)
        else:
            m.Add(sum(opts) + fv[bl] >= 1)
            m.Add(sum(opts) <= 1)
    for k, ys in keyuse.items():
        m.AddAtMostOne(ys)
    nf = [fv[bl] for bl in balls if not isinstance(fv[bl], bool)]
    LEN = sum(int(round(c["len"] * 100)) * x[c["id"]] for c in C)
    if NEED is not None:
        m.Add(sum(sited.values()) >= int(NEED))
    if ATMOST is not None:
        m.Add(sum(sited.values()) <= int(ATMOST))
    m.Maximize(sum(sited.values()) * 10**7 - sum(nf) * 10**5 - sum(s.values()) * 10**3 - LEN)
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = TL
    sv.parameters.num_workers = int(opt(a, "--workers", 2))
    st = sv.Solve(m)
    name = sv.StatusName(st)
    status = "FEASIBLE-at-limit" if st == cp_model.FEASIBLE else name
    res = dict(tool="sitesolve", status=status, cands=str(pos[0]), terms=a[2:], free=sorted(FREE), freeany=FREEANY,
               gnd=GND, gate=GATE, need=NEED, t=round(time.time() - T0, 1), N=len(D["nets"]),
               bound_nets=math.ceil(sv.BestObjectiveBound() / 10**7 - 1e-6) if st in (cp_model.OPTIMAL, cp_model.FEASIBLE) else None)
    if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        nets = {}
        for c in C:
            if sv.Value(x[c["id"]]):
                nets[c["net"]] = dict(via=c["via"], link=c["link"], end_x=c["ex"], len=c["len"], gnd_mm=c["gnd"],
                                      blocks=c["blocks"])
        res.update(sited=len(nets), failed=[n for n in D["nets"] if n not in nets], nets=nets,
                   gnd_stitch=[dict(via=t["via"], pour=t["pour"]) for t in ST if t["id"] in s and sv.Value(s[t["id"]])],
                   freed=sorted(bl for bl in balls if fv[bl] is True or (not isinstance(fv[bl], bool) and sv.Value(fv[bl]))),
                   lines={t["line"]: t["at"] for t in LO if sv.Value(f[t["id"]])},
                   match={bl: list(k) for (bl, k), y in mv.items() if sv.Value(y)},
                   link_mm=round(sum(v["len"] for v in nets.values()), 3))
    else:
        res.update(sited=None, failed=None)
    with open(pos[1], "w") as fo:
        json.dump(res, fo, indent=1)
    say(f"{status} sited {res['sited']}/{res['N']} (bound {res['bound_nets']}) failed {res['failed']} "
        f"freed {res.get('freed')} stitches {len(res.get('gnd_stitch') or [])} lines {res.get('lines')} t={res['t']}s")
    say(f"sites -> {pos[1]}")
    return res


# ---------------------------------------------------------------------------------------- run / table
GEN_OPTS = ("--pull", "--drop", "--line", "--gate")


def split_terms(terms):
    """gen terms and solve terms from one list."""
    g, s, i = [], [], 0
    while i < len(terms):
        t = terms[i]
        if t in GEN_OPTS:
            g += terms[i:i + 2]
            if t == "--gate":
                s += terms[i:i + 2]
            i += 2
        elif t == "--freeany":
            s.append(t)
            i += 1
        else:
            s += terms[i:i + 2]
            i += 2
    return g, s


def cache_name(spec, board, gterms):
    pcb = next(p for p in sorted(Path(board).glob("*.kicad_pcb")) if not p.name.startswith(("_autosave-", "~")))
    h = hashlib.sha1()
    h.update(Path(spec).read_bytes())
    h.update(pcb.read_bytes())
    h.update(" ".join(gterms).encode())
    h.update(Path(__file__).read_bytes())
    return f"cands_{h.hexdigest()[:12]}.json.gz"


def run(a):
    board, out = opt(a, "--board"), opt(a, "--out")
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--")
                                                                   or a[i - 1] == "--freeany")]
    if not board or not out or len(pos) != 1:
        die("run --board COPY SPEC.json --out DIR [terms]")
    spec = pos[0]
    rest = [x for i, x in enumerate(a) if x not in (spec,) and x not in ("--board", "--out")
            and not (i and a[i - 1] in ("--board", "--out"))]
    gterms, sterms = split_terms(rest)
    Path(out).mkdir(parents=True, exist_ok=True)
    cf = Path(out) / cache_name(spec, Path(board).expanduser(), gterms)
    if not cf.exists():
        r = subprocess.run([str(KPY), __file__, "gen", "--board", board, spec, str(cf)] + gterms)
        if r.returncode:
            sys.exit(r.returncode)
    else:
        say(f"candidates cached: {cf}")
    so = Path(out) / ("s_" + (hashlib.sha1(" ".join(gterms + sterms).encode()).hexdigest()[:10]) + ".json")
    r = subprocess.run([str(VPY), __file__, "solve", str(cf), str(so)] + sterms)
    sys.exit(r.returncode)


def table(a):
    sys.path.insert(0, str(TOOLS))
    import par
    board, out = opt(a, "--board"), opt(a, "--out")
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    if not board or not out or len(pos) != 2:
        die("table --board COPY SPEC.json ROWS.json --out DIR [--jobs N]")
    spec, rows_p = pos
    rows = load_json(rows_p)
    jobs = int(opt(a, "--jobs", 4))
    O = Path(out)
    O.mkdir(parents=True, exist_ok=True)
    gens = {}
    for r in rows:
        g = list(r.get("gen", []))
        gens.setdefault(" ".join(g), (g, O / cache_name(spec, Path(board).expanduser(), g)))
    todo = [(g, cf) for g, cf in gens.values() if not cf.exists()]
    say(f"table: {len(rows)} rows, {len(gens)} candidate sets ({len(todo)} to generate), {jobs} jobs")
    res = par.cmap([[str(KPY), __file__, "gen", "--board", board, spec, str(cf)] + g for g, cf in todo], jobs=jobs)
    for (g, cf), (rc, sec, txt) in zip(todo, res):
        (O / (cf.name + ".log")).write_text(txt or "")
        if rc:
            say(f"gen {' '.join(g) or '(base)'} FAILED rc {rc}: {(txt or '').strip().splitlines()[-1:]}")
    cmds, outs = [], []
    for i, r in enumerate(rows):
        cf = gens[" ".join(r.get("gen", []))][1]
        so = O / f"s_{i:03d}.json"
        outs.append(so)
        cmds.append([str(VPY), __file__, "solve", str(cf), str(so), "--workers", "1"] + list(r.get("solve", [])))
    res = par.cmap(cmds, jobs=jobs)
    lines = ["terms\tsited/N\tfailing nets\tcost\tstatus\tsolve file"]
    for r, so, (rc, sec, txt) in zip(rows, outs, res):
        try:
            v = json.loads(so.read_text())
            st, sn, fl = v["status"], f"{v['sited']}/{v['N']}" if v["sited"] is not None else f"-/{v['N']}", \
                ", ".join(v["failed"] or []) if v["failed"] is not None else "-"
        except Exception:
            st, sn, fl = f"ERROR rc {rc}", "-", "-"
        terms = r.get("label") or " ".join(r.get("gen", []) + r.get("solve", [])) or "base"
        lines.append(f"{terms}\t{sn}\t{fl}\t{r.get('cost', '')}\t{st}\t{so.name}")
    (O / "table.tsv").write_text("\n".join(lines) + "\n")
    say("\n".join(lines))
    say(f"-> {O / 'table.tsv'}")


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = a[0], a[1:]
    if cmd == "--table":
        cmd = "table"
    if cmd == "gen":
        gen(rest)
    elif cmd == "solve":
        res = solve(rest)
        return 0 if res["status"] in ("OPTIMAL", "INFEASIBLE", "FEASIBLE-at-limit") else 1
    elif cmd == "run":
        run(rest)
    elif cmd == "table":
        table(rest)
    else:
        die(f"unknown command {cmd!r}: gen, solve, run or table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
