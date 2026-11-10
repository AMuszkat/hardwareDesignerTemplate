#!/usr/bin/env python3
"""dump.py -- one board's copper, holes, zones, texts and outline as plain numbers: geometry.json.

    kicad_project/tools/kpy tools/review/dump.py BOARD_DIR OUT.json

Run ONCE per PCB snapshot by snapshot.py, on the frozen copy (it imports
pcbnew). Everything a reviewer used to rebuild with an inline pcbnew script
before it could measure anything is written here once, so the measuring is
plain Python on a file: `tools/review/geom.py` answers the usual questions
from it, and `changes.py` diffs two of them object by object.

Every length is mm, every position KiCad's board coordinates (x right, y
DOWN), every angle degrees; numbers are rounded to 1e-6 mm, which is KiCad's
own nanometre grid, so nothing is lost.

  source        the board file, the KiCad version, this schema's version
  board         copper_layers (top to bottom), outline_box, edges (Edge.Cuts as
                straight pieces, arcs as chords on the centre line), thickness,
                rules (the board's own minimums: clearance, hole_to_hole,
                hole_clearance, edge_clearance, track_width, via_annular,
                via_diameter, through_drill, silk_clearance), netclasses and
                net_class (the nets that are not in Default)
  parts         ref, value, footprint, at, rot, side (F / B), courtyard box,
                box (its pads' box), dnp
  pads          ref, num, net, type (smd / pth / npth / conn), shape, at (the
                pad's position), size (x, y before rotation), rot (absolute),
                layers (the copper layers it has copper on), drill and hole
                (a drilled pad: the hole's centre line and radius), and the
                pad's copper as  core + r  (below)
  tracks        uuid, net, layer, a, b, w
  arcs          uuid, net, layer, a, mid, b, w, centre, radius, angle, length,
                pts (the centre line as chords, within 0.001 mm of the arc)
  vias          uuid, net, at, d (pad diameter), drill, type, layers (every
                copper layer of its span, top first), flashed (the layers
                where KiCad draws its pad; the others carry its bare hole)
  zones         uuid, net, name, layers, priority, keepout (a rule area: what
                it forbids), clearance, min_width, box, outline (polygons with
                holes), and filled per layer: area and the islands, each with
                area, box, outline and holes -- as REFILLED here, the way
                measure.py refills, so a fill left stale in the file cannot
                be measured
  texts         every text on the board: text (as shown), layer, part (its
                footprint's reference, or null for a board text), kind
                (reference / value / field / text), at, box, rot, height,
                thickness, visible, uuid.  A footprint's Reference and Value
                are always listed (visible or not), its other fields only when
                visible.

CORE + R, the one model geom.py measures with: a pad's copper is the set of
points within r of its core. A round pad is its centre (one point) and its
radius; an oval the line between its two arc centres and half its narrow
side; a rounded rectangle its inner rectangle and the corner radius; a
rectangle its four corners and r = 0. Those are exact. A trapezoid, a
chamfered or a custom pad is KiCad's own outline polygon (curves within
0.002 mm) and r = 0. Every exact core is CHECKED here against KiCad's own
outline of the same pad -- each vertex of it must lie on the modelled edge
within 0.005 mm -- and a pad that fails falls back to KiCad's polygon (it is
then marked `"model": "polygon"`), so a pad shape this file does not
understand is never measured wrong.

Limits, said here so no one finds them by a wrong number: a pad or via whose
padstack has a different shape per copper layer is dumped with its outermost
layer's shape on every layer it is flashed on; a pad's solder-mask and paste
openings, graphic lines on silk and the footprints' 3D models are not here
(measurements.json has silk-over-mask).
"""
import json
import math
import sys
from pathlib import Path

import pcbnew as pn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure import bbox, courtyard, edge_segments  # noqa: E402  (the same outline and courtyard the measurements use)

SCHEMA = 1
CHECK_TOL = 0.005            # mm: an exact pad model may differ this much from KiCad's own outline
mm = pn.ToMM


def r6(v):
    return round(v, 6)


def xy(p):
    return [r6(mm(p.x)), r6(mm(p.y))]


def rbox(b):
    return [r6(v) for v in b] if b else None


