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
    box      X0,Y0,X1,Y1         every object in a rectangle

SEL -- what a query is about:

    U1.A5          a pad            U1             every pad of a part
    net:NAME       a net's tracks, arcs and vias (not its pads)
    pads:NAME      a net's pads
    via:X,Y        the via whose centre is nearest that point (within 0.5 mm)
    track:X,Y      the track or arc whose copper is nearest that point (within 0.5 mm)
    uuid:PREFIX    the track, arc, via or zone whose uuid starts so
    box:X0,Y0,X1,Y1   the copper touching that rectangle

NAME is the net's full name, or its last path component when that is unique
("/cpu/DDR_A3" answers to DDR_A3).  --layer L narrows any query to one copper
layer.  --json prints the rows as JSON instead of a table.

THE DEFINITIONS (each function's docstring repeats its own):

  * Every copper object is a CORE grown by a radius r: a track is its centre
    line grown by half its width, a via its centre grown by half its pad, a
    round pad its centre grown by its radius, an oval pad the line between
    its two arc centres grown by half its narrow side, a rounded rectangle
    the inner rectangle grown by its corner radius, a rectangle itself with
    r = 0.  These are exact.  A custom, chamfered or trapezoid pad is KiCad's
    own outline polygon (curves within 0.002 mm); an arc track is a chain of
    chords within 0.001 mm of it; a pour is its filled polygons, as refilled
    by dump.py.
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
import json
import math
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

    def select(self, sel, layer=None):
        """The Items a SEL names (see the module docstring), narrowed to `layer` when given."""
        its = self.items
        if sel.startswith("net:"):
            n = self.net(sel[4:])
            out = [i for i in its if i.net == n and i.kind in ("track", "arc", "via", "via-hole")]
        elif sel.startswith("pads:"):
            n = self.net(sel[5:])
            out = [i for i in its if i.net == n and i.kind in ("pad", "pad-hole")]
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
        g, seen, out = self.grid(), set(), {}
        for c in self._cells((box[0] - R, box[1] - R, box[2] + R, box[3] + R)):
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
        middle of the shortest line between the two; `centre` the distance between the two cores'
        first points (a via's or a round pad's centre; a track's start)."""
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
                                centre=math.dist(a.pts[0], b.pts[0]))
        return best

    def _layer_key(self, L):
        return self.copper_layers.index(L) if L in self.copper_layers else 99

    def foreign(self, item, layer=None, within=None, ignore=(), pours=True):
        """The FOREIGN copper nearest one Item: [{gap, other, at, layers}], nearest first.

        Foreign: another net (an object with no net is foreign to all); `ignore` names nets
        left out (a pair's other half).  Compared on shared copper layers only (`layer`: that
        one).  gap: edge to edge, 0.0 = touching or overlapping.  Without `within`, the list is
        the one nearest object (or empty: nothing foreign on its layers); with it, every foreign
        object nearer than `within` mm.  `pours=False` leaves zone fills out."""
        layers = item.layers & {layer} if layer else item.layers
        if not layers:
            return []

        def is_foreign(o):
            if o.id == item.id or o.src is item.src or not (o.layers & layers):
                return False
            if o.kind == "pour" and not pours:
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

    def nearest(self, items, layer=None, within=None, ignore=(), pours=True):
        """`foreign` for every Item of a selection: rows {own, gap, other, at, layers}, smallest
        gap first.  A selection with nothing foreign near it gives no row for that object."""
        rows = []
        for it in items:
            for h in self.foreign(it, layer, within, ignore, pours):
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
    return Geometry(json.loads(p.read_text()))


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
                                      "copper", "box"])
    ap.add_argument("args", nargs="*", help="the query's SEL / NET / REF / box")
    ap.add_argument("--layer", help="one copper layer")
    ap.add_argument("--within", type=float, help="nearest: every foreign object nearer than this (mm), not only the nearest")
    ap.add_argument("--below", type=float, help="via-pad, holes, label: only rows under this (mm); holes default 1.0")
    ap.add_argument("--ignore", action="append", default=[], help="nearest: a net that is not foreign (a pair's other half); repeatable")
    ap.add_argument("--no-pours", action="store_true", help="nearest: leave zone fills out")
    ap.add_argument("--any-layer", action="store_true", help="gap: compare the two even when they share no layer")
    ap.add_argument("--all-nets", action="store_true", help="joints: every net")
    ap.add_argument("--all", action="store_true", help="every row (joints: the unflagged too; others: no row limit)")
    ap.add_argument("--allow", default="0,45,90", help="joints: the turns that are not flagged (default 0,45,90)")
    ap.add_argument("--top", type=int, default=20, help="rows printed (default 20)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    G = load(a.snapshot)
    d = G.d
    q = a.query
    need = {"show": 1, "nearest": 1, "gap": 2, "copper": 1, "box": 1}
    if q in need and len(a.args) != need[q]:
        ap.error(f"{q} takes {need[q]} argument(s)")
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
        rows = G.nearest(G.select(a.args[0], a.layer), a.layer, a.within, [G.net(n) for n in a.ignore], not a.no_pours)
        emit(a, ["gap", "own", "foreign", "on", "at"],
             [[r["gap"], r["own"].name, r["other"].name, ",".join(r["layers"]), fmt_pt(r["at"])] for r in cut(rows)],
             rows, more(rows) or ("" if rows else "nothing foreign on its layers"))
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
