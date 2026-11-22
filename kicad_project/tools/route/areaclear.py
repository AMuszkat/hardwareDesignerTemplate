#!/usr/bin/env python3
"""areaclear.py -- copper that runs out of a relaxed DRC area keeps the clearance the area relaxes.

    tools/kpy tools/route/areaclear.py --board board1              # the board as it is; exit 1 on a breach
    tools/kpy tools/route/areaclear.py --board <copy dir> [--json OUT]

THE TRAP.  A custom rule that relaxes clearance over a rule area --

    (rule "fab: U1 first mm" (constraint clearance (min 0.1016mm))
      (condition "A.intersectsArea('U1_FIRST_MM') && B.intersectsArea('U1_FIRST_MM')"))

-- is judged by KiCad per ITEM: a track segment that starts inside the area and runs on past its edge
intersects the area, so the WHOLE segment is held to the relaxed number, outside the area too.  Two
45-degree lines 0.1035 mm apart ran 0.5 mm past U1_FIRST_MM's edge where their class needs 0.2032,
and DRC passed them (board1 R3ab, 2026-11-22).  The handoff's own words -- "break each track at the
area's edge; past it every class clearance holds" -- are what DRC cannot test.

THE CHECK.  The areas are read from the board's .kicad_dru: every rule with a clearance constraint
whose condition names `intersectsArea('NAME')` (its `(layer ...)` clause kept), and NAME's rule area
on the board (its outline and layers).  Every track segment (arcs as chords) that touches an area on
one of its layers is CLIPPED at the area's outline, and each piece outside it is measured against
every other net's copper on that layer -- tracks, flashed vias, flashed pads, pour fills -- whole,
wherever it lies.  The gap must be at least what applies without the area's rule:

    the larger of the two nets' class clearances and Board Setup's minimum clearance;
    for a coupled pair (KiCad's isCoupledDiffPair: names ending + / - or P / N) or a pair a rule
    names by `A.NetName == 'X' && B.NetName == 'Y'`, the smallest clearance those rules give.

So a line that leaves the area must already stand at its class spacing where it crosses the edge
(the opening is done inside the area, as #30 says).  A breach whose point lies inside ANOTHER relaxed
region -- another such area, or a courtyard a clearance rule names with `intersectsCourtyard` -- is
not reported: that region's own rule is in force there.  Not modelled: a pad's or a zone's local
clearance override, an unflashed via or pad (hole clearance is not a rule an area relaxes), vias and
pads straddling the edge (only tracks are clipped).

`route.py check` fails a plan that adds a breach (the board before and the scratch copy with the plan
are both measured); `route.py audit` prints an `area clear` line and fails on any breach.

The geometry (`clip`, `breaches`, `parse_dru`) is plain Python on lists, so
`tools/route/test_areaclear.py` drives it without KiCad.
"""
import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

EPS = 0.002            # mm: polygonised pads and fills, and the fill's own error
CELL = 0.5             # mm: the spatial index


# ---------------------------------------------------------------------------
# the .kicad_dru
# ---------------------------------------------------------------------------

def _blocks(text):
    """Each top-level (rule ...) of a .kicad_dru, comments dropped."""
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    out, depth, start, q = [], 0, None, False
    for i, ch in enumerate(text):
        if ch == '"':
            q = not q
        if q:
            continue
        if ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:i + 1])
    return [b for b in out if b.startswith("(rule")]


def parse_dru(text):
    """{"areas": [{"rule", "area", "min", "layer"}], "courtyards": {REF}, "pair_min": mm or None,
    "pair_names": [(patA, patB, mm)]} from a .kicad_dru's text."""
    areas, courts, pair_min, names = [], set(), None, []
    for blk in _blocks(text):
        m = re.search(r"\(constraint\s+clearance\s+\(min\s+([0-9.]+)\s*mm\)", blk)
        if not m:
            continue
        mn = float(m.group(1))
        name = re.match(r'\(rule\s+"([^"]*)"', blk)
        cond = re.search(r'\(condition\s+"([^"]*)"\)', blk)
        cond = cond.group(1) if cond else ""
        lay = re.search(r'\(layer\s+"?([^")\s]+)"?\)', blk)
        for a in sorted(set(re.findall(r"(?<!!)[AB]\.intersectsArea\('([^']+)'\)", cond))):
            areas.append({"rule": name.group(1) if name else "?", "area": a, "min": mn,
                          "layer": lay.group(1) if lay else None})
        courts |= set(re.findall(r"(?<!!)[AB]\.intersectsCourtyard\('([^']+)'\)", cond))
        if "isCoupledDiffPair()" in cond:
            pair_min = mn if pair_min is None else min(pair_min, mn)
        for pa, pb in re.findall(r"A\.NetName == '([^']+)' && B\.NetName == '([^']+)'", cond):
            names.append((pa, pb, mn))
    return {"areas": areas, "courtyards": courts, "pair_min": pair_min, "pair_names": names}


