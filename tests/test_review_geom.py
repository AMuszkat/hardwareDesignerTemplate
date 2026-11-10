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
     pad, hole to hole, a net's copper, a box.
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
        arc=dict(uuid=arc.m_Uuid.AsString(), length=mm(arc.GetLength()), radius=mm(arc.GetRadius())))))
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
               for r, at in (("R1", [12, 0]), ("R2", [15, 5]), ("R4", [12.3, 2.4]), ("R5", [3, 6]), ("J1", [2, 15]), ("H1", [2, 17]))],
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
        ],
        arcs=[dict(uuid="arc-M", net="M", layer="F.Cu", a=[17, 15], mid=[15 + s2, 15 + s2], b=[15, 17], w=0.2,
                   centre=[15, 15], radius=2.0, angle=90.0, length=math.pi, pts=arc_pts)],
        vias=[dict(uuid="v1", net="A", at=[12, 0], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu", "B.Cu"]),
              dict(uuid="v3", net="J", at=[18, 18], d=0.6, drill=0.3, type="through", layers=["F.Cu", "B.Cu"],
                   flashed=["F.Cu"])],
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
        ],
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
    # the command line prints the same number
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "geom.py"), str(path), "gap", "track:5,0", "track:5,0.5"],
                       capture_output=True, text=True)
    c.eq("CLI gap", r.returncode == 0 and "0.3000" in r.stdout, True)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "review" / "geom.py"), str(path), "joints", "C"],
                       capture_output=True, text=True)
    c.eq("CLI joints", "5 joint(s), 1 flagged" in r.stdout and "26.57" in r.stdout, True)
    for q in (["info"], ["show", "R1"], ["nearest", "net:C"], ["label"], ["joints", "--all-nets", "--all"], ["via-pad"],
              ["holes"], ["copper", "C"], ["box", "0,0,20,20"], ["nearest", "net:A", "--json"]):
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
