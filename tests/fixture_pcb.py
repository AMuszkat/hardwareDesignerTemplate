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

Every footprint is board-only, so the schematic-parity test has nothing to
compare against the empty schematic and passes: DRC judges copper only.
Deterministic: the same arguments write the same board.
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--nets", type=int, default=8)
    ap.add_argument("--kind", choices=["router", "prelayout", "vias", "sparse"], default="router")
    a = ap.parse_args()
    if a.kind == "prelayout":
        print(make_prelayout(a.out))
    elif a.kind == "sparse":
        print(make_sparse(a.out, a.nets if a.nets != 8 else 16))
    elif a.kind == "vias":
        print(make_vias(a.out, a.nets if a.nets != 8 else 48))
    else:
        print(make(a.out, a.nets))


if __name__ == "__main__":
    main()