def _glob(pat, s):
    return re.fullmatch(".*".join(re.escape(p) for p in pat.split("*")), s) is not None


def _base(net):
    return net.rsplit("/", 1)[-1]


def coupled(a, b):
    """KiCad's isCoupledDiffPair: the same name but its last character, + / - or P / N."""
    a, b = _base(a), _base(b)
    if len(a) != len(b) or not a or a[:-1] != b[:-1]:
        return False
    return {a[-1], b[-1]} in ({"+", "-"}, {"P", "N"})


def layer_ok(spec, layer):
    if spec is None:
        return True
    outer = layer in ("F.Cu", "B.Cu")
    return spec == layer or (spec == "outer" and outer) or (spec == "inner" and not outer)


# ---------------------------------------------------------------------------
# geometry, on plain lists (mm)
# ---------------------------------------------------------------------------

def _pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    q = (a[0] + t * dx, a[1] + t * dy)
    return math.hypot(p[0] - q[0], p[1] - q[1]), q


def _cross(a, b, c, d):
    """The parameter t on a-b where it crosses c-d, or None."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-15:
        return None
    t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den
    u = ((c[0] - a[0]) * r[1] - (c[1] - a[1]) * r[0]) / den
    return t if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12 else None


def seg_seg(a, b, c, d):
    """(distance, point on a-b, point on c-d) between two segments."""
    if _cross(a, b, c, d) is not None:
        t = _cross(a, b, c, d)
        p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        return 0.0, p, p
    best = None
    for p, (x, y), on_ab in ((a, (c, d), False), (b, (c, d), False), (c, (a, b), True), (d, (a, b), True)):
        dd, q = _pt_seg(p, x, y)
        if best is None or dd < best[0]:
            best = (dd, q, p) if on_ab else (dd, p, q)
    return best


def inside(p, rings):
    """Even-odd over every ring (an outline and its holes)."""
    x, y = p
    c = False
    for r in rings:
        n = len(r)
        for i in range(n):
            (x1, y1), (x2, y2) = r[i], r[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                c = not c
    return c


def clip(a, b, rings):
    """The pieces of segment a-b OUTSIDE the polygon (rings: outline + holes), as [(p, q)]."""
    ts = {0.0, 1.0}
    for r in rings:
        n = len(r)
        for i in range(n):
            t = _cross(a, b, r[i], r[(i + 1) % n])
            if t is not None:
                ts.add(min(1.0, max(0.0, t)))
    ts = sorted(ts)
    at = lambda t: (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    out = []
    for t0, t1 in zip(ts, ts[1:]):
        if t1 - t0 < 1e-9 or inside(at((t0 + t1) / 2), rings):
            continue
        if out and abs(out[-1][1] - t0) < 1e-12:
            out[-1] = (out[-1][0], t1)
        else:
            out.append((t0, t1))
    return [(at(t0), at(t1)) for t0, t1 in out]


def touches(a, b, half, rings):
    """Does the track's copper (a stadium) meet the polygon?"""
    if inside(a, rings) or inside(b, rings):
        return True
    return any(seg_seg(a, b, r[i], r[(i + 1) % len(r)])[0] <= half for r in rings for i in range(len(r)))


def _item_dist(p, q, it):
    """(edge gap from the centreline p-q, point on p-q, point on the item) -- the piece's own half
    width is the caller's."""
    k = it["kind"]
    if k == "track":
        d, u, v = seg_seg(p, q, it["a"], it["b"])
        return d - it["w"] / 2, u, v
    if k == "via":
        d, u = _pt_seg(it["at"], p, q)
        return d - it["r"], u, it["at"]
    best = None
    for r in it["rings"]:
        for i in range(len(r)):
            d, u, v = seg_seg(p, q, r[i], r[(i + 1) % len(r)])
            if best is None or d < best[0]:
                best = (d, u, v)
    if it["kind"] == "poly" and (inside(p, it["rings"]) or inside(q, it["rings"])):
        return 0.0, p, p
    return best if best else (math.inf, p, p)


