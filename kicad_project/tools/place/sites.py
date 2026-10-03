#!/usr/bin/env python3
"""Where can a part go so that EVERY PAD CAN REACH A VIA?

    tools/kpy tools/place/sites.py --board board1 --ref C13 \
        --box 131,112.5,140,115 --near U1:5 --rot 0,90
    tools/kpy tools/place/sites.py --board board1 --plan moves.json

A decoupling cap is only decoupling if each of its pads has a via beside it: the
power pad down to its pour, the ground pad down to the ground plane.  A position
that passes `place.py check` can still be useless -- the copper already laid can
leave a pad with nowhere to drop, and a strip of caps over a layer stack another
bus already owns decouples nothing.

For every candidate position this prints the nearest legal via site per pad.  A
site is legal when it is clear on EVERY layer -- the clearance to foreign pads,
tracks and vias, and outside its own pad (no via-in-pad unless the fab sells it)
-- so the search sees exactly what the board is, not what the schematic wishes.

The via and the clearances default to the board's own: the Default net class's
via from the .kicad_pro, and the LARGEST class clearance (KiCad enforces the
stricter of two nets' classes, and a decoupling via sits beside every kind).
`--via 0.6,0.3`, `--clr`, `--own`, `--reach` override them.

It does not move anything: it prints positions to put in a plan for
`place.py check`, which is still what judges courtyards, silk and the rest.
"""

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "place"))
import place as P  # noqa: E402

# The working numbers; configure() sets them from the board (or the CLI).
VIA, DRILL, CLR = 0.6, 0.3, 0.2      # via pad and drill, clearance to foreign copper
OWN = 0.4                            # via centre to its OWN pad: the hole stays outside the pad's mask
                                     # opening with ~0.1 mm of tent.  A smaller number lets the hole
                                     # land in the pad's opening -- a solder thief at assembly.
REACH = 0.9                          # how far a pad may be from its via


def board_rules(board_dir):
    """(via pad, via drill, largest class clearance) from the .kicad_pro, or None."""
    import json
    pro = next(Path(board_dir).glob("*.kicad_pro"), None)
    if not pro:
        return None
    classes = json.loads(pro.read_text()).get("net_settings", {}).get("classes", [])
    d = next((c for c in classes if c.get("name") == "Default"), None)
    if not d or not d.get("via_diameter"):
        return None
    clr = max([c.get("clearance") or 0.0 for c in classes] + [d.get("clearance") or 0.0])
    return float(d["via_diameter"]), float(d.get("via_drill") or d["via_diameter"] / 2), float(clr)


def configure(B, via=None, clr=None, own=None, reach=None):
    """Set VIA/DRILL/CLR/OWN/REACH from the board, then any explicit override."""
    global VIA, DRILL, CLR, OWN, REACH
    r = board_rules(B.dir)
    if r:
        VIA, DRILL, CLR = r
    if via:
        VIA, DRILL = (float(v) for v in str(via).split(","))
    if clr is not None:
        CLR = float(clr)
    OWN = float(own) if own is not None else DRILL / 2 + B.mask_exp + 0.1
    if reach is not None:
        REACH = float(reach)
    return VIA, DRILL, CLR, OWN, REACH


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def rect_dist(px, py, L, T, R, B):
    return math.hypot(max(L - px, 0.0, px - R), max(T - py, 0.0, py - B))


