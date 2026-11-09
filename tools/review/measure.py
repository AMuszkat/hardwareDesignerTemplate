#!/usr/bin/env python3
"""Measurements a PCB reviewer reads instead of measuring with pcbnew itself.

    kicad_project/tools/kpy tools/review/measure.py BOARD_DIR OUT.json

Run by snapshot.py on the frozen copy (it imports pcbnew). Facts only, no
verdicts; every length in mm, every position in KiCad's board coordinates
(`board.outline_origin` converts a handoff's outline-relative ones):

  board         outline box and origin, copper layers
  parts         per footprint: position, rotation, side, value, footprint,
                courtyard box, its placement.json group, and every pad: net, position
  groups        per placement.json group: extent, and for each member the
                courtyard gap to the nearest other member (an outlier stands out)
  nets          per net: routed length per layer, track widths, vias, layers
  pairs         nets named as a pair (_P/_N, +/-, P/N): each length, skew,
                edge-to-edge gap (min, median) and the length coupled at no
                more than 1.5x the minimum gap
  pours         per zone and layer: net, islands, area, each island's area, the
                fill's nearest approach to Edge.Cuts, and which of its net's pads on
                that layer the fill reaches and which it does not
  silk_over_mask  every silk item (lines, texts) that overlaps a pad's mask
                opening on its side: the part, the pad, where
  silk_texts    every visible silk text (reference, value, board text): its box and
                its distance to Edge.Cuts
  layout_rows   per numbered row of handoff/10_layout.md (every row, so an empty one
                says why): the refs it names that are not on the board, the refs it names
                (with H1..H4 ranges) and where each is; each "(x, y)" it gives
                and the nearest part to it, read as board coordinates and as
                outline-relative; the distances it states; every pair of its
                refs (up to 8): centre distance and nearest pad-to-pad gap
"""
import json
import math
import re
import statistics
import sys
from pathlib import Path

import pcbnew as pn

mm = pn.ToMM


def bbox(item):
    b = item.GetBoundingBox()
    return (mm(b.GetLeft()), mm(b.GetTop()), mm(b.GetRight()), mm(b.GetBottom()))


def rect_gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def courtyard(f):
    side = pn.B_CrtYd if f.IsFlipped() else pn.F_CrtYd
    items = [g for g in f.GraphicalItems() if g.GetLayer() == side]
    if not items:
        pads = [bbox(p) for p in f.Pads()]
        if not pads:
            return None
        return (min(p[0] for p in pads), min(p[1] for p in pads), max(p[2] for p in pads), max(p[3] for p in pads))
    bs = [bbox(g) for g in items]
    return (min(p[0] for p in bs), min(p[1] for p in bs), max(p[2] for p in bs), max(p[3] for p in bs))


def edge_segments(b):
    """Edge.Cuts as straight pieces (arcs polygonised), for distances."""
    out = []
    for d in b.GetDrawings():
        if d.GetLayer() != pn.Edge_Cuts:
            continue
        if d.GetShape() == pn.SHAPE_T_SEGMENT:
            out.append(((mm(d.GetStart().x), mm(d.GetStart().y)), (mm(d.GetEnd().x), mm(d.GetEnd().y))))
            continue
        s = pn.SHAPE_POLY_SET()
        d.TransformShapeToPolygon(s, pn.Edge_Cuts, 0, pn.FromMM(0.005), pn.ERROR_INSIDE)
        for i in range(s.OutlineCount()):
            o = s.Outline(i)
            q = [(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
            out += list(zip(q, q[1:] + q[:1]))
    return out


def poly_points(s):
    pts = []
    for i in range(s.OutlineCount()):
        o = s.Outline(i)
        pts += [(mm(o.CPoint(k).x), mm(o.CPoint(k).y)) for k in range(o.PointCount())]
    return pts


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if not L2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])


PAIR_SUFFIXES = (("_P", "_N"), ("+", "-"), ("P", "N"))


def pairs_of(names):
    out, used = [], set()
    for n in sorted(names):
        if n in used:
            continue
        for sp, sn in PAIR_SUFFIXES:
            if n.endswith(sp):
                m = n[: -len(sp)] + sn
                if m in names and m not in used:
                    out.append((n, m))
                    used |= {n, m}
                    break
    return out