def _cells(x0, y0, x1, y1):
    for i in range(int(math.floor(x0 / CELL)), int(math.floor(x1 / CELL)) + 1):
        for j in range(int(math.floor(y0 / CELL)), int(math.floor(y1 / CELL)) + 1):
            yield i, j


def _bbox(it):
    k = it["kind"]
    if k == "track":
        h = it["w"] / 2
        return (min(it["a"][0], it["b"][0]) - h, min(it["a"][1], it["b"][1]) - h,
                max(it["a"][0], it["b"][0]) + h, max(it["a"][1], it["b"][1]) + h)
    if k == "via":
        return it["at"][0] - it["r"], it["at"][1] - it["r"], it["at"][0] + it["r"], it["at"][1] + it["r"]
    xs = [p[0] for r in it["rings"] for p in r]
    ys = [p[1] for r in it["rings"] for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def breaches(areas, items, required, skip_regions=(), reach=0.5):
    """Every breach of the clearance past each relaxed area.

    areas:    [{"area", "rule", "min", "layer" (None/outer/inner/name), "layers": {layer}, "rings"}]
    items:    [{"kind": "track", "net", "layer", "a", "b", "w"} | {"kind": "via", "net", "layer", "at", "r"}
               | {"kind": "pad" | "poly", "net", "layer", "rings", "what"?}]   (one entry per copper layer)
    required: f(net_a, net_b) -> mm, the clearance without the area's rule
    skip_regions: [(layer or None, rings)] -- other relaxed regions; a breach whose point lies in one is not
               reported
    -> [{"area", "layer", "net", "other", "kind", "at", "gap", "need", "piece"}], one per item pair, its
       worst point."""
    index = defaultdict(lambda: defaultdict(list))
    for n, it in enumerate(items):
        for c in _cells(*_bbox(it)):
            index[it["layer"]][c].append(n)
    out = {}
    for A in areas:
        for ti, t in enumerate(items):
            if t["kind"] != "track" or t["layer"] not in A["layers"] or not layer_ok(A["layer"], t["layer"]):
                continue
            h = t["w"] / 2
            if not touches(t["a"], t["b"], h, A["rings"]):
                continue
            for p, q in clip(t["a"], t["b"], A["rings"]):
                x0, y0 = min(p[0], q[0]) - h - reach, min(p[1], q[1]) - h - reach
                x1, y1 = max(p[0], q[0]) + h + reach, max(p[1], q[1]) + h + reach
                seen = set()
                for c in _cells(x0, y0, x1, y1):
                    for n in index[t["layer"]].get(c, ()):
                        if n in seen:
                            continue
                        seen.add(n)
                        o = items[n]
                        if n == ti or o["net"] == t["net"]:
                            continue
                        need = required(t["net"], o["net"])
                        g, u, v = _item_dist(p, q, o)
                        g -= h
                        if g >= need - EPS:
                            continue
                        mid = ((u[0] + v[0]) / 2, (u[1] + v[1]) / 2)
                        if any((L is None or L == t["layer"]) and inside(mid, R) for L, R in skip_regions):
                            continue
                        key = (A["area"], t["layer"], min(ti, n), max(ti, n))
                        if key not in out or g < out[key]["gap"]:
                            out[key] = {"area": A["area"], "rule": A["rule"], "layer": t["layer"], "net": t["net"],
                                        "other": o["net"], "kind": o.get("what", o["kind"]),
                                        "at": [round(u[0], 4), round(u[1], 4)], "gap": round(max(g, 0.0), 4),
                                        "to": [round(v[0], 4), round(v[1], 4)],
                                        "straddles": inside(v, A["rings"]),
                                        "need": need, "piece": [[round(p[0], 4), round(p[1], 4)],
                                                                [round(q[0], 4), round(q[1], 4)]]}
    # one line per net pair and place: two segments of a net meeting at a corner see the same breach
    best = {}
    for r in out.values():
        k = (r["area"], r["layer"], tuple(sorted((r["net"], r["other"]))), round(r["at"][0], 2), round(r["at"][1], 2))
        if k not in best or r["gap"] < best[k]["gap"]:
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["area"], r["layer"], r["at"][1], r["at"][0]))


