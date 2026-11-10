#!/usr/bin/env python3
"""A synthetic KiCad board for timing and regression tests of the PCB tools.

    tools/kpy tests/fixture_pcb.py OUT_DIR [--nets 8] [--quick]

Writes OUT_DIR/fixture/ -- a board folder the board tools accept by path
(outside kicad_project/): fixture.kicad_pcb, .kicad_pro, an empty
fixture.kicad_sch, and routing/R1_spec.json, a netgroup spec. The board:

  * 50 x 30 mm, two copper layers, the outline on Edge.Cuts;
  * J1 and J2, 1 x N 2.54 mm headers (KiCad's stock footprint) facing each
    other across the board, N signal nets from J1 to J2 in an order with
    four crossings, so the router needs both layers and vias;
  * four 0603 resistors between them on GND, obstacles in the bundle's way;
  * a GND pour on F.Cu in one corner, tied to a resistor (audit's pour gate).

`--kind prelayout` writes OUT_DIR/prelayout/ instead: the three cases the
pre-routing gate (tools/route/prelayout.py) must fail, with the spec that
configures them in routing/prelayout.json --
  * U1, a 0.5 mm BGA (100 balls, 11 x 11) whose every ball needs its own
    via: most fall back into their pads (board1's in-pad fanout);
  * J3, an 80-pin 0.4 mm board-to-board receptacle 2 mm from the edge with
    every pin used: too few via sites (board2's Q8);
  * J4 -> J5, ten nets between two headers in reversed order: 45 inversions
    in a corridor declared to take none (board2's R5).

`--kind sparse` writes OUT_DIR/sparse/ instead: local nets in a big window
(the corridor search's case, ROADMAP R3) -- a 100 x 70 mm board, four
bundles of N/2 nets, each between two 1 x N/2 headers 14 mm apart in its own
quadrant, in reversed order (every pair crosses), and the spec's window the
whole board, so an unbounded A* may wander 100 mm for a 14 mm net.

`--kind vias` writes OUT_DIR/vias/ instead: the via fixer's workload
(tools/copper/viafix.py) -- a grid of 0603 resistors, each with a via joined
to pad 1 by a crooked link (or touching it), the via tied on B.Cu to a
second via of its net, and other nets' tracks hemming every pad in, so no
place on an axis is free: the candidate search of each via goes deep, and
`--push` shoves tracks for most of them (the via-links skill's slow case).

`--kind large` writes OUT_DIR/large/ instead: a board the size of a real one,
for timing the placement and fanout tools where their cost shows (ROADMAP M2)
-- 100 x 80 mm, four copper layers, rounded corners and two round cut-outs;
U1, a 536-ball 0.5 mm BGA with a 24-capacitor decoupling ring on the back;
266 passives in four fields of different density, each with a via and a stub;
eight SOIC-8, twelve SOT-23, three connectors; a ten-line bus on B.Cu; GND
stitching and a GND plane on In1.Cu; sixteen capacitors parked off the board
for `place.py auto` (placement/auto_spec.json), a plan for `place.py check`
(placement/check_plan.json, one part of it illegal on purpose) and a layer
file for `fanout.py layers` (routing/layers_spec.json).  Board Setup carries
the silk text minimums `place.py labels` needs.

Every footprint is board-only, so the schematic-parity test has nothing to
compare against the empty schematic and passes: DRC judges copper only.
Deterministic: the same arguments write the same board (KiCad's id generator
is seeded; a KiCad without `KIID.SeedGenerator` writes a new order each time).
"""
import argparse
import json
import sys
from pathlib import Path

STOCK = Path("/usr/share/kicad/footprints")


def stock_dir():
    import os
    for env in ("KICAD10_FOOTPRINT_DIR", "KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR"):
        if os.environ.get(env):
            return Path(os.environ[env])
    for p in (STOCK, Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
              Path("/usr/local/share/kicad/footprints")):
        if p.exists():
            return p
    sys.exit("fixture_pcb: KiCad's stock footprints not found (set KICAD10_FOOTPRINT_DIR)")


EMPTY_SCH = """(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "9.0")
\t(uuid "8d0c3f37-3a3c-4d39-9b7e-000000000001")
\t(paper "A4")
\t(lib_symbols)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
\t(embedded_fonts no)
)
"""