REF_RE = re.compile(r"\b([A-Z][A-Z_]*\d+)(?:\s*\.\.\s*[A-Z_]*?(\d+))?\b")
XY_RE = re.compile(r"\((-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)\)")
DIST_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\b")


def layout_rows(board_dir):
    p = Path(board_dir) / "handoff" / "10_layout.md"
    if not p.exists():
        return []
    rows, cur = [], None
    for ln in p.read_text().splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)", ln)
        if m:
            cur = [int(m[1]), m[2]]
            rows.append(cur)
        elif cur and ln.strip() and not ln.startswith("#"):
            cur[1] += " " + ln.strip()
    return rows


def main():
    board_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    pcb = sorted(board_dir.glob("*.kicad_pcb"))[0]
    b = pn.LoadBoard(str(pcb))
    pj = board_dir / "placement.json"
    groups = json.loads(pj.read_text()).get("groups", {}) if pj.exists() else {}
    group_of = {r: g for g, refs in groups.items() for r in refs}

    edges = []
    for d in b.GetDrawings():
        if d.GetLayer() == pn.Edge_Cuts:
            e, h = bbox(d), mm(d.GetWidth()) / 2          # the line's centre, not its ink
            edges.append((e[0] + h, e[1] + h, e[2] - h, e[3] - h))
    outline = (min(e[0] for e in edges), min(e[1] for e in edges), max(e[2] for e in edges),
               max(e[3] for e in edges)) if edges else None
    copper = [b.GetLayerName(l) for l in b.GetEnabledLayers().CuStack()]
    M = {"units": "mm", "board": {"outline": outline, "outline_origin": outline[:2] if outline else None,
                                  "copper_layers": copper}}

    # parts
    fps = {f.GetReference(): f for f in b.GetFootprints()}
    parts, crt = {}, {}
    for r, f in sorted(fps.items()):
        crt[r] = courtyard(f)
        parts[r] = {"at": [round(mm(f.GetPosition().x), 4), round(mm(f.GetPosition().y), 4)],
                    "rot": round(f.GetOrientationDegrees(), 2), "side": "B" if f.IsFlipped() else "F",
                    "value": f.GetValue(), "footprint": str(f.GetFPID().GetUniStringLibId()),
                    "courtyard": [round(v, 4) for v in crt[r]] if crt[r] else None,
                    "group": group_of.get(r),
                    "pads": {p.GetNumber() or f"#{k}": {"net": p.GetNetname(),
                                                       "at": [round(mm(p.GetPosition().x), 4),
                                                              round(mm(p.GetPosition().y), 4)]}
                             for k, p in enumerate(f.Pads())}}
    M["parts"] = parts

    # groups
    G = {}
    for g, refs in groups.items():
        on = [r for r in refs if r in crt and crt[r]]
        if not on:
            G[g] = {"extent": None, "missing": refs}
            continue
        ext = (min(crt[r][0] for r in on), min(crt[r][1] for r in on), max(crt[r][2] for r in on),
               max(crt[r][3] for r in on))
        mem = {}
        for r in on:
            others = [rect_gap(crt[r], crt[o]) for o in on if o != r and fps[o].IsFlipped() == fps[r].IsFlipped()]
            mem[r] = {"nearest_member_gap": round(min(others), 3) if others else None}
        G[g] = {"extent": [round(v, 3) for v in ext], "members": mem,
                "missing": [r for r in refs if r not in fps]}
    M["groups"] = G

    # nets: tracks and vias
    segs = {}
    nets = {}
    for t in b.GetTracks():
        n = t.GetNetname()
        d = nets.setdefault(n, {"length": {}, "widths": set(), "vias": 0, "layers": set()})
        if t.GetClass() == "PCB_VIA":
            d["vias"] += 1
            continue
        L = b.GetLayerName(t.GetLayer())
        ln = mm(t.GetLength())
        d["length"][L] = d["length"].get(L, 0.0) + ln
        d["widths"].add(round(mm(t.GetWidth()), 4))
        d["layers"].add(L)
        if t.GetClass() == "PCB_TRACK":
            segs.setdefault(n, []).append(((mm(t.GetStart().x), mm(t.GetStart().y)),
                                           (mm(t.GetEnd().x), mm(t.GetEnd().y)), L, mm(t.GetWidth())))
    M["nets"] = {n: {"length": {k: round(v, 3) for k, v in d["length"].items()},
                     "total": round(sum(d["length"].values()), 3), "widths": sorted(d["widths"]),
                     "vias": d["vias"], "layers": sorted(d["layers"])}
                 for n, d in sorted(nets.items()) if n}

    # pairs
    allnets = {str(k) for k in b.GetNetsByName().keys() if str(k)}
    P = []
    for p_, n_ in pairs_of(allnets):
        lp, ln_ = M["nets"].get(p_, {}).get("total", 0.0), M["nets"].get(n_, {}).get("total", 0.0)
        gaps = []
        for a, c, L, w in segs.get(p_, []):
            k = max(1, int(math.dist(a, c) / 0.25))
            for i in range(k + 1):
                q = (a[0] + (c[0] - a[0]) * i / k, a[1] + (c[1] - a[1]) * i / k)
                ds = [seg_dist(q, a2, c2) - (w + w2) / 2 for a2, c2, L2, w2 in segs.get(n_, []) if L2 == L]
                if ds:
                    gaps.append(min(ds))
        row = {"p": p_, "n": n_, "length": [lp, ln_], "skew": round(abs(lp - ln_), 3)}
        if gaps:
            g0 = min(gaps)
            near = [g for g in gaps if g <= max(1.5 * g0, g0 + 0.05)]
            row.update(gap_min=round(g0, 4), gap_median=round(statistics.median(near), 4),
                       coupled_length=round(0.25 * len(near), 2))
        P.append(row)
    M["pairs"] = P

    # pours
    pn.ZONE_FILLER(b).Fill(b.Zones())
    E = edge_segments(b)
    err0 = pn.FromMM(0.005)
    pours = []
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().CuStack():
            fl = z.GetFilledPolysList(lid)
            areas = []
            for i in range(fl.OutlineCount()):
                s = pn.SHAPE_POLY_SET()
                s.AddOutline(fl.Outline(i))
                for h in range(fl.HoleCount(i)):
                    s.AddHole(fl.Hole(i, h))
                areas.append(round(s.Area() / 1e12, 2))
            pts = poly_points(fl)
            to_edge = min((seg_dist(q, a_, c_) for q in pts for a_, c_ in E), default=None)
            reached, not_reached = [], []
            for r, f in fps.items():
                for p in f.Pads():
                    if p.GetNetCode() != z.GetNetCode() or not p.IsOnLayer(lid):
                        continue
                    ps = pn.SHAPE_POLY_SET()
                    p.TransformShapeToPolygon(ps, lid, 0, err0, pn.ERROR_INSIDE)
                    ps.BooleanIntersection(fl)
                    (reached if ps.Area() > 0 else not_reached).append(f"{r}.{p.GetNumber()}")
            pours.append({"net": z.GetNetname(), "layer": b.GetLayerName(lid), "islands": len(areas),
                          "area_mm2": round(sum(areas), 2), "island_areas": sorted(areas, reverse=True),
                          "fill_to_edge": round(to_edge, 3) if to_edge is not None else None,
                          "pads_reached": sorted(reached), "pads_not_reached": sorted(not_reached)})
    M["pours"] = pours

    # silk over mask openings
    err = pn.FromMM(0.005)
    masks = {"F": [], "B": []}
    for r, f in fps.items():
        for p in f.Pads():
            for side, cu, mk in (("F", pn.F_Cu, pn.F_Mask), ("B", pn.B_Cu, pn.B_Mask)):
                if p.IsOnLayer(mk):
                    s = pn.SHAPE_POLY_SET()
                    exp = p.GetSolderMaskExpansion(mk) if hasattr(p, "GetSolderMaskExpansion") else 0
                    p.TransformShapeToPolygon(s, cu if p.IsOnLayer(cu) else mk, max(0, exp), err, pn.ERROR_OUTSIDE)
                    masks[side].append((f"{r}.{p.GetNumber()}", bbox(p), s))
    silk = []
    items = [(g, None) for g in b.GetDrawings()]
    for r, f in fps.items():
        items += [(g, r) for g in f.GraphicalItems()] + [(fd, r) for fd in f.GetFields() if fd.IsVisible()]
    for g, r in items:
        L = g.GetLayer()
        if L not in (pn.F_SilkS, pn.B_SilkS):
            continue
        side = "F" if L == pn.F_SilkS else "B"
        gb = bbox(g)
        cand = [m for m in masks[side] if rect_gap(gb, m[1]) < 0.3]
        if not cand:
            continue
        s = pn.SHAPE_POLY_SET()
        try:
            g.TransformShapeToPolygon(s, L, 0, err, pn.ERROR_INSIDE)
        except Exception:
            continue
        for pad, _pb, ms in cand:
            i = pn.SHAPE_POLY_SET(s)
            i.BooleanIntersection(ms)
            a = i.Area() / 1e12
            if a > 1e-4:
                silk.append({"part": r, "item": g.GetClass() + (f" '{g.GetText()}'" if hasattr(g, "GetText") else ""),
                             "layer": b.GetLayerName(L), "pad": pad, "overlap_mm2": round(a, 4),
                             "at": [round((gb[0] + gb[2]) / 2, 3), round((gb[1] + gb[3]) / 2, 3)]})
    M["silk_over_mask"] = silk
    texts = []
    for g, r in items:
        L = g.GetLayer()
        if L not in (pn.F_SilkS, pn.B_SilkS) or not hasattr(g, "GetText") or not g.IsVisible():
            continue
        gb = bbox(g)
        corners = [(gb[0], gb[1]), (gb[2], gb[1]), (gb[0], gb[3]), (gb[2], gb[3])]
        d_edge = min((seg_dist(q, a_, c_) for q in corners for a_, c_ in E), default=None)
        texts.append({"part": r, "text": g.GetShownText(False) if hasattr(g, "GetShownText") else g.GetText(),
                      "layer": b.GetLayerName(L), "box": [round(v, 3) for v in gb],
                      "edge_distance": round(d_edge, 3) if d_edge is not None else None})
    M["silk_texts"] = texts

    # the handoff's layout rows
    pad_boxes = {r: [bbox(p) for p in f.Pads()] for r, f in fps.items()}
    o = outline[:2] if outline else (0.0, 0.0)

    def nearest(x, y):
        best = None
        for r, c in crt.items():
            if c:
                d = rect_gap(c, (x, y, x, y))
                if best is None or d < best[1]:
                    best = (r, d)
        return {"ref": best[0], "courtyard_distance": round(best[1], 3)} if best else None

    R = []
    for num, text in layout_rows(board_dir):
        refs = []
        for m in REF_RE.finditer(text):
            if m[2]:
                pre = re.match(r"([A-Z][A-Z_]*?)(\d+)$", m[1])
                if pre and int(m[2]) >= int(pre[2]) and int(m[2]) - int(pre[2]) < 64:
                    refs += [f"{pre[1]}{k}" for k in range(int(pre[2]), int(m[2]) + 1)]
                    continue
            refs.append(m[1])
        named = list(dict.fromkeys(refs))
        refs = [r for r in named if r in fps]
        pts = [(float(x), float(y)) for x, y in XY_RE.findall(text)]
        row = {"row": num, "not_on_board": [r for r in named if r not in fps],
               "refs": {r: {k: parts[r][k] for k in ("at", "rot", "side")} for r in refs},
               "points": [{"xy": list(q), "as_board": nearest(*q), "as_outline_relative": nearest(q[0] + o[0], q[1] + o[1])}
                          for q in pts],
               "distances_stated": [float(d) for d in DIST_RE.findall(text)]}
        if 2 <= len(refs) <= 8:
            pw = []
            for i, a in enumerate(refs):
                for c in refs[i + 1:]:
                    gaps = [rect_gap(pa, pc) for pa in pad_boxes[a] for pc in pad_boxes[c]] or [None]
                    pw.append({"a": a, "b": c, "centre": round(math.dist(parts[a]["at"], parts[c]["at"]), 3),
                               "nearest_pad_gap": round(min(gaps), 3) if gaps[0] is not None else None})
            row["pairwise"] = pw
        R.append(row)
    M["layout_rows"] = R

    out.write_text(json.dumps(M, indent=1) + "\n")
    print(f"measurements.json: {len(parts)} parts, {len(M['nets'])} routed nets, {len(P)} pair(s), "
          f"{len(pours)} pour layer(s), {len(silk)} silk-over-mask overlap(s), {len(R)} layout row(s) measured")


if __name__ == "__main__":
    main()