def rot_xy(x, y, deg):
    """A footprint-local offset turned by KiCad's angle (counter-clockwise on a screen whose y
    runs down): x' = x cos a + y sin a, y' = -x sin a + y cos a."""
    a = math.radians(deg)
    return x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)


def chain_points(c):
    return [[r6(mm(c.CPoint(k).x)), r6(mm(c.CPoint(k).y))] for k in range(c.PointCount())]


def poly_area(pts):
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                   for i in range(len(pts)))) / 2


def pts_box(pts):
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def polys(s):
    """A SHAPE_POLY_SET as [{outline, holes}]."""
    out = []
    for i in range(s.OutlineCount()):
        o = chain_points(s.Outline(i))
        if len(o) >= 3:
            out.append(dict(outline=o, holes=[chain_points(s.Hole(i, h)) for h in range(s.HoleCount(i))]))
    return out


def pt_seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if not L2 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])


def core_dist(p, core):
    """The distance from p to a core's boundary (a point, a segment, a closed polygon)."""
    if len(core) == 1:
        return math.dist(p, core[0])
    if len(core) == 2:
        return pt_seg(p, core[0], core[1])
    return min(pt_seg(p, core[i], core[(i + 1) % len(core)]) for i in range(len(core)))


PAD_TYPES = {pn.PAD_ATTRIB_PTH: "pth", pn.PAD_ATTRIB_SMD: "smd", pn.PAD_ATTRIB_CONN: "conn", pn.PAD_ATTRIB_NPTH: "npth"}
PAD_SHAPES = {pn.PAD_SHAPE_CIRCLE: "circle", pn.PAD_SHAPE_RECT: "rect", pn.PAD_SHAPE_OVAL: "oval",
              pn.PAD_SHAPE_TRAPEZOID: "trapezoid", pn.PAD_SHAPE_ROUNDRECT: "roundrect",
              pn.PAD_SHAPE_CHAMFERED_RECT: "chamfered", pn.PAD_SHAPE_CUSTOM: "custom"}
VIA_TYPES = {pn.VIATYPE_THROUGH: "through", pn.VIATYPE_BLIND: "blind", pn.VIATYPE_BURIED: "buried",
             pn.VIATYPE_MICROVIA: "micro"} if hasattr(pn, "VIATYPE_BURIED") else {}


def stadium(cx, cy, sx, sy, deg):
    """An oval (or a slot) of size sx x sy turned by deg about (cx, cy): (core, r)."""
    r = min(sx, sy) / 2
    h = (max(sx, sy) - min(sx, sy)) / 2
    if h < 1e-7:
        return [[r6(cx), r6(cy)]], r6(r)
    ends = ((-h, 0.0), (h, 0.0)) if sx > sy else ((0.0, -h), (0.0, h))
    return [[r6(cx + dx), r6(cy + dy)] for dx, dy in (rot_xy(x, y, deg) for x, y in ends)], r6(r)


def pad_core(p, lid):
    """(core, r, model) of a pad's copper on layer lid; see the module docstring."""
    shape = p.GetShape(lid)
    c = p.ShapePos(lid)
    cx, cy = mm(c.x), mm(c.y)
    sx, sy = mm(p.GetSize(lid).x), mm(p.GetSize(lid).y)
    deg = p.GetOrientationDegrees()
    outline = pn.SHAPE_POLY_SET()
    p.TransformShapeToPolygon(outline, lid, 0, pn.FromMM(0.002), pn.ERROR_INSIDE)
    kicad = [q for poly in polys(outline) for q in poly["outline"]]
    core, r = None, 0.0
    if shape == pn.PAD_SHAPE_CIRCLE:
        core, r = [[r6(cx), r6(cy)]], r6(sx / 2)
    elif shape == pn.PAD_SHAPE_OVAL:
        core, r = stadium(cx, cy, sx, sy, deg)
    elif shape in (pn.PAD_SHAPE_RECT, pn.PAD_SHAPE_ROUNDRECT):
        rr = mm(p.GetRoundRectCornerRadius(lid)) if shape == pn.PAD_SHAPE_ROUNDRECT else 0.0
        hx, hy = sx / 2 - rr, sy / 2 - rr
        if hx < 1e-7 and hy < 1e-7:
            core = [[r6(cx), r6(cy)]]
        elif hx < 1e-7 or hy < 1e-7:
            core = [[r6(cx + dx), r6(cy + dy)] for dx, dy in (rot_xy(x, y, deg) for x, y in ((-hx, -hy), (hx, hy)))]
        else:
            core = [[r6(cx + dx), r6(cy + dy)] for dx, dy in
                    (rot_xy(x, y, deg) for x, y in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)))]
        r = r6(rr)
    if core is not None and kicad:
        # KiCad's own outline of this pad: every vertex of it lies on the true edge
        worst = max(abs(core_dist(q, core) - r) for q in kicad)
        if worst <= CHECK_TOL:
            return core, r, "exact"
    if len(polys(outline)) == 1:
        return polys(outline)[0]["outline"], 0.0, "polygon"
    # a pad KiCad draws as several pieces (or as nothing): its box, so it is at least never missed
    b = bbox(p)
    return [[r6(b[0]), r6(b[1])], [r6(b[2]), r6(b[1])], [r6(b[2]), r6(b[3])], [r6(b[0]), r6(b[3])]], 0.0, "box"


