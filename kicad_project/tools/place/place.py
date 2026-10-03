#!/usr/bin/env python3
"""Placement checks for a board, read from the saved .kicad_pcb with KiCad's own pcbnew.

    tools/kpy tools/place/place.py gaps   --board board1 [--refs U2,C10]
    tools/kpy tools/place/place.py check  --board board1 plan.json
    tools/kpy tools/place/place.py apply  --board board1 plan.json
    tools/kpy tools/place/place.py auto   --board board1 spec.json
    tools/kpy tools/place/place.py sync   --board board1
    tools/kpy tools/place/place.py labels --board board1 [--dry-run]
    tools/kpy tools/place/place.py park   --board board1 [--refs J1,H1]
    tools/kpy tools/place/place.py drc    --board board1

  gaps    courtyard gap to the nearest neighbour on each side, for every
          footprint on the board (per side).  Read-only.
  check   apply a plan IN MEMORY and test it (see README): courtyards per side,
          copper between different nets on a shared copper layer, silk marks
          against the neighbour's mask opening, pads and holes against the
          board edge, the keep-outs in <board>/placement.json.  Then the net
          span (MST of pad positions on the board) for every net the plan
          touches, before -> after, and each group's extent.  Read-only.
  apply   check, and if it passes, apply the plan and save through
          pcbnew.SaveBoard (backup first).  KiCad must be closed.  With KiCad
          open, apply the same plan with Konnect's set_component_placements
          instead (it cannot flip).
  sync    every project-library footprint on the board against its
          .kicad_mod: pads, attributes, silk / courtyard / fab geometry
          (back-side footprints compared after KiCad's flip).  Exit 1 on a
          difference.
  labels  reference designators to the Board Setup silk minimums, then a spot
          for each label on its own side; a label with no legal spot is hidden
          on silk (the Fab ${REFERENCE} still carries it).  KiCad must be closed.
  auto    supporting parts: the cheapest legal spot in a region by weighted
          distance to what each part connects to; writes <spec>.plan.json.
  park    move every part not kept off the board (east of the outline) so a
          re-placement starts from a clean floor.  KiCad must be closed.
  drc     KiCad's DRC with the schematic-parity test ON, summarised.

A plan is JSON {"REF": [x, y, rot]} or [x, y, rot, "B"] for the back side
(absolute board mm, rotation in degrees as KiCad shows it).

Read tools/place/README.md.  Run with tools/kpy (it imports pcbnew).  gaps /
check / sync read the SAVED file: save in KiCad first (Konnect save_project).
"""

import argparse
import itertools
import json
import math
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import boardsel                                     # noqa: E402


def die(msg, code=2):
    print(f"{Path(sys.argv[0]).stem or 'place'}: {msg}", file=sys.stderr)
    sys.exit(code)


@contextmanager
def quiet_stderr():
    sys.stderr.flush()
    saved, null = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 2)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(null)
        os.close(saved)


def pcbnew_module():
    try:
        with quiet_stderr():
            import pcbnew
    except ImportError:
        die(f"cannot import pcbnew under {sys.executable}; run it with tools/kpy")
    return pcbnew


def rgap(a, c):
    """Edge-to-edge distance between two rectangles; negative = overlap depth.
    A 5-tuple (x0, y0, x1, y1, "circle") is a circle given by its bounding box."""
    if len(a) == 5 or len(c) == 5:
        if len(a) == 5 and len(c) == 5:
            r1, r2 = (a[2] - a[0]) / 2, (c[2] - c[0]) / 2
            return math.dist(((a[0] + a[2]) / 2, (a[1] + a[3]) / 2), ((c[0] + c[2]) / 2, (c[1] + c[3]) / 2)) - r1 - r2
        circ, rect = (a, c) if len(a) == 5 else (c, a)
        cx, cy, r = (circ[0] + circ[2]) / 2, (circ[1] + circ[3]) / 2, (circ[2] - circ[0]) / 2
        nx, ny = min(max(cx, rect[0]), rect[2]), min(max(cy, rect[1]), rect[3])
        return math.dist((nx, ny), (cx, cy)) - r
    dx = max(c[0] - a[2], a[0] - c[2])
    dy = max(c[1] - a[3], a[1] - c[3])
    if dx < 0 and dy < 0:
        return max(dx, dy)
    return math.hypot(max(dx, 0), max(dy, 0))


def grow(b, g):
    return (b[0] - g, b[1] - g, b[2] + g, b[3] + g)


# ---------------------------------------------------------------------------
# board model
# ---------------------------------------------------------------------------

