#!/usr/bin/env python3
"""knots.py -- where a router race that did not close is stuck.

    python3 tools/review/knots.py <partial.json> [<partial.json> ...] [--gap 0.3]

READ ONLY. A race that ends on a plateau leaves `partial.json` beside each
variant (kicad_project/tools/route): `contested` maps a net to the cells
[layer, x, y] it still shares or cannot keep. This prints them as KNOTS --
cells of one layer nearer than --gap, with the nets in them -- largest first:
a handful of places, not a cell total.

Read it before a term is offered for the wall: a knot of two tracks on one
layer is a crossing with no second layer there, and the term to look for is
what shuts the other layers at that place. A static count (crossing pairs,
lanes) ranks a rung only after it has predicted a race.
"""
import argparse, json


def knots(contested, gap):
    cells = {}
    for net, pts in contested.items():
        for layer, x, y in pts:
            cells.setdefault((layer, round(x, 3), round(y, 3)), set()).add(net)
    out = []
    for (layer, x, y), nets in sorted(cells.items()):
        hit = [k for k in out if k["layer"] == layer
               and any(abs(x - a) <= gap and abs(y - b) <= gap for a, b in k["pts"])]
        if hit:
            k = hit[0]
            for other in hit[1:]:
                k["pts"] += other["pts"]; k["nets"] |= other["nets"]; out.remove(other)
        else:
            k = {"layer": layer, "pts": [], "nets": set()}; out.append(k)
        k["pts"].append((x, y)); k["nets"] |= nets
    return sorted(out, key=lambda k: -len(k["pts"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("partial", nargs="+")
    ap.add_argument("--gap", type=float, default=0.3, help="cells nearer than this (mm) are one knot")
    a = ap.parse_args()
    for f in a.partial:
        p = json.load(open(f))
        ks = knots(p.get("contested", {}), a.gap)
        n = sum(len(k["pts"]) for k in ks)
        print(f"{f}: {p.get('state', '?')} at iteration {p.get('iteration', '?')}, {n} cells in {len(ks)} knot(s)")
        for k in ks:
            xs = [x for x, _ in k["pts"]]; ys = [y for _, y in k["pts"]]
            print(f"  {len(k['pts']):4d}  {k['layer']:7s} x {min(xs):.2f}-{max(xs):.2f}  y {min(ys):.2f}-{max(ys):.2f}"
                  f"  {' x '.join(sorted(k['nets']))}")


if __name__ == "__main__":
    main()