def make(out, n=8):
    import pcbnew
    d = Path(out) / "fixture"
    (d / "routing").mkdir(parents=True, exist_ok=True)
    pcb = d / "fixture.kicad_pcb"
    for f in d.glob("fixture.*"):
        f.unlink()
    b = pcbnew.NewBoard(str(pcb))
    mm = pcbnew.FromMM
    W, H = 50.0, 30.0
    for (x0, y0), (x1, y1) in zip([(0, 0), (W, 0), (W, H), (0, H)], [(W, 0), (W, H), (0, H), (0, 0)]):
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0)))
        s.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        b.Add(s)
    nets = {}

    def net(name):
        if name not in nets:
            ni = pcbnew.NETINFO_ITEM(b, name)
            b.Add(ni)
            nets[name] = ni
        return nets[name]

    lib = stock_dir()

    def place(libname, fpname, ref, x, y, rot=0):
        f = pcbnew.FootprintLoad(str(lib / f"{libname}.pretty"), fpname)
        f.SetFPID(pcbnew.LIB_ID(libname, fpname))
        f.SetReference(ref)
        f.SetValue(fpname)
        f.SetBoardOnly(True)
        b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        f.SetOrientationDegrees(rot)
        return f

    pitch = 2.54
    y0 = H / 2 - pitch * (n - 1) / 2
    hdr = f"PinHeader_1x{n:02d}_P2.54mm_Vertical"
    j1 = place("Connector_PinHeader_2.54mm", hdr, "J1", 5.0, y0)
    j2 = place("Connector_PinHeader_2.54mm", hdr, "J2", W - 5.0, y0)
    order = list(range(n))
    order[0], order[1], order[-1], order[-2] = order[1], order[0], order[-2], order[-1]   # four crossings
    spec_nets = {}
    for k in range(n):
        nm = f"SIG{k}"
        p1 = j1.FindPadByNumber(str(k + 1))
        p2 = j2.FindPadByNumber(str(order[k] + 1))
        p1.SetNet(net(nm))
        p2.SetNet(net(nm))
        x, y = pcbnew.ToMM(p1.GetPosition().x), pcbnew.ToMM(p1.GetPosition().y)
        gx, gy = (round(1.0 + round((v - 1.0) / 0.2) * 0.2, 4) for v in (x, y))   # a grid point inside the pad
        spec_nets[nm] = {"src": [gx, gy], "src_pad": ["J1", str(k + 1)],
                         "layer": ["F.Cu", "B.Cu"],
                         "legs": [{"to": ["J2", str(order[k] + 1)], "goal_layer": "F.Cu"}]}
    gnd = net("GND")
    for i, (x, y) in enumerate([(20.0, 11.0), (20.0, 19.0), (30.0, 9.0), (30.0, 21.0)]):
        r = place("Resistor_SMD", "R_0603_1608Metric", f"R{i + 1}", x, y, 90)
        for p in r.Pads():
            p.SetNet(gnd)
    z = pcbnew.ZONE(b)
    z.SetLayer(pcbnew.F_Cu)
    z.SetNet(gnd)
    o = z.Outline()
    o.NewOutline()
    for x, y in ((14.0, 1.0), (24.0, 1.0), (24.0, 4.0), (14.0, 4.0)):
        o.Append(mm(x), mm(y))
    b.Add(z)
    # a GND resistor inside the pour: one island, tied
    r5 = place("Resistor_SMD", "R_0603_1608Metric", "R5", 19.0, 2.5, 0)
    for p in r5.Pads():
        p.SetNet(gnd)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(str(pcb), b)
    (d / "fixture.kicad_sch").write_text(EMPTY_SCH)
    spec = {"group": "R1", "why": "fixture: a crossing bundle J1 -> J2 past four GND resistors",
            "plan": "routing/R1_plan.json", "window": [1.0, 1.0, W - 1.0, H - 1.0],
            "pitch": 0.2, "width": 0.2, "clearance": 0.2, "padclr": 0.2, "via": [0.6, 0.3],
            "layers": ["F.Cu", "B.Cu"], "edge": 0.5, "nets": spec_nets}
    (d / "routing" / "R1_spec.json").write_text(json.dumps(spec, indent=1) + "\n")
    return d