def required_fn(class_clr, floor, rules):
    """f(a, b): the clearance without an area's rule -- the stricter class (or Board Setup's floor), or
    the pair rules' number for a coupled pair."""
    pm, names = rules.get("pair_min"), rules.get("pair_names", [])

    def f(a, b):
        for pa, pb, mn in names:
            if (_glob(pa, a) and _glob(pb, b)) or (_glob(pa, b) and _glob(pb, a)):
                return mn
        if pm is not None and coupled(a, b):
            return pm
        return max(class_clr(a), class_clr(b), floor)
    return f


def text(r):
    def sh(n):
        return _base(n) or "<no net>"
    return (f"{sh(r['net'])} / {sh(r['other'])} ({r['kind']}) on {r['layer']} at ({r['at'][0]:.3f}, {r['at'][1]:.3f}): "
            f"{r['gap']:.4f} mm, needs {r['need']:.4f} past {r['area']}"
            + (f" -- to its copper at ({r['to'][0]:.3f}, {r['to'][1]:.3f}) inside the area" if r.get("straddles") else
               " -- both outside the area") + " (its rule relaxes the whole segment)")


# ---------------------------------------------------------------------------
# the board (pcbnew)
# ---------------------------------------------------------------------------

def _rings(pn, sps):
    mm = pn.ToMM
    out = []
    for i in range(sps.OutlineCount()):
        chains = [sps.Outline(i)] + [sps.Hole(i, j) for j in range(sps.HoleCount(i))]
        for ch in chains:
            pts = [(mm(ch.CPoint(k).x), mm(ch.CPoint(k).y)) for k in range(ch.PointCount())]
            if len(pts) >= 3:
                out.append(pts)
    return out


