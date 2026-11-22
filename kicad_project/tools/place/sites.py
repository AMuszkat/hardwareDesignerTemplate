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

EXIT LANES.  A via or pad beside a BGA can stand in a ball's exit lane (a ring-1 ball straight out, a
ring-2 ball between two ring-1 lands) long before the escape track exists: no DRC sees it, and the
nearest site is often exactly there (board1 RV61_u1gap: C28's best via shut U1 P26's ring-2 W lane, found
only by route.py check after the plan was written).  So every BGA named in routing/prelayout.json
"exit_lanes" is judged as tools/route/exitlane.py judges it -- its lanes, its geometry, its gate --
and a candidate via (or the part's own pad copper on the BGA's exit layer) that would make a ball fail
that gate is dropped; the count and the balls are printed.  `--lanes FILE` reads the entries from
another file (a prelayout.json, or a bare list of entries), `--no-lanes` turns it off.

ON THE PAD'S AXIS.  The guide's via-link rule (10_layout #48 (3) on board1) wants ONE straight segment
out of the pad's centre into the via's centre along one of the pad's axes, 45 degrees only where no axis
link fits, and never touching a pad by less than its "via to pad gap".  So the candidates are points on
the pad's four axis rays (0.01 mm steps from its centre); only when none of them is legal are the four
diagonals tried, and such a site is marked [45] (none when the guide's "45-degree via link" is "never").
The own-pad distance is at least the via's radius + the guide's gap, which is vialink.py's via-touch test.
The link itself (centre to via, the caller's width or 0.15) keeps the clearance from every pad of another
net, every track and via and the part's other pads: a site whose link crosses foreign copper is no site.
board1 RV61_c54b: the old nearest-point search put C32's vias off its pads' axes, and route.py check's
vialink gate failed each one after the plan was written.  `--no-axis` gives the old any-angle search.

KEEP-OUTS.  A position whose courtyard enters a placement.json keep-out of the part's side ("B", "F",
"both"; a keep-out's "allow" list is honoured) is barred, exactly as `place.py check` bars it
(place.keepout_hits).  The positions it barred that would have ranked ahead of the winner are printed
with the keep-outs' names, so a near site lost to a reservation is a visible choice, not a silent one
(board1 RV61_c54b: C32's two nearest ring sites lie in "pair corridor south of U1 (bottom)" and "DRAM
channel U1-U500").  `--no-keepouts` gives the old behaviour; `--plan` names a part in a keep-out and fails.

RANKING.  `--near REF:PAD` ranks by distance to that pad; `--near REF:NET` (a net's short name) by
distance to the NEAREST of REF's pads on that net -- "the ring site nearest its rail's balls" (board1
10_layout #44).  Default rank: that distance + the via links' lengths; `--rank near`: the distance first
(to 0.01 mm), the links after.  `--box` takes several boxes, ';' separated (a ring band in one run).
`--json OUT` writes every listed position's pads, centres and vias (and their kind) for a gate to read.

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
sys.path.insert(1, str(ROOT / "tools" / "route"))
import exitlane as EL  # noqa: E402  (the lanes and their geometry: imported, never copied)

# The working numbers; configure() sets them from the board (or the CLI).
VIA, DRILL, CLR = 0.6, 0.3, 0.2      # via pad and drill, clearance to foreign copper
OWN = 0.4                            # via centre to its OWN pad: the hole stays outside the pad's mask
                                     # opening with ~0.1 mm of tent.  A smaller number lets the hole
                                     # land in the pad's opening -- a solder thief at assembly.
REACH = 0.9                          # how far a pad may be from its via
GAP = None                           # the guide's "via to pad gap" (via copper to any pad), or None
DIAG = "last resort"                 # the guide's "45-degree via link": last resort / allowed / never
AXIS = True                          # candidates on the pad's axes (then diagonals); False: any angle
AXSTEP = 0.01                        # step along an axis ray, from the pad's centre
LINKW = 0.15                         # the link's width when a caller names none: its copper keeps CLR too


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


def configure(B, via=None, clr=None, own=None, reach=None, gates=None, axis=True):
    """Set VIA/DRILL/CLR/OWN/REACH from the board, then any explicit override.  `gates` (route.load_gates,
    an RBoard's .gates) gives the guide's via-link row: the own-pad distance is then at least the via's
    radius + its gap (vialink.py's via-touch test), and its 45-degree policy.  axis=False: any angle."""
    global VIA, DRILL, CLR, OWN, REACH, GAP, DIAG, AXIS
    r = board_rules(B.dir)
    if r:
        VIA, DRILL, CLR = r
    if via:
        VIA, DRILL = (float(v) for v in str(via).split(","))
    if clr is not None:
        CLR = float(clr)
    vl = (gates or {}).get("via_link") or {}
    GAP = vl.get("gap")
    DIAG = vl.get("diag") or "last resort"
    AXIS = bool(axis)
    OWN = float(own) if own is not None else max(DRILL / 2 + B.mask_exp + 0.1,
                                                 VIA / 2 + GAP if GAP is not None else 0.0)
    if reach is not None:
        REACH = float(reach)
    return VIA, DRILL, CLR, OWN, REACH


def rules_line():
    return (f"via {VIA:g}/{DRILL:g}, clearance {CLR:g}, own-pad {OWN:g}, reach {REACH:g}; "
            + (f"links on the pad's axis, 45 degrees {DIAG}" if AXIS else "links at any angle (--no-axis)")
            + (f", via-to-pad gap {GAP:g}" if GAP is not None else ", no 'via to pad gap' row in the guide"))


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def rect_dist(px, py, L, T, R, B):
    return math.hypot(max(L - px, 0.0, px - R), max(T - py, 0.0, py - B))


class Lanes:
    """The exit lanes of every BGA an "exit_lanes" spec names, judged once on the board as it is
    (exitlane.judge), and the question a search asks of each candidate: would this copper make a ball
    fail its gate?  `held` is a dict {entry index: set of (ball, lane key)} the caller carries across
    one part's pads and vias (two vias that each leave a ring-2 ball one lane can shut it together)."""

    def __init__(self, B, entries, source):
        self.source = source
        self.res = []
        for e in entries:
            try:
                self.res.append(EL.judge_entry(B, e))
            except EL.Usage as ex:
                P.die(f"exit lanes ({source}): {e.get('ref')}: {ex}")
        self.drops = {}                       # "U1 P26" -> candidate via sites dropped for it
        self.pos_drops = {}                   # "U1 P26" -> positions dropped for their pad copper
        self._memo, self._ev = {}, {}

    def header(self):
        return "exit lanes (%s): " % self.source + "; ".join(
            f"{r['ref']} {len(r['lanes'])} lanes on {r['layer']}, gate {r['gate']}, "
            f"{len(r['gate_balls'][r['gate']])} ball(s) already without one" for r in self.res)

    def _keys(self, it, layer_side=None):
        out = {}
        for i, r in enumerate(self.res):
            if layer_side is not None and r["layer"][0] != layer_side:
                continue                      # a pad on the other side is not on the exit layer
            k = EL.blocked_by(r, it)
            if k:
                out[i] = k
        return out

    def via_keys(self, x, y, net, rad):
        m = (round(x, 3), round(y, 3), net, round(rad, 4))
        if m not in self._memo:
            self._memo[m] = self._keys(dict(k="via", net=net, x=x, y=y, r=rad))
        return self._memo[m]

    def pad_keys(self, rect, net, side):
        L, T, R, Bo = rect
        return self._keys(dict(k="pad", net=net, x=(L + R) / 2, y=(T + Bo) / 2,
                               polys=[[(L, T), (R, T), (R, Bo), (L, Bo)]]), side)

    def track_keys(self, a, b, width, net, side):
        return self._keys(dict(k="track", net=net, seg=(a[0], a[1], b[0], b[1]), r=width / 2), side)

    def shut(self, held, new):
        """the balls (as "U1 P26") that fail their gate with held + new blocked, and do not now."""
        out = []
        for i, k in new.items():
            extra = frozenset(held.get(i, set()) | k)
            m = (i, extra)
            if m not in self._ev:
                self._ev[m] = EL.shuts(self.res[i], extra)
            out += [f"{self.res[i]['ref']} {b}" for b in self._ev[m]]
        return out

    @staticmethod
    def add(held, new):
        for i, k in new.items():
            held.setdefault(i, set()).update(k)

    def note(self, balls, pos=False):
        d = self.pos_drops if pos else self.drops
        for b in balls:
            d[b] = d.get(b, 0) + 1

    def summary(self):
        def fmt(d):
            return ", ".join(f"{b} x{n}" for b, n in sorted(d.items(), key=lambda t: (-t[1], t[0])))
        return (f"exit lanes: dropped {sum(self.drops.values())} candidate via site(s)"
                + (f" ({fmt(self.drops)})" if self.drops else "")
                + f" and {sum(self.pos_drops.values())} position(s) for their pad copper"
                + (f" ({fmt(self.pos_drops)})" if self.pos_drops else "")
                + " that would shut a ball's exit lane")


def load_lanes(B, path=None, off=False):
    """Lanes from routing/prelayout.json "exit_lanes" (default), from --lanes FILE, or None."""
    import json
    if off:
        print("exit lanes: off (--no-lanes)")
        return None
    if path:
        f = Path(path)
        if not f.is_absolute() and not f.exists():
            f = Path(B.dir) / f                       # relative to the board, as the spec's paths are
        d = json.loads(f.read_text())
        entries = d.get("exit_lanes") if isinstance(d, dict) else d
        if not isinstance(entries, list) or not all(isinstance(x, dict) and x.get("ref") for x in entries):
            P.die(f"--lanes {path}: wants {{\"exit_lanes\": [{{\"ref\": ...}}]}} or a list of such entries")
        src = str(path)
    else:
        try:
            entries = EL.load_spec(B.dir)
        except EL.Usage as ex:
            P.die(str(ex))
        src = EL.SPEC
        if not entries:
            print(f"exit lanes: none ({EL.SPEC} has no \"exit_lanes\")")
            return None
    if not hasattr(B, "rule_widths"):
        P.die("exit lanes need a route.RBoard (sites.make_board)")
    LN = Lanes(B, entries, src)
    print(LN.header())
    return LN


def make_board(board_dir):
    """route.RBoard (a place.Board with the rule lookups exitlane.py needs)."""
    import route as R
    return R.RBoard(Path(board_dir))


class World:
    """Every pad, track and via on the board, as rectangles and segments."""

    def __init__(self, B, ignore=()):
        mm = B.mm
        self.short = lambda n: n.split("/")[-1]
        self.pads, self.tracks, self.vias, self.court, self.side = [], [], [], {}, {}
        self._memo = {}
        self.lanes, self.lane_drop = None, None   # a Lanes, set by the caller; the last site it dropped
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
                if not p.IsOnCopperLayer():
                    continue                     # a paste or mask aperture is not copper
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

    CELL = 1.0                                             # mm: the obstacle index's grid

    def _index(self):
        """pads, tracks and vias by grid cell -- rebuilt when a caller has added to the lists
        (decap.py feeds its own vias and stubs back in); a query then reads a few cells, not the board."""
        sig = (len(self.pads), len(self.tracks), len(self.vias))
        if getattr(self, "_sig", None) == sig:
            return
        import collections
        self._sig, self._memo = sig, {}
        self._grid = collections.defaultdict(lambda: ([], [], []))
        c = self.CELL

        def put(kind, item, x0, y0, x1, y1):
            for i in range(int(math.floor(x0 / c)), int(math.floor(x1 / c)) + 1):
                for j in range(int(math.floor(y0 / c)), int(math.floor(y1 / c)) + 1):
                    self._grid[(i, j)][kind].append(item)
        for p in self.pads:
            put(0, p, p[3], p[4], p[5], p[6])
        for t in self.tracks:
            (ax, ay), (bx, by), w = t
            put(1, t, min(ax, bx) - w / 2, min(ay, by) - w / 2, max(ax, bx) + w / 2, max(ay, by) + w / 2)
        for v in self.vias:
            put(2, v, v[0] - v[2], v[1] - v[2], v[0] + v[2], v[1] + v[2])

    def _near(self, x, y, r):
        """(pads, tracks, vias) whose cells lie within r of (x, y); an item can come back twice"""
        self._index()
        c = self.CELL
        out = ([], [], [])
        for i in range(int(math.floor((x - r) / c)), int(math.floor((x + r) / c)) + 1):
            for j in range(int(math.floor((y - r) / c)), int(math.floor((y + r) / c)) + 1):
                g = self._grid.get((i, j))
                if g:
                    for k in range(3):
                        out[k].extend(g[k])
        return out

    def via_ok(self, x, y, own_rects=()):
        o = self.outline
        if min(x - o[0], o[2] - x, y - o[1], o[3] - y) - VIA / 2 < self.edge:
            return False                                   # off the board, or inside copper-to-edge
        for L, T, R, Bo in own_rects:                      # never via-in-pad, but its own pad is not foreign
            if rect_dist(x, y, L, T, R, Bo) < OWN:
                return False
        pads, tracks, vias = self._near(x, y, VIA / 2 + CLR + 1e-6)
        for _r, _n, _net, L, T, R, Bo in pads:
            if rect_dist(x, y, L, T, R, Bo) - VIA / 2 < CLR:
                return False
        for a, b, w in tracks:
            if seg_dist((x, y), a, b) - w / 2 - VIA / 2 < CLR:
                return False
        for vx, vy, r in vias:
            if math.dist((x, y), (vx, vy)) - r - VIA / 2 < CLR:
                return False
        return True

    def via_free(self, x, y):
        """via_ok against the BOARD only, memoised -- the obstacles never move (a list a caller grew
        rebuilds the index and clears the memo)."""
        self._index()
        k = (round(x, 4), round(y, 4))
        v = self._memo.get(k)
        if v is None:
            v = self._memo[k] = self.via_ok(x, y)
        return v

    @staticmethod
    def candidates(rect, kind, step=0.05):
        """[(distance from the pad's copper, x, y)] nearest first, within REACH and at least OWN out.
        "axis": the pad's four axis rays from its centre (AXSTEP apart) -- a link from the centre is one
        straight 0/90 segment, vialink.py's axis test (pad_frame's centre is the pad's box centre);
        "45": the four diagonals the same way; "any": the old square grid around the pad (`step`)."""
        L, T, R, Bo = rect
        cx, cy = (L + R) / 2, (T + Bo) / 2
        out = []
        if kind == "any":
            n = int(REACH / step) + 1
            for i in range(-n, n + 1):
                for j in range(-n, n + 1):
                    x, y = round(cx + i * step, 3), round(cy + j * step, 3)
                    d = rect_dist(x, y, L, T, R, Bo)
                    if d <= REACH:
                        out.append((d, x, y))
        else:
            dirs = ((1, 0), (-1, 0), (0, 1), (0, -1)) if kind == "axis" else ((1, 1), (1, -1), (-1, 1), (-1, -1))
            tmax = max(R - L, Bo - T) / 2 * (1.4143 if kind == "45" else 1.0) + REACH * 1.4143
            for dx, dy in dirs:
                for i in range(1, int(tmax / AXSTEP) + 2):
                    t = i * AXSTEP
                    x = cx if dx == 0 else round(cx + dx * t, 4)      # on the centre line exactly
                    y = cy if dy == 0 else round(cy + dy * t, 4)
                    d = rect_dist(x, y, L, T, R, Bo)
                    if d > REACH:
                        break
                    if d >= OWN:
                        out.append((d, x, y))
        out.sort()
        return out

    def link_free(self, rect, x, y, others=(), net=None, lw=None):
        """the straight link from the pad's centre to (x, y), `lw` wide, keeps CLR from every pad of
        another net (a pad of `net` is its own copper), every track and via on the board, and the
        part's other pads (`others`) -- a via site whose link crosses foreign copper is no site
        (axsearch.py's link test, board1 RV61_c54b: C32's nearest axis site linked across U1's copper)."""
        hw = (LINKW if lw is None else lw) / 2
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        n = max(2, int(math.dist((cx, cy), (x, y)) / 0.01) + 1)
        pts = [(cx + (x - cx) * t / n, cy + (y - cy) * t / n) for t in range(n + 1)]
        sn = self.short(net) if net else None
        mx, my = (cx + x) / 2, (cy + y) / 2
        pads, tracks, vias = self._near(mx, my, math.dist((cx, cy), (x, y)) / 2 + hw + CLR + 1e-6)
        for _r, _n, pn, L, T, R, Bo in pads:
            if sn is not None and pn == sn:
                continue
            if any(rect_dist(px, py, L, T, R, Bo) - hw < CLR for px, py in pts):
                return False
        for a, b, w in tracks:
            if any(seg_dist(q, a, b) - w / 2 - hw < CLR for q in pts):
                return False
        for vx, vy, r in vias:
            if seg_dist((vx, vy), (cx, cy), (x, y)) - r - hw < CLR:
                return False
        for L, T, R, Bo in others:
            if any(rect_dist(px, py, L, T, R, Bo) - hw < CLR for px, py in pts):
                return False
        return True

    def nearest_via(self, rect, own_rects, step=0.05, skip=(), net=None, held=None, lw=None):
        """the nearest point outside `rect` where a via is legal, at least
        VIA + CLR from any via already chosen for this part (`skip`), as (d, x, y, kind):
        kind "axis" (on the pad's axis), "45" (a diagonal: only when no axis site is legal,
        and not when the guide says never) or "any" (--no-axis, the old search).

        `rect` is the pad the via belongs to -- same net, so it only has to stay
        OUT of it.  The part's OTHER pads (own_rects) are a different net and get
        the full clearance, which is the bug that let a cap's ground via sit
        0.35 mm from its own power pad and fail KiCad's hole-clearance rule.

        With self.lanes and the pad's full `net`, a site whose via would make a BGA ball fail its
        exit-lane gate (with `held`, what this part already put in lanes) is dropped and counted; the
        chosen site's lane pairs are added to `held`."""
        others = [r for r in own_rects if r != rect]
        kinds = (["axis"] + ([] if DIAG == "never" else ["45"])) if AXIS else ["any"]
        for kind in kinds:
            for d, x, y in self.candidates(rect, kind, step):
                if any(math.dist((x, y), q) < VIA + CLR for q in skip):
                    continue
                if rect_dist(x, y, *rect) < OWN:
                    continue
                if any(rect_dist(x, y, *r) - VIA / 2 < CLR for r in others):
                    continue
                if self.via_free(x, y) and (kind == "any" or self.link_free(rect, x, y, others, net, lw)):
                    if self.lanes and net is not None:
                        k = self.lanes.via_keys(x, y, net, VIA / 2)
                        bad = self.lanes.shut(held if held is not None else {}, k) if k else []
                        if bad:
                            self.lanes.note(bad)
                            if self.lane_drop is None:
                                self.lane_drop = (x, y, bad)
                            continue
                        if held is not None:
                            self.lanes.add(held, k)
                    return (d, x, y, kind)
        return None


def on_axis(rect, x, y, tol=1e-4):
    """'axis', '45' or None: where (x, y) lies seen from the pad's centre"""
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    dx, dy = abs(x - cx), abs(y - cy)
    if dx < tol or dy < tol:
        return "axis"
    return "45" if abs(dx - dy) < tol else None


def vtag(v):
    return " [45]" if len(v) > 3 and v[3] == "45" else ""


def boardsel_path(B, rel):
    return P.boardsel.in_board(B.dir, rel)


def turn_rect(r, dt):
    """rotate a local rect (L, T, R, B, ...) by dt degrees (multiples of 90) about the part's origin"""
    L, T, R, Bo = r[:4]
    for _ in range(int(dt // 90) % 4):
        L, T, R, Bo = T, -R, Bo, -L
    return (min(L, R), min(T, Bo), max(L, R), max(T, Bo)) + tuple(r[4:])


def parse_boxes(s):
    """'x0,y0,x1,y1;x0,...' -> [(x0, y0, x1, y1)]"""
    return [tuple(float(v) for v in q.split(",")) for q in (s or "").split(";") if q.strip()]


class Bars:
    """What bars a position: the placement.json keep-outs of the part's side (place.keepout_hits,
    exactly as `place.py check` judges them; off with --no-keepouts) and the search's own --bar boxes
    (absolute board mm).  hits(rects) -> the names a courtyard enters; counts them for the summary."""

    def __init__(self, B, ref, side, keepouts=True, bars=()):
        self.B, self.ref, self.side, self.on, self.bars = B, ref, side, keepouts, list(bars)
        self.count = {}

    def hits(self, rects):
        out = set()
        for r in rects:
            if self.on:
                out.update(P.keepout_hits(self.B, r[:4], self.side, self.ref))
            for i, (x0, y0, x1, y1) in enumerate(self.bars):
                if r[0] < x1 and x0 < r[2] and r[1] < y1 and y0 < r[3]:
                    out.add(f"--bar {i + 1}")
        out = sorted(out)
        for h in out:
            self.count[h] = self.count.get(h, 0) + 1
        return out

    def summary(self):
        if not self.on and not self.bars:
            return "keep-outs: off (--no-keepouts)"
        return (f"keep-outs ({'placement.json, side ' + self.side if self.on else 'off (--no-keepouts)'}"
                + (f", {len(self.bars)} --bar box(es)" if self.bars else "") + "): barred "
                + (", ".join(f"'{k}' x{n}" for k, n in sorted(self.count.items(), key=lambda t: (-t[1], t[0])))
                   or "nothing") + " (positions whose courtyard enters it)")


def fmt_vias(vs):
    return ", ".join(f"{n}/{net}: via ({v[1]:.3f},{v[2]:.3f}) {v[0]:.2f}{vtag(v)}" for n, net, v, _c in vs)


def json_rec(ref, x, y, rot, side, vs, **kw):
    return dict(ref=ref, at=[x, y], rot=rot, side=side, **kw, pads=[
        dict(pad=n, net=net, centre=[round(c[0], 6), round(c[1], 6)],
             via=None if v is None else [v[1], v[2]], kind=None if v is None else v[3],
             dist=None if v is None else round(v[0], 4)) for n, net, v, c in vs])


def report_plan(B, a, LN=None):
    """Every pad of every part the plan moves, with the nearest legal via site --
    the check `place.py check` cannot make, because it does not know the copper."""
    import json
    mm = B.mm
    plan = P.load_moves(boardsel_path(B, a.plan))     # as place.py reads it: _notes skipped
    bad = lanebad = kobad = 0
    recs = []
    for ref, entry in plan.items():
        x, y, rot = entry[:3]
        want_back = len(entry) > 3 and str(entry[3]).upper().startswith("B")
        # one World per part: its OWN pads are not obstacles to its own vias, but
        # every other part's are -- including the others this plan moves, which
        # are already on the board when the plan is a report of what was applied.
        W = World(B, ignore={ref})
        W.lanes = LN
        W.vias += a.xvias
        f = B.fps.get(ref) or P.die(f"{ref} is not on the board")
        if want_back != f.IsFlipped():
            P.die(f"{ref}: the plan puts it on the {'back' if want_back else 'front'} and the board "
                  "has it on the other side. Via sites are judged for parts where they ARE: "
                  "apply the plan first (place.py apply), then run this report.")
        pos = (mm(f.GetPosition().x), mm(f.GetPosition().y))
        dt = (rot - f.GetOrientationDegrees()) % 360
        rects, nums = [], []
        for p in f.Pads():
            if not p.IsOnCopperLayer():
                continue                             # a paste aperture needs no via
            bb = p.GetBoundingBox()
            r = turn_rect((mm(bb.GetLeft()) - pos[0], mm(bb.GetTop()) - pos[1],
                           mm(bb.GetRight()) - pos[0], mm(bb.GetBottom()) - pos[1]), dt)
            rects.append((r[0] + x, r[1] + y, r[2] + x, r[3] + y))
            nums.append((p.GetNumber(), W.short(p.GetNetname()), p.GetNetname()))
        vs, held, why = [], {}, {}
        side = "B" if want_back else "F"
        BR = Bars(B, ref, side, not a.no_keepouts, a.bars)
        crt = [turn_rect((q[0] - pos[0], q[1] - pos[1], q[2] - pos[0], q[3] - pos[1]), dt)
               for q in B.courtyard_rects(f)]
        ko = BR.hits([(q[0] + x, q[1] + y, q[2] + x, q[3] + y) for q in crt])
        if ko:
            kobad += 1
            print(f"   {ref:5s} ({x:7.2f}, {y:7.2f}) rot {rot:4.0f}  KEEPOUT: its courtyard enters "
                  + ", ".join(f"'{h}'" for h in ko))
        if LN:
            pk = {}
            for k, r in enumerate(rects):
                Lanes.add(pk, LN.pad_keys(r, nums[k][2], side))
            sh = LN.shut({}, pk)
            if sh:
                LN.note(sh, pos=True)
                lanebad += 1
                print(f"   {ref:5s} ({x:7.2f}, {y:7.2f}) rot {rot:4.0f}  LANE: its pad copper shuts {', '.join(sh)}")
            held = pk
        for k, r in enumerate(rects):
            W.lane_drop = None
            v = W.nearest_via(r, rects, skip=[q for q in vs if q], net=nums[k][2], held=held)
            vs.append((v[1], v[2]) if v else None)
            nums[k] = nums[k] + (v,)
            if W.lane_drop:
                why[k] = W.lane_drop
            bad += v is None
        print(f"   {ref:5s} ({x:7.2f}, {y:7.2f}) rot {rot:4.0f}  " +
              ", ".join(f"{nums[k][0]}/{nums[k][1]}: " +
                        (f"via ({vs[k][0]:.3f}, {vs[k][1]:.3f}){vtag(nums[k][3])}" if vs[k] else "NO VIA SITE")
                        + (f" [nearer ({why[k][0]:.3f}, {why[k][1]:.3f}) dropped: shuts {', '.join(why[k][2])}]"
                           if k in why else "")
                        for k in range(len(rects))))
        recs.append(json_rec(ref, x, y, rot, side, [(nums[k][0], nums[k][1], nums[k][3],
                                                     ((rects[k][0] + rects[k][2]) / 2, (rects[k][1] + rects[k][3]) / 2))
                                                    for k in range(len(rects))], keepouts=ko))
    if LN:
        print(LN.summary())
    if a.json:
        Path(a.json).write_text(json.dumps(dict(rules=rules_line(), positions=recs), indent=1) + "\n")
        print(f"wrote {a.json}: {len(recs)} part(s)")
    print("SITES", "PASS" if not bad and not lanebad and not kobad else
          f"FAIL ({bad} pad(s) with no via site, {lanebad} part(s) whose pads shut an exit lane, "
          f"{kobad} part(s) in a keep-out)")
    return 1 if bad or lanebad or kobad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", type=P.board_dir_arg, default=None,
                    help="boardN, the folder name or a path (default: this session's board)")
    ap.add_argument("--ref", help="the part to place (its footprint is read from the board)")
    ap.add_argument("--box", help="x0,y0,x1,y1 to search; several boxes ';' separated")
    ap.add_argument("--plan", help="instead: a placement plan {REF: [x, y, rot]} -- report each pad's via")
    ap.add_argument("--near", help="REF:PAD, or REF:NET (the nearest of REF's pads on that net) -- rank by "
                    "distance to it (e.g. U1:5, U1:VDD_SOC)")
    ap.add_argument("--rank", choices=("sum", "near"), default="sum",
                    help="sum (default): distance + via links; near: the distance first (to 0.01 mm), links after")
    ap.add_argument("--via", help="pad,drill of the via (default: the Default class's)")
    ap.add_argument("--clr", type=float, help="clearance to foreign copper (default: the largest class clearance)")
    ap.add_argument("--own", type=float, help="via centre to its own pad (default: the larger of drill/2 + mask "
                    "expansion + 0.1 and the via's radius + the guide's via-to-pad gap)")
    ap.add_argument("--reach", type=float, help="how far from its pad a via may be (default 0.9)")
    ap.add_argument("--rot", default="0,90", help="ABSOLUTE rotations to try, as a plan writes them")
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--gap", type=float, default=None, help="courtyard gap (default: placement.json)")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--lanes", help="exit-lane entries from this file (a prelayout.json or a list of entries; "
                    "default: the board's routing/prelayout.json \"exit_lanes\")")
    ap.add_argument("--no-lanes", action="store_true", help="do not drop sites that shut a BGA's exit lane")
    ap.add_argument("--no-axis", action="store_true", help="the old search: a via at any angle from its pad")
    ap.add_argument("--no-keepouts", action="store_true", help="do not bar placement.json keep-outs")
    ap.add_argument("--bar", default="", help="x0,y0,x1,y1;... -- more boxes a courtyard may not enter (absolute mm)")
    ap.add_argument("--extra-via", default="", help="x,y;... -- planned vias (the board's via size) as obstacles")
    ap.add_argument("--json", help="write the listed positions (pads, centres, vias, kinds) to this file")
    a = ap.parse_args()
    a.board = a.board or P.board_dir_arg(None)
    B = make_board(a.board)
    mm = B.mm
    configure(B, a.via, a.clr, a.own, a.reach, gates=getattr(B, "gates", None), axis=not a.no_axis)
    print(rules_line())
    a.bars = parse_boxes(a.bar)
    a.xvias = [(x, y, VIA / 2) for x, y in (tuple(float(v) for v in q.split(","))
                                            for q in a.extra_via.split(";") if q.strip())]
    LN = load_lanes(B, a.lanes, a.no_lanes)
    if a.plan:
        return report_plan(B, a, LN)
    if not a.ref or not a.box:
        P.die("give --ref and --box (or --plan)")
    f = B.fps.get(a.ref) or P.die(f"{a.ref} is not on the board")
    if a.ref not in B.fps or B.courtyard(f) is None:
        P.die(f"{a.ref} has neither a courtyard nor pads to place by")
    gap = a.gap if a.gap is not None else B.cfg.get("min_courtyard_gap", 0.03)
    W = World(B, ignore={a.ref})
    W.lanes = LN
    W.vias += a.xvias
    side = W.side[a.ref]
    court = W.court[a.ref]
    BR = Bars(B, a.ref, side, not a.no_keepouts, a.bars)
    pos = (mm(f.GetPosition().x), mm(f.GetPosition().y))
    rot0 = f.GetOrientationDegrees()
    # the part's pads and courtyard, in local coordinates at its current rotation
    local = []
    for p in f.Pads():
        if not p.IsOnCopperLayer():
            continue                                 # a paste aperture needs no via
        bb = p.GetBoundingBox()
        local.append((mm(bb.GetLeft()) - pos[0], mm(bb.GetTop()) - pos[1],
                      mm(bb.GetRight()) - pos[0], mm(bb.GetBottom()) - pos[1], p.GetNumber(),
                      W.short(p.GetNetname()), p.GetNetname()))
    lc = (court[0] - pos[0], court[1] - pos[1], court[2] - pos[0], court[3] - pos[1])
    lcr = [(q[0] - pos[0], q[1] - pos[1], q[2] - pos[0], q[3] - pos[1]) for q in B.courtyard_rects(f)]

    targets = []
    if a.near:
        tr, tp = a.near.split(":")
        tf = B.fps.get(tr) or P.die(f"--near {a.near}: {tr} is not on the board")
        pads_ = list(tf.Pads())
        hit = [p for p in pads_ if p.GetNumber() == tp] or [p for p in pads_ if W.short(p.GetNetname()) == tp]
        if not hit:
            P.die(f"--near {a.near}: {tr} has no pad numbered {tp} and no pad on a net named {tp}")
        targets = [(mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in hit]
    near_d = (lambda x, y: min(math.dist((x, y), t) for t in targets)) if targets else (lambda x, y: 0.0)

    def key(d, vs):
        """(45-degree links, rank): a "last resort" 45 ranks a position after every all-axis one
        (#48 (3): 45 only where no axis link fits -- and one fits where the part can sit elsewhere)"""
        links = sum(v[2][0] for v in vs)
        n45 = sum(1 for v in vs if v[2][3] == "45") if DIAG == "last resort" else 0
        return (n45,) + ((round(d, 2), round(links, 4)) if a.rank == "near" else (round(d + links, 6),))

    # only courtyards on the part's OWN side are in its way
    others = [(r, c) for r, c in W.court.items() if r != a.ref and W.side[r] == W.side[a.ref]]
    ol = B.outline if a.ref not in set(B.cfg.get("overhang", [])) | set(B.cfg.get("fixed", [])) \
        else (-1e9, -1e9, 1e9, 1e9)

    def evaluate(x, y, pads, rects):
        """[(pad, net, via, centre)] for every pad, or None"""
        held = {}
        if LN:
            for k, r in enumerate(rects):
                Lanes.add(held, LN.pad_keys(r, pads[k][6], side))
            sh = LN.shut({}, held)
            if sh:
                LN.note(sh, pos=True)
                return None
        vs = []
        for k, r in enumerate(rects):
            v = W.nearest_via(r, rects, skip=[(q[2][1], q[2][2]) for q in vs], net=pads[k][6], held=held)
            if v is None:
                return None
            vs.append((pads[k][4], pads[k][5], v, ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)))
        return vs

    out, barred = [], []
    for rot in (float(v) for v in a.rot.split(",")):
        dt = (rot - rot0) % 360
        pads = [turn_rect(r, dt) for r in local]
        c = turn_rect(lc, dt)[:4]
        crs = [turn_rect(q, dt)[:4] for q in lcr]
        for x0, y0, x1, y1 in parse_boxes(a.box):
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
                    ko = BR.hits([(q[0] + x, q[1] + y, q[2] + x, q[3] + y) for q in crs])
                    if ko:
                        barred.append((near_d(x, y), x, y, rot, pads, rects, ko))
                        continue
                    vs = evaluate(x, y, pads, rects)
                    if vs is None:
                        continue
                    d = near_d(x, y)
                    out.append((key(d, vs), x, y, rot, d, vs))
    out.sort(key=lambda t: t[:4])
    # the barred positions that would have ranked ahead of the winner: evaluated nearest first, and only
    # while they can still beat it (a key's first term is never below its distance)
    # (the best one per keep-out, or set of them, so each reservation that cost a nearer site is named)
    nearer, seen = [], set()
    if out and barred:
        wk = out[0][0]
        for d, x, y, rot, pads, rects, ko in sorted(barred, key=lambda t: t[:4]):
            if (round(d, 2) if a.rank == "near" else d) > wk[1]:
                break
            if tuple(ko) in seen:
                continue
            vs = evaluate(x, y, pads, rects)
            if vs is not None and key(d, vs) < wk:
                seen.add(tuple(ko))
                nearer.append((key(d, vs), x, y, rot, d, vs, ko))
                if len(nearer) >= a.top:
                    break
        nearer.sort(key=lambda t: t[:4])
    print(f"{a.ref} ({f.GetValue()}), now at ({pos[0]:.2f}, {pos[1]:.2f}) rot {rot0:.0f}; "
          f"{len(out)} positions in the box where every pad reaches a via"
          + (f"; ranked by distance to {a.near}" + (" first" if a.rank == "near" else " + links") if a.near else ""))
    if LN:
        print(LN.summary())
    print(BR.summary())
    if not AXIS:
        print("links: any angle (--no-axis) -- a site off the pad's axis fails the guide's via-link rule")
    else:
        n45 = sum(1 for t in out[:a.top] for q in t[5] if q[2][3] == "45")
        print(f"links: every via on its pad's axis" + (f", {n45} on a 45-degree diagonal (no axis site: list "
                                                       "them in the group's report)" if n45 else ""))
    for _k, x, y, rot, d, vs, ko in nearer:
        print(f"   barred ({x:7.2f}, {y:7.2f}) rot {rot:5.0f}" + (f"  {d:5.2f} mm from {a.near}" if targets else "")
              + f"   in {', '.join(repr(h) for h in ko)}   " + fmt_vias(vs) + "   (the nearest it barred)")
    for _k, x, y, rot, d, vs in out[:a.top]:
        print(f"   ({x:7.2f}, {y:7.2f}) rot {rot:5.0f}" + (f"  {d:5.2f} mm from {a.near}" if targets else "")
              + "   " + fmt_vias(vs))
    if a.json:
        import json
        recs = [json_rec(a.ref, x, y, rot, side, vs, dist_near=round(d, 4)) for _k, x, y, rot, d, vs in out[:a.top]]
        recs += [json_rec(a.ref, x, y, rot, side, vs, dist_near=round(d, 4), barred=ko)
                 for _k, x, y, rot, d, vs, ko in nearer]
        Path(a.json).write_text(json.dumps(dict(rules=rules_line(), positions=recs), indent=1) + "\n")
        print(f"wrote {a.json}: {len(recs)} position(s)")
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