def make_prelayout(out):
    import pcbnew
    d = Path(out) / "prelayout"
    (d / "routing").mkdir(parents=True, exist_ok=True)
    pcb = d / "prelayout.kicad_pcb"
    for f in d.glob("prelayout.*"):
        f.unlink()
    b = pcbnew.NewBoard(str(pcb))
    mm = pcbnew.FromMM
    W, H = 60.0, 40.0
    for (x0, y0), (x1, y1) in zip([(0, 0), (W, 0), (W, H), (0, H)], [(W, 0), (W, H), (0, H), (0, 0)]):
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0)))
        s.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        b.Add(s)
    lib = stock_dir()
    nets = {}

    def net(name):
        if name not in nets:
            nets[name] = pcbnew.NETINFO_ITEM(b, name)
            b.Add(nets[name])
        return nets[name]

    def place(libname, fpname, ref, x, y, rot=0):
        f = pcbnew.FootprintLoad(str(lib / f"{libname}.pretty"), fpname)
        f.SetFPID(pcbnew.LIB_ID(libname, fpname))
        f.SetReference(ref)
        f.SetValue(fpname)
        f.SetBoardOnly(True)
        b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        f.SetOrientationDegrees(rot)
        return f

    u1 = place("Package_BGA", "BGA-100_6.0x6.0mm_Layout11x11_P0.5mm_Ball0.3mm_Pad0.25mm_NSMD", "U1", 15.0, 15.0)
    for p in u1.Pads():
        p.SetNet(net(f"U1_{p.GetNumber()}"))
    j3 = place("Connector_Hirose_DF40", "Hirose_DF40B(2.0)-80DS-0.4V_2x40-1MP_P0.4mm", "J3", 38.0, 37.0)
    for p in j3.Pads():
        if p.GetNumber() and p.GetNumber() != "MP":
            p.SetNet(net(f"J3_{p.GetNumber()}"))
    hdr = "PinHeader_1x10_P2.54mm_Vertical"
    j4 = place("Connector_PinHeader_2.54mm", hdr, "J4", 4.0, 3.0)
    j5 = place("Connector_PinHeader_2.54mm", hdr, "J5", W - 4.0, 3.0)
    for k in range(10):
        n = net(f"BUS{k}")
        j4.FindPadByNumber(str(k + 1)).SetNet(n)
        j5.FindPadByNumber(str(10 - k)).SetNet(n)
    pcbnew.SaveBoard(str(pcb), b)
    (d / "prelayout.kicad_sch").write_text(EMPTY_SCH)
    spec = {"fanout": [{"ref": "U1", "plan": ["--via", "0.4,0.2", "--clearance", "0.0889", "--width", "0.0889"],
                        "in_pad_ok": False, "why": "fixture: via-in-pad not ordered"}],
            "rows": [{"ref": "J3", "pads": "outer", "side": "S", "via": [0.6, 0.3], "gap": 0.2, "clearance": 0.2,
                      "width": 0.2, "edge": 0.45, "lateral": 0.6}],
            "bundles": [{"a": "J4", "b": "J5", "max": 0, "why": "fixture: one free layer in the corridor"}]}
    (d / "routing" / "prelayout.json").write_text(json.dumps(spec, indent=1) + "\n")
    return d