def from_board(B):
    """(areas, items, required, skip_regions, rules) for a route.py RBoard (or place.Board with
    class_clearance)."""
    pn, b, mm = B.pn, B.b, B.pn.ToMM
    dru = next(Path(B.dir).glob("*.kicad_dru"), None)
    rules = parse_dru(dru.read_text() if dru else "")
    zones = {}
    for z in b.Zones():
        if z.GetIsRuleArea() and z.GetZoneName():
            zones.setdefault(z.GetZoneName(), []).append(z)
    areas, missing = [], []
    for r in rules["areas"]:
        zs = zones.get(r["area"])
        if not zs:
            missing.append(r["area"])
            continue
        for z in zs:
            areas.append(dict(r, layers={b.GetLayerName(l) for l in z.GetLayerSet().Seq() if pn.IsCopperLayer(l)},
                              rings=_rings(pn, z.Outline())))
    if not areas:
        return areas, [], None, [], dict(rules, missing=missing)
    names = {a["area"] for a in areas}
    skip = []
    for fp in b.GetFootprints():
        if fp.GetReference() in rules["courtyards"]:
            for side, lay in (("F", pn.F_CrtYd), ("B", pn.B_CrtYd)):
                cy = fp.GetCourtyard(lay)
                if cy.OutlineCount():
                    skip.append((None, _rings(pn, cy)))
    # the copper near the areas (every area's box, padded): a fill is clipped to it
    xs = [p[0] for a in areas for r in a["rings"] for p in r]
    ys = [p[1] for a in areas for r in a["rings"] for p in r]
    pad_ = 3.0
    box = (min(xs) - pad_, min(ys) - pad_, max(xs) + pad_, max(ys) + pad_)
    layers = set().union(*(a["layers"] for a in areas))
    lid = {n: b.GetLayerID(n) for n in layers}
    inbox = lambda x0, y0, x1, y1: not (x1 < box[0] or x0 > box[2] or y1 < box[1] or y0 > box[3])
    items = []
    for t in b.GetTracks():
        lay = t.GetLayerName()
        if t.Type() == pn.PCB_VIA_T:
            at = (mm(t.GetPosition().x), mm(t.GetPosition().y))
            if not inbox(at[0], at[1], at[0], at[1]):
                continue
            for n, L in lid.items():
                if t.IsOnLayer(L) and t.FlashLayer(L):
                    items.append({"kind": "via", "net": t.GetNetname(), "layer": n, "at": at, "r": mm(t.GetWidth(L)) / 2})
            continue
        if lay not in layers:
            continue
        w = mm(t.GetWidth())
        if t.Type() == pn.PCB_ARC_T:
            s, m_, e = t.GetStart(), t.GetMid(), t.GetEnd()
            pts = _arc_pts((mm(s.x), mm(s.y)), (mm(m_.x), mm(m_.y)), (mm(e.x), mm(e.y)))
        else:
            pts = [(mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y))]
        for a_, b_ in zip(pts, pts[1:]):
            if a_ == b_ or not inbox(min(a_[0], b_[0]), min(a_[1], b_[1]), max(a_[0], b_[0]), max(a_[1], b_[1])):
                continue
            items.append({"kind": "track", "net": t.GetNetname(), "layer": lay, "a": a_, "b": b_, "w": w})
    for pad in b.GetPads():
        p = pad.GetPosition()
        if not inbox(mm(p.x) - 3, mm(p.y) - 3, mm(p.x) + 3, mm(p.y) + 3):
            continue
        for n, L in lid.items():
            if not (pad.IsOnLayer(L) and pad.FlashLayer(L)):
                continue
            s = pn.SHAPE_POLY_SET()
            pad.TransformShapeToPolygon(s, L, 0, pn.FromMM(0.001), pn.ERROR_INSIDE)
            rg = _rings(pn, s)
            if rg:
                items.append({"kind": "pad", "net": pad.GetNetname(), "layer": n, "rings": rg,
                              "what": f"pad {pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}"})
    rect = pn.SHAPE_POLY_SET()
    rect.NewOutline()
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        rect.Append(pn.FromMM(x), pn.FromMM(y))
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for n, L in lid.items():
            if not z.IsOnLayer(L) or not z.HasFilledPolysForLayer(L):
                continue
            f = pn.SHAPE_POLY_SET(z.GetFilledPolysList(L))
            f.BooleanIntersection(rect)
            for i in range(f.OutlineCount()):
                one = pn.SHAPE_POLY_SET()
                one.AddOutline(f.Outline(i))
                for j in range(f.HoleCount(i)):
                    one.AddHole(f.Hole(i, j))
                rg = _rings(pn, one)
                if rg:
                    items.append({"kind": "poly", "net": z.GetNetname(), "layer": n, "rings": rg, "what": "pour"})
    floor = getattr(B, "clearance", None)
    if floor is None:
        floor = mm(b.GetDesignSettings().m_MinClearance)
    cc = {}

    def class_clr(net):
        if net not in cc:
            cc[net] = (B.class_clearance(net) if net and b.FindNet(net) else None) or 0.0
        return cc[net]
    # the other areas are skip regions for an area's pieces only when they are a DIFFERENT area
    for a in areas:
        a["others"] = [(None, x["rings"]) for x in areas if x["area"] != a["area"]]
    return areas, items, required_fn(class_clr, floor, rules), skip, dict(rules, missing=missing, names=sorted(names))


def _arc_pts(s, m, e, n=8):
    ax, ay = s
    bx, by = m
    cx, cy = e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [s, e]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, a1, am = (math.atan2(p[1] - uy, p[0] - ux) for p in (s, e, m))
    sweep = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    return [(ux + r * math.cos(a0 + sweep * k / n), uy + r * math.sin(a0 + sweep * k / n)) for k in range(n + 1)]


def run(B):
    """(breaches, info) on a board; info["areas"] is empty when no rule relaxes clearance over an area."""
    areas, items, req, skip, info = from_board(B)
    if not areas:
        return [], info
    out = []
    for a in areas:
        out += breaches([a], items, req, skip + a["others"])
    return out, info


def key(r):
    return (r["area"], r["layer"], r["net"], r["other"], round(r["at"][0], 2), round(r["at"][1], 2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="boardN, the folder name or a path (a scratch copy OUTSIDE kicad_project/)")
    ap.add_argument("--json", help="write the breaches here")
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import route as R
    B = R.RBoard(R.P.board_dir_arg(a.board))
    out, info = run(B)
    for m in info.get("missing", []):
        print(f"  area {m}: a rule names it, the board has no rule area of that name -- not judged")
    if not info.get("names"):
        print("AREACLEAR ----: the .kicad_dru relaxes clearance over no rule area on this board")
        return 0
    for r in out:
        print("  " + text(r))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))
    print(f"AREACLEAR {'PASS' if not out else 'FAIL'}: {len(out)} breach(es) past {', '.join(info['names'])}")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
