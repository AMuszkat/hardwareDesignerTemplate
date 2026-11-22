#!/usr/bin/env python3
"""tools/review/geom.py measured against numbers worked out by hand, and against pcbnew.

    python3 tests/test_review_geom.py

Two parts, on temporary files only:

  1. BY HAND. A geometry.json written here, small enough to check with a
     pencil (two 0.2 mm tracks whose centre lines are 0.5 mm apart: gap 0.3),
     and every measurement of geom.py asked of it: the gap between two
     selections, the nearest foreign copper (in a pour's hole, inside a pour,
     far away, on a layer where a via has only its hole), a label's own and
     other pad, joint angles (45, 90, a tee, an arc met on its tangent, a
     26.57-degree one flagged, one inside its own pad left alone), via to
     pad, hole to hole, a net's copper, a box; a copper shape; the length a
     track runs beside another net and over one on another layer; a net in
     three pieces with its loose ends; the nearest via of a net; every net's
     length by width, layer and box; a probe at a point; `added:`.
  2. AGAINST PCBNEW. tests/fixture_pcb.py's board, with tracks, an arc, vias
     and a refilled pour added (this file re-run under kicad_project/tools/kpy),
     dumped by tools/review/dump.py -- and every pair of copper objects on a
     shared layer measured twice: by geom.py from the dump, and by pcbnew's
     own collision engine on the board (the clearance at which
     SHAPE.Collide() starts to report a hit, found by bisection to 1 nm).
     Routed lengths, a label's distances, the holes and the joints likewise.

Exit 0 and `PASS: ...` / 1 and `FAIL: ...`; `SKIP: ...` (exit 0) when pcbnew
is not importable through kpy -- part 1 has still run, and failed loudly if
wrong.
"""
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KPY = ROOT / "kicad_project" / "tools" / "kpy"
TOL_PCBNEW = 0.002          # mm: an arc is chords within 0.001, a pour's fill is KiCad's own polygon


# ---- part 2, under kpy: lay copper on the fixture and measure it with pcbnew -----------------

def kpy_side(work):
    import pcbnew as pn
    mm, MM = pn.ToMM, pn.FromMM
    pcb = Path(work) / "fixture" / "fixture.kicad_pcb"
    b = pn.LoadBoard(str(pcb))
    nets = b.GetNetsByName()
    V = lambda x, y: pn.VECTOR2I(MM(x), MM(y))

    def track(net, layer, a, c, w=0.2):
        t = pn.PCB_TRACK(b)
        t.SetStart(V(*a)); t.SetEnd(V(*c)); t.SetWidth(MM(w)); t.SetLayer(layer); t.SetNet(nets[net])
        b.Add(t)
        return t

    def via(net, x, y):
        v = pn.PCB_VIA(b)
        v.SetPosition(V(x, y)); v.SetWidth(pn.F_Cu, MM(0.6)); v.SetDrill(MM(0.3)); v.SetLayerPair(pn.F_Cu, pn.B_Cu)
        v.SetNet(nets[net])
        b.Add(v)
        return v

    # SIG0: J1.1 -> a 45-degree jog -> a via -> B.Cu -> a via -> F.Cu -> J2.2
    for a, c in (((5, 11.19), (8, 11.19)), ((8, 11.19), (10, 13.19)), ((10, 13.19), (17, 13.19)),
                 ((33, 13.19), (44.46, 13.19)), ((44.46, 13.19), (45, 13.73))):
        track("SIG0", pn.F_Cu, a, c)
    track("SIG0", pn.B_Cu, (17, 13.19), (33, 13.19), 0.3)
    via("SIG0", 17, 13.19)
    via("SIG0", 33, 13.19)
    via("GND", 17.9, 13.19)                       # 0.9 mm from SIG0's: copper 0.3 apart, holes 0.6
    # SIG1: a quarter-circle arc met on its tangent at both ends
    track("SIG1", pn.F_Cu, (5, 13.73), (7, 13.73))
    arc = pn.PCB_ARC(b)
    arc.SetStart(V(7, 13.73)); arc.SetMid(V(7 + math.sqrt(2), 15.73 - math.sqrt(2))); arc.SetEnd(V(9, 15.73))
    arc.SetWidth(MM(0.2)); arc.SetLayer(pn.F_Cu); arc.SetNet(nets["SIG1"])
    b.Add(arc)
    track("SIG1", pn.F_Cu, (9, 15.73), (9, 17))
    # SIG2: a joint that is not 0 / 45 / 90 (about 30 degrees), and it crosses SIG1: gap 0
    track("SIG2", pn.F_Cu, (5, 16.27), (8, 16.27))
    track("SIG2", pn.F_Cu, (8, 16.27), (11, 18.0))
    # SIG3: a wide track under the GND pour and a via beside it (the refill bends round the via)
    track("SIG3", pn.F_Cu, (12, 5), (26, 5), 0.25)
    via("SIG3", 13.5, 2.5)
    # a copper polygon with a net (what a plan's "polys" become), and a line drawn on copper without one
    poly = pn.PCB_SHAPE(b)
    poly.SetShape(pn.SHAPE_T_POLY)
    poly.SetPolyPoints([V(30, 15), V(33, 15), V(33, 17), V(31.5, 18), V(30, 17)])
    poly.SetFilled(True); poly.SetWidth(0); poly.SetLayer(pn.F_Cu); poly.SetNet(nets["SIG3"])
    b.Add(poly)
    line = pn.PCB_SHAPE(b)
    line.SetShape(pn.SHAPE_T_SEGMENT)
    line.SetStart(V(30, 19)); line.SetEnd(V(33, 19.5)); line.SetWidth(MM(0.3)); line.SetLayer(pn.B_Cu)
    b.Add(line)
    pn.ZONE_FILLER(b).Fill(b.Zones())
    pn.SaveBoard(str(pcb), b)

    cu = [lid for lid in b.GetEnabledLayers().CuStack()]
    objs = []                                     # (selector, net, {layer id: SHAPE})
    keep = []                                     # the shapes' owners, kept alive
    for f in b.GetFootprints():
        for p in f.Pads():
            objs.append((f"{f.GetReference()}.{p.GetNumber()}", p.GetNetname(),
                         {lid: p.GetEffectiveShape(lid) for lid in cu if p.IsOnLayer(lid)}))
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            objs.append(("uuid:" + t.m_Uuid.AsString(), t.GetNetname(), {lid: t.GetEffectiveShape(lid) for lid in cu}))
        else:
            objs.append(("uuid:" + t.m_Uuid.AsString(), t.GetNetname(), {t.GetLayer(): t.GetEffectiveShape(t.GetLayer())}))
    for z in b.Zones():
        fills = {}
        for lid in cu:
            if z.IsOnLayer(lid):
                s = pn.SHAPE_POLY_SET(z.GetFilledPolysList(lid))
                keep.append(s)
                fills[lid] = s
        objs.append(("uuid:" + z.m_Uuid.AsString(), z.GetNetname(), fills))
    for g in (poly, line):
        objs.append(("uuid:" + g.m_Uuid.AsString(), g.GetNetname(), {g.GetLayer(): g.GetEffectiveShape(g.GetLayer())}))

    def clearance(s0, s1):
        """The distance between two shapes: the clearance at which they start to collide."""
        if s0.Collide(s1, 0):
            return 0.0
        lo, hi = 0, MM(80)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if s0.Collide(s1, mid):
                hi = mid
            else:
                lo = mid
        return mm(hi)

    pairs = []
    for i in range(len(objs)):
        for j in range(i + 1, len(objs)):
            for lid in cu:
                if lid in objs[i][2] and lid in objs[j][2]:
                    pairs.append(dict(a=objs[i][0], b=objs[j][0], net_a=objs[i][1], net_b=objs[j][1],
                                      layer=b.GetLayerName(lid), gap=clearance(objs[i][2][lid], objs[j][2][lid])))
    lengths = {}
    for t in b.GetTracks():
        if t.GetClass() != "PCB_VIA":
            k = f"{t.GetNetname()}|{b.GetLayerName(t.GetLayer())}"
            lengths[k] = lengths.get(k, 0.0) + mm(t.GetLength())
    labels = {}
    for f in b.GetFootprints():
        c = f.Reference().GetBoundingBox().GetCenter()
        ctr = lambda p: (mm(p.GetBoundingBox().GetCenter().x), mm(p.GetBoundingBox().GetCenter().y))
        own = min(math.dist((mm(c.x), mm(c.y)), ctr(p)) for p in f.Pads())
        other = min(math.dist((mm(c.x), mm(c.y)), ctr(p)) for g in b.GetFootprints() if g is not f
                    and g.GetReference() != f.GetReference() for p in g.Pads() if p.IsOnLayer(pn.F_Cu))
        labels[f.GetReference()] = dict(own=own, other=other)
    holes = []
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            holes.append((mm(t.GetPosition().x), mm(t.GetPosition().y), mm(t.GetDrillValue())))
    for f in b.GetFootprints():
        for p in f.Pads():
            if p.GetDrillSize().x > 0:
                holes.append((mm(p.GetPosition().x), mm(p.GetPosition().y), mm(p.GetDrillSize().x)))
    hole_gaps = sorted(math.dist(h[:2], g[:2]) - h[2] / 2 - g[2] / 2 for i, h in enumerate(holes) for g in holes[i + 1:])
    (Path(work) / "ref.json").write_text(json.dumps(dict(
        pairs=pairs, lengths=lengths, labels=labels, hole_gaps=[g for g in hole_gaps if g < 3.0],
        arc=dict(uuid=arc.m_Uuid.AsString(), length=mm(arc.GetLength()), radius=mm(arc.GetRadius())),
        shapes=sorted([poly.m_Uuid.AsString(), line.m_Uuid.AsString()]))))
    return 0