def make_sparse(out, n=16):
    import pcbnew
    d = Path(out) / "sparse"
    (d / "routing").mkdir(parents=True, exist_ok=True)
    pcb = d / "sparse.kicad_pcb"
    for f in d.glob("sparse.*"):
        f.unlink()
    b = pcbnew.NewBoard(str(pcb))
    mm = pcbnew.FromMM
    W, H = 100.0, 70.0
    for (x0, y0), (x1, y1) in zip([(0, 0), (W, 0), (W, H), (0, H)], [(W, 0), (W, H), (0, H), (0, 0)]):
        e = pcbnew.PCB_SHAPE(b)
        e.SetShape(pcbnew.SHAPE_T_SEGMENT)
        e.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0)))
        e.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        e.SetLayer(pcbnew.Edge_Cuts)
        e.SetWidth(mm(0.1))
        b.Add(e)
    lib = stock_dir()

    def place(fpname, ref, x, y):
        f = pcbnew.FootprintLoad(str(lib / "Connector_PinHeader_2.54mm.pretty"), fpname)
        f.SetFPID(pcbnew.LIB_ID("Connector_PinHeader_2.54mm", fpname))
        f.SetReference(ref)
        f.SetValue(fpname)
        f.SetBoardOnly(True)
        b.Add(f)
        f.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        return f

    k = max(2, n // 4)
    hdr = f"PinHeader_1x{k:02d}_P2.54mm_Vertical"
    spec_nets = {}
    for q, (cx, cy) in enumerate([(25.0, 17.5), (75.0, 17.5), (25.0, 52.5), (75.0, 52.5)]):
        y0 = cy - 2.54 * (k - 1) / 2
        ja = place(hdr, f"J{2 * q + 1}", cx - 7.0, y0)
        jb = place(hdr, f"J{2 * q + 2}", cx + 7.0, y0)
        for i in range(k):
            nm = f"Q{q}S{i}"
            ni = pcbnew.NETINFO_ITEM(b, nm)
            b.Add(ni)
            pa, pb = ja.FindPadByNumber(str(i + 1)), jb.FindPadByNumber(str(k - i))
            pa.SetNet(ni)
            pb.SetNet(ni)
            x, y = pcbnew.ToMM(pa.GetPosition().x), pcbnew.ToMM(pa.GetPosition().y)
            gx, gy = (round(1.0 + round((v - 1.0) / 0.2) * 0.2, 4) for v in (x, y))
            spec_nets[nm] = {"src": [gx, gy], "src_pad": [ja.GetReference(), str(i + 1)], "layer": ["F.Cu", "B.Cu"],
                             "legs": [{"to": [jb.GetReference(), str(k - i)], "goal_layer": "F.Cu"}]}
    pcbnew.SaveBoard(str(pcb), b)
    (d / "sparse.kicad_sch").write_text(EMPTY_SCH)
    spec = {"group": "R1", "why": "fixture: four local crossing bundles in one big window (R3)",
            "plan": "routing/R1_plan.json", "window": [1.0, 1.0, W - 1.0, H - 1.0],
            "pitch": 0.2, "width": 0.2, "clearance": 0.2, "padclr": 0.2, "via": [0.6, 0.3],
            "layers": ["F.Cu", "B.Cu"], "edge": 0.5, "nets": spec_nets}
    (d / "routing" / "R1_spec.json").write_text(json.dumps(spec, indent=1) + "\n")
    return d


def make_vias(out, n=48):
    import pcbnew
    d = Path(out) / "vias"
    (d / "routing").mkdir(parents=True, exist_ok=True)
    pcb = d / "vias.kicad_pcb"
    for f in d.glob("vias.*"):
        f.unlink()
    b = pcbnew.NewBoard(str(pcb))
    mm = pcbnew.FromMM
    V = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))
    cols = 8
    rows = (n + cols - 1) // cols
    W, H = 6.0 + cols * 6.0, 6.0 + rows * 5.0
    for (x0, y0), (x1, y1) in zip([(0, 0), (W, 0), (W, H), (0, H)], [(W, 0), (W, H), (0, H), (0, 0)]):
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(V(x0, y0))
        s.SetEnd(V(x1, y1))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        b.Add(s)
    nets = {}

    def net(name):
        if name not in nets:
            ni = pcbnew.NETINFO_ITEM(b, name)
            b.Add(ni)
            nets[name] = ni
        return nets[name]

    def track(nm, layer, pts, w=0.2):
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(V(ax, ay))
            t.SetEnd(V(bx, by))
            t.SetWidth(mm(w))
            t.SetLayer(layer)
            b.Add(t)
            t.SetNet(net(nm))

    def via(nm, x, y):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(V(x, y))
        v.SetWidth(mm(0.6))
        v.SetDrill(mm(0.3))
        b.Add(v)
        v.SetNet(net(nm))

    lib = stock_dir()
    for k in range(n):
        cx, cy = 6.0 + (k % cols) * 6.0, 5.0 + (k // cols) * 5.0
        f = pcbnew.FootprintLoad(str(lib / "Resistor_SMD.pretty"), "R_0603_1608Metric")
        f.SetFPID(pcbnew.LIB_ID("Resistor_SMD", "R_0603_1608Metric"))
        f.SetReference(f"R{k + 1}")
        f.SetValue("R_0603")
        f.SetBoardOnly(True)
        b.Add(f)
        f.SetPosition(V(cx, cy))
        nm = f"N{k}"
        p1, p2 = f.FindPadByNumber("1"), f.FindPadByNumber("2")
        p1.SetNet(net(nm))
        p2.SetNet(net(f"M{k}"))
        px, py = pcbnew.ToMM(p1.GetPosition().x), pcbnew.ToMM(p1.GetPosition().y)
        if k % 3 == 2:                         # a via touching its pad, no link
            vx, vy = px - 0.65, py + 0.35
        else:                                  # a crooked link: off-axis, not 45 degrees
            vx, vy = px - 1.0, py - 0.55 - 0.05 * (k % 4)
            track(nm, pcbnew.F_Cu, [(px, py), (vx, vy)])
        via(nm, vx, vy)
        via(nm, vx - 1.2, vy + 1.6)            # the anchor its B.Cu track runs back to
        track(nm, pcbnew.B_Cu, [(vx, vy), (vx - 1.2, vy + 1.2), (vx - 1.2, vy + 1.6)])
        # other nets hem pad 1 in, close enough that every place on an axis within reach is blocked by a track --
        # a wall to its left and a roof above on F.Cu, a floor under the pad's column on B.Cu: a free place needs a
        # push (--push) or the last-resort 45-degree link
        track(f"WALL{k}", pcbnew.F_Cu, [(px - 1.45, cy - 2.2), (px - 1.45, cy + 2.0)])
        track(f"ROOF{k}", pcbnew.F_Cu, [(px - 1.3, py - 1.15), (px + 2.2, py - 1.15)])
        track(f"FLOOR{k}", pcbnew.B_Cu, [(px + 0.3, cy - 2.2), (px + 0.3, cy + 2.0)])
    pcbnew.SaveBoard(str(pcb), b)
    (d / "vias.kicad_sch").write_text(EMPTY_SCH)
    (d / "general_routing_guide.md").write_text(
        "# Routing guide (fixture)\n\n| rule | setting | source |\n|---|---|---|\n"
        "| joints | 0/45 | fixture |\n| copper to edge | 0.5 mm | fixture |\n"
        "| via to pad gap | 0.15 mm | fixture |\n| 45-degree via link | last resort | fixture |\n")
    return d


def make_large(out):
    """OUT/large/: a board the size of a real one, for timing the placement and fanout tools."""
    import math
    import pcbnew
    d = Path(out) / "large"
    for sub in ("routing", "placement"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    pcb = d / "large.kicad_pcb"
    for f in d.glob("large.*"):
        f.unlink()
    b = pcbnew.NewBoard(str(pcb))
    b.SetCopperLayerCount(4)
    mm = pcbnew.FromMM
    V = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))
    W, H, RC = 100.0, 80.0, 3.0

    def edge(shape, **kw):
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(shape)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        if shape == pcbnew.SHAPE_T_SEGMENT:
            s.SetStart(V(*kw["a"]))
            s.SetEnd(V(*kw["b"]))
        elif shape == pcbnew.SHAPE_T_ARC:
            s.SetArcGeometry(V(*kw["a"]), V(*kw["mid"]), V(*kw["b"]))
        else:
            s.SetCenter(V(*kw["c"]))
            s.SetEnd(V(kw["c"][0] + kw["r"], kw["c"][1]))
        b.Add(s)

    # the outline: rounded corners (KiCad's outline builder turns each arc into short segments, as on a
    # real board) and two round cut-outs -- what makes a label's edge distance cost what it does there
    k = RC * (1 - math.sqrt(0.5))
    edge(pcbnew.SHAPE_T_SEGMENT, a=(RC, 0), b=(W - RC, 0))
    edge(pcbnew.SHAPE_T_ARC, a=(W - RC, 0), mid=(W - k, k), b=(W, RC))
    edge(pcbnew.SHAPE_T_SEGMENT, a=(W, RC), b=(W, H - RC))
    edge(pcbnew.SHAPE_T_ARC, a=(W, H - RC), mid=(W - k, H - k), b=(W - RC, H))
    edge(pcbnew.SHAPE_T_SEGMENT, a=(W - RC, H), b=(RC, H))
    edge(pcbnew.SHAPE_T_ARC, a=(RC, H), mid=(k, H - k), b=(0, H - RC))
    edge(pcbnew.SHAPE_T_SEGMENT, a=(0, H - RC), b=(0, RC))
    edge(pcbnew.SHAPE_T_ARC, a=(0, RC), mid=(k, k), b=(RC, 0))
    edge(pcbnew.SHAPE_T_CIRCLE, c=(50.0, 6.0), r=1.6)
    edge(pcbnew.SHAPE_T_CIRCLE, c=(92.0, 74.0), r=1.6)

    ds = b.GetDesignSettings()                 # what board_setup.py types from 14_fab.md
    ds.m_MinSilkTextHeight = mm(0.8)
    ds.m_MinSilkTextThickness = mm(0.12)
    ds.m_SilkClearance = mm(0.15)
    ds.m_MinClearance = mm(0.09)
    ds.m_CopperEdgeClearance = mm(0.3)
    ds.m_HoleToHoleMin = mm(0.25)
    ds.m_TrackMinWidth = mm(0.09)
    ds.m_ViasMinSize = mm(0.4)
    ds.m_MinThroughDrill = mm(0.2)
    ds.m_ViasMinAnnularWidth = mm(0.1)

    lib = stock_dir()
    nets = {}

    def net(name):
        if name not in nets:
            nets[name] = pcbnew.NETINFO_ITEM(b, name)
            b.Add(nets[name])
        return nets[name]

    def place(libname, fpname, ref, x, y, rot=0, back=False):
        f = pcbnew.FootprintLoad(str(lib / f"{libname}.pretty"), fpname)
        f.SetFPID(pcbnew.LIB_ID(libname, fpname))
        f.SetReference(ref)
        f.SetValue(fpname[:12])
        f.SetBoardOnly(True)
        b.Add(f)
        f.SetPosition(V(x, y))
        if back:
            f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        f.SetOrientationDegrees(rot)
        return f

    nvia = 0

    def via(netname, x, y):
        nonlocal nvia
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(V(x, y))
        v.SetWidth(pcbnew.F_Cu, mm(0.45))
        v.SetDrill(mm(0.2))
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetNet(net(netname))
        b.Add(v)
        nvia += 1

    def track(netname, layer, pts, w=0.15):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(V(x0, y0))
            t.SetEnd(V(x1, y1))
            t.SetWidth(mm(w))
            t.SetLayer(layer)
            t.SetNet(net(netname))
            b.Add(t)

    def xy(p):
        return pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)

    # U1: a 536-ball 0.5 mm BGA; the outer five rings are signals, the rest alternate VDD / GND
    UX, UY = 50.0, 40.0
    u1 = place("Package_BGA", "Microchip_FCSG536_BGA-536_16x16mm_Layout30x30_P0.5mm", "U1", UX, UY)
    pos = {p.GetNumber(): xy(p) for p in u1.Pads() if p.GetNumber()}
    x0 = min(x for x, _ in pos.values())
    y0 = min(y for _, y in pos.values())
    x1 = max(x for x, _ in pos.values())
    y1 = max(y for _, y in pos.values())
    sig, power = 0, []
    for p in sorted(u1.Pads(), key=lambda p: (xy(p)[1], xy(p)[0])):
        if not p.GetNumber():
            continue
        x, y = xy(p)
        ring = int(round(min(x - x0, x1 - x, y - y0, y1 - y) / 0.5))
        i, j = int(round((x - x0) / 0.5)), int(round((y - y0) / 0.5))
        if ring < 5:
            p.SetNet(net(f"S{sig:03d}"))
            sig += 1
        else:
            p.SetNet(net("VDD" if (i + j) % 2 else "GND"))
            power.append(((x, y), "VDD" if (i + j) % 2 else "GND"))
    nsig = sig

    # its decoupling ring: 24 x 0402 on the back, under the power balls, each with two vias
    for n in range(24):
        cx = UX - 4.4 + 2.2 * (n % 5) if n < 20 else UX - 3.3 + 2.2 * (n - 20)
        cy = UY - 4.0 + 2.0 * (n // 5) if n < 20 else UY + 4.0
        c = place("Capacitor_SMD", "C_0402_1005Metric", f"C{n + 1}", cx, cy, 0, back=True)
        for num, nm in (("1", "VDD"), ("2", "GND")):
            p = c.FindPadByNumber(num)
            p.SetNet(net(nm))
            px, py = xy(p)
            vx, vy = min((q for q, n_ in power if n_ == nm), key=lambda q: math.dist(q, (px, py + 0.75)))
            via(nm, vx, vy)                       # in the pad of the nearest ball of its rail
            track(nm, pcbnew.B_Cu, [(px, py), (vx, vy)], 0.2)

    # the passives: four fields at different densities, a via and a stub at every pad 1
    sigs = iter(range(10 ** 6))

    def field(prefix, first, fp, libname, x_, y_, cols, rows, px_, py_, rot, pull):
        n = first
        for r_ in range(rows):
            for c_ in range(cols):
                f = place(libname, fp, f"{prefix}{n}", x_ + c_ * px_, y_ + r_ * py_, rot)
                s = next(sigs)
                nm = f"S{s:03d}" if s < nsig else f"N{s:03d}"
                p1, p2 = f.FindPadByNumber("1"), f.FindPadByNumber("2")
                p1.SetNet(net(nm))
                p2.SetNet(net(pull if (r_ + c_) % 3 else f"M{s:03d}"))
                ax, ay = xy(p1)
                vx, vy = (ax, ay + py_ / 2) if rot == 0 else (ax + px_ / 2, ay)
                via(nm, vx, vy)
                track(nm, pcbnew.F_Cu, [(ax, ay), (vx, vy)])
                n += 1
        return n

    field("R", 1, "R_0402_1005Metric", "Resistor_SMD", 10.0, 8.0, 12, 8, 2.4, 1.7, 0, "GND")        # dense
    field("C", 25, "C_0603_1608Metric", "Capacitor_SMD", 62.0, 9.0, 9, 6, 3.6, 2.8, 0, "GND")        # loose
    field("R", 97, "R_0402_1005Metric", "Resistor_SMD", 9.0, 53.0, 12, 6, 1.9, 3.0, 90, "VDD")       # columns
    field("C", 79, "C_0402_1005Metric", "Capacitor_SMD", 64.0, 54.0, 8, 5, 3.0, 2.5, 0, "GND")       # medium

    # ICs and connectors
    for n in range(8):
        f = place("Package_SO", "SOIC-8_3.9x4.9mm_P1.27mm", f"U{n + 2}", 9.0 + 7.6 * (n % 4), 27.0 if n < 4 else 45.0)
        for p in f.Pads():
            p.SetNet(net("GND" if p.GetNumber() == "4" else "VDD" if p.GetNumber() == "8"
                         else f"U{n + 2}_{p.GetNumber()}"))
    for n in range(12):
        f = place("Package_TO_SOT_SMD", "SOT-23", f"Q{n + 1}", 64.0 + 4.0 * (n % 6), 30.0 if n < 6 else 47.0)
        for p in f.Pads():
            p.SetNet(net("GND" if p.GetNumber() == "2" else f"Q{n + 1}_{p.GetNumber()}"))
    j1 = place("Connector_PinHeader_2.54mm", "PinHeader_2x20_P2.54mm_Vertical", "J1", 3.0, 15.0)
    for p in j1.Pads():
        p.SetNet(net(f"M{int(p.GetNumber()):03d}" if int(p.GetNumber()) % 4 else "GND"))
    j2 = place("Connector_PinHeader_2.54mm", "PinHeader_1x20_P2.54mm_Vertical", "J2", 96.5, 15.0)
    for p in j2.Pads():
        p.SetNet(net(f"N{300 + int(p.GetNumber()):03d}"))
    j3 = place("Connector_Hirose_DF40", "Hirose_DF40B(2.0)-80DS-0.4V_2x40-1MP_P0.4mm", "J3", 50.0, 72.0)
    for p in j3.Pads():
        if p.GetNumber() and p.GetNumber() != "MP":
            p.SetNet(net(f"S{int(p.GetNumber()) - 1:03d}"))

    # a routed group: a ten-line bus on B.Cu from the dense field toward U1, with a 45-degree jog
    for n in range(10):
        y_ = 23.0 + 0.3 * n
        track(f"S{n:03d}", pcbnew.B_Cu, [(10.0 + 2.4 * n, 8.85), (10.0 + 2.4 * n, y_ - 1.0), (11.0 + 2.4 * n, y_),
                                         (38.0 - 0.3 * n, y_), (40.0 - 0.3 * n, y_ + 2.0)], 0.1)

    # GND stitching, clear of everything above
    for gx in range(14):
        for gy in range(4):
            via("GND", 6.0 + 6.6 * gx, (2.0, 36.5, 50.2, 77.5)[gy])
    # sixteen more 0402 parked east of the outline: `place.py auto` gives them their spots on the back
    for n in range(16):
        c = place("Capacitor_SMD", "C_0402_1005Metric", f"C{201 + n}", W + 5.0 + 2.5 * (n % 4), 5.0 + 2.0 * (n // 4))
        c.FindPadByNumber("1").SetNet(net("VDD"))
        c.FindPadByNumber("2").SetNet(net("GND"))

    # a GND plane on In1.Cu
    z = pcbnew.ZONE(b)
    z.SetLayer(pcbnew.In1_Cu)
    z.SetNet(net("GND"))
    z.SetAssignedPriority(0)
    o = z.Outline()
    o.NewOutline()
    for x, y in ((1.0, 1.0), (W - 1.0, 1.0), (W - 1.0, H - 1.0), (1.0, H - 1.0)):
        o.Append(mm(x), mm(y))
    b.Add(z)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(str(pcb), b)
    (d / "large.kicad_sch").write_text(EMPTY_SCH)

    (d / "placement.json").write_text(json.dumps({
        "fixed": ["J1", "J2", "J3"], "hole_to_edge": 0.3, "labels": {"own_nearest": ["Q*"]}}, indent=1) + "\n")
    # auto: the sixteen parked capacitors onto the back, round the ring already there
    (d / "placement" / "auto_spec.json").write_text(json.dumps({"step": 0.1, "parts": [
        {"ref": f"C{201 + n}", "side": "B", "rots": [0, 90],
         "region": [UX - 7.5, UY - 7.5, UX + 7.5, UY + 7.5], "anchors": ["U1"], "weights": {"GND": 0.2}}
        for n in range(16)]}, indent=1) + "\n")
    # check: a plan that moves forty parts of the dense field half a pitch (legal) and one onto its neighbour (not)
    plan = {f"R{n}": [10.0 + 2.4 * ((n - 1) % 12) + 0.1, 8.0 + 1.7 * ((n - 1) // 12), 0] for n in range(1, 41)}
    plan["R50"] = [10.0 + 2.4 * 2 + 0.6, 8.0 + 1.7 * 4, 0]
    (d / "placement" / "check_plan.json").write_text(json.dumps(plan, indent=1) + "\n")
    (d / "routing" / "layers_spec.json").write_text(json.dumps({"layers": [
        {"name": "F.Cu", "side": "component", "width": 0.0889, "clearance": 0.0889},
        {"name": "In2.Cu", "width": 0.1016, "clearance": 0.1016, "nfp": True, "hole_clearance": 0.1778},
        {"name": "B.Cu", "width": 0.0889, "clearance": 0.0889}], "nets": ["^S"]}, indent=1) + "\n")
    (d / "FIXTURE.txt").write_text(f"{len(b.GetFootprints())} footprints, {nvia} vias, {len(b.GetTracks()) - nvia} track segments\n")
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--nets", type=int, default=8)
    ap.add_argument("--kind", choices=["router", "prelayout", "vias", "sparse", "large"], default="router")
    a = ap.parse_args()
    # KiCad gives every new item a random id and writes a board's footprints in id order, and the
    # tools walk them in that order: without a seed two fixtures from the same arguments differ,
    # and so do the label search's and the DRC's results on them.
    import pcbnew
    if hasattr(pcbnew.KIID, "SeedGenerator"):
        pcbnew.KIID.SeedGenerator(20261110)
    if a.kind == "prelayout":
        print(make_prelayout(a.out))
    elif a.kind == "large":
        print(make_large(a.out))
    elif a.kind == "sparse":
        print(make_sparse(a.out, a.nets if a.nets != 8 else 16))
    elif a.kind == "vias":
        print(make_vias(a.out, a.nets if a.nets != 8 else 48))
    else:
        print(make(a.out, a.nets))


if __name__ == "__main__":
    main()