class World:
    """Every pad, track and via on the board, as rectangles and segments."""

    def __init__(self, B, ignore=()):
        mm = B.mm
        self.short = lambda n: n.split("/")[-1]
        self.pads, self.tracks, self.vias, self.court, self.side = [], [], [], {}, {}
        self._memo = {}
        self.outline, self.edge = B.outline, B.edge_clr     # a via is copper: it keeps copper-to-edge
        import pcbnew
        for ref, f in B.fps.items():
            c = B.courtyard(f)
            if c:                                # a circular courtyard counts by its box
                self.court[ref] = c[:4]
                self.side[ref] = B.side(f)
            if ref in ignore:
                continue
            for p in f.Pads():
                bb = p.GetBoundingBox()
                self.pads.append((ref, p.GetNumber(), self.short(p.GetNetname()),
                                  mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())))
        for t in B.b.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                self.vias.append((mm(t.GetPosition().x), mm(t.GetPosition().y),
                                  mm(t.GetBoundingBox().GetWidth()) / 2))
            else:
                self.tracks.append(((mm(t.GetStart().x), mm(t.GetStart().y)),
                                    (mm(t.GetEnd().x), mm(t.GetEnd().y)), mm(t.GetWidth())))

    def via_ok(self, x, y, own_rects=()):
        o = self.outline
        if min(x - o[0], o[2] - x, y - o[1], o[3] - y) - VIA / 2 < self.edge:
            return False                                   # off the board, or inside copper-to-edge
        for L, T, R, Bo in own_rects:                      # never via-in-pad, but its own pad is not foreign
            if rect_dist(x, y, L, T, R, Bo) < OWN:
                return False
        for _r, _n, _net, L, T, R, Bo in self.pads:
            if rect_dist(x, y, L, T, R, Bo) - VIA / 2 < CLR:
                return False
        for a, b, w in self.tracks:
            if seg_dist((x, y), a, b) - w / 2 - VIA / 2 < CLR:
                return False
        for vx, vy, r in self.vias:
            if math.dist((x, y), (vx, vy)) - r - VIA / 2 < CLR:
                return False
        return True

    def via_free(self, x, y):
        """via_ok against the BOARD only, memoised -- the obstacles never move."""
        k = (round(x, 2), round(y, 2))
        v = self._memo.get(k)
        if v is None:
            v = self._memo[k] = self.via_ok(k[0], k[1])
        return v

    def nearest_via(self, rect, own_rects, step=0.05, skip=()):
        """the nearest point outside `rect` where a via is legal, at least
        VIA + CLR from any via already chosen for this part (`skip`).

        `rect` is the pad the via belongs to -- same net, so it only has to stay
        OUT of it.  The part's OTHER pads (own_rects) are a different net and get
        the full clearance, which is the bug that let a cap's ground via sit
        0.35 mm from its own power pad and fail KiCad's hole-clearance rule."""
        L, T, R, Bo = rect
        cx, cy = (L + R) / 2, (T + Bo) / 2
        n = int(REACH / step) + 1
        cand = []
        for i in range(-n, n + 1):
            for j in range(-n, n + 1):
                x, y = round(cx + i * step, 3), round(cy + j * step, 3)
                d = rect_dist(x, y, L, T, R, Bo)
                if d <= REACH:
                    cand.append((d, x, y))
        cand.sort()
        others = [r for r in own_rects if r != rect]
        for d, x, y in cand:
            if any(math.dist((x, y), q) < VIA + CLR for q in skip):
                continue
            if rect_dist(x, y, *rect) < OWN:
                continue
            if any(rect_dist(x, y, *r) - VIA / 2 < CLR for r in others):
                continue
            if self.via_free(x, y):
                return (d, x, y)
        return None


def boardsel_path(B, rel):
    return P.boardsel.in_board(B.dir, rel)


