#!/usr/bin/env python3
"""geom.py -- the measurements a PCB reviewer keeps rewriting, on a snapshot's geometry.json.

    python3 tools/review/geom.py <snapshot | geometry.json> <query> ...

READ ONLY, plain python3, no pcbnew: everything is computed from the one dump
snapshot.py wrote (tools/review/dump.py: tracks, arcs, vias, pads, zones with
their fills, texts, the outline -- all mm, KiCad board coordinates, y down).
A reviewer runs a query instead of writing a script; a measurement this file
cannot answer is written as a script in tmpWork/ and NAMED in the report's
"Measured by hand", so it is added here.

    info                         what the dump holds: counts, layers, the board's rules
    show     SEL                 the selected objects, one row each
    nearest  SEL [--within G]    per selected object, the nearest FOREIGN copper and the gap
    gap      SEL SEL             the smallest gap between two selections
    label    [REF ...]           a reference label: its own part's nearest pad against any other part's
    joints   NET | --all-nets    the joint angles of a net's track chain; not 0 / 45 / 90 is flagged
    via-pad  [SEL] [--below G]   each via's gap to the nearest pad, its own net's and a foreign one
    holes    [--below G]         the hole-to-hole gaps (vias and drilled pads), smallest first
    copper   NET [--layer L]     a net's copper per layer: length, segments, widths, vias
    lengths  [NETS] [--max-width W] [--box B] [--layer L,L] [--over D]
                                 every net's track length, per layer and summed (a neck's whole length; a box's share)
    conn     NET                 is the net one piece: its pieces, the pads in each, its loose track ends
    stitch   SEL --to NET [--over D]   per via, the nearest via of NET, centre to centre
    coupled  SEL --within G      the length a selection's tracks run nearer than G to each foreign net, same layer
    overlap  SEL [--adjacent L,L] [--margin M]   the length its tracks lie over / under foreign copper of other layers
    probe    X,Y [--radius R] [--drill D] [--net N]   what a disc at a point would meet on each layer, hole and edge
    box      X0,Y0,X1,Y1         every object in a rectangle
    bare     REF [REF ...] [--net N,N]   the pads of a part that no copper of their net reaches (a ball with no stub, no via, no pour)
    unused-pads REF | X0,Y0,X1,Y1   the vias in a part's courtyard (or a rectangle): which drop their unconnected inner
                                 pads (KiCad's 'remove unconnected layers') and which keep a pad on every layer
    links    REF [REF ...]       each pad's link to a via of its net: its pieces' angles to the pad's axes, its shape
                                 (axis / 45 / axis+45 / bent / angle / via in pad), the start's offset from the pad's centre
    ring     REF NET [--parts P]  a rail's capacitors at a BGA: each under its courtyard or on the ring outside it, its
                                 rail pad's distance to the rail's nearest ball, and how near the courtyard edge the
                                 rail's balls come (the bound on the nearest ring site)
    neck     NET FROM TO --layer L [--res R]   a pour's neck: the least total copper width across any cut of NET's
                                 copper on L between the copper in box FROM (feed vias) and box TO (a ball field), and
                                 where the cut runs -- a width-for-current rule on a plane, which no track rule sees
    lanes    REF [--power P,P] [--rings 1|2] [--track W] [--clear C] [--reach MM]
                                 a BGA's outer signal balls' exit lanes on their own layer (ring 1 straight out, ring 2
                                 between two ring-1 lands) to the courtyard line, and what foreign copper stands in them

--plan FILE adds a routing plan's copper (route.py's JSON: tracks, vias) to the
board as kinds plan-track / plan-via, selected by plan:; --plan-only makes
nearest measure against the plan's copper alone (a board via against a ripped
group's reviewed lanes).

SEL -- what a query is about:

    U1.A5          a pad            U1             every pad of a part
    net:NAME       a net's tracks, arcs, vias and copper shapes (not its pads)
    pads:NAME      a net's pads
    added:[NAME]   the tracks, arcs, vias and shapes CHANGES.md lists as added (of that net) -- a snapshot folder only
    via:X,Y        the via whose centre is nearest that point (within 0.5 mm)
    plan:[NAME]    the copper --plan FILE added (of that net)
    track:X,Y      the track or arc whose copper is nearest that point (within 0.5 mm)
    uuid:PREFIX    the track, arc, via or zone whose uuid starts so
    box:X0,Y0,X1,Y1   the copper touching that rectangle

NAME is the net's full name, or its last path component when that is unique
("/cpu/DDR_A3" answers to DDR_A3); with * or ? it is a pattern for several
nets (net:DDR_DQ*).  --layer L narrows any query to one copper layer.  --json
prints the rows as JSON instead of a table.

BEFORE AND AFTER: the same query on the base snapshot (CHANGES.md names it in
its first line; it is the folder beside this one).  Several queries in one
shell call, `;` between them, cost one turn.

THE DEFINITIONS (each function's docstring repeats its own):

  * Every copper object is a CORE grown by a radius r: a track is its centre
    line grown by half its width, a via its centre grown by half its pad, a
    round pad its centre grown by its radius, an oval pad the line between
    its two arc centres grown by half its narrow side, a rounded rectangle
    the inner rectangle grown by its corner radius, a rectangle itself with
    r = 0.  These are exact.  A custom, chamfered or trapezoid pad is KiCad's
    own outline polygon (curves within 0.002 mm); an arc track is a chain of
    chords within 0.001 mm of it; a pour is its filled polygons, as refilled
    by dump.py; a copper shape (a polygon, rectangle or line drawn on a copper
    layer, with or without a net) is KiCad's outline of it.
  * ALONG A TRACK (coupled, overlap): the track's centre line is cut into
    stations --step apart (0.01 mm), each standing for its piece of the
    length; a station counts when the disc of the track's width there is
    nearer than the limit to the other copper.  A length is right to about
    one step per place where it starts or stops.
  * GAP is edge to edge: the distance between two cores less both radii, and
    0.0 when the two touch or overlap (never negative).  CENTRE distances are
    named "centre".  Two objects are compared only on a copper layer both are
    on; a via is on every layer of its span, with its pad where KiCad flashes
    one and its bare hole (kind "via-hole", r = drill / 2) where it does not; a
    plated through pad likewise ("pad-hole").
  * FOREIGN is another net.  An object with no net is foreign to everything;
    a non-plated hole is listed as kind "hole" (its wall, not copper).
  * A number here is geometry, not a verdict: the rule it is held to is the
    handoff's (14_fab.md, rules.json) and KiCad's DRC is the gate.
"""
import argparse
import fnmatch
import json
import math
import re
import sys
from pathlib import Path

CELL = 1.0                 # mm, the search grid
SNAP = 0.002               # mm: two track ends this close are one joint (route.py's angle gate uses the same)
ANGLE_TOL = 0.5            # degrees
INF = float("inf")


# -- plane geometry -----------------------------------------------------------

def pt_seg(p, a, b):
    """(distance from point p to segment ab, the closest point on ab)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if not L2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    q = (a[0] + t * dx, a[1] + t * dy)
    return math.hypot(q[0] - p[0], q[1] - p[1]), q


def seg_seg(a, b, c, d):
    """(distance between segments ab and cd, the closest point on ab, on cd); 0.0 where they
    cross or touch.  Either may be a point (a == b)."""
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    if ((o1 > 0 > o2) or (o1 < 0 < o2)) and ((o3 > 0 > o4) or (o3 < 0 < o4)):
        t = o3 / (o3 - o4)
        x = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        return 0.0, x, x
    best = (INF, None, None)
    for p, s, e, own in ((a, c, d, True), (b, c, d, True), (c, a, b, False), (d, a, b, False)):
        dist, q = pt_seg(p, s, e)
        if dist < best[0]:
            best = (dist, p, q) if own else (dist, q, p)
    return best


def in_polygon(p, pts):
    """Ray casting: True when p is strictly inside the closed polygon (a point on the boundary
    may read either way -- callers measure the distance to the boundary first)."""
    x, y, inside = p[0], p[1], False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def turn(u, v):
    """The turn, in degrees, between two unit vectors LEAVING a joint: 0 = the copper runs
    straight on, 45 = a 45-degree bend, 90 = a right angle, 135 = an acute corner, 180 = it
    folds back on itself."""
    dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return 180.0 - math.degrees(math.acos(dot))


def clip_len(a, b, box):
    """The length of segment ab inside the rectangle box (x0, y0, x1, y1), edges included
    (Liang-Barsky)."""
    x0, y0, x1, y1 = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return 0.0
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return max(0.0, t1 - t0) * math.hypot(dx, dy)


def centre_of(it):
    """An Item's centre for a centre-to-centre distance: a pad's position (not the first
    corner of its core), a via's centre; a track's start."""
    s = it.src if isinstance(it.src, dict) else {}
    if it.kind in ("pad", "pad-hole", "hole") and s.get("at"):
        return tuple(s["at"])
    return it.pts[0]


# -- the objects ---------------------------------------------------------------

class Item:
    """One copper object (or a hole): `pts` is its core -- 1 point, 2 (a segment) or 3+ (a
    closed, filled polygon, with `holes`) -- grown by `r`.  `layers`: the copper layers it is
    on.  `src`: its record in geometry.json."""
    __slots__ = ("id", "kind", "net", "layers", "pts", "holes", "r", "box", "name", "src", "_bands")

    def __init__(self, kind, net, layers, pts, r, name, src, holes=()):
        self.kind, self.net, self.layers = kind, net or "", frozenset(layers)
        self.pts = [tuple(p) for p in pts]
        self.holes = [[tuple(p) for p in h] for h in holes]
        self.r, self.name, self.src, self._bands = r, name, src, None
        xs, ys = [p[0] for p in self.pts], [p[1] for p in self.pts]
        self.box = (min(xs) - r, min(ys) - r, max(xs) + r, max(ys) + r)

    def edges(self):
        """The core's boundary as segments (a point is one segment of no length)."""
        n = len(self.pts)
        if n <= 2:
            return [(self.pts[0], self.pts[-1])]
        out = [(self.pts[i], self.pts[(i + 1) % n]) for i in range(n)]
        for h in self.holes:
            out += [(h[i], h[(i + 1) % len(h)]) for i in range(len(h))]
        return out

    def contains(self, p):
        """True when p is inside the core's filled area (a polygon core only; its holes are
        outside).  A big pour is cut in 1 mm rows once, so a test reads one row's edges."""
        if len(self.pts) < 3:
            return False
        if not (self.box[0] <= p[0] <= self.box[2] and self.box[1] <= p[1] <= self.box[3]):
            return False
        if len(self.pts) + sum(len(h) for h in self.holes) < 64:
            return in_polygon(p, self.pts) and not any(in_polygon(p, h) for h in self.holes)
        if self._bands is None:
            self._bands = {}
            for a, b in self.edges():
                for k in range(int(math.floor(min(a[1], b[1]) / CELL)), int(math.floor(max(a[1], b[1]) / CELL)) + 1):
                    self._bands.setdefault(k, []).append((a, b))
        x, y, inside = p[0], p[1], False
        for (x1, y1), (x2, y2) in self._bands.get(int(math.floor(y / CELL)), ()):
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside         # outline and holes together: even-odd is the filled area
        return inside


def core_gap(A, B):
    """(gap, point on A's core, point on B's core): the distance between the two cores less
    both radii, never below 0.0.  0.0 also when one lies inside the other's filled area."""
    best = (INF, None, None)
    for a, b in A.edges():
        for c, d in B.edges():
            got = seg_seg(a, b, c, d)
            if got[0] < best[0]:
                best = got
                if best[0] == 0.0:
                    return 0.0, best[1], best[2]
    if B.contains(A.pts[0]):
        return 0.0, A.pts[0], A.pts[0]
    if A.contains(B.pts[0]):
        return 0.0, B.pts[0], B.pts[0]
    return max(0.0, best[0] - A.r - B.r), best[1], best[2]


def fmt_pt(p):
    return f"({p[0]:.3f}, {p[1]:.3f})"