def arc_points(t):
    """An arc track's centre line as chords within 0.001 mm of it, start to end."""
    c, st, en, md = t.GetCenter(), t.GetStart(), t.GetEnd(), t.GetMid()
    cx, cy, r = mm(c.x), mm(c.y), mm(t.GetRadius())
    ang = lambda p_: math.atan2(mm(p_.y) - cy, mm(p_.x) - cx)
    a0, a1, am = ang(st), ang(en), ang(md)
    sweep = (a1 - a0) % (2 * math.pi)
    if (am - a0) % (2 * math.pi) > sweep:
        sweep -= 2 * math.pi
    step = 2 * math.acos(max(-1.0, 1 - 0.001 / r)) if r > 0.001 else math.pi / 4
    n = max(2, int(math.ceil(abs(sweep) / step)))
    pts = [[r6(cx + r * math.cos(a0 + sweep * k / n)), r6(cy + r * math.sin(a0 + sweep * k / n))] for k in range(n + 1)]
    pts[0], pts[-1] = xy(st), xy(en)
    return pts, math.degrees(sweep)


def text_row(t, part, kind, b):
    box = bbox(t)
    return dict(text=t.GetShownText(False), layer=b.GetLayerName(t.GetLayer()), part=part, kind=kind,
                at=xy(t.GetPosition()), box=rbox(box), rot=r6(t.GetTextAngleDegrees()),
                height=r6(mm(t.GetTextHeight())), thickness=r6(mm(t.GetTextThickness())),
                visible=bool(t.IsVisible()), uuid=t.m_Uuid.AsString())