def report_plan(B, a):
    """Every pad of every part the plan moves, with the nearest legal via site --
    the check `place.py check` cannot make, because it does not know the copper."""
    import json
    mm = B.mm
    plan = P.load_moves(boardsel_path(B, a.plan))     # as place.py reads it: _notes skipped
    bad = 0
    for ref, entry in plan.items():
        x, y, rot = entry[:3]
        want_back = len(entry) > 3 and str(entry[3]).upper().startswith("B")
        # one World per part: its OWN pads are not obstacles to its own vias, but
        # every other part's are -- including the others this plan moves, which
        # are already on the board when the plan is a report of what was applied.
        W = World(B, ignore={ref})
        f = B.fps.get(ref) or P.die(f"{ref} is not on the board")
        if want_back != f.IsFlipped():
            P.die(f"{ref}: the plan puts it on the {'back' if want_back else 'front'} and the board "
                  "has it on the other side. Via sites are judged for parts where they ARE: "
                  "apply the plan first (place.py apply), then run this report.")
        pos = (mm(f.GetPosition().x), mm(f.GetPosition().y))
        dt = (rot - f.GetOrientationDegrees()) % 360
        rects, nums = [], []
        for p in f.Pads():
            bb = p.GetBoundingBox()
            r = (mm(bb.GetLeft()) - pos[0], mm(bb.GetTop()) - pos[1],
                 mm(bb.GetRight()) - pos[0], mm(bb.GetBottom()) - pos[1])
            for _ in range(int(dt // 90) % 4):
                r = (r[1], -r[2], r[3], -r[0])
            r = (min(r[0], r[2]) + x, min(r[1], r[3]) + y, max(r[0], r[2]) + x, max(r[1], r[3]) + y)
            rects.append(r)
            nums.append((p.GetNumber(), W.short(p.GetNetname())))
        vs = []
        for k, r in enumerate(rects):
            v = W.nearest_via(r, rects, skip=[q for q in vs if q])
            vs.append((v[1], v[2]) if v else None)
            bad += v is None
        print(f"   {ref:5s} ({x:7.2f}, {y:7.2f}) rot {rot:4.0f}  " +
              ", ".join(f"{nums[k][0]}/{nums[k][1]}: " +
                        (f"via ({vs[k][0]:.2f}, {vs[k][1]:.2f})" if vs[k] else "NO VIA SITE")
                        for k in range(len(rects))))
    print("SITES", "PASS" if not bad else f"FAIL ({bad} pad(s) with no via site)")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", type=P.board_dir_arg, default=None,
                    help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--ref", help="the part to place (its footprint is read from the board)")
    ap.add_argument("--box", help="x0,y0,x1,y1 to search")
    ap.add_argument("--plan", help="instead: a placement plan {REF: [x, y, rot]} -- report each pad's via")
    ap.add_argument("--near", help="REF:PAD -- rank by distance to this pad (e.g. U1:5)")
    ap.add_argument("--via", help="pad,drill of the via (default: the Default class's)")
    ap.add_argument("--clr", type=float, help="clearance to foreign copper (default: the largest class clearance)")
    ap.add_argument("--own", type=float, help="via centre to its own pad (default: drill/2 + mask expansion + 0.1)")
    ap.add_argument("--reach", type=float, help="how far from its pad a via may be (default 0.9)")
    ap.add_argument("--rot", default="0,90", help="ABSOLUTE rotations to try, as a plan writes them")
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--gap", type=float, default=None, help="courtyard gap (default: placement.json)")
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    a.board = a.board or P.board_dir_arg(None)
    B = P.Board(a.board)
    mm = B.mm
    configure(B, a.via, a.clr, a.own, a.reach)
    print(f"via {VIA:g}/{DRILL:g}, clearance {CLR:g}, own-pad {OWN:g}, reach {REACH:g}")
    if a.plan:
        return report_plan(B, a)
    if not a.ref or not a.box:
        P.die("give --ref and --box (or --plan)")
    f = B.fps.get(a.ref) or P.die(f"{a.ref} is not on the board")
    if a.ref not in B.fps or B.courtyard(f) is None:
        P.die(f"{a.ref} has neither a courtyard nor pads to place by")
    gap = a.gap if a.gap is not None else B.cfg.get("min_courtyard_gap", 0.03)
    W = World(B, ignore={a.ref})
    court = W.court[a.ref]
    pos = (mm(f.GetPosition().x), mm(f.GetPosition().y))
    rot0 = f.GetOrientationDegrees()
    # the part's pads and courtyard, in local coordinates at its current rotation
    local = []
    for p in f.Pads():
        bb = p.GetBoundingBox()
        local.append((mm(bb.GetLeft()) - pos[0], mm(bb.GetTop()) - pos[1],
                      mm(bb.GetRight()) - pos[0], mm(bb.GetBottom()) - pos[1], p.GetNumber(),
                      W.short(p.GetNetname())))
    lc = (court[0] - pos[0], court[1] - pos[1], court[2] - pos[0], court[3] - pos[1])

    def turn(r, dt):
        """rotate a local rect by dt degrees (multiples of 90)"""
        L, T, R, Bo = r[:4]
        for _ in range(int(dt // 90) % 4):
            L, T, R, Bo = T, -R, Bo, -L
        return (min(L, R), min(T, Bo), max(L, R), max(T, Bo)) + tuple(r[4:])

    target = None
    if a.near:
        tr, tp = a.near.split(":")
        for p in B.fps[tr].Pads():
            if p.GetNumber() == tp:
                target = (mm(p.GetPosition().x), mm(p.GetPosition().y))
    x0, y0, x1, y1 = (float(v) for v in a.box.split(","))
    # only courtyards on the part's OWN side are in its way
    others = [(r, c) for r, c in W.court.items() if r != a.ref and W.side[r] == W.side[a.ref]]
    ol = B.outline if a.ref not in set(B.cfg.get("overhang", [])) | set(B.cfg.get("fixed", [])) \
        else (-1e9, -1e9, 1e9, 1e9)
    out = []
    for rot in (float(v) for v in a.rot.split(",")):
        dt = (rot - rot0) % 360
        pads = [turn(r, dt) for r in local]
        c = turn(lc + (0, 0), dt)[:4]
        nx = int(round((x1 - x0) / a.step)) + 1
        ny = int(round((y1 - y0) / a.step)) + 1
        for i in range(nx):
            for j in range(ny):
                x, y = round(x0 + i * a.step, 3), round(y0 + j * a.step, 3)
                cc = (c[0] + x, c[1] + y, c[2] + x, c[3] + y)
                if cc[0] < ol[0] or cc[1] < ol[1] or cc[2] > ol[2] or cc[3] > ol[3]:
                    continue                                 # courtyard off the board
                if any(cc[0] < o[2] + gap and o[0] < cc[2] + gap and cc[1] < o[3] + gap and o[1] < cc[3] + gap
                       for _r, o in others):
                    continue
                rects = [(p[0] + x, p[1] + y, p[2] + x, p[3] + y) for p in pads]
                vs, ok = [], True
                for k, r in enumerate(rects):
                    v = W.nearest_via(r, rects, skip=[(q[2][1], q[2][2]) for q in vs])
                    if v is None:
                        ok = False
                        break
                    vs.append((pads[k][4], pads[k][5], v))
                if not ok:
                    continue
                d = math.dist((x, y), target) if target else 0.0
                out.append((d + sum(v[2][0] for v in vs), x, y, rot, d, vs))
    out.sort()
    print(f"{a.ref} ({f.GetValue()}), now at ({pos[0]:.2f}, {pos[1]:.2f}) rot {rot0:.0f}; "
          f"{len(out)} positions in the box where every pad reaches a via"
          + (f"; ranked by distance to {a.near}" if a.near else ""))
    for score, x, y, rot, d, vs in out[:a.top]:
        print(f"   ({x:7.2f}, {y:7.2f}) rot {rot:5.0f}" + (f"  {d:5.2f} mm from {a.near}" if target else "")
              + "   " + ", ".join(f"{n}/{net}: via ({v[1]:.2f},{v[2]:.2f}) {v[0]:.2f}" for n, net, v in vs))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