class Geometry:
    """A snapshot's geometry.json, with its copper as Items and a grid to search them.

    G = load(snapshot); G.d is the JSON as written (G.d["tracks"], ["pads"], ...)."""

    def __init__(self, d):
        self.d = d
        self.dir = None                    # the snapshot folder, when loaded from one (changes.json for `added:`)
        self.copper_layers = list(d["board"]["copper_layers"])
        self.items = []
        self._grid = None
        self._atoms = []
        add = self._add
        for t in d.get("tracks", []):
            add("track", t["net"], [t["layer"]], [t["a"], t["b"]], t["w"] / 2,
                f"track {short(t['net'])} {t['layer']} {fmt_pt(t['a'])}-{fmt_pt(t['b'])} w{t['w']:g}", t)
        for t in d.get("arcs", []):
            p = t["pts"]
            for a, b in zip(p, p[1:]):
                add("arc", t["net"], [t["layer"]], [a, b], t["w"] / 2,
                    f"arc {short(t['net'])} {t['layer']} {fmt_pt(t['a'])}-{fmt_pt(t['b'])} w{t['w']:g}", t)
        for v in d.get("vias", []):
            name = f"via {short(v['net'])} {fmt_pt(v['at'])} {v['d']:g}/{v['drill']:g}"
            flashed = v.get("flashed", v["layers"])
            if flashed:
                add("via", v["net"], flashed, [v["at"]], v["d"] / 2, name, v)
            bare = [L for L in v["layers"] if L not in flashed]
            if bare:
                add("via-hole", v["net"], bare, [v["at"]], v["drill"] / 2, name + " (hole only)", v)
        for p in d.get("pads", []):
            name = f"pad {p['ref']}.{p['num']} {short(p['net'])}".rstrip()
            if p["layers"]:
                add("pad", p["net"], p["layers"], p["core"], p["r"], name, p)
            if p.get("hole"):
                bare = [L for L in self.copper_layers if L not in p["layers"]]
                if bare and p.get("type") == "npth":
                    add("hole", "", bare, p["hole"]["core"], p["hole"]["r"],
                        f"hole {p['ref']}.{p['num']}".rstrip("."), p)
                elif bare:
                    add("pad-hole", p["net"], bare, p["hole"]["core"], p["hole"]["r"], name + " (hole only)", p)
        for z in d.get("zones", []):
            for L, f in (z.get("filled") or {}).items():
                for k, isl in enumerate(f.get("islands", []), 1):
                    if len(isl.get("outline", [])) >= 3:
                        add("pour", z["net"], [L], isl["outline"], 0.0,
                            f"pour {short(z['net'])} {L} island {k}", z, isl.get("holes", ()))
        for sh in d.get("shapes", []):
            for poly in sh.get("polys", []):
                if len(poly.get("outline", [])) >= 3:
                    b = sh.get("box") or [0, 0, 0, 0]
                    add("shape", sh["net"], [sh["layer"]], poly["outline"], 0.0,
                        f"shape {short(sh['net'])} {sh['layer']} {fmt_pt(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))}",
                        sh, poly.get("holes", ()))

    def _add(self, kind, net, layers, pts, r, name, src, holes=()):
        it = Item(kind, net, layers, pts, r, name, src, holes)
        it.id = len(self.items)
        self.items.append(it)

    # -- names and selections ----------------------------------------------------

    def nets(self):
        return sorted({i.net for i in self.items if i.net} | {p["net"] for p in self.d.get("pads", []) if p["net"]})

    def net(self, name):
        """A net's full name: `name` itself, or the one net whose last path component it is."""
        have = self.nets()
        if name in have:
            return name
        got = [n for n in have if n.rsplit("/", 1)[-1] == name]
        if len(got) == 1:
            return got[0]
        near = got or [n for n in have if name.lower() in n.lower()][:12]
        raise SystemExit(f"geom: no net {name!r}" + (f" (did you mean: {', '.join(near)})" if near else ""))

    def net_list(self, name):
        """The nets a NAME stands for: one (`net`), or with * / ? every net whose full name or
        last path component matches the pattern."""
        if "*" not in name and "?" not in name:
            return [self.net(name)]
        rx = re.compile("".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in name) + r"\Z")
        got = [n for n in self.nets() if rx.match(n) or rx.match(n.rsplit("/", 1)[-1])]
        if not got:
            raise SystemExit(f"geom: no net matches {name!r}")
        return got

    def changes(self):
        """The snapshot's changes.json (changes.py): what CHANGES.md lists, as data."""
        p = self.dir / "changes.json" if self.dir else None
        if p is None or not p.exists():
            raise SystemExit("geom: `added:` reads the snapshot's changes.json -- give the snapshot folder, not a geometry.json")
        c = json.loads(p.read_text())
        if not c.get("base"):
            raise SystemExit("geom: changes.json has no base (everything is new): select by net or by box instead")
        return c

    def select(self, sel, layer=None):
        """The Items a SEL names (see the module docstring), narrowed to `layer` when given."""
        its = self.items
        if sel.startswith("net:"):
            n = set(self.net_list(sel[4:]))
            out = [i for i in its if i.net in n and i.kind in ("track", "arc", "via", "via-hole", "shape")]
        elif sel.startswith("pads:"):
            n = set(self.net_list(sel[5:]))
            out = [i for i in its if i.net in n and i.kind in ("pad", "pad-hole")]
        elif sel.startswith("added:"):
            c = self.changes()
            uu = {t.get("uuid") for k in ("copper", "vias", "shapes") for t in (c.get(k) or {}).get("added", [])}
            out = [i for i in its if i.kind in ("track", "arc", "via", "via-hole", "shape") and i.src.get("uuid") in uu]
            if sel[6:]:
                n = set(self.net_list(sel[6:]))
                out = [i for i in out if i.net in n]
        elif sel.startswith("plan:"):
            out = [i for i in its if i.kind in ("plan-track", "plan-via")]
            if sel[5:]:
                n = set(self.net_list(sel[5:]))
                out = [i for i in out if i.net in n]
        elif sel.startswith("via:"):
            x, y = (float(v) for v in sel[4:].split(",")[:2])
            vias = [i for i in its if i.kind in ("via", "via-hole")]
            best = min(vias, key=lambda i: math.dist(i.pts[0], (x, y)), default=None)
            if best is None or math.dist(best.pts[0], (x, y)) > 0.5:
                raise SystemExit(f"geom: no via within 0.5 mm of ({x:g}, {y:g})")
            out = [i for i in vias if i.src is best.src]
        elif sel.startswith("track:"):
            v = sel[6:].split(",")
            x, y = float(v[0]), float(v[1])
            layer = v[2] if len(v) > 2 else layer
            cand = [i for i in its if i.kind in ("track", "arc") and (not layer or layer in i.layers)]
            best, bd = None, 0.5
            for i in cand:
                dd = max(0.0, pt_seg((x, y), i.pts[0], i.pts[-1])[0] - i.r)
                if dd < bd or (dd == bd and best is None):
                    best, bd = i, dd
            if best is None:
                raise SystemExit(f"geom: no track within 0.5 mm of ({x:g}, {y:g})" + (f" on {layer}" if layer else ""))
            out = [i for i in cand if i.src is best.src]
        elif sel.startswith("uuid:"):
            out = [i for i in its if str(i.src.get("uuid", "")).startswith(sel[5:])]
        elif sel.startswith("box:"):
            out = self.in_box([float(v) for v in sel[4:].split(",")], layer)
        else:
            ref, _, num = sel.partition(".")
            out = [i for i in its if i.kind in ("pad", "hole", "pad-hole") and i.src["ref"] == ref and (not num or i.src["num"] == num)]
            if not out and "." in sel:                    # a reference with a dot in it
                out = [i for i in its if i.kind in ("pad", "hole", "pad-hole") and i.src["ref"] == sel]
        if layer:
            out = [i for i in out if layer in i.layers]
        if not out:
            raise SystemExit(f"geom: {sel!r} selects nothing" + (f" on {layer}" if layer else ""))
        return out

    # -- the search grid -----------------------------------------------------------

    def _cells(self, box):
        return [(ix, iy) for ix in range(int(math.floor(box[0] / CELL)), int(math.floor(box[2] / CELL)) + 1)
                for iy in range(int(math.floor(box[1] / CELL)), int(math.floor(box[3] / CELL)) + 1)]

    def grid(self):
        """Every core edge, filed under each 1 mm cell its copper can reach."""
        if self._grid is None:
            g, atoms = {}, self._atoms
            for it in self.items:
                for a, b in it.edges():
                    k = len(atoms)
                    atoms.append((it.id, a, b))
                    n = max(1, int(math.ceil(math.dist(a, b) / (CELL / 2))))
                    m = it.r + CELL / 4 + 1e-6
                    cells = set()
                    for s in range(n + 1):
                        x, y = a[0] + (b[0] - a[0]) * s / n, a[1] + (b[1] - a[1]) * s / n
                        cells.update(self._cells((x - m, y - m, x + m, y + m)))
                    for c in cells:
                        g.setdefault(c, []).append(k)
            self._grid = g
        return self._grid

    def _near(self, box, R):
        """{item id: its edges} for every item with copper in `box` grown by R."""
        return self._cands(self._cells((box[0] - R, box[1] - R, box[2] + R, box[3] + R)))

    def _cands(self, cells):
        """{item id: its edges filed under those grid cells}."""
        g, seen, out = self.grid(), set(), {}
        for c in cells:
            for k in g.get(c, ()):
                if k not in seen:
                    seen.add(k)
                    j, a, b = self._atoms[k]
                    out.setdefault(j, []).append((a, b))
        return out

    # -- measurements ----------------------------------------------------------------

    def gap(self, A, B, layer=None, any_layer=False):
        """The smallest edge-to-edge gap between two selections (lists of Items), compared only
        on copper layers both are on (`any_layer`: wherever they are, as if on one layer).
        Returns {gap, a, b, at, layers, centre} or None when no pair shares a layer.  `at` is the
        middle of the shortest line between the two; `centre` the distance between the two
        objects' centres (a pad's position, a via's centre; for a track its start)."""
        best = None
        for a in A:
            for b in B:
                if a is b:
                    continue
                shared = a.layers & b.layers
                if layer:
                    shared = shared & {layer}
                if not shared and not any_layer:
                    continue
                if best and max(a.box[0] - b.box[2], b.box[0] - a.box[2], a.box[1] - b.box[3],
                                b.box[1] - a.box[3]) > best["gap"]:
                    continue
                g, p, q = core_gap(a, b)
                if best is None or g < best["gap"]:
                    best = dict(gap=g, a=a, b=b, at=((p[0] + q[0]) / 2, (p[1] + q[1]) / 2),
                                layers=sorted(shared, key=self._layer_key),
                                centre=math.dist(centre_of(a), centre_of(b)))
        return best

    def _layer_key(self, L):
        return self.copper_layers.index(L) if L in self.copper_layers else 99

    def foreign(self, item, layer=None, within=None, ignore=(), pours=True, kinds=None):
        """The FOREIGN copper nearest one Item: [{gap, other, at, layers}], nearest first.

        Foreign: another net (an object with no net is foreign to all); `ignore` names nets
        left out (a pair's other half).  Compared on shared copper layers only (`layer`: that
        one).  gap: edge to edge, 0.0 = touching or overlapping.  Without `within`, the list is
        the one nearest object (or empty: nothing foreign on its layers); with it, every foreign
        object nearer than `within` mm.  `pours=False` leaves zone fills out; `kinds`, when given,
        is the only kinds of object counted (("plan-track", "plan-via"): a plan's copper alone)."""
        layers = item.layers & {layer} if layer else item.layers
        if not layers:
            return []

        def is_foreign(o):
            if o.id == item.id or o.src is item.src or not (o.layers & layers):
                return False
            if o.kind == "pour" and not pours:
                return False
            if kinds is not None and o.kind not in kinds:
                return False
            if o.net in ignore:
                return False
            return not (item.net and o.net == item.net)

        b = self.d["board"].get("outline_box") or (0, 0, 500, 500)
        limit = math.hypot(b[2] - b[0], b[3] - b[1]) + 10.0
        R = within if within is not None else 0.5
        found = {}
        # inside a foreign pour's filled area: its edges may all be far away
        for o in self.items:
            if o.kind == "pour" and is_foreign(o) and o.contains(item.pts[0]):
                found[o.id] = (0.0, item.pts[0], item.pts[0])
        mine = item.edges()
        while True:
            for j, edges in self._near(item.box, R).items():
                o = self.items[j]
                if j in found or not is_foreign(o):
                    continue
                best = (INF, None, None)
                for c, d in edges:
                    for a, e in mine:
                        got = seg_seg(a, e, c, d)
                        if got[0] < best[0]:
                            best = got
                if best[0] > 0.0 and (o.contains(item.pts[0]) or item.contains(o.pts[0])):
                    best = (0.0, item.pts[0], item.pts[0])
                found[j] = (max(0.0, best[0] - item.r - o.r), best[1], best[2])
            hit = sorted(((g, j) for j, (g, _p, _q) in found.items() if g <= R), key=lambda t: (t[0], t[1]))
            if within is not None or hit or R > limit:
                break
            R *= 2
            found = {j: v for j, v in found.items() if v[0] == 0.0}
        if within is not None:
            hit = [(g, j) for g, j in hit if g < within]
        else:
            hit = hit[:1]
        return [dict(gap=g, other=self.items[j],
                     at=((found[j][1][0] + found[j][2][0]) / 2, (found[j][1][1] + found[j][2][1]) / 2),
                     layers=sorted(self.items[j].layers & layers, key=self._layer_key)) for g, j in hit]

    def nearest(self, items, layer=None, within=None, ignore=(), pours=True, kinds=None):
        """`foreign` for every Item of a selection: rows {own, gap, other, at, layers}, smallest
        gap first.  A selection with nothing foreign near it gives no row for that object."""
        rows = []
        for it in items:
            for h in self.foreign(it, layer, within, ignore, pours, kinds):
                rows.append(dict(own=it, **h))
        rows.sort(key=lambda r: (r["gap"], r["own"].id, r["other"].id))
        return rows

    def label(self, ref):
        """Whose pad a reference label sits by.

        The label is the part's Reference text; its position is the CENTRE of its box.  `own`
        is the distance from there to the centre of the nearest of the part's own pads, `other`
        to the centre of the nearest pad of any other part on the label's side of the board
        (F.Silkscreen: pads on F.Cu; B.Silkscreen: on B.Cu) -- centre to centre, the same
        measure `place.py labels` places by (a pad's centre is the centre of its copper's box,
        which is its position for every symmetric pad).  margin = other - own: negative, and
        the label reads as the other part's.  `own_gap` / `other_gap` are edge to edge instead:
        the label's box to that pad's copper (0.0 = the text is over the pad).
        Returns None when the part has no Reference text at all.  A HIDDEN label is not printed,
        so it has `visible` False and no distances (KiCad keeps no meaningful box for it)."""
        d = self.d
        t = next((t for t in d.get("texts", []) if t.get("part") == ref and t.get("kind") == "reference"), None)
        if t is None:
            return None
        b = t["box"]
        c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        cu = "B.Cu" if t["layer"].startswith("B.") else "F.Cu"
        if not t.get("visible", True):
            return dict(ref=ref, text=t["text"], layer=t["layer"], at=c, visible=False, own=None, own_pad=None,
                        other=None, other_pad=None, margin=None, own_gap=None, other_gap=None)
        box = Item("text", "", [cu], [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])], 0.0, "label", t)
        own = other = None
        for it in self.items:
            if it.kind != "pad":
                continue
            p = it.src
            mine = p["ref"] == ref
            if not mine and cu not in it.layers:
                continue
            dist = math.dist(c, ((it.box[0] + it.box[2]) / 2, (it.box[1] + it.box[3]) / 2))
            cur = own if mine else other
            if cur is None or dist < cur[0]:
                if mine:
                    own = (dist, it)
                else:
                    other = (dist, it)
        row = dict(ref=ref, text=t["text"], layer=t["layer"], at=c, visible=True,
                   own=own[0] if own else None, own_pad=f"{ref}.{own[1].src['num']}" if own else None,
                   other=other[0] if other else None,
                   other_pad=f"{other[1].src['ref']}.{other[1].src['num']}" if other else None)
        row["margin"] = (other[0] - own[0]) if own and other else None
        row["own_gap"] = core_gap(box, own[1])[0] if own else None
        row["other_gap"] = core_gap(box, other[1])[0] if other else None
        return row

    def joints(self, net=None, layer=None, allow=(0.0, 45.0, 90.0)):
        """The joints of a net's track chain (every net when `net` is None), per layer.

        A JOINT is a point where two or more track or arc ends of one net on one layer meet
        (within 0.002 mm), or where one end lands on the middle of another track of that net
        (a tee).  `turn` is the angle between the two pieces there: 0 = straight on, 45, 90 =
        a right angle, 135 = an acute corner (an arc is taken by its tangent at that end).  At
        a joint of three or more, every pair is measured; a 45-degree branch off a straight
        run reads 0 / 45 / 135 and is not flagged.  A tee's turn is the branch against the
        track it lands on, 0..90.

        `flag` is True when a turn is not within 0.5 degrees of an allowed one (`allow`,
        default 0 / 45 / 90; for three or more, also their supplements).  `free` names the pad
        or via of the same net the joint lies inside -- copper entering a pad or a via, which
        the board's own angle gate does not count -- and such a joint is never flagged.
        Rows: {net, layer, at, kind (corner / junction / tee), turns, flag, free}."""
        d = self.d
        want = self.net(net) if net else None
        segs = []
        for t in d.get("tracks", []):
            if (want and t["net"] != want) or (layer and t["layer"] != layer) or t["a"] == t["b"]:
                continue
            a, b = tuple(t["a"]), tuple(t["b"])
            L = math.dist(a, b)
            u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
            segs.append(dict(net=t["net"], layer=t["layer"], a=a, b=b, ua=u, ub=(-u[0], -u[1]), straight=True))
        for t in d.get("arcs", []):
            if (want and t["net"] != want) or (layer and t["layer"] != layer):
                continue
            p = [tuple(q) for q in t["pts"]]

            def tangent(end, nxt):
                # perpendicular to the radius, pointing the way the arc leaves this end
                rx, ry = end[0] - t["centre"][0], end[1] - t["centre"][1]
                n = math.hypot(rx, ry) or 1.0
                tx, ty = -ry / n, rx / n
                if tx * (nxt[0] - end[0]) + ty * (nxt[1] - end[1]) < 0:
                    tx, ty = -tx, -ty
                return (tx, ty)
            segs.append(dict(net=t["net"], layer=t["layer"], a=p[0], b=p[-1], ua=tangent(p[0], p[1]),
                             ub=tangent(p[-1], p[-2]), straight=False))
        nodes = {}
        for i, s in enumerate(segs):
            for end in ("a", "b"):
                x, y = s[end]
                nodes.setdefault((s["net"], s["layer"], round(x / SNAP), round(y / SNAP)), []).append((i, end))
        own = {}
        for it in self.items:
            if it.kind in ("pad", "via") and it.net:
                own.setdefault(it.net, []).append(it)

        def free(net_, layer_, pt):
            for it in own.get(net_, ()):
                if layer_ in it.layers and core_gap(it, Item("pt", "", [layer_], [pt], 0.0, "", None))[0] == 0.0:
                    return it.name.rsplit(" ", 1)[0] if it.kind == "pad" else "via"
            return None

        def near(x, ok):
            return any(abs(x - k) <= ANGLE_TOL for k in ok)

        rows = []
        for (net_, layer_, _x, _y), ends in nodes.items():
            pt = segs[ends[0][0]][ends[0][1]]
            if len(ends) >= 2:
                turns = []
                for m in range(len(ends)):
                    for n in range(m + 1, len(ends)):
                        (i, e1), (j, e2) = ends[m], ends[n]
                        turns.append(round(turn(segs[i]["u" + e1], segs[j]["u" + e2]), 2))
                ok = tuple(allow) if len(ends) == 2 else tuple(allow) + tuple(180.0 - a for a in allow if a)
                fr = free(net_, layer_, pt)
                rows.append(dict(net=net_, layer=layer_, at=pt, kind="corner" if len(ends) == 2 else "junction",
                                 turns=turns, free=fr, flag=(not fr) and any(not near(t, ok) for t in turns)))
                continue
            i, end = ends[0]
            for j, o in enumerate(segs):
                if j == i or o["net"] != net_ or o["layer"] != layer_ or not o["straight"]:
                    continue
                dist, q = pt_seg(pt, o["a"], o["b"])
                if dist > SNAP or math.dist(q, o["a"]) < SNAP or math.dist(q, o["b"]) < SNAP:
                    continue
                tn = turn(segs[i]["u" + end], o["ua"])
                tn = round(min(tn, 180.0 - tn), 2)
                fr = free(net_, layer_, pt)
                rows.append(dict(net=net_, layer=layer_, at=pt, kind="tee", turns=[tn], free=fr,
                                 flag=(not fr) and not near(tn, [a for a in allow if a] or allow)))
                break
        rows.sort(key=lambda r: (r["net"], self._layer_key(r["layer"]), r["at"]))
        return rows

    def via_pad(self, vias=None, layer=None):
        """Each via against the pads: rows {via, own_gap, own_pad, other_gap, other_pad, centre},
        smallest of the two gaps first.

        gap: the via's pad EDGE (its hole's wall on a layer where KiCad flashes no pad) to the
        pad's copper edge, on a copper layer both are on; 0.0 = the via touches or sits in the
        pad.  own: the nearest pad of the via's own net (which DRC never tests); other: the
        nearest pad of any other net.  centre: via centre to the centre of whichever of the two
        pads is nearer.  A via with no pad on any of its layers gives no row."""
        vias = vias if vias is not None else [i for i in self.items if i.kind in ("via", "via-hole")]
        pads = [i for i in self.items if i.kind == "pad"]
        by_src = {}
        for v in vias:
            if v.kind not in ("via", "via-hole") or (layer and layer not in v.layers):
                continue
            row = by_src.setdefault(id(v.src), dict(via=v, own_gap=None, own_pad=None, other_gap=None, other_pad=None))
            if v.kind == "via":
                row["via"] = v
            lay = v.layers & {layer} if layer else v.layers
            for p in pads:
                if not (p.layers & lay):
                    continue
                key = "own" if (v.net and p.net == v.net) else "other"
                cur = row[key + "_gap"]
                if cur is not None and max(v.box[0] - p.box[2], p.box[0] - v.box[2], v.box[1] - p.box[3],
                                           p.box[1] - v.box[3]) > cur:
                    continue
                g = core_gap(v, p)[0]
                if cur is None or g < cur:
                    row[key + "_gap"], row[key + "_pad"] = g, p
        rows = []
        for row in by_src.values():
            if row["own_pad"] is None and row["other_pad"] is None:
                continue
            both = [(row[k + "_gap"], row[k + "_pad"]) for k in ("own", "other") if row[k + "_pad"] is not None]
            g, p = min(both, key=lambda t: t[0])
            row["min"] = g
            row["centre"] = math.dist(row["via"].pts[0], p.src["at"])
            rows.append(row)
        rows.sort(key=lambda r: (r["min"], r["via"].id))
        return rows

    def holes(self, below=1.0):
        """Hole-to-hole gaps: every pair of drilled holes nearer than `below` mm, wall to wall,
        smallest first.  A hole is a via's drill or a pad's (plated or not; a slot is the line
        between its two arc centres grown by half its width).  gap = the distance between the
        two holes' centre lines less both radii -- the finished-drill sizes in the board file,
        not the tool's -- and 0.0 when the holes meet or overlap.  The layers do not matter: a
        blind via's hole counts against a through hole's.  Rows: {gap, a, b, centre}."""
        hs = []
        for v in self.d.get("vias", []):
            hs.append(Item("hole", v["net"], [], [v["at"]], v["drill"] / 2,
                           f"via {short(v['net'])} {fmt_pt(v['at'])} drill {v['drill']:g}", v))
        for p in self.d.get("pads", []):
            h = p.get("hole")
            if h:
                hs.append(Item("hole", p["net"], [], h["core"], h["r"],
                               f"pad {p['ref']}.{p['num']} drill {'x'.join(f'{v:g}' for v in p['drill'])}", p))
        cell = below + 2 * max([h.r for h in hs] or [0.0]) + 0.01
        g = {}
        for k, h in enumerate(hs):
            h.id = k
            for ix in range(int(math.floor(h.box[0] / cell)), int(math.floor(h.box[2] / cell)) + 1):
                for iy in range(int(math.floor(h.box[1] / cell)), int(math.floor(h.box[3] / cell)) + 1):
                    g.setdefault((ix, iy), []).append(k)
        rows, seen = [], set()
        for h in hs:
            for ix in range(int(math.floor(h.box[0] / cell)) - 1, int(math.floor(h.box[2] / cell)) + 2):
                for iy in range(int(math.floor(h.box[1] / cell)) - 1, int(math.floor(h.box[3] / cell)) + 2):
                    for k in g.get((ix, iy), ()):
                        if k <= h.id or (h.id, k) in seen:
                            continue
                        seen.add((h.id, k))
                        o = hs[k]
                        gap, _p, _q = core_gap(h, o)
                        if gap < below:
                            rows.append(dict(gap=gap, a=h, b=o, centre=math.dist(h.pts[0], o.pts[0])))
        rows.sort(key=lambda r: (r["gap"], r["a"].id, r["b"].id))
        return rows

    def copper(self, net, layer=None):
        """A net's copper, per layer: {net, layers: {L: {length, segments, arcs, widths: {w:
        length}}}, total, vias: [{at, d, drill, layers}]}.

        length: the sum of the centre-line lengths of its tracks and arcs on that layer (an
        arc by radius x angle).  Nothing is merged: two tracks laid over each other count
        twice, and a via adds no length.  widths: the length laid at each track width."""
        n = self.net(net)
        per = {}
        for t in self.d.get("tracks", []) + self.d.get("arcs", []):
            if t["net"] != n or (layer and t["layer"] != layer):
                continue
            arc = "mid" in t
            ln = t["length"] if arc else math.dist(t["a"], t["b"])
            p = per.setdefault(t["layer"], dict(length=0.0, segments=0, arcs=0, widths={}))
            p["length"] += ln
            p["arcs" if arc else "segments"] += 1
            p["widths"][t["w"]] = p["widths"].get(t["w"], 0.0) + ln
        vias = [dict(at=v["at"], d=v["d"], drill=v["drill"], layers=v["layers"]) for v in self.d.get("vias", [])
                if v["net"] == n and (not layer or layer in v["layers"])]
        return dict(net=n, layers={L: per[L] for L in sorted(per, key=self._layer_key)},
                    total=sum(p["length"] for p in per.values()), vias=vias)

    def in_box(self, box, layer=None):
        """The copper Items whose copper touches the rectangle (x0, y0, x1, y1): the gap between
        the object (its full width) and the rectangle is 0.0 -- inside it, crossing it or just
        reaching its edge."""
        x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
        rect = Item("box", "", [], [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], 0.0, "box", None)
        out = []
        for it in self.items:
            if layer and layer not in it.layers:
                continue
            if it.box[0] > x1 or it.box[2] < x0 or it.box[1] > y1 or it.box[3] < y0:
                continue
            if core_gap(it, rect)[0] == 0.0:
                out.append(it)
        return out

    def bare(self, refs):
        """The pads of the named parts that no copper of their net reaches: [{pad, net, at}].

        A pad is BARE when it has a net (not empty, not KiCad's 'unconnected-(...)') and no
        track, arc, via (its pad or, where it has none on that layer, its hole), copper shape or
        pour of that net touches it on a layer the pad is on -- the same touch as `conn` (copper
        overlapping or gap 0.0, or one's first point inside the other).  Another PAD of the net
        does not count: two lands side by side are not joined by standing close."""
        want, rows = set(refs), []
        fills = [i for i in self.items if i.kind == "pour"]
        for it in self.items:
            if it.kind != "pad" or it.src["ref"] not in want or not it.net or it.net.startswith("unconnected-"):
                continue
            mine, hit = it.edges(), False
            for j, edges in self._near(it.box, 0.0).items():
                o = self.items[j]
                if o.net != it.net or o.kind in ("pad", "pad-hole", "hole") or not (o.layers & it.layers):
                    continue
                g = min(seg_seg(a, e, c, d)[0] for a, e in mine for c, d in edges) - it.r - o.r
                if g <= 1e-9 or o.contains(it.pts[0]) or it.contains(o.pts[0]):
                    hit = True
                    break
            if not hit:
                hit = any(o.net == it.net and (o.layers & it.layers) and o.contains(it.pts[0]) for o in fills)
            if not hit:
                rows.append(dict(pad=f"{it.src['ref']}.{it.src['num']}", net=it.net, at=tuple(it.src["at"][:2])))
        return rows

    def links(self, refs):
        """Each pad's link to a via of its net, on each layer the pad is on: [{pad, net, layer,
        via, shape, angles, lengths, off}].  A link is a chain of tracks of the pad's net and
        layer from a point inside the pad to a via's centre through plain joints (one track on
        in, one out, no pad), at most 4 tracks and 3 mm; collinear tracks count as one piece.
        angles: each piece's angle to the pad's axes (0 / 90 along one, 45 between them).
        shape: 'axis' (one piece along an axis), '45' (one piece at 45), 'axis+45' (an axis piece,
        then one at 45), 'bent' (anything else of 2+ pieces), 'angle' (one piece at another
        angle); off: the first point's distance from the pad's centre (a link that does not
        leave the centre).  A via whose centre lies in the pad is 'via in pad'.  `refs`: part
        references or fnmatch patterns ('C3*')."""
        pads = [it for it in self.items if it.kind == "pad" and it.net and not it.net.startswith("unconnected-")
                and any(fnmatch.fnmatchcase(it.src["ref"], r) for r in refs)]
        by = {}
        for it in self.items:
            if it.kind in ("track", "via", "pad"):
                for L in it.layers:
                    by.setdefault((it.net, L), []).append(it)

        def inside(it, p):
            return it.contains(p) or min(pt_seg(p, a, b)[0] for a, b in it.edges()) <= it.r + 1e-6

        def at(p, q):
            return math.hypot(p[0] - q[0], p[1] - q[1]) <= SNAP

        rows = []
        for pd in pads:
            c, rot = tuple(pd.src["at"][:2]), (pd.src.get("rot") or 0.0)
            for L in sorted(pd.layers):
                pool = by.get((pd.net, L), [])
                vias = [v for v in pool if v.kind == "via"]
                for v in vias:
                    if inside(pd, v.pts[0]):
                        rows.append(dict(pad=f"{pd.src['ref']}.{pd.src['num']}", net=pd.net, layer=L, via=v.pts[0],
                                         shape="via in pad", angles=[], lengths=[], off=0.0))
                # a link starts at a track end in the pad, the ones at its centre first; a track
                # wholly in the pad starts one only from the centre (a piece inside the land)
                starts = sorted(((math.dist(t.pts[k], c), t, k) for t in pool if t.kind == "track" for k in (0, 1)
                                 if inside(pd, t.pts[k])), key=lambda x: x[0])
                used = set()
                for d0, t, k in starts:
                    if id(t) in used or (d0 > SNAP and inside(pd, t.pts[1 - k])):
                        continue
                    if True:
                        chain, pts, p = [t], [t.pts[k], t.pts[1 - k]], t.pts[1 - k]
                        n = math.dist(*t.pts)
                        via = None
                        while True:
                            via = next((v for v in vias if at(v.pts[0], p)), None)
                            if via or len(chain) >= 4 or n > 3.0:
                                break
                            on = [o for o in pool if o.kind == "track" and o not in chain and (at(o.pts[0], p) or at(o.pts[1], p))]
                            if len(on) != 1 or any(o.kind == "pad" and o is not pd and inside(o, p) for o in pool):
                                break
                            o = on[0]
                            q = o.pts[1] if at(o.pts[0], p) else o.pts[0]
                            chain.append(o); pts.append(q); p = q; n += math.dist(*o.pts)
                        if not via:
                            continue
                        used.update(id(x) for x in chain)
                        pieces = []
                        for a, b in zip(pts, pts[1:]):
                            d = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                            if pieces and abs((d - pieces[-1][0] + 180) % 360 - 180) < 0.05:
                                pieces[-1][1] += math.dist(a, b)
                            else:
                                pieces.append([d, math.dist(a, b)])
                        angs = [round(min((d - rot) % 90, 90 - (d - rot) % 90), 2) for d, _ in pieces]
                        ax = [x < ANGLE_TOL for x in angs]
                        d45 = [abs(x - 45) < ANGLE_TOL for x in angs]
                        if len(pieces) == 1:
                            shape = "axis" if ax[0] else "45" if d45[0] else "angle"
                        elif len(pieces) == 2 and ax[0] and d45[1]:
                            shape = "axis+45"
                        else:
                            shape = "bent"
                        rows.append(dict(pad=f"{pd.src['ref']}.{pd.src['num']}", net=pd.net, layer=L, via=via.pts[0],
                                         shape=shape, angles=angs, lengths=[round(x, 4) for _, x in pieces],
                                         off=round(math.dist(pts[0], c), 4)))
        return rows

    def ring(self, ref, net, parts="C*"):
        """A rail's capacitors at a BGA: {courtyard, balls, edge, caps: [{ref, value, at, side, where, pad, ball, d,
        out}]}.

        balls: the BGA's pads on the rail (`net`: a name or pattern, as `net_list`).  edge: per ball, its centre's
        distance to the nearest courtyard side (the smallest of the four), smallest first -- a capacitor outside
        the courtyard stands at least that far from the nearest ball.  caps: every OTHER part matching `parts`
        (fnmatch) with a pad on the rail; where is 'under' when the part's centre is inside the courtyard, else
        'ring'; d is the centre-to-centre distance from its rail pad to the nearest rail ball (ball names it);
        out is how far its centre stands outside the courtyard (0 under it).  Nearest first."""
        bga = next((q for q in self.d.get("parts", []) if q["ref"] == ref), None)
        if bga is None:
            raise SystemExit(f"geom: no part {ref!r} in this dump")
        cy = bga.get("courtyard") or bga.get("box")
        rail = set(self.net_list(net))
        pads = [i for i in self.items if i.kind == "pad" and i.net in rail]
        balls = [i for i in pads if i.src["ref"] == ref]
        if not balls:
            raise SystemExit(f"geom: {ref} has no pad on {net}")
        edge = sorted((min(b.src["at"][0] - cy[0], cy[2] - b.src["at"][0], b.src["at"][1] - cy[1], cy[3] - b.src["at"][1]),
                       b.src["num"]) for b in balls)
        info = {q["ref"]: q for q in self.d.get("parts", [])}
        caps = []
        for it in pads:
            r = it.src["ref"]
            if r == ref or not fnmatch.fnmatch(r, parts) or any(c["pad"] == f"{r}.{it.src['num']}" for c in caps):
                continue
            q = info.get(r, {})
            at = q.get("at") or it.src["at"]
            pa = it.src["at"]
            d, b = min((math.hypot(pa[0] - x.src["at"][0], pa[1] - x.src["at"][1]), x.src["num"]) for x in balls)
            out = math.hypot(max(cy[0] - at[0], 0, at[0] - cy[2]), max(cy[1] - at[1], 0, at[1] - cy[3]))
            caps.append(dict(ref=r, value=q.get("value", ""), at=tuple(at[:2]), side=q.get("side", ""),
                             where="under" if out == 0 else "ring", pad=f"{r}.{it.src['num']}", ball=b,
                             d=round(d, 4), out=round(out, 4)))
        caps.sort(key=lambda c: c["d"])
        return dict(courtyard=cy, balls=[b.src["num"] for b in balls], edge=[(round(e, 4), n) for e, n in edge], caps=caps)

    def neck(self, net, layer, src, dst, res=0.05):
        """A net's copper on one layer, as a current sees it: the least total width of copper across any cut that
        separates the copper inside box `src` (a regulator's feed vias) from the copper inside box `dst` (a ball
        field).  {width, raw, pieces: [(x0, y0, x1, y1)], cells, res}; width 0 when the two do not join.

        Every copper object of the net on the layer (fills, shapes, flashed vias, pads, tracks, arcs) is rasterised
        at `res` (a cell is copper when its centre is); the cut is a max-flow / min-cut on the 8-neighbour grid
        with Cauchy-Crofton weights (axis pi/8, diagonal pi/(8 sqrt 2) a cell).  Those sum to 0.948 a cell across a
        straight cut on an axis or at 45 degrees (1.026 at 22.5), and each cut piece loses one diagonal at each
        copper edge; width (mm) is the flow divided by 0.948 plus those two half-diagonals a piece: exact on an axis
        cut, within 4 per cent and about a cell a piece at any angle (raw: the flow uncorrected).  pieces: the cut's
        cells on the source side, grouped -- where the neck is.  Near a limit, halve `res` and see it hold."""
        its = [i for i in self.items if i.net == net and layer in i.layers
               and i.kind in ("pour", "shape", "via", "pad", "track", "arc", "plan-track", "plan-via")]
        if not its:
            raise SystemExit(f"geom: {net} has no copper on {layer}")
        x0 = min(i.box[0] for i in its) - res; y0 = min(i.box[1] for i in its) - res
        x1 = max(i.box[2] for i in its) + res; y1 = max(i.box[3] for i in its) + res
        nx, ny = int((x1 - x0) / res) + 2, int((y1 - y0) / res) + 2
        cu = bytearray(nx * ny)
        cx = lambda i: x0 + (i + 0.5) * res
        for it in its:
            if len(it.pts) >= 3:
                edges = it.edges()
                j0, j1 = max(0, int((it.box[1] - y0) / res)), min(ny - 1, int((it.box[3] - y0) / res) + 1)
                for j in range(j0, j1 + 1):
                    y = y0 + (j + 0.5) * res
                    xs = sorted(xa + (y - ya) * (xb - xa) / (yb - ya) for (xa, ya), (xb, yb) in edges if (ya > y) != (yb > y))
                    for a, b in zip(xs[::2], xs[1::2]):
                        for i in range(max(0, int(math.ceil((a - x0) / res - 0.5))), min(nx - 1, int(math.floor((b - x0) / res - 0.5))) + 1):
                            cu[j * nx + i] = 1
            else:
                a, b = it.pts[0], it.pts[-1]
                for j in range(max(0, int((it.box[1] - y0) / res)), min(ny - 1, int((it.box[3] - y0) / res) + 1) + 1):
                    for i in range(max(0, int((it.box[0] - x0) / res)), min(nx - 1, int((it.box[2] - x0) / res) + 1) + 1):
                        if not cu[j * nx + i] and pt_seg((cx(i), y0 + (j + 0.5) * res), a, b)[0] <= it.r:
                            cu[j * nx + i] = 1

        def inbox(k, bx):
            x, y = cx(k % nx), y0 + (k // nx + 0.5) * res
            return bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]
        S = [k for k in range(nx * ny) if cu[k] and inbox(k, src)]
        T = bytearray(nx * ny)
        for k in range(nx * ny):
            if cu[k] and inbox(k, dst):
                T[k] = 1
        S = [k for k in S if not T[k]]
        if not S or not any(T):
            raise SystemExit(f"geom: no {net} copper on {layer} inside the " + ("source" if not S else "sink") + " box")
        K = 1000
        DIRS = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]
        W = [round(K * math.pi / 8)] * 4 + [round(K * math.pi / (8 * math.sqrt(2)))] * 4
        OPP = [2, 3, 0, 1, 5, 4, 7, 6]
        OFF = [dy * nx + dx for dx, dy in DIRS]
        cap = [0] * (nx * ny * 8)
        for k in range(nx * ny):
            if cu[k]:
                i, j = k % nx, k // nx
                for d, (dx, dy) in enumerate(DIRS):
                    if 0 <= i + dx < nx and 0 <= j + dy < ny and cu[k + OFF[d]]:
                        cap[k * 8 + d] = W[d]
        flow, isS = 0, None
        while True:                       # Dinic: BFS levels from every source cell, then blocking flow
            lev = [-1] * (nx * ny)
            q = list(S)
            for k in S:
                lev[k] = 0
            h, reached = 0, False
            while h < len(q):
                k = q[h]; h += 1
                if T[k]:
                    reached = True
                    continue
                for d in range(8):
                    if cap[k * 8 + d] > 0:
                        m = k + OFF[d]
                        if lev[m] < 0:
                            lev[m] = lev[k] + 1
                            q.append(m)
            if not reached:
                isS = lev
                break
            ptr = [0] * (nx * ny)
            for s0 in S:
                while True:
                    path, k = [], s0
                    while k is not None and not T[k]:
                        while ptr[k] < 8:
                            d = ptr[k]
                            if cap[k * 8 + d] > 0 and lev[k + OFF[d]] == lev[k] + 1:
                                break
                            ptr[k] += 1
                        if ptr[k] < 8:
                            path.append(k)
                            k += OFF[ptr[k]]
                        else:                     # a dead end: never again this phase; step back
                            lev[k] = -2
                            k = path.pop() if path else None
                            if k is not None:
                                ptr[k] += 1
                    if k is None:
                        break
                    f = min(cap[c * 8 + ptr[c]] for c in path)
                    for c in path:
                        cap[c * 8 + ptr[c]] -= f
                        cap[(c + OFF[ptr[c]]) * 8 + OPP[ptr[c]]] += f
                    flow += f
        side = [l >= 0 for l in isS]
        cells = [k for k in range(nx * ny) if side[k] and any(cu[k + OFF[d]] and not side[k + OFF[d]]
                 for d in range(8) if 0 <= k % nx + DIRS[d][0] < nx and 0 <= k // nx + DIRS[d][1] < ny)]
        seen, pieces, cs = set(), [], set(cells)
        for c0 in cells:
            if c0 in seen:
                continue
            grp, st = [], [c0]
            seen.add(c0)
            while st:
                c = st.pop(); grp.append(c)
                for d in range(8):
                    m = c + OFF[d]
                    if m in cs and m not in seen:
                        seen.add(m); st.append(m)
            xs = [cx(c % nx) for c in grp]; ys = [y0 + (c // nx + 0.5) * res for c in grp]
            pieces.append((round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)))
        CC = math.pi / 8 * (1 + math.sqrt(2))        # the weights' sum across a straight cut, a cell: 0.948 on axis and 45
        raw = flow * res / K
        return dict(width=raw / CC + (2 * (math.pi / (8 * math.sqrt(2))) / CC) * res * len(pieces) if raw else 0.0,
                    raw=raw, pieces=sorted(pieces), cells=len(cells), res=res)

    def unused_pads(self, box):
        """The vias whose centre lies in a rectangle (its edge included), each with what it does
        with its unconnected pads: [{via, drop, flashed, span}].  drop is the via's own property
        (KiCad's 'remove unconnected layers'; None when the dump is older than the key); flashed
        and span count the copper layers it has a pad on and the layers it crosses."""
        x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
        e = 1e-9
        return [dict(via=v, drop=v.get("drop_unused"), flashed=len(v.get("flashed", v["layers"])), span=len(v["layers"]))
                for v in self.d.get("vias", []) if x0 - e <= v["at"][0] <= x1 + e and y0 - e <= v["at"][1] <= y1 + e]

    def add_plan(self, plan, tag="plan"):
        """A routing plan's copper added to the board: route.py's JSON, `tracks` as {net, layer,
        width, pts} (a polyline) and `vias` as {net, at, pad, drill[, layers]} (a via with no
        `layers` spans every copper layer, its pad on each).  Items of kind plan-track and
        plan-via, so a query measures the board against a plan that is not on it (a ripped
        group's reviewed lanes).  Returns the items added."""
        n0 = len(self.items)
        for t in plan.get("tracks", []):
            for a, b in zip(t["pts"], t["pts"][1:]):
                self._add("plan-track", t["net"], [t["layer"]], [a, b], t["width"] / 2,
                          f"{tag} track {short(t['net'])} {t['layer']} {fmt_pt(a)}-{fmt_pt(b)} w{t['width']:g}", t)
        for v in plan.get("vias", []):
            self._add("plan-via", v["net"], v.get("layers") or self.copper_layers, [v["at"]], v["pad"] / 2,
                      f"{tag} via {short(v['net'])} {fmt_pt(v['at'])} {v['pad']:g}/{v['drill']:g}", v)
        self._grid, self._atoms = None, []
        return self.items[n0:]

    def lanes(self, ref, power=(), track=None, clear=None, rings=2, reach=None):
        """The exit lanes of a BGA's outer SIGNAL balls on the layer of its lands, and what stands
        in them: {pitch, cols, rows, layer, end, track, clear, balls: [{ball, net, ring, at, exit,
        lanes: [{side, offset, a, b, gap, by, free}]}]}.

        The part's pads are a lattice: pitch is the smallest spacing between pad centres along x
        or y.  A ball's RING is 1 + its distance in sites to the nearest edge row or column of the
        lattice (empty sites count).  A ring-1 ball leaves straight out of its land, on the line
        through its centre normal to its edge; a ring-2 ball between two ring-1 lands, on one of
        the two lines half a pitch either side of its centre.  A ball as near two edges (a
        corner) has its lanes on both.  Each lane runs from the ball's centre, projected onto it,
        to the part's courtyard line (its pad box grown by a pitch, without one), or `reach` mm
        past the outer lands' centre line when that is nearer (a courtyard far out of the lands
        tests farther than the escape needs); side is the edge it leaves by (-x, +x, -y, +y in
        board coordinates).  gap: the distance from the lane's centre line to the nearest FOREIGN
        object on that layer (another net, or none) found within `clear` of the track, less the
        object's radius and half a track of width `track` (0.0 when they touch or a foreign fill
        holds the lane; None when nothing stands that near); FREE when gap is None or >= `clear`.
        `unassigned`: the balls that have a free lane but cannot all leave at once, one track a
        lane (assign_lanes).  exit: the ball has a free lane.
        `power`: fnmatch patterns for the nets whose balls leave by vias, matched against the
        full name and the last path component; a ball with no net, or KiCad's
        'unconnected-(...)', is not asked.  track / clear default to the board's rules
        (track_width, clearance)."""
        part = next((q for q in self.d.get("parts", []) if q["ref"] == ref), None)
        pads = [i for i in self.items if i.kind == "pad" and i.src["ref"] == ref]
        if part is None or not pads:
            raise SystemExit(f"geom: no part {ref!r} with pads in this dump")
        rules = self.d["board"].get("rules") or {}
        track = rules.get("track_width") if track is None else track
        clear = rules.get("clearance") if clear is None else clear
        if track is None or clear is None:
            raise SystemExit("geom: lanes needs --track and --clear (the dump's board rules have none)")
        layer = sorted(pads[0].layers, key=self._layer_key)[0]
        xs = sorted({round(i.src["at"][0], 4) for i in pads})
        ys = sorted({round(i.src["at"][1], 4) for i in pads})
        steps = [b - a for v in (xs, ys) for a, b in zip(v, v[1:]) if b - a > 1e-3]
        if not steps:
            raise SystemExit(f"geom: {ref}'s pads are no lattice")
        pitch = min(steps)
        ncol, nrow = round((xs[-1] - xs[0]) / pitch) + 1, round((ys[-1] - ys[0]) / pitch) + 1
        end = part.get("courtyard") or [xs[0] - pitch, ys[0] - pitch, xs[-1] + pitch, ys[-1] + pitch]
        if reach is not None:
            end = [max(end[0], xs[0] - reach), max(end[1], ys[0] - reach), min(end[2], xs[-1] + reach), min(end[3], ys[-1] + reach)]
        hw = track / 2

        def is_power(net):
            tail = net.rsplit("/", 1)[-1]
            return any(fnmatch.fnmatchcase(net, pat) or fnmatch.fnmatchcase(tail, pat) for pat in power)

        fills = [o for o in self.items if o.kind in ("pour", "shape") and layer in o.layers]

        def lane_gap(a, b, net):
            best, by = INF, None
            for o in fills:
                if not (o.net and o.net == net) and (o.contains(a) or o.contains(b)):
                    return 0.0, o
            box = (min(a[0], b[0]) - hw, min(a[1], b[1]) - hw, max(a[0], b[0]) + hw, max(a[1], b[1]) + hw)
            for j, edges in self._near(box, clear + 1e-6).items():
                o = self.items[j]
                if layer not in o.layers or (o.net and o.net == net):
                    continue
                g = min(seg_seg(a, b, c, d)[0] for c, d in edges) - o.r - hw
                if g < best or (g == best and by is not None and o.id < by.id):
                    best, by = g, o
            return (max(0.0, best), by) if by is not None else (None, None)

        balls = []
        for it in sorted(pads, key=lambda i: (i.src["at"][1], i.src["at"][0])):
            net = it.net
            if not net or net.startswith("unconnected-") or is_power(net):
                continue
            x, y = it.src["at"][0], it.src["at"][1]
            ci, ri = round((x - xs[0]) / pitch), round((y - ys[0]) / pitch)
            depth = {"-x": ci, "+x": ncol - 1 - ci, "-y": ri, "+y": nrow - 1 - ri}
            k = min(depth.values())
            if k >= rings:
                continue
            lanes = []
            for side, dd in depth.items():
                if dd != k:
                    continue
                for o in ((0.0,) if k == 0 else (-pitch / 2, pitch / 2)):
                    if side in ("-x", "+x"):
                        a, b = (x, y + o), (end[0] if side == "-x" else end[2], y + o)
                    else:
                        a, b = (x + o, y), (x + o, end[1] if side == "-y" else end[3])
                    g, by = lane_gap(a, b, net)
                    lanes.append(dict(side=side, offset=o, a=a, b=b, gap=g, by=by, free=g is None or g >= clear - 1e-9))
            balls.append(dict(ball=it.src["num"], net=net, ring=k + 1, at=(x, y), lanes=lanes,
                              exit=any(ln["free"] for ln in lanes)))
        return dict(pitch=pitch, cols=ncol, rows=nrow, layer=layer, end=end, track=track, clear=clear, balls=balls,
                    unassigned=assign_lanes(balls)[1])

    def others_in_box(self, box):
        """What is in a rectangle besides copper: {parts, texts, zones}.  A part is in when its
        courtyard box (its pads' box without one) overlaps the rectangle; a text when its box
        does; a zone when its outline's box does."""
        x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])

        def hit(b):
            return b and not (b[0] > x1 or b[2] < x0 or b[1] > y1 or b[3] < y0)
        return dict(parts=[p for p in self.d.get("parts", []) if hit(p.get("courtyard") or p.get("box"))],
                    texts=[t for t in self.d.get("texts", []) if hit(t.get("box"))],
                    zones=[z for z in self.d.get("zones", []) if hit(z.get("box"))])

    # -- along a track, across a net, at a point ------------------------------------------

    def _stations(self, it, step):
        """A track Item's centre line as (point, the length it stands for), `step` apart."""
        a, b = it.pts[0], it.pts[-1]
        L = math.dist(a, b)
        n = max(1, int(math.ceil(L / step - 1e-9)))
        for k in range(n):
            t = (k + 0.5) / n
            yield (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), L / n

    @staticmethod
    def _disc_gap(p, r, o, edges):
        """The gap between the disc (p, r) and Item o, measured to the given edges of o's core
        (raw: negative when they overlap); 0.0 or less also when p is inside o's filled area."""
        g = min(pt_seg(p, c, d)[0] for c, d in edges) - r - o.r
        if g > 0.0 and len(o.pts) >= 3 and o.contains(p):
            return 0.0
        return g

    def _around(self, p, reach, state):
        """The candidates within `reach` of point p ({item id: edges}); `state` remembers the
        last cells asked for, so stations along one track read the grid once."""
        cells = tuple(self._cells((p[0] - reach, p[1] - reach, p[0] + reach, p[1] + reach)))
        if state.get("cells") != cells:
            state["cells"], state["got"] = cells, self._cands(cells)
        return state["got"]

    def coupled(self, items, within, layer=None, ignore=(), pours=True, step=0.01):
        """The length a selection's tracks and arcs run NEARER THAN `within` to foreign copper on
        their own layer, per foreign net.

        Each track's centre line is cut into stations `step` apart; a station counts for a
        foreign net when the gap between the track's copper there (the disc of its width) and
        any copper of that net on the layer -- track, via pad or bare hole, pad, shape, pour --
        is under `within`.  gap: edge to edge, as everywhere.  `ignore`: nets left out (a pair's
        other half, a rail); `pours=False` leaves zone fills out.
        Returns {rows, totals}.  rows: {net, layer, other, length, gap (the smallest), at (where
        it is smallest), box (of the counted stations)}, longest first.  totals: per own net and
        layer {net, layer, length (routed), near (the length with ANY foreign copper under
        `within` -- not the sum of the rows, which overlap)}."""
        rows, totals, state = {}, {}, {}
        fills = [o for o in self.items if o.kind == "pour"] if pours else []
        for it in items:
            if it.kind not in ("track", "arc") or (layer and layer not in it.layers):
                continue
            L = next(iter(it.layers))
            tot = totals.setdefault((it.net, L), dict(net=it.net, layer=L, length=0.0, near=0.0))
            for pt, ds in self._stations(it, step):
                tot["length"] += ds
                seen = {}
                for j, edges in self._around(pt, it.r + within, state).items():
                    o = self.items[j]
                    if o.src is it.src or L not in o.layers or (it.net and o.net == it.net) or o.net in ignore:
                        continue
                    if o.kind == "pour" and not pours:
                        continue
                    g = max(0.0, self._disc_gap(pt, it.r, o, edges))
                    if g < within and g < seen.get(o.net, INF):
                        seen[o.net] = g
                for o in fills:                       # deep inside a foreign fill: no edge of it is near
                    if L in o.layers and o.net != it.net and o.net not in ignore and o.net not in seen and o.contains(pt):
                        seen[o.net] = 0.0
                if seen:
                    tot["near"] += ds
                for other, g in seen.items():
                    r = rows.setdefault((it.net, L, other), dict(net=it.net, layer=L, other=other, length=0.0,
                                                                 gap=INF, at=pt, box=[pt[0], pt[1], pt[0], pt[1]]))
                    r["length"] += ds
                    if g < r["gap"]:
                        r["gap"], r["at"] = g, pt
                    b = r["box"]
                    r["box"] = [min(b[0], pt[0]), min(b[1], pt[1]), max(b[2], pt[0]), max(b[3], pt[1])]
        out = sorted(rows.values(), key=lambda r: (-r["length"], r["net"], r["layer"], r["other"]))
        return dict(rows=out, totals=sorted(totals.values(), key=lambda t: (t["net"], self._layer_key(t["layer"]))))

    def overlap(self, items, adjacent=None, margin=0.0, ignore=(), step=0.01):
        """The length a selection's tracks and arcs lie OVER OR UNDER foreign copper of other
        layers, in plan view (copper over copper, whatever is between).

        The other layers: `adjacent` (a list), else the copper layers next to the track's own in
        the stack.  Stations as in `coupled`; a station counts for a foreign net on layer M when
        the plan-view gap between the track's copper there and a track, arc, via PAD, pad or
        shape of that net on M is `margin` or less (0.0: the two outlines overlap or touch).
        Pours and bare holes never count: a plane under a track is what should be there.
        Returns {rows, totals}.  rows: {net, layer, other_layer, other, length, at (the first
        station), box}, longest first.  totals: per own net and layer {net, layer, length
        (routed), over: {M: the length with any foreign copper on M}, both: the length with
        foreign copper on EVERY one of the other layers at once (a track between two signal
        layers has no plane beside it there) -- None with fewer than two}."""
        rows, totals, state = {}, {}, {}
        for it in items:
            if it.kind not in ("track", "arc"):
                continue
            L = next(iter(it.layers))
            k = self._layer_key(L)
            others = list(adjacent) if adjacent else [M for M in (self.copper_layers[k - 1] if 0 < k < 99 else None,
                                                                 self.copper_layers[k + 1] if k + 1 < len(self.copper_layers) else None) if M]
            others = [M for M in others if M != L]
            tot = totals.setdefault((it.net, L), dict(net=it.net, layer=L, length=0.0, over={M: 0.0 for M in others},
                                                      both=0.0 if len(others) > 1 else None))
            for M in others:
                tot["over"].setdefault(M, 0.0)
            for pt, ds in self._stations(it, step):
                tot["length"] += ds
                seen = {}
                for j, edges in self._around(pt, it.r + margin, state).items():
                    o = self.items[j]
                    if o.kind not in ("track", "arc", "via", "pad", "shape") or (it.net and o.net == it.net) or o.net in ignore:
                        continue
                    hit = [M for M in others if M in o.layers and (M, o.net) not in seen]
                    if hit and self._disc_gap(pt, it.r, o, edges) <= margin + 1e-9:
                        for M in hit:
                            seen[(M, o.net)] = True
                on = {M for M, _n in seen}
                for M in on:
                    tot["over"][M] += ds
                if tot["both"] is not None and len(on) == len(others):
                    tot["both"] += ds
                for M, other in seen:
                    r = rows.setdefault((it.net, L, M, other), dict(net=it.net, layer=L, other_layer=M, other=other,
                                                                    length=0.0, at=pt, box=[pt[0], pt[1], pt[0], pt[1]]))
                    r["length"] += ds
                    b = r["box"]
                    r["box"] = [min(b[0], pt[0]), min(b[1], pt[1]), max(b[2], pt[0]), max(b[3], pt[1])]
        out = sorted(rows.values(), key=lambda r: (-r["length"], r["net"], r["layer"], r["other_layer"], r["other"]))
        return dict(rows=out, totals=sorted(totals.values(), key=lambda t: (t["net"], self._layer_key(t["layer"]))))

    def stitch(self, items, to):
        """Per via of a selection, the nearest via of the net(s) `to`: CENTRE TO CENTRE (the
        `stitch via` check's measure, tools/route/checks.py), whatever the layers.  Rows {via,
        centre, other (that via's Item), shared (the copper layers both span)}, FARTHEST first;
        centre None when the board has no via of `to` at all."""
        want = set(self.net_list(to))
        targets = [v for v in self.d.get("vias", []) if v["net"] in want]
        rows, done = [], set()
        by_src = {}
        for i in self.items:
            if i.kind == "via":
                by_src[id(i.src)] = i                      # its pad, where it has one
            elif i.kind == "via-hole":
                by_src.setdefault(id(i.src), i)
        for it in items:
            if it.kind not in ("via", "via-hole") or id(it.src) in done:
                continue
            done.add(id(it.src))
            best = min((v for v in targets if v is not it.src), key=lambda v: math.dist(v["at"], it.pts[0]), default=None)
            rows.append(dict(via=by_src[id(it.src)], centre=math.dist(best["at"], it.pts[0]) if best else None,
                             other=by_src[id(best)] if best else None,
                             shared=[L for L in it.src["layers"] if best and L in best["layers"]]))
        rows.sort(key=lambda r: (r["centre"] is not None, -(r["centre"] or 0.0), r["via"].id))
        return rows

    def conn(self, net):
        """Is a net one piece?  {net, pads, pieces, loose}.

        A PIECE is copper of the net that touches: two objects are joined when their copper
        overlaps or touches (gap 0.0) on a copper layer both are on; a via or a plated hole joins
        its layers; a pour joins what stands in its fill.  pieces: {pads (names), tracks, vias,
        pours, shapes, length, box}, the one with most pads first -- a routed net is ONE piece
        holding all its pads; a pad nothing reaches is a piece of its own.
        loose: the track ends that land in no other copper of the net -- the end POINT lies
        outside every other track, via, pad, shape and pour of the net on its layer (KiCad
        connects a track by its end point) -- each {at, layer, track, gap}: gap is from the
        track's copper at that end to the nearest other copper of the net there (0.0: the copper
        touches although the end point is outside it; None: nothing of the net within 0.5 mm)."""
        n = self.net(net)
        its = [i for i in self.items if i.net == n and i.kind != "hole"]
        parent = {i.id: i.id for i in its}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def join(x, y):
            x, y = find(x), find(y)
            if x != y:
                parent[max(x, y)] = min(x, y)
        by_src = {}
        for i in its:
            if id(i.src) in by_src and i.kind not in ("pour", "shape", "arc"):
                join(i.id, by_src[id(i.src)])               # a via and its bare hole, a pad and its hole
            by_src.setdefault(id(i.src), i.id)
        fills = [i for i in its if i.kind == "pour"]
        for it in its:
            if it.kind == "pour":
                continue
            mine = it.edges()
            for j, edges in self._near(it.box, 0.0).items():
                o = self.items[j]
                if j not in parent or j == it.id or not (o.layers & it.layers) or (o.kind != "pour" and j < it.id):
                    continue
                if find(j) == find(it.id):
                    continue
                g = min(seg_seg(a, e, c, d)[0] for a, e in mine for c, d in edges) - it.r - o.r
                if g <= 1e-9 or o.contains(it.pts[0]) or it.contains(o.pts[0]):
                    join(it.id, j)
            for o in fills:
                if (o.layers & it.layers) and find(o.id) != find(it.id) and o.contains(it.pts[0]):
                    join(it.id, o.id)
        groups = {}
        for i in its:
            groups.setdefault(find(i.id), []).append(i)
        pieces = []
        for g in groups.values():
            tracks = {id(i.src): i for i in g if i.kind in ("track", "arc")}
            length = sum(t.src["length"] if "mid" in t.src else math.dist(t.src["a"], t.src["b"]) for t in tracks.values())
            pieces.append(dict(
                pads=sorted({f"{i.src['ref']}.{i.src['num']}" for i in g if i.kind in ("pad", "pad-hole")}),
                tracks=len(tracks), vias=len({id(i.src) for i in g if i.kind in ("via", "via-hole")}),
                pours=sum(1 for i in g if i.kind == "pour"), shapes=len({id(i.src) for i in g if i.kind == "shape"}),
                length=length, box=[min(i.box[0] for i in g), min(i.box[1] for i in g),
                                    max(i.box[2] for i in g), max(i.box[3] for i in g)]))
        pieces.sort(key=lambda p_: (-len(p_["pads"]), -p_["length"], p_["box"]))
        loose, state = [], {}
        for it in its:
            if it.kind not in ("track", "arc"):
                continue
            L = next(iter(it.layers))
            ends = [it.pts[0], it.pts[-1]]
            if it.kind == "arc":                           # a chord inside the arc is no end of it
                ends = [e for e in ends if e in (tuple(it.src["pts"][0]), tuple(it.src["pts"][-1]))]
            for end in ends:
                landed, near = False, None
                for j, edges in self._around(end, it.r + 0.5, state).items():
                    o = self.items[j]
                    if j not in parent or o.src is it.src or L not in o.layers:
                        continue
                    g = self._disc_gap(end, 0.0, o, edges)
                    if g <= 1e-9:
                        landed = True
                        break
                    near = max(0.0, g - it.r) if near is None else min(near, max(0.0, g - it.r))
                if not landed and any(L in o.layers and o.contains(end) for o in fills):
                    landed = True
                if not landed:
                    loose.append(dict(at=end, layer=L, track=it.name, gap=near if near is not None and near <= 0.5 else None))
        loose.sort(key=lambda r: (self._layer_key(r["layer"]), r["at"]))
        return dict(net=n, pads=sum(len(p_["pads"]) for p_ in pieces), pieces=pieces, loose=loose)

    def lengths(self, nets=None, max_width=None, box=None, layers=None):
        """Every net's track length: rows {net, total, layers: {L: length}, segments, vias},
        longest first; nets with no track counted are left out.

        length: centre-line length of tracks and arcs (an arc by its chords when clipped), summed
        per layer and over the layers.  `nets`: a name or a pattern (default: every net).
        `max_width`: only copper this wide or narrower counts -- a neck-down's whole length on a
        net.  `box` (x0, y0, x1, y1): only the part of each centre line inside the rectangle
        counts, and only the vias whose centre is in it.  `layers`: only those copper layers."""
        want = set(self.net_list(nets)) if nets else None
        if box:
            box = (min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3]))
        rows = {}
        for t in self.d.get("tracks", []) + self.d.get("arcs", []):
            if (want is not None and t["net"] not in want) or (layers and t["layer"] not in layers):
                continue
            if max_width is not None and t["w"] > max_width + 1e-9:
                continue
            arc = "mid" in t
            if box:
                p = t["pts"] if arc else [t["a"], t["b"]]
                ln = sum(clip_len(a, b, box) for a, b in zip(p, p[1:]))
            else:
                ln = t["length"] if arc else math.dist(t["a"], t["b"])
            if ln <= 0.0:
                continue
            r = rows.setdefault(t["net"], dict(net=t["net"], total=0.0, layers={}, segments=0, vias=0))
            r["total"] += ln
            r["segments"] += 1
            r["layers"][t["layer"]] = r["layers"].get(t["layer"], 0.0) + ln
        for v in self.d.get("vias", []):
            if v["net"] in rows and (not box or (box[0] <= v["at"][0] <= box[2] and box[1] <= v["at"][1] <= box[3])):
                rows[v["net"]]["vias"] += 1
        return sorted(rows.values(), key=lambda r: (-r["total"], r["net"]))

    def probe(self, at, radius=0.0, drill=None, net=None):
        """What a disc of `radius` at a point would meet -- can a via stand here.

        {layers: per copper layer {layer, gap, other, at}: the nearest copper there that is not
        of `net` (every net is foreign without one; pours count), gap from the disc's edge;
        hole: {gap, other, centre}: the nearest drilled hole, wall to wall from a hole of `drill`
        at the point (None without `drill`); edge: the disc's edge to the nearest outline
        segment (None when the dump has no outline)}.  Geometry only: the rule each gap is held
        to is the handoff's."""
        own = self.net(net) if net else ""
        at = (float(at[0]), float(at[1]))
        rows = []
        for L in self.copper_layers:
            it = Item("probe", own, [L], [at], radius, "probe", {})
            it.id = -1
            got = self.foreign(it, layer=L)
            rows.append(dict(layer=L, gap=got[0]["gap"] if got else None, other=got[0]["other"] if got else None,
                             at=got[0]["at"] if got else None))
        hole = None
        if drill is not None:
            me = Item("hole", own, [], [at], drill / 2, "probe hole", {})
            for v in self.d.get("vias", []):
                g = max(0.0, math.dist(v["at"], at) - drill / 2 - v["drill"] / 2)
                if hole is None or g < hole["gap"]:
                    hole = dict(gap=g, other=f"via {short(v['net'])} {fmt_pt(v['at'])} drill {v['drill']:g}",
                                centre=math.dist(v["at"], at))
            for p in self.d.get("pads", []):
                h = p.get("hole")
                if h:
                    o = Item("hole", p["net"], [], h["core"], h["r"], "", p)
                    g = core_gap(me, o)[0]
                    if hole is None or g < hole["gap"]:
                        hole = dict(gap=g, other=f"pad {p['ref']}.{p['num']} drill {'x'.join(f'{v:g}' for v in p['drill'])}",
                                    centre=math.dist(p["at"], at))
        edges = self.d["board"].get("edges") or []
        edge = max(0.0, min(pt_seg(at, a, b)[0] for a, b in edges) - radius) if edges else None
        return dict(at=at, layers=rows, hole=hole, edge=edge)