class Board:
    def __init__(self, board_dir):
        self.dir = board_dir
        self.pcb = boardsel.one_pcb(board_dir)
        self.pn = pn = pcbnew_module()
        with quiet_stderr():
            self.b = pn.LoadBoard(str(self.pcb))
        self.mm = pn.ToMM
        cfg = board_dir / "placement.json"
        self.cfg = json.loads(cfg.read_text()) if cfg.exists() else {}
        self.fps = {f.GetReference(): f for f in self.b.GetFootprints()}
        refs = [f.GetReference() for f in self.b.GetFootprints()]
        dup = sorted({r for r in refs if refs.count(r) > 1})
        if dup:
            die(f"{len(dup)} reference(s) are used by more than one footprint: {', '.join(dup)}. "
                "Every footprint needs its own (mounting holes left at 'REF**' included): "
                "one of each pair would be invisible to every check here.")
        self.nocrt = set()                    # footprints judged by their pads: no courtyard
        self.outline = self._outline()
        self.rings = self._rings()
        ds = self.b.GetDesignSettings()
        self.mask_exp = self.mm(ds.m_SolderMaskExpansion)
        self.silk_clr = self.mm(ds.m_SilkClearance)
        self.web = self.mm(ds.m_SolderMaskMinWidth)
        # the Board Setup minimums (board_setup.py types them from 14_fab.md)
        self.text_h = self.mm(ds.m_MinSilkTextHeight)
        self.text_t = self.mm(ds.m_MinSilkTextThickness)
        self.edge_clr = self.mm(ds.m_CopperEdgeClearance)
        self.clearance = self.mm(ds.m_MinClearance)
        self.hole_edge = self.cfg.get("hole_to_edge")       # None = not transcribed yet
        self.hole_to_hole = self.mm(ds.m_HoleToHoleMin)
        self.CRT = {"F": pn.F_CrtYd, "B": pn.B_CrtYd}
        self.SILK = {"F": pn.F_SilkS, "B": pn.B_SilkS}

    def _outline(self):
        pts = []
        for d in self.b.GetDrawings():
            if d.GetLayer() == self.pn.Edge_Cuts:
                bb, w = d.GetBoundingBox(), d.GetWidth() / 2
                pts += [(self.mm(bb.GetLeft() + w), self.mm(bb.GetTop() + w)),
                        (self.mm(bb.GetRight() - w), self.mm(bb.GetBottom() - w))]
        if not pts:
            die("no board outline on Edge.Cuts")
        return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))

    def _rings(self):
        """The real outline: [outer ring, hole, hole, ...], each a list of (x, y)
        mm, from KiCad's own outline builder (arcs become short segments). None
        when KiCad cannot close the outline -- the checks then fall back to its
        bounding box and say so."""
        pn = self.pn
        try:
            ps = pn.SHAPE_POLY_SET()
            with quiet_stderr():
                try:                                  # KiCad 10: (outlines, infer-if-necessary)
                    ok = self.b.GetBoardPolygonOutlines(ps, False)
                except TypeError:                     # older signature
                    ok = self.b.GetBoardPolygonOutlines(ps)
            if not ok or ps.OutlineCount() != 1:
                return None
            def ring(ch):
                return [(self.mm(ch.CPoint(i).x), self.mm(ch.CPoint(i).y)) for i in range(ch.PointCount())]
            rings = [ring(ps.Outline(0))] + [ring(ps.Hole(0, h)) for h in range(ps.HoleCount(0))]
            return rings if len(rings[0]) >= 3 else None
        except Exception:                                    # noqa: BLE001 -- API drift
            return None

    def on_board(self, x, y):
        """Is the point on board material (inside the outline, outside its cut-outs)?"""
        if not self.rings:
            o = self.outline
            return o[0] <= x <= o[2] and o[1] <= y <= o[3]

        def inside(pts):
            c = False
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            return c
        return inside(self.rings[0]) and not any(inside(h) for h in self.rings[1:])

    def edge_dist(self, x, y):
        """Distance from a point to the nearest board edge; negative off the board."""
        if not self.rings:
            o = self.outline
            return min(x - o[0], y - o[1], o[2] - x, o[3] - y)
        best = 1e9
        for pts in self.rings:
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                dx, dy = x2 - x1, y2 - y1
                L = dx * dx + dy * dy
                t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L))
                best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
        return best if self.on_board(x, y) else -best

    def box_edge_dist(self, bx):
        """Smallest edge distance over a box's corners and edge midpoints."""
        xm, ym = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        return min(self.edge_dist(x, y) for x, y in
                   ((bx[0], bx[1]), (bx[2], bx[1]), (bx[2], bx[3]), (bx[0], bx[3]),
                    (xm, bx[1]), (xm, bx[3]), (bx[0], ym), (bx[2], ym)))

    def box(self, bb):
        return (self.mm(bb.GetLeft()), self.mm(bb.GetTop()), self.mm(bb.GetRight()), self.mm(bb.GetBottom()))

    def side(self, f):
        return "B" if f.IsFlipped() else "F"

    def courtyard(self, f):
        """(side, box) with the 0.05 mm line's centreline as the edge, or None."""
        s = self.side(f)
        items = [g for g in f.GraphicalItems() if g.GetLayer() == self.CRT[s]]
        if not items:
            # No courtyard (a test point, a fiducial): still an obstacle, by the
            # extent of its pads -- a part planned on top of it must not pass.
            pb = [self.box(p.GetBoundingBox()) for p in f.Pads()]
            if not pb:
                return None
            self.nocrt.add(f.GetReference())
            return (min(b[0] for b in pb), min(b[1] for b in pb),
                    max(b[2] for b in pb), max(b[3] for b in pb))
        cy = [g.GetBoundingBox() for g in items]
        box = (min(self.mm(c.GetLeft()) for c in cy) + 0.025, min(self.mm(c.GetTop()) for c in cy) + 0.025,
               max(self.mm(c.GetRight()) for c in cy) - 0.025, max(self.mm(c.GetBottom()) for c in cy) - 0.025)
        if len(items) == 1 and items[0].GetShape() == self.pn.SHAPE_T_CIRCLE:
            return box + ("circle",)
        return box

    def courtyard_rects(self, f):
        """The courtyard as rectangles that cover it EXACTLY, not its box.

        An angled connector's courtyard follows the part -- body on one side,
        pin field on the other, narrower -- and judging it by its bounding box
        claims the body's full length over bare legs.  A
        rectilinear polygon is cut into row strips on its own vertex grid; any
        other shape (a rect, a circle, a slanted polygon) falls back to its box,
        which is conservative."""
        box = self.courtyard(f)
        if not box:
            return []
        box = box[:4]
        s = self.side(f)
        items = [g for g in f.GraphicalItems() if g.GetLayer() == self.CRT[s]]
        if len(items) != 1 or items[0].GetShape() != self.pn.SHAPE_T_POLY:
            return [box]
        pts = [(self.mm(q.x), self.mm(q.y)) for q in items[0].GetPolyPoints()]
        if len(pts) < 4 or any(abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6
                               for a, b in zip(pts, pts[1:] + pts[:1])):
            return [box]

        def inside(x, y):
            c = False
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                    c = not c
            return c
        xs = sorted({round(q[0], 4) for q in pts})
        ys = sorted({round(q[1], 4) for q in pts})
        rects = []
        for j in range(len(ys) - 1):
            run = None
            for i in range(len(xs) - 1):
                if inside((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2):
                    run = (run[0] if run else xs[i], xs[i + 1])
                elif run:
                    rects.append((run[0], ys[j], run[1], ys[j + 1]))
                    run = None
            if run:
                rects.append((run[0], ys[j], run[1], ys[j + 1]))
        return rects or [box]

    def placed(self):
        """Footprints whose courtyard overlaps the outline (a mounting hole's
        courtyard runs over the edge and is still an obstacle); parts parked
        outside the outline are not."""
        o = self.outline
        out = {}
        for r, f in self.fps.items():
            c = self.courtyard(f)
            if c and c[0] < o[2] and c[2] > o[0] and c[1] < o[3] and c[3] > o[1]:
                out[r] = f
        return out

    def pads(self, f):
        pn = self.pn
        out = []
        for p in f.Pads():
            cu = set()
            npth = p.GetAttribute() == pn.PAD_ATTRIB_NPTH
            big = max(p.GetSizeX(), p.GetSizeY()) > max(p.GetDrillSizeX(), p.GetDrillSizeY())
            if not npth or big:                      # a bare NPTH is a hole, not copper
                for lay in p.GetLayerSet().CuStack():
                    cu.add("F" if lay == pn.F_Cu else ("B" if lay == pn.B_Cu else "In"))
            hole = p.GetDrillSizeX() > 0
            out.append(dict(num=p.GetNumber(), net=p.GetNetname(), cu=cu, hole=hole,
                            drill=self.mm(max(p.GetDrillSizeX(), p.GetDrillSizeY())) if hole else 0.0,
                            box=self.box(p.GetBoundingBox()), pos=(self.mm(p.GetPosition().x), self.mm(p.GetPosition().y))))
        return out

    def silk(self, f):
        s = self.side(f)
        return [self.box(g.GetBoundingBox()) for g in f.GraphicalItems()
                if g.GetLayer() == self.SILK[s] and "TEXT" not in g.GetClass()]

    def apply_moves(self, moves):
        pn = self.pn
        for r, m in moves.items():
            f = self.fps[r]
            want_b = len(m) > 3 and str(m[3]).upper().startswith("B")
            if want_b != f.IsFlipped():
                f.Flip(f.GetPosition(), pn.FLIP_DIRECTION_LEFT_RIGHT)
            f.SetPosition(pn.VECTOR2I(pn.FromMM(m[0]), pn.FromMM(m[1])))
            f.SetOrientationDegrees(m[2])


def board_dir_arg(s):
    """boardN, the folder name or a path; None = the folder you are in, else
    this session's board (tools/boardsel.py)."""
    return boardsel.resolve(s)


# ---------------------------------------------------------------------------
# gaps
# ---------------------------------------------------------------------------

def cmd_gaps(a):
    B = Board(a.board)
    P = B.placed()
    info = {r: (B.side(f), B.courtyard(f)) for r, f in P.items()}
    only = set(a.refs.split(",")) if a.refs else None
    print(f"{len(P)} footprints on the board")
    for r in sorted(P, key=lambda r: (info[r][0], info[r][1][1], info[r][1][0])):
        if only and r not in only:
            continue
        s, A = info[r]
        best = {}
        for c, (s2, C) in info.items():
            if c == r or s2 != s:
                continue
            dx, dy = max(C[0] - A[2], A[0] - C[2]), max(C[1] - A[3], A[1] - C[3])
            if dy < 0:
                d = "R" if C[0] >= A[2] - 1e-6 else ("L" if C[2] <= A[0] + 1e-6 else None)
                if d and (d not in best or dx < best[d][1]):
                    best[d] = (c, dx)
            if dx < 0:
                d = "B" if C[1] >= A[3] - 1e-6 else ("T" if C[3] <= A[1] + 1e-6 else None)
                if d and (d not in best or dy < best[d][1]):
                    best[d] = (c, dy)
        f = P[r]
        pos = f.GetPosition()
        near = "  ".join(f"{d}:{best[d][0]}={best[d][1]:.2f}" for d in "LRTB" if d in best)
        print(f"{r:6} {s} ({B.mm(pos.x):7.2f},{B.mm(pos.y):7.2f},{f.GetOrientationDegrees():4.0f})  {near}")
    return 0


# ---------------------------------------------------------------------------
# check / apply
# ---------------------------------------------------------------------------

def keepout_hits(B, box, side="F", ref=None):
    o = B.outline
    hits = []
    for k in B.cfg.get("keepouts", []):
        if k.get("side", "F") not in (side, "both") or ref in k.get("allow", []):
            continue
        r = k["rect"]
        x0 = o[0] + r[0] if r[0] is not None else -1e9
        y0 = o[1] + r[1] if r[1] is not None else -1e9
        x1 = o[0] + r[2] if r[2] is not None else 1e9
        y1 = o[1] + r[3] if r[3] is not None else 1e9
        if box[0] < x1 and box[2] > x0 and box[1] < y1 and box[3] > y0:
            hits.append(k["name"])
    return hits


def snapshot(B, refs):
    out = {}
    for r in refs:
        f = B.fps[r]
        out[r] = dict(side=B.side(f), box=B.courtyard(f), rects=B.courtyard_rects(f),
                      pads=B.pads(f), silk=B.silk(f))
    return out


def mst(points):
    if len(points) < 2:
        return 0.0
    left, total = set(range(1, len(points))), 0.0
    dist = {i: math.dist(points[0], points[i]) for i in left}
    while left:
        i = min(left, key=dist.get)
        total += dist[i]
        left.remove(i)
        for j in left:
            dist[j] = min(dist[j], math.dist(points[i], points[j]))
    return total


def run_check(B, moves, verbose=True):
    unknown = [r for r in moves if r not in B.fps]
    if unknown:
        die(f"not on the board: {unknown}")
    if B.hole_edge is None:
        die("placement.json: 'hole_to_edge' is not set. Copy the hole-to-board-edge "
            "minimum from handoff/14_fab.md (mm); a missing row there is a QUESTIONS.md item.")
    if not B.rings:
        print("note: KiCad could not close the board outline; edge tests use its bounding box")
    before_all = set(B.placed())
    before = snapshot(B, before_all | set(moves))
    B.apply_moves(moves)
    refs = set(B.placed()) | set(moves)
    after = snapshot(B, refs)
    fixed = set(B.cfg.get("fixed", []))
    overhang = set(B.cfg.get("overhang", []))
    min_cy = B.cfg.get("min_courtyard_gap", 0.03)
    # mask web between two openings, and never less than the copper clearance
    pad_min = max(B.web + 2 * B.mask_exp, B.clearance)
    fails = []
    for x, y in itertools.combinations(sorted(refs), 2):
        if x not in moves and y not in moves:
            continue
        X, Y = after[x], after[y]
        if X["box"] and Y["box"] and X["side"] == Y["side"]:
            g = min(rgap(a_, b_) for a_ in X["rects"] for b_ in Y["rects"])
            if g < min_cy:
                fails.append(f"COURTYARD {x}-{y} ({X['side']}) gap {g:.3f}")
        for p in X["pads"]:
            for q in Y["pads"]:
                if p["hole"] and q["hole"]:              # drilling: any nets, plated or not
                    hh = math.dist(p["pos"], q["pos"]) - p["drill"] / 2 - q["drill"] / 2
                    if hh < B.hole_to_hole:
                        fails.append(f"HOLES {x}.{p['num']} - {y}.{q['num']} {hh:.3f} < {B.hole_to_hole:g}")
                shared = (p["cu"] & q["cu"]) or (p["hole"] and q["cu"]) or (q["hole"] and p["cu"])
                if not shared or (p["net"] and p["net"] == q["net"]):
                    continue
                g = rgap(p["box"], q["box"])
                if g < pad_min:
                    fails.append(f"PADS {x}.{p['num']}({p['net'].split('/')[-1]}) - {y}.{q['num']}({q['net'].split('/')[-1]}) {g:.3f} < {pad_min:.2f}")
        for h_ref, c_ref in ((x, y), (y, x)):
            H, C = after[h_ref], after[c_ref]
            if C["box"]:
                for p in H["pads"]:
                    if p["hole"]:
                        hb = (p["pos"][0] - p["drill"] / 2, p["pos"][1] - p["drill"] / 2,
                              p["pos"][0] + p["drill"] / 2, p["pos"][1] + p["drill"] / 2)
                        if any(rgap(hb, cr) < 0 for cr in C["rects"]):
                            fails.append(f"PTH {h_ref}.{p['num']} inside {c_ref}'s courtyard")
        for s_ref, p_ref in ((x, y), (y, x)):
            S, P = after[s_ref], after[p_ref]
            for s in S["silk"]:
                for q in P["pads"]:
                    on_side = S["side"] in q["cu"] or q["hole"]
                    if on_side and rgap(s, q["box"]) < B.mask_exp + B.silk_clr:
                        fails.append(f"SILK {s_ref} mark over {p_ref}.{q['num']} mask opening "
                                     f"({rgap(s, q['box']) - B.mask_exp:.3f} < {B.silk_clr:g})")
    o = B.outline
    for r in moves:
        if r in fixed:
            continue
        X = after[r]
        if X["box"] is None or r in B.nocrt:
            fails.append(f"NO COURTYARD {r}")
            continue
        bx = X["box"]
        if r not in overhang and any(B.box_edge_dist(cr) < 0 for cr in X["rects"]):
            fails.append(f"OUTLINE {r}: courtyard off the board (list it under 'overhang' if a connector's pins are meant to)")
        for p in X["pads"]:
            e = B.box_edge_dist(p["box"])
            if p["cu"] and e < B.edge_clr:
                fails.append(f"EDGE {r}.{p['num']} copper {e:.2f} mm from the edge (< {B.edge_clr:g})")
            if p["hole"]:
                cx, cy = p["pos"]
                he = B.edge_dist(cx, cy) - p["drill"] / 2
                if he < B.hole_edge:
                    fails.append(f"EDGE {r}.{p['num']} hole {he:.2f} mm from the edge (< {B.hole_edge:g})")
        hits = sorted({h for cr in X["rects"] for h in keepout_hits(B, cr, X["side"], r)})
        if hits:
            fails.append(f"KEEPOUT {r}: {', '.join(hits)}")
    ok = not fails
    for fl in fails:
        print(fl)
    near = sorted(B.nocrt & refs - set(moves))
    if near:
        print(f"note: no courtyard on {', '.join(near)} -- judged by their pads' extent")
    print("CHECKS PASS" if ok else f"CHECKS FAIL ({len(fails)})")
    if verbose:
        ignore = set(B.cfg.get("ignore_nets", []))
        nets = {}
        for r in moves:
            for p in after[r]["pads"]:
                if p["net"] and p["net"] not in ignore and not p["net"].startswith("unconnected"):
                    nets.setdefault(p["net"], None)
        rows = []
        for n in nets:
            pa = [p["pos"] for r in refs for p in after[r]["pads"] if p["net"] == n]
            pb = [p["pos"] for r in before if r in before_all for p in before[r]["pads"] if p["net"] == n]
            rows.append((n, len(pa), mst(pb), mst(pa)))
        if rows:
            print("net span on the board (MST of pad positions), before -> after [pads placed]:")
            for n, k, s0, s1 in sorted(rows, key=lambda t: -t[3])[:a_limit(B)]:
                print(f"  {n.split('/')[-1]:>18} {s0:6.1f} -> {s1:6.1f} mm  [{k}]")
        for name, members in B.cfg.get("groups", {}).items():
            m = [r for r in members if r in after and after[r]["box"] and r in moves]
            if not m:
                continue
            bx = [after[r]["box"] for r in m]
            e = (min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx))
            print(f"group {name}: {e[2]-e[0]:.2f} x {e[3]-e[1]:.2f} mm at ({e[0]:.2f},{e[1]:.2f})")
    return ok


