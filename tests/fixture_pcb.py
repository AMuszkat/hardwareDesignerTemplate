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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("--nets", type=int, default=8)
    a = ap.parse_args()
    print(make(a.out, a.nets))


if __name__ == "__main__":
    main()
