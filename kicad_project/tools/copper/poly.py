#!/usr/bin/env python3
"""poly.py -- net-tied copper polygons: list them, or write a plan's polygons
onto a board (route.py apply does this; here for a dry run and a listing).

    tools/kpy tools/copper/poly.py [--board B]                       list the polygons on the board
    tools/kpy tools/copper/poly.py [--board B] --plan P.json         dry run of the plan's polys / polyrips

A polygon is a PCB_SHAPE (poly, filled) on a copper layer carrying a net:
copper of that net for DRC and connectivity, no mask opening, no thermal
relief -- the shape of a star or a pad entry that stadiums cannot make.  In a
route.py plan:
    "polys":    [{"net": "DVDD", "layer": "F.Cu", "pts": [[x, y], ...]}, ...]
    "polyrips": [{"net": "DVDD", "layer": "F.Cu", "at": [x0, y0]}, ...]     the one whose first vertex is there
Importable: add(b, pcbnew, entry), rip(b, pcbnew, entry) -> the item / None, find(b, pcbnew)."""
import argparse
import json
import sys

import common as C


def find(b, pcbnew):
    return [d for d in b.GetDrawings() if d.Type() == pcbnew.PCB_SHAPE_T and d.GetShape() == pcbnew.SHAPE_T_POLY and d.GetNetCode()]


def net_of(b, name):
    n = b.FindNet(name)
    if n is None:
        hits = [x for x in b.GetNetsByName().values() if C.short(x.GetNetname()) == name]
        n = hits[0] if len(hits) == 1 else None
    return n


def add(b, pcbnew, q):
    """A new polygon on the in-memory board; returns it.  Dies on an unknown net or layer."""
    FM = pcbnew.FromMM
    net = net_of(b, q["net"])
    if net is None:
        C.die(f"polygon: no net {q['net']!r} on the board (give the full hierarchical name)")
    if len(q.get("pts", [])) < 3:
        C.die(f"polygon on {q['net']}: fewer than three points")
    lid = b.GetLayerID(q["layer"])
    if lid < 0:
        C.die(f"polygon on {q['net']}: no layer {q['layer']!r}")
    s = pcbnew.PCB_SHAPE(b)
    s.SetShape(pcbnew.SHAPE_T_POLY); s.SetFilled(True); s.SetWidth(0)
    s.SetLayer(lid); s.SetPolyPoints([pcbnew.VECTOR2I(FM(x), FM(y)) for x, y in q["pts"]])
    s.SetNetCode(net.GetNetCode())
    b.Add(s)
    return s


def match(b, pcbnew, q):
    """The polygon a polyrip names: that net and layer, first vertex at `at` (0.01 mm)."""
    mm = pcbnew.ToMM
    x0, y0 = q["at"]
    for d in find(b, pcbnew):
        v = d.GetPolyShape().CVertex(0)
        if (d.GetNetname() == q["net"] or C.short(d.GetNetname()) == q["net"]) and d.GetLayerName() == q["layer"] \
                and abs(mm(v.x) - x0) < 0.01 and abs(mm(v.y) - y0) < 0.01:
            return d
    return None


def describe(d, pcbnew):
    mm = pcbnew.ToMM
    v = d.GetPolyShape().CVertex(0)
    return (f"{C.short(d.GetNetname()):12s} {d.GetLayerName():7s} {d.GetPolyShape().VertexCount()} pts "
            f"from ({mm(v.x):.3f}, {mm(v.y):.3f})  {d.GetPolyShape().Area() / 1e12:.3f} mm^2")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    C.add_board_args(ap)
    ap.add_argument("--plan", help="a route.py plan: show what its polys / polyrips would do (nothing is written)")
    a = C.args(ap)
    b = C.load(a)
    pcbnew = C.pcbnew_module()
    have = find(b, pcbnew)
    print(f"{len(have)} net-tied polygon(s) on the board")
    for d in have:
        print("  " + describe(d, pcbnew))
    if a.plan:
        plan = json.loads(open(C.in_board(a, a.plan)).read())
        for q in plan.get("polyrips", []):
            d = match(b, pcbnew, q)
            print(f"  rip  {q['net']} {q['layer']} at {q['at']}: " + ("found" if d else "NOT ON THE BOARD"))
        for q in plan.get("polys", []):
            s = add(b, pcbnew, q)
            print(f"  add  {describe(s, pcbnew)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