def lane_key(ln):
    """A lane's identity: two balls whose lanes share it compete for one track (two ring-2
    balls either side of one gap between ring-1 lands)."""
    return (ln["side"], round(ln["a"][0] if ln["side"] in ("-y", "+y") else ln["a"][1], 4))


def assign_lanes(balls):
    """Can every ball with a free lane leave AT ONCE, one track a lane?  A maximum bipartite
    matching (augmenting paths) of balls to their free lanes' keys.  -> ({ball: key}, [the
    balls left without one]).  A per-ball 'free lane' does not prove it: five ring-2 balls in
    a row have six gaps, and two gaps blocked can leave one ball short while each still has
    a free lane of its own."""
    opts = {b["ball"]: [lane_key(ln) for ln in b["lanes"] if ln["free"]] for b in balls}
    owner = {}

    def take(ball, seen):
        for k in opts[ball]:
            if k in seen:
                continue
            seen.add(k)
            if k not in owner or take(owner[k], seen):
                owner[k] = ball
                return True
        return False

    left = [b["ball"] for b in balls if opts[b["ball"]] and not take(b["ball"], set())]
    return {v: k for k, v in owner.items()}, left


def short(net):
    """A net name without its sheet path, for a table."""
    return (net or "").rsplit("/", 1)[-1] or "(no net)"