def a_limit(B):
    return B.cfg.get("net_report_limit", 40)


def load_moves(path):
    moves = json.loads(Path(path).read_text())
    return {k: v for k, v in moves.items() if not k.startswith("_")}


def cmd_check(a):
    B = Board(a.board)
    return 0 if run_check(B, load_moves(a.moves)) else 1


def locks(pcb):
    return [p for p in (pcb.parent / f"~{pcb.name}.lck", pcb.parent / f"~{pcb.stem}.kicad_pro.lck") if p.exists()]


def cmd_apply(a):
    B = Board(a.board)
    held = locks(B.pcb)
    if held and not a.dry_run:
        die(f"KiCad has the board open ({', '.join(p.name for p in held)}); use Konnect set_component_placements, or close KiCad", 3)
    moves = load_moves(a.moves)
    if not run_check(B, moves, verbose=False):
        die("plan fails its checks; nothing written", 1)
    if a.dry_run:
        print("(dry run: the plan passes; nothing written)")
        return 0
    n0 = (len(B.b.GetFootprints()), len(B.b.GetTracks()), len(B.b.GetDrawings()))
    backup = B.pcb.with_suffix(".kicad_pcb.place-bak")
    shutil.copy2(B.pcb, backup)
    with quiet_stderr():
        ok = B.pn.SaveBoard(str(B.pcb), B.b)
    if not ok or n0 != (len(B.b.GetFootprints()), len(B.b.GetTracks()), len(B.b.GetDrawings())):
        shutil.copy2(backup, B.pcb)
        die("save failed or item counts moved; board restored", 4)
    C = Board(a.board)                    # read back what was saved
    bad = []
    for r, m in moves.items():
        f = C.fps[r]
        p = f.GetPosition()
        side = "B" if len(m) > 3 and str(m[3]).upper().startswith("B") else "F"
        if abs(C.mm(p.x) - m[0]) > 1e-4 or abs(C.mm(p.y) - m[1]) > 1e-4 or C.side(f) != side \
                or abs(((f.GetOrientationDegrees() - m[2]) + 180) % 360 - 180) > 1e-3:
            bad.append(r)
    if bad:
        shutil.copy2(backup, B.pcb)
        die(f"saved positions differ from the plan for {bad}; board restored", 4)
    print(f"applied {len(moves)} placements to {B.pcb.name} (read back and matched; previous copy {backup.name})")
    return 0


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def cmd_sync(a):
    sys.path.insert(0, str(ROOT / "tools" / "fp"))
    import fp as FP
    B_dir = a.board
    pcb = boardsel.one_pcb(B_dir)
    pretties = sorted((B_dir / "lib").glob("*.pretty"))
    if not pretties:
        print("0 project-library footprints to compare (lib/ has no .pretty yet): nothing to differ")
        return 0
    if len(pretties) != 1:
        die("expected one lib/*.pretty")
    lib = pretties[0]
    stem = lib.name[:-len(".pretty")]
    tree = FP.raw_parse(pcb.read_text())
    G = ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly")
    PAD_INSTANCE = ("uuid", "net", "pinfunction", "pintype", "property")   # board-side, or never written on boards
    OWNED = ("exclude_from_bom", "exclude_from_pos_files", "dnp")          # set from the symbol by Update PCB
    FLIP_LAYER = lambda s: ("B." + s[2:] if s.startswith("F.") else ("F." + s[2:] if s.startswith("B.") else s))

    def r4(node):   # board geometry is whole nanometres: compare at 0.1 um
        if isinstance(node, list):
            return [r4(x) for x in node]
        try:
            return repr(round(float(node), 4) + 0.0)
        except ValueError:
            return node

    def mirror(node):
        """A library item as KiCad stores it on a back-side footprint: layers
        swapped, local y mirrored (KiCad flips top-bottom in the local frame)."""
        if not isinstance(node, list):
            return node
        out = [node[0]]
        for k in node[1:]:
            if isinstance(k, list) and k and k[0] in ("start", "end", "center", "mid", "at", "xy") and len(k) >= 3:
                v = [k[0], k[1], repr(-float(k[2]) + 0.0)] + k[3:]
                if k[0] == "at" and len(k) > 3:
                    v[3] = repr((-float(k[3])) % 360)
                out.append(v)
            elif isinstance(k, list) and k and k[0] in ("layer", "layers"):
                out.append([k[0]] + ['"%s"' % FLIP_LAYER(FP.rq(x)) if isinstance(x, str) else x for x in k[1:]])
            else:
                out.append(mirror(k))
        return out

    def norm(x):
        x = json.loads(json.dumps(x).replace('["type", "default"]', '["type", "solid"]'))
        if x[0] == "fp_rect":
            # KiCad stores a rect rotated off 90 degrees (or flipped) as a 4-point
            # polygon, and may swap its corners: compare every rect as its polygon
            st = next(k for k in x if isinstance(k, list) and k[0] == "start")
            en = next(k for k in x if isinstance(k, list) and k[0] == "end")
            x0, y0, x1, y1 = float(st[1]), float(st[2]), float(en[1]), float(en[2])
            pts = ["pts"] + [["xy", repr(a), repr(b)] for a, b in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            x = ["fp_poly", pts] + [k for k in x[1:] if not (isinstance(k, list) and k[0] in ("start", "end"))]
        s = r4(FP._norm([k for k in x if not (isinstance(k, list) and k and k[0] == "uuid")]))
        if x[0] == "fp_poly":
            pts = sorted(json.dumps(p) for p in s[1][1:])
            s[1] = [s[1][0]] + sorted(set(pts))
        return json.dumps(s)

    def padn(x, rot):
        x = [list(k) if isinstance(k, list) else k for k in x]
        for k in x:
            if isinstance(k, list) and k[0] == "at":
                ang = ((float(k[3]) if len(k) > 3 else 0.0) - rot) % 360
                k[:] = k[:3] + ([f"{ang:g}"] if 1e-6 < ang < 360 - 1e-6 else [])
        return json.dumps(r4(FP._norm([k for k in x if not (isinstance(k, list) and k and k[0] in PAD_INSTANCE)])))

    def graphics(node, layer):
        return sorted(norm(x) for x in node if isinstance(x, list) and x[0] in G and FP.rlayer(x) == layer)

    def pads(node, rot=0.0):
        return sorted(padn(x, rot) for x in node if isinstance(x, list) and x[0] == "pad")

    def attrs(node):
        at = next((k for k in node if isinstance(k, list) and k[0] == "attr"), ["attr"])
        return sorted(x for x in at[1:] if x not in OWNED)

    bad, n, nb = [], 0, 0
    for f in tree:
        if not (isinstance(f, list) and f[0] == "footprint"):
            continue
        libname, _, name = FP.rq(f[1]).partition(":")
        if libname != stem or not name:
            continue
        ref = next(FP.rq(k[2]) for k in f if isinstance(k, list) and k[0] == "property" and FP.rq(k[1]) == "Reference")
        path = lib / f"{name}.kicad_mod"
        if not path.exists():
            bad.append(f"{ref}: {name} is not in {lib.name}")
            continue
        L = FP.raw_parse(path.read_text())
        back = FP.rq(FP.rkid(f, "layer")[1]) == "B.Cu"
        if back:
            L = [mirror(k) if isinstance(k, list) and k and k[0] in G + ("pad",) else k for k in L]
            nb += 1
        n += 1
        at = next((k for k in f if isinstance(k, list) and k[0] == "at"), ["at", 0, 0])
        rot = float(at[3]) if len(at) > 3 else 0.0
        if pads(f, rot) != pads(L, 0.0 if not back else 0.0):
            bad.append(f"{ref} pads")
        if attrs(f) != attrs(L):
            bad.append(f"{ref} attr {attrs(f)} vs library {attrs(L)}")
        side = "B." if back else "F."
        for layer in ("SilkS", "CrtYd", "Fab"):
            if graphics(f, side + layer) != graphics(L, side + layer):
                bad.append(f"{ref} {side}{layer}")
    print(f"{n} {stem} footprints compared with the library ({nb} on the back): "
          + ("all identical" if not bad else "DIFFER: " + ", ".join(bad)))
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

def cmd_labels(a):
    B = Board(a.board)
    pn = B.pn
    pcb = B.pcb
    if not a.dry_run and locks(pcb):
        die("KiCad has the board open; close it first", 3)
    H, T = B.text_h, B.text_t
    if H <= 0 or T <= 0:
        die("Board Setup has no silkscreen text minimums yet: run "
            "tools/board_setup/board_setup.py apply first (they come from 14_fab.md)")
    for f in B.fps.values():                      # every label to the fab's minimum
        r = f.Reference()
        r.SetTextSize(pn.VECTOR2I(pn.FromMM(H), pn.FromMM(H)))
        r.SetTextThickness(pn.FromMM(T))
    P = B.placed()
    hide = set(B.cfg.get("labels", {}).get("hide", []))
    o = B.outline
    inner = (o[0] + B.silk_clr, o[1] + B.silk_clr, o[2] - B.silk_clr, o[3] - B.silk_clr)
    g_pad = B.mask_exp + B.silk_clr
    STEP, TOP = 0.25, 40
    clash = lambda p, q: rgap(p, q) < B.silk_clr
    result, hidden = {}, sorted(r for r in P if r in hide)
    for side in ("F", "B"):
        # obstacles on this side: pads with copper here (or a hole), silk marks here,
        # every courtyard on this side (a label under a part is unreadable)
        obst, cyd = [], {}
        for r, f in B.fps.items():
            for p in B.pads(f):
                if side in p["cu"] or p["hole"]:
                    obst.append(grow(p["box"], g_pad))
            if B.side(f) == side:
                obst += [grow(s, B.silk_clr) for s in B.silk(f)]
                # ... and its other silk text (a value, a note drawn in the footprint)
                obst += [grow(B.box(g.GetBoundingBox()), B.silk_clr) for g in f.GraphicalItems()
                         if g.GetLayer() == B.SILK[side] and "TEXT" in g.GetClass()]
                c = B.courtyard(f)
                if c:
                    cyd[r] = c[:4]
        # board-level silk on this side: logos, revision text, drawn lines
        obst += [grow(B.box(d.GetBoundingBox()), B.silk_clr) for d in B.b.GetDrawings()
                 if d.GetLayer() == B.SILK[side]]
        todo = [r for r in P if r not in hide and B.side(P[r]) == side and r in cyd]

        def text_box(ref, x, y, ang):
            ref.SetTextAngleDegrees(ang)
            ref.SetPosition(pn.VECTOR2I(pn.FromMM(x), pn.FromMM(y)))
            return B.box(ref.GetBoundingBox())

        cands = {}
        for r in todo:
            ref = P[r].Reference()
            c = cyd[r]
            cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
            near_obst = [ob for ob in obst if rgap(c, ob) < 6.0]
            near_cyd = [cc for q, cc in cyd.items() if rgap(c, cc) < 6.0]
            out = []
            for ang in (0, 90):
                w0 = text_box(ref, 0, 0, ang)
                tw, th = w0[2] - w0[0], w0[3] - w0[1]
                for d in [i * STEP + 0.05 for i in range(0, 9)]:
                    spots = []
                    n = int((c[2] - c[0] + tw) / 2 / STEP) + 2
                    spots += [(cx + k * STEP, c[1] - d - th / 2) for k in range(-n, n + 1)]
                    spots += [(cx + k * STEP, c[3] + d + th / 2) for k in range(-n, n + 1)]
                    n = int((c[3] - c[1] + th) / 2 / STEP) + 2
                    spots += [(c[0] - d - tw / 2, cy + k * STEP) for k in range(-n, n + 1)]
                    spots += [(c[2] + d + tw / 2, cy + k * STEP) for k in range(-n, n + 1)]
                    for sx, sy in spots:
                        bx = (sx - tw / 2, sy - th / 2, sx + tw / 2, sy + th / 2)
                        if bx[0] < inner[0] or bx[1] < inner[1] or bx[2] > inner[2] or bx[3] > inner[3] \
                                or B.box_edge_dist(bx) < B.silk_clr:
                            continue
                        if any(rgap(bx, ob) < 0 for ob in near_obst) or any(rgap(bx, cc) < 0 for cc in near_cyd):
                            continue
                        out.append((math.dist((sx, sy), (cx, cy)) + (0.4 if ang else 0.0), ang, sx, sy, bx))
            out.sort(key=lambda t: t[0])
            cands[r] = out
        taken = []
        while todo:
            feas = {r: [k for k in cands[r] if not any(clash(k[4], t) for t in taken)] for r in todo}
            for r in [r for r in todo if not feas[r]]:
                hidden.append(r)
                todo.remove(r)
            if not todo:
                break
            r = min(todo, key=lambda r: (len(feas[r]), feas[r][0][0]))
            others = {q: feas[q][:TOP] for q in todo if q != r}
            best = min(feas[r][:TOP], key=lambda k: (
                sum(1 for q in others for m in others[q] if clash(k[4], m[4]))
                + 100 * sum(1 for q in others if all(clash(k[4], m[4]) for m in others[q])), k[0]))
            result[r] = best[1:4]
            taken.append(best[4])
            todo.remove(r)
    for r, (ang, sx, sy) in result.items():
        ref = P[r].Reference()
        ref.SetTextAngleDegrees(ang)
        ref.SetPosition(pn.VECTOR2I(pn.FromMM(sx), pn.FromMM(sy)))
        ref.SetVisible(True)
    for r in hidden:
        P[r].Reference().SetVisible(False)
    print(f"labels: {len(B.fps)} designators set to {H:g} mm / {T:g} mm stroke; "
          f"{len(result)} placed next to their part, {len(hidden)} hidden on silk "
          f"(still on Fab as ${{REFERENCE}}): {', '.join(sorted(hidden)) or '-'}")
    if a.dry_run:
        print("(dry run, nothing written)")
        return 0
    backup = pcb.with_suffix(".kicad_pcb.labels-bak")
    shutil.copy2(pcb, backup)
    with quiet_stderr():
        ok = pn.SaveBoard(str(pcb), B.b)
    if not ok:
        die(f"SaveBoard failed; {backup.name} holds the previous board", 4)
    print(f"saved {pcb.name} (previous copy: {backup.name})")
    return 0


# ---------------------------------------------------------------------------
# auto: place supporting parts next to what they connect to
# ---------------------------------------------------------------------------

def cmd_auto(a):
    """spec JSON:
      {"base": "plan.json" (optional: hand-placed parts applied first),
       "step": 0.1,
       "parts": [{"ref": "C306", "side": "F"|"B", "region": [x0, y0, x1, y1],
                  "rots": [0, 90, 180, 270], "pins": {"1": ["U3", "7"]},
                  "anchors": ["U3"], "weights": {"GND": 0.2}}, ...]}
    Parts are placed in order. Cost = sum over the part's pads of weight x
    distance to its target: an explicit pin, else the nearest pad of the same
    net on the anchor parts (or anywhere on the board). Candidates must pass
    the same courtyard / copper / silk / edge / keep-out rules as `check`.
    Writes <spec>.plan.json (base + new) for `check` and `apply`."""
    B = Board(a.board)
    spec = json.loads(Path(a.moves).read_text())
    pn = B.pn
    plan = {}
    if spec.get("base"):
        base_path = Path(spec["base"])
        if not base_path.is_absolute():
            base_path = Path(a.moves).parent / base_path
        plan.update(load_moves(base_path))
        B.apply_moves(plan)
    step = spec.get("step", 0.1)
    avoid = [tuple(v) for v in spec.get("avoid", [])]      # reserved areas (e.g. a BGA's dog-bone via field)
    fixed = set(B.cfg.get("fixed", []))
    overhang = set(B.cfg.get("overhang", []))
    min_cy = B.cfg.get("min_courtyard_gap", 0.03)
    pad_min = max(B.web + 2 * B.mask_exp, B.clearance)
    o = B.outline

    def geometry(f, side, rot):
        """courtyard, pads, silk of footprint f at (0, 0, rot) on side, relative."""
        if (side == "B") != f.IsFlipped():
            f.Flip(f.GetPosition(), pn.FLIP_DIRECTION_LEFT_RIGHT)
        f.SetPosition(pn.VECTOR2I(0, 0))
        f.SetOrientationDegrees(rot)
        return B.courtyard(f), B.pads(f), B.silk(f)

    def shift(bx, dx, dy):
        return (bx[0] + dx, bx[1] + dy, bx[2] + dx, bx[3] + dy)

    placed_info = {}
    for r, f in B.placed().items():
        placed_info[r] = dict(side=B.side(f), box=B.courtyard(f), pads=B.pads(f), silk=B.silk(f))

    missing = [it.get("ref") for it in spec.get("parts", []) if it.get("ref") not in B.fps]
    if missing:
        die(f"spec names parts that are not on the board: {missing}")
    homeless = []
    for item in spec["parts"]:
        r = item["ref"]
        f = B.fps[r]
        side = item.get("side", "F")
        rots = item.get("rots", [0, 90, 180, 270])
        region = item["region"]
        weights = dict(item.get("weights", {}))        # e.g. {"GND": 0.25}: a plane net pulls less
        anchors = item.get("anchors")
        others = {q: v for q, v in placed_info.items() if q != r}
        targets = {}
        geo = {}
        for rot in rots:
            geo[rot] = geometry(f, side, rot)
        for p in geo[rots[0]][1]:
            n = p["net"]
            if not n or n.startswith("unconnected"):
                continue
            pin = item.get("pins", {}).get(p["num"])
            if pin:
                q = placed_info.get(pin[0])
                pts = [pp["pos"] for pp in (q["pads"] if q else []) if pp["num"] == str(pin[1])]
            else:
                pool = [placed_info[q] for q in (anchors or others) if q in placed_info and q != r]
                pts = [pp["pos"] for v in pool for pp in v["pads"] if pp["net"] == n]
            if pts:
                targets[p["num"]] = (pts, weights.get(n.split("/")[-1], weights.get(n, 1.0)))
        # nearby obstacles only
        reach = grow(region, 12.0)
        near = {q: v for q, v in others.items() if v["box"] and rgap(v["box"], reach) < 0}
        near_avoid = [v for v in avoid if rgap(v, reach) < 0]
        # cost first for every candidate (cheap), legality only for the best ones
        best = []
        nx = int(round((region[2] - region[0]) / step)) + 1
        ny = int(round((region[3] - region[1]) / step)) + 1
        for rot in rots:
            cyr, padsr, silkr = geo[rot]
            if cyr is None:
                die(f"{r} has no courtyard")
            tp = [(p["pos"], targets[p["num"]]) for p in padsr if p["num"] in targets]
            for i in range(nx):
                x = region[0] + i * step
                for j in range(ny):
                    y = region[1] + j * step
                    cost = 0.0
                    for (px, py), (pts, w) in tp:
                        ax, ay = px + x, py + y
                        cost += w * min(math.hypot(ax - qx, ay - qy) for qx, qy in pts)
                    best.append((cost, rot, x, y))
        best.sort()
        chosen = None
        for cost, rot, x, y in best:
            cyr, padsr, silkr = geo[rot]
            cb = shift(cyr, x, y)
            if r not in overhang and r not in fixed and (cb[0] < o[0] or cb[1] < o[1] or cb[2] > o[2] or cb[3] > o[3]):
                continue
            if any(v["side"] == side and rgap(cb, v["box"]) < min_cy for v in near.values()):
                continue
            if any(rgap(cb, v) < 0 for v in near_avoid):
                continue
            pads_abs = [dict(p, box=shift(p["box"], x, y), pos=(p["pos"][0] + x, p["pos"][1] + y)) for p in padsr]
            silk_abs = [shift(s, x, y) for s in silkr]
            bad = False
            for p in pads_abs:
                pb = p["box"]
                if p["cu"] and min(pb[0] - o[0], pb[1] - o[1], o[2] - pb[2], o[3] - pb[3]) < B.edge_clr:
                    bad = True
                    break
            if bad or keepout_hits(B, cb, side, r):
                continue
            for q, v in near.items():
                for pp in v["pads"]:                     # a neighbour's plated hole inside this courtyard
                    if pp["hole"]:
                        hb = (pp["pos"][0] - pp["drill"] / 2, pp["pos"][1] - pp["drill"] / 2,
                              pp["pos"][0] + pp["drill"] / 2, pp["pos"][1] + pp["drill"] / 2)
                        if rgap(hb, cb) < 0:
                            bad = True
                            break
                if bad:
                    break
                for p in pads_abs:                       # this part's own holes inside a neighbour's courtyard
                    if p["hole"] and v["box"]:
                        hb = (p["pos"][0] - p["drill"] / 2, p["pos"][1] - p["drill"] / 2,
                              p["pos"][0] + p["drill"] / 2, p["pos"][1] + p["drill"] / 2)
                        if rgap(hb, v["box"]) < 0:
                            bad = True
                            break
                    for pp in v["pads"]:
                        shared = (p["cu"] & pp["cu"]) or (p["hole"] and pp["cu"]) or (pp["hole"] and p["cu"])
                        if shared and not (p["net"] and p["net"] == pp["net"]) and rgap(p["box"], pp["box"]) < pad_min:
                            bad = True
                            break
                        if p["hole"] and pp["hole"]:
                            hh = math.dist(p["pos"], pp["pos"]) - p["drill"] / 2 - pp["drill"] / 2
                            if hh < B.hole_to_hole:
                                bad = True
                                break
                    if bad:
                        break
                if bad:
                    break
                for s in silk_abs:
                    if any((side in pp["cu"] or pp["hole"]) and rgap(s, pp["box"]) < B.mask_exp + B.silk_clr for pp in v["pads"]):
                        bad = True
                        break
                if v["side"] == side:
                    for s in v["silk"]:
                        if any((v["side"] in p["cu"] or p["hole"]) and rgap(s, p["box"]) < B.mask_exp + B.silk_clr for p in pads_abs):
                            bad = True
                            break
                if bad:
                    break
            if not bad:
                chosen = (cost, rot, x, y, cb, pads_abs, silk_abs)
                break
        if chosen is None:
            print(f"{r}: NO legal spot in region {region}")
            homeless.append(r)
            continue
        cost, rot, x, y, cb, pads_abs, silk_abs = chosen
        x, y = round(x, 3), round(y, 3)
        plan[r] = [x, y, rot] + (["B"] if side == "B" else [])
        placed_info[r] = dict(side=side, box=cb, pads=pads_abs, silk=silk_abs)
        print(f"{r:6} -> ({x:7.2f},{y:7.2f},{rot:4}) {side}  cost {cost:6.2f}")
        # leave the footprint where it was chosen, for the next part's obstacles
        geometry(f, side, rot)
        f.SetPosition(pn.VECTOR2I(pn.FromMM(x), pn.FromMM(y)))
    out = Path(a.moves).with_suffix(".plan.json")
    out.write_text(json.dumps(plan, indent=1) + "\n")
    print(f"wrote {out} ({len(plan)} placements)"
          + (f"; NO spot for {', '.join(homeless)}" if homeless else ""))
    return 1 if homeless else 0


def cmd_park(a):
    """Move parts off the board (front side, on a grid east of the outline) so a
    re-placement starts from a clean floor. Parked parts are not 'placed', so no
    legality check applies to them; KiCad must be closed."""
    B = Board(a.board)
    if locks(B.pcb) and not a.dry_run:
        die("KiCad has the board open; close it first", 3)
    keep = set((a.refs or "").split(",")) - {""}
    spec = json.loads(Path(a.moves).read_text()) if a.moves else {}
    keep |= set(spec.get("keep", []))
    # `fixed` parts (mounting holes) and locked footprints stay where they are
    keep |= set(B.cfg.get("fixed", [])) | {r for r, f in B.fps.items() if f.IsLocked()}
    o = B.outline
    n, y, row_h = 0, o[1], 0.0
    x = o[2] + 5.0
    for r, f in sorted(B.fps.items()):
        if r in keep:
            continue
        c = B.courtyard(f)
        if c and c[0] < o[2] + 10 and c[2] > o[0] - 10 and c[1] < o[3] + 10 and c[3] > o[1] - 10:
            if f.IsFlipped():
                f.Flip(f.GetPosition(), B.pn.FLIP_DIRECTION_LEFT_RIGHT)
            f.SetOrientationDegrees(0)
            c = B.courtyard(f)                      # its extent as it now lies
            pos = f.GetPosition()
            px, py = B.mm(pos.x), B.mm(pos.y)
            w, h = c[2] - c[0], c[3] - c[1]
            if n and n % 12 == 0:                   # next row, below the tallest part so far
                x, y, row_h = o[2] + 5.0, y + row_h + 1.0, 0.0
            # put the courtyard's own left/top edge at (x, y): the whole part clears the board
            f.SetPosition(B.pn.VECTOR2I(B.pn.FromMM(px + x - c[0]), B.pn.FromMM(py + y - c[1])))
            x, row_h = x + w + 1.0, max(row_h, h)
            n += 1
    if a.dry_run:
        print(f"(dry run) would park {n} footprints east of the board; {len(keep)} kept in place")
        return 0
    backup = B.pcb.with_suffix(".kicad_pcb.park-bak")
    shutil.copy2(B.pcb, backup)
    with quiet_stderr():
        ok = B.pn.SaveBoard(str(B.pcb), B.b)
    if not ok:
        die("SaveBoard failed", 4)
    print(f"parked {n} footprints east of the board, front side, rotation 0 ({len(keep)} kept in place)")
    return 0


def cmd_drc(a):
    """KiCad's own DRC WITH the schematic-parity test, summarised.

    ⚠ `kicad-cli pcb drc` only tests parity when given --schematic-parity. Without
    it the report's `schematic_parity` list is simply empty, which reads exactly
    like a pass -- and that is how pads left with no net by a scripted footprint
    swap go unnoticed until the router never connects them. Never run the bare
    command; run this.

    Board-only footprints listed under `fixed` in placement.json (the mounting
    holes) are expected as `extra_footprint` and do not fail the run. Anything
    else in the parity list exits 1."""
    import collections
    import subprocess
    import tempfile
    board_dir = Path(a.board)
    pcb = boardsel.one_pcb(board_dir)
    if not pcb.with_suffix(".kicad_sch").exists():
        die(f"{pcb.with_suffix('.kicad_sch').name} is not beside the board: the parity test "
            "cannot run, and a run without it reads exactly like a pass", 4)
    cfg_file = board_dir / "placement.json"
    fixed = set((json.loads(cfg_file.read_text()) if cfg_file.exists() else {}).get("fixed", []))
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "drc.json"
        r = subprocess.run(["kicad-cli", "pcb", "drc", "--schematic-parity", "--format", "json",
                            "-o", str(out), str(pcb)], capture_output=True, text=True)
        if not out.exists():
            die(f"kicad-cli pcb drc failed: {r.stderr.strip()[-300:]}", 4)
        d = json.loads(out.read_text())
    # kicad-cli says on STDERR when it could not build the schematic netlist --
    # and then writes an EMPTY parity list, which is indistinguishable from a pass.
    msg = (r.stderr + r.stdout)
    if "parity" in msg.lower() and ("fail" in msg.lower() or "unable" in msg.lower()):
        die("the schematic-parity test did NOT run: "
            + " ".join(msg.split())[-300:] + " -- fix the schematic (annotate, ERC), then re-run", 4)
    viol = collections.Counter(v["type"] for v in d.get("violations", []))
    print("violations:", dict(viol) or "none")
    print("unconnected:", len(d.get("unconnected_items", [])))
    bad = []
    for v in d.get("schematic_parity", []):
        who = " ".join(i.get("description", "") for i in v.get("items", []))
        ref = who.split()[-1] if who else "?"
        if v["type"] == "extra_footprint" and ref in fixed:
            continue
        bad.append(f"{v['type']}: {v['description'][:90]} [{ref}]")
    n_ok = len(d.get("schematic_parity", [])) - len(bad)
    print(f"schematic parity: {len(bad)} problem(s)"
          + (f" ({n_ok} board-only footprint(s) from placement.json 'fixed' ignored)" if n_ok else ""))
    for b in bad:
        print("  PARITY", b)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["gaps", "check", "apply", "sync", "labels", "auto", "park", "drc"])
    ap.add_argument("moves", nargs="?", help="check/apply: plan JSON")
    ap.add_argument("--board", help="boardN, the folder name or a path (default: the folder "
                    "you are in, else this session's board)")
    ap.add_argument("--refs", help="gaps: only these references; park: references to leave in place")
    ap.add_argument("--dry-run", action="store_true", help="apply / labels / park: write nothing")
    a = ap.parse_args()
    a.board = board_dir_arg(a.board)
    if a.moves:
        a.moves = str(boardsel.in_board(a.board, a.moves))    # as typed, else inside the board folder
    if a.dry_run and a.command not in ("apply", "labels", "park"):
        die(f"--dry-run has no meaning for {a.command} (it writes nothing anyway)")
    if a.command in ("check", "apply", "auto") and not a.moves:
        die(f"{a.command} needs a plan or spec file")
    sys.exit({"gaps": cmd_gaps, "check": cmd_check, "apply": cmd_apply, "sync": cmd_sync,
              "labels": cmd_labels, "auto": cmd_auto, "park": cmd_park, "drc": cmd_drc}[a.command](a))


if __name__ == "__main__":
    main()