def dump(board_dir):
    board_dir = Path(board_dir)
    pcbs = sorted(board_dir.glob("*.kicad_pcb"))
    if not pcbs:
        sys.exit(f"dump: no .kicad_pcb in {board_dir}")
    b = pn.LoadBoard(str(pcbs[0]))
    pn.ZONE_FILLER(b).Fill(b.Zones())
    name = b.GetLayerName
    cu = [lid for lid in b.GetEnabledLayers().CuStack()]
    cu_set = set(cu)
    ds = b.GetDesignSettings()

    edges = edge_segments(b)
    ex = [p[0] for s in edges for p in s]
    ey = [p[1] for s in edges for p in s]
    ns = ds.m_NetSettings

    def klass(c):
        return dict(clearance=r6(mm(c.GetClearance())), track_width=r6(mm(c.GetTrackWidth())),
                    via_diameter=r6(mm(c.GetViaDiameter())), via_drill=r6(mm(c.GetViaDrill())))
    default = ns.GetDefaultNetclass()
    classes = {str(default.GetName()): klass(default)}
    for k, c in ns.GetNetclasses().items():
        classes[str(k)] = klass(c)
    net_class = {}
    for n, ni in b.GetNetsByName().items():
        c = str(ni.GetNetClassName())
        if str(n) and c and c != str(default.GetName()):
            net_class[str(n)] = c

    out = dict(
        source=dict(file=pcbs[0].name, kicad=pn.Version(), schema=SCHEMA, units="mm"),
        board=dict(
            copper_layers=[name(lid) for lid in cu],
            outline_box=[r6(min(ex)), r6(min(ey)), r6(max(ex)), r6(max(ey))] if edges else None,
            edges=[[[r6(a[0]), r6(a[1])], [r6(c[0]), r6(c[1])]] for a, c in edges],
            thickness=r6(mm(ds.GetBoardThickness())),
            rules=dict(clearance=r6(mm(ds.m_MinClearance)), hole_to_hole=r6(mm(ds.m_HoleToHoleMin)),
                       hole_clearance=r6(mm(ds.m_HoleClearance)), edge_clearance=r6(mm(ds.m_CopperEdgeClearance)),
                       track_width=r6(mm(ds.m_TrackMinWidth)), via_annular=r6(mm(ds.m_ViasMinAnnularWidth)),
                       via_diameter=r6(mm(ds.m_ViasMinSize)), through_drill=r6(mm(ds.m_MinThroughDrill)),
                       silk_clearance=r6(mm(ds.m_SilkClearance))),
            netclasses=classes, net_class=net_class),
        parts=[], pads=[], tracks=[], arcs=[], vias=[], zones=[], texts=[])

    models = {}
    for f in sorted(b.GetFootprints(), key=lambda f_: f_.GetReference()):
        ref = f.GetReference()
        pad_boxes = [bbox(p) for p in f.Pads()]
        out["parts"].append(dict(
            ref=ref, value=f.GetValue(), footprint=f.GetFPIDAsString(), at=xy(f.GetPosition()),
            rot=r6(f.GetOrientationDegrees()), side="B" if f.IsFlipped() else "F",
            courtyard=rbox(courtyard(f)),
            box=rbox(pts_box([(q[0], q[1]) for q in pad_boxes] + [(q[2], q[3]) for q in pad_boxes])) if pad_boxes else None,
            dnp=bool(f.IsDNP()), uuid=f.m_Uuid.AsString()))
        for p in f.Pads():
            kind = PAD_TYPES.get(p.GetAttribute(), "smd")
            on = [lid for lid in p.GetLayerSet().CuStack() if lid in cu_set]
            flashed = [lid for lid in on if p.FlashLayer(lid)] if kind != "npth" else []
            lid0 = flashed[0] if flashed else (on[0] if on else p.GetPrincipalLayer())
            core, r, model = pad_core(p, lid0)
            models[model] = models.get(model, 0) + 1
            row = dict(ref=ref, num=p.GetNumber(), net=p.GetNetname(), type=kind,
                       shape=PAD_SHAPES.get(p.GetShape(lid0), "custom"), at=xy(p.GetPosition()),
                       size=[r6(mm(p.GetSize(lid0).x)), r6(mm(p.GetSize(lid0).y))],
                       rot=r6(p.GetOrientationDegrees()), layers=[name(lid) for lid in flashed],
                       core=core, r=r)
            if model != "exact":
                row["model"] = model
            dr = p.GetDrillSize()
            if dr.x > 0 and dr.y > 0:
                row["drill"] = [r6(mm(dr.x)), r6(mm(dr.y))]
                hc, hr = stadium(mm(p.GetPosition().x), mm(p.GetPosition().y), mm(dr.x), mm(dr.y),
                                 p.GetOrientationDegrees())
                row["hole"] = dict(core=hc, r=hr)
            out["pads"].append(row)
        seen = set()
        for fld in f.GetFields():
            is_ref, is_val = fld.IsReference(), fld.IsValue()
            if not (is_ref or is_val or fld.IsVisible()):
                continue
            seen.add(fld.m_Uuid.AsString())
            out["texts"].append(text_row(fld, ref, "reference" if is_ref else "value" if is_val else "field", b))
        for g in f.GraphicalItems():
            if g.GetClass() == "PCB_TEXT" and g.m_Uuid.AsString() not in seen:
                out["texts"].append(text_row(g, ref, "text", b))
    for d in b.GetDrawings():
        if d.GetClass() == "PCB_TEXT":
            out["texts"].append(text_row(d, None, "text", b))

    for t in b.GetTracks():
        cls = t.GetClass()
        if cls == "PCB_VIA":
            top, bot = t.TopLayer(), t.BottomLayer()
            span = cu[cu.index(top):cu.index(bot) + 1] if top in cu_set and bot in cu_set else [top, bot]
            out["vias"].append(dict(
                uuid=t.m_Uuid.AsString(), net=t.GetNetname(), at=xy(t.GetPosition()),
                d=r6(mm(t.GetWidth(top))), drill=r6(mm(t.GetDrillValue())),
                type=VIA_TYPES.get(t.GetViaType(), str(t.GetViaType())),
                layers=[name(lid) for lid in span], flashed=[name(lid) for lid in span if t.FlashLayer(lid)]))
        elif cls == "PCB_ARC":
            pts, sweep = arc_points(t)
            out["arcs"].append(dict(
                uuid=t.m_Uuid.AsString(), net=t.GetNetname(), layer=name(t.GetLayer()), a=xy(t.GetStart()),
                mid=xy(t.GetMid()), b=xy(t.GetEnd()), w=r6(mm(t.GetWidth())), centre=xy(t.GetCenter()),
                radius=r6(mm(t.GetRadius())), angle=r6(sweep), length=r6(mm(t.GetLength())), pts=pts))
        else:
            out["tracks"].append(dict(uuid=t.m_Uuid.AsString(), net=t.GetNetname(), layer=name(t.GetLayer()),
                                      a=xy(t.GetStart()), b=xy(t.GetEnd()), w=r6(mm(t.GetWidth()))))
    key = lambda t_: (t_["net"], t_.get("layer", ""), t_.get("a", t_.get("at")), t_.get("b", []), t_["uuid"])
    for k in ("tracks", "arcs", "vias"):
        out[k].sort(key=key)

    for z in b.Zones():
        zl = [lid for lid in z.GetLayerSet().Seq()]
        o = polys(z.Outline())
        allp = [q for poly in o for q in poly["outline"]]
        row = dict(uuid=z.m_Uuid.AsString(), net=z.GetNetname(), name=z.GetZoneName(),
                   layers=[name(lid) for lid in zl], priority=int(z.GetAssignedPriority()),
                   box=rbox(pts_box(allp)) if allp else None, outline=o)
        if z.GetIsRuleArea():
            row["keepout"] = dict(tracks=bool(z.GetDoNotAllowTracks()), vias=bool(z.GetDoNotAllowVias()),
                                  pads=bool(z.GetDoNotAllowPads()), fills=bool(z.GetDoNotAllowZoneFills()),
                                  footprints=bool(z.GetDoNotAllowFootprints()))
        else:
            c = z.GetLocalClearance()
            row["clearance"] = r6(mm(c)) if c is not None else None
            row["min_width"] = r6(mm(z.GetMinThickness()))
            row["filled"] = {}
            for lid in zl:
                if lid not in cu_set:
                    continue
                isl = []
                for poly in polys(z.GetFilledPolysList(lid)):
                    area = poly_area(poly["outline"]) - sum(poly_area(h) for h in poly["holes"])
                    isl.append(dict(area=round(area, 4), box=rbox(pts_box(poly["outline"])), **poly))
                isl.sort(key=lambda i_: (-i_["area"], i_["box"]))
                row["filled"][name(lid)] = dict(area=round(sum(i_["area"] for i_ in isl), 4), islands=isl)
        out["zones"].append(row)
    out["zones"].sort(key=lambda z_: (z_["net"], z_["layers"], z_["box"] or [], z_["uuid"]))
    out["source"]["pad_models"] = models
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("\n\n")[1])
    d = dump(sys.argv[1])
    Path(sys.argv[2]).write_text(json.dumps(d, separators=(",", ":")) + "\n")
    s = d["source"]
    print(f"geometry: {len(d['parts'])} parts, {len(d['pads'])} pads, {len(d['tracks'])} tracks, "
          f"{len(d['arcs'])} arcs, {len(d['vias'])} vias, {len(d['zones'])} zones, {len(d['texts'])} texts; "
          f"pad models {s['pad_models']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