def load(path):
    """The Geometry of a snapshot folder (its geometry.json) or of a geometry.json itself."""
    p = Path(path)
    if p.is_dir():
        p = p / "geometry.json"
    if not p.exists():
        raise SystemExit(f"geom: no {p} -- a PCB snapshot writes it (tools/review/snapshot.py --kind pcb); "
                         "for an older snapshot: kicad_project/tools/kpy tools/review/dump.py "
                         "<snapshot>/snap/<board> <snapshot>/geometry.json")
    G = Geometry(json.loads(p.read_text()))
    G.dir = p.parent
    return G


# -- the command line -----------------------------------------------------------------

def table(header, rows):
    rows = [[("" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v)) for v in r] for r in rows]
    w = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(header)]
    print("  ".join(str(h).ljust(w[i]) for i, h in enumerate(header)).rstrip())
    print("  ".join("-" * w[i] for i in range(len(header))))
    for r in rows:
        print("  ".join(r[i].ljust(w[i]) for i in range(len(header))).rstrip())


def plain(v):
    """A row value for --json."""
    if isinstance(v, Item):
        return v.name
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return round(v, 6) if isinstance(v, float) else v


def emit(a, header, rows, dicts, note=""):
    if a.json:
        print(json.dumps([plain(d) for d in dicts], indent=1))
        return
    table(header, rows)
    if note:
        print(note)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", help="a PCB snapshot folder (out/reviews/<board>/<id>-pcb) or a geometry.json")
    ap.add_argument("query", choices=["info", "show", "nearest", "gap", "label", "joints", "via-pad", "holes",
                                      "copper", "lengths", "conn", "stitch", "coupled", "overlap", "probe", "box",
                                      "bare", "unused-pads", "lanes", "links", "ring", "neck"])
    ap.add_argument("args", nargs="*", help="the query's SEL / NET / REF / box")
    ap.add_argument("--layer", help="one copper layer")
    ap.add_argument("--within", type=float, help="nearest: every foreign object nearer than this (mm), not only the nearest; "
                    "coupled: the gap under which a track counts as beside foreign copper")
    ap.add_argument("--to", help="stitch: the net (or pattern) whose nearest via is looked for, e.g. GND")
    ap.add_argument("--over", type=float, help="stitch: only vias farther than this (mm); lengths: only nets longer than this")
    ap.add_argument("--adjacent", help="overlap: the other layers, comma-separated (default: the two next to the track's)")
    ap.add_argument("--margin", type=float, default=0.0, help="overlap: counts when the plan-view gap is this or less (default 0: copper over copper)")
    ap.add_argument("--step", type=float, default=0.01, help="coupled, overlap: the station pitch along a track (mm, default 0.01)")
    ap.add_argument("--max-width", type=float, help="lengths: only tracks this wide or narrower (a neck-down)")
    ap.add_argument("--box", dest="in_box", help="lengths: X0,Y0,X1,Y1 -- only the length inside this rectangle")
    ap.add_argument("--radius", type=float, default=0.0, help="probe: the disc's radius (a via's pad / 2)")
    ap.add_argument("--drill", type=float, help="probe: a hole of this diameter at the point, for hole to hole")
    ap.add_argument("--net", help="probe: the net the disc would be of (its own copper is not foreign); "
                    "bare: only pads of these nets (names or patterns, comma-separated)")
    ap.add_argument("--below", type=float, help="via-pad, holes, label: only rows under this (mm); holes default 1.0")
    ap.add_argument("--ignore", action="append", default=[], help="nearest, coupled, overlap: a net (or pattern) that is not foreign (a pair's other half); repeatable")
    ap.add_argument("--no-pours", action="store_true", help="nearest, coupled: leave zone fills out")
    ap.add_argument("--any-layer", action="store_true", help="gap: compare the two even when they share no layer")
    ap.add_argument("--all-nets", action="store_true", help="joints: every net")
    ap.add_argument("--all", action="store_true", help="every row (joints: the unflagged too; others: no row limit)")
    ap.add_argument("--allow", default="0,45,90", help="joints: the turns that are not flagged (default 0,45,90)")
    ap.add_argument("--top", type=int, default=20, help="rows printed (default 20)")
    ap.add_argument("--plan", help="a routing plan's JSON (route.py's tracks / vias) added to the board as plan-track / "
                    "plan-via; a relative path not found as given is looked for in the snapshot's board folder")
    ap.add_argument("--plan-only", action="store_true", help="nearest: measure against the --plan copper alone")
    ap.add_argument("--power", default="GND*,VSS*,AGND*,DGND*,+*,VDD*,VCC*,NVCC*,VREF*",
                    help="lanes: fnmatch patterns of the nets whose balls leave by vias, comma-separated "
                    "(default GND*,VSS*,AGND*,DGND*,+*,VDD*,VCC*,NVCC*,VREF*)")
    ap.add_argument("--rings", type=int, choices=[1, 2], default=2, help="lanes: the outer rings asked (default 2)")
    ap.add_argument("--track", type=float, help="lanes: the track width (default: the board's track_width rule)")
    ap.add_argument("--clear", type=float, help="lanes: the clearance (default: the board's clearance rule)")
    ap.add_argument("--reach", type=float, help="lanes: end each lane this far past the outer lands' centre line when "
                    "that is nearer than the courtyard line")
    ap.add_argument("--parts", default="C*", help="ring: the parts counted, an fnmatch pattern (default C*)")
    ap.add_argument("--res", type=float, default=0.05, help="neck: the raster pitch (mm, default 0.05)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    G = load(a.snapshot)
    if a.plan:
        pf = Path(a.plan)
        if not pf.exists() and not pf.is_absolute() and G.dir:
            pf = next((c for c in sorted((G.dir / "snap").glob(f"*/{a.plan}"))), pf)
        if not pf.exists():
            ap.error(f"--plan: no {a.plan}")
        G.add_plan(json.loads(pf.read_text()), pf.stem)
    if a.plan_only and not a.plan:
        ap.error("--plan-only needs --plan FILE")
    d = G.d
    q = a.query
    need = {"show": 1, "nearest": 1, "gap": 2, "copper": 1, "box": 1, "conn": 1, "stitch": 1, "coupled": 1,
            "overlap": 1, "probe": 1, "unused-pads": 1, "lanes": 1, "ring": 2, "neck": 3}
    if q in need and len(a.args) != need[q]:
        ap.error(f"{q} takes {need[q]} argument(s)")
    if q in ("bare", "links") and not a.args:
        ap.error(f"{q} takes one or more part references")
    if q == "lengths" and len(a.args) > 1:
        ap.error("lengths takes one net name or pattern, or none (every net)")
    if q == "stitch" and not a.to:
        ap.error("stitch needs --to NET (the net whose vias are looked for, e.g. --to GND)")
    if q == "coupled" and a.within is None:
        ap.error("coupled needs --within G (mm)")
    ignore = [n for pat in a.ignore for n in G.net_list(pat)]
    cut = (lambda rows: rows) if a.all else (lambda rows: rows[:a.top])

    def more(rows):
        return "" if a.all or len(rows) <= a.top else f"... {len(rows) - a.top} more row(s): --all or --top N"

    if q == "info":
        b = d["board"]
        src = d.get("source") or {}
        print(f"{src.get('file', 'geometry.json')} (KiCad {src.get('kicad', '?')}): {len(d.get('parts', []))} parts, {len(d.get('pads', []))} pads, "
              f"{len(d.get('tracks', []))} tracks, {len(d.get('arcs', []))} arcs, {len(d.get('vias', []))} vias, "
              f"{len(d.get('zones', []))} zones, {len(d.get('texts', []))} texts, {len(G.nets())} nets")
        print(f"copper layers: {', '.join(b['copper_layers'])}; outline box {b.get('outline_box')}; "
              f"{len(b.get('edges', []))} outline segment(s)")
        print("board rules: " + ", ".join(f"{k} {v:g}" for k, v in (b.get("rules") or {}).items()))
        for n, c in (b.get("netclasses") or {}).items():
            print(f"net class {n}: " + ", ".join(f"{k} {v:g}" for k, v in c.items()))
    elif q == "show":
        its = G.select(a.args[0], a.layer)
        emit(a, ["kind", "object", "layers", "box"],
             [[i.kind, i.name, ",".join(sorted(i.layers, key=G._layer_key)),
               " ".join(f"{v:.3f}" for v in i.box)] for i in cut(its)],
             [dict(kind=i.kind, object=i.name, layers=sorted(i.layers), box=i.box) for i in its], more(its))
    elif q == "nearest":
        rows = G.nearest(G.select(a.args[0], a.layer), a.layer, a.within, ignore, not a.no_pours,
                         ("plan-track", "plan-via") if a.plan_only else None)
        emit(a, ["gap", "own", "foreign", "on", "at"],
             [[r["gap"], r["own"].name, r["other"].name, ",".join(r["layers"]), fmt_pt(r["at"])] for r in cut(rows)],
             rows, more(rows) or ("" if rows else f"nothing foreign nearer than {a.within:g} mm" if a.within is not None
                               else "nothing foreign on its layers"))
    elif q == "gap":
        A, B = G.select(a.args[0], a.layer), G.select(a.args[1], a.layer)
        r = G.gap(A, B, a.layer, a.any_layer)
        if r is None:
            print("the two share no copper layer (--any-layer compares them anyway)")
            return 1
        emit(a, ["gap", "a", "b", "on", "at", "centre"],
             [[r["gap"], r["a"].name, r["b"].name, ",".join(r["layers"]) or "(no shared layer)", fmt_pt(r["at"]),
               r["centre"]]], [r])
    elif q == "label":
        refs = a.args or sorted({p["ref"] for p in d.get("parts", [])})
        rows, missing = [], []
        for ref in refs:
            r = G.label(ref)
            if r is None:
                missing.append(ref)
            else:
                rows.append(r)
        hidden = [r["ref"] for r in rows if not r["visible"]]
        if not a.args:
            rows = [r for r in rows if r["visible"]]
        if a.below is not None:
            rows = [r for r in rows if r["margin"] is not None and r["margin"] < a.below]
        rows.sort(key=lambda r: (r["margin"] is None, r["margin"] if r["margin"] is not None else 0.0, r["ref"]))

        def flag(r):
            if not r["visible"]:
                return "HIDDEN"
            return "NEARER ANOTHER PART" if r["margin"] is not None and r["margin"] < 0 else ""
        emit(a, ["ref", "label at", "layer", "own", "own pad", "other", "other pad", "margin", "own gap", "other gap", ""],
             [[r["ref"], fmt_pt(r["at"]), r["layer"], r["own"], r["own_pad"], r["other"], r["other_pad"], r["margin"],
               r["own_gap"], r["other_gap"], flag(r)] for r in cut(rows)], rows,
             (more(rows) + "\n" if more(rows) else "")
             + "own / other: label centre to pad centre; margin = other - own; the gaps: label box to pad copper"
             + (f"\nhidden (not printed, not measured): {len(hidden)} -- {', '.join(hidden[:40])}"
                + (" ..." if len(hidden) > 40 else "") if hidden else "")
             + (f"\nno Reference text at all: {', '.join(missing)}" if missing else ""))
    elif q == "joints":
        if not a.args and not a.all_nets:
            ap.error("joints takes a net, or --all-nets")
        allow = tuple(float(v) for v in a.allow.split(","))
        rows = []
        for n in (a.args or [None]):
            rows += G.joints(n, a.layer, allow)
        shown = rows if a.all else [r for r in rows if r["flag"]]
        emit(a, ["net", "layer", "at", "kind", "turn(s)", "", "inside"],
             [[short(r["net"]), r["layer"], fmt_pt(r["at"]), r["kind"], " ".join(f"{t:g}" for t in r["turns"]),
               "FLAG" if r["flag"] else "", r["free"] or ""] for r in (shown if a.all else shown[:a.top])], shown,
             f"{len(rows)} joint(s), {sum(1 for r in rows if r['flag'])} flagged (not {a.allow}), "
             f"{sum(1 for r in rows if r['free'])} inside a pad or via of the net (not flagged)"
             + ("" if a.all else "; --all lists every joint"))
    elif q == "via-pad":
        vias = G.select(a.args[0], a.layer) if a.args else None
        rows = G.via_pad(vias, a.layer)
        if a.below is not None:
            rows = [r for r in rows if r["min"] < a.below]
        emit(a, ["via", "own gap", "own pad", "other gap", "other pad", "centre"],
             [[r["via"].name, r["own_gap"], r["own_pad"].name if r["own_pad"] else "", r["other_gap"],
               r["other_pad"].name if r["other_pad"] else "", r["centre"]] for r in cut(rows)], rows,
             (more(rows) + "\n" if more(rows) else "") + "gap: via pad edge to pad copper edge, 0 = touching or in the pad; "
             "centre: via centre to the nearer pad's centre")
    elif q == "holes":
        rows = G.holes(a.below if a.below is not None else 1.0)
        emit(a, ["gap", "a", "b", "centre"], [[r["gap"], r["a"].name, r["b"].name, r["centre"]] for r in cut(rows)],
             rows, (more(rows) + "\n" if more(rows) else "") + f"{len(rows)} pair(s) under "
             f"{a.below if a.below is not None else 1.0:g} mm, wall to wall")
    elif q == "copper":
        c = G.copper(a.args[0], a.layer)
        if a.json:
            print(json.dumps(plain(c), indent=1))
            return 0
        print(f"{c['net']}: {c['total']:.4f} mm routed, {len(c['vias'])} via(s)")
        table(["layer", "length", "segments", "arcs", "widths (mm: length)"],
              [[L, p["length"], p["segments"], p["arcs"],
                ", ".join(f"{w:g}: {ln:.4f}" for w, ln in sorted(p["widths"].items()))] for L, p in c["layers"].items()])
        if c["vias"]:
            table(["via at", "pad", "drill", "layers"],
                  [[fmt_pt(v["at"]), v["d"], v["drill"], f"{v['layers'][0]}..{v['layers'][-1]}"] for v in cut(c["vias"])])
            if more(c["vias"]):
                print(more(c["vias"]))
    elif q == "lengths":
        box = [float(v) for v in a.in_box.split(",")] if a.in_box else None
        layers = a.layer.split(",") if a.layer else None
        rows = G.lengths(a.args[0] if a.args else None, a.max_width, box, layers)
        if a.over is not None:
            rows = [r for r in rows if r["total"] > a.over]
        cols = [L for L in G.copper_layers if any(L in r["layers"] for r in rows)]
        emit(a, ["net", "total"] + cols + ["segments", "vias"],
             [[short(r["net"]), r["total"]] + [r["layers"].get(L) for L in cols] + [r["segments"], r["vias"]] for r in cut(rows)],
             rows, (more(rows) + "\n" if more(rows) else "") + f"{len(rows)} net(s); centre-line length, mm"
             + (f"; only tracks {a.max_width:g} mm wide or narrower" if a.max_width is not None else "")
             + (f"; only inside {a.in_box}" if box else "") + (f"; only nets over {a.over:g} mm" if a.over is not None else ""))
    elif q == "conn":
        c = G.conn(a.args[0])
        if a.json:
            print(json.dumps(plain(c), indent=1))
            return 0
        one = len(c["pieces"]) == 1
        print(f"{c['net']}: {c['pads']} pad(s) in {len(c['pieces'])} piece(s)" + (" -- ONE PIECE" if one else " -- NOT ONE PIECE")
              + f"; {len(c['loose'])} loose track end(s)")
        table(["piece", "pads", "tracks", "vias", "pours", "length", "box", "pads in it"],
              [[k, len(p["pads"]), p["tracks"], p["vias"], p["pours"], p["length"], " ".join(f"{v:.2f}" for v in p["box"]),
                " ".join(p["pads"][:8]) + (f" ... {len(p['pads']) - 8} more" if len(p["pads"]) > 8 else "")]
               for k, p in enumerate(cut(c["pieces"]), 1)])
        if more(c["pieces"]):
            print(more(c["pieces"]))
        if c["loose"]:
            table(["loose end at", "layer", "track", "copper gap to the net"],
                  [[fmt_pt(r["at"]), r["layer"], r["track"], "> 0.5" if r["gap"] is None else r["gap"]] for r in cut(c["loose"])])
            if more(c["loose"]):
                print(more(c["loose"]))
    elif q == "stitch":
        rows = G.stitch(G.select(a.args[0], a.layer), a.to)
        far = [r for r in rows if r["centre"] is None or (a.over is not None and r["centre"] > a.over)]
        shown = far if a.over is not None else rows
        emit(a, ["centre", "via", f"nearest {a.to} via", "layers both span"],
             [["none" if r["centre"] is None else r["centre"], r["via"].name, r["other"].name if r["other"] else "",
               f"{r['shared'][0]}..{r['shared'][-1]}" if r["shared"] else ""] for r in cut(shown)], shown,
             (more(shown) + "\n" if more(shown) else "") + f"{len(rows)} via(s), centre to centre, farthest first"
             + (f"; {len(far)} farther than {a.over:g} mm" if a.over is not None else ""))
    elif q == "coupled":
        c = G.coupled(G.select(a.args[0], a.layer), a.within, a.layer, ignore, not a.no_pours, a.step)
        if a.json:
            print(json.dumps(plain(c), indent=1))
            return 0
        table(["net", "layer", "beside", "length", "least gap", "at", "over the box"],
              [[short(r["net"]), r["layer"], short(r["other"]), r["length"], r["gap"], fmt_pt(r["at"]),
                " ".join(f"{v:.2f}" for v in r["box"])] for r in cut(c["rows"])])
        if more(c["rows"]):
            print(more(c["rows"]))
        table(["net", "layer", "routed", f"nearer than {a.within:g} to any foreign copper"],
              [[short(t["net"]), t["layer"], t["length"], t["near"]] for t in c["totals"]])
    elif q == "overlap":
        c = G.overlap(G.select(a.args[0], a.layer), a.adjacent.split(",") if a.adjacent else None, a.margin, ignore, a.step)
        if a.json:
            print(json.dumps(plain(c), indent=1))
            return 0
        table(["net", "layer", "over / under", "on", "length", "from", "over the box"],
              [[short(r["net"]), r["layer"], short(r["other"]), r["other_layer"], r["length"], fmt_pt(r["at"]),
                " ".join(f"{v:.2f}" for v in r["box"])] for r in cut(c["rows"])])
        if more(c["rows"]):
            print(more(c["rows"]))
        table(["net", "layer", "routed", "with foreign copper on", "on every one at once"],
              [[short(t["net"]), t["layer"], t["length"], ", ".join(f"{M}: {v:.4f}" for M, v in t["over"].items()),
                t["both"]] for t in c["totals"]])
        print(f"plan view, gap {a.margin:g} mm or less; tracks, via pads, pads and shapes (never pours or bare holes)")
    elif q == "probe":
        x, y = (float(v) for v in a.args[0].split(",")[:2])
        r = G.probe((x, y), a.radius, a.drill, a.net)
        if a.json:
            print(json.dumps(plain(r), indent=1))
            return 0
        print(f"a disc of radius {a.radius:g} at {fmt_pt(r['at'])}" + (f", net {G.net(a.net)}" if a.net else ", every net foreign"))
        table(["layer", "gap", "nearest copper", "at"],
              [[w["layer"], w["gap"], w["other"].name if w["other"] else "nothing", fmt_pt(w["at"]) if w["at"] else ""]
               for w in r["layers"]])
        if r["hole"]:
            print(f"hole to hole (a {a.drill:g} mm drill here): {r['hole']['gap']:.4f} wall to wall to {r['hole']['other']} "
                  f"(centres {r['hole']['centre']:.4f})")
        if r["edge"] is not None:
            print(f"disc edge to the board outline: {r['edge']:.4f}")
    elif q == "box":
        box = [float(v) for v in a.args[0].replace("box:", "").split(",")]
        its = G.in_box(box, a.layer)
        o = G.others_in_box(box)
        if a.json:
            print(json.dumps(dict(copper=[i.name for i in its], parts=[p["ref"] for p in o["parts"]],
                                  texts=[t["text"] for t in o["texts"]],
                                  zones=[z.get("name") or z["net"] for z in o["zones"]]), indent=1))
            return 0
        table(["kind", "object", "layers"], [[i.kind, i.name, ",".join(sorted(i.layers, key=G._layer_key))] for i in cut(its)])
        if more(its):
            print(more(its))
        print("parts: " + (", ".join(f"{p['ref']} {fmt_pt(p['at'])} {p['side']}" for p in o["parts"]) or "none"))
        print("texts: " + (", ".join(f"'{t['text']}' {t['layer']} {fmt_pt(t['at'])}" for t in o["texts"]) or "none"))
        print("zones: " + (", ".join(f"{short(z['net'])} {'/'.join(z['layers'])}" + (" (rule area)" if z.get("keepout") else "")
                                     for z in o["zones"]) or "none"))
    elif q == "bare":
        rows = G.bare(a.args)
        if a.net:
            only = {n for pat in a.net.split(",") for n in G.net_list(pat)}
            rows = [r for r in rows if r["net"] in only]
        emit(a, ["pad", "net", "at"], [[r["pad"], short(r["net"]), fmt_pt(r["at"])] for r in cut(rows)], rows,
             (more(rows) + "\n" if more(rows) else "")
             + f"{len(rows)} pad(s) of {', '.join(a.args)}" + (f" on {a.net}" if a.net else "") + f" with a net and no track, via, shape or pour of it touching them")
    elif q == "links":
        rows = G.links(a.args)
        # a link the board declared (<board>/routing/vialink_exceptions.json in the snapshot) is listed, not counted
        exc = []
        for f in Path(a.snapshot).glob("snap/*/routing/vialink_exceptions.json") if Path(a.snapshot).is_dir() else ():
            exc += [e["via"] for e in json.loads(f.read_text())]
        for r in rows:
            r["declared"] = any(abs(e[0] - r["via"][0]) < 0.01 and abs(e[1] - r["via"][1]) < 0.01 for e in exc)
        bad = [r for r in rows if (r["shape"] not in ("axis", "via in pad") or r["off"] > 0.005) and not r["declared"]]
        show = rows if a.all else bad
        emit(a, ["pad", "net", "layer", "via", "shape", "pieces (angle to the pad's axes / length)", "off centre"],
             [[r["pad"], short(r["net"]), r["layer"], fmt_pt(r["via"]), r["shape"] + (" (declared)" if r["declared"] else ""),
               ", ".join(f"{x:g} deg / {L:.3f}" for x, L in zip(r["angles"], r["lengths"])), f"{r['off']:.3f}"] for r in cut(show)],
             show, (more(show) + "\n" if more(show) else "")
             + f"{len(rows)} link(s) of {', '.join(a.args)}; {len(bad)} not one axis piece out of the pad's centre"
             + (f", {sum(r['declared'] for r in rows)} more declared in the board's vialink_exceptions.json" if exc else "")
             + ("" if a.all else " (listed; --all lists every link)"))
    elif q == "neck":
        if not a.layer:
            ap.error("neck needs --layer L (one copper layer)")
        bx = []
        for t in a.args[1:]:
            try:
                v = [float(x) for x in t.split(",")]
            except ValueError:
                v = []
            if len(v) != 4:
                ap.error(f"neck: {t!r} is not a box X0,Y0,X1,Y1")
            bx.append((min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3])))
        R = G.neck(a.args[0], a.layer, bx[0], bx[1], a.res)
        emit(a, ["cut piece (x0, y0, x1, y1)"], [[", ".join(f"{x:.2f}" for x in p)] for p in cut(R["pieces"])], [R],
             (more(R["pieces"]) + "\n" if more(R["pieces"]) else "")
             + f"{a.args[0]} on {a.layer}: the narrowest cut between the copper in {a.args[1]} and the copper in "
             + f"{a.args[2]} is {R['width']:.3f} mm of copper ({R['width'] / 0.0254:.1f} mil), in "
             + f"{len(R['pieces'])} piece(s) (raster {R['res']:g} mm)")
    elif q == "ring":
        R = G.ring(a.args[0], a.args[1], a.parts)
        rows = R["caps"]
        emit(a, ["part", "value", "side", "where", "rail pad", "nearest ball", "pad to ball", "outside courtyard"],
             [[c["ref"], c["value"], c["side"], c["where"], c["pad"], c["ball"], f"{c['d']:.3f}", f"{c['out']:.3f}"]
              for c in cut(rows)], R, (more(rows) + "\n" if more(rows) else "")
             + f"{len(rows)} part(s) of {a.parts} on {a.args[1]}: {sum(c['where'] == 'under' for c in rows)} under "
             + f"{a.args[0]}'s courtyard, {sum(c['where'] == 'ring' for c in rows)} on the ring; {len(R['balls'])} ball(s) "
             + f"on the rail, the nearest to the courtyard edge {R['edge'][0][1]} at {R['edge'][0][0]:.3f} mm "
             + "(no ring part stands nearer than that to a ball)")
    elif q == "unused-pads":
        arg = a.args[0]
        part = next((p for p in d.get("parts", []) if p["ref"] == arg), None)
        if part:
            box = part.get("courtyard") or part.get("box")
            if not box:
                ap.error(f"{arg} has no courtyard and no pad box in this dump")
            where = f"{arg}'s courtyard"
        else:
            try:
                box = [float(v) for v in arg.replace("box:", "").split(",")]
                assert len(box) == 4
            except (ValueError, AssertionError):
                ap.error(f"unused-pads takes a part reference or X0,Y0,X1,Y1; no part is named {arg}")
            where = "the rectangle"
        rows = G.unused_pads(box)
        keep = [r for r in rows if r["drop"] is False]
        unknown = [r for r in rows if r["drop"] is None]
        if a.json:
            print(json.dumps(dict(box=box, vias=len(rows), drop=sum(1 for r in rows if r["drop"]), keep=len(keep), unknown=len(unknown),
                                  keeping=[dict(net=r["via"]["net"], at=r["via"]["at"], flashed=r["flashed"], span=r["span"]) for r in keep]), indent=1))
            return 0
        print(f"{where} ({', '.join(f'{v:g}' for v in box)}): {len(rows)} via(s); {sum(1 for r in rows if r['drop'])} drop their "
              f"unconnected pads, {len(keep)} keep a pad on every layer" + (f", {len(unknown)} unknown (a dump older than the key: "
              "re-dump the board)" if unknown else ""))
        table(["via that keeps its pads", "pads on", "of layers"],
              [[f"{short(r['via']['net'])} {fmt_pt(r['via']['at'])}", r["flashed"], r["span"]] for r in cut(keep)])
        if more(keep):
            print(more(keep))
    elif q == "lanes":
        r = G.lanes(a.args[0], [x for x in a.power.split(",") if x], a.track, a.clear, a.rings, a.reach)
        shut = [b for b in r["balls"] if not b["exit"]]
        part = [b for b in r["balls"] if b["exit"] and not all(ln["free"] for ln in b["lanes"])]
        if a.json:
            print(json.dumps(dict({k: v for k, v in r.items() if k != "balls"}, balls=[dict(
                b, lanes=[dict(ln, by=ln["by"].name if ln["by"] else None) for ln in b["lanes"]]) for b in r["balls"]]), indent=1))
            return 0
        print(f"{a.args[0]}: {r['cols']} x {r['rows']} lattice, pitch {r['pitch']:g} mm, lands on {r['layer']}; a {r['track']:g} mm "
              f"track at {r['clear']:g} mm, each lane to " + (f"{a.reach:g} mm past the outer lands or " if a.reach is not None else "")
              + f"the courtyard line, the nearer ({', '.join(f'{v:g}' for v in r['end'])})")
        print(f"{len(r['balls'])} signal ball(s) in rings 1-{a.rings}: {len(r['balls']) - len(shut)} with a free lane, "
              f"{len(shut)} with none" + (f"; {len(part)} more with a lane blocked (--all lists them)" if part and not a.all else ""))
        un = r["unassigned"]
        print(f"at once, one track a lane: " + ("every ball with a free lane leaves" if not un else
              f"{len(un)} cannot -- the lanes they share are taken: {', '.join(un)}"))
        rows = []
        for b in shut + (part if a.all else []):
            for ln in b["lanes"]:
                if ln["free"]:
                    continue
                along = f"x {ln['a'][0]:.3f}" if ln["side"] in ("-y", "+y") else f"y {ln['a'][1]:.3f}"
                rows.append([b["ball"], short(b["net"]), b["ring"], "none" if not b["exit"] else "another",
                             f"{ln['side']} at {along}", f"{ln['gap']:.4f}", ln["by"].name if ln["by"] else ""])
        table(["ball", "net", "ring", "free lane", "lane", "gap", "blocked by"], cut(rows))
        if more(rows):
            print(more(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