# ---- part 1: the hand-written board ---------------------------------------------------------

def rect(cx, cy, sx, sy):
    return [[cx - sx / 2, cy - sy / 2], [cx + sx / 2, cy - sy / 2], [cx + sx / 2, cy + sy / 2], [cx - sx / 2, cy + sy / 2]]


def hand_board():
    """Every number below is chosen so the expected results are short sums; see hand_checks."""
    def tr(net, layer, a, b, w=0.2):
        return dict(uuid=f"t-{net}-{a[0]}-{a[1]}", net=net, layer=layer, a=list(a), b=list(b), w=w)

    def pad(ref, num, net, at, core, r, layers=("F.Cu",), kind="smd", **kw):
        return dict(ref=ref, num=num, net=net, type=kind, shape="x", at=list(at), size=[0, 0], rot=0,
                    layers=list(layers), core=core, r=r, **kw)
    s2 = math.sqrt(2)
    c45 = [[15 + x * math.cos(math.pi / 4) + y * math.sin(math.pi / 4), 5 - x * math.sin(math.pi / 4) + y * math.cos(math.pi / 4)]
           for x, y in ((-1, -0.5), (1, -0.5), (1, 0.5), (-1, 0.5))]         # a 2 x 1 pad turned 45 degrees at (15, 5)
    n = 64
    arc_pts = [[15 + 2 * math.cos(math.pi / 2 * k / n), 15 + 2 * math.sin(math.pi / 2 * k / n)] for k in range(n + 1)]
    return dict(
        source=dict(file="hand.kicad_pcb", kicad="-", schema=1, units="mm"),
        board=dict(copper_layers=["F.Cu", "B.Cu"], outline_box=[-2, -2, 20, 20], edges=[], thickness=1.6,
                   rules=dict(clearance=0.2), netclasses={}, net_class={}),
        parts=[dict(ref=r, value="", footprint="", at=at, rot=0, side="F", courtyard=None, box=None, dnp=False)
               for r, at in (("R1", [12, 0]), ("R2", [15, 5]), ("R4", [12.3, 2.4]), ("R5", [3, 6]), ("J1", [2, 15]), ("H1", [2, 17]))]
        + [dict(ref="V1", value="", footprint="", at=[60, 0], rot=0, side="F", courtyard=[58, -2, 62, 2], box=None, dnp=False),
           dict(ref="U9", value="", footprint="", at=[80.75, 0.75], rot=0, side="F", courtyard=[79.5, -0.5, 82.0, 2.0], box=None,
                dnp=False)],
        tracks=[
            tr("A", "F.Cu", (0, 0), (10, 0)), tr("B", "F.Cu", (0, 0.5), (10, 0.5)),
            # net C: a chain with a 45, a 45, a 90 and a 26.57-degree joint, and a tee
            tr("C", "F.Cu", (0, 5), (2, 5)), tr("C", "F.Cu", (2, 5), (3, 6)), tr("C", "F.Cu", (3, 6), (3, 8)),
            tr("C", "F.Cu", (3, 8), (5, 8)), tr("C", "F.Cu", (5, 8), (6, 8.5), 0.3), tr("C", "F.Cu", (4, 8), (4, 9)),
            tr("E", "F.Cu", (15, 7), (15, 9)),
            tr("H", "F.Cu", (5, 11.5), (5, 12.5)),          # in the pour's hole
            tr("I", "F.Cu", (1, 12), (2, 12)),              # inside the pour's copper
            tr("K", "B.Cu", (18.5, 17), (18.5, 19)), tr("L", "F.Cu", (17.3, 17), (17.3, 19)),
            tr("M", "F.Cu", (17, 13), (17, 15)),            # meets the arc on its tangent
            # x >= 30, far from everything above: the queries along a track and across a net
            tr("P", "F.Cu", (30, 0), (40, 0)), tr("P", "B.Cu", (40, 0), (40, -3)),
            tr("P", "F.Cu", (44, 5), (44.5, 5)),            # a stub 0.1 short of its pad: a piece of its own
            tr("Q", "F.Cu", (32, 0.5), (36, 0.5)),          # beside P for 4 mm, copper 0.3 apart
            tr("R", "B.Cu", (35, -2), (35, 2), 0.4),        # crosses under P
            tr("S", "B.Cu", (31, 0.25), (33, 0.25)),        # under P's edge: plan-view gap 0.05
            tr("U", "In9.Cu", (34.9, -2), (34.9, 2)),       # a third layer, crossing P too
            tr("X", "F.Cu", (33, 10), (33, 12)),            # 0.9 from the copper shape of net W
            # links: C9's pads at x 90..102, 3 apart (holes over 1.6 apart), y 0, each 0.5 x 0.5
            tr("LK1", "F.Cu", (90, 0), (90, -0.4)), tr("LK1", "F.Cu", (90, -0.4), (90, -1.0)),   # one axis piece of 1.0
            tr("LK2", "F.Cu", (93, 0), (93, -0.3)), tr("LK2", "F.Cu", (93, -0.3), (93.5, -0.8)), # axis 0.3, then 45
            tr("LK3", "F.Cu", (96, 0), (96.5, -1.0)),       # atan(0.5 / 1.0) = 26.565 deg off the y axis
            tr("LK5", "F.Cu", (102.1, 0), (102.1, -1.0)),    # along an axis, 0.1 off the pad's centre
        ],
        shapes=[dict(uuid="s1", net="W", layer="F.Cu", kind="poly", box=[30, 10, 32, 12],
                     polys=[dict(outline=rect(31, 11, 2, 2), holes=[])])],
        arcs=[dict(uuid="arc-M", net="M", layer="F.Cu", a=[17, 15], mid=[15 + s2, 15 + s2], b=[15, 17], w=0.2,
                   centre=[15, 15], radius=2.0, angle=90.0, length=math.pi, pts=arc_pts)],
        vias=[dict(uuid="v1", net="A", at=[12, 0], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"]),
              dict(uuid="v3", net="J", at=[18, 18], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu"]),
              dict(uuid="vP", net="P", at=[40, 0], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"]),
              dict(uuid="vG", net="GNDX", at=[40, 3], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"]),
              # in V1's courtyard (58, -2)-(62, 2): one drops its unconnected pads, one keeps them, one on the
              # courtyard's corner from a dump without the key; a fourth 2 mm outside
              dict(uuid="vd", net="VV", at=[59, 0], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu"], drop_unused=True),
              dict(uuid="vk", net="VV", at=[61, 0], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"], drop_unused=False),
              dict(uuid="vu", net="VV", at=[62, 2], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"]),
              dict(uuid="vo", net="VV", at=[64, 0], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"], drop_unused=False),
              # 0.35 mm straight out of U9's ball A2 (80.5, 0), on its exit lane
              dict(uuid="v9", net="SH9", at=[80.5, -0.35], d=0.4, drill=0.2, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"])]
             + [dict(uuid=f"vl{n}", net=f"LK{n}", at=list(at), d=0.4, drill=0.2, type="through", layers=["F.Cu", "B.Cu"],
                     flashed=["F.Cu", "B.Cu"]) for n, at in ((1, (90, -1.0)), (2, (93.5, -0.8)), (3, (96.5, -1.0)),
                                                             (4, (99, 0.1)), (5, (102.1, -1.0)))],
        pads=[
            pad("R1", "1", "B", (12, 1.0), rect(12, 1.0, 1.0, 0.5), 0.0),
            pad("R1", "2", "A", (12, -1.0), [[12, -1.0]], 0.4),
            pad("R2", "1", "D", (15, 5), c45, 0.0),
            pad("R4", "1", "G", (12.3, 2.4), [[12.3, 2.4]], 0.1),
            pad("R5", "1", "C", (3, 6), [[3, 6]], 0.25),
            pad("J1", "1", "N", (2, 15), [[2, 15]], 0.8, ("F.Cu", "B.Cu"), "pth", drill=[1.0, 1.0],
                hole=dict(core=[[2, 15]], r=0.5)),
            pad("J1", "2", "O", (4.54, 15), [[4.54, 15]], 0.8, ("F.Cu", "B.Cu"), "pth", drill=[1.0, 1.0],
                hole=dict(core=[[4.54, 15]], r=0.5)),
            pad("H1", "", "", (2, 17), [[2, 17]], 0.5, (), "npth", drill=[1.0, 1.0], hole=dict(core=[[2, 17]], r=0.5)),
            pad("P1", "1", "P", (30, 0), [[30, 0]], 0.3),
            pad("P2", "1", "P", (40, -3), [[40, -3]], 0.3, ("B.Cu",)),
            pad("P3", "1", "P", (45, 5), [[45, 5]], 0.3),
        ] + [pad("C9", str(n), f"LK{n}", (x, 0), rect(x, 0, 0.5, 0.5), 0.0) for n, x in ((1, 90), (2, 93), (3, 96), (4, 99), (5, 102))] + [pad("U9", f"{'ABCD'[j]}{i + 1}", "GND" if (i, j) == (3, 3) else f"U9S{'ABCD'[j]}{i + 1}",
                 (80 + 0.5 * i, 0.5 * j), [[80 + 0.5 * i, 0.5 * j]], 0.1125) for j in range(4) for i in range(4)],
        zones=[dict(uuid="z-gnd", net="GND", name="", layers=["F.Cu"], priority=0, box=[0, 10, 10, 14],
                    outline=[dict(outline=rect(5, 12, 10, 4), holes=[])],
                    filled={"F.Cu": dict(area=36.0, islands=[dict(area=36.0, box=[0, 10, 10, 14], outline=rect(5, 12, 10, 4),
                                                                 holes=[rect(5, 12, 2, 2)])])})],
        texts=[dict(text="R1", layer="F.Silkscreen", part="R1", kind="reference", at=[12, 2.0], box=[11.5, 1.7, 12.5, 2.3],
                    rot=0, height=0.6, thickness=0.1, visible=True, uuid="x1"),
               dict(text="R2", layer="F.Silkscreen", part="R2", kind="reference", at=[0, 0], box=[-1, -1, 1, 1],
                    rot=0, height=0.6, thickness=0.1, visible=False, uuid="x2")])


class Checks:
    def __init__(self):
        self.n, self.bad = 0, []

    def eq(self, what, got, want, tol=1e-6):
        self.n += 1
        ok = (got == want) if not isinstance(want, float) else (got is not None and abs(got - want) <= tol)
        if not ok:
            self.bad.append(f"{what}: {got!r}, expected {want!r}")


def hand_checks(geom, path, c):
    G = geom.load(path)
    one = lambda sel, layer=None: G.select(sel, layer)
    gap = lambda a, b, **kw: G.gap(one(a), one(b), **kw)
    near = lambda sel, **kw: G.nearest(one(sel), **kw)
    # two 0.2 mm tracks, centre lines 0.5 mm apart
    c.eq("gap A / B tracks", gap("track:5,0", "track:5,0.5")["gap"], 0.3)
    c.eq("centre A / B tracks (their first points)", gap("track:5,0", "track:5,0.5")["centre"], 0.5)
    # via (r 0.3) at (12, 0): the rect pad's edge is at y 0.75, the round pad (r 0.4) is 1.0 away
    c.eq("gap via / rect pad", gap("via:12,0", "R1.1")["gap"], 0.45)
    c.eq("gap via / round pad", gap("via:12,0", "R1.2")["gap"], 0.3)
    # a point 2 mm from the centre of a 2 x 1 pad turned 45 degrees, on its diagonal axis
    want = math.hypot(math.sqrt(2) - 1, math.sqrt(2) - 0.5) - 0.1
    c.eq("gap track end / pad turned 45", gap("track:15,8", "R2.1")["gap"], want)
    # nearest foreign copper
    r = near("track:5,0")
    c.eq("nearest to track A: rows", len(r), 1)
    c.eq("nearest to track A: gap", r[0]["gap"], 0.3)
    c.eq("nearest to track A: who", r[0]["other"].net, "B")
    r = near("track:5,0", ignore=["B"])                  # then R4.1, sqrt(2.3^2 + 2.4^2) away, r 0.1
    c.eq("nearest to track A ignoring B: who", r[0]["other"].name, "pad R4.1 G")
    c.eq("nearest to track A ignoring B: gap", r[0]["gap"], math.hypot(2.3, 2.4) - 0.2)
    c.eq("within 0.25 of track A: none", len(near("track:5,0", within=0.25)), 0)
    c.eq("within 0.35 of track A: one", len(near("track:5,0", within=0.35)), 1)
    r = near("track:5,12")                               # in the pour's 2 x 2 hole: 0.5 to its edge, less 0.1
    c.eq("track in a pour's hole: gap", r[0]["gap"], 0.4)
    c.eq("track in a pour's hole: who", r[0]["other"].kind, "pour")
    c.eq("track inside a pour: gap 0", near("track:1.5,12")[0]["gap"], 0.0)
    c.eq("track inside a pour, pours left out: not the pour", near("track:1.5,12", pours=False)[0]["other"].kind != "pour", True)
    c.eq("tee of net C to the pour", near("track:4,8.5")[0]["gap"], 0.9)
    # a via flashed on F.Cu only: its pad on F.Cu, its bare hole on B.Cu
    c.eq("B.Cu track / unflashed via: hole wall", gap("track:18.5,18,B.Cu", "via:18,18")["gap"], 0.5 - 0.15 - 0.1)
    c.eq("F.Cu track / via pad", gap("track:17.3,18,F.Cu", "via:18,18")["gap"], 0.7 - 0.3 - 0.1)
    c.eq("tracks on different layers: no gap", gap("track:18.5,18,B.Cu", "track:17.3,18,F.Cu"), None)
    c.eq("... unless asked", gap("track:18.5,18,B.Cu", "track:17.3,18,F.Cu", any_layer=True)["gap"], 1.0)
    # through pads 2.54 apart, 1.6 mm pads, 1.0 mm holes
    c.eq("gap J1.1 / J1.2 copper", gap("J1.1", "J1.2")["gap"], 0.94)
    h = G.holes(below=1.6)
    c.eq("holes under 1.6: pairs", len(h), 2)
    c.eq("hole J1.1 / H1", h[0]["gap"], 1.0)
    c.eq("hole J1.1 / J1.2", h[1]["gap"], 1.54)
    c.eq("hole J1.1 / J1.2 centre", h[1]["centre"], 2.54)
    # the label of R1: centre (12, 2.0); own pads 1.0 and 3.0 away, R4's pad 0.5 away
    L = G.label("R1")
    c.eq("label own", L["own"], 1.0)
    c.eq("label own pad", L["own_pad"], "R1.1")
    c.eq("label other", L["other"], 0.5)
    c.eq("label other pad", L["other_pad"], "R4.1")
    c.eq("label margin", L["margin"], -0.5)
    c.eq("label own gap (box to pad edge)", L["own_gap"], 0.45)
    c.eq("label other gap (the pad touches the box)", L["other_gap"], 0.0)
    c.eq("hidden label: not measured", (G.label("R2")["visible"], G.label("R2")["own"]), (False, None))
    c.eq("no label", G.label("J1"), None)
    # joints of net C
    J = {(round(j["at"][0], 3), round(j["at"][1], 3)): j for j in G.joints("C")}
    c.eq("joints of C", len(J), 5)
    c.eq("joint (2,5)", (J[(2, 5)]["turns"], J[(2, 5)]["flag"], J[(2, 5)]["kind"]), ([45.0], False, "corner"))
    c.eq("joint (3,6) inside its pad", (J[(3, 6)]["turns"], J[(3, 6)]["flag"], J[(3, 6)]["free"]), ([45.0], False, "pad R5.1"))
    c.eq("joint (3,8)", (J[(3, 8)]["turns"], J[(3, 8)]["flag"]), ([90.0], False))
    c.eq("joint (5,8) flagged", (J[(5, 8)]["turns"], J[(5, 8)]["flag"]),
         ([round(math.degrees(math.atan2(0.5, 1.0)), 2)], True))
    c.eq("tee (4,8)", (J[(4, 8)]["turns"], J[(4, 8)]["flag"], J[(4, 8)]["kind"]), ([90.0], False, "tee"))
    c.eq("joints of C, 90 not allowed", sum(j["flag"] for j in G.joints("C", allow=(0.0, 45.0))), 3)
    JM = G.joints("M")
    c.eq("arc met on its tangent", [(j["turns"], j["flag"]) for j in JM], [([0.0], False)])
    # via to pad
    vp = {r["via"].src["uuid"]: r for r in G.via_pad()}
    c.eq("via-pad own", (round(vp["v1"]["own_gap"], 9), vp["v1"]["own_pad"].name), (0.3, "pad R1.2 A"))
    c.eq("via-pad other", (round(vp["v1"]["other_gap"], 9), vp["v1"]["other_pad"].name), (0.45, "pad R1.1 B"))
    c.eq("via-pad centre (to the nearer pad)", vp["v1"]["centre"], 1.0)
    # copper of net C: 2 + sqrt2 + 2 + 2 + sqrt(1.25) + 1
    cu = G.copper("C")
    c.eq("copper C total", cu["total"], 7 + math.sqrt(2) + math.sqrt(1.25))
    c.eq("copper C segments", cu["layers"]["F.Cu"]["segments"], 6)
    c.eq("copper C at 0.2", cu["layers"]["F.Cu"]["widths"][0.2], 7 + math.sqrt(2))
    c.eq("copper C at 0.3", cu["layers"]["F.Cu"]["widths"][0.3], math.sqrt(1.25))
    cm = G.copper("M")
    c.eq("copper M: a track and a quarter circle", cm["total"], 2 + math.pi)
    c.eq("copper M arcs", cm["layers"]["F.Cu"]["arcs"], 1)
    c.eq("copper A vias", len(G.copper("A")["vias"]), 1)
    # a box round R1
    c.eq("box", sorted(i.name.split(" (")[0] for i in G.in_box([11, -2, 13, 3])),
         ["pad R1.1 B", "pad R1.2 A", "pad R4.1 G", "via A"])
    c.eq("box on B.Cu", [i.kind for i in G.in_box([11, -2, 13, 3], "B.Cu")], ["via"])
    c.eq("box: a track only reaching it", len(G.in_box([10.1, -0.05, 10.5, 0.05])), 1)
    c.eq("box: just past it", len(G.in_box([10.1001, -0.05, 10.5, 0.05])), 0)
    c.eq("box: parts", [p["ref"] for p in G.others_in_box([11, -2, 13, 3])["parts"]], [])
    # ---- the measurements reviewers used to script ------------------------------------------
    STEP = 0.021                                          # two ends of a counted stretch, one 0.01 station each
    # the centre column is pad position to via centre, not the first corner of a pad's core
    c.eq("gap via / rect pad: centre", gap("via:12,0", "R1.1")["centre"], 1.0)
    # a copper shape (a 2 x 2 square of net W) is copper: a 0.2 track 1.0 from its edge
    c.eq("gap shape / track", gap("uuid:s1", "track:33,11")["gap"], 0.9)
    c.eq("nearest to track X: the shape", near("track:33,11")[0]["other"].kind, "shape")
    c.eq("net:W selects its shape", [i.kind for i in one("net:W")], ["shape"])
    # patterns
    c.eq("net pattern", G.net_list("GND*"), ["GND", "GNDX"])
    c.eq("net pattern in a selection", sorted({i.net for i in one("net:GND?")}), ["GNDX"])
    # coupled: A and B run side by side for all 10 mm, copper 0.3 apart
    cp = G.coupled(one("net:A"), 0.35)
    c.eq("coupled A: beside", [(r["other"], r["layer"]) for r in cp["rows"]], [("B", "F.Cu")])
    c.eq("coupled A: length", cp["rows"][0]["length"], 10.0, 1e-9)
    c.eq("coupled A: least gap", cp["rows"][0]["gap"], 0.3)
    c.eq("coupled A within 0.25: nothing", G.coupled(one("net:A"), 0.25)["rows"], [])
    # P runs beside Q for Q's 4 mm, and past each of Q's ends while hypot(dx, 0.5) - 0.2 < 0.35
    want = 4 + 2 * math.sqrt(0.55 ** 2 - 0.5 ** 2)
    cp = G.coupled(one("net:P"), 0.35)
    c.eq("coupled P: beside", [(r["other"], r["layer"]) for r in cp["rows"]], [("Q", "F.Cu")])
    c.eq("coupled P: length", cp["rows"][0]["length"], want, STEP)
    c.eq("coupled P: least gap", cp["rows"][0]["gap"], 0.3)
    tot = {t["layer"]: t for t in cp["totals"]}
    c.eq("coupled P: routed F.Cu / B.Cu", (round(tot["F.Cu"]["length"], 6), round(tot["B.Cu"]["length"], 6)), (10.5, 3.0))
    c.eq("coupled P: near anything", (tot["F.Cu"]["near"], tot["B.Cu"]["near"]), (cp["rows"][0]["length"], 0.0))
    c.eq("coupled P, Q ignored", G.coupled(one("net:P"), 0.35, ignore=["Q"])["rows"], [])
    # overlap: R (0.4 wide, B.Cu) crosses under P (0.2): 0.6 mm of P lies over it; S is 0.05 off P's edge
    ov = G.overlap(one("net:P", "F.Cu"))
    c.eq("overlap P: over", [(r["other"], r["other_layer"]) for r in ov["rows"]], [("R", "B.Cu")])
    c.eq("overlap P / R: length", ov["rows"][0]["length"], 0.6, STEP)
    c.eq("overlap P: one other layer, no 'both'", ov["totals"][0]["both"], None)
    ov = G.overlap(one("net:P", "F.Cu"), margin=0.1)       # S: 2 mm and past each end while hypot(dx, 0.25) <= 0.3
    got = {r["other"]: r["length"] for r in ov["rows"]}
    c.eq("overlap P / S at margin 0.1", got.get("S"), 2 + 2 * math.sqrt(0.3 ** 2 - 0.25 ** 2), STEP)
    c.eq("overlap P / R at margin 0.1", got.get("R"), 0.8, STEP)
    ov = G.overlap(one("net:P", "F.Cu"), adjacent=["B.Cu", "In9.Cu"])
    t = ov["totals"][0]
    c.eq("overlap P, two layers: over B.Cu", t["over"]["B.Cu"], 0.6, STEP)
    c.eq("overlap P, two layers: over In9.Cu", t["over"]["In9.Cu"], 0.4, STEP)
    c.eq("overlap P: copper on both sides (x 34.7 .. 35.1)", t["both"], 0.4, STEP)
    c.eq("overlap P, R ignored", [r["other"] for r in G.overlap(one("net:P", "F.Cu"), ignore=["R"])["rows"]], [])
    # stitch: centre to centre, farthest first
    st = G.stitch(one("net:P"), "GNDX")
    c.eq("stitch P via to GNDX", [(r["via"].src["uuid"], r["centre"], r["other"].src["uuid"]) for r in st], [("vP", 3.0, "vG")])
    c.eq("stitch: layers both span", st[0]["shared"], ["F.Cu", "B.Cu"])
    c.eq("stitch to a net with no via", G.stitch(one("via:12,0"), "B")[0]["centre"], None)
    c.eq("stitch to its own net: not itself", G.stitch(one("via:12,0"), "A")[0]["centre"], None)
    # conn: P1 - track - via - track - P2 is one piece; the pad P3 and the stub 0.1 short of it are two more
    cn = G.conn("P")
    c.eq("conn P: pieces", [(p["pads"], p["tracks"], p["vias"]) for p in cn["pieces"]],
         [(["P1.1", "P2.1"], 2, 1), (["P3.1"], 0, 0), ([], 1, 0)])
    c.eq("conn P: the routed piece's length", cn["pieces"][0]["length"], 13.0)
    c.eq("conn P: loose ends", [(e["at"], e["layer"]) for e in cn["loose"]], [((44.0, 5.0), "F.Cu"), ((44.5, 5.0), "F.Cu")])
    c.eq("conn P: far end, nothing within 0.5", cn["loose"][0]["gap"], None)
    c.eq("conn P: near end, copper 0.1 from the pad", cn["loose"][1]["gap"], 0.1)
    cn = G.conn("C")                                       # six tracks and the pad R5.1 they pass through
    c.eq("conn C: one piece", [(p["pads"], p["tracks"]) for p in cn["pieces"]], [(["R5.1"], 6)])
    c.eq("conn C: its three open ends", sorted(e["at"] for e in cn["loose"]), [(0.0, 5.0), (4.0, 9.0), (6.0, 8.5)])
    c.eq("conn M: the arc joins its track; two open ends", (len(G.conn("M")["pieces"]), len(G.conn("M")["loose"])), (1, 2))
    # bare: P1.1 has its track's end, P2.1 its B.Cu track's, R5.1 the tracks through it; P3.1's stub ends
    # 0.1 short; R1.2 (r 0.4 at (12, -1)) is 0.3 from its net's via (r 0.3 at (12, 0)); R1.1's net B runs 2 mm away
    c.eq("bare: the pads no copper of their net reaches", [r["pad"] for r in G.bare(["P1", "P2", "P3", "R5", "R1"])],
         ["R1.1", "R1.2", "P3.1"])
    c.eq("bare: a pad a pour holds is not bare; a pad with no net is not asked", G.bare(["H1", "P1"]), [])
    # unused-pads: the vias in V1's courtyard, its edge included
    up = G.unused_pads([58, -2, 62, 2])
    c.eq("unused-pads: vias in the box", [r["via"]["uuid"] for r in up], ["vd", "vk", "vu"])
    c.eq("unused-pads: drops / keeps / unknown", [r["drop"] for r in up], [True, False, None])
    c.eq("unused-pads: pads on, of layers", [(r["flashed"], r["span"]) for r in up], [(1, 2), (2, 2), (2, 2)])
    # lanes: U9 is a 4 x 4 lattice, pitch 0.5, lands r 0.1125; a 0.0889 track at 0.0889 (half a track 0.04445).
    # The SH9 via (r 0.2) stands 0.35 out of A2, on A2's lane: gap 0.  B2 (ring 2) leaves -y at x 80.25 / 80.75,
    # each 0.25 from the via's centre: 0.25 - 0.2 - 0.04445; or -x at y 0.25 / 0.75, nothing within 0.0889.
    # A1's -y lane is 0.5 from the via.  D4 is GND: not asked.
    L = G.lanes("U9", ["GND*"], 0.0889, 0.0889)
    c.eq("lanes: lattice", (L["pitch"], L["cols"], L["rows"], L["layer"]), (0.5, 4, 4, "F.Cu"))
    c.eq("lanes: signal balls (GND not asked)", len(L["balls"]), 15)
    c.eq("lanes: with no free lane", [b["ball"] for b in L["balls"] if not b["exit"]], ["A2"])
    bl = {b["ball"]: b for b in L["balls"]}
    c.eq("lanes: A2 ring 1, one lane, through the via",
         [(bl["A2"]["ring"], ln["side"], ln["gap"], ln["by"].src["uuid"]) for ln in bl["A2"]["lanes"]], [(1, "-y", 0.0, "v9")])
    c.eq("lanes: B2 ring 2, four lanes", [(ln["side"], ln["a"]) for ln in bl["B2"]["lanes"]],
         [("-x", (80.5, 0.25)), ("-x", (80.5, 0.75)), ("-y", (80.25, 0.5)), ("-y", (80.75, 0.5))])
    c.eq("lanes: B2's -x lanes free", [ln["free"] for ln in bl["B2"]["lanes"]], [True, True, False, False])
    c.eq("lanes: B2 -y x 80.25 gap", bl["B2"]["lanes"][2]["gap"], 0.25 - 0.2 - 0.04445)
    c.eq("lanes: B2 -y x 80.75 gap", bl["B2"]["lanes"][3]["gap"], 0.25 - 0.2 - 0.04445)
    c.eq("lanes: A1 a corner, -x and -y", [ln["side"] for ln in bl["A1"]["lanes"]], ["-x", "-y"])
    c.eq("lanes: B3's -y x 80.75 lane blocked, x 81.25 free, +x free",
         [(ln["side"], ln["free"]) for ln in bl["B3"]["lanes"]], [("+x", True), ("+x", True), ("-y", False), ("-y", True)])
    c.eq("lanes: every power net asked", len(G.lanes("U9", (), 0.0889, 0.0889)["balls"]), 16)
    c.eq("lanes: ring 1 only", len(G.lanes("U9", ["GND*"], 0.0889, 0.0889, rings=1)["balls"]), 11)
    c.eq("lanes: --reach 0.25 ends A2's lane at y -0.25, inside the via's pad (y -0.15): still blocked",
         [b["ball"] for b in G.lanes("U9", ["GND*"], 0.0889, 0.0889, reach=0.25)["balls"] if not b["exit"]], ["A2"])
    c.eq("lanes: --reach 0.05: A2's lane ends at y -0.05, 0.3 from the via's centre: 0.3 - 0.2 - 0.04445",
         G.lanes("U9", ["GND*"], 0.0889, 0.0889, reach=0.05)["balls"][1]["lanes"][0]["gap"], 0.3 - 0.2 - 0.04445)
    c.eq("lanes: --reach 0.0: A2's lane is its centre, 0.35 from the via's: free (0.10555)",
         [b["ball"] for b in G.lanes("U9", ["GND*"], 0.0889, 0.0889, reach=0.0)["balls"] if not b["exit"]], [])
    # lanes at once: the fixture's balls each have a lane of their own -> nobody left out
    c.eq("lanes: at once on U9", L["unassigned"], [])
    # assign_lanes by hand: X and Y want lane 1.0, Y also 1.5, Z only 1.5 -> one of them short, and it is Z
    # (X keeps 1.0, Y takes 1.5); X [1.0, 1.5] and Y [1.0] -> X moves to 1.5, both leave
    def ball(name, *keys):
        return dict(ball=name, lanes=[dict(side="-y", a=(k, 0), free=f) for k, f in keys])
    c.eq("lanes: three balls, two lanes", geom.assign_lanes([ball("X", (1.0, True)), ball("Y", (1.0, True), (1.5, True)),
                                                             ball("Z", (1.5, True))])[1], ["Z"])
    c.eq("lanes: an augmenting path moves X", geom.assign_lanes([ball("X", (1.0, True), (1.5, True)), ball("Y", (1.0, True))]),
         ({"X": ("-y", 1.5), "Y": ("-y", 1.0)}, []))
    c.eq("lanes: a blocked lane is no option", geom.assign_lanes([ball("X", (1.0, True)), ball("Y", (1.0, True), (1.5, False))])[1],
         ["Y"])
    # links: C9.1 one axis piece (two collinear tracks, 0.4 + 0.6); C9.2 axis 0.3 then 45 (0.5 * sqrt 2);
    # C9.3 one piece 26.565 deg off the y axis (atan 0.5); C9.4 a via 0.1 inside its pad; C9.5 on the axis, 0.1 off centre
    lk = {r["pad"]: r for r in G.links(["C9"])}
    c.eq("links: every pad", sorted(lk), ["C9.1", "C9.2", "C9.3", "C9.4", "C9.5"])
    c.eq("links: C9.1 axis, collinear tracks one piece", (lk["C9.1"]["shape"], lk["C9.1"]["angles"], lk["C9.1"]["lengths"],
                                                          lk["C9.1"]["off"]), ("axis", [0.0], [1.0], 0.0))
    c.eq("links: C9.2 axis+45", (lk["C9.2"]["shape"], lk["C9.2"]["angles"], lk["C9.2"]["lengths"]),
         ("axis+45", [0.0, 45.0], [0.3, round(0.5 * math.sqrt(2), 4)]))
    c.eq("links: C9.3 at an angle", (lk["C9.3"]["shape"], lk["C9.3"]["angles"]),
         ("angle", [round(math.degrees(math.atan(0.5)), 2)]))
    c.eq("links: C9.4 via in pad", (lk["C9.4"]["shape"], lk["C9.4"]["via"]), ("via in pad", (99, 0.1)))
    c.eq("links: C9.5 off centre", (lk["C9.5"]["shape"], lk["C9.5"]["off"]), ("axis", 0.1))
    c.eq("links: a pattern", len(G.links(["C*"])), 5)
    # --plan: a plan's track 0.35 below the via's centre, 0.1 wide: 0.35 - 0.2 - 0.05 from the via, counted alone;
    # the board's nearest to that via is A2's land, 0.35 - 0.2 - 0.1125
    P = geom.load(path)
    added = P.add_plan(dict(tracks=[dict(net="PL", layer="F.Cu", width=0.1, pts=[[80, -0.7], [81, -0.7], [81, -1.6]])],
                            vias=[dict(net="PL", at=[81, -1.6], pad=0.4, drill=0.2)]), "t")
    c.eq("plan: items added", [i.kind for i in added], ["plan-track", "plan-track", "plan-via"])
    c.eq("plan: selected by plan:", len(P.select("plan:PL")), 3)
    r = P.nearest(P.select("via:80.5,-0.35"), kinds=("plan-track", "plan-via"))
    c.eq("plan: the via against the plan alone", (r[0]["other"].kind, round(r[0]["gap"], 9)), ("plan-track", 0.1))
    c.eq("plan: only the plan's copper counted", all(x["other"].kind.startswith("plan-") for x in r), True)
    r = P.nearest(P.select("via:80.5,-0.35"))
    c.eq("plan: without --plan-only the board's land is nearer", (r[0]["other"].name, round(r[0]["gap"], 9)),
         ("pad U9.A2 U9SA2", round(0.35 - 0.2 - 0.1125, 9)))
    # lengths
    ln = {r["net"]: r for r in G.lengths()}
    c.eq("lengths P", (round(ln["P"]["total"], 9), ln["P"]["layers"], ln["P"]["segments"], ln["P"]["vias"]),
         (13.5, {"F.Cu": 10.5, "B.Cu": 3.0}, 3, 1))
    c.eq("lengths M: the arc by its length", ln["M"]["total"], 2 + math.pi)
    c.eq("lengths, longest first", G.lengths()[0]["net"], "P")
    c.eq("lengths C, 0.25 wide or narrower", G.lengths("C", max_width=0.25)[0]["total"], 7 + math.sqrt(2))
    r = G.lengths("P", box=[35, -1, 41, 1])[0]             # 5 mm of the F.Cu track, 1 mm of the B.Cu one, the via
    c.eq("lengths P in a box", (r["layers"], r["vias"]), ({"F.Cu": 5.0, "B.Cu": 1.0}, 1))
    c.eq("lengths P on B.Cu", G.lengths("P", layers=["B.Cu"])[0]["total"], 3.0)
    c.eq("lengths by pattern", [r["net"] for r in G.lengths("GND*")], [])
    r = G.lengths("M", box=[14, 14, 18, 18])[0]            # the arc whole (chords: within 1e-6) and 1 mm of the track
    c.eq("lengths M in a box: arc by chords", r["total"], 1 + math.pi, 1e-3)
    # probe: a 0.05 disc at (35, 0.2) -- P's edge at y 0.1, Q's at 0.4, R under it
    pr = G.probe((35, 0.2), radius=0.05, drill=0.3)
    by = {w["layer"]: w for w in pr["layers"]}
    c.eq("probe F.Cu: gap", by["F.Cu"]["gap"], 0.05)
    c.eq("probe F.Cu: who", by["F.Cu"]["other"].net, "P")
    c.eq("probe B.Cu: over R", (by["B.Cu"]["gap"], by["B.Cu"]["other"].net), (0.0, "R"))
    c.eq("probe: hole to hole", pr["hole"]["gap"], math.hypot(5, 0.2) - 0.3)
    c.eq("probe: no outline in the dump", pr["edge"], None)
    pr = G.probe((35, 0.2), radius=0.05, net="P")
    c.eq("probe as net P: Q is the nearest", (round(pr["layers"][0]["gap"], 9), pr["layers"][0]["other"].net), (0.15, "Q"))
    # added: what changes.json lists (written beside the dump, as in a snapshot folder)
    (Path(path).parent / "changes.json").write_text(json.dumps(dict(
        base=dict(id="RV0"), copper=dict(added=[dict(uuid="t-Q-32-0.5")], removed=[]), vias=dict(added=[dict(uuid="vP")], removed=[]))))
    G2 = geom.load(path)
    c.eq("added:", sorted(i.kind + " " + i.net for i in G2.select("added:")), ["track Q", "via P"])
    c.eq("added:NET", [i.name for i in G2.select("added:Q")], [one("track:34,0.5")[0].name])
    c.eq("nearest added:", G2.nearest(G2.select("added:Q"))[0]["gap"], 0.3)
    # the command line prints the same number
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "geom.py"), str(path), "gap", "track:5,0", "track:5,0.5"],
                       capture_output=True, text=True)
    c.eq("CLI gap", r.returncode == 0 and "0.3000" in r.stdout, True)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "geom.py"), str(path), "joints", "C"],
                       capture_output=True, text=True)
    c.eq("CLI joints", "5 joint(s), 1 flagged" in r.stdout and "26.57" in r.stdout, True)
    for q in (["info"], ["show", "R1"], ["nearest", "net:C"], ["label"], ["joints", "--all-nets", "--all"], ["via-pad"],
              ["holes"], ["copper", "C"], ["box", "0,0,20,20"], ["nearest", "net:A", "--json"],
              ["lengths"], ["lengths", "?", "--max-width", "0.25", "--layer", "F.Cu,B.Cu", "--over", "1"],
              ["lengths", "P", "--box", "35,-1,41,1", "--json"], ["conn", "P"], ["conn", "GND", "--json"],
              ["stitch", "net:P", "--to", "GNDX", "--over", "1.27"], ["coupled", "net:P", "--within", "0.35"],
              ["coupled", "added:", "--within", "0.35", "--json"], ["overlap", "net:P", "--adjacent", "B.Cu,In9.Cu"],
              ["probe", "35,0.2", "--radius", "0.05", "--drill", "0.3", "--net", "P"], ["show", "added:"],
              ["nearest", "added:", "--within", "0.5", "--ignore", "GND*"]):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "geom.py"), str(path)] + q,
                           capture_output=True, text=True)
        c.eq(f"CLI {' '.join(q)} runs", (r.returncode, r.stderr.strip()), (0, ""))


def pcbnew_checks(geom, work, c):
    """geom.py on the dump against pcbnew's numbers on the board (ref.json, written by kpy_side)."""
    ref = json.loads((work / "ref.json").read_text())
    G = geom.load(work / "geometry.json")
    worst, n_pairs, zero = 0.0, 0, 0
    foreign = {}
    for p in ref["pairs"]:
        A, B = G.select(p["a"], p["layer"]), G.select(p["b"], p["layer"])
        got = G.gap(A, B, layer=p["layer"])
        n_pairs += 1
        zero += p["gap"] == 0.0
        d = abs(got["gap"] - p["gap"]) if got else 99.0
        worst = max(worst, d)
        if d > TOL_PCBNEW:
            c.bad.append(f"{p['a']} / {p['b']} on {p['layer']}: geom {got and got['gap']}, pcbnew {p['gap']}")
        if p["net_a"] != p["net_b"] or not p["net_a"]:
            for me in ("a", "b"):
                k = (p[me], p["layer"])
                foreign[k] = min(foreign.get(k, 99.0), p["gap"])
    c.n += n_pairs
    # the nearest foreign copper of every object = the smallest of its foreign pairs
    worst_near = 0.0
    for (sel, layer), want in foreign.items():
        items = [it for it in G.select(sel, layer) if it.kind != "pour"]
        if not items:
            continue
        # an arc is several items (its chords): the object's nearest is the nearest of any of them
        got = [r[0]["gap"] for r in (G.foreign(it, layer=layer) for it in items) if r]
        c.n += 1
        d = abs(min(got) - want) if got else 99.0
        worst_near = max(worst_near, d)
        if d > TOL_PCBNEW:
            c.bad.append(f"nearest foreign of {sel} on {layer}: geom {got and min(got)}, pcbnew {want}")
    for k, want in ref["lengths"].items():
        net, layer = k.split("|")
        c.eq(f"routed length {k}", G.copper(net, layer)["layers"][layer]["length"], want, 1e-5)
    arc = next(a for a in G.d["arcs"] if a["uuid"] == ref["arc"]["uuid"])
    c.eq("arc length", arc["length"], ref["arc"]["length"], 1e-5)
    c.eq("arc chords within 0.001 mm of the arc",
         max(abs(math.dist(p, arc["centre"]) - arc["radius"]) for p in arc["pts"]) < 1e-5
         and arc["radius"] - min(math.dist(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), arc["centre"])
                                 for p, q in zip(arc["pts"], arc["pts"][1:])) <= 0.001, True)
    for refdes, want in ref["labels"].items():
        L = G.label(refdes)
        c.eq(f"label {refdes} own", L["own"], want["own"], 1e-5)
        c.eq(f"label {refdes} other", L["other"], want["other"], 1e-5)
    got = [h["gap"] for h in G.holes(below=3.0)]
    c.eq("hole pairs under 3 mm", len(got), len(ref["hole_gaps"]))
    for g, w in zip(got, ref["hole_gaps"]):
        c.eq("hole gap", g, w, 1e-5)
    flagged = [j for j in G.joints() if j["flag"]]
    c.eq("flagged joints", [(j["net"], [round(v) for v in j["at"]]) for j in flagged], [("SIG2", [8, 16])])
    c.eq("the flagged turn", flagged[0]["turns"] if flagged else None, [round(math.degrees(math.atan2(1.73, 3.0)), 2)])
    c.eq("SIG1 through its arc: straight on at both ends", [j["turns"] for j in G.joints("SIG1")], [[0.0], [0.0]])
    c.eq("pads modelled exactly", G.d["source"]["pad_models"].get("exact"), len(G.d["pads"]))
    c.eq("copper shapes dumped", sorted(s["uuid"] for s in G.d["shapes"]), ref["shapes"])
    c.eq("a shape's net and layer", sorted((s["net"].rsplit("/", 1)[-1], s["layer"]) for s in G.d["shapes"]),
         [("", "B.Cu"), ("SIG3", "F.Cu")])
    return n_pairs, zero, worst, worst_near


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--kpy":
        return kpy_side(sys.argv[2])
    sys.path.insert(0, str(ROOT / "tools" / "review"))
    import geom
    work = Path(tempfile.mkdtemp(prefix="test-review-geom-"))
    try:
        c = Checks()
        (work / "hand.json").write_text(json.dumps(hand_board()))
        hand_checks(geom, work / "hand.json", c)
        n_hand = c.n
        if c.bad:
            print("\n".join(c.bad))
            print(f"FAIL: geom.py against the hand-computed board: {len(c.bad)} of {n_hand} checks wrong; first: {c.bad[0]}")
            return 1
        print(f"by hand: {n_hand} checks agree")
        r = subprocess.run([str(KPY), "-c", "import pcbnew"], capture_output=True, text=True)
        if r.returncode:
            print(f"SKIP: pcbnew is not importable through kicad_project/tools/kpy (the {n_hand} hand-computed checks passed)")
            return 0
        for cmd in ([str(KPY), str(ROOT / "tests" / "fixture_pcb.py"), str(work), "--nets", "4"],
                    [str(KPY), str(Path(__file__).resolve()), "--kpy", str(work)],
                    [str(KPY), str(ROOT / "tools" / "review" / "dump.py"), str(work / "fixture"), str(work / "geometry.json")]):
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
            if r.returncode:
                print(r.stdout + r.stderr)
                print(f"FAIL: {' '.join(Path(x).name for x in cmd[:2])} exited {r.returncode}")
                return 1
        n_pairs, zero, worst, worst_near = pcbnew_checks(geom, work, c)
        if c.bad:
            print("\n".join(c.bad[:40]))
            print(f"FAIL: geom.py against pcbnew: {len(c.bad)} of {c.n - n_hand} checks wrong; first: {c.bad[0]}")
            return 1
        print(f"PASS: {n_hand} hand-computed checks exact; against pcbnew on the fixture board {n_pairs} object pairs "
              f"({zero} touching) agree within {worst * 1000:.2f} um and every object's nearest foreign copper within "
              f"{worst_near * 1000:.2f} um (limit {TOL_PCBNEW * 1000:.0f} um); {c.n - n_hand - n_pairs} more "
              "(lengths, labels, holes, joints) agree")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
